"""Populate core_players from seasonal roster data, filtered to skill positions."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import nfl_data_py as nfl

from config import SEASONS, SKILL_POSITIONS
from db import get_connection


def load_core_players(con) -> int:
    rosters = nfl.import_seasonal_rosters(SEASONS)
    rosters = rosters[rosters["position"].isin(SKILL_POSITIONS)]
    rosters = rosters.dropna(subset=["player_id", "player_name"])

    # A player can appear in multiple seasons; keep their most recent
    # season's name/position as the canonical record.
    rosters = rosters.sort_values("season").drop_duplicates("player_id", keep="last")

    players = rosters[["player_id", "player_name", "position"]].copy()
    players["player_name_normalized"] = players["player_name"].str.lower().str.strip()
    players = players[["player_id", "player_name", "player_name_normalized", "position"]]

    con.register("players_df", players)
    con.execute("""
        INSERT INTO core_players
        SELECT * FROM players_df
        ON CONFLICT (player_id) DO UPDATE SET
            player_name = excluded.player_name,
            player_name_normalized = excluded.player_name_normalized,
            position = excluded.position
    """)
    con.unregister("players_df")
    return len(players)


if __name__ == "__main__":
    con = get_connection()
    n = load_core_players(con)
    print(f"core_players: upserted {n} rows")
    con.close()
