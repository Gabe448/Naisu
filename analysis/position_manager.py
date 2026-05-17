"""
Position manager — monitors open trades, fires alerts when stop or target is hit.

Runs every 10 minutes alongside the market scan.
Does NOT auto-execute trades. Paper trading only until you flip the switch.
"""
from data.yf_client import get_price as _get_price
from memory.position_store import (
    get_open_positions,
    update_position_price,
    close_position,
    add_thesis_update,
)


STOP_BUFFER_PCT  = 0.001   # 0.1% buffer — avoids alerting on single tick through stop
TARGET_BUFFER_PCT = 0.001


def check_all_positions() -> list[dict]:
    """
    Check every open position against current price.
    Returns list of alert dicts for any that hit stop or target.
    """
    positions = get_open_positions()
    alerts = []

    for pos in positions:
        ticker = pos["ticker"]
        try:
            current = _get_price(ticker)
            if not current:
                continue

            updated = update_position_price(pos["id"], current)
            if not updated:
                continue

            alert = _check_levels(updated, current)
            if alert:
                alerts.append(alert)

        except Exception as e:
            print(f"[position_manager] {ticker} error: {e}")

    return alerts


def _check_levels(pos: dict, price: float) -> dict | None:
    direction = pos["direction"]
    stop      = pos["stop_price"]
    target    = pos["target_price"]
    pid       = pos["id"]
    ticker    = pos["ticker"]

    if direction == "long":
        if price <= stop * (1 + STOP_BUFFER_PCT):
            closed = close_position(pid, "stopped_out", price)
            return _alert(ticker, "STOPPED OUT", price, closed)

        if price >= target * (1 - TARGET_BUFFER_PCT):
            closed = close_position(pid, "target_hit", price)
            return _alert(ticker, "TARGET HIT", price, closed)

    elif direction == "short":
        if price >= stop * (1 - STOP_BUFFER_PCT):
            closed = close_position(pid, "stopped_out", price)
            return _alert(ticker, "STOPPED OUT", price, closed)

        if price <= target * (1 + TARGET_BUFFER_PCT):
            closed = close_position(pid, "target_hit", price)
            return _alert(ticker, "TARGET HIT", price, closed)

    return None


def _alert(ticker: str, event: str, price: float, closed_pos: dict | None) -> dict:
    pos = closed_pos or {}
    return {
        "type":       event,
        "ticker":     ticker,
        "price":      price,
        "pnl_pct":    pos.get("final_pnl_pct", 0),
        "direction":  pos.get("direction", "?"),
        "entry":      pos.get("entry_price", 0),
        "stop":       pos.get("stop_price", 0),
        "target":     pos.get("target_price", 0),
        "days_held":  pos.get("days_held", 0),
        "position_id": pos.get("id", "?"),
    }


def get_position_summary() -> list[dict]:
    """Snapshot of all open positions with current PnL — for morning brief."""
    positions = get_open_positions()
    summaries = []

    for pos in positions:
        ticker = pos["ticker"]
        try:
            current = _get_price(ticker) or pos["entry_price"]
            updated = update_position_price(pos["id"], current)
            if updated:
                summaries.append(updated)
        except Exception as e:
            summaries.append(pos)
            print(f"[position_manager] {ticker} error: {e}")

    return summaries
