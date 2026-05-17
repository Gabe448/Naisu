"""
DEPRECATED — scoring rules removed.

Claude now makes all decisions from raw data. See analysis/claude_analyst.py.

This file is kept as a stub so legacy imports (signal_builder, prompts) don't crash.
CONVICTION_THRESHOLD and MIN_RR are exported for reference only — they are not
used in the main pipeline. Claude does not use a numeric threshold.
"""
import os
import pandas as pd

# Kept for reference / legacy imports only. Not used by Claude.
CONVICTION_THRESHOLD = int(os.environ.get("CONVICTION_THRESHOLD", 70))
MIN_RR               = float(os.environ.get("MIN_RR_RATIO", 2.0))


def score_signal(
    ticker:    str,
    price:     float,
    daily_df:  pd.DataFrame,
    four_h_df: pd.DataFrame,
    gex:       dict,
    flow_bias: dict,
    entry:     float,
    stop:      float,
    target:    float | None = None,
    t1:        float | None = None,
    t2:        float | None = None,
    t3:        float | None = None,
    signals:   list | None  = None,
    pattern:   dict | None  = None,
) -> dict:
    """
    Stub — returns a pass-through result. All actual decisions made by Claude.
    Kept so signal_builder.py (legacy !signal path) doesn't crash.
    """
    if t2 is None:
        t2 = target
    rr = round(abs((t2 - entry) / (entry - stop)), 2) if (t2 and entry != stop) else 0
    direction = _infer_direction(signals or [], price, daily_df, four_h_df, flow_bias)
    return {
        "ticker":             ticker,
        "total_score":        100,
        "is_high_conviction": True,
        "direction":          direction,
        "entry":              entry,
        "stop":               stop,
        "target":             t2,
        "rr_ratio":           rr,
        "components":         {},
    }


def _infer_direction(signals, price, daily, four_h, flow_bias) -> str:
    votes  = [s["direction"] for s in signals if s.get("direction") in ("long", "short")]
    longs  = votes.count("long")
    shorts = votes.count("short")
    if longs != shorts:
        return "long" if longs > shorts else "short"
    bias = (flow_bias or {}).get("bias", "neutral")
    if bias == "bullish":
        return "long"
    if bias == "bearish":
        return "short"
    if daily is not None and not daily.empty:
        last = daily.iloc[-1]
        ma20 = last.get("ma20")
        if ma20 and not pd.isna(ma20):
            return "long" if price > ma20 else "short"
    return "long"
