import streamlit as st

import queries_ext
from components import utils

COLUMNS = [
    ("Player", "player_name", "str"),
    ("Team", "team", "str"),
    ("Opponent", "opponent", "str"),
    ("TD Prob", "td_probability", "pct1"),
    ("Matchup", "opp_rank_rec_yards_allowed_vs_te", "matchup"),
    ("Opp Avg Allowed", "opp_avg_rec_yards_allowed_vs_te", "float1"),
    ("YTD Targets", "szn_targets", "int"),
    ("YTD Receptions", "szn_receptions", "int"),
    ("YTD Rec Yds", "szn_receiving_yards", "int"),
    ("YTD Avg Yds/G", "szn_avg_rec_yds", "float1"),
    ("L5 Targets", "l5_targets", "int"),
    ("L5 Receptions", "l5_receptions", "int"),
    ("L5 Rec Yds", "l5_receiving_yards", "int"),
    ("L5 Avg Yds/G", "l5_avg_rec_yds", "float1"),
    ("L3 Targets", "l3_targets", "int"),
    ("L3 Receptions", "l3_receptions", "int"),
    ("L3 Rec Yds", "l3_receiving_yards", "int"),
    ("L3 Avg Yds/G", "l3_avg_rec_yds", "float1"),
]


@st.cache_data(ttl=300)
def _load(season: int, week: int, game_ids: tuple):
    return queries_ext.get_weekly_position_board(season, week, "TE", game_ids=list(game_ids))


def render(season: int, week: int, game_ids: list):
    df = _load(season, week, tuple(sorted(game_ids)))
    utils.render_position_tab(df, COLUMNS, rank_col="opp_rank_rec_yards_allowed_vs_te", key_prefix="te_rec")
