"""Shared display helpers: NULL-safe formatting, matchup color coding, and
the generic position-board table renderer used by all four tabs.

NULL is never rendered as 0 anywhere in this module -- per the analytics
layer's contract, NULL means "no qualifying data," so it always renders as
the em dash below.
"""

import html

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


def fmt_num(value, decimals: int = 0) -> str:
    """Render a numeric value, or NULL_DISPLAY if NaN/None. Never 0 for a
    missing value -- callers must pass an actual 0 to see "0"."""
    if value is None or pd.isna(value):
        return NULL_DISPLAY
    return f"{value:,.{decimals}f}"


def fmt_rank(rank, pool_size) -> str:
    """"25 of 31" style rank display. NULL_DISPLAY if either is missing --
    never assumes a 32-team pool."""
    if rank is None or pool_size is None or pd.isna(rank) or pd.isna(pool_size):
        return NULL_DISPLAY
    return f"{int(rank)} of {int(pool_size)}"


def default_sort(df: pd.DataFrame, rank_col: str) -> pd.DataFrame:
    """matchup_advantage=True first, then by rank_col descending (worst
    defenses / best matchups at top). NaN ranks sort last."""
    out = df.copy()
    out["_adv"] = out["matchup_advantage"].fillna(False)
    out["_rank_sort"] = out[rank_col].fillna(-1)
    out = out.sort_values(["_adv", "_rank_sort"], ascending=[False, False])
    return out.drop(columns=["_adv", "_rank_sort"]).reset_index(drop=True)


_TABLE_CSS = """
<style>
.nfl-board-wrap { overflow-x: auto; }
.nfl-board { width: 100%; border-collapse: collapse; font-size: 0.88rem; }
.nfl-board th {
    text-align: right; padding: 8px 10px; background: #262730;
    color: #fafafa; position: sticky; top: 0; white-space: nowrap;
    border-bottom: 2px solid #3a3b47;
}
.nfl-board th:first-child, .nfl-board th:nth-child(2), .nfl-board th:nth-child(3) { text-align: left; }
.nfl-board td { padding: 6px 10px; border-bottom: 1px solid #262730; color: #e6e6e6; white-space: nowrap; }
.nfl-board tbody tr:nth-child(odd) { background: #1a1c24; }
.nfl-board tbody tr:hover { background: #2a2c38; }
.nfl-badge {
    display: inline-block; padding: 2px 9px; border-radius: 10px;
    color: #fff; font-weight: 600; font-size: 0.85rem;
}
</style>
"""


def _render_table_html(df: pd.DataFrame, columns: list[tuple[str, str, str]], rank_col: str, pool_col: str) -> str:
    header_html = "".join(f"<th>{html.escape(h)}</th>" for h, _, _ in columns)
    row_chunks = []
    for _, row in df.iterrows():
        cells = []
        for header, col, kind in columns:
            if kind == "matchup":
                rank, pool = row[rank_col], row[pool_col]
                label = fmt_rank(rank, pool)
                if label == NULL_DISPLAY:
                    cells.append(f'<td style="text-align:center;color:{NULL_COLOR};">{NULL_DISPLAY}</td>')
                else:
                    color = get_matchup_color(rank, pool)
                    cells.append(
                        f'<td style="text-align:center;"><span class="nfl-badge" '
                        f'style="background:{color};">{label}</span></td>'
                    )
            elif kind == "str":
                val = row[col]
                text = NULL_DISPLAY if val is None or pd.isna(val) else html.escape(str(val))
                cells.append(f"<td>{text}</td>")
            else:
                decimals = 1 if kind == "float1" else 0
                cells.append(f'<td style="text-align:right;">{fmt_num(row[col], decimals)}</td>')
        row_chunks.append("<tr>" + "".join(cells) + "</tr>")

    return (
        _TABLE_CSS
        + '<div class="nfl-board-wrap"><table class="nfl-board">'
        + f"<thead><tr>{header_html}</tr></thead><tbody>{''.join(row_chunks)}</tbody>"
        + "</table></div>"
    )


def render_position_tab(
    df: pd.DataFrame,
    columns: list[tuple[str, str, str]],
    rank_col: str,
    key_prefix: str,
    pool_col: str = "opp_pool_size",
) -> None:
    """Render a full position board tab: min-games filter, sort control,
    color-coded matchup table. `columns` is a list of
    (header, source_column, kind) where kind is one of "str", "int",
    "float1", "matchup". `rank_col` is the opp_rank_* column this tab's
    Matchup badge/default-sort is based on.
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

    sortable_headers = ["Best Matchup (default)"] + [h for h, _, kind in columns if kind != "str"]
    sort_choice = st.selectbox("Sort by", sortable_headers, key=f"{key_prefix}_sort")

    if sort_choice == "Best Matchup (default)":
        sorted_df = default_sort(filtered, rank_col)
    else:
        sort_col = next(col for h, col, _ in columns if h == sort_choice)
        sorted_df = filtered.sort_values(sort_col, ascending=False, na_position="last").reset_index(drop=True)

    st.caption(f"{len(sorted_df)} players")
    st.markdown(_render_table_html(sorted_df, columns, rank_col, pool_col), unsafe_allow_html=True)
