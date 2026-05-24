"""
main.py — Orchestrates the core loop.

Loop: every 10 minutes during market hours
  1. scanner.py   → top 10 S&P 500 candidates
  2. analysis.py  → Claude decides SIGNAL / WATCHLIST / SKIP
  3. discord_bot  → post signals and watchlist items
  4. memory.py    → write Obsidian notes
"""

import time
import os
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

import scanner
import analysis
import discord_bot
import memory

SCAN_INTERVAL_MINUTES = 10
MARKET_OPEN_ET = 9   # 9:30 ET — approximate, good enough
MARKET_CLOSE_ET = 16  # 4:00 PM ET


def _is_market_hours() -> bool:
    """Rough check — NYSE market hours in US/Eastern."""
    now_utc = datetime.now(timezone.utc)
    # ET = UTC-5 (EST) or UTC-4 (EDT) — use UTC-4 as approximation
    now_et = now_utc - timedelta(hours=4)
    if now_et.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    return MARKET_OPEN_ET <= now_et.hour < MARKET_CLOSE_ET


def run_once():
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Starting scan...")

    candidates = scanner.run_scan()
    if not candidates:
        print("  No candidates passed filters.")
        return

    print(f"  {len(candidates)} candidates found: {[c['ticker'] for c in candidates]}")

    decisions = analysis.analyze(candidates)
    if not decisions:
        print("  Analysis returned no decisions.")
        return

    signals = [d for d in decisions if d.get("decision") == "SIGNAL"]
    watchlist = [d for d in decisions if d.get("decision") == "WATCHLIST"]

    print(f"  Signals: {[d['ticker'] for d in signals]}")
    print(f"  Watchlist: {[d['ticker'] for d in watchlist]}")

    # Post to Discord
    discord_bot.post(decisions, candidates)

    # Write Obsidian notes
    scan_map = {c["ticker"]: c for c in candidates}
    for d in signals:
        memory.write_signal(d, scan_map.get(d["ticker"], {}))
    for d in watchlist:
        memory.write_watchlist(d, scan_map.get(d["ticker"], {}))


def main():
    print("Trading Bot starting...")
    print(f"Scan interval: every {SCAN_INTERVAL_MINUTES} minutes during market hours\n")

    while True:
        if _is_market_hours():
            try:
                run_once()
            except Exception as e:
                print(f"[ERROR] {e}")
        else:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Market closed — sleeping...")

        time.sleep(SCAN_INTERVAL_MINUTES * 60)


if __name__ == "__main__":
    main()
