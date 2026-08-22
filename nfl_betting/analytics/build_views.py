"""Create view_red_zone_target_share and view_red_zone_rush_share.

Per-player-game red-zone target/carry share, layered on top of
agg_player_game. Views are virtual (re-evaluated on every query), so
"idempotent" here just means CREATE OR REPLACE.

Must run after ingest_aggregates.py (agg_player_game) and ingest_pbp.py
(pbp_plays) -- no dependency on the other analytics/build_*.py scripts.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import get_connection

# pass_attempt/rush_attempt are BOOLEAN columns in this schema (not 0/1
# integers) -- using them directly is equivalent to the spec's
# `pass_attempt = 1` / `rush_attempt = 1` and matches the boolean-filter
# style used everywhere else in this codebase.
CREATE_VIEW_RED_ZONE_TARGET_SHARE_SQL = """
CREATE OR REPLACE VIEW view_red_zone_target_share AS
SELECT
    apg.*,
    apg.red_zone_targets * 1.0 / NULLIF(team_rz.team_rz_targets, 0)
        AS red_zone_target_share
FROM agg_player_game apg
JOIN (
    SELECT game_id, posteam,
           SUM(1) FILTER (WHERE yardline_100 <= 20 AND pass_attempt) AS team_rz_targets
    FROM pbp_plays GROUP BY game_id, posteam
) team_rz ON apg.game_id = team_rz.game_id
         AND apg.team = team_rz.posteam
"""

CREATE_VIEW_RED_ZONE_RUSH_SHARE_SQL = """
CREATE OR REPLACE VIEW view_red_zone_rush_share AS
SELECT
    apg.*,
    apg.red_zone_carries * 1.0 / NULLIF(team_rz.team_rz_carries, 0)
        AS red_zone_rush_share
FROM agg_player_game apg
JOIN (
    SELECT game_id, posteam,
           SUM(1) FILTER (WHERE yardline_100 <= 20 AND rush_attempt) AS team_rz_carries
    FROM pbp_plays GROUP BY game_id, posteam
) team_rz ON apg.game_id = team_rz.game_id
         AND apg.team = team_rz.posteam
"""


def build_views(con) -> dict:
    con.execute(CREATE_VIEW_RED_ZONE_TARGET_SHARE_SQL)
    con.execute(CREATE_VIEW_RED_ZONE_RUSH_SHARE_SQL)
    return {
        "view_red_zone_target_share": con.execute(
            "SELECT COUNT(*) FROM view_red_zone_target_share"
        ).fetchone()[0],
        "view_red_zone_rush_share": con.execute(
            "SELECT COUNT(*) FROM view_red_zone_rush_share"
        ).fetchone()[0],
    }


if __name__ == "__main__":
    con = get_connection()
    counts = build_views(con)
    for view, count in counts.items():
        print(f"{view}: {count} rows")
    con.close()
