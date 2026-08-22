"""Dashboard-ready query functions over the NFL betting analytics layer.

Every function returns a pandas DataFrame -- empty (not an exception) when
no matching data exists -- and every function that takes `season`/`week`
lets a dashboard query any historical snapshot, not just the most recent.

"As of week W" semantics: `agg_team_defense_season` and
`agg_player_season_to_date` only carry a row for weeks a team/player
actually played (see ASSUMPTIONS_AND_ISSUES.md, Phase 2/3 notes) -- there's
no row for a bye week, and obviously none yet for a week that hasn't been
played. Functions here resolve "as of week W" by finding the most recent
available row at or before W within that season, rather than requiring an
exact match. This is what makes "profile heading into an upcoming game"
and "profile during a bye week" lookups work. The resolved week is always
returned alongside the requested one so callers can tell whether a fallback
happened.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import duckdb
import pandas as pd

from config import DB_PATH


def connect(db_path=None) -> duckdb.DuckDBPyConnection:
    """Open a read-only connection to the analytics DuckDB file."""
    return duckdb.connect(str(db_path or DB_PATH), read_only=True)


# Position -> which player-side and defense-side columns are "relevant" for
# that position's props, plus the primary stat used for get_prop_matchup's
# matchup_advantage flag and get_league_defensive_rankings' sort order.
POSITION_STAT_MAP = {
    "QB": {
        "primary_stat": "passing_yards",
        "primary_avg_column": "avg_pass_yards_allowed",
        "primary_rank_column": "rank_pass_yards_allowed",
        "player_metrics": ["passing_yards", "pass_attempts", "touchdowns"],
        "defense_avg_columns": [
            "avg_pass_yards_allowed", "avg_pass_attempts_allowed",
            "avg_pass_completions_allowed", "avg_completion_pct_allowed",
            "avg_pass_tds_allowed",
        ],
        "defense_rank_columns": ["rank_pass_yards_allowed", "rank_rz_tds_allowed"],
    },
    "RB": {
        "primary_stat": "rushing_yards",
        "primary_avg_column": "avg_rush_yards_allowed_vs_rb",
        "primary_rank_column": "rank_rush_yards_allowed_vs_rb",
        "player_metrics": [
            "rushing_yards", "carries", "carry_share",
            "receiving_yards", "targets", "target_share", "touchdowns",
        ],
        "defense_avg_columns": [
            "avg_rush_yards_allowed_vs_rb", "avg_rush_attempts_allowed_vs_rb",
            "avg_rush_tds_allowed_vs_rb", "avg_rush_yards_per_carry_allowed_vs_rb",
            "avg_rec_yards_allowed_vs_rb", "avg_targets_allowed_vs_rb",
        ],
        "defense_rank_columns": ["rank_rush_yards_allowed_vs_rb", "rank_rz_tds_allowed"],
    },
    "WR": {
        "primary_stat": "receiving_yards",
        "primary_avg_column": "avg_rec_yards_allowed_vs_wr",
        "primary_rank_column": "rank_rec_yards_allowed_vs_wr",
        "player_metrics": ["receiving_yards", "targets", "target_share", "receptions", "touchdowns"],
        "defense_avg_columns": [
            "avg_rec_yards_allowed_vs_wr", "avg_targets_allowed_vs_wr",
            "avg_receptions_allowed_vs_wr", "avg_rec_tds_allowed_vs_wr",
            "avg_yards_per_target_allowed_vs_wr",
        ],
        "defense_rank_columns": ["rank_rec_yards_allowed_vs_wr", "rank_targets_allowed_vs_wr", "rank_rz_tds_allowed"],
    },
    "TE": {
        "primary_stat": "receiving_yards",
        "primary_avg_column": "avg_rec_yards_allowed_vs_te",
        "primary_rank_column": "rank_rec_yards_allowed_vs_te",
        "player_metrics": ["receiving_yards", "targets", "target_share", "receptions", "touchdowns"],
        "defense_avg_columns": [
            "avg_rec_yards_allowed_vs_te", "avg_targets_allowed_vs_te",
            "avg_receptions_allowed_vs_te", "avg_rec_tds_allowed_vs_te",
            "avg_yards_per_target_allowed_vs_te",
        ],
        "defense_rank_columns": ["rank_rec_yards_allowed_vs_te", "rank_targets_allowed_vs_te", "rank_rz_tds_allowed"],
    },
}

def _validate_bottom_pct(bottom_pct: float) -> None:
    if not (0 < bottom_pct <= 1):
        raise ValueError(f"bottom_pct must be in (0, 1], got {bottom_pct}")


def _pool_size(con, season: int, week: int) -> int:
    """How many teams `agg_team_defense_season`'s stored rank_X columns for
    (season, week) were actually ranked against -- Phase 2 computed rank_X
    via RANK() OVER (PARTITION BY season, week ...), so the pool is exactly
    the teams with a row at that native week (fewer than 32 is common
    early in a season or around bye weeks)."""
    return con.execute(
        "SELECT COUNT(DISTINCT defteam) FROM agg_team_defense_season WHERE season = ? AND week = ?",
        [season, week],
    ).fetchone()[0]


def _player_profile_as_of(con, player_id: str, season: int, week: int) -> pd.DataFrame:
    sql = """
        WITH as_of AS (
            SELECT *
            FROM agg_player_season_to_date
            WHERE player_id = ? AND season = ? AND week <= ?
            ORDER BY week DESC
            LIMIT 1
        )
        SELECT
            ao.*,
            ? AS requested_week,
            ao.week AS as_of_week,
            cp.player_name,
            apg.position,
            apg.team
        FROM as_of ao
        JOIN agg_player_game apg
            ON apg.player_id = ao.player_id AND apg.season = ao.season AND apg.week = ao.week
        JOIN core_players cp ON cp.player_id = ao.player_id
    """
    return con.execute(sql, [player_id, season, week, week]).fetchdf()


def get_player_prop_profile(player_id: str, season: int, week: int, con=None) -> pd.DataFrame:
    """Single-row DataFrame of a player's l3/l5/szn prop-relevant rolling
    averages as of `week` -- i.e. through their most recent active game at
    or before `week` in `season`. Includes `requested_week` and
    `as_of_week` so a caller can tell if a fallback (bye/future week)
    occurred. Empty DataFrame if the player has no active game at or
    before that week in that season.
    """
    if con is not None:
        return _player_profile_as_of(con, player_id, season, week)
    with connect() as c:
        return _player_profile_as_of(c, player_id, season, week)


def _defense_profile_as_of(con, defteam: str, season: int, week: int) -> pd.DataFrame:
    sql = """
        WITH as_of AS (
            SELECT *
            FROM agg_team_defense_season
            WHERE defteam = ? AND season = ? AND week <= ?
            ORDER BY week DESC
            LIMIT 1
        )
        SELECT
            ao.*,
            ? AS requested_week,
            ao.week AS as_of_week,
            ct.team_name
        FROM as_of ao
        JOIN core_teams ct ON ct.team_id = ao.defteam
    """
    return con.execute(sql, [defteam, season, week, week]).fetchdf()


def get_defense_matchup(defteam: str, season: int, week: int, con=None) -> pd.DataFrame:
    """Single-row DataFrame of a defense's season-to-date per-game
    averages and rankings as of `week` -- i.e. through their most recently
    played game at or before `week` in `season`. Empty DataFrame if the
    team has no game at or before that week in that season.
    """
    if con is not None:
        return _defense_profile_as_of(con, defteam, season, week)
    with connect() as c:
        return _defense_profile_as_of(c, defteam, season, week)


def get_prop_matchup(
    player_id: str, defteam: str, season: int, week: int, bottom_pct: float = 0.5, con=None
) -> pd.DataFrame:
    """Single-row DataFrame combining a player's rolling averages with the
    opposing defense's allowed averages/rankings for the player's position
    (auto-detected from the player's most recent active game). Defense
    columns are prefixed `opp_`. Adds `primary_stat` (the stat used for the
    position's headline prop), `primary_stat_rank` (that defense's rank
    against it), `pool_size` (how many teams that rank was computed
    against), and `matchup_advantage` (True if that rank falls in the
    worst `bottom_pct` fraction of `pool_size` -- e.g. `bottom_pct=0.25`
    flags the worst quarter of the league; default 0.5 = worse half,
    matching the original spec).

    Empty DataFrame if the player or the defense has no data at or before
    `week` in `season`, or if the player's position isn't QB/RB/WR/TE.
    Raises ValueError if `bottom_pct` isn't in (0, 1].
    """
    _validate_bottom_pct(bottom_pct)

    def _build(c):
        player_df = _player_profile_as_of(c, player_id, season, week)
        if player_df.empty:
            return pd.DataFrame()

        position = player_df.at[0, "position"]
        stat_map = POSITION_STAT_MAP.get(position)
        if stat_map is None:
            return pd.DataFrame()

        defense_df = _defense_profile_as_of(c, defteam, season, week)
        if defense_df.empty:
            return pd.DataFrame()

        player_cols = ["player_id", "player_name", "position", "team", "season", "requested_week"]
        for prefix in ("l3_", "l5_", "szn_"):
            player_cols += [f"{prefix}{m}" for m in stat_map["player_metrics"]]
        player_out = player_df[player_cols].copy()
        player_out["player_as_of_week"] = player_df["as_of_week"]

        defense_cols = ["defteam", "team_name"] + stat_map["defense_avg_columns"] + stat_map["defense_rank_columns"]
        defense_out = defense_df[defense_cols].rename(columns={"defteam": "opp_defteam", "team_name": "opp_team_name"})
        defense_out = defense_out.rename(columns={
            c: f"opp_{c}" for c in stat_map["defense_avg_columns"] + stat_map["defense_rank_columns"]
        })
        defense_out["opp_as_of_week"] = defense_df["as_of_week"]

        primary_rank = int(defense_df.at[0, stat_map["primary_rank_column"]])
        pool_size = _pool_size(c, season, int(defense_df.at[0, "as_of_week"]))
        cutoff = pool_size * (1 - bottom_pct)
        extra = pd.DataFrame([{
            "primary_stat": stat_map["primary_stat"],
            "primary_stat_rank": primary_rank,
            "pool_size": pool_size,
            "matchup_advantage": primary_rank > cutoff,
        }])

        return pd.concat(
            [player_out.reset_index(drop=True), defense_out.reset_index(drop=True), extra],
            axis=1,
        )

    if con is not None:
        return _build(con)
    with connect() as c:
        return _build(c)


def get_league_defensive_rankings(
    season: int, week: int, position: str, bottom_pct: float | None = None, con=None
) -> pd.DataFrame:
    """All teams with data at or before `week` in `season`, ranked by
    defensive performance against `position` (WR/TE/RB/QB), sorted best
    defense (rank 1) to worst.

    Each team's row is its own most recent snapshot at or before `week`
    (byes mean not every team is "as of" the same native week). Rank is
    **recomputed fresh** across those snapshots here -- rather than reusing
    the stored per-native-week `rank_X` column -- so it's an apples-to-apples
    comparison across all teams regardless of bye staggering. Ties use the
    same "skip after a tie" semantics as SQL RANK().

    `bottom_pct`, if given, filters the result down to just the worst
    `bottom_pct` fraction of *this* pool (e.g. `bottom_pct=0.25` returns
    only the bottom quarter of teams that actually have data as of `week`
    -- so unlike `get_prop_matchup`'s `bottom_pct`, this one's denominator
    is never stale/mismatched since it's applied to the same freshly
    ranked set being returned). Default `None` returns every team.

    Empty DataFrame if `position` isn't QB/RB/WR/TE or no teams have data.
    Raises ValueError if `bottom_pct` is given and isn't in (0, 1].
    """
    stat_map = POSITION_STAT_MAP.get(position)
    if stat_map is None:
        return pd.DataFrame()
    if bottom_pct is not None:
        _validate_bottom_pct(bottom_pct)

    sql = """
        WITH as_of AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY defteam ORDER BY week DESC) AS rn
            FROM agg_team_defense_season
            WHERE season = ? AND week <= ?
        )
        SELECT ao.*, ct.team_name
        FROM as_of ao
        JOIN core_teams ct ON ct.team_id = ao.defteam
        WHERE ao.rn = 1
    """

    def _build(c):
        df = c.execute(sql, [season, week]).fetchdf()
        if df.empty:
            return pd.DataFrame()

        cols = ["defteam", "team_name", "week", "games_played"] + stat_map["defense_avg_columns"]
        out = df[cols].rename(columns={"week": "as_of_week"}).copy()
        out["rank"] = out[stat_map["primary_avg_column"]].rank(method="min", ascending=True).astype(int)
        out = out.sort_values("rank").reset_index(drop=True)
        if bottom_pct is not None:
            cutoff = len(out) * (1 - bottom_pct)
            out = out[out["rank"] > cutoff].reset_index(drop=True)
        return out

    if con is not None:
        return _build(con)
    with connect() as c:
        return _build(c)


def get_player_game_log(player_id: str, season: int, con=None) -> pd.DataFrame:
    """All of a player's `agg_player_game` rows for `season`, enriched with
    opponent and game date from `core_games`, sorted by week. Empty
    DataFrame if the player has no rows that season.
    """
    sql = """
        SELECT
            apg.*,
            cg.game_date,
            CASE WHEN apg.team = cg.home_team THEN cg.away_team ELSE cg.home_team END AS opponent
        FROM agg_player_game apg
        JOIN core_games cg ON cg.game_id = apg.game_id
        WHERE apg.player_id = ? AND apg.season = ?
        ORDER BY apg.week
    """
    if con is not None:
        return con.execute(sql, [player_id, season]).fetchdf()
    with connect() as c:
        return c.execute(sql, [player_id, season]).fetchdf()
