"""Trains a per-player-per-game anytime-touchdown probability model for
RB/WR/TE and writes predictions to `agg_td_probability`.

Retrains from scratch on every run (a few seconds on ~13K rows) rather than
persisting a model file between runs -- consistent with the rest of this
pipeline's "recompute from source, idempotent" design (see
build_player_season.py, build_defense_season.py). There is no saved model
artifact to go stale or need cache-busting; running this script always
reflects the current contents of the database.

Full methodology, feature selection rationale, and the leakage rule this
script exists to enforce: see analytics/TD_MODEL.md.

Must run after build_defense_season.py and build_player_season.py (needs
agg_team_defense_season and agg_player_season_to_date).
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import log_loss, roc_auc_score

from db import get_connection

POSITIONS = ("RB", "WR", "TE")

# Curated, not "every column that exists" -- the exploratory pass (see
# TD_MODEL.md) tested all ~100 candidate columns and found the ~50 pass-
# protection/coverage defense columns (completion%, pressure rate,
# defenders in box, etc.) carry essentially zero signal for "will this
# specific skill player score," since they describe pass-defense quality,
# not red-zone/scoring tendency. Dropping them reduces noise and overfit
# risk on a ~13K-row dataset rather than improving reported accuracy.
PLAYER_FEATURES = [
    "szn_touchdowns", "l5_touchdowns", "l3_touchdowns",
    "szn_targets", "l5_targets", "l3_targets",
    "szn_target_share", "l5_target_share", "l3_target_share",
    "szn_receptions", "l5_receptions",
    "szn_carries", "l5_carries", "l3_carries",
    "szn_carry_share", "l5_carry_share",
    "szn_rushing_yards", "l5_rushing_yards", "l3_rushing_yards",
    "szn_receiving_yards",
    "szn_snap_pct", "l5_snap_pct", "l3_snap_pct",
    "szn_red_zone_target_share_avg", "szn_red_zone_rush_share_avg",
    "szn_games_played",
]
OPPONENT_FEATURES = [
    "avg_rz_tds_allowed",
    "avg_rush_tds_allowed_vs_rb", "avg_rec_tds_allowed_vs_wr",
    "avg_rec_tds_allowed_vs_te", "avg_rec_tds_allowed_vs_rb",
    "avg_rush_yards_allowed_vs_rb", "avg_rec_yards_allowed_vs_wr",
    "avg_rec_yards_allowed_vs_te", "avg_rec_yards_allowed_vs_rb",
    "avg_rz_rush_yards_allowed_vs_rb", "avg_rz_rec_yards_allowed_vs_wr",
    "avg_rz_rec_yards_allowed_vs_te", "avg_rz_targets_allowed_vs_wr",
    "avg_rz_targets_allowed_vs_te",
]
GAME_CONTEXT_FEATURES = ["is_home", "team_implied_total", "team_favored_by", "is_dome", "div_game"]

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS agg_td_probability (
    player_id          VARCHAR,
    game_id             VARCHAR,
    season              INTEGER,
    week                 INTEGER,
    position              VARCHAR,
    td_probability         DOUBLE,
    scored_td_actual        BOOLEAN,
    model_holdout_auc         DOUBLE,
    model_holdout_log_loss     DOUBLE,
    trained_at                  TIMESTAMP,
    PRIMARY KEY (player_id, game_id)
);
"""


def _current_season_week(con) -> tuple[int, int]:
    row = con.execute(
        "SELECT season, week FROM core_games WHERE game_date >= CURRENT_DATE ORDER BY season ASC, week ASC LIMIT 1"
    ).fetchone()
    if row is not None:
        return row
    row = con.execute("SELECT season, week FROM core_games ORDER BY season DESC, week DESC LIMIT 1").fetchone()
    return row


def _build_population(con, cur_season: int, cur_week: int) -> pd.DataFrame:
    """Every RB/WR/TE (player, game) that's ever been played, plus the
    current/upcoming week's roster (players whose most recent known team is
    playing in cur_season/cur_week). `is_history=False` rows are excluded
    from training (no outcome exists yet) but get scored like everything
    else.
    """
    pos_list = ", ".join(f"'{p}'" for p in POSITIONS)
    sql = f"""
        WITH historical AS (
            SELECT player_id, game_id, season, week, team, position, TRUE AS is_history
            FROM agg_player_game
            WHERE position IN ({pos_list})
        ),
        current_team AS (
            SELECT player_id, team, position,
                   ROW_NUMBER() OVER (PARTITION BY player_id ORDER BY week DESC) AS rn
            FROM agg_player_game
            WHERE season = ? AND week <= ? AND position IN ({pos_list})
        ),
        week_games AS (
            SELECT game_id, home_team, away_team FROM core_games WHERE season = ? AND week = ?
        ),
        current_roster AS (
            SELECT ct.player_id, wg.game_id, ? AS season, ? AS week, ct.team, ct.position, FALSE AS is_history
            FROM current_team ct
            JOIN week_games wg ON ct.team = wg.home_team OR ct.team = wg.away_team
            WHERE ct.rn = 1
        )
        SELECT * FROM historical
        UNION ALL
        SELECT cr.* FROM current_roster cr
        WHERE NOT EXISTS (
            SELECT 1 FROM historical h WHERE h.player_id = cr.player_id AND h.game_id = cr.game_id
        )
    """
    return con.execute(sql, [cur_season, cur_week, cur_season, cur_week, cur_season, cur_week]).fetchdf()


def _attach_game_context(con, pop: pd.DataFrame) -> pd.DataFrame:
    games = con.execute("""
        SELECT game_id, home_team, away_team, spread_line, total_line, roof, div_game
        FROM core_games
    """).fetchdf()
    df = pop.merge(games, on="game_id", how="left")
    df["opponent"] = np.where(df["team"] == df["home_team"], df["away_team"], df["home_team"])
    df["is_home"] = (df["team"] == df["home_team"]).astype(int)
    df["team_implied_total"] = np.where(
        df["is_home"] == 1, (df["total_line"] + df["spread_line"]) / 2, (df["total_line"] - df["spread_line"]) / 2
    )
    df["team_favored_by"] = np.where(df["is_home"] == 1, df["spread_line"], -df["spread_line"])
    df["is_dome"] = df["roof"].isin(["dome", "closed"]).astype(int)
    df["div_game"] = df["div_game"].astype(float)
    return df


def _attach_clean_td_label(con, df: pd.DataFrame) -> pd.DataFrame:
    """Offense-only TD (excludes return TDs, which contaminate
    agg_player_game.touchdowns -- see TD_MODEL.md)."""
    clean = con.execute("""
        SELECT td_player_id AS player_id, game_id, COUNT(*) AS off_tds
        FROM pbp_plays
        WHERE touchdown AND td_player_id IS NOT NULL AND (rush_attempt OR complete_pass)
        GROUP BY td_player_id, game_id
    """).fetchdf()
    df = df.merge(clean, on=["player_id", "game_id"], how="left")
    df["scored_td_actual"] = np.where(df["is_history"], df["off_tds"].fillna(0) > 0, np.nan)
    return df.drop(columns=["off_tds"])


def _attach_lagged_features(con, df: pd.DataFrame) -> pd.DataFrame:
    """The leakage rule: every player/opponent feature must reflect
    knowledge from *before* the game in `week` -- never that game's own
    result. Implemented as LAG(1) over each entity's full week sequence.
    This is provably correct for BOTH populations at once: for an
    already-played week, LAG(1) skips that week's own (already-known)
    outcome; for the current/upcoming week, the row at that week in
    agg_player_season_to_date is itself a carry-forward placeholder with
    the exact same values LAG(1) would return, since nothing has happened
    yet to update it. One rule, two correct outcomes -- see TD_MODEL.md.
    """
    lag_p = ",\n    ".join(f"LAG({c}) OVER (PARTITION BY player_id, season ORDER BY week) AS feat_{c}" for c in PLAYER_FEATURES)
    player_prior = con.execute(f"SELECT player_id, season, week,\n{lag_p}\nFROM agg_player_season_to_date").fetchdf()

    lag_d = ",\n    ".join(f"LAG({c}) OVER (PARTITION BY defteam, season ORDER BY week) AS opp_feat_{c}" for c in OPPONENT_FEATURES)
    def_prior = con.execute(f"SELECT defteam, season, week,\n{lag_d}\nFROM agg_team_defense_season").fetchdf()

    touches = con.execute("""
        WITH t AS (
            SELECT rusher_player_id AS player_id, game_id, goal_to_go FROM pbp_plays WHERE rusher_player_id IS NOT NULL
            UNION ALL
            SELECT receiver_player_id AS player_id, game_id, goal_to_go FROM pbp_plays WHERE receiver_player_id IS NOT NULL
        )
        SELECT player_id, game_id, COUNT(*) FILTER (WHERE goal_to_go) AS gtg_touches
        FROM t GROUP BY player_id, game_id
    """).fetchdf()
    gtg = df[["player_id", "game_id", "season", "week"]].merge(touches, on=["player_id", "game_id"], how="left")
    gtg["gtg_touches"] = gtg["gtg_touches"].fillna(0)
    gtg = gtg.sort_values(["player_id", "season", "week"])
    gtg["feat_szn_gtg_touches_avg"] = (
        gtg.groupby(["player_id", "season"])["gtg_touches"].apply(lambda s: s.shift(1).expanding().mean()).reset_index(level=[0, 1], drop=True)
    )

    df = df.merge(player_prior, on=["player_id", "season", "week"], how="left")
    df = df.merge(def_prior, left_on=["opponent", "season", "week"], right_on=["defteam", "season", "week"], how="left")
    df = df.merge(gtg[["player_id", "game_id", "feat_szn_gtg_touches_avg"]], on=["player_id", "game_id"], how="left")
    return df


def build_feature_frame(con, cur_season: int, cur_week: int) -> tuple[pd.DataFrame, list[str]]:
    pop = _build_population(con, cur_season, cur_week)
    df = _attach_game_context(con, pop)
    df = _attach_clean_td_label(con, df)
    df = _attach_lagged_features(con, df)
    df = pd.get_dummies(df, columns=["position"], prefix="pos")

    lagged_player = [f"feat_{c}" for c in PLAYER_FEATURES] + ["feat_szn_gtg_touches_avg"]
    lagged_opp = [f"opp_feat_{c}" for c in OPPONENT_FEATURES]
    pos_cols = [c for c in df.columns if c.startswith("pos_")]
    feature_cols = lagged_player + lagged_opp + GAME_CONTEXT_FEATURES + pos_cols
    return df, feature_cols


def train_and_score(con) -> pd.DataFrame:
    cur_season, cur_week = _current_season_week(con)
    df, feature_cols = build_feature_frame(con, cur_season, cur_week)

    # Drop rows with no lagged player history at all (season debut / no
    # prior game to learn from yet) -- these can't be modeled either way.
    df = df.dropna(subset=[f"feat_{c}" for c in PLAYER_FEATURES])

    X = df[feature_cols].astype(float)
    labeled = df["scored_td_actual"].notna()

    # Holdout metric: train on all seasons but the most recent, test on the
    # most recent -- reported for transparency, not used for live scoring.
    seasons_sorted = sorted(df.loc[labeled, "season"].unique())
    holdout_auc = holdout_logloss = None
    if len(seasons_sorted) >= 2:
        holdout_season = seasons_sorted[-1]
        train_mask = labeled & (df["season"] != holdout_season)
        test_mask = labeled & (df["season"] == holdout_season)
        if train_mask.sum() > 200 and test_mask.sum() > 50 and df.loc[test_mask, "scored_td_actual"].astype(int).nunique() > 1:
            eval_model = HistGradientBoostingClassifier(max_depth=4, learning_rate=0.05, max_iter=300, random_state=42)
            eval_model.fit(X[train_mask], df.loc[train_mask, "scored_td_actual"].astype(int))
            pred = eval_model.predict_proba(X[test_mask])[:, 1]
            holdout_auc = roc_auc_score(df.loc[test_mask, "scored_td_actual"].astype(int), pred)
            holdout_logloss = log_loss(df.loc[test_mask, "scored_td_actual"].astype(int), pred)

    # Production model: trained on every labeled row available, used to
    # score everything (history + the live current/upcoming week).
    final_model = HistGradientBoostingClassifier(max_depth=4, learning_rate=0.05, max_iter=300, random_state=42)
    final_model.fit(X[labeled], df.loc[labeled, "scored_td_actual"].astype(int))
    df["td_probability"] = final_model.predict_proba(X)[:, 1]

    df["model_holdout_auc"] = holdout_auc
    df["model_holdout_log_loss"] = holdout_logloss
    df["trained_at"] = datetime.now(timezone.utc)
    df["position"] = np.select(
        [df["pos_RB"] == 1, df["pos_WR"] == 1, df["pos_TE"] == 1], ["RB", "WR", "TE"], default=None
    )

    out_cols = ["player_id", "game_id", "season", "week", "position", "td_probability",
                "scored_td_actual", "model_holdout_auc", "model_holdout_log_loss", "trained_at"]
    return df[out_cols], holdout_auc, holdout_logloss, (cur_season, cur_week)


def load_agg_td_probability(con) -> int:
    con.execute(CREATE_TABLE_SQL)
    predictions, holdout_auc, holdout_logloss, current_week = train_and_score(con)
    con.register("predictions_df", predictions)
    con.execute("CREATE OR REPLACE TABLE agg_td_probability AS SELECT * FROM predictions_df")
    con.unregister("predictions_df")
    n = con.execute("SELECT COUNT(*) FROM agg_td_probability").fetchone()[0]
    print(f"  current week detected: season {current_week[0]}, week {current_week[1]}")
    if holdout_auc is not None:
        print(f"  holdout AUC: {holdout_auc:.4f}, holdout log-loss: {holdout_logloss:.4f}")
    else:
        print("  holdout metrics skipped (not enough seasons of labeled data yet)")
    return n


if __name__ == "__main__":
    con = get_connection()
    print(f"agg_td_probability: {load_agg_td_probability(con)} total rows")
    con.close()
