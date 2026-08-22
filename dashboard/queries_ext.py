"""Board-level query helpers for the Streamlit dashboard.

`nfl_betting/analytics/queries.py` is built for single-player/single-team
lookups (one row per call). The dashboard needs a "one row per active
player this week" board instead -- that's what `get_weekly_position_board`
provides, plus a couple of small helpers for week auto-detection and the
game selector.

Deliberately does NOT modify `nfl_betting/analytics/queries.py` -- this
module imports it read-only and adds to it.

A note on `agg_player_season_to_date`'s l3_/l5_/szn_ columns: despite the
"_yards"/"_carries" naming, every one of those columns is already a
**per-game rolling average** (`AVG(...) OVER (...)` in
`build_player_season.py`), not a running total. The board reconstructs
"totals" (e.g. `szn_rushing_yards`) by multiplying the stored average by
the number of games in that window, and exposes the average directly
(unchanged) as `szn_avg_rush_yds` etc. Both are therefore exact, not
approximations.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nfl_betting"))

import duckdb
import pandas as pd

from analytics import queries as _q

connect = _q.connect

_POSITIONS = ("RB", "WR", "TE")

# Per-game metric columns fetched from agg_player_season_to_date, aliased
# with a `_pg` suffix as a reminder that the raw column is a per-game
# average, not a total.
_ROLLING_METRICS = ["rushing_yards", "carries", "receiving_yards", "receptions", "targets"]


def get_current_week(con: duckdb.DuckDBPyConnection | None = None) -> dict:
    """Auto-detect the "current" (season, week) from today's date.

    Finds the minimum (season, week) among games with `game_date >=
    today`. If no such games exist (offseason, or 2026 data not published
    yet), falls back to the most recent (season, week) in core_games and
    sets `is_fallback=True`.

    Returns {"season": int, "week": int, "is_fallback": bool}, or
    {"season": None, "week": None, "is_fallback": True} if core_games is
    completely empty.
    """

    def _build(c):
        upcoming = c.execute(
            """
            SELECT season, week
            FROM core_games
            WHERE game_date >= CURRENT_DATE
            ORDER BY season ASC, week ASC
            LIMIT 1
            """
        ).fetchone()
        if upcoming is not None:
            return {"season": upcoming[0], "week": upcoming[1], "is_fallback": False}

        latest = c.execute(
            """
            SELECT season, week
            FROM core_games
            ORDER BY season DESC, week DESC
            LIMIT 1
            """
        ).fetchone()
        if latest is None:
            return {"season": None, "week": None, "is_fallback": True}
        return {"season": latest[0], "week": latest[1], "is_fallback": True}

    if con is not None:
        return _build(con)
    with connect() as c:
        return _build(c)


def get_data_as_of_week(season: int, con: duckdb.DuckDBPyConnection | None = None):
    """Latest week within `season` that has real (played) analytics data,
    i.e. the max week present in agg_team_defense_season. None if the
    season has no played games yet.
    """
    sql = "SELECT MAX(week) FROM agg_team_defense_season WHERE season = ?"
    if con is not None:
        row = con.execute(sql, [season]).fetchone()
    else:
        with connect() as c:
            row = c.execute(sql, [season]).fetchone()
    return row[0] if row else None


def get_games_for_week(season: int, week: int, con: duckdb.DuckDBPyConnection | None = None) -> pd.DataFrame:
    """All games for (season, week), with a `day_name` column (Thursday/
    Sunday/Monday/etc, derived from game_date) and a `matchup` label
    ("AWAY @ HOME"). Empty DataFrame if none.
    """
    sql = """
        SELECT
            game_id, season, week, game_date, home_team, away_team,
            strftime(game_date, '%A') AS day_name,
            away_team || ' @ ' || home_team AS matchup
        FROM core_games
        WHERE season = ? AND week = ?
        ORDER BY game_date, game_id
    """
    if con is not None:
        return con.execute(sql, [season, week]).fetchdf()
    with connect() as c:
        return c.execute(sql, [season, week]).fetchdf()


_BOARD_SQL = """
WITH player_team AS (
    SELECT
        player_id, team, position, week,
        ROW_NUMBER() OVER (PARTITION BY player_id ORDER BY week DESC) AS rn
    FROM agg_player_game
    WHERE season = ? AND week <= ? AND position = ?
),
current_team AS (
    SELECT player_id, team, position FROM player_team WHERE rn = 1
),
week_games AS (
    SELECT game_id, home_team, away_team
    FROM core_games
    WHERE season = ? AND week = ?
),
roster AS (
    SELECT
        ct.player_id, ct.team, ct.position, wg.game_id,
        CASE WHEN ct.team = wg.home_team THEN wg.away_team ELSE wg.home_team END AS opponent
    FROM current_team ct
    JOIN week_games wg ON ct.team = wg.home_team OR ct.team = wg.away_team
),
player_stats AS (
    SELECT
        *,
        ROW_NUMBER() OVER (PARTITION BY player_id ORDER BY week DESC) AS rn
    FROM agg_player_season_to_date
    WHERE season = ? AND week <= ?
),
player_stats_asof AS (
    SELECT * FROM player_stats WHERE rn = 1 AND szn_games_played >= 1
),
defense_stats AS (
    SELECT
        *,
        ROW_NUMBER() OVER (PARTITION BY defteam ORDER BY week DESC) AS rn
    FROM agg_team_defense_season
    WHERE season = ? AND week <= ?
),
defense_asof AS (
    SELECT * FROM defense_stats WHERE rn = 1
),
defense_ranked AS (
    SELECT
        defteam,
        week AS opp_as_of_week,
        avg_rush_yards_allowed_vs_rb,
        RANK() OVER (ORDER BY avg_rush_yards_allowed_vs_rb ASC) AS rank_rush_yards_allowed_vs_rb,
        avg_rec_yards_allowed_vs_rb,
        RANK() OVER (ORDER BY avg_rec_yards_allowed_vs_rb ASC) AS rank_rec_yards_allowed_vs_rb,
        avg_targets_allowed_vs_rb,
        RANK() OVER (ORDER BY avg_targets_allowed_vs_rb ASC) AS rank_targets_allowed_vs_rb,
        avg_rec_yards_allowed_vs_wr,
        RANK() OVER (ORDER BY avg_rec_yards_allowed_vs_wr ASC) AS rank_rec_yards_allowed_vs_wr,
        avg_targets_allowed_vs_wr,
        RANK() OVER (ORDER BY avg_targets_allowed_vs_wr ASC) AS rank_targets_allowed_vs_wr,
        avg_rec_yards_allowed_vs_te,
        RANK() OVER (ORDER BY avg_rec_yards_allowed_vs_te ASC) AS rank_rec_yards_allowed_vs_te,
        avg_targets_allowed_vs_te,
        RANK() OVER (ORDER BY avg_targets_allowed_vs_te ASC) AS rank_targets_allowed_vs_te,
        COUNT(*) OVER () AS pool_size
    FROM defense_asof
)
SELECT
    r.player_id, cp.player_name, r.team, r.position, r.game_id, r.opponent,

    ps.week AS player_as_of_week,
    ps.szn_games_played,

    ps.szn_rushing_yards AS szn_rushing_yards_pg,
    ps.szn_carries AS szn_carries_pg,
    ps.szn_receiving_yards AS szn_receiving_yards_pg,
    ps.szn_receptions AS szn_receptions_pg,
    ps.szn_targets AS szn_targets_pg,
    ps.szn_touchdowns AS szn_touchdowns_pg,
    ps.szn_snap_pct,
    ps.szn_red_zone_target_share_avg,
    ps.szn_red_zone_rush_share_avg,

    ps.l5_rushing_yards AS l5_rushing_yards_pg,
    ps.l5_carries AS l5_carries_pg,
    ps.l5_receiving_yards AS l5_receiving_yards_pg,
    ps.l5_receptions AS l5_receptions_pg,
    ps.l5_targets AS l5_targets_pg,

    ps.l3_rushing_yards AS l3_rushing_yards_pg,
    ps.l3_carries AS l3_carries_pg,
    ps.l3_receiving_yards AS l3_receiving_yards_pg,
    ps.l3_receptions AS l3_receptions_pg,
    ps.l3_targets AS l3_targets_pg,

    dr.opp_as_of_week,
    dr.avg_rush_yards_allowed_vs_rb AS opp_avg_rush_yards_allowed_vs_rb,
    dr.rank_rush_yards_allowed_vs_rb AS opp_rank_rush_yards_allowed_vs_rb,
    dr.avg_rec_yards_allowed_vs_rb AS opp_avg_rec_yards_allowed_vs_rb,
    dr.rank_rec_yards_allowed_vs_rb AS opp_rank_rec_yards_allowed_vs_rb,
    dr.avg_targets_allowed_vs_rb AS opp_avg_targets_allowed_vs_rb,
    dr.rank_targets_allowed_vs_rb AS opp_rank_targets_allowed_vs_rb,
    dr.avg_rec_yards_allowed_vs_wr AS opp_avg_rec_yards_allowed_vs_wr,
    dr.rank_rec_yards_allowed_vs_wr AS opp_rank_rec_yards_allowed_vs_wr,
    dr.avg_targets_allowed_vs_wr AS opp_avg_targets_allowed_vs_wr,
    dr.rank_targets_allowed_vs_wr AS opp_rank_targets_allowed_vs_wr,
    dr.avg_rec_yards_allowed_vs_te AS opp_avg_rec_yards_allowed_vs_te,
    dr.rank_rec_yards_allowed_vs_te AS opp_rank_rec_yards_allowed_vs_te,
    dr.avg_targets_allowed_vs_te AS opp_avg_targets_allowed_vs_te,
    dr.rank_targets_allowed_vs_te AS opp_rank_targets_allowed_vs_te,
    dr.pool_size AS opp_pool_size
FROM roster r
JOIN player_stats_asof ps ON ps.player_id = r.player_id
JOIN core_players cp ON cp.player_id = r.player_id
LEFT JOIN defense_ranked dr ON dr.defteam = r.opponent
"""

# Which opp_ column family (vs_rb / vs_wr / vs_te) + primary-stat rank
# column each tab's position cares about. RB carries both rush and
# receiving families since RB props span both.
_OPP_FAMILIES = {
    "RB": ["vs_rb"],
    "WR": ["vs_wr"],
    "TE": ["vs_te"],
}
_PRIMARY_RANK_COL = {
    "RB": "opp_rank_rush_yards_allowed_vs_rb",
    "WR": "opp_rank_rec_yards_allowed_vs_wr",
    "TE": "opp_rank_rec_yards_allowed_vs_te",
}


def get_weekly_position_board(
    season: int,
    week: int,
    position: str,
    game_ids: list[str] | None = None,
    con: duckdb.DuckDBPyConnection | None = None,
) -> pd.DataFrame:
    """One row per active player of `position` for (season, week), with
    season/L5/L3 rolling stats and the opposing defense's allowed
    averages/ranks. Empty DataFrame if `position` isn't RB/WR/TE or no
    players match.

    `game_ids`, if given, filters to those games only (e.g. from the
    sidebar's day-of-week / game multiselect). None returns every game
    that week.
    """
    if position not in _POSITIONS:
        return pd.DataFrame()

    params = [season, week, position, season, week, season, week, season, week]

    def _build(c):
        df = c.execute(_BOARD_SQL, params).fetchdf()
        if df.empty:
            return pd.DataFrame()

        if game_ids is not None:
            # Empty list is a deliberate "nothing selected" filter, not "no
            # filter" -- only a literal None means "return every game".
            df = df[df["game_id"].isin(game_ids)].reset_index(drop=True)
        if df.empty:
            return pd.DataFrame()

        df["games_l5"] = df["szn_games_played"].clip(upper=5)
        df["games_l3"] = df["szn_games_played"].clip(upper=3)

        for prefix, games_col in (("szn", "szn_games_played"), ("l5", "games_l5"), ("l3", "games_l3")):
            for metric in _ROLLING_METRICS:
                pg_col = f"{prefix}_{metric}_pg"
                df[f"{prefix}_{metric}"] = (df[pg_col] * df[games_col]).round()

        df["szn_touchdowns"] = (df["szn_touchdowns_pg"] * df["szn_games_played"]).round()

        df["szn_avg_rush_yds"] = df["szn_rushing_yards_pg"]
        df["szn_avg_rec_yds"] = df["szn_receiving_yards_pg"]
        df["l5_avg_rush_yds"] = df["l5_rushing_yards_pg"]
        df["l5_avg_rec_yds"] = df["l5_receiving_yards_pg"]
        df["l3_avg_rush_yds"] = df["l3_rushing_yards_pg"]
        df["l3_avg_rec_yds"] = df["l3_receiving_yards_pg"]

        drop_cols = [c for c in df.columns if c.endswith("_pg")] + ["games_l5", "games_l3", "rn"]
        df = df.drop(columns=[c for c in drop_cols if c in df.columns])

        # Keep only the opp_* family relevant to this position.
        keep_families = _OPP_FAMILIES[position]
        all_families = ("vs_rb", "vs_wr", "vs_te")
        drop_families = [f for f in all_families if f not in keep_families]
        opp_drop = [c for c in df.columns if c.startswith("opp_") and any(c.endswith(f) for f in drop_families)]
        df = df.drop(columns=opp_drop)

        primary_rank_col = _PRIMARY_RANK_COL[position]
        pool = df["opp_pool_size"]
        rank = df[primary_rank_col]
        df["matchup_advantage"] = (rank > (pool * 0.5)).where(rank.notna() & pool.notna())

        return df.reset_index(drop=True)

    if con is not None:
        return _build(con)
    with connect() as c:
        return _build(c)
