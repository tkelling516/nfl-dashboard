"""Populate core_teams, core_games, and pbp_plays from play-by-play data."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
import nfl_data_py as nfl

from config import SEASONS
from db import get_connection

PBP_COLUMNS = [
    "game_id", "play_id", "drive", "posteam", "defteam", "down", "ydstogo",
    "yardline_100", "play_type", "yards_gained", "epa", "wp",
    "passer_player_id", "rusher_player_id", "receiver_player_id",
    "air_yards", "first_down", "game_date", "season", "week",
    "touchdown", "td_player_id", "complete_pass", "pass_attempt",
    "rush_attempt", "return_team", "penalty",
    "route", "offense_personnel", "defenders_in_box", "was_pressure",
    "time_to_throw", "defense_coverage_type", "two_point_attempt",
    "goal_to_go", "home_team", "away_team",
]

BOOL_COLUMNS = [
    "first_down", "touchdown", "complete_pass", "pass_attempt",
    "rush_attempt", "penalty", "was_pressure", "two_point_attempt",
    "goal_to_go",
]

# Per-game context (spread, total, weather, division-game flag) -- lives on
# core_games (the game dimension), not pbp_plays. nfl_data_py serves it from
# a separate, much lighter schedules endpoint rather than the play-by-play
# merge; import_schedules()'s game_id matches pbp's game_id directly.
SCHEDULE_COLUMNS = ["game_id", "spread_line", "total_line", "roof", "temp", "wind", "div_game"]

# route/offense_personnel/defense_coverage_type come through as "" rather than
# NaN when untracked (e.g. non-pass plays) -- treat both as missing.
EMPTY_STRING_TO_NULL_COLUMNS = ["route", "offense_personnel", "defense_coverage_type"]

PBP_NON_KEY_COLUMNS = [c for c in PBP_COLUMNS if c not in ("game_id", "play_id", "home_team", "away_team")]


def _load_pbp() -> pd.DataFrame:
    # route/offense_personnel/defenders_in_box/was_pressure/time_to_throw/
    # defense_coverage_type only exist after nfl_data_py merges in
    # participation data -- passing them to `columns=` filters the *base*
    # pbp parquet before that merge happens and breaks the download. So we
    # fetch every column and subset with pandas afterward instead.
    pbp = nfl.import_pbp_data(SEASONS, columns=None, downcast=True)
    pbp = pbp[PBP_COLUMNS]
    for col in BOOL_COLUMNS:
        pbp[col] = pbp[col].fillna(0).astype(bool)
    for col in EMPTY_STRING_TO_NULL_COLUMNS:
        pbp[col] = pbp[col].replace("", None)
    pbp["game_date"] = pd.to_datetime(pbp["game_date"]).dt.date
    return pbp


def _load_schedules() -> pd.DataFrame:
    sched = nfl.import_schedules(SEASONS)[SCHEDULE_COLUMNS].copy()
    sched["div_game"] = sched["div_game"].fillna(0).astype(bool)
    return sched


def load_core_teams(con, pbp: pd.DataFrame) -> int:
    abbrs = pd.unique(pd.concat([
        pbp["posteam"], pbp["defteam"], pbp["home_team"], pbp["away_team"],
    ]).dropna())
    abbrs = [a for a in abbrs if a]

    desc = nfl.import_team_desc()[["team_abbr", "team_name"]]
    teams = pd.DataFrame({"team_abbr": abbrs}).merge(desc, on="team_abbr", how="left")
    teams["team_id"] = teams["team_abbr"]
    teams = teams[["team_id", "team_abbr", "team_name"]]

    con.register("teams_df", teams)
    con.execute("""
        INSERT INTO core_teams
        SELECT * FROM teams_df
        ON CONFLICT (team_id) DO UPDATE SET
            team_abbr = excluded.team_abbr,
            team_name = excluded.team_name
    """)
    con.unregister("teams_df")
    return len(teams)


def load_core_games(con, pbp: pd.DataFrame, schedules: pd.DataFrame) -> int:
    games = (
        pbp[["game_id", "game_date", "season", "week", "home_team", "away_team"]]
        .drop_duplicates("game_id")
        .merge(schedules, on="game_id", how="left")
    )
    con.register("games_df", games)
    con.execute("""
        INSERT INTO core_games
        SELECT * FROM games_df
        ON CONFLICT (game_id) DO UPDATE SET
            game_date = excluded.game_date,
            season = excluded.season,
            week = excluded.week,
            home_team = excluded.home_team,
            away_team = excluded.away_team,
            spread_line = excluded.spread_line,
            total_line = excluded.total_line,
            roof = excluded.roof,
            temp = excluded.temp,
            wind = excluded.wind,
            div_game = excluded.div_game
    """)
    con.unregister("games_df")
    return len(games)


def load_pbp_plays(con, pbp: pd.DataFrame) -> int:
    plays = pbp[[c for c in PBP_COLUMNS if c not in ("home_team", "away_team")]].dropna(subset=["game_id", "play_id"])
    insert_cols = [c for c in PBP_COLUMNS if c not in ("home_team", "away_team")]
    update_clause = ",\n            ".join(f"{c} = excluded.{c}" for c in PBP_NON_KEY_COLUMNS)

    con.register("plays_df", plays)
    con.execute(f"""
        INSERT INTO pbp_plays ({", ".join(insert_cols)})
        SELECT {", ".join(insert_cols)} FROM plays_df
        ON CONFLICT (game_id, play_id) DO UPDATE SET
            {update_clause}
    """)
    con.unregister("plays_df")
    return len(plays)


if __name__ == "__main__":
    con = get_connection()
    pbp = _load_pbp()
    schedules = _load_schedules()
    print(f"loaded {len(pbp)} pbp rows for seasons {SEASONS}")
    print(f"core_teams: upserted {load_core_teams(con, pbp)} rows")
    print(f"core_games: upserted {load_core_games(con, pbp, schedules)} rows")
    print(f"pbp_plays: upserted {load_pbp_plays(con, pbp)} rows")
    con.close()
