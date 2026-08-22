"""Compute agg_player_game and agg_team_game from pbp_plays + participation.

Must run after ingest_pbp.py (needs pbp_plays/core_games/core_teams) and
ingest_rosters.py (needs core_players, already filtered to skill positions).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import get_connection
from ingestion.ingest_participation import build_participation_df

AGG_PLAYER_GAME_COLUMNS = [
    "player_id", "game_id", "season", "week", "team", "position",
    "is_active", "player_type", "targets", "carries", "pass_attempts",
    "team_targets", "team_carries", "team_pass_attempts",
    "total_rush_yards", "total_receiving_yards", "total_receptions",
    "touchdowns", "passing_yards", "yards", "epa_avg",
    "red_zone_targets", "red_zone_carries", "red_zone_attempts",
    "air_yards", "snap_count", "snap_pct", "routes_run",
]

AGG_PLAYER_GAME_SQL = """
WITH passing AS (
    SELECT
        passer_player_id AS player_id,
        game_id,
        posteam AS team,
        COUNT(*) FILTER (WHERE pass_attempt) AS pass_attempts,
        SUM(yards_gained) FILTER (WHERE pass_attempt AND complete_pass) AS passing_yards,
        SUM(air_yards) FILTER (WHERE pass_attempt) AS air_yards_thrown,
        AVG(epa) FILTER (WHERE pass_attempt) AS epa_avg_passing,
        COUNT(*) FILTER (WHERE pass_attempt AND yardline_100 <= 20) AS red_zone_attempts
    FROM pbp_plays
    WHERE passer_player_id IS NOT NULL
    GROUP BY passer_player_id, game_id, posteam
),
rushing AS (
    SELECT
        rusher_player_id AS player_id,
        game_id,
        posteam AS team,
        COUNT(*) FILTER (WHERE rush_attempt) AS carries,
        SUM(yards_gained) FILTER (WHERE rush_attempt) AS total_rush_yards,
        AVG(epa) FILTER (WHERE rush_attempt) AS epa_avg_rushing,
        COUNT(*) FILTER (WHERE rush_attempt AND yardline_100 <= 20) AS red_zone_carries
    FROM pbp_plays
    WHERE rusher_player_id IS NOT NULL
    GROUP BY rusher_player_id, game_id, posteam
),
receiving AS (
    SELECT
        receiver_player_id AS player_id,
        game_id,
        posteam AS team,
        COUNT(*) FILTER (WHERE pass_attempt) AS targets,
        SUM(yards_gained) FILTER (WHERE pass_attempt AND complete_pass) AS total_receiving_yards,
        COUNT(*) FILTER (WHERE complete_pass) AS total_receptions,
        SUM(air_yards) FILTER (WHERE pass_attempt) AS air_yards_targeted,
        AVG(epa) FILTER (WHERE pass_attempt) AS epa_avg_receiving,
        COUNT(*) FILTER (WHERE pass_attempt AND yardline_100 <= 20) AS red_zone_targets
    FROM pbp_plays
    WHERE receiver_player_id IS NOT NULL
    GROUP BY receiver_player_id, game_id, posteam
),
touchdowns AS (
    SELECT
        td_player_id AS player_id,
        game_id,
        COUNT(*) AS touchdowns
    FROM pbp_plays
    WHERE touchdown AND td_player_id IS NOT NULL
    GROUP BY td_player_id, game_id
),
team_offense AS (
    SELECT
        posteam AS team,
        game_id,
        COUNT(*) FILTER (WHERE pass_attempt) AS team_pass_attempts,
        COUNT(*) FILTER (WHERE pass_attempt AND receiver_player_id IS NOT NULL) AS team_targets,
        COUNT(*) FILTER (WHERE rush_attempt) AS team_carries
    FROM pbp_plays
    WHERE posteam IS NOT NULL
    GROUP BY posteam, game_id
),
keys AS (
    SELECT player_id, game_id FROM passing
    UNION SELECT player_id, game_id FROM rushing
    UNION SELECT player_id, game_id FROM receiving
    UNION SELECT player_id, game_id FROM touchdowns
    UNION SELECT player_id, game_id FROM stg_participation
)
SELECT
    k.player_id,
    k.game_id,
    g.season,
    g.week,
    COALESCE(p.team, r.team, rec.team, sp.team) AS team,
    cp.position,
    (sp.player_id IS NOT NULL
        OR COALESCE(p.pass_attempts, 0) > 0
        OR COALESCE(r.carries, 0) > 0
        OR COALESCE(rec.targets, 0) > 0) AS is_active,
    CASE WHEN cp.position = 'QB' THEN 'QB' ELSE 'skill' END AS player_type,
    COALESCE(rec.targets, 0) AS targets,
    COALESCE(r.carries, 0) AS carries,
    COALESCE(p.pass_attempts, 0) AS pass_attempts,
    to_.team_targets,
    to_.team_carries,
    to_.team_pass_attempts,
    COALESCE(r.total_rush_yards, 0) AS total_rush_yards,
    COALESCE(rec.total_receiving_yards, 0) AS total_receiving_yards,
    COALESCE(rec.total_receptions, 0) AS total_receptions,
    COALESCE(td.touchdowns, 0) AS touchdowns,
    COALESCE(p.passing_yards, 0) AS passing_yards,
    COALESCE(p.passing_yards, 0) + COALESCE(r.total_rush_yards, 0) + COALESCE(rec.total_receiving_yards, 0) AS yards,
    CASE
        WHEN COALESCE(p.pass_attempts, 0) + COALESCE(r.carries, 0) + COALESCE(rec.targets, 0) = 0 THEN NULL
        ELSE (
            COALESCE(p.epa_avg_passing, 0) * COALESCE(p.pass_attempts, 0)
            + COALESCE(r.epa_avg_rushing, 0) * COALESCE(r.carries, 0)
            + COALESCE(rec.epa_avg_receiving, 0) * COALESCE(rec.targets, 0)
        ) / (COALESCE(p.pass_attempts, 0) + COALESCE(r.carries, 0) + COALESCE(rec.targets, 0))
    END AS epa_avg,
    COALESCE(rec.red_zone_targets, 0) AS red_zone_targets,
    COALESCE(r.red_zone_carries, 0) AS red_zone_carries,
    COALESCE(p.red_zone_attempts, 0) AS red_zone_attempts,
    COALESCE(p.air_yards_thrown, rec.air_yards_targeted, 0) AS air_yards,
    sp.snap_count,
    sp.snap_pct,
    -- Not sourced from pbp_plays.route: nfl_data_py flattens participation
    -- data to one row per play, so `route` only ever captures the targeted
    -- receiver's route. Counting it per receiver/game is ~equal to targets
    -- (verified: never exceeds it, matches 96% of the time), not a real
    -- routes-run count. Left NULL until a true per-play multi-player
    -- participation source is wired in.
    CAST(NULL AS INTEGER) AS routes_run
FROM keys k
JOIN core_players cp ON cp.player_id = k.player_id
JOIN core_games g ON g.game_id = k.game_id
LEFT JOIN passing p ON p.player_id = k.player_id AND p.game_id = k.game_id
LEFT JOIN rushing r ON r.player_id = k.player_id AND r.game_id = k.game_id
LEFT JOIN receiving rec ON rec.player_id = k.player_id AND rec.game_id = k.game_id
LEFT JOIN touchdowns td ON td.player_id = k.player_id AND td.game_id = k.game_id
LEFT JOIN stg_participation sp ON sp.player_id = k.player_id AND sp.game_id = k.game_id
LEFT JOIN team_offense to_
    ON to_.team = COALESCE(p.team, r.team, rec.team, sp.team) AND to_.game_id = k.game_id
"""

AGG_TEAM_GAME_COLUMNS = [
    "team", "game_id", "season", "week", "total_plays", "pass_plays",
    "run_plays", "total_pass_yards", "total_rush_yards",
    "third_down_attempts", "third_down_conversions", "third_down_pct",
    "red_zone_trips", "red_zone_plays", "red_zone_pass_plays",
    "red_zone_run_plays", "fourth_down_attempts", "fourth_down_conversions",
    "avg_epa",
]

AGG_TEAM_GAME_SQL = """
WITH team_plays AS (
    SELECT
        posteam AS team,
        game_id,
        COUNT(*) AS total_plays,
        COUNT(*) FILTER (WHERE pass_attempt) AS pass_plays,
        COUNT(*) FILTER (WHERE rush_attempt) AS run_plays,
        SUM(yards_gained) FILTER (WHERE pass_attempt AND complete_pass) AS total_pass_yards,
        SUM(yards_gained) FILTER (WHERE rush_attempt) AS total_rush_yards,
        COUNT(*) FILTER (WHERE down = 3) AS third_down_attempts,
        COUNT(*) FILTER (WHERE down = 3 AND first_down) AS third_down_conversions,
        COUNT(*) FILTER (WHERE down = 4) AS fourth_down_attempts,
        COUNT(*) FILTER (WHERE down = 4 AND first_down) AS fourth_down_conversions,
        COUNT(*) FILTER (WHERE yardline_100 <= 20) AS red_zone_plays,
        COUNT(*) FILTER (WHERE yardline_100 <= 20 AND pass_attempt) AS red_zone_pass_plays,
        COUNT(*) FILTER (WHERE yardline_100 <= 20 AND rush_attempt) AS red_zone_run_plays,
        AVG(epa) AS avg_epa
    FROM pbp_plays
    WHERE posteam IS NOT NULL
    GROUP BY posteam, game_id
),
red_zone_drives AS (
    SELECT posteam AS team, game_id, drive, COUNT(*) AS n
    FROM pbp_plays
    WHERE posteam IS NOT NULL AND drive IS NOT NULL AND yardline_100 <= 20
    GROUP BY posteam, game_id, drive
),
red_zone_trips AS (
    SELECT team, game_id, COUNT(*) AS red_zone_trips
    FROM red_zone_drives
    GROUP BY team, game_id
)
SELECT
    tp.team,
    tp.game_id,
    g.season,
    g.week,
    tp.total_plays,
    tp.pass_plays,
    tp.run_plays,
    tp.total_pass_yards,
    tp.total_rush_yards,
    tp.third_down_attempts,
    tp.third_down_conversions,
    CASE WHEN tp.third_down_attempts = 0 THEN NULL
         ELSE tp.third_down_conversions::DOUBLE / tp.third_down_attempts END AS third_down_pct,
    COALESCE(rzt.red_zone_trips, 0) AS red_zone_trips,
    tp.red_zone_plays,
    tp.red_zone_pass_plays,
    tp.red_zone_run_plays,
    tp.fourth_down_attempts,
    tp.fourth_down_conversions,
    tp.avg_epa
FROM team_plays tp
JOIN core_games g ON g.game_id = tp.game_id
LEFT JOIN red_zone_trips rzt ON rzt.team = tp.team AND rzt.game_id = tp.game_id
"""


def load_agg_player_game(con) -> int:
    participation = build_participation_df()
    con.register("stg_participation", participation)

    cols = ", ".join(AGG_PLAYER_GAME_COLUMNS)
    update_clause = ",\n            ".join(
        f"{c} = excluded.{c}" for c in AGG_PLAYER_GAME_COLUMNS if c not in ("player_id", "game_id")
    )
    con.execute(f"""
        INSERT INTO agg_player_game ({cols})
        {AGG_PLAYER_GAME_SQL}
        ON CONFLICT (player_id, game_id) DO UPDATE SET
            {update_clause}
    """)
    con.unregister("stg_participation")
    return con.execute("SELECT COUNT(*) FROM agg_player_game").fetchone()[0]


def load_agg_team_game(con) -> int:
    cols = ", ".join(AGG_TEAM_GAME_COLUMNS)
    update_clause = ",\n            ".join(
        f"{c} = excluded.{c}" for c in AGG_TEAM_GAME_COLUMNS if c not in ("team", "game_id")
    )
    con.execute(f"""
        INSERT INTO agg_team_game ({cols})
        {AGG_TEAM_GAME_SQL}
        ON CONFLICT (team, game_id) DO UPDATE SET
            {update_clause}
    """)
    return con.execute("SELECT COUNT(*) FROM agg_team_game").fetchone()[0]


if __name__ == "__main__":
    con = get_connection()
    print(f"agg_player_game: {load_agg_player_game(con)} total rows")
    print(f"agg_team_game: {load_agg_team_game(con)} total rows")
    con.close()
