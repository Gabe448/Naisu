"""
Historical OHLCV loader for backtesting.

Sources (in priority order):
  1. Polygon.io  — _fetch_bars() per ticker, full date range
  2. yfinance    — batch download, chunked by 200 tickers

Caches everything to backtest/cache/{ticker}_{timeframe}.parquet.
Cache is reused if it covers the requested date range and is <24h old.

Hourly yfinance limit: only 60 days per call.
For longer ranges (e.g. 6 months), we chunk into 60-day windows and merge.
"""
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

CACHE_DIR = Path(__file__).parent / "cache"
CACHE_TTL_HOURS = 24


def load_history(
    tickers: list[str],
    start: datetime,
    end: datetime,
    timeframe: str = "1d",
    progress: bool = True,
) -> dict[str, pd.DataFrame]:
    """
    Load OHLCV history for a list of tickers.

    timeframe: "1d" | "1h"
    Returns: {ticker: DataFrame(open, high, low, close, volume) with DatetimeIndex}

    Tries Polygon first (one ticker at a time, stops on rate limit).
    Falls back to yfinance batch download for anything not covered.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    result: dict[str, pd.DataFrame] = {}
    uncached: list[str] = []

    for ticker in tickers:
        df = _load_from_cache(ticker, timeframe, start, end)
        if df is not None:
            result[ticker] = df
        else:
            uncached.append(ticker)

    if not uncached:
        return result

    if progress:
        print(f"[data_loader] Fetching {len(uncached)} tickers ({timeframe}) from {start.date()} to {end.date()}")

    # Try Polygon first
    polygon_result = _fetch_polygon(uncached, start, end, timeframe, progress)

    # yfinance for anything Polygon missed or errored
    missing = [t for t in uncached if t not in polygon_result]
    if missing:
        if progress:
            print(f"[data_loader] Polygon missed {len(missing)} tickers — falling back to yfinance")
        yf_result = _fetch_yfinance(missing, start, end, timeframe, progress)
        polygon_result.update(yf_result)

    for ticker, df in polygon_result.items():
        if not df.empty:
            _save_to_cache(ticker, timeframe, df)
            result[ticker] = df

    return result


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _cache_path(ticker: str, timeframe: str) -> Path:
    return CACHE_DIR / f"{ticker}_{timeframe}.parquet"


def _load_from_cache(
    ticker: str,
    timeframe: str,
    start: datetime,
    end: datetime,
) -> Optional[pd.DataFrame]:
    path = _cache_path(ticker, timeframe)
    if not path.exists():
        return None

    age_hours = (datetime.now().timestamp() - path.stat().st_mtime) / 3600
    if age_hours > CACHE_TTL_HOURS:
        return None

    try:
        df = pd.read_parquet(path)
        df.index = pd.to_datetime(df.index).tz_localize(None)

        # Accept if cached range covers [start, end - 3 days] (market closures allowed)
        covers_start = df.index.min() <= pd.Timestamp(start) + timedelta(days=5)
        covers_end   = df.index.max() >= pd.Timestamp(end)   - timedelta(days=5)
        if covers_start and covers_end:
            return df
    except Exception:
        pass
    return None


def _save_to_cache(ticker: str, timeframe: str, df: pd.DataFrame) -> None:
    try:
        df.to_parquet(_cache_path(ticker, timeframe))
    except Exception as e:
        print(f"[data_loader] cache write failed {ticker}: {e}")


# ── Polygon fetcher ───────────────────────────────────────────────────────────

def _fetch_polygon(
    tickers: list[str],
    start: datetime,
    end: datetime,
    timeframe: str,
    progress: bool,
) -> dict[str, pd.DataFrame]:
    result: dict[str, pd.DataFrame] = {}
    try:
        from data.polygon_client import _fetch_bars
        from data.api_errors import RateLimitError

        from_str = start.strftime("%Y-%m-%d")
        to_str   = end.strftime("%Y-%m-%d")
        mult, span = (1, "day") if timeframe == "1d" else (1, "hour")

        for i, ticker in enumerate(tickers):
            try:
                df = _fetch_bars(ticker, mult, span, from_str, to_str)
                if not df.empty:
                    df = _normalize_df(df)
                    result[ticker] = df
                    if progress and (i + 1) % 50 == 0:
                        print(f"[data_loader] Polygon: {i+1}/{len(tickers)}")
            except RateLimitError:
                print(f"[data_loader] Polygon rate limit at ticker {ticker} ({i+1}/{len(tickers)}) — switching to yfinance")
                break
            except Exception:
                continue  # per-ticker error, skip

    except ImportError:
        pass  # polygon not installed
    except Exception as e:
        print(f"[data_loader] Polygon fetch error: {e}")

    return result


# ── yfinance fetcher ──────────────────────────────────────────────────────────

def _fetch_yfinance(
    tickers: list[str],
    start: datetime,
    end: datetime,
    timeframe: str,
    progress: bool,
) -> dict[str, pd.DataFrame]:
    import yfinance as yf

    interval = "1d" if timeframe == "1d" else "1h"

    # yfinance hourly limit: 60 days per call. Chunk into 60-day windows for longer ranges.
    if timeframe == "1h":
        return _fetch_yfinance_hourly_chunked(tickers, start, end, progress)

    # Daily: single download covers any range
    result: dict[str, pd.DataFrame] = {}
    chunk_size = 200
    chunks = [tickers[i:i + chunk_size] for i in range(0, len(tickers), chunk_size)]

    for ci, chunk in enumerate(chunks):
        try:
            raw = yf.download(
                tickers=chunk,
                start=start.strftime("%Y-%m-%d"),
                end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),  # end is exclusive in yf
                interval=interval,
                group_by="ticker",
                auto_adjust=True,
                progress=False,
                threads=True,
            )
            if raw.empty:
                continue

            for ticker in chunk:
                try:
                    df = _extract_yf_ticker(raw, ticker, chunk)
                    if df is not None and not df.empty:
                        result[ticker] = df
                except Exception:
                    continue

            if progress:
                print(f"[data_loader] yfinance daily chunk {ci+1}/{len(chunks)} done ({len(result)} total)")
        except Exception as e:
            print(f"[data_loader] yfinance chunk {ci+1} error: {e}")

    return result


def _fetch_yfinance_hourly_chunked(
    tickers: list[str],
    start: datetime,
    end: datetime,
    progress: bool,
) -> dict[str, pd.DataFrame]:
    """
    yfinance hourly data: max 60 days per request.
    Chunk the date range into 60-day windows, fetch each, then merge.
    """
    import yfinance as yf

    # Build list of 60-day windows
    windows: list[tuple[datetime, datetime]] = []
    cur = start
    while cur < end:
        win_end = min(cur + timedelta(days=59), end)
        windows.append((cur, win_end))
        cur = win_end + timedelta(days=1)

    # Per ticker: merge across all windows
    all_frames: dict[str, list[pd.DataFrame]] = {t: [] for t in tickers}

    chunk_size = 100  # smaller chunks for hourly (more data per ticker)
    chunks = [tickers[i:i + chunk_size] for i in range(0, len(tickers), chunk_size)]

    for wi, (w_start, w_end) in enumerate(windows):
        for ci, chunk in enumerate(chunks):
            try:
                raw = yf.download(
                    tickers=chunk,
                    start=w_start.strftime("%Y-%m-%d"),
                    end=(w_end + timedelta(days=1)).strftime("%Y-%m-%d"),
                    interval="1h",
                    group_by="ticker",
                    auto_adjust=True,
                    progress=False,
                    threads=True,
                )
                if raw.empty:
                    continue

                for ticker in chunk:
                    try:
                        df = _extract_yf_ticker(raw, ticker, chunk)
                        if df is not None and not df.empty:
                            all_frames[ticker].append(df)
                    except Exception:
                        continue
            except Exception as e:
                print(f"[data_loader] yfinance hourly window {wi+1}/{len(windows)} chunk {ci+1} error: {e}")

        if progress:
            print(f"[data_loader] yfinance hourly window {wi+1}/{len(windows)}: {w_start.date()} → {w_end.date()}")

    # Merge frames per ticker
    result: dict[str, pd.DataFrame] = {}
    for ticker, frames in all_frames.items():
        if frames:
            merged = pd.concat(frames).sort_index()
            merged = merged[~merged.index.duplicated(keep="last")]
            result[ticker] = merged

    return result


# ── Normalization ─────────────────────────────────────────────────────────────

def _extract_yf_ticker(
    raw: pd.DataFrame,
    ticker: str,
    chunk: list[str],
) -> Optional[pd.DataFrame]:
    if len(chunk) == 1:
        df = raw.copy()
    else:
        lvl0 = raw.columns.get_level_values(0)
        if ticker not in lvl0:
            return None
        df = raw[ticker].copy()

    df = df.dropna(subset=["Close"])
    if df.empty:
        return None
    return _normalize_df(df)


def _normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    """Lowercase columns, strip timezone, keep OHLCV only."""
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]
    df.index = pd.to_datetime(df.index)
    if df.index.tzinfo is not None:
        df.index = df.index.tz_localize(None)
    keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
    return df[keep].sort_index()


# ── Convenience: resample 1h → 4h ────────────────────────────────────────────

def resample_4h(hourly_df: pd.DataFrame) -> pd.DataFrame:
    """Resample 1H bars to 4H. Matches yf_client._get_4h_bars() logic."""
    if hourly_df.empty:
        return hourly_df
    return hourly_df.resample("4h").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna()


# ── Slice helpers for no-lookahead replay ─────────────────────────────────────

def slice_daily(daily_df: pd.DataFrame, as_of: datetime, lookback_days: int = 90) -> pd.DataFrame:
    """
    Return daily bars strictly up to (and including) as_of.
    Limits to lookback_days to match live scanner behaviour.
    """
    cutoff = pd.Timestamp(as_of)
    start  = cutoff - timedelta(days=lookback_days)
    mask   = (daily_df.index <= cutoff) & (daily_df.index >= start)
    return daily_df[mask].copy()


def slice_hourly(hourly_df: pd.DataFrame, as_of: datetime, lookback_days: int = 60) -> pd.DataFrame:
    """Return hourly bars strictly up to as_of, limited to lookback_days."""
    cutoff = pd.Timestamp(as_of)
    start  = cutoff - timedelta(days=lookback_days)
    mask   = (hourly_df.index <= cutoff) & (hourly_df.index >= start)
    return hourly_df[mask].copy()
