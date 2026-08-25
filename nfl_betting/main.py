"""Run the full NFL data ingestion pipeline in order."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import SEASONS
from db import get_connection
from ingestion.ingest_rosters import load_core_players
from ingestion.ingest_pbp import _load_pbp, _load_schedules, load_core_teams, load_core_games, load_pbp_plays
from ingestion.ingest_aggregates import load_agg_player_game, load_agg_team_game


def main() -> None:
    con = get_connection()
    print(f"Running pipeline for seasons {SEASONS}\n")

    print("[1/5] Loading rosters -> core_players")
    print(f"  upserted {load_core_players(con)} players")

    print("[2/5] Loading play-by-play -> core_teams, core_games, pbp_plays")
    pbp = _load_pbp()
    schedules = _load_schedules()
    print(f"  loaded {len(pbp)} plays")
    print(f"  upserted {load_core_teams(con, pbp)} teams")
    print(f"  upserted {load_core_games(con, pbp, schedules)} games")
    print(f"  upserted {load_pbp_plays(con, pbp)} plays")
    del pbp

    print("[3/5] Computing agg_player_game (incl. participation staging)")
    print(f"  total rows: {load_agg_player_game(con)}")

    print("[4/5] Computing agg_team_game")
    print(f"  total rows: {load_agg_team_game(con)}")

    print("[5/5] betting_* and external_* tables created empty (deferred ingestion)")

    con.close()
    print("\nPipeline complete.")


if __name__ == "__main__":
    main()
