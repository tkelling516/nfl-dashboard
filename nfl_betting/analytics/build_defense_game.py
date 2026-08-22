"""Compute agg_team_defense_game from pbp_plays + agg_player_game.

One row per defending team per game. Position attribution (RB/WR/TE) is
pulled from agg_player_game (position AT GAME TIME), never core_players,
per the handoff doc's warning about core_players only holding each
player's most-recent-season position.

Must run after ingest_pbp.py and ingest_aggregates.py (needs pbp_plays
and agg_player_game populated).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import get_connection

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS agg_team_defense_game (
    defteam                              VARCHAR,
    game_id                              VARCHAR,
    season                                INTEGER,
    week                                  INTEGER,
    rush_yards_allowed                    INTEGER,
    rush_yards_allowed_vs_rb              INTEGER,
    rush_attempts_allowed                 INTEGER,
    rush_attempts_allowed_vs_rb           INTEGER,
    rush_tds_allowed                      INTEGER,
    rush_tds_allowed_vs_rb                INTEGER,
    rush_yards_per_carry_allowed          DOUBLE,
    rush_yards_per_carry_allowed_vs_rb    DOUBLE,
    rec_yards_allowed_vs_wr               INTEGER,
    rec_yards_allowed_vs_te               INTEGER,
    rec_yards_allowed_vs_rb               INTEGER,
    receptions_allowed_vs_wr              INTEGER,
    receptions_allowed_vs_te              INTEGER,
    receptions_allowed_vs_rb              INTEGER,
    targets_allowed_vs_wr                 INTEGER,
    targets_allowed_vs_te                 INTEGER,
    targets_allowed_vs_rb                 INTEGER,
    rec_tds_allowed_vs_wr                 INTEGER,
    rec_tds_allowed_vs_te                 INTEGER,
    rec_tds_allowed_vs_rb                 INTEGER,
    yards_per_target_allowed_vs_wr        DOUBLE,
    yards_per_target_allowed_vs_te        DOUBLE,
    yards_per_target_allowed_vs_rb        DOUBLE,
    pass_yards_allowed                    INTEGER,
    pass_attempts_allowed                 INTEGER,
    pass_completions_allowed              INTEGER,
    pass_tds_allowed                      INTEGER,
    completion_pct_allowed                DOUBLE,
    rz_rush_yards_allowed_vs_rb           INTEGER,
    rz_rec_yards_allowed_vs_wr            INTEGER,
    rz_rec_yards_allowed_vs_te            INTEGER,
    rz_targets_allowed_vs_wr              INTEGER,
    rz_targets_allowed_vs_te              INTEGER,
    rz_tds_allowed                        INTEGER,
    avg_defenders_in_box                  DOUBLE,
    defenders_in_box_coverage_rate        DOUBLE,
    avg_time_to_throw                     DOUBLE,
    time_to_throw_coverage_rate           DOUBLE,
    pressure_rate                         DOUBLE,
    pressure_coverage_rate                DOUBLE,
    PRIMARY KEY (defteam, game_id)
);
"""

AGG_TEAM_DEFENSE_GAME_COLUMNS = [
    "defteam", "game_id", "season", "week",
    "rush_yards_allowed", "rush_yards_allowed_vs_rb",
    "rush_attempts_allowed", "rush_attempts_allowed_vs_rb",
    "rush_tds_allowed", "rush_tds_allowed_vs_rb",
    "rush_yards_per_carry_allowed", "rush_yards_per_carry_allowed_vs_rb",
    "rec_yards_allowed_vs_wr", "rec_yards_allowed_vs_te", "rec_yards_allowed_vs_rb",
    "receptions_allowed_vs_wr", "receptions_allowed_vs_te", "receptions_allowed_vs_rb",
    "targets_allowed_vs_wr", "targets_allowed_vs_te", "targets_allowed_vs_rb",
    "rec_tds_allowed_vs_wr", "rec_tds_allowed_vs_te", "rec_tds_allowed_vs_rb",
    "yards_per_target_allowed_vs_wr", "yards_per_target_allowed_vs_te", "yards_per_target_allowed_vs_rb",
    "pass_yards_allowed", "pass_attempts_allowed", "pass_completions_allowed",
    "pass_tds_allowed", "completion_pct_allowed",
    "rz_rush_yards_allowed_vs_rb", "rz_rec_yards_allowed_vs_wr", "rz_rec_yards_allowed_vs_te",
    "rz_targets_allowed_vs_wr", "rz_targets_allowed_vs_te", "rz_tds_allowed",
    "avg_defenders_in_box", "defenders_in_box_coverage_rate",
    "avg_time_to_throw", "time_to_throw_coverage_rate",
    "pressure_rate", "pressure_coverage_rate",
]

# Plays excluded from every count below: special-teams plays that aren't
# meaningful "defense allowed X" events, plus two-point conversion attempts
# (box scores don't count these as rushing/receiving/passing attempts).
# NOTE: `penalty=true` is deliberately NOT excluded -- nflverse's `penalty`
# flag just means *a* penalty occurred on the play, not that the play's
# yardage was negated. Truly negated plays already carry play_type='no_play'
# (excluded above). Excluding all penalty=true rows was an earlier bug here:
# it dropped ~1,059 live plays (541 run + 522 pass) that still count in the
# official box score (verified against the 2024_01_ARI_BUF box score).
AGG_TEAM_DEFENSE_GAME_SQL = """
WITH base AS (
    SELECT
        p.*,
        rp.position AS rusher_position,
        rc.position AS receiver_position
    FROM pbp_plays p
    LEFT JOIN agg_player_game rp
        ON rp.game_id = p.game_id AND rp.player_id = p.rusher_player_id
    LEFT JOIN agg_player_game rc
        ON rc.game_id = p.game_id AND rc.player_id = p.receiver_player_id
    WHERE p.defteam IS NOT NULL
      AND (p.play_type IS NULL OR p.play_type NOT IN ('punt', 'kickoff', 'extra_point', 'no_play'))
      AND COALESCE(p.two_point_attempt, FALSE) = FALSE
)
SELECT
    defteam,
    game_id,
    season,
    week,

    -- Rushing allowed
    SUM(yards_gained) FILTER (WHERE rush_attempt) AS rush_yards_allowed,
    SUM(yards_gained) FILTER (WHERE rush_attempt AND rusher_position = 'RB') AS rush_yards_allowed_vs_rb,
    COUNT(*) FILTER (WHERE rush_attempt) AS rush_attempts_allowed,
    COUNT(*) FILTER (WHERE rush_attempt AND rusher_position = 'RB') AS rush_attempts_allowed_vs_rb,
    COUNT(*) FILTER (WHERE rush_attempt AND touchdown) AS rush_tds_allowed,
    COUNT(*) FILTER (WHERE rush_attempt AND touchdown AND rusher_position = 'RB') AS rush_tds_allowed_vs_rb,
    CASE WHEN COUNT(*) FILTER (WHERE rush_attempt) = 0 THEN NULL
         ELSE SUM(yards_gained) FILTER (WHERE rush_attempt)::DOUBLE
              / COUNT(*) FILTER (WHERE rush_attempt) END AS rush_yards_per_carry_allowed,
    CASE WHEN COUNT(*) FILTER (WHERE rush_attempt AND rusher_position = 'RB') = 0 THEN NULL
         ELSE SUM(yards_gained) FILTER (WHERE rush_attempt AND rusher_position = 'RB')::DOUBLE
              / COUNT(*) FILTER (WHERE rush_attempt AND rusher_position = 'RB') END AS rush_yards_per_carry_allowed_vs_rb,

    -- Receiving allowed
    SUM(yards_gained) FILTER (WHERE complete_pass AND receiver_position = 'WR') AS rec_yards_allowed_vs_wr,
    SUM(yards_gained) FILTER (WHERE complete_pass AND receiver_position = 'TE') AS rec_yards_allowed_vs_te,
    SUM(yards_gained) FILTER (WHERE complete_pass AND receiver_position = 'RB') AS rec_yards_allowed_vs_rb,
    COUNT(*) FILTER (WHERE complete_pass AND receiver_position = 'WR') AS receptions_allowed_vs_wr,
    COUNT(*) FILTER (WHERE complete_pass AND receiver_position = 'TE') AS receptions_allowed_vs_te,
    COUNT(*) FILTER (WHERE complete_pass AND receiver_position = 'RB') AS receptions_allowed_vs_rb,
    COUNT(*) FILTER (WHERE pass_attempt AND receiver_position = 'WR') AS targets_allowed_vs_wr,
    COUNT(*) FILTER (WHERE pass_attempt AND receiver_position = 'TE') AS targets_allowed_vs_te,
    COUNT(*) FILTER (WHERE pass_attempt AND receiver_position = 'RB') AS targets_allowed_vs_rb,
    COUNT(*) FILTER (WHERE complete_pass AND touchdown AND receiver_position = 'WR') AS rec_tds_allowed_vs_wr,
    COUNT(*) FILTER (WHERE complete_pass AND touchdown AND receiver_position = 'TE') AS rec_tds_allowed_vs_te,
    COUNT(*) FILTER (WHERE complete_pass AND touchdown AND receiver_position = 'RB') AS rec_tds_allowed_vs_rb,
    CASE WHEN COUNT(*) FILTER (WHERE pass_attempt AND receiver_position = 'WR') = 0 THEN NULL
         ELSE SUM(yards_gained) FILTER (WHERE complete_pass AND receiver_position = 'WR')::DOUBLE
              / COUNT(*) FILTER (WHERE pass_attempt AND receiver_position = 'WR') END AS yards_per_target_allowed_vs_wr,
    CASE WHEN COUNT(*) FILTER (WHERE pass_attempt AND receiver_position = 'TE') = 0 THEN NULL
         ELSE SUM(yards_gained) FILTER (WHERE complete_pass AND receiver_position = 'TE')::DOUBLE
              / COUNT(*) FILTER (WHERE pass_attempt AND receiver_position = 'TE') END AS yards_per_target_allowed_vs_te,
    CASE WHEN COUNT(*) FILTER (WHERE pass_attempt AND receiver_position = 'RB') = 0 THEN NULL
         ELSE SUM(yards_gained) FILTER (WHERE complete_pass AND receiver_position = 'RB')::DOUBLE
              / COUNT(*) FILTER (WHERE pass_attempt AND receiver_position = 'RB') END AS yards_per_target_allowed_vs_rb,

    -- Passing allowed (QB-facing, all receiver positions combined)
    SUM(yards_gained) FILTER (WHERE complete_pass) AS pass_yards_allowed,
    COUNT(*) FILTER (WHERE pass_attempt) AS pass_attempts_allowed,
    COUNT(*) FILTER (WHERE complete_pass) AS pass_completions_allowed,
    COUNT(*) FILTER (WHERE pass_attempt AND touchdown) AS pass_tds_allowed,
    CASE WHEN COUNT(*) FILTER (WHERE pass_attempt) = 0 THEN NULL
         ELSE COUNT(*) FILTER (WHERE complete_pass)::DOUBLE
              / COUNT(*) FILTER (WHERE pass_attempt) END AS completion_pct_allowed,

    -- Red zone allowed
    SUM(yards_gained) FILTER (WHERE rush_attempt AND yardline_100 <= 20 AND rusher_position = 'RB') AS rz_rush_yards_allowed_vs_rb,
    SUM(yards_gained) FILTER (WHERE complete_pass AND yardline_100 <= 20 AND receiver_position = 'WR') AS rz_rec_yards_allowed_vs_wr,
    SUM(yards_gained) FILTER (WHERE complete_pass AND yardline_100 <= 20 AND receiver_position = 'TE') AS rz_rec_yards_allowed_vs_te,
    COUNT(*) FILTER (WHERE pass_attempt AND yardline_100 <= 20 AND receiver_position = 'WR') AS rz_targets_allowed_vs_wr,
    COUNT(*) FILTER (WHERE pass_attempt AND yardline_100 <= 20 AND receiver_position = 'TE') AS rz_targets_allowed_vs_te,
    -- Offense-only: excludes defensive/return scores (pick-sixes etc). A
    -- play is never both `rush_attempt`/`complete_pass` and `return_team
    -- IS NOT NULL` (verified against the data), so this filter cleanly
    -- isolates rushing + receiving TDs without any return-TD leakage.
    COUNT(*) FILTER (WHERE touchdown AND yardline_100 <= 20 AND (rush_attempt OR complete_pass)) AS rz_tds_allowed,

    -- NGS-derived (sparse ~40-45% coverage): never treat NULL as 0, track
    -- coverage_rate alongside every average so downstream code can judge
    -- confidence in the metric.
    AVG(defenders_in_box) FILTER (WHERE rush_attempt AND defenders_in_box IS NOT NULL) AS avg_defenders_in_box,
    CASE WHEN COUNT(*) FILTER (WHERE rush_attempt) = 0 THEN 0.0
         ELSE COUNT(*) FILTER (WHERE rush_attempt AND defenders_in_box IS NOT NULL)::DOUBLE
              / COUNT(*) FILTER (WHERE rush_attempt) END AS defenders_in_box_coverage_rate,
    AVG(time_to_throw) FILTER (WHERE pass_attempt AND time_to_throw IS NOT NULL) AS avg_time_to_throw,
    CASE WHEN COUNT(*) FILTER (WHERE pass_attempt) = 0 THEN 0.0
         ELSE COUNT(*) FILTER (WHERE pass_attempt AND time_to_throw IS NOT NULL)::DOUBLE
              / COUNT(*) FILTER (WHERE pass_attempt) END AS time_to_throw_coverage_rate,
    CASE WHEN COUNT(*) FILTER (WHERE pass_attempt AND was_pressure IS NOT NULL) = 0 THEN 0.0
         ELSE COUNT(*) FILTER (WHERE pass_attempt AND was_pressure)::DOUBLE
              / COUNT(*) FILTER (WHERE pass_attempt AND was_pressure IS NOT NULL) END AS pressure_rate,
    CASE WHEN COUNT(*) FILTER (WHERE pass_attempt) = 0 THEN 0.0
         ELSE COUNT(*) FILTER (WHERE pass_attempt AND was_pressure IS NOT NULL)::DOUBLE
              / COUNT(*) FILTER (WHERE pass_attempt) END AS pressure_coverage_rate

FROM base
GROUP BY defteam, game_id, season, week
"""


def load_agg_team_defense_game(con) -> int:
    con.execute(CREATE_TABLE_SQL)

    cols = ", ".join(AGG_TEAM_DEFENSE_GAME_COLUMNS)
    update_clause = ",\n            ".join(
        f"{c} = excluded.{c}" for c in AGG_TEAM_DEFENSE_GAME_COLUMNS if c not in ("defteam", "game_id")
    )
    con.execute(f"""
        INSERT INTO agg_team_defense_game ({cols})
        {AGG_TEAM_DEFENSE_GAME_SQL}
        ON CONFLICT (defteam, game_id) DO UPDATE SET
            {update_clause}
    """)
    return con.execute("SELECT COUNT(*) FROM agg_team_defense_game").fetchone()[0]


if __name__ == "__main__":
    con = get_connection()
    print(f"agg_team_defense_game: {load_agg_team_defense_game(con)} total rows")
    con.close()
