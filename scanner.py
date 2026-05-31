"""
scanner.py — S&P 500 scanner, runs every 10 minutes.
Scores stocks on volume spike, momentum, RSI, and news.
Filters out extended/overheated names before scoring.
Returns top 10 candidates for Claude analysis.

Data source: Polygon.io (replaces yfinance — no cookie/crumb issues on servers).
  Step 1: Snapshot endpoint  → current price + volume for all S&P 500 (1–2 API calls)
  Step 2: Volume pre-filter  → only 1.5x+ spike tickers proceed (~20–40 tickers)
  Step 3: Aggs endpoint      → 1 year of daily bars per candidate (1 call each)
"""

import pandas as pd
import requests
import os
import time
from datetime import datetime, timedelta

POLYGON_API_KEY = os.getenv("POLYGON_API_KEY", "")
POLYGON_BASE = "https://api.polygon.io"

SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

# Polygon free tier: 5 req/min. Paid Starter+: unlimited.
# 0.15s between history calls = safe on both tiers.
_HISTORY_CALL_DELAY = 0.15


# ---------------------------------------------------------------------------
# Ticker helpers
# ---------------------------------------------------------------------------

def get_sp500_tickers() -> list[str]:
    """Fetch S&P 500 symbols from Wikipedia. Falls back to a liquid subset."""
    try:
        tables = pd.read_html(SP500_URL)
        tickers = tables[0]["Symbol"].tolist()
        # Wikipedia uses dots (BRK.B). Convert to Polygon format (BRK/B).
        return [t.replace(".", "/") for t in tickers]
    except Exception:
        return [
            "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "TSLA",
            "UNH", "LLY", "JPM", "V", "AVGO", "XOM", "MA", "HD", "PG",
            "COST", "JNJ", "MRK", "ABBV", "CVX", "CRM", "AMD", "NFLX", "TMO",
            "BAC", "ORCL", "ACN", "PEP", "KO", "LIN", "MCD", "CSCO", "ABT",
            "TXN", "WMT", "DHR", "NKE", "PM", "ADBE", "MS", "RTX", "INTC",
            "GS", "IBM", "AMGN", "HON", "CAT", "GE", "SPGI", "ISRG", "NOW",
            "BKNG", "VRTX", "SYK", "DE", "AXP", "PLD", "BLK", "GILD", "ADI",
            "MDLZ", "CI", "REGN", "CB", "CME", "ZTS", "LRCX", "PANW", "KLAC",
            "SO", "DUK", "MO", "F", "GM", "USB", "MMC", "ICE", "AON", "PNC",
        ]


# ---------------------------------------------------------------------------
# Polygon data fetchers
# ---------------------------------------------------------------------------

def _polygon_get(url: str, params: dict) -> dict:
    """GET wrapper with error logging."""
    params["apiKey"] = POLYGON_API_KEY
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[scanner] Polygon request failed: {e}")
        return {}


def _fetch_snapshot(tickers: list[str]) -> dict[str, dict]:
    """
    Fetch today's snapshot for all tickers in batches of 250.
    Returns {ticker: {price, vol, prev_vol, vol_ratio, pct_today}}.
    """
    result = {}
    batch_size = 250

    for i in range(0, len(tickers), batch_size):
        batch = tickers[i : i + batch_size]
        data = _polygon_get(
            f"{POLYGON_BASE}/v2/snapshot/locale/us/markets/stocks/tickers",
            {"tickers": ",".join(batch)},
        )
        for item in data.get("tickers", []):
            ticker = item.get("ticker", "")
            day = item.get("day", {})
            prev = item.get("prevDay", {})

            today_vol = day.get("v", 0) or 0
            prev_vol = prev.get("v", 0) or 0
            today_close = day.get("c", 0) or 0
            prev_close = prev.get("c", 0) or 0

            if prev_vol <= 0 or today_close <= 0:
                continue

            vol_ratio = today_vol / prev_vol
            pct_today = (today_close - prev_close) / prev_close if prev_close else 0

            result[ticker] = {
                "price": today_close,
                "vol": today_vol,
                "prev_vol": prev_vol,
                "vol_ratio": vol_ratio,
                "pct_today": pct_today,
            }

    return result


def _fetch_history(ticker: str, days: int = 365) -> pd.DataFrame | None:
    """
    Fetch `days` of daily OHLCV bars from Polygon for a single ticker.
    Returns a DataFrame with columns: Open, High, Low, Close, Volume.
    """
    to_date = datetime.utcnow().strftime("%Y-%m-%d")
    from_date = (datetime.utcnow() - timedelta(days=days + 30)).strftime("%Y-%m-%d")

    data = _polygon_get(
        f"{POLYGON_BASE}/v2/aggs/ticker/{ticker}/range/1/day/{from_date}/{to_date}",
        {"adjusted": "true", "sort": "asc", "limit": 400},
    )

    bars = data.get("results", [])
    if not bars or len(bars) < 30:
        return None

    df = pd.DataFrame(bars)
    df["date"] = pd.to_datetime(df["t"], unit="ms")
    df = df.set_index("date")
    df = df.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"})
    return df[["Open", "High", "Low", "Close", "Volume"]]


def _fetch_news_tickers(tickers: list[str]) -> set[str]:
    """Return set of tickers that have Polygon news in the last 24h."""
    if not POLYGON_API_KEY:
        return set()
    yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = _polygon_get(
        f"{POLYGON_BASE}/v2/reference/news",
        {"published_utc.gte": yesterday, "limit": 100},
    )
    news_set: set[str] = set()
    for article in data.get("results", []):
        for t in article.get("tickers", []):
            news_set.add(t)
    return news_set


# ---------------------------------------------------------------------------
# Math helpers (pure pandas — no data-source dependency)
# ---------------------------------------------------------------------------

def _rsi(closes: pd.Series, period: int = 14) -> float:
    delta = closes.diff().dropna()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean().iloc[-1]
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean().iloc[-1]
    if avg_loss == 0:
        return 100.0
    return 100 - (100 / (1 + avg_gain / avg_loss))


def _ema(closes: pd.Series, period: int) -> pd.Series:
    return closes.ewm(span=period, adjust=False).mean()


def _has_consolidation_base(closes: pd.Series, days: int = 10) -> bool:
    """True if price stayed within an 8% swing over the last `days` bars."""
    if len(closes) < days:
        return False
    window = closes.iloc[-days:]
    return (window.max() - window.min()) / window.min() <= 0.08


def _is_same_day_base_breakout(hist: pd.DataFrame) -> bool:
    """True if today broke out of a 10+ day tight consolidation base."""
    if len(hist) < 12:
        return False
    closes = hist["Close"]
    is_breakout = closes.iloc[-1] > closes.iloc[-21:-1].max()
    if not is_breakout:
        return False
    return _has_consolidation_base(closes.iloc[:-1], days=10)


# ---------------------------------------------------------------------------
# Filter & scoring
# ---------------------------------------------------------------------------

def _passes_extended_filter(hist: pd.DataFrame) -> tuple[bool, str]:
    """
    Returns (passes, reason). Drops overheated/extended stocks.
    Exception: same-day breakout from a 10+ day base always passes.
    """
    closes = hist["Close"]
    today_close = closes.iloc[-1]
    prev_close = closes.iloc[-2]

    # Base-breakout exception — keep unconditionally
    if _is_same_day_base_breakout(hist):
        return True, "base_breakout"

    # Up > 10% today
    pct_today = (today_close - prev_close) / prev_close
    if pct_today > 0.10:
        return False, f"up_{pct_today:.1%}_today"

    # > 20% above 50-day EMA
    ema50 = _ema(closes, 50).iloc[-1]
    pct_above_ema = (today_close - ema50) / ema50
    if pct_above_ema > 0.20:
        return False, f"{pct_above_ema:.1%}_above_50EMA"

    # New 52-week high with no 5-day consolidation
    is_52wk_high = today_close >= closes.rolling(252).max().iloc[-1]
    if is_52wk_high and not _has_consolidation_base(closes.iloc[:-1], days=5):
        return False, "52wk_high_no_base"

    return True, "ok"


def _score_stock(ticker: str, hist: pd.DataFrame) -> dict | None:
    """Score 0–100. Returns None if data insufficient or volume gate fails."""
    if len(hist) < 30:
        return None

    closes = hist["Close"]
    volumes = hist["Volume"]

    score = 0
    reasons = []

    # Volume spike (0–30 pts) — hard gate at 1.5x
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
        return None  # Volume gate

    # Price momentum (0–25 pts)
    ret_5d = (closes.iloc[-1] - closes.iloc[-6]) / closes.iloc[-6]
    if ret_5d > 0.05:
        score += 25
        reasons.append(f"5d+{ret_5d:.1%}")
    elif ret_5d > 0.02:
        score += 15
        reasons.append(f"5d+{ret_5d:.1%}")
    elif ret_5d > 0:
        score += 5

    # RSI cross (0–25 pts)
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

    # EMA structure (0–20 pts)
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


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_scan() -> list[dict]:
    """
    Full scan pipeline:
      1. Snapshot all S&P 500 tickers (1–2 API calls)
      2. Pre-filter by volume spike >= 1.5x (drops ~95% of names)
      3. Fetch full history for surviving candidates (1 call each)
      4. Apply extended filter + score
      5. Add news bonus
      6. Return top 10
    """
    if not POLYGON_API_KEY:
        print("[scanner] No POLYGON_API_KEY set — aborting scan")
        return []

    tickers = get_sp500_tickers()
    print(f"[scanner] {len(tickers)} tickers loaded")

    # Step 1 — snapshot (broad, cheap)
    snapshot = _fetch_snapshot(tickers)
    print(f"[scanner] Snapshot returned {len(snapshot)} tickers")

    # Step 2 — volume pre-filter
    volume_candidates = [
        t for t, s in snapshot.items() if s.get("vol_ratio", 0) >= 1.5
    ]
    print(f"[scanner] {len(volume_candidates)} tickers pass volume pre-filter (1.5x+)")

    # Step 3–4 — fetch history + filter + score
    news_tickers = _fetch_news_tickers(tickers)
    results = []

    for ticker in volume_candidates:
        try:
            hist = _fetch_history(ticker)
            time.sleep(_HISTORY_CALL_DELAY)  # respect rate limits

            if hist is None or len(hist) < 30:
                continue

            passes, _ = _passes_extended_filter(hist)
            if not passes:
                continue

            scored = _score_stock(ticker, hist)
            if scored is None:
                continue

            # Step 5 — news bonus
            if ticker in news_tickers:
                scored["score"] += 5
                scored["reasons"] += ", news"

            results.append(scored)
        except Exception as e:
            print(f"[scanner] Error processing {ticker}: {e}")
            continue

    results.sort(key=lambda x: x["score"], reverse=True)
    top10 = results[:10]
    print(f"[scanner] Top 10: {[r['ticker'] for r in top10]}")
    return top10
