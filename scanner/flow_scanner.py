"""
DEX-based directional bias — replaces Unusual Whales entirely.

LuminaFlow's DEX (Delta Exposure) tells us net dealer delta positioning:
  DEX > 0 → dealers net long delta → they sold puts / net bullish structural tilt
  DEX < 0 → dealers net short delta → they sold calls / net bearish structural tilt

Why DEX beats options flow for swing trading:
  - Represents aggregate accumulated positioning, not just today's prints
  - Tells you which way dealers MUST hedge as price moves (directional pressure)
  - Not polluted by retail 0DTE noise
  - Already available since we call LuminaFlow for GEX anyway

Public API intentionally mirrors the old Unusual Whales signature so nothing
upstream needs to change. In the pipeline path, use extract_dex_bias(gex_result)
from gex_analysis.py instead — it avoids a redundant second API call.
"""
import math

from data.luminaflow_client import get_dex


def get_ticker_flow_bias(ticker: str, **kwargs) -> dict:
    """
    Derives directional bias from LuminaFlow DEX.
    Drop-in replacement for the old UW version — same return shape.
    Used by the on-demand !signal command path.
    In the pipeline path, extract_dex_bias(gex_result) is used instead.
    """
    try:
        dex_data = get_dex(ticker)
        return _parse_dex_bias(ticker, dex_data)
    except Exception:
        return _neutral_bias(ticker)


def _parse_dex_bias(ticker: str, dex_data: dict) -> dict:
    total_dex = float(dex_data.get("total_dex", 0) or 0)

    if total_dex == 0:
        return _neutral_bias(ticker)

    bias = "bullish" if total_dex > 0 else "bearish"

    # Log10 normalization — DEX spans many orders of magnitude across tickers.
    # Denominator 9 maps ~$1B to strength 1.0. Tune after seeing real LuminaFlow data.
    strength = min(math.log10(max(abs(total_dex), 1)) / 9, 1.0)

    # Map strength to call_pct equivalent (backward compat with scoring.py)
    call_pct = 0.5 + (strength * 0.4 if bias == "bullish" else -strength * 0.4)

    return {
        "ticker":    ticker,
        "bias":      bias,
        "strength":  round(strength, 3),
        "total_dex": total_dex,
        "call_pct":  round(call_pct, 3),
        "put_pct":   round(1 - call_pct, 3),
        "call_count": 0,
        "put_count":  0,
        "source":    "luminaflow_dex",
    }


def _neutral_bias(ticker: str) -> dict:
    return {
        "ticker":    ticker,
        "bias":      "neutral",
        "strength":  0.0,
        "total_dex": 0,
        "call_pct":  0.5,
        "put_pct":   0.5,
        "call_count": 0,
        "put_count":  0,
        "source":    "luminaflow_dex",
    }


# Kept for import compatibility — no longer scans Unusual Whales
def scan_swing_flow(*args, **kwargs) -> list:
    return []
