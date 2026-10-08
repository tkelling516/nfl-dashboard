"""Shared display helpers: NULL-safe formatting, matchup color coding, and
the generic position-board table renderer used by all four tabs.

NULL is never rendered as 0 anywhere in this module -- per the analytics
layer's contract, NULL means "no qualifying data," so it always renders as
the em dash below.

Tables render via st.dataframe (not a hand-built HTML table) specifically
so column headers get Streamlit's native click-to-sort for free. The
tradeoff: the Matchup column is a plain colored cell (via a pandas Styler)
rather than a rounded badge -- st.dataframe's grid can't render arbitrary
HTML inside a cell, only CSS on the cell itself.
"""

import pandas as pd
import streamlit as st

NULL_DISPLAY = "—"  # em dash
NULL_COLOR = "#9e9e9e"

_MATCHUP_COLORS = {
    "elite": "#d32f2f",       # rank in top 25% (best defense) -- bad matchup
    "above_avg": "#f57c00",   # 25-50%
    "below_avg": "#81c784",   # 50-75%
    "worst": "#2e7d32",       # bottom 25% (worst defense) -- great matchup
}


def get_matchup_color(rank, pool_size) -> str:
    """Hex color for a defensive rank, by percentile within pool_size.
    Grey for NULL/unknown. 1 = best defense, pool_size = worst defense.
    """
    if rank is None or pool_size is None or pd.isna(rank) or pd.isna(pool_size) or pool_size <= 0:
        return NULL_COLOR
    pct = rank / pool_size
    if pct <= 0.25:
        return _MATCHUP_COLORS["elite"]
    if pct <= 0.5:
        return _MATCHUP_COLORS["above_avg"]
    if pct <= 0.75:
        return _MATCHUP_COLORS["below_avg"]
    return _MATCHUP_COLORS["worst"]


# Implied team total bands: (floor, color), checked top-down; below the last
# floor is red. A total exactly on a boundary lands in the higher band.
_IMPL_TOTAL_BANDS = ((27, "#2e7d32"), (24, "#81c784"), (21, "#f57c00"))
_IMPL_TOTAL_LOW = "#d32f2f"


def get_impl_total_color(total) -> str:
    """Hex color for a team's implied total (high-scoring environment =
    green, low = red). Grey for NULL -- no line posted for that game."""
    if total is None or pd.isna(total):
        return NULL_COLOR
    for floor, color in _IMPL_TOTAL_BANDS:
        if total >= floor:
            return color
    return _IMPL_TOTAL_LOW


# nflverse's wording -> the short code the player tables show. "IR" never
# appears in the weekly report today but maps to OUT if it ever does.
_STATUS_CODES = {"out": "OUT", "ir": "OUT", "injured reserve": "OUT", "doubtful": "D", "questionable": "Q"}
STATUS_LABELS = {"OUT": "Out", "D": "Doubtful", "Q": "Questionable"}
STATUS_SEVERITY = {"OUT": 0, "D": 1, "Q": 2}
# (background, text) per status code.
_STATUS_STYLES = {"OUT": ("#d32f2f", "white"), "D": ("#f57c00", "white"), "Q": ("#fdd835", "#1a1a1a")}

# Muted look for a player who's OUT: the grid can't do real opacity (it only
# honors color / background-color from a Styler), so grey text, and a flat
# dark fill in place of any colored cell.
_OUT_TEXT = "color: #7d7d7d"
_OUT_FILL = "background-color: #3a3a3a; color: #8a8a8a"


def normalize_injury_status(raw) -> str:
    """'Out'/'Doubtful'/'Questionable' (any case) -> 'OUT'/'D'/'Q';
    anything else, including NULL, -> '' (no designation, nothing shown)."""
    if raw is None or pd.isna(raw):
        return ""
    return _STATUS_CODES.get(str(raw).strip().lower(), "")


def status_cell_style(code: str) -> str:
    bg_fg = _STATUS_STYLES.get(code)
    if bg_fg is None:
        return ""
    return f"background-color: {bg_fg[0]}; color: {bg_fg[1]}; font-weight: bold"


def fmt_num(value, decimals: int = 0) -> str:
    """Render a numeric value, or NULL_DISPLAY if NaN/None. Never 0 for a
    missing value -- callers must pass an actual 0 to see "0"."""
    if value is None or pd.isna(value):
        return NULL_DISPLAY
    return f"{value:,.{decimals}f}"


def fmt_pct(value, decimals: int = 1) -> str:
    """Render a 0-1 probability/share as a percentage, or NULL_DISPLAY."""
    if value is None or pd.isna(value):
        return NULL_DISPLAY
    return f"{value * 100:.{decimals}f}%"


def default_sort(df: pd.DataFrame, rank_col: str) -> pd.DataFrame:
    """matchup_advantage=True first, then by rank_col descending (worst
    defenses / best matchups at top). NaN ranks sort last. This is just the
    *initial* row order -- clicking any column header re-sorts from there."""
    out = df.copy()
    out["_adv"] = out["matchup_advantage"].fillna(False)
    out["_rank_sort"] = out[rank_col].fillna(-1)
    out = out.sort_values(["_adv", "_rank_sort"], ascending=[False, False])
    return out.drop(columns=["_adv", "_rank_sort"]).reset_index(drop=True)


FORMATTERS = {
    "int": lambda v: fmt_num(v, 0),
    "float1": lambda v: fmt_num(v, 1),
    "pct1": fmt_pct,
    "matchup": lambda v: fmt_num(v, 0),
    "impl_total": lambda v: fmt_num(v, 1),
    "ratio1": lambda v: NULL_DISPLAY if v is None or pd.isna(v) else f"{v:.1f}x",
}


def build_styler(df: pd.DataFrame, columns: list[tuple[str, str, str]]):
    """(display_df, styler) for a (header, source_column, kind) column
    spec, with every numeric kind formatted NULL-safely via FORMATTERS.
    Doesn't apply matchup coloring -- chain `.map(...)` onto the returned
    styler for that (see render_position_tab)."""
    display = pd.DataFrame({header: df[col] for header, col, _kind in columns})
    formatters = {header: FORMATTERS[kind] for header, _col, kind in columns if kind in FORMATTERS}
    return display, display.style.format(formatters)


def render_table(headers: list[str], styler, pinned_count: int = 2) -> None:
    """st.dataframe with the first `pinned_count` columns pinned and
    native click-to-sort (the whole reason this uses st.dataframe over a
    hand-built HTML table)."""
    st.dataframe(
        styler,
        column_order=headers,
        column_config={h: st.column_config.Column(pinned=True) for h in headers[:pinned_count]},
        hide_index=True,
    )


def _make_row_styles(columns: list[tuple[str, str, str]], pool_size):
    """One function that styles every cell of a row, so colored cells
    (Matchup, Impl Total, Status) and the muted look for OUT players are
    decided together -- separate .map()/.apply() passes layering on the
    same cell would leave CSS precedence up to the grid."""
    headers = [h for h, _, _ in columns]
    kinds = {h: kind for h, _, kind in columns}
    status_headers = [h for h in headers if kinds[h] == "status"]

    def row_styles(row):
        is_out = any(row[h] == "OUT" for h in status_headers)
        styles = []
        for h in headers:
            kind = kinds[h]
            if kind == "status":
                styles.append(status_cell_style(row[h]))
            elif kind in ("matchup", "impl_total"):
                if is_out:
                    styles.append(_OUT_FILL)
                else:
                    color = get_matchup_color(row[h], pool_size) if kind == "matchup" else get_impl_total_color(row[h])
                    styles.append(f"background-color: {color}; color: white")
            else:
                styles.append(_OUT_TEXT if is_out else "")
        return styles

    return row_styles


def render_position_tab(
    df: pd.DataFrame,
    columns: list[tuple[str, str, str]],
    rank_col: str,
    key_prefix: str,
    pool_col: str = "opp_pool_size",
    pinned_count: int = 2,
) -> None:
    """Render a full position board tab: min-games filter, then a
    click-to-sort, color-coded table. `columns` is a list of
    (header, source_column, kind) where kind is one of "str", "int",
    "float1", "pct1", "matchup", "impl_total", "status". `rank_col` is the
    opp_rank_* column the Matchup column's color and the initial sort order
    are based on. A "status" column holds OUT/D/Q (or "") and, when it says
    OUT, mutes the rest of that player's row.
    """
    if df.empty:
        st.info("No qualifying players found for this selection.")
        return

    min_games = st.number_input(
        "Min games played", min_value=1, max_value=10, value=1, step=1, key=f"{key_prefix}_min_games"
    )
    filtered = df[df["szn_games_played"] >= min_games]
    if filtered.empty:
        st.info("No players meet that games-played threshold.")
        return

    sorted_df = default_sort(filtered, rank_col)
    pool_size = sorted_df[pool_col].max()  # constant across one board; NaN only if no team has data yet

    _display, styler = build_styler(sorted_df, columns)
    styler = styler.apply(_make_row_styles(columns, pool_size), axis=1)

    if pd.notna(pool_size):
        st.caption(f"{len(sorted_df)} players — defensive ranks out of {int(pool_size)} teams with data through this week")
    else:
        st.caption(f"{len(sorted_df)} players")

    render_table([h for h, _, _ in columns], styler, pinned_count=pinned_count)
