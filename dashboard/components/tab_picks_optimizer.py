"""Picks Optimizer: a rules-based ALT-line prop scoring tool. Independent
of the four position-board tabs -- reads the same underlying data via
queries_ext, but doesn't touch or depend on any of their code.

Scores only recompute on the "Run Optimizer" button, not on every widget
change (weight sliders especially would otherwise thrash on every drag).
The eligible-player pool (for populating the lock/exclude lists) is cheap
enough to recompute on every rerun -- it's just cached-query results
filtered in pandas, no per-player scoring math.
"""

import pandas as pd
import streamlit as st

import queries_ext
from components import utils
from optimizer import scoring
from optimizer.factors import PROP_TYPES

_SCORE_BANDS = [(80, "#2e7d32"), (60, "#f57c00"), (0, "#d32f2f")]


def _score_color(score) -> str:
    if score is None or pd.isna(score):
        return utils.NULL_COLOR
    for threshold, color in _SCORE_BANDS:
        if score >= threshold:
            return color
    return _SCORE_BANDS[-1][1]


@st.cache_data(ttl=300)
def _load_board(season: int, week: int, position: str, game_ids: tuple):
    return queries_ext.get_weekly_position_board(season, week, position, game_ids=list(game_ids))


@st.cache_data(ttl=300)
def _load_optimizer_factors(season: int, week: int, position: str):
    return queries_ext.get_optimizer_player_factors(season, week, position)


@st.cache_data(ttl=300)
def _load_defense_box(season: int, week: int):
    return queries_ext.get_defense_box_factor(season, week)


def _eligible_pool(season, week, game_ids, position, min_games, min_snap_pct, min_matchup_rank, rank_col):
    board = _load_board(season, week, position, tuple(sorted(game_ids)))
    if board.empty:
        return board

    merged = board.merge(_load_optimizer_factors(season, week, position), on="player_id", how="left")
    merged = merged.merge(_load_defense_box(season, week), left_on="opponent", right_on="defteam", how="left")

    merged = merged[merged["szn_games_played"] >= min_games]
    merged = merged[merged["szn_snap_pct"].fillna(0) >= min_snap_pct]

    if min_matchup_rank > 1:
        # NULL matchup rank means "opponent has no data yet," not "bad
        # matchup" -- never let a minimum-rank filter hide it.
        merged = merged[merged[rank_col].isna() | (merged[rank_col] >= min_matchup_rank)]

    return merged.reset_index(drop=True)


def render(season: int, week: int, game_ids: list):
    left, right = st.columns([1, 3])

    with left:
        prop_type = st.selectbox("Prop type", list(PROP_TYPES.keys()), key="opt_prop_type")
        spec = PROP_TYPES[prop_type]
        position, rank_col = spec["position"], spec["matchup_rank_col"]

        threshold = st.number_input(
            spec["threshold_label"], min_value=0.0, value=spec["default_threshold"], step=1.0,
            help="Set to 0 to hide the vs-Line column.", key=f"opt_threshold_{prop_type}",
        )

        st.markdown("**Factor weights**")
        n_factors = len(spec["factors"])
        default_w = round(100 / n_factors)
        weights = {
            f["key"]: st.slider(f["label"], 0, 100, default_w, help=f["description"], key=f"opt_w_{prop_type}_{f['key']}")
            for f in spec["factors"]
        }

        st.markdown("**Minimum filters**")
        min_snap_pct = st.slider("Min snap %", 0, 100, 40, key="opt_min_snap") / 100.0
        min_games = st.number_input("Min games played", min_value=1, max_value=17, value=3, key="opt_min_games")

        preview = _load_board(season, week, position, tuple(sorted(game_ids)))
        pool_size_guess = 32
        if not preview.empty and preview["opp_pool_size"].notna().any():
            pool_size_guess = int(preview["opp_pool_size"].max())
        min_rank = st.slider(
            "Min matchup rank (only show defenses ranked X or worse)", 1, pool_size_guess, 1, key="opt_min_rank"
        )

        pool = _eligible_pool(season, week, game_ids, position, min_games, min_snap_pct, min_rank, rank_col)
        player_names = sorted(pool["player_name"].unique()) if not pool.empty else []

        st.markdown("**Lock / exclude**")
        locked = st.multiselect("Lock players", player_names, key="opt_locked")
        excluded = st.multiselect("Exclude players", player_names, key="opt_excluded")

        run = st.button("Run Optimizer", type="primary", key="opt_run", use_container_width=True)

    with right:
        if run:
            scored = pool[~pool["player_name"].isin(excluded)]
            scored = scoring.score_all_players(scored, prop_type, weights, threshold=threshold if threshold > 0 else None)
            st.session_state["optimizer_results"] = {
                "df": scored, "prop_type": prop_type, "threshold": threshold, "locked": list(locked),
            }

        results = st.session_state.get("optimizer_results")
        if results is None:
            st.info("Configure factors and filters on the left, then click **Run Optimizer**.")
            return

        if results["prop_type"] != prop_type:
            st.warning(
                f"Showing results for **{results['prop_type']}** — click **Run Optimizer** to refresh for **{prop_type}**."
            )

        _render_results(results, week)


def _render_results(results: dict, week: int) -> None:
    df = results["df"]
    prop_type = results["prop_type"]
    threshold = results["threshold"]
    locked = set(results["locked"])
    spec = PROP_TYPES[prop_type]

    st.subheader(f"{prop_type} — Week {week} Picks")
    st.caption("🟢 Strong (80–100)    🟡 Lean (60–79)    🔴 Weak (below 60)")

    if df.empty:
        st.info("No players match the current filters.")
        return

    df = df.copy()
    df["_locked"] = df["player_name"].isin(locked)
    df = df.sort_values(["_locked", "overall_score"], ascending=[False, False]).reset_index(drop=True)
    df["Player"] = df.apply(lambda r: ("📌 " if r["_locked"] else "") + r["player_name"], axis=1)

    columns = [
        ("Player", "Player", "str"),
        ("Team", "team", "str"),
        ("Opponent", "opponent", "str"),
        ("Score", "overall_score", "float1"),
        ("Matchup", spec["matchup_rank_col"], "matchup"),
        ("YTD Avg", spec["primary_szn_col"], "float1"),
        ("L5 Avg", spec["primary_l5_col"], "float1"),
        ("L3 Avg", spec["primary_l3_col"], "float1"),
    ]
    if threshold and threshold > 0:
        columns.append(("vs Line (L3)", "threshold_vs_l3", "ratio1"))
    columns.append(("Snap %", "szn_snap_pct", "pct1"))

    _display, styler = utils.build_styler(df, columns)
    pool_size = df["opp_pool_size"].max()
    styler = styler.map(lambda v: f"background-color: {utils.get_matchup_color(v, pool_size)}; color: white", subset=["Matchup"])
    styler = styler.map(lambda v: f"background-color: {_score_color(v)}; color: white", subset=["Score"])

    pool_note = f" — defensive ranks out of {int(pool_size)} teams" if pd.notna(pool_size) else ""
    st.caption(f"{len(df)} players scored{pool_note}")
    utils.render_table([h for h, _, _ in columns], styler, pinned_count=1)

    with st.expander("Factor breakdown"):
        _render_factor_breakdown(df, spec)

    st.markdown("**Player reasoning**")
    for _, row in df.iterrows():
        with st.expander(row["Player"]):
            for line in row["reasoning"]:
                st.markdown(f"- {line}")


def _render_factor_breakdown(df: pd.DataFrame, spec: dict) -> None:
    rows = []
    for _, r in df.iterrows():
        entry = {"Player": r["Player"]}
        for f in spec["factors"]:
            key = f["key"]
            val = r["factor_values"].get(key)
            score = r["factor_scores"].get(key)
            estimated = r["factor_estimated"].get(key)
            prefix = "~" if estimated else ""
            val_text = utils.NULL_DISPLAY if val is None or (isinstance(val, float) and pd.isna(val)) else f"{val:.2f}"
            entry[f"{f['label']} (raw)"] = prefix + val_text
            entry[f"{f['label']} (score)"] = prefix + (f"{score:.2f}" if score is not None else utils.NULL_DISPLAY)
        rows.append(entry)
    st.dataframe(pd.DataFrame(rows), hide_index=True)
    st.caption("~ prefix = estimated (no underlying data; scored neutral at 0.50)")
