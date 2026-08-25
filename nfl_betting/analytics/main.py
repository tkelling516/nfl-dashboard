"""Run the full analytics-layer build pipeline in dependency order.

Assumes core_* / pbp_plays / agg_player_game / agg_team_game are already
populated (nfl_betting/main.py). Safe to re-run -- every step upserts
(ON CONFLICT DO UPDATE) or is a view (CREATE OR REPLACE).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import get_connection
from analytics.build_defense_game import load_agg_team_defense_game
from analytics.build_defense_season import load_agg_team_defense_season
from analytics.build_player_season import load_agg_player_season_to_date
from analytics.build_views import build_views
from analytics.build_td_model import load_agg_td_probability


def main() -> None:
    con = get_connection()
    print("Running analytics layer build\n")

    print("[1/5] Computing agg_team_defense_game")
    print(f"  total rows: {load_agg_team_defense_game(con)}")

    print("[2/5] Computing agg_team_defense_season")
    print(f"  total rows: {load_agg_team_defense_season(con)}")

    print("[3/5] Computing agg_player_season_to_date")
    print(f"  total rows: {load_agg_player_season_to_date(con)}")

    print("[4/5] Creating views (view_red_zone_target_share, view_red_zone_rush_share)")
    for view, count in build_views(con).items():
        print(f"  {view}: {count} rows")

    print("[5/5] Training TD probability model -> agg_td_probability")
    print(f"  total rows: {load_agg_td_probability(con)}")

    con.close()
    print("\nAnalytics layer build complete.")


if __name__ == "__main__":
    main()
