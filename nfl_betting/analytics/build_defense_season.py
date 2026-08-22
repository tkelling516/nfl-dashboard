"""Compute agg_team_defense_season from agg_team_defense_game.

One row per (defteam, season, week): season-to-date cumulative per-game
averages, plus league rankings (1=best defense, 32=worst) for the
prop-relevant categories, as of that week.

Must run after build_defense_game.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import get_connection
from analytics.build_defense_game import AGG_TEAM_DEFENSE_GAME_COLUMNS

# Every metric column from agg_team_defense_game (drops the dimension
# columns: defteam, game_id, season, week).
METRIC_COLUMNS = AGG_TEAM_DEFENSE_GAME_COLUMNS[4:]


def _avg_name(col: str) -> str:
    # A few source columns (avg_defenders_in_box, avg_time_to_throw) are
    # already per-game averages -- don't double-prefix into avg_avg_*.
    return col if col.startswith("avg_") else f"avg_{col}"


AVG_COLUMNS = [_avg_name(c) for c in METRIC_COLUMNS]

# Prop-relevant rankings: lower allowed = better defense = rank 1.
RANK_COLUMNS = {
    "rank_rush_yards_allowed_vs_rb": "avg_rush_yards_allowed_vs_rb",
    "rank_rec_yards_allowed_vs_wr": "avg_rec_yards_allowed_vs_wr",
    "rank_rec_yards_allowed_vs_te": "avg_rec_yards_allowed_vs_te",
    "rank_targets_allowed_vs_wr": "avg_targets_allowed_vs_wr",
    "rank_targets_allowed_vs_te": "avg_targets_allowed_vs_te",
    "rank_pass_yards_allowed": "avg_pass_yards_allowed",
    "rank_rz_tds_allowed": "avg_rz_tds_allowed",
}

AGG_TEAM_DEFENSE_SEASON_COLUMNS = (
    ["defteam", "season", "week", "games_played"]
    + AVG_COLUMNS
    + list(RANK_COLUMNS.keys())
)

_ddl_lines = (
    ["defteam VARCHAR", "season INTEGER", "week INTEGER", "games_played INTEGER"]
    + [f"{c} DOUBLE" for c in AVG_COLUMNS]
    + [f"{c} INTEGER" for c in RANK_COLUMNS]
)
CREATE_TABLE_SQL = "CREATE TABLE IF NOT EXISTS agg_team_defense_season (\n    " + \
    ",\n    ".join(_ddl_lines) + \
    ",\n    PRIMARY KEY (defteam, season, week)\n);"

# Running (season-to-date-through-this-week) average for every metric,
# via a window frame from the start of the season through the current row.
_running_select = (
    ["defteam", "season", "week",
     "COUNT(*) OVER w AS games_played"]
    + [f"AVG({src}) OVER w AS {avg}" for src, avg in zip(METRIC_COLUMNS, AVG_COLUMNS)]
)
_outer_select = (
    ["defteam", "season", "week", "games_played"]
    + AVG_COLUMNS
    + [
        f"RANK() OVER (PARTITION BY season, week ORDER BY {src} ASC) AS {rank}"
        for rank, src in RANK_COLUMNS.items()
    ]
)
_running_select_sql = ",\n        ".join(_running_select)
_outer_select_sql = ",\n    ".join(_outer_select)

AGG_TEAM_DEFENSE_SEASON_SQL = f"""
WITH running AS (
    SELECT
        {_running_select_sql}
    FROM agg_team_defense_game
    WINDOW w AS (
        PARTITION BY defteam, season ORDER BY week
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    )
)
SELECT
    {_outer_select_sql}
FROM running
"""


def load_agg_team_defense_season(con) -> int:
    con.execute(CREATE_TABLE_SQL)

    cols = ", ".join(AGG_TEAM_DEFENSE_SEASON_COLUMNS)
    update_clause = ",\n            ".join(
        f"{c} = excluded.{c}" for c in AGG_TEAM_DEFENSE_SEASON_COLUMNS
        if c not in ("defteam", "season", "week")
    )
    con.execute(f"""
        INSERT INTO agg_team_defense_season ({cols})
        {AGG_TEAM_DEFENSE_SEASON_SQL}
        ON CONFLICT (defteam, season, week) DO UPDATE SET
            {update_clause}
    """)
    return con.execute("SELECT COUNT(*) FROM agg_team_defense_season").fetchone()[0]


if __name__ == "__main__":
    con = get_connection()
    print(f"agg_team_defense_season: {load_agg_team_defense_season(con)} total rows")
    con.close()
