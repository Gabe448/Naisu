"""
GEX / DEX analysis from LuminaFlow data.

GEX (Gamma Exposure):
  Positive GEX → market makers long gamma → BUY dips, SELL rips → mean reversion / chop
  Negative GEX → market makers short gamma → amplify moves → trending / expansion

DEX (Delta Exposure):
  Positive DEX → dealers net long delta → bullish structural tilt
  Negative DEX → dealers net short delta → bearish structural tilt

extract_dex_bias() is the pipeline path (avoids a second API call when GEX
data is already fetched upstream). flow_scanner handles on-demand requests.
"""
import math
from dataclasses import dataclass, field


@dataclass
class GexContext:
    ticker:       str
    gex:          float
    dex:          float
    gex_regime:   str       # "positive" | "negative" | "neutral"
    dex_bias:     str       # "bullish" | "bearish" | "neutral"
    dex_strength: float     # 0.0 - 1.0
    call_pct:     float
    put_pct:      float
    notes:        list[str] = field(default_factory=list)


def extract_dex_bias(gex_result: dict) -> dict:
    """
    Extract directional bias dict from a LuminaFlow GEX API response.
    Return shape mirrors flow_scanner.get_ticker_flow_bias() for compatibility.
    """
    ticker    = gex_result.get("ticker", "")
    total_dex = float(gex_result.get("total_dex", 0) or 0)

    if total_dex == 0:
        return _neutral_dex(ticker)

    bias     = "bullish" if total_dex > 0 else "bearish"
    # Log10 normalisation: denominator 9 maps ~$1B DEX to strength 1.0
    strength = round(min(math.log10(max(abs(total_dex), 1)) / 9, 1.0), 3)
    call_pct = round(0.5 + (strength * 0.4 if bias == "bullish" else -strength * 0.4), 3)

    return {
        "ticker":    ticker,
        "bias":      bias,
        "strength":  strength,
        "total_dex": total_dex,
        "call_pct":  call_pct,
        "put_pct":   round(1 - call_pct, 3),
        "source":    "luminaflow_dex",
    }


def parse_gex_context(ticker: str, gex_result: dict) -> GexContext:
    """
    Full GEX context including regime + DEX bias.
    Positive GEX  → tighter targets / stops (mean reversion).
    Negative GEX  → wider stops, let runners go (trending).
    """
    total_gex = float(gex_result.get("total_gex", 0) or 0)
    dex_info  = extract_dex_bias(gex_result)

    if abs(total_gex) < 1_000_000:
        gex_regime = "neutral"
    elif total_gex > 0:
        gex_regime = "positive"
    else:
        gex_regime = "negative"

    notes: list[str] = []
    if gex_regime == "positive":
        notes.append("Positive GEX: mean reversion likely — tighter targets, smaller stops")
    elif gex_regime == "negative":
        notes.append("Negative GEX: trending environment — let runners go, wider stops")
    else:
        notes.append("Neutral GEX — no strong regime signal")

    bias_str = dex_info["bias"]
    strength = dex_info["strength"]
    if bias_str == "bullish":
        notes.append(f"DEX bullish (strength {strength:.2f}) — dealer hedging supports upside")
    elif bias_str == "bearish":
        notes.append(f"DEX bearish (strength {strength:.2f}) — dealer hedging pressures downside")

    return GexContext(
        ticker=ticker,
        gex=total_gex,
        dex=float(gex_result.get("total_dex", 0) or 0),
        gex_regime=gex_regime,
        dex_bias=bias_str,
        dex_strength=strength,
        call_pct=dex_info["call_pct"],
        put_pct=dex_info["put_pct"],
        notes=notes,
    )


def analyze_exposure(ticker: str) -> dict:
    """
    Fetch GEX/DEX/VEX from LuminaFlow and return a structured exposure dict.

    Returned keys (mirrors get_stub_gex shape for drop-in compatibility):
      ticker, spot, total_gex, total_dex, total_vex, net_vex,
      gex_regime, vex_regime, gex_flip, gex_wall, call_wall,
      put_wall, king_above, king_below, call_walls, put_walls,
      key_levels, top_gex_strikes, strikes
    """
    from data.luminaflow_client import get_gex
    raw = get_gex(ticker)

    spot       = float(raw.get("spot", 0) or 0)
    total_gex  = float(raw.get("net_gex", 0) or 0)
    total_dex  = float(raw.get("net_dex", 0) or 0)
    total_vex  = float(raw.get("net_vex", 0) or 0)

    # GEX regime
    if abs(total_gex) < 1_000_000:
        gex_regime = "neutral"
    elif total_gex > 0:
        gex_regime = "positive"
    else:
        gex_regime = "negative"

    vex_regime = "positive" if total_vex > 0 else ("negative" if total_vex < 0 else "neutral")

    # LuminaFlow returns king_above/king_below as dicts: {"strike": 738, "gex": 268062921}
    king_above_raw = raw.get("king_above")
    king_below_raw = raw.get("king_below")
    king_above = king_above_raw.get("strike") if isinstance(king_above_raw, dict) else king_above_raw
    king_below = king_below_raw.get("strike") if isinstance(king_below_raw, dict) else king_below_raw

    # Sort strikes by GEX to derive walls
    strikes_raw = raw.get("strikes", [])
    above = sorted(
        [s for s in strikes_raw if s.get("strike", 0) > spot],
        key=lambda s: s.get("gex", 0), reverse=True
    )
    below = sorted(
        [s for s in strikes_raw if s.get("strike", 0) <= spot],
        key=lambda s: s.get("gex", 0)
    )

    call_wall = above[0]["strike"] if above else (king_above or round(spot * 1.05, 2))
    put_wall  = below[0]["strike"] if below else round(spot * 0.95, 2)
    call_walls = [s["strike"] for s in above[:2]] or [round(spot * 1.05, 2), round(spot * 1.08, 2)]
    put_walls  = [s["strike"] for s in below[:2]] or [round(spot * 0.95, 2), round(spot * 0.92, 2)]

    # Gamma flip: king_above if available, else call_wall
    gex_flip = float(king_above or call_wall)

    # Top strikes by absolute GEX for Claude context
    key_levels = sorted(strikes_raw, key=lambda s: abs(s.get("gex", 0)), reverse=True)[:10]

    return {
        "ticker":          ticker,
        "spot":            spot,
        "total_gex":       total_gex,
        "total_dex":       total_dex,
        "total_vex":       total_vex,
        "net_vex":         total_vex,
        "gex_regime":      gex_regime,
        "vex_regime":      vex_regime,
        "gex_flip":        gex_flip,
        "gex_wall":        call_wall,
        "call_wall":       call_wall,
        "put_wall":        put_wall,
        "king_above":      king_above,
        "king_below":      king_below,
        "call_walls":      call_walls,
        "put_walls":       put_walls,
        "key_levels":      key_levels,
        "top_gex_strikes": key_levels,
        "strikes":         strikes_raw,
    }


def get_nearest_gex_level(price: float, gex: dict) -> float | None:
    """Return the strike in gex['key_levels'] closest to price."""
    levels = gex.get("key_levels") or gex.get("strikes", [])
    if not levels:
        return None
    return min(levels, key=lambda s: abs(s.get("strike", 0) - price)).get("strike")


def _neutral_dex(ticker: str) -> dict:
    return {
        "ticker":    ticker,
        "bias":      "neutral",
        "strength":  0.0,
        "total_dex": 0,
        "call_pct":  0.5,
        "put_pct":   0.5,
        "source":    "luminaflow_dex",
    }
