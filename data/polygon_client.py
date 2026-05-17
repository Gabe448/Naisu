"""
Polygon.io client — price data, news, snapshots, multi-timeframe OHLCV.

Timeframe philosophy:
  1D  bars → trend direction, MA levels, weekly pivots
  4H  bars → intermediate structure, where swing entry zones form
  1H  bars → entry/exit precision trigger
"""
import os
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import requests as _requests
from polygon import RESTClient
from data.api_errors import RateLimitError, raise_for_status_smart


def get_client() -> RESTClient:
    return RESTClient(api_key=os.environ["POLYGON_API_KEY"])


def get_snapshot(ticker: str) -> object:
    """
    Real-time snapshot — requires Polygon Starter plan.
    Falls back to previous close from daily aggs (free tier) if 403.
    Returns a duck-typed object with .day.close so callers don't change.
    """
    try:
        client = get_client()
        return client.get_snapshot_ticker("stocks", ticker)
    except Exception:
        return _prev_close_fallback(ticker)


class _FakeSnapshot:
    """Mimics the Polygon snapshot object shape used throughout the codebase."""
    def __init__(self, close: float):
        self.day = type("day", (), {"close": close})()


def _prev_close_fallback(ticker: str) -> _FakeSnapshot:
    """Use previous close from daily aggs — available on free tier."""
    key = os.environ["POLYGON_API_KEY"]
    r = _requests.get(
        f"https://api.polygon.io/v2/aggs/ticker/{ticker}/prev",
        params={"apiKey": key, "adjusted": "true"},
        timeout=10,
    )
    raise_for_status_smart(r, "Polygon")   # lets 429 propagate as RateLimitError
    results = r.json().get("results", [])
    if results:
        return _FakeSnapshot(close=results[0]["c"])
    return _FakeSnapshot(close=0.0)


def get_news(ticker: str, limit: int = 10) -> list:
    client = get_client()
    return list(client.list_ticker_news(ticker, limit=limit))


# ── OHLCV helpers ────────────────────────────────────────────────────────────

def get_daily_bars(ticker: str, lookback_days: int = 60) -> pd.DataFrame:
    """Daily OHLCV — used for trend, MAs, weekly pivots."""
    to_date = datetime.now().strftime("%Y-%m-%d")
    from_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    return _fetch_bars(ticker, 1, "day", from_date, to_date)


def get_4h_bars(ticker: str, lookback_days: int = 20) -> pd.DataFrame:
    """4-hour bars — intermediate structure for swing setup identification."""
    to_date = datetime.now().strftime("%Y-%m-%d")
    from_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    return _fetch_bars(ticker, 4, "hour", from_date, to_date)


def get_1h_bars(ticker: str, lookback_days: int = 5) -> pd.DataFrame:
    """1-hour bars — entry/exit precision."""
    to_date = datetime.now().strftime("%Y-%m-%d")
    from_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    return _fetch_bars(ticker, 1, "hour", from_date, to_date)


def _fetch_bars(ticker: str, multiplier: int, timespan: str, from_date: str, to_date: str) -> pd.DataFrame:
    client = get_client()
    aggs = list(client.get_aggs(ticker, multiplier, timespan, from_date, to_date, limit=5000))
    if not aggs:
        return pd.DataFrame()

    df = pd.DataFrame([{
        "timestamp": a.timestamp,
        "open":   a.open,
        "high":   a.high,
        "low":    a.low,
        "close":  a.close,
        "volume": a.volume,
        "vwap":   getattr(a, "vwap", None),
    } for a in aggs])

    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("datetime").sort_index()
    return df


# ── Derived metrics ───────────────────────────────────────────────────────────

def compute_moving_averages(df: pd.DataFrame) -> pd.DataFrame:
    """Add 20 and 50 period MAs to a bars DataFrame."""
    df = df.copy()
    df["ma20"] = df["close"].rolling(20).mean()
    df["ma50"] = df["close"].rolling(50).mean()
    return df


def get_weekly_pivots(ticker: str) -> dict:
    """
    Previous week's H/L/C → standard pivot points.
    These act as key S/R for swing entries.
    """
    df = get_daily_bars(ticker, lookback_days=14)
    if df.empty:
        return {}

    # Identify bars from last completed week
    df["week"] = df.index.isocalendar().week
    df["year"] = df.index.isocalendar().year
    current_week = df.iloc[-1]["week"]
    current_year = df.iloc[-1]["year"]

    prev_week = df[(df["week"] != current_week) | (df["year"] != current_year)]
    if prev_week.empty:
        return {}

    H = prev_week["high"].max()
    L = prev_week["low"].min()
    C = prev_week["close"].iloc[-1]

    P  = (H + L + C) / 3
    R1 = 2 * P - L
    S1 = 2 * P - H
    R2 = P + (H - L)
    S2 = P - (H - L)

    return {"P": P, "R1": R1, "R2": R2, "S1": S1, "S2": S2, "prev_high": H, "prev_low": L}


def get_multi_tf_context(ticker: str) -> dict:
    """
    Single call that returns all timeframe data with MAs computed.
    This is the main input to the signal builder.
    """
    daily = compute_moving_averages(get_daily_bars(ticker, lookback_days=60))
    four_h = get_4h_bars(ticker, lookback_days=20)
    one_h  = get_1h_bars(ticker, lookback_days=5)
    pivots = get_weekly_pivots(ticker)

    return {
        "daily": daily,
        "4h":    four_h,
        "1h":    one_h,
        "weekly_pivots": pivots,
    }
