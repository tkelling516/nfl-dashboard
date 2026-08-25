"""Factor definitions per prop type for the Picks Optimizer.

Each factor is a dict: key, label, description (slider help text),
raw(row) -> raw value or None, score(raw, row) -> float in [0, 1] or
None, reason(raw, score) -> human-readable string. `row` is one row (a
pandas Series) from the board that tab_picks_optimizer.py builds by
merging get_weekly_position_board() with
queries_ext.get_optimizer_player_factors()/get_defense_box_factor() --
see PROP_TYPES for exactly which columns each prop type's factors read.

score() returning None means "we don't know" (a NULL upstream, e.g. no
NGS reading, no prior game) -- scoring.py is what turns that into the
neutral 0.5, never 0. A missing reading isn't evidence of a bad matchup.
"""

import math

# Ratio a "trend" factor (this window's average vs. the season average)
# is judged against -- 1.5x the season average maxes out the score.
_TREND_CAP = 1.5


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _get(row, col):
    """NULL-safe cell lookup: None for missing/NaN, the value otherwise."""
    v = row.get(col)
    if v is None:
        return None
    try:
        if isinstance(v, float) and math.isnan(v):
            return None
    except TypeError:
        pass
    return v


def _ratio_score(raw, benchmark):
    if raw is None:
        return None
    return _clamp01(raw / benchmark)


def _matchup_factor(rank_col: str, stat_label: str):
    def raw(row):
        return _get(row, rank_col)

    def score(raw_val, row):
        # rank 1 = best defense (worst matchup) -> 0.0; rank pool_size =
        # worst defense (best matchup) -> 1.0. Note: the literal formula
        # in the original spec, (pool_size - rank) / (pool_size - 1),
        # actually produces the opposite of its own stated example --
        # this is the corrected version, verified against a real
        # rank-32-of-32 matchup scoring 1.0 rather than 0.0.
        rank, pool = raw_val, _get(row, "opp_pool_size")
        if rank is None or pool is None or pool <= 1:
            return None
        return _clamp01((rank - 1) / (pool - 1))

    def reason(raw_val, score_val):
        if raw_val is None or score_val is None:
            return "Matchup: no opponent data yet"
        return f"Matchup: faces {_ordinal(int(raw_val))}-ranked {stat_label} defense (score: {score_val:.2f})"

    return {
        "key": "matchup_rank",
        "label": "Matchup",
        "description": f"Opponent's rank defending {stat_label} -- worse defense scores higher.",
        "raw": raw,
        "score": score,
        "reason": reason,
    }


def _share_factor(key: str, col: str, label: str, benchmark: float, verb: str):
    def raw(row):
        return _get(row, col)

    def score(raw_val, row):
        return _ratio_score(raw_val, benchmark)

    def reason(raw_val, score_val):
        if raw_val is None or score_val is None:
            return f"{label}: no data yet"
        return f"{label}: {raw_val * 100:.0f}% {verb} (score: {score_val:.2f})"

    return {
        "key": key,
        "label": label,
        "description": f"{label}, benchmarked at {benchmark * 100:.0f}% (caps the score at 1.0).",
        "raw": raw,
        "score": score,
        "reason": reason,
    }


def _snap_pct_factor(benchmark: float):
    def raw(row):
        return _get(row, "szn_snap_pct")

    def score(raw_val, row):
        return _ratio_score(raw_val, benchmark)

    def reason(raw_val, score_val):
        if raw_val is None or score_val is None:
            return "Snap %: no data yet"
        return f"Snap %: {raw_val * 100:.0f}% (score: {score_val:.2f})"

    return {
        "key": "snap_pct",
        "label": "Snap %",
        "description": f"Season snap share, benchmarked at {benchmark * 100:.0f}% (full-time).",
        "raw": raw,
        "score": score,
        "reason": reason,
    }


def _trend_factor(key: str, l3_col: str, szn_col: str, label: str):
    def raw(row):
        l3, szn = _get(row, l3_col), _get(row, szn_col)
        if l3 is None or not szn:
            return None
        return l3 / szn

    def score(raw_val, row):
        return _ratio_score(raw_val, _TREND_CAP)

    def reason(raw_val, score_val):
        if raw_val is None or score_val is None:
            return f"{label}: no season baseline yet"
        pct = (raw_val - 1) * 100
        direction = "above" if pct >= 0 else "below"
        return f"{label}: L3 avg {abs(pct):.0f}% {direction} season avg (score: {score_val:.2f})"

    return {
        "key": key,
        "label": label,
        "description": "Trailing-3-game average vs. season average.",
        "raw": raw,
        "score": score,
        "reason": reason,
    }


def _defenders_in_box_factor():
    def raw(row):
        return _get(row, "opp_avg_defenders_in_box")

    def score(raw_val, row):
        ratio = _ratio_score(raw_val, 8.0)
        return None if ratio is None else 1 - ratio

    def reason(raw_val, score_val):
        if raw_val is None or score_val is None:
            return "Defenders in box: no NGS data"
        return f"Defenders in box: {raw_val:.1f} avg (score: {score_val:.2f})"

    return {
        "key": "defenders_in_box",
        "label": "Defenders in Box",
        "description": "Opponent's average defenders in the box vs. the run -- fewer is better for the rusher.",
        "raw": raw,
        "score": score,
        "reason": reason,
    }


def _air_yards_factor():
    def raw(row):
        return _get(row, "szn_air_yards_per_target")

    def score(raw_val, row):
        return _ratio_score(raw_val, 12.0)

    def reason(raw_val, score_val):
        if raw_val is None or score_val is None:
            return "Air yards/target: no data yet"
        return f"Air yards/target: {raw_val:.1f} avg (score: {score_val:.2f})"

    return {
        "key": "air_yards",
        "label": "Air Yards/Target",
        "description": "Season average depth of target -- deep-threat benchmark 12.0.",
        "raw": raw,
        "score": score,
        "reason": reason,
    }


# Every prop type's board (`primary_*_col`), matchup columns, threshold
# default, and factor list. `position` picks which get_weekly_position_board
# call the tab makes.
PROP_TYPES = {
    "RB Rush Yards": {
        "position": "RB",
        "primary_szn_col": "szn_avg_rush_yds",
        "primary_l5_col": "l5_avg_rush_yds",
        "primary_l3_col": "l3_avg_rush_yds",
        "matchup_rank_col": "opp_rank_rush_yards_allowed_vs_rb",
        "threshold_label": "Target line (rush yards)",
        "default_threshold": 60.0,
        "factors": [
            _matchup_factor("opp_rank_rush_yards_allowed_vs_rb", "rush"),
            _share_factor("carry_share_l3", "l3_carry_share", "Carry Share L3", 0.25, "of team carries"),
            _snap_pct_factor(0.80),
            _trend_factor("rush_yards_trend", "l3_avg_rush_yds", "szn_avg_rush_yds", "Rush yards trend"),
            _defenders_in_box_factor(),
        ],
    },
    "RB Rush Attempts": {
        "position": "RB",
        "primary_szn_col": "szn_avg_carries",
        "primary_l5_col": "l5_avg_carries",
        "primary_l3_col": "l3_avg_carries",
        "matchup_rank_col": "opp_rank_rush_yards_allowed_vs_rb",
        "threshold_label": "Target line (rush attempts)",
        "default_threshold": 12.0,
        "factors": [
            _matchup_factor("opp_rank_rush_yards_allowed_vs_rb", "rush"),
            _share_factor("carry_share_l3", "l3_carry_share", "Carry Share L3", 0.25, "of team carries"),
            _snap_pct_factor(0.80),
            _trend_factor("rush_yards_trend", "l3_avg_rush_yds", "szn_avg_rush_yds", "Rush yards trend"),
            _defenders_in_box_factor(),
        ],
    },
    "RB Receiving Yards": {
        "position": "RB",
        "primary_szn_col": "szn_avg_rec_yds",
        "primary_l5_col": "l5_avg_rec_yds",
        "primary_l3_col": "l3_avg_rec_yds",
        "matchup_rank_col": "opp_rank_rec_yards_allowed_vs_rb",
        "threshold_label": "Target line (rec yards)",
        "default_threshold": 40.0,
        "factors": [
            _matchup_factor("opp_rank_rec_yards_allowed_vs_rb", "receiving"),
            _share_factor("target_share_l3", "l3_target_share", "Target Share L3", 0.15, "of team targets"),
            _share_factor("target_rate", "l3_target_rate", "Target Rate L3", 0.15, "of snaps"),
            _trend_factor("rec_yards_trend", "l3_avg_rec_yds", "szn_avg_rec_yds", "Rec yards trend"),
            _snap_pct_factor(0.80),
        ],
    },
    "WR Receiving Yards": {
        "position": "WR",
        "primary_szn_col": "szn_avg_rec_yds",
        "primary_l5_col": "l5_avg_rec_yds",
        "primary_l3_col": "l3_avg_rec_yds",
        "matchup_rank_col": "opp_rank_rec_yards_allowed_vs_wr",
        "threshold_label": "Target line (rec yards)",
        "default_threshold": 40.0,
        "factors": [
            _matchup_factor("opp_rank_rec_yards_allowed_vs_wr", "receiving"),
            _share_factor("target_share_l3", "l3_target_share", "Target Share L3", 0.25, "of team targets"),
            _share_factor("target_rate", "l3_target_rate", "Target Rate L3", 0.20, "of snaps"),
            _trend_factor("rec_yards_trend", "l3_avg_rec_yds", "szn_avg_rec_yds", "Rec yards trend"),
            _air_yards_factor(),
            _snap_pct_factor(0.85),
        ],
    },
    "TE Receiving Yards": {
        "position": "TE",
        "primary_szn_col": "szn_avg_rec_yds",
        "primary_l5_col": "l5_avg_rec_yds",
        "primary_l3_col": "l3_avg_rec_yds",
        "matchup_rank_col": "opp_rank_rec_yards_allowed_vs_te",
        "threshold_label": "Target line (rec yards)",
        "default_threshold": 40.0,
        "factors": [
            _matchup_factor("opp_rank_rec_yards_allowed_vs_te", "receiving"),
            _share_factor("target_share_l3", "l3_target_share", "Target Share L3", 0.15, "of team targets"),
            _share_factor("target_rate", "l3_target_rate", "Target Rate L3", 0.15, "of snaps"),
            _trend_factor("rec_yards_trend", "l3_avg_rec_yds", "szn_avg_rec_yds", "Rec yards trend"),
            _snap_pct_factor(0.80),
        ],
    },
}
