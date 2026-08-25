import streamlit as st

import queries_ext
from components import utils

COLUMNS = [
    ("Player", "player_name", "str"),
    ("Team", "team", "str"),
    ("Opponent", "opponent", "str"),
    ("TD Prob", "td_probability", "pct1"),
    ("Matchup", "opp_rank_rush_yards_allowed_vs_rb", "matchup"),
    ("Opp Avg Allowed", "opp_avg_rush_yards_allowed_vs_rb", "float1"),
    ("STD Carries", "szn_carries", "int"),
    ("STD Rush Yds", "szn_rushing_yards", "int"),
    ("STD Avg Yds/G", "szn_avg_rush_yds", "float1"),
    ("L5 Carries", "l5_carries", "int"),
    ("L5 Rush Yds", "l5_rushing_yards", "int"),
    ("L5 Avg Yds/G", "l5_avg_rush_yds", "float1"),
    ("L3 Carries", "l3_carries", "int"),
    ("L3 Rush Yds", "l3_rushing_yards", "int"),
    ("L3 Avg Yds/G", "l3_avg_rush_yds", "float1"),
]


@st.cache_data(ttl=300)
def _load(season: int, week: int, game_ids: tuple):
    return queries_ext.get_weekly_position_board(season, week, "RB", game_ids=list(game_ids))


def render(season: int, week: int, game_ids: list):
    df = _load(season, week, tuple(sorted(game_ids)))
    utils.render_position_tab(df, COLUMNS, rank_col="opp_rank_rush_yards_allowed_vs_rb", key_prefix="rb_rush")
