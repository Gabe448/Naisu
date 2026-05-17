"""
Position store — persists open and closed trades to a local JSON file.
This is the bot's trade journal. Obsidian writer reads from this.

Position lifecycle:
  open → stopped_out | target_hit | manually_closed
"""
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional


def _store_path() -> Path:
    return Path(os.environ.get("POSITION_STORE_PATH", "positions.json"))


def _load() -> dict:
    path = _store_path()
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"open": [], "closed": []}


def _save(data: dict) -> None:
    with open(_store_path(), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)


# ── Public API ────────────────────────────────────────────────────────────────

def open_position(
    ticker:          str,
    direction:       str,   # "long" | "short"
    entry_price:     float,
    stop_price:      float,
    target_price:    float,
    conviction_score: int,
    thesis:          str,
    signal:          dict,
) -> dict:
    position = {
        "id":               str(uuid.uuid4())[:8],
        "ticker":           ticker,
        "direction":        direction,
        "entry_price":      entry_price,
        "stop_price":       stop_price,
        "target_price":     target_price,
        "conviction_score": conviction_score,
        "thesis":           thesis,
        "entry_date":       datetime.now().isoformat(),
        "status":           "open",
        "days_held":        0,
        "current_price":    entry_price,
        "pnl_pct":          0.0,
        "signal_snapshot":  signal,
        "thesis_updates":   [],
    }
    data = _load()
    data["open"].append(position)
    _save(data)
    return position


def get_open_positions() -> list[dict]:
    return _load()["open"]


def get_closed_positions() -> list[dict]:
    return _load()["closed"]


def update_position_price(position_id: str, current_price: float) -> Optional[dict]:
    """Update current price and PnL. Returns updated position."""
    data = _load()
    for pos in data["open"]:
        if pos["id"] == position_id:
            pos["current_price"] = current_price
            entry = pos["entry_price"]
            if pos["direction"] == "long":
                pos["pnl_pct"] = round((current_price - entry) / entry * 100, 2)
            else:
                pos["pnl_pct"] = round((entry - current_price) / entry * 100, 2)

            entry_dt = datetime.fromisoformat(pos["entry_date"])
            pos["days_held"] = (datetime.now() - entry_dt).days
            _save(data)
            return pos
    return None


def close_position(position_id: str, reason: str, exit_price: float) -> Optional[dict]:
    """Move position from open → closed with exit details."""
    data = _load()
    for i, pos in enumerate(data["open"]):
        if pos["id"] == position_id:
            pos["status"]     = reason   # "stopped_out" | "target_hit" | "manually_closed"
            pos["exit_price"] = exit_price
            pos["exit_date"]  = datetime.now().isoformat()
            entry = pos["entry_price"]
            if pos["direction"] == "long":
                pos["final_pnl_pct"] = round((exit_price - entry) / entry * 100, 2)
            else:
                pos["final_pnl_pct"] = round((entry - exit_price) / entry * 100, 2)
            data["closed"].append(pos)
            data["open"].pop(i)
            _save(data)
            return pos
    return None


def add_thesis_update(position_id: str, update: str) -> None:
    data = _load()
    for pos in data["open"]:
        if pos["id"] == position_id:
            pos["thesis_updates"].append({
                "time":   datetime.now().isoformat(),
                "update": update,
            })
            _save(data)
            return


def get_weekly_stats() -> dict:
    """Stats for closed trades — used by weekly review."""
    from datetime import timedelta
    closed = get_closed_positions()
    week_ago = datetime.now() - timedelta(days=7)

    recent = [
        p for p in closed
        if datetime.fromisoformat(p.get("exit_date", "2000-01-01")) >= week_ago
    ]

    if not recent:
        return {"trades": 0}

    wins  = [p for p in recent if p.get("final_pnl_pct", 0) > 0]
    losses = [p for p in recent if p.get("final_pnl_pct", 0) <= 0]

    avg_win    = sum(p["final_pnl_pct"] for p in wins)  / len(wins)  if wins   else 0
    avg_loss   = sum(p["final_pnl_pct"] for p in losses)/ len(losses) if losses else 0
    win_rate   = len(wins) / len(recent) * 100
    total_pnl  = sum(p.get("final_pnl_pct", 0) for p in recent)

    return {
        "trades":    len(recent),
        "wins":      len(wins),
        "losses":    len(losses),
        "win_rate":  round(win_rate, 1),
        "avg_win":   round(avg_win, 2),
        "avg_loss":  round(avg_loss, 2),
        "total_pnl": round(total_pnl, 2),
        "positions": recent,
    }
