"""Scoring engine: turns one player row (or a whole board) into a
weighted 0-100 score using the factor definitions in factors.py.
"""

import pandas as pd

from optimizer.factors import PROP_TYPES


def compute_score(row, prop_type: str, weights: dict) -> dict:
    """One player row -> {overall_score, factor_scores, factor_values,
    factor_estimated, reasoning}. `weights` is {factor_key: 0-100ish
    slider value}; normalized to sum to 1.0 here, not by the caller. A
    factor whose raw value is NULL scores 0.5 (neutral, never 0) for the
    overall score, and its `factor_estimated` flag is set so the UI can
    mark it as an estimate rather than a real reading.
    """
    factors = PROP_TYPES[prop_type]["factors"]

    total_weight = sum(max(weights.get(f["key"], 0), 0) for f in factors)
    if total_weight <= 0:
        norm_weights = {f["key"]: 1.0 / len(factors) for f in factors}
    else:
        norm_weights = {f["key"]: max(weights.get(f["key"], 0), 0) / total_weight for f in factors}

    factor_scores, factor_values, factor_estimated, reasoning = {}, {}, {}, []
    for f in factors:
        raw_val = f["raw"](row)
        raw_score = f["score"](raw_val, row)  # None means "unknown"
        estimated = raw_score is None
        final_score = 0.5 if estimated else raw_score

        factor_scores[f["key"]] = final_score
        factor_values[f["key"]] = raw_val
        factor_estimated[f["key"]] = estimated
        line = f["reason"](raw_val, raw_score)
        reasoning.append(("~" if estimated else "") + line)

    overall = 100.0 * sum(norm_weights[f["key"]] * factor_scores[f["key"]] for f in factors)

    return {
        "overall_score": overall,
        "factor_scores": factor_scores,
        "factor_values": factor_values,
        "factor_estimated": factor_estimated,
        "reasoning": reasoning,
    }


def score_all_players(board_df: pd.DataFrame, prop_type: str, weights: dict, threshold: float | None = None) -> pd.DataFrame:
    """compute_score applied to every row, sorted by overall_score
    descending. Appends threshold_vs_l3/l5/szn (this window's average
    divided by `threshold`) when threshold is given and > 0.
    """
    if board_df.empty:
        return board_df.copy()

    spec = PROP_TYPES[prop_type]
    results = [compute_score(row, prop_type, weights) for _, row in board_df.iterrows()]

    out = board_df.reset_index(drop=True).copy()
    out["overall_score"] = [r["overall_score"] for r in results]
    out["factor_scores"] = [r["factor_scores"] for r in results]
    out["factor_values"] = [r["factor_values"] for r in results]
    out["factor_estimated"] = [r["factor_estimated"] for r in results]
    out["reasoning"] = [r["reasoning"] for r in results]

    if threshold:
        out["threshold_vs_l3"] = out[spec["primary_l3_col"]] / threshold
        out["threshold_vs_l5"] = out[spec["primary_l5_col"]] / threshold
        out["threshold_vs_szn"] = out[spec["primary_szn_col"]] / threshold

    return out.sort_values("overall_score", ascending=False).reset_index(drop=True)
