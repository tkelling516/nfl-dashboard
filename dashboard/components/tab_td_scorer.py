"""Master anytime-TD leaderboard: top N per position (RB/WR/TE) by
td_probability, combined into one list with a position filter. Deliberately
lean columns -- rushing/receiving stat lines don't unify cleanly across
positions (RB carries both), so this stays a pure "who's most likely to
score" ranking rather than a full stat board. For the full stat line, see
the position-specific tabs.
"""

import pandas as pd
import streamlit as st

import queries_ext
from components import utils

POSITIONS = ["RB", "WR", "TE"]

COLUMNS = [
    ("Player", "player_name", "str"),
    ("Position", "position", "str"),
    ("Team", "team", "str"),
    ("Opponent", "opponent", "str"),
    ("TD Prob", "td_probability", "pct1"),
]


@st.cache_data(ttl=300)
def _load(season: int, week: int, position: str, game_ids: tuple):
    return queries_ext.get_weekly_position_board(season, week, position, game_ids=list(game_ids))


def render(season: int, week: int, game_ids: list):
    game_ids_t = tuple(sorted(game_ids))

    top_n = st.number_input("Show top N per position", min_value=5, max_value=50, value=25, step=5, key="td_scorer_top_n")

    frames = []
    for pos in POSITIONS:
        df = _load(season, week, pos, game_ids_t)
        if df.empty:
            continue
        df = df[df["td_probability"].notna()].sort_values("td_probability", ascending=False).head(top_n)
        frames.append(df)

    if not frames:
        st.info("No qualifying players found for this selection.")
        return

    combined = pd.concat(frames, ignore_index=True)

    selected_positions = st.multiselect("Position", POSITIONS, default=POSITIONS, key="td_scorer_position")
    filtered = combined[combined["position"].isin(selected_positions)]
    if filtered.empty:
        st.info("No players match the selected position(s).")
        return

    filtered = filtered.sort_values("td_probability", ascending=False).reset_index(drop=True)

    _display, styler = utils.build_styler(filtered, COLUMNS)

    st.caption(f"Top {top_n} per position by TD probability — {len(filtered)} shown")
    utils.render_table([h for h, _, _ in COLUMNS], styler)
