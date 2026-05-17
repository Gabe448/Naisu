"""
EMA computation and support/resistance level detection.

Used by patterns, signals, and trade_builder to find key price levels.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class Level:
    price:      float
    touches:    int
    level_type: str    # "support" | "resistance" | "both"
    strength:   float  # 0.0 – 1.0


def compute_emas(df: pd.DataFrame) -> pd.DataFrame:
    """Add ema20 / ema50 / ema100 / ema200 columns. Returns a copy."""
    out = df.copy()
    for span in (20, 50, 100, 200):
        out[f"ema{span}"] = out["close"].ewm(span=span, adjust=False).mean()
    return out


# ── Pivot detection ───────────────────────────────────────────────────────────

def find_peaks(series: pd.Series, window: int = 5) -> pd.Index:
    """Indices where value is the local maximum within ±window bars."""
    arr   = series.values
    idxs  = []
    for i in range(window, len(arr) - window):
        if arr[i] == max(arr[i - window : i + window + 1]):
            idxs.append(series.index[i])
    return pd.Index(idxs)


def find_troughs(series: pd.Series, window: int = 5) -> pd.Index:
    """Indices where value is the local minimum within ±window bars."""
    arr  = series.values
    idxs = []
    for i in range(window, len(arr) - window):
        if arr[i] == min(arr[i - window : i + window + 1]):
            idxs.append(series.index[i])
    return pd.Index(idxs)


def find_pivots(df: pd.DataFrame, window: int = 5) -> tuple[list[float], list[float]]:
    """Return (peak_prices, trough_prices) for the DataFrame."""
    peaks   = find_peaks(df["high"], window)
    troughs = find_troughs(df["low"],  window)
    return (
        [float(df.loc[p, "high"]) for p in peaks],
        [float(df.loc[t, "low"])  for t in troughs],
    )


# ── S/R clustering ────────────────────────────────────────────────────────────

def cluster_levels(prices: list[float], tolerance_pct: float = 0.015) -> list[Level]:
    """
    Group nearby pivot prices into S/R clusters.
    Prices within tolerance_pct of the first element → same cluster.
    """
    if not prices:
        return []

    clusters: list[list[float]] = []
    for price in sorted(prices):
        placed = False
        for cluster in clusters:
            if abs(price - cluster[0]) / cluster[0] <= tolerance_pct:
                cluster.append(price)
                placed = True
                break
        if not placed:
            clusters.append([price])

    levels = []
    for cluster in clusters:
        avg   = sum(cluster) / len(cluster)
        strength = min(len(cluster) / 5, 1.0)   # 5 touches = max strength
        levels.append(Level(
            price=round(avg, 2),
            touches=len(cluster),
            level_type="both",
            strength=strength,
        ))
    return sorted(levels, key=lambda x: x.price)


def get_key_levels(df: pd.DataFrame, window: int = 5) -> list[Level]:
    """Full pipeline: find pivots → cluster → tag support/resistance."""
    peaks, troughs = find_pivots(df, window)
    levels         = cluster_levels(peaks + troughs)
    current        = float(df["close"].iloc[-1])

    for lvl in levels:
        if lvl.price < current * 0.99:
            lvl.level_type = "support"
        elif lvl.price > current * 1.01:
            lvl.level_type = "resistance"
        else:
            lvl.level_type = "both"

    return levels


# ── Nearest level helpers ─────────────────────────────────────────────────────

def nearest_support(levels: list[Level], price: float) -> float | None:
    supports = [l.price for l in levels if l.price < price * 0.99]
    return max(supports) if supports else None


def nearest_resistance(levels: list[Level], price: float) -> float | None:
    resistances = [l.price for l in levels if l.price > price * 1.01]
    return min(resistances) if resistances else None
