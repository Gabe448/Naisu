"""
Rule-based signal checkers — the 7 key decision rules from the strategy.

Each function returns a SignalResult with:
  name     — identifier used by confluence scorer
  fired    — True if the signal is active
  strength — 0.0–1.0 (used to weight the confluence score)
  notes    — human-readable explanation

Rules:
  1. Multiple resistance rejections  → distribution / reversal likely
  2. Rejection wicks at extremes     → buyers/sellers stepped in
  3. EMA reclaim / loss              → trend confirmation
  4. Volume spike at lows            → capitulation / exhaustion
  5. Failed breakout                 → sharp reversal opposite direction
  (6 & 7 — sector behavior / post-earnings handled at the caller level)
  +  Trend alignment (EMA stack)     → macro trend filter
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class SignalResult:
    name:     str
    fired:    bool
    strength: float   # 0.0 – 1.0
    notes:    list[str] = field(default_factory=list)


# ── Rule 1 ────────────────────────────────────────────────────────────────────

def check_multiple_rejections(
    df: pd.DataFrame,
    level: float,
    tolerance_pct: float = 0.015,
) -> SignalResult:
    """
    2–3+ tests at a resistance/support level with lower highs = distribution.
    Bearish when above level; bullish when below.
    """
    band_hi = level * (1 + tolerance_pct)
    band_lo = level * (1 - tolerance_pct)

    touches     = 0
    lower_highs = []
    prev_high: float | None = None

    for _, row in df.iterrows():
        in_band = band_lo <= row["high"] <= band_hi or (
            row["high"] >= band_lo and row["close"] <= band_hi
        )
        if in_band:
            touches += 1
            if prev_high is not None:
                lower_highs.append(bool(row["high"] < prev_high))
            prev_high = float(row["high"])

    fired    = touches >= 2
    strength = round(min(touches / 4, 1.0), 2)
    lh_count = sum(lower_highs)

    notes = [f"{touches} touches at ${level:.2f}"]
    if lh_count:
        notes.append(f"{lh_count} lower highs → distribution")

    return SignalResult(name="multiple_rejections", fired=fired, strength=strength, notes=notes)


# ── Rule 2 ────────────────────────────────────────────────────────────────────

def check_rejection_wicks(df: pd.DataFrame) -> SignalResult:
    """
    Long wicks at extremes = buyers/sellers stepping in.
    Especially powerful at S/R. Checks last 3 bars.
    """
    recent       = df.tail(3)
    max_wick_pct = 0.0
    notes        = []

    for _, row in recent.iterrows():
        price      = float(row["close"])
        upper_wick = float(row["high"]) - max(float(row["close"]), float(row["open"]))
        lower_wick = min(float(row["close"]), float(row["open"])) - float(row["low"])

        upper_pct = upper_wick / price
        lower_pct = lower_wick / price

        if upper_pct >= 0.01:
            notes.append(f"Upper wick {upper_pct:.1%} — bearish rejection")
            max_wick_pct = max(max_wick_pct, upper_pct)
        if lower_pct >= 0.01:
            notes.append(f"Lower wick {lower_pct:.1%} — bullish rejection")
            max_wick_pct = max(max_wick_pct, lower_pct)

    fired    = max_wick_pct >= 0.01
    strength = round(min(max_wick_pct / 0.03, 1.0), 2)  # 3% wick → full strength

    return SignalResult(
        name="rejection_wicks",
        fired=fired,
        strength=strength,
        notes=notes or ["No significant wicks in last 3 bars"],
    )


# ── Rule 3 ────────────────────────────────────────────────────────────────────

def check_ema_reclaim(df: pd.DataFrame) -> SignalResult:
    """
    EMA reclaim (bullish) or loss (bearish) on 20 / 50 / 200.
    200 EMA = macro trend filter — don't fight it.
    """
    from analysis.levels import compute_emas
    df = compute_emas(df)

    current = float(df["close"].iloc[-1])
    prev    = float(df["close"].iloc[-2])
    notes   = []
    score   = 0.0

    weights = {20: 0.25, 50: 0.35, 200: 0.40}

    for span, w in weights.items():
        col = f"ema{span}"
        if col not in df.columns:
            continue
        ema_now  = float(df[col].iloc[-1])
        ema_prev = float(df[col].iloc[-2])

        above_now  = current > ema_now
        above_prev = prev    > ema_prev

        if above_now and not above_prev:
            score += w
            notes.append(f"Reclaimed EMA{span} ${ema_now:.2f} ✅ bullish")
        elif not above_now and above_prev:
            score += w
            notes.append(f"Lost EMA{span} ${ema_now:.2f} ❌ bearish")
        elif above_now:
            score += w * 0.4
            notes.append(f"Above EMA{span} ${ema_now:.2f}")
        else:
            notes.append(f"Below EMA{span} ${ema_now:.2f}")

    fired = score > 0.1
    return SignalResult(
        name="ema_reclaim",
        fired=fired,
        strength=round(min(score, 1.0), 2),
        notes=notes,
    )


# ── Rule 4 ────────────────────────────────────────────────────────────────────

def check_volume_capitulation(df: pd.DataFrame) -> SignalResult:
    """
    Volume spike at lows = exhaustion / capitulation.
    1.5x+ average volume at or near the 20-day low.
    With rejection wick → reversal signal.
    """
    if len(df) < 21:
        return SignalResult(name="volume_capitulation", fired=False, strength=0.0)

    avg_vol   = float(df["volume"].iloc[-21:-1].mean())
    today_vol = float(df["volume"].iloc[-1])
    rel_vol   = today_vol / avg_vol if avg_vol > 0 else 1.0

    current = float(df["close"].iloc[-1])
    lo_20   = float(df["low"].tail(20).min())
    at_low  = current <= lo_20 * 1.03   # within 3% of 20-day low

    fired    = rel_vol >= 1.5 and at_low
    strength = round(min((rel_vol - 1.0) / 2.0, 1.0), 2) if at_low else 0.0

    return SignalResult(
        name="volume_capitulation",
        fired=fired,
        strength=strength,
        notes=[
            f"Rel volume: {rel_vol:.1f}x avg",
            "At 20-day low ✅" if at_low else "Not at low — partial signal only",
        ],
    )


# ── Rule 5 ────────────────────────────────────────────────────────────────────

def check_failed_breakout(
    df: pd.DataFrame,
    level: float,
    tolerance_pct: float = 0.015,
) -> SignalResult:
    """
    Price attempted to break level, failed, reversed.
    Failed breakout above level → bearish.
    Failed breakdown below level → bullish.
    """
    if len(df) < 5:
        return SignalResult(name="failed_breakout", fired=False, strength=0.0)

    lookback = df.tail(10)
    current  = float(df["close"].iloc[-1])

    broke_above = any(
        float(r["close"]) > level * (1 + tolerance_pct)
        for _, r in lookback.iterrows()
    )
    broke_below = any(
        float(r["close"]) < level * (1 - tolerance_pct)
        for _, r in lookback.iterrows()
    )

    failed_above = broke_above and current < level * 0.995
    failed_below = broke_below and current > level * 1.005

    fired = failed_above or failed_below
    note  = ""
    if failed_above:
        note = f"Failed breakout above ${level:.2f} → bearish reversal"
    elif failed_below:
        note = f"Failed breakdown below ${level:.2f} → bullish reversal"

    return SignalResult(
        name="failed_breakout",
        fired=fired,
        strength=0.80 if fired else 0.0,
        notes=[note] if note else ["No failed breakout detected"],
    )


# ── Trend alignment (EMA stack) ───────────────────────────────────────────────

def check_trend_alignment(df: pd.DataFrame) -> SignalResult:
    """
    Bullish stack: price > EMA20 > EMA50 > EMA200.
    Bearish stack: price < EMA20 < EMA50 < EMA200.
    200 EMA = macro trend filter — don't fight it.
    """
    from analysis.levels import compute_emas
    df = compute_emas(df)

    current = float(df["close"].iloc[-1])
    emas    = {
        span: float(df[f"ema{span}"].iloc[-1])
        for span in (20, 50, 200)
        if f"ema{span}" in df.columns
    }

    if not emas:
        return SignalResult(name="trend_alignment", fired=False, strength=0.0)

    bullish_stack = (
        len(emas) == 3
        and current > emas[20] > emas[50] > emas[200]
    )
    bearish_stack = (
        len(emas) == 3
        and current < emas[20] < emas[50] < emas[200]
    )

    fired    = bullish_stack or bearish_stack
    strength = 0.85 if fired else 0.35

    notes = []
    if bullish_stack:
        notes.append("✅ Bullish EMA stack: price > 20 > 50 > 200")
    elif bearish_stack:
        notes.append("❌ Bearish EMA stack: price < 20 < 50 < 200")
    else:
        notes.append("Mixed EMA stack — no clean trend confirmation")
        for span, val in sorted(emas.items()):
            side = "above" if current > val else "below"
            notes.append(f"  EMA{span} ${val:.2f} — price {side}")

    return SignalResult(
        name="trend_alignment",
        fired=fired,
        strength=round(strength, 2),
        notes=notes,
    )


# ── Convenience runner ────────────────────────────────────────────────────────

def run_all_signals(
    df: pd.DataFrame,
    key_level: float | None = None,
) -> list[SignalResult]:
    """
    Run the full signal suite.
    key_level: nearest S/R level for rejection and breakout checks.
    """
    results = [
        check_rejection_wicks(df),
        check_ema_reclaim(df),
        check_volume_capitulation(df),
        check_trend_alignment(df),
    ]

    if key_level is not None:
        results += [
            check_multiple_rejections(df, key_level),
            check_failed_breakout(df, key_level),
        ]

    return results
