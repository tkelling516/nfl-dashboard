"""Load snap-count participation data and stage it (player_id, game_id) for
agg_player_game. Not a schema table on its own -- ingest_aggregates.py
consumes the DataFrame this module builds directly.

Note: nfl_data_py 0.3.3 exposes snap counts via import_snap_counts(),
keyed by pfr_player_id rather than the gsis player_id used everywhere
else in this schema. We cross-walk ids via import_ids().

routes_run is computed separately in ingest_aggregates.py from
pbp_plays.route (sourced from NGS participation data merged into
import_pbp_data()), not from this module.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from urllib.error import HTTPError

import pandas as pd
import nfl_data_py as nfl

from config import SEASONS


def _import_snap_counts_available(seasons) -> pd.DataFrame:
    # import_snap_counts() (unlike import_pbp_data()) raises instead of
    # skipping seasons with no data yet published (e.g. a season that
    # hasn't started), so fetch year-by-year and skip missing ones.
    frames = []
    for year in seasons:
        try:
            frames.append(nfl.import_snap_counts([year]))
        except HTTPError:
            print(f"snap counts not available for {year}, skipping")
    if not frames:
        return pd.DataFrame(columns=[
            "game_id", "season", "week", "player", "pfr_player_id",
            "position", "team", "offense_snaps", "offense_pct",
        ])
    return pd.concat(frames, ignore_index=True)


def build_participation_df(seasons=SEASONS) -> pd.DataFrame:
    snaps = _import_snap_counts_available(seasons)

    ids = nfl.import_ids(columns=["gsis_id", "pfr_id"])
    ids = ids.dropna(subset=["gsis_id", "pfr_id"]).drop_duplicates("pfr_id")

    merged = snaps.merge(
        ids, left_on="pfr_player_id", right_on="pfr_id", how="inner"
    )
    merged = merged.drop_duplicates(["gsis_id", "game_id"])

    participation = merged.rename(columns={
        "gsis_id": "player_id",
        "offense_snaps": "snap_count",
        "offense_pct": "snap_pct",
    })[["player_id", "game_id", "season", "week", "team", "snap_count", "snap_pct"]]

    return participation


if __name__ == "__main__":
    df = build_participation_df()
    print(f"staged {len(df)} player-game participation rows for seasons {SEASONS}")
