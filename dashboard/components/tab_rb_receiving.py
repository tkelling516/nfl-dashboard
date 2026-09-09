import streamlit as st

import queries_ext
from components import utils

COLUMNS = [
    ("Player", "player_name", "str"),
    ("Team", "team", "str"),
    ("Opponent", "opponent", "str"),
    ("TD Prob", "td_probability", "pct1"),
    ("Matchup", "opp_rank_rec_yards_allowed_vs_rb", "matchup"),
    ("Opp Avg Allowed", "opp_avg_rec_yards_allowed_vs_rb", "float1"),
    ("YTD Avg Targets/G", "szn_avg_targets", "float1"),
    ("YTD Avg Receptions/G", "szn_avg_receptions", "float1"),
    ("YTD Avg Yds/G", "szn_avg_rec_yds", "float1"),
    ("L5 Avg Targets/G", "l5_avg_targets", "float1"),
    ("L5 Avg Receptions/G", "l5_avg_receptions", "float1"),
    ("L5 Avg Yds/G", "l5_avg_rec_yds", "float1"),
    ("L3 Avg Targets/G", "l3_avg_targets", "float1"),
    ("L3 Avg Receptions/G", "l3_avg_receptions", "float1"),
    ("L3 Avg Yds/G", "l3_avg_rec_yds", "float1"),
    ("L1 Targets", "l1_targets", "int"),
    ("L1 Receptions", "l1_receptions", "int"),
    ("L1 Rec Yds", "l1_receiving_yards", "int"),
]


@st.cache_data(ttl=300)
def _load(season: int, week: int, game_ids: tuple):
    return queries_ext.get_weekly_position_board(season, week, "RB", game_ids=list(game_ids))


def render(season: int, week: int, game_ids: list):
    df = _load(season, week, tuple(sorted(game_ids)))
    utils.render_position_tab(df, COLUMNS, rank_col="opp_rank_rec_yards_allowed_vs_rb", key_prefix="rb_rec")
