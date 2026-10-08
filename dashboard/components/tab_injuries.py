"""Injuries tab: this week's injury designations by team, plus an on-demand
AI analysis of what they mean for prop bets.

The AI analysis is grounded in this dashboard's own data (game lines, the
injury report, and each team's key RB/WR/TE with usage, matchup rank and
modeled TD probability) rather than only the injury list, so it can also
weigh good players in good matchups instead of guessing at this season's
performance from memory.
"""

import pandas as pd
import streamlit as st

import queries_ext
from components import board_context, utils

AI_MODEL = "claude-sonnet-5-5"
AI_MAX_TOKENS = 6000

SYSTEM_PROMPT = (
    "You are an NFL prop betting analyst. Analyze the injury report for this week and identify the "
    "most actionable implications for player prop bets. Focus on:\n"
    "1. Players who may see significantly increased targets, carries, or routes due to teammate injuries\n"
    "2. Games where injuries change the offensive game plan\n"
    "3. Specific prop markets to target (receiving yards, rushing attempts, etc.) based on the injury "
    "landscape\n"
    "Be specific about player names and prop types. Format as a structured analysis with a brief "
    "game-by-game breakdown followed by a ranked list of top beneficiary picks.\n\n"
    "Do not only analyze picks and props based on the injury landscape. Be an NFL props betting analyst "
    "that also considers good players in good matchups. The NFL betting landscape is complex and is "
    "impacted by many factors - player ability and opportunities, matchups, game environment, recent "
    "trends, injuries, etc."
)

_LABEL_TO_CODE = {label: code for code, label in utils.STATUS_LABELS.items()}

# Per-team caps on the players handed to the model, with the usage floor a
# player needs to count as "key" -- keeps the prompt to a sane size.
_KEY_PLAYER_RULES = {
    "RB": {"sort": "szn_avg_carries", "cap": 3, "keep": lambda d: (d["szn_avg_carries"] >= 6) | (d["szn_avg_targets"] >= 3)},
    "WR": {"sort": "szn_avg_targets", "cap": 4, "keep": lambda d: d["szn_avg_targets"] >= 4},
    "TE": {"sort": "szn_avg_targets", "cap": 2, "keep": lambda d: d["szn_avg_targets"] >= 3},
}


@st.cache_data(ttl=300)
def _load_injuries(season: int, week: int):
    return queries_ext.get_injury_report(season, week)


@st.cache_data(ttl=300)
def _load_games(season: int, week: int):
    return queries_ext.get_games_for_week(season, week)


@st.cache_data(ttl=300)
def _load_lines(season: int, week: int):
    return queries_ext.get_game_lines(season, week)


@st.cache_data(ttl=300)
def _load_board(season: int, week: int, position: str):
    return queries_ext.get_weekly_position_board(season, week, position)


def _designated(injuries: pd.DataFrame) -> pd.DataFrame:
    """Injury rows that carry a real designation, with a `code` column
    (OUT / D / Q) and a severity sort key."""
    if injuries.empty:
        return injuries.assign(code=pd.Series(dtype=str), _sev=pd.Series(dtype=int))
    out = injuries.assign(code=injuries["status"].map(utils.normalize_injury_status))
    out = out[out["code"] != ""]
    return out.assign(_sev=out["code"].map(utils.STATUS_SEVERITY))


# ---------------------------------------------------------------------------
# Section 1: the report
# ---------------------------------------------------------------------------

def _render_report(season: int, week: int, game_ids: list) -> None:
    games = _load_games(season, week)
    selected = games[games["game_id"].isin(game_ids)] if not games.empty else games
    teams = set(selected["home_team"]) | set(selected["away_team"]) if not selected.empty else set()

    designated = _designated(_load_injuries(season, week))
    designated = designated[designated["team"].isin(teams)]

    if designated.empty:
        st.info(
            f"No injury designations are in for Week {week} yet. Teams publish them through the week "
            "(mostly Friday) — re-run the update pipeline then."
        )
        return

    designated = designated.sort_values(["_sev", "team", "player_name"])
    table = pd.DataFrame({
        "Team": designated["team"],
        "Player": designated["player_name"],
        "Position": designated["position"],
        "Status": designated["code"].map(utils.STATUS_LABELS),
        "Injury": designated["injury"].fillna(utils.NULL_DISPLAY),
        "Report Date": designated["report_date"].map(
            lambda d: d.strftime("%Y-%m-%d") if pd.notna(d) else utils.NULL_DISPLAY
        ),
    }).reset_index(drop=True)
    styler = table.style.map(
        lambda label: utils.status_cell_style(_LABEL_TO_CODE.get(label, "")), subset=["Status"]
    )

    st.caption(
        f"{len(table)} designations across {designated['team'].nunique()} teams — sorted by severity, then team "
        "(click the Team header to group by team). Report Date is the day this data was pulled, so re-run the "
        "update pipeline for fresher designations."
    )
    utils.render_table(list(table.columns), styler, pinned_count=2)


# ---------------------------------------------------------------------------
# Section 2: AI analysis
# ---------------------------------------------------------------------------

def _n(value, fmt: str) -> str:
    return "n/a" if value is None or pd.isna(value) else format(value, fmt)


def _spread_text(game) -> str:
    spread = game["spread"]
    if pd.isna(spread):
        return "no line posted"
    if spread == 0:
        favorite = "pick'em"
    elif spread < 0:
        favorite = f"{game['home_team']} {spread:.1f}"
    else:
        favorite = f"{game['away_team']} -{spread:.1f}"
    return (
        f"{favorite}, total {game['total']:.1f} "
        f"(implied {game['home_team']} {game['home_implied']:.1f}, {game['away_team']} {game['away_implied']:.1f})"
    )


def _player_line(r) -> str:
    status = f" [{utils.STATUS_LABELS[r.injury_status]}]" if r.injury_status else ""
    pool = _n(r.opp_pool_size, ".0f")
    td = f"TD {r.td_probability:.0%}" if pd.notna(r.td_probability) else "TD n/a"
    if r.position == "RB":
        stats = (
            f"YTD {_n(r.szn_avg_carries, '.1f')} car, {_n(r.szn_avg_rush_yds, '.0f')} rush yds, "
            f"{_n(r.szn_avg_targets, '.1f')} tgt per game; L3 {_n(r.l3_avg_carries, '.1f')} car, "
            f"{_n(r.l3_avg_rush_yds, '.0f')} rush yds"
        )
        ranks = (
            f"opp rush-D rank {_n(r.opp_rank_rush_yards_allowed_vs_rb, '.0f')}/{pool}, "
            f"rec-D rank {_n(r.opp_rank_rec_yards_allowed_vs_rb, '.0f')}/{pool}"
        )
    else:
        rank_col = f"opp_rank_rec_yards_allowed_vs_{r.position.lower()}"
        stats = (
            f"YTD {_n(r.szn_avg_targets, '.1f')} tgt, {_n(r.szn_avg_receptions, '.1f')} rec, "
            f"{_n(r.szn_avg_rec_yds, '.0f')} yds per game; L3 {_n(r.l3_avg_targets, '.1f')} tgt, "
            f"{_n(r.l3_avg_rec_yds, '.0f')} yds"
        )
        ranks = f"opp {r.position}-D rank {_n(getattr(r, rank_col), '.0f')}/{pool}"
    return f"  - {r.player_name} ({r.position}){status}: {stats}; {td}; {ranks}"


def _key_players_by_team(season: int, week: int) -> dict[str, list[str]]:
    """team -> formatted lines for its key RB/WR/TE, strongest usage first."""
    lines: dict[str, list[str]] = {}
    for position, rule in _KEY_PLAYER_RULES.items():
        board = _load_board(season, week, position)
        if board.empty:
            continue
        board = board_context.attach_context(board, season, week)
        board = board[rule["keep"](board)].sort_values(rule["sort"], ascending=False)
        for team, group in board.groupby("team", sort=False):
            for row in group.head(rule["cap"]).itertuples():
                lines.setdefault(team, []).append(_player_line(row))
    return lines


def build_analysis_context(season: int, week: int) -> tuple[str, pd.DataFrame]:
    """The full-week context handed to the model, one block per game:
    betting line, each team's injuries, each team's key RB/WR/TE. Also
    returns the designated-injury rows (for the report-date note)."""
    games = _load_games(season, week)
    lines = _load_lines(season, week)
    designated = _designated(_load_injuries(season, week)).sort_values(["_sev", "player_name"])
    key_players = _key_players_by_team(season, week)

    if not games.empty and not lines.empty:
        games = games.merge(lines[["game_id", "spread", "total", "home_implied", "away_implied"]], on="game_id", how="left")

    blocks = []
    for game in games.to_dict("records"):
        away, home = game["away_team"], game["home_team"]
        spread = _spread_text(game) if "spread" in game else "no line posted"
        header = f"=== {away} @ {home} ({game['day_name']} {pd.Timestamp(game['game_date']):%Y-%m-%d}) | {spread} ==="
        block = [header]
        for team in (away, home):
            injured = designated[designated["team"] == team]
            if injured.empty:
                block.append(f"{team} injuries: none designated")
            else:
                entries = [
                    f"{r.player_name} ({r.position}) {utils.STATUS_LABELS[r.code]}" + (f" - {r.injury}" if pd.notna(r.injury) else "")
                    for r in injured.itertuples()
                ]
                block.append(f"{team} injuries: " + "; ".join(entries))
            block.append(f"{team} key players:")
            block.extend(key_players.get(team, ["  - (none above the usage cutoff)"]))
        blocks.append("\n".join(block))

    report_date = designated["report_date"].max() if not designated.empty else None
    as_of = f"as of {report_date:%Y-%m-%d}" if pd.notna(report_date) else "none published yet"
    intro = (
        f"Week {week} of the {season} NFL season. Below, game by game: the betting line, each team's injury "
        f"designations ({as_of}; they can change before kickoff), and each team's key RB/WR/TE from my "
        "dashboard — season-to-date (YTD) and last-3-game (L3) per-game averages, TD = my model's anytime-"
        "touchdown probability. Opponent defense ranks run from 1 = toughest matchup (fewest yards allowed to "
        "that position) to the pool size = softest. Use only this data for this season's performance; do not "
        "invent statistics.\n\n"
    )
    return intro + "\n\n".join(blocks), designated


def _api_key() -> str | None:
    try:
        return st.secrets["ANTHROPIC_API_KEY"]
    except Exception:  # noqa: BLE001 -- no secrets file at all, or the key just isn't in it
        return None


def _make_client(api_key: str):
    import anthropic  # deferred so the rest of the tab works even if the package is missing

    return anthropic.Anthropic(api_key=api_key)


def _stream_analysis(client, user_content: str):
    with client.messages.stream(
        model=AI_MODEL,
        max_tokens=AI_MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    ) as stream:
        for chunk in stream.text_stream:
            yield chunk


def _generate(api_key: str, season: int, week: int, cache_key: str) -> None:
    context, _designated_rows = build_analysis_context(season, week)
    try:
        text = st.write_stream(_stream_analysis(_make_client(api_key), context))
    except Exception as exc:  # noqa: BLE001 -- surface any API/network failure rather than crash the tab
        st.error(f"AI analysis failed: {exc}")
        return
    st.session_state[cache_key] = text


def _render_ai_analysis(season: int, week: int) -> None:
    st.caption(f"Covers all of Week {week}'s games, regardless of the sidebar game filter.")

    api_key = _api_key()
    if not api_key:
        st.info("Add ANTHROPIC_API_KEY to Streamlit secrets to enable AI analysis.")
        return

    cache_key = f"injury_ai_{season}_{week}"
    cached = st.session_state.get(cache_key)

    if cached:
        if st.button("Regenerate", key=f"injury_ai_regen_{season}_{week}"):
            _generate(api_key, season, week, cache_key)
        else:
            st.markdown(cached)
    elif st.button("🤖 Generate AI Analysis", key=f"injury_ai_gen_{season}_{week}"):
        _generate(api_key, season, week, cache_key)


def render(season: int, week: int, game_ids: list):
    st.subheader(f"Week {week} injury report")
    _render_report(season, week, game_ids)

    st.subheader("AI injury analysis")
    _render_ai_analysis(season, week)
