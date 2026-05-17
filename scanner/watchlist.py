"""
Watchlist — tickers the bot actively monitors.
Extend this to pull from a file, DB, or Discord command later.
"""

WATCHLIST: list[str] = [
    "SPY",
    "QQQ",
    "AAPL",
    "TSLA",
    "NVDA",
    "AMZN",
    "META",
]


def get_watchlist() -> list[str]:
    return WATCHLIST


def add_ticker(ticker: str) -> None:
    t = ticker.upper()
    if t not in WATCHLIST:
        WATCHLIST.append(t)


def remove_ticker(ticker: str) -> None:
    t = ticker.upper()
    if t in WATCHLIST:
        WATCHLIST.remove(t)
