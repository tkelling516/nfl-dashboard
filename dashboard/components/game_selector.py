"""Sidebar: title, day-of-week filter, game multiselect, data-freshness note.

Returns the list of selected game_ids (or None if every game for the
week/day is selected) so tab components can filter the board to them.
"""

import pandas as pd
import streamlit as st

import queries_ext

_DAY_OPTIONS = ["All Games", "Thursday", "Sunday", "Monday"]


@st.cache_data(ttl=300)
def _load_games(season: int, week: int):
    return queries_ext.get_games_for_week(season, week)


@st.cache_data(ttl=1800)
def _load_as_of_week(season: int):
    return queries_ext.get_data_as_of_week(season)


def render(season: int, week: int):
    st.sidebar.title("NFL Props Dashboard")

    games = _load_games(season, week)

    day_choice = st.sidebar.radio("Day", _DAY_OPTIONS, index=0)
    if day_choice != "All Games":
        games = games[games["day_name"] == day_choice]

    as_of_week = _load_as_of_week(season)
    if as_of_week is not None and as_of_week != week:
        st.sidebar.caption(f"Data through Week {as_of_week} — Week {week} hasn't been played yet.")
    elif as_of_week is not None:
        st.sidebar.caption(f"Data through Week {as_of_week}.")

    if games.empty:
        st.sidebar.warning("No games match this filter.")
        return []

    labels = [f"{row.matchup} ({pd.Timestamp(row.game_date):%Y-%m-%d})" for row in games.itertuples()]
    label_to_id = dict(zip(labels, games["game_id"]))

    selected_labels = st.sidebar.multiselect(
        "Games", options=labels, default=labels, key=f"games_{season}_{week}_{day_choice}"
    )

    return [label_to_id[l] for l in selected_labels]
