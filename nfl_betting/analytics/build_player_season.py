"""Compute agg_player_season_to_date from agg_player_game (+ pbp_plays for
team red-zone totals, + core_games for the full team-week grain).

One row per (player_id, season, week) for every week that player's team(s)
played that season -- not just weeks the player was active. Rolling
l3_/l5_/szn_ averages are computed from active games only
(agg_player_game.is_active = true) and then carried forward (last
observation carried forward, LOCF) onto any week where the player's team
played but the player had no qualifying involvement (bench/inactive/
zero-snap games -- these never get an agg_player_game row at all, see
ASSUMPTIONS_AND_ISSUES.md Phase 3 note #2). `is_active_week` flags which
rows are real vs carried forward. Weeks before a player's first active
game in a season are left NULL (nothing to carry forward yet).

Must run after build_defense_game.py isn't required, but pbp_plays,
agg_player_game, and core_games must be populated (ingest_pbp.py +
ingest_aggregates.py).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import get_connection

# Output metric name -> source expression available in the `active_games` CTE.
METRIC_SOURCE = {
    "rushing_yards": "total_rush_yards",
    "receiving_yards": "total_receiving_yards",
    "receptions": "total_receptions",
    "targets": "targets",
    "touchdowns": "touchdowns",
    "carries": "carries",
    "pass_attempts": "pass_attempts",
    "passing_yards": "passing_yards",
    "snap_pct": "snap_pct",
    "epa_avg": "epa_avg",
    "red_zone_targets": "red_zone_targets",
    "red_zone_carries": "red_zone_carries",
    "target_share": "target_share_game",
    "carry_share": "carry_share_game",
}

WINDOWS = {
    "l1": "ROWS BETWEEN CURRENT ROW AND CURRENT ROW",
    "l3": "ROWS BETWEEN 2 PRECEDING AND CURRENT ROW",
    "l5": "ROWS BETWEEN 4 PRECEDING AND CURRENT ROW",
    "szn": "ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW",
}

WINDOWED_COLUMNS = [f"{w}_{m}" for w in WINDOWS for m in METRIC_SOURCE]
SZN_ONLY_COLUMNS = ["szn_games_played", "szn_red_zone_target_share_avg", "szn_red_zone_rush_share_avg"]
# Every rolling-stat column that gets carried forward (LOCF) onto gap weeks.
CARRY_FORWARD_COLUMNS = WINDOWED_COLUMNS + SZN_ONLY_COLUMNS

AGG_PLAYER_SEASON_COLUMNS = ["player_id", "season", "week", "is_active_week"] + CARRY_FORWARD_COLUMNS

_ddl_lines = (
    ["player_id VARCHAR", "season INTEGER", "week INTEGER", "is_active_week BOOLEAN"]
    + [f"{c} DOUBLE" for c in WINDOWED_COLUMNS]
    + ["szn_games_played INTEGER", "szn_red_zone_target_share_avg DOUBLE", "szn_red_zone_rush_share_avg DOUBLE"]
)
CREATE_TABLE_SQL = "CREATE TABLE IF NOT EXISTS agg_player_season_to_date (\n    " + \
    ",\n    ".join(_ddl_lines) + \
    ",\n    PRIMARY KEY (player_id, season, week)\n);"

_rolling_select = ["player_id", "season", "week", "TRUE AS is_active_week"]
for window, frame in WINDOWS.items():
    for metric, source in METRIC_SOURCE.items():
        _rolling_select.append(
            f"AVG({source}) OVER (PARTITION BY player_id, season ORDER BY week {frame}) AS {window}_{metric}"
        )
_rolling_select.append(
    "COUNT(*) OVER (PARTITION BY player_id, season ORDER BY week ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS szn_games_played"
)
_rolling_select.append(
    "AVG(rz_target_share_game) OVER (PARTITION BY player_id, season ORDER BY week ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS szn_red_zone_target_share_avg"
)
_rolling_select.append(
    "AVG(rz_rush_share_game) OVER (PARTITION BY player_id, season ORDER BY week ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS szn_red_zone_rush_share_avg"
)
_rolling_select_sql = ",\n        ".join(_rolling_select)

# Carry each rolling column forward within "groups" that reset at every
# active week (grp = running count of active weeks seen so far). Exactly
# one row per group has a non-NULL value for a given column (the active
# row itself); MAX() over the group picks that value up for every later
# row in the same group, ignoring NULLs automatically. Portable LOCF
# technique that doesn't depend on window IGNORE NULLS support.
_carry_forward_select = ["player_id", "season", "week", "is_active_week"] + [
    f"MAX({c}) OVER (PARTITION BY player_id, season, grp) AS {c}" for c in CARRY_FORWARD_COLUMNS
]
_carry_forward_select_sql = ",\n    ".join(_carry_forward_select)

AGG_PLAYER_SEASON_SQL = f"""
WITH team_rz AS (
    SELECT
        game_id,
        posteam,
        SUM(1) FILTER (WHERE yardline_100 <= 20 AND pass_attempt) AS team_rz_targets,
        SUM(1) FILTER (WHERE yardline_100 <= 20 AND rush_attempt) AS team_rz_carries
    FROM pbp_plays
    WHERE posteam IS NOT NULL
    GROUP BY game_id, posteam
),
active_games AS (
    SELECT
        apg.player_id,
        apg.season,
        apg.week,
        apg.total_rush_yards,
        apg.total_receiving_yards,
        apg.total_receptions,
        apg.targets,
        apg.touchdowns,
        apg.carries,
        apg.pass_attempts,
        apg.passing_yards,
        apg.snap_pct,
        apg.epa_avg,
        apg.red_zone_targets,
        apg.red_zone_carries,
        CASE WHEN COALESCE(apg.team_targets, 0) = 0 THEN NULL
             ELSE apg.targets::DOUBLE / apg.team_targets END AS target_share_game,
        CASE WHEN COALESCE(apg.team_carries, 0) = 0 THEN NULL
             ELSE apg.carries::DOUBLE / apg.team_carries END AS carry_share_game,
        CASE WHEN COALESCE(rz.team_rz_targets, 0) = 0 THEN NULL
             ELSE apg.red_zone_targets::DOUBLE / rz.team_rz_targets END AS rz_target_share_game,
        CASE WHEN COALESCE(rz.team_rz_carries, 0) = 0 THEN NULL
             ELSE apg.red_zone_carries::DOUBLE / rz.team_rz_carries END AS rz_rush_share_game
    FROM agg_player_game apg
    LEFT JOIN team_rz rz ON rz.game_id = apg.game_id AND rz.posteam = apg.team
    WHERE apg.is_active
),
rolling AS (
    SELECT
        {_rolling_select_sql}
    FROM active_games
),
-- Every (season, week) a player's team(s) played, whether or not the
-- player themselves has an agg_player_game row that week. A player who
-- appears under multiple teams in a season (trade) gets the union of
-- both teams' weeks; see ASSUMPTIONS_AND_ISSUES.md Phase 3 note for the
-- known imprecision this introduces around a trade date.
team_weeks AS (
    SELECT home_team AS team, season, week FROM core_games
    UNION
    SELECT away_team AS team, season, week FROM core_games
),
player_teams AS (
    SELECT DISTINCT player_id, season, team FROM agg_player_game
),
player_weeks AS (
    SELECT DISTINCT pt.player_id, pt.season, tw.week
    FROM player_teams pt
    JOIN team_weeks tw ON tw.team = pt.team AND tw.season = pt.season
),
full_grain AS (
    SELECT
        pw.player_id,
        pw.season,
        pw.week,
        COALESCE(r.is_active_week, FALSE) AS is_active_week,
        SUM(CASE WHEN r.is_active_week THEN 1 ELSE 0 END) OVER (
            PARTITION BY pw.player_id, pw.season ORDER BY pw.week
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS grp,
        {", ".join(f"r.{c}" for c in CARRY_FORWARD_COLUMNS)}
    FROM player_weeks pw
    LEFT JOIN rolling r ON r.player_id = pw.player_id AND r.season = pw.season AND r.week = pw.week
)
SELECT
    {_carry_forward_select_sql}
FROM full_grain
"""


def load_agg_player_season_to_date(con) -> int:
    con.execute(CREATE_TABLE_SQL)
    # Migration for pre-existing tables built before is_active_week existed.
    con.execute("ALTER TABLE agg_player_season_to_date ADD COLUMN IF NOT EXISTS is_active_week BOOLEAN;")
    # Migration for pre-existing tables built before the l1 window existed
    # (and forward-compatible if another window ever gets added the same
    # way -- ADD COLUMN IF NOT EXISTS is a no-op for columns already there).
    for col in WINDOWED_COLUMNS:
        con.execute(f"ALTER TABLE agg_player_season_to_date ADD COLUMN IF NOT EXISTS {col} DOUBLE;")

    cols = ", ".join(AGG_PLAYER_SEASON_COLUMNS)
    update_clause = ",\n            ".join(
        f"{c} = excluded.{c}" for c in AGG_PLAYER_SEASON_COLUMNS
        if c not in ("player_id", "season", "week")
    )
    con.execute(f"""
        INSERT INTO agg_player_season_to_date ({cols})
        {AGG_PLAYER_SEASON_SQL}
        ON CONFLICT (player_id, season, week) DO UPDATE SET
            {update_clause}
    """)
    return con.execute("SELECT COUNT(*) FROM agg_player_season_to_date").fetchone()[0]


if __name__ == "__main__":
    con = get_connection()
    print(f"agg_player_season_to_date: {load_agg_player_season_to_date(con)} total rows")
    con.close()
