"""Per-player context the four position tabs share: the player's team's
implied total (game environment) and injury status.

Lives here, rather than inside get_weekly_position_board, so that query
stays exactly as it was and the tabs don't each repeat the same two merges.
"""

import numpy as np
import pandas as pd
import streamlit as st

import queries_ext
from components import utils

# Session-state key of the sidebar slider; apply_context reads it so the
# tabs don't each need the value threaded through their render() signature.
MIN_IMPL_TOTAL_KEY = "min_implied_total"


@st.cache_data(ttl=300)
def _load_game_lines(season: int, week: int):
    return queries_ext.get_game_lines(season, week)


@st.cache_data(ttl=300)
def _load_injuries(season: int, week: int):
    return queries_ext.get_injury_report(season, week)


def has_game_lines(season: int, week: int) -> bool:
    lines = _load_game_lines(season, week)
    return (not lines.empty) and bool(lines["home_implied"].notna().any())


def render_sidebar_filter(season: int, week: int) -> None:
    """Min implied team total slider (0 = no filter). Disabled, with a
    note, when no game this week has a posted line yet."""
    enabled = has_game_lines(season, week)
    st.sidebar.slider(
        "Min implied team total",
        min_value=0.0,
        max_value=35.0,
        value=0.0,
        step=0.5,
        key=MIN_IMPL_TOTAL_KEY,
        disabled=not enabled,
        help="Hide players whose team's implied total is below this. 0 turns the filter off. "
        "Players whose game has no posted line are hidden once this is above 0.",
    )
    if enabled:
        st.sidebar.caption("Applies to the RB / WR / TE tabs.")
    else:
        st.sidebar.caption("Odds not yet loaded for this week.")


def attach_context(df: pd.DataFrame, season: int, week: int) -> pd.DataFrame:
    """Add `team_implied_total` and `injury_status` (OUT / D / Q / "") to a
    position board, without filtering anything. Never blocks the table: no
    lines -> NaN implied totals (shown as an em dash), no injuries -> empty
    status.
    """
    out = df.copy()

    lines = _load_game_lines(season, week)
    if lines.empty:
        out["team_implied_total"] = np.nan
    else:
        merged = out[["game_id", "team"]].merge(
            lines[["game_id", "home_team", "home_implied", "away_implied"]], on="game_id", how="left"
        )
        out["team_implied_total"] = np.where(
            merged["team"] == merged["home_team"], merged["home_implied"], merged["away_implied"]
        )

    injuries = _load_injuries(season, week)
    if injuries.empty:
        out["injury_status"] = ""
    else:
        coded = injuries.assign(code=injuries["status"].map(utils.normalize_injury_status))
        coded = coded[coded["code"] != ""]
        # One status per player; if a player were ever listed twice, keep the more severe.
        coded = coded.assign(_sev=coded["code"].map(utils.STATUS_SEVERITY)).sort_values("_sev")
        coded = coded.drop_duplicates("player_id")[["player_id", "code"]].rename(columns={"code": "injury_status"})
        out = out.merge(coded, on="player_id", how="left")
        out["injury_status"] = out["injury_status"].fillna("")

    return out


def apply_context(df: pd.DataFrame, season: int, week: int) -> pd.DataFrame:
    """attach_context, then the sidebar's min-implied-total filter (off at
    0, and ignored while the slider is disabled for lack of any lines)."""
    out = attach_context(df, season, week)

    min_total = st.session_state.get(MIN_IMPL_TOTAL_KEY, 0.0)
    if min_total > 0 and has_game_lines(season, week):
        out = out[out["team_implied_total"] >= min_total]

    return out.reset_index(drop=True)
