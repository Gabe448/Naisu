"""
scanner.py — S&P 500 scanner, runs every 10 minutes.
Scores stocks on volume spike, momentum, RSI, and news.
Filters out extended/overheated names before scoring.
Returns top 10 candidates for Claude analysis.
"""

import yfinance as yf
import pandas as pd
import numpy as np
import requests
import os
from datetime import datetime, timedelta

POLYGON_API_KEY = os.getenv("POLYGON_API_KEY", "")

SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def get_sp500_tickers() -> list[str]:
    try:
        tables = pd.read_html(SP500_URL)
        tickers = tables[0]["Symbol"].tolist()
        return [t.replace(".", "-") for t in tickers]
    except Exception:
        # Hardcoded fallback — a representative liquid subset
        return [
            "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "TSLA",
            "BRK-B", "UNH", "LLY", "JPM", "V", "AVGO", "XOM", "MA", "HD", "PG",
            "COST", "JNJ", "MRK", "ABBV", "CVX", "CRM", "AMD", "NFLX", "TMO",
            "BAC", "ORCL", "ACN", "PEP", "KO", "LIN", "MCD", "CSCO", "ABT",
            "TXN", "WMT", "DHR", "NKE", "PM", "ADBE", "MS", "RTX", "INTC",
            "GS", "IBM", "AMGN", "HON", "CAT", "GE", "SPGI", "ISRG", "NOW",
            "BKNG", "VRTX", "SYK", "DE", "AXP", "PLD", "BLK", "GILD", "ADI",
            "MDLZ", "CI", "REGN", "CB", "CME", "ZTS", "LRCX", "PANW", "KLAC",
            "SO", "DUK", "MO", "F", "GM", "USB", "MMC", "ICE", "AON", "PNC",
        ]


def _rsi(closes: pd.Series, period: int = 14) -> float:
    delta = closes.diff().dropna()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean().iloc[-1]
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean().iloc[-1]
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _ema(closes: pd.Series, period: int) -> pd.Series:
    return closes.ewm(span=period, adjust=False).mean()


def _has_consolidation_base(closes: pd.Series, days: int = 10) -> bool:
    """True if price was in a tight range (<=8% swing) for the last `days` bars."""
    if len(closes) < days:
        return False
    window = closes.iloc[-days:]
    swing = (window.max() - window.min()) / window.min()
    return swing <= 0.08


def _is_same_day_base_breakout(hist: pd.DataFrame) -> bool:
    """
    True if today broke above a 10+ day consolidation base.
    Keep these even if other extended filters would remove them.
    """
    if len(hist) < 12:
        return False
    closes = hist["Close"]
    # Check if today is a new 20-day high
    is_breakout = closes.iloc[-1] > closes.iloc[-21:-1].max()
    if not is_breakout:
        return False
    # Check if prior 10 days were in a tight base
    return _has_consolidation_base(closes.iloc[:-1], days=10)


def _passes_extended_filter(hist: pd.DataFrame) -> tuple[bool, str]:
    """
    Returns (passes, reason). Removes overheated stocks.
    Exception: same-day breakout from a 10+ day base always passes.
    """
    closes = hist["Close"]
    today_open = hist["Open"].iloc[-1]
    today_close = closes.iloc[-1]
    prev_close = closes.iloc[-2]

    # Same-day breakout exception — always keep
    if _is_same_day_base_breakout(hist):
        return True, "base_breakout"

    # Up more than 10% today already
    pct_today = (today_close - prev_close) / prev_close
    if pct_today > 0.10:
        return False, f"up_{pct_today:.1%}_today"

    # Price more than 20% above 50-day EMA
    ema50 = _ema(closes, 50).iloc[-1]
    pct_above_ema = (today_close - ema50) / ema50
    if pct_above_ema > 0.20:
        return False, f"{pct_above_ema:.1%}_above_50EMA"

    # New 52-week high today with no consolidation in last 5 days
    is_52wk_high = today_close >= closes.rolling(252).max().iloc[-1]
    if is_52wk_high and not _has_consolidation_base(closes.iloc[:-1], days=5):
        return False, "52wk_high_no_base"

    return True, "ok"


def _score_stock(ticker: str, hist: pd.DataFrame) -> dict | None:
    """Score a single stock 0–100. Returns None if data is insufficient."""
    if len(hist) < 30:
        return None

    closes = hist["Close"]
    volumes = hist["Volume"]

    score = 0
    reasons = []

    # --- Volume spike (0–30 pts) ---
    avg_vol_20 = volumes.iloc[-21:-1].mean()
    today_vol = volumes.iloc[-1]
    vol_ratio = today_vol / avg_vol_20 if avg_vol_20 > 0 else 0
    if vol_ratio >= 3.0:
        score += 30
        reasons.append(f"vol {vol_ratio:.1f}x")
    elif vol_ratio >= 2.0:
        score += 20
        reasons.append(f"vol {vol_ratio:.1f}x")
    elif vol_ratio >= 1.5:
        score += 10
        reasons.append(f"vol {vol_ratio:.1f}x")
    else:
        return None  # Volume gate: must have 1.5x+ spike

    # --- Price momentum (0–25 pts) ---
    ret_5d = (closes.iloc[-1] - closes.iloc[-6]) / closes.iloc[-6]
    if ret_5d > 0.05:
        score += 25
        reasons.append(f"5d+{ret_5d:.1%}")
    elif ret_5d > 0.02:
        score += 15
        reasons.append(f"5d+{ret_5d:.1%}")
    elif ret_5d > 0:
        score += 5

    # --- RSI cross (0–25 pts) ---
    rsi_now = _rsi(closes)
    rsi_prev = _rsi(closes.iloc[:-1])
    if 40 <= rsi_now <= 70:
        score += 15
        reasons.append(f"RSI {rsi_now:.0f}")
    if rsi_prev < 50 <= rsi_now:
        score += 10
        reasons.append("RSI cross 50")
    elif rsi_prev < 30 <= rsi_now:
        score += 10
        reasons.append("RSI cross 30")

    # --- EMA structure (0–20 pts) ---
    ema20 = _ema(closes, 20).iloc[-1]
    ema50 = _ema(closes, 50).iloc[-1]
    ema200 = _ema(closes, 200).iloc[-1]
    price = closes.iloc[-1]
    if price > ema20 > ema50 > ema200:
        score += 20
        reasons.append("bullish EMAs")
    elif price > ema50 > ema200:
        score += 12
        reasons.append("above 50/200")
    elif price > ema200:
        score += 6

    return {
        "ticker": ticker,
        "score": score,
        "price": round(float(closes.iloc[-1]), 2),
        "vol_ratio": round(vol_ratio, 2),
        "rsi": round(rsi_now, 1),
        "ret_5d": round(ret_5d * 100, 2),
        "reasons": ", ".join(reasons),
    }


def _fetch_news_tickers(tickers: list[str]) -> set[str]:
    """Ask Polygon for tickers with news in the last 24h."""
    if not POLYGON_API_KEY:
        return set()
    try:
        yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        url = (
            f"https://api.polygon.io/v2/reference/news"
            f"?published_utc.gte={yesterday}&limit=100&apiKey={POLYGON_API_KEY}"
        )
        resp = requests.get(url, timeout=10)
        data = resp.json().get("results", [])
        news_set = set()
        for article in data:
            for t in article.get("tickers", []):
                news_set.add(t)
        return news_set
    except Exception:
        return set()


def run_scan() -> list[dict]:
    """
    Full scan: fetch S&P 500, apply extended filter, score, return top 10.
    """
    tickers = get_sp500_tickers()
    news_tickers = _fetch_news_tickers(tickers)

    results = []
    # Batch download all tickers — much faster than one by one
    try:
        raw = yf.download(
            tickers,
            period="1y",
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            threads=True,
            progress=False,
        )
    except Exception:
        return []

    for ticker in tickers:
        try:
            if len(tickers) == 1:
                hist = raw
            else:
                hist = raw[ticker].dropna()

            if hist is None or len(hist) < 30:
                continue

            passes, reason = _passes_extended_filter(hist)
            if not passes:
                continue

            scored = _score_stock(ticker, hist)
            if scored is None:
                continue

            # Bonus for news hit
            if ticker in news_tickers:
                scored["score"] += 5
                scored["reasons"] += ", news"

            results.append(scored)
        except Exception:
            continue

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:10]
