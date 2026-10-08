import streamlit as st

import queries_ext
from components import board_context, utils

# Status sits right after the player; Impl Total goes with the other game
# context, between Opponent and Matchup.
BASE_COLUMNS = [
    ("Player", "player_name", "str"),
    ("Status", "injury_status", "status"),
    ("Team", "team", "str"),
    ("Opponent", "opponent", "str"),
    ("Impl Total", "team_implied_total", "impl_total"),
    ("TD Prob", "td_probability", "pct1"),
    ("Matchup", "opp_rank_rec_yards_allowed_vs_wr", "matchup"),
    ("Opp Avg Allowed", "opp_avg_rec_yards_allowed_vs_wr", "float1"),
]
YTD_COLUMNS = [
    ("YTD Avg Targets/G", "szn_avg_targets", "float1"),
    ("YTD Avg Receptions/G", "szn_avg_receptions", "float1"),
    ("YTD Avg Yds/G", "szn_avg_rec_yds", "float1"),
]
WINDOW_COLUMNS = {
    "Last 3 games": [
        ("L3 Avg Targets/G", "l3_avg_targets", "float1"),
        ("L3 Avg Receptions/G", "l3_avg_receptions", "float1"),
        ("L3 Avg Yds/G", "l3_avg_rec_yds", "float1"),
    ],
    "Last 5 games": [
        ("L5 Avg Targets/G", "l5_avg_targets", "float1"),
        ("L5 Avg Receptions/G", "l5_avg_receptions", "float1"),
        ("L5 Avg Yds/G", "l5_avg_rec_yds", "float1"),
    ],
}


@st.cache_data(ttl=300)
def _load(season: int, week: int, game_ids: tuple):
    return queries_ext.get_weekly_position_board(season, week, "WR", game_ids=list(game_ids))


def render(season: int, week: int, game_ids: list):
    df = _load(season, week, tuple(sorted(game_ids)))
    window = "Last 3 games"

    if not df.empty:
        df = board_context.apply_context(df, season, week)

        filter_mode = st.radio(
            "Filter to relevant receivers by", ["Season avg targets/game", "Season avg receptions/game"],
            horizontal=True, key="wr_rec_filter_mode",
        )
        if filter_mode == "Season avg targets/game":
            min_targets = st.number_input(
                "Min avg targets/game", min_value=0.0, max_value=20.0, value=3.0, step=0.5, key="wr_rec_min_targets",
            )
            df = df[df["szn_avg_targets"] >= min_targets]
        else:
            min_receptions = st.number_input(
                "Min avg receptions/game", min_value=0.0, max_value=15.0, value=2.0, step=0.5, key="wr_rec_min_receptions",
            )
            df = df[df["szn_avg_receptions"] >= min_receptions]

        window = st.radio("Show trailing window", list(WINDOW_COLUMNS), horizontal=True, key="wr_rec_window")

    columns = BASE_COLUMNS + YTD_COLUMNS + WINDOW_COLUMNS[window]
    utils.render_position_tab(
        df, columns, rank_col="opp_rank_rec_yards_allowed_vs_wr", key_prefix="wr_rec", pinned_count=3,
    )
