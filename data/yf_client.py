"""
yfinance-only data client — no Polygon, no LuminaFlow required.

Provides drop-in replacements for:
  get_price(ticker)
  get_multi_tf_context(ticker)
  get_stub_gex(ticker, price)
  get_momentum_flow_bias(ticker, daily_df)

4H bars: built by resampling 1H yfinance data.
GEX: stubbed with price-relative levels (no options data).
Flow bias: derived from 10-day momentum + MA position.
"""
import math

import pandas as pd
import yfinance as yf


def get_price(ticker: str) -> float:
    hist = yf.Ticker(ticker).history(period="2d", interval="1d")
    return float(hist["Close"].iloc[-1]) if not hist.empty else 0.0


def get_multi_tf_context(ticker: str) -> dict:
    daily  = _get_daily_bars(ticker)
    four_h = _get_4h_bars(ticker)
    one_h  = _get_1h_bars(ticker)
    pivots = _compute_weekly_pivots(daily)
    return {"daily": daily, "4h": four_h, "1h": one_h, "weekly_pivots": pivots}


def get_stub_gex(ticker: str, price: float) -> dict:
    """Neutral GEX stub — no options data. Signals scored with partial info."""
    return {
        "ticker":     ticker,
        "total_gex":  0,
        "gex_regime": "positive",
        "gex_flip":   round(price * 1.03, 2),
        "gex_wall":   round(price * 1.08, 2),
        "call_walls": [round(price * 1.05, 2), round(price * 1.08, 2)],
        "put_walls":  [round(price * 0.95, 2), round(price * 0.92, 2)],
        "key_levels": [],
        "spot":       price,
        "total_dex":  0,
        "total_vex":  0,
    }


def get_momentum_flow_bias(ticker: str, daily: pd.DataFrame) -> dict:
    """Flow bias from 10-day price momentum + MA20 position. No options needed."""
    if daily.empty:
        return _neutral_bias(ticker)

    price = float(daily["close"].iloc[-1])
    ma20  = daily["ma20"].iloc[-1] if "ma20" in daily.columns else None

    if ma20 is None or pd.isna(ma20) or ma20 == 0:
        return _neutral_bias(ticker)

    mom = 0.0
    if len(daily) >= 11:
        prev = float(daily["close"].iloc[-11])
        mom  = (price - prev) / prev if prev else 0.0

    above_ma20 = price > ma20

    if above_ma20 and mom > 0.01:
        bias     = "bullish"
        strength = min(abs(mom) * 5, 1.0)
    elif not above_ma20 and mom < -0.01:
        bias     = "bearish"
        strength = min(abs(mom) * 5, 1.0)
    else:
        return _neutral_bias(ticker)

    call_pct  = 0.5 + (strength * 0.4 if bias == "bullish" else -strength * 0.4)
    # Non-zero sentinel so _score_flow doesn't short-circuit on total_dex == 0.
    # Magnitude is proportional to strength so log10 normalization in scoring
    # produces the right score tier.
    synthetic_dex = int(1e9 * strength) * (1 if bias == "bullish" else -1)
    return {
        "ticker":     ticker,
        "bias":       bias,
        "strength":   round(strength, 3),
        "total_dex":  synthetic_dex,
        "call_pct":   round(call_pct, 3),
        "put_pct":    round(1 - call_pct, 3),
        "call_count": 0,
        "put_count":  0,
        "source":     "yf_momentum",
    }


# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_daily_bars(ticker: str) -> pd.DataFrame:
    hist = yf.Ticker(ticker).history(period="3mo", interval="1d")
    if hist.empty:
        return pd.DataFrame()
    df = hist[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.columns = ["open", "high", "low", "close", "volume"]
    df.index   = df.index.tz_localize(None) if df.index.tzinfo else df.index
    df["ma20"] = df["close"].rolling(20).mean()
    df["ma50"] = df["close"].rolling(50).mean()
    return df


def _get_4h_bars(ticker: str) -> pd.DataFrame:
    hist = yf.Ticker(ticker).history(period="60d", interval="1h")
    if hist.empty:
        return pd.DataFrame()
    df = hist[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.columns = ["open", "high", "low", "close", "volume"]
    df.index   = df.index.tz_localize(None) if df.index.tzinfo else df.index
    df = df.resample("4h").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna()
    return df


def _get_1h_bars(ticker: str) -> pd.DataFrame:
    hist = yf.Ticker(ticker).history(period="5d", interval="1h")
    if hist.empty:
        return pd.DataFrame()
    df = hist[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.columns = ["open", "high", "low", "close", "volume"]
    df.index   = df.index.tz_localize(None) if df.index.tzinfo else df.index
    return df


def _compute_weekly_pivots(daily: pd.DataFrame) -> dict:
    if daily.empty or len(daily) < 5:
        return {}
    idx = pd.to_datetime(daily.index)
    week = idx.isocalendar().week.values
    year = idx.isocalendar().year.values
    cur_w, cur_y = week[-1], year[-1]
    mask     = (week != cur_w) | (year != cur_y)
    prev     = daily[mask]
    if prev.empty:
        return {}
    H  = prev["high"].max()
    L  = prev["low"].min()
    C  = float(prev["close"].iloc[-1])
    P  = (H + L + C) / 3
    return {
        "P":  P,
        "R1": 2 * P - L,
        "R2": P + (H - L),
        "S1": 2 * P - H,
        "S2": P - (H - L),
        "prev_high": H,
        "prev_low":  L,
    }


def _neutral_bias(ticker: str) -> dict:
    return {
        "ticker": ticker, "bias": "neutral", "strength": 0.0,
        "total_dex": 0, "call_pct": 0.5, "put_pct": 0.5,
        "call_count": 0, "put_count": 0, "source": "yf_momentum",
    }
