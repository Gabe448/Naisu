"""
Chart pattern detection.

Each detector returns a PatternResult or None.
scan_patterns() runs all detectors and returns the list sorted by confidence.

Supported patterns:
  Reversal:     Head & Shoulders, Double Top, Double Bottom
  Continuation: Cup & Handle, Ascending Triangle, Descending Triangle, Falling Wedge
  Bilateral:    Symmetrical Triangle, Megaphone
"""
from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd

from analysis.levels import find_peaks, find_troughs


class PatternType(str, Enum):
    HEAD_AND_SHOULDERS   = "head_and_shoulders"
    DOUBLE_TOP           = "double_top"
    DOUBLE_BOTTOM        = "double_bottom"
    FALLING_WEDGE        = "falling_wedge"
    ASCENDING_TRIANGLE   = "ascending_triangle"
    DESCENDING_TRIANGLE  = "descending_triangle"
    SYMMETRICAL_TRIANGLE = "symmetrical_triangle"
    CUP_AND_HANDLE       = "cup_and_handle"
    MEGAPHONE            = "megaphone"


class PatternBias(str, Enum):
    BULLISH   = "bullish"
    BEARISH   = "bearish"
    BILATERAL = "bilateral"


@dataclass
class PatternResult:
    pattern:    PatternType
    bias:       PatternBias
    confidence: float        # 0.0 – 1.0
    trigger:    float        # breakout / breakdown price
    description: str
    notes:      list[str] = field(default_factory=list)


def scan_patterns(df: pd.DataFrame) -> list[PatternResult]:
    """Run all detectors. Returns list sorted by confidence (highest first)."""
    if len(df) < 30:
        return []

    detectors = [
        _detect_head_and_shoulders,
        _detect_double_top,
        _detect_double_bottom,
        _detect_falling_wedge,
        _detect_ascending_triangle,
        _detect_descending_triangle,
        _detect_symmetrical_triangle,
        _detect_cup_and_handle,
        _detect_megaphone,
    ]

    results = []
    for detect in detectors:
        try:
            result = detect(df)
            if result is not None:
                results.append(result)
        except Exception:
            continue

    return sorted(results, key=lambda r: r.confidence, reverse=True)


# ── Reversal patterns ─────────────────────────────────────────────────────────

def _detect_head_and_shoulders(df: pd.DataFrame) -> PatternResult | None:
    """Bearish: 3 peaks with middle highest; troughs form neckline."""
    peaks   = list(find_peaks(df["high"],   window=5))
    troughs = list(find_troughs(df["low"],  window=5))

    if len(peaks) < 3 or len(troughs) < 2:
        return None

    p1, p2, p3 = peaks[-3], peaks[-2], peaks[-1]
    h1 = float(df.loc[p1, "high"])
    h2 = float(df.loc[p2, "high"])
    h3 = float(df.loc[p3, "high"])

    # Head must be highest; shoulders roughly symmetric (within 7%)
    if not (h2 > h1 and h2 > h3):
        return None
    if abs(h1 - h3) / h2 > 0.07:
        return None

    mid_troughs = [t for t in troughs if p1 < t < p3]
    if not mid_troughs:
        return None

    neckline = float(df.loc[mid_troughs[-1], "low"])
    current  = float(df["close"].iloc[-1])
    proximity = abs(current - neckline) / neckline
    confidence = round(max(0.40, 0.90 - proximity * 5), 2)

    return PatternResult(
        pattern=PatternType.HEAD_AND_SHOULDERS,
        bias=PatternBias.BEARISH,
        confidence=confidence,
        trigger=round(neckline, 2),
        description=f"H&S — neckline ${neckline:.2f}, head ${h2:.2f}",
        notes=[f"L shoulder ${h1:.2f}", f"Head ${h2:.2f}", f"R shoulder ${h3:.2f}"],
    )


def _detect_double_top(df: pd.DataFrame) -> PatternResult | None:
    """Bearish: two peaks at similar levels, neckline = trough between them."""
    peaks   = list(find_peaks(df["high"],  window=5))
    troughs = list(find_troughs(df["low"], window=5))

    if len(peaks) < 2:
        return None

    p1, p2 = peaks[-2], peaks[-1]
    h1 = float(df.loc[p1, "high"])
    h2 = float(df.loc[p2, "high"])

    if abs(h1 - h2) / max(h1, h2) > 0.025:
        return None

    mid_troughs = [t for t in troughs if p1 < t < p2]
    if not mid_troughs:
        return None

    neckline  = float(df.loc[mid_troughs[-1], "low"])
    avg_top   = (h1 + h2) / 2
    proximity = abs(float(df["close"].iloc[-1]) - neckline) / neckline
    confidence = round(max(0.45, 0.85 - proximity * 4), 2)

    return PatternResult(
        pattern=PatternType.DOUBLE_TOP,
        bias=PatternBias.BEARISH,
        confidence=confidence,
        trigger=round(neckline, 2),
        description=f"Double Top — tops ~${avg_top:.2f}, neckline ${neckline:.2f}",
        notes=[f"Top 1 ${h1:.2f}", f"Top 2 ${h2:.2f}"],
    )


def _detect_double_bottom(df: pd.DataFrame) -> PatternResult | None:
    """Bullish: two troughs at similar levels, neckline = peak between them."""
    peaks   = list(find_peaks(df["high"],  window=5))
    troughs = list(find_troughs(df["low"], window=5))

    if len(troughs) < 2:
        return None

    t1, t2 = troughs[-2], troughs[-1]
    l1 = float(df.loc[t1, "low"])
    l2 = float(df.loc[t2, "low"])

    if abs(l1 - l2) / min(l1, l2) > 0.025:
        return None

    mid_peaks = [p for p in peaks if t1 < p < t2]
    if not mid_peaks:
        return None

    neckline  = float(df.loc[mid_peaks[-1], "high"])
    avg_bot   = (l1 + l2) / 2
    proximity = abs(float(df["close"].iloc[-1]) - neckline) / neckline
    confidence = round(max(0.45, 0.85 - proximity * 4), 2)

    return PatternResult(
        pattern=PatternType.DOUBLE_BOTTOM,
        bias=PatternBias.BULLISH,
        confidence=confidence,
        trigger=round(neckline, 2),
        description=f"Double Bottom — bottoms ~${avg_bot:.2f}, neckline ${neckline:.2f}",
        notes=[f"Bottom 1 ${l1:.2f}", f"Bottom 2 ${l2:.2f}"],
    )


# ── Continuation patterns ─────────────────────────────────────────────────────

def _detect_falling_wedge(df: pd.DataFrame) -> PatternResult | None:
    """Bullish (70%): descending converging trendlines."""
    sub = df.tail(min(40, len(df))).copy()

    peaks_idx   = find_peaks(sub["high"],  window=3)
    troughs_idx = find_troughs(sub["low"], window=3)

    if len(peaks_idx) < 2 or len(troughs_idx) < 2:
        return None

    def _slope(idx_list, col: str) -> float:
        xs = np.array([sub.index.get_loc(i) for i in idx_list[-3:]])
        ys = np.array([float(sub.loc[i, col]) for i in idx_list[-3:]])
        return float(np.polyfit(xs, ys, 1)[0]) if len(xs) >= 2 else 0.0

    hi_slope = _slope(peaks_idx,   "high")
    lo_slope = _slope(troughs_idx, "low")

    # Both lines descending; upper falls faster (converging)
    if not (hi_slope < 0 and lo_slope < 0 and hi_slope < lo_slope):
        return None

    current = float(sub["close"].iloc[-1])
    # Estimate upper trendline value at current bar
    xs = np.array([sub.index.get_loc(i) for i in peaks_idx[-3:]])
    ys = np.array([float(sub.loc[i, "high"]) for i in peaks_idx[-3:]])
    coeffs  = np.polyfit(xs, ys, 1)
    trigger = float(coeffs[0] * len(sub) + coeffs[1])

    confidence = round(max(0.40, 0.75 - abs(current - trigger) / max(trigger, 1) * 3), 2)

    return PatternResult(
        pattern=PatternType.FALLING_WEDGE,
        bias=PatternBias.BULLISH,
        confidence=confidence,
        trigger=round(trigger, 2),
        description=f"Falling Wedge — breakout trigger ~${trigger:.2f}",
        notes=[f"Upper slope {hi_slope:.3f}", f"Lower slope {lo_slope:.3f}"],
    )


def _detect_ascending_triangle(df: pd.DataFrame) -> PatternResult | None:
    """Bullish: flat resistance + rising lows."""
    sub   = df.tail(min(40, len(df))).copy()
    highs = sub["high"].values
    lows  = sub["low"].values

    # Resistance: top 10th percentile of highs, clustered within 1.5%
    top_high = np.percentile(highs, 90)
    near_top = highs[highs >= top_high * 0.985]
    if len(near_top) < 2:
        return None
    resistance = float(np.mean(near_top))

    # Rising lows
    x        = np.arange(len(lows))
    lo_slope = float(np.polyfit(x, lows, 1)[0])
    if lo_slope <= 0:
        return None

    current    = float(sub["close"].iloc[-1])
    confidence = 0.65 if current > resistance * 0.97 else 0.45

    return PatternResult(
        pattern=PatternType.ASCENDING_TRIANGLE,
        bias=PatternBias.BULLISH,
        confidence=confidence,
        trigger=round(resistance, 2),
        description=f"Ascending Triangle — resistance ${resistance:.2f}",
        notes=["Rising lows confirmed", f"Flat resistance ~${resistance:.2f}"],
    )


def _detect_descending_triangle(df: pd.DataFrame) -> PatternResult | None:
    """Bearish (70%): flat support + falling highs."""
    sub   = df.tail(min(40, len(df))).copy()
    highs = sub["high"].values
    lows  = sub["low"].values

    bot_low  = np.percentile(lows, 10)
    near_bot = lows[lows <= bot_low * 1.015]
    if len(near_bot) < 2:
        return None
    support = float(np.mean(near_bot))

    x        = np.arange(len(highs))
    hi_slope = float(np.polyfit(x, highs, 1)[0])
    if hi_slope >= 0:
        return None

    current    = float(sub["close"].iloc[-1])
    confidence = 0.65 if current < support * 1.03 else 0.45

    return PatternResult(
        pattern=PatternType.DESCENDING_TRIANGLE,
        bias=PatternBias.BEARISH,
        confidence=confidence,
        trigger=round(support, 2),
        description=f"Descending Triangle — support ${support:.2f}",
        notes=["Falling highs confirmed", f"Flat support ~${support:.2f}"],
    )


# ── Bilateral patterns ────────────────────────────────────────────────────────

def _detect_symmetrical_triangle(df: pd.DataFrame) -> PatternResult | None:
    """Bilateral: converging trendlines — apex = decision point. Use strangle."""
    sub   = df.tail(min(40, len(df))).copy()
    x     = np.arange(len(sub))
    highs = sub["high"].values
    lows  = sub["low"].values

    hi_coeffs = np.polyfit(x, highs, 1)
    lo_coeffs = np.polyfit(x, lows,  1)

    hi_slope = float(hi_coeffs[0])
    lo_slope = float(lo_coeffs[0])

    # Upper descending, lower ascending
    if not (hi_slope < 0 < lo_slope):
        return None

    apex     = len(sub)
    resist   = float(hi_coeffs[0] * apex + hi_coeffs[1])
    support  = float(lo_coeffs[0] * apex + lo_coeffs[1])

    return PatternResult(
        pattern=PatternType.SYMMETRICAL_TRIANGLE,
        bias=PatternBias.BILATERAL,
        confidence=0.60,
        trigger=round((resist + support) / 2, 2),
        description=f"Symmetrical Triangle — apex ${support:.2f}–${resist:.2f} — use strangle",
        notes=[f"Buy trigger: ${resist:.2f}", f"Sell trigger: ${support:.2f}"],
    )


def _detect_cup_and_handle(df: pd.DataFrame) -> PatternResult | None:
    """Bullish: U-shaped cup + shallow handle. Break above rim = buy."""
    if len(df) < 60:
        return None

    sub   = df.tail(60)
    close = sub["close"].values

    cup_start_high = float(np.max(close[:20]))
    cup_low        = float(np.min(close[10:40]))
    cup_depth      = (cup_start_high - cup_low) / cup_start_high

    if not (0.10 <= cup_depth <= 0.50):
        return None

    recovery_high = float(np.max(close[-20:]))
    if recovery_high / cup_start_high < 0.90:
        return None

    handle_low   = float(np.min(close[-10:]))
    handle_depth = (recovery_high - handle_low) / (cup_start_high - cup_low)
    if handle_depth > 0.35:
        return None

    rim        = round(cup_start_high, 2)
    current    = float(df["close"].iloc[-1])
    confidence = round(max(0.50, 0.85 - abs(current - rim) / rim * 3), 2)

    return PatternResult(
        pattern=PatternType.CUP_AND_HANDLE,
        bias=PatternBias.BULLISH,
        confidence=confidence,
        trigger=rim,
        description=f"Cup & Handle — breakout above rim ${rim:.2f}",
        notes=[f"Cup depth {cup_depth:.0%}", f"Handle depth {handle_depth:.0%} of cup"],
    )


def _detect_megaphone(df: pd.DataFrame) -> PatternResult | None:
    """Bilateral — DANGEROUS: expanding volatility. Wait for resolution."""
    sub = df.tail(min(40, len(df))).copy()
    x   = np.arange(len(sub))

    hi_slope = float(np.polyfit(x, sub["high"].values, 1)[0])
    lo_slope = float(np.polyfit(x, sub["low"].values,  1)[0])

    if not (hi_slope > 0 and lo_slope < 0):
        return None

    early_range = float(sub["high"].iloc[:10].mean() - sub["low"].iloc[:10].mean())
    late_range  = float(sub["high"].iloc[-10:].mean() - sub["low"].iloc[-10:].mean())

    if late_range < early_range * 1.20:
        return None

    return PatternResult(
        pattern=PatternType.MEGAPHONE,
        bias=PatternBias.BILATERAL,
        confidence=0.50,
        trigger=float(df["close"].iloc[-1]),
        description="Megaphone — expanding volatility. DANGEROUS. Wait for resolution.",
        notes=["Only trade at clear boundary rejections", "Strangle at extremes only"],
    )
