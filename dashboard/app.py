"""NFL Props Dashboard -- Streamlit entry point.

Read-only over nfl_betting.duckdb via queries.py / queries_ext.py. Never
writes to the database.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st

import queries_ext
from components import (
    game_selector,
    tab_td_scorer,
    tab_rb_rushing,
    tab_rb_receiving,
    tab_wr_receiving,
    tab_te_receiving,
    tab_picks_optimizer,
)

st.set_page_config(page_title="NFL Props Dashboard", page_icon="🏈", layout="wide")


@st.cache_data(ttl=1800)
def _load_current_week():
    return queries_ext.get_current_week()


def main():
    current = _load_current_week()
    season, week, is_fallback = current["season"], current["week"], current["is_fallback"]

    if season is None:
        st.error("No game data found in the database yet. Run the ingestion pipeline first.")
        return

    if is_fallback:
        st.warning(
            f"Season hasn't started yet — showing most recent week available "
            f"(Season {season}, Week {week})."
        )

    st.title("NFL Props Dashboard")
    st.header(f"Week {week} — {season} Season")

    game_ids = game_selector.render(season, week)

    tabs = st.tabs([
        "🎯 TD Scorer", "🏃 RB Rushing", "🏃 RB Receiving", "🏈 WR Receiving", "🏈 TE Receiving", "🧮 Picks Optimizer",
    ])

    with tabs[0]:
        tab_td_scorer.render(season, week, game_ids)
    with tabs[1]:
        tab_rb_rushing.render(season, week, game_ids)
    with tabs[2]:
        tab_rb_receiving.render(season, week, game_ids)
    with tabs[3]:
        tab_wr_receiving.render(season, week, game_ids)
    with tabs[4]:
        tab_te_receiving.render(season, week, game_ids)
    with tabs[5]:
        tab_picks_optimizer.render(season, week, game_ids)


if __name__ == "__main__":
    main()
