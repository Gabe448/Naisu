"""
S&P 500 ticker list — fetched from Wikipedia, cached locally for 24 hours.

yfinance quirk: tickers with dots (BRK.B) must be dashes (BRK-B).
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

_CACHE_PATH = Path("data/sp500_tickers.json")
_CACHE_TTL  = timedelta(hours=24)


def get_sp500_tickers() -> list[str]:
    """Return S&P 500 tickers, using cache if fresh, else re-fetch."""
    if _cache_is_fresh():
        return _load_cache()
    return _fetch_and_cache()


def _cache_is_fresh() -> bool:
    if not _CACHE_PATH.exists():
        return False
    mtime = datetime.fromtimestamp(_CACHE_PATH.stat().st_mtime)
    return datetime.now() - mtime < _CACHE_TTL


def _load_cache() -> list[str]:
    with open(_CACHE_PATH, "r") as f:
        return json.load(f)


def _fetch_and_cache() -> list[str]:
    try:
        tables = pd.read_html(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            attrs={"id": "constituents"},
        )
        tickers = tables[0]["Symbol"].tolist()
        # Fix dot notation for yfinance
        tickers = [t.replace(".", "-") for t in tickers]
        tickers = sorted(set(tickers))

        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_CACHE_PATH, "w") as f:
            json.dump(tickers, f)

        print(f"[sp500] Fetched {len(tickers)} tickers from Wikipedia.")
        return tickers

    except Exception as e:
        print(f"[sp500] Wikipedia fetch failed ({e}), using fallback list.")
        return _FALLBACK


# Fallback: major S&P components in case Wikipedia is unreachable
_FALLBACK = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "BRK-B", "LLY",
    "AVGO", "JPM", "TSLA", "UNH", "XOM", "V", "MA", "JNJ", "PG", "MRK", "HD",
    "ABBV", "CVX", "COST", "ORCL", "BAC", "KO", "WMT", "PEP", "MCD", "CRM",
    "TMO", "ACN", "ABT", "CSCO", "LIN", "DHR", "AMD", "TXN", "NEE", "PM",
    "NKE", "AMGN", "QCOM", "UPS", "LOW", "MS", "INTC", "INTU", "GS", "HON",
    "ELV", "SPGI", "RTX", "BLK", "T", "DE", "SYK", "PLD", "CI", "AMAT",
    "MDLZ", "ADI", "REGN", "SCHW", "ISRG", "GILD", "AXP", "MMC", "VRTX",
    "CB", "ZTS", "MO", "AMT", "DUK", "SO", "BSX", "CME", "EOG", "SLB",
    "COP", "WM", "PH", "NOC", "GD", "ITW", "APD", "MCO", "USB", "ETN",
    "SPY", "QQQ", "IWM",
]
