"""
Manual_watchlist.py — tickers you want the bot to always monitor.

HOW TO USE:
  Add tickers to MANUAL_WATCHLIST below. Save the file.
  The bot picks them up automatically on next scan cycle.
  These run alongside the S&P 500 pipeline scan — if any pass
  signal guidelines, a setup gets posted to Discord.

  Format: plain uppercase strings. One per line for readability.
"""

MANUAL_WATCHLIST: list[str] = [
    "TSLA",
    "NVDA",
    "AAPL",
    "META",
    "AMZN",
]


def get_manual_watchlist() -> list[str]:
    """Return the manual watchlist, deduped and uppercased."""
    return list(dict.fromkeys(t.upper() for t in MANUAL_WATCHLIST if t.strip()))


def add_ticker(ticker: str) -> None:
    """Add a ticker at runtime (does not persist across restarts — edit the list above)."""
    t = ticker.upper()
    if t not in MANUAL_WATCHLIST:
        MANUAL_WATCHLIST.append(t)


def remove_ticker(ticker: str) -> None:
    """Remove a ticker at runtime."""
    t = ticker.upper()
    if t in MANUAL_WATCHLIST:
        MANUAL_WATCHLIST.remove(t)
