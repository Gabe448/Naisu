"""
Simulated position tracker for backtesting.

Separate from live positions.json — writes to backtest/results/positions_<run_id>.json.

Position lifecycle:
  pending  → entry_filled  → stopped_out | target_hit | time_exit | manually_closed

Entry logic:
  - Entry fills at next trading day's OPEN price (realistic: signal posted EOD)
  - If next day opens beyond stop (gap through), position is NOT opened (skipped)

Exit logic (checked each day the position is open):
  Long:   stop_hit if day LOW  <= stop_price
          target_hit if day HIGH >= target_price
  Short:  stop_hit if day HIGH >= stop_price
          target_hit if day LOW  <= target_price

  When both stop and target are hit on the same day:
    - Use whichever is closer to the open (conservative: assume stop hit first)

Max hold: 10 trading days → force close at that day's close price.
"""
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

RESULTS_DIR = Path(__file__).parent / "results"


def _run_store_path(run_id: str) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    return RESULTS_DIR / f"positions_{run_id}.json"


def _load(run_id: str) -> dict:
    path = _run_store_path(run_id)
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"open": [], "closed": [], "pending": [], "skipped": []}


def _save(run_id: str, data: dict) -> None:
    with open(_run_store_path(run_id), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)


# ── Position creation ─────────────────────────────────────────────────────────

def queue_signal(
    run_id:       str,
    ticker:       str,
    direction:    str,
    signal_date:  datetime,
    stop_price:   float,
    target_price: float,
    conviction:   int,
    thesis:       str,
    claude_json:  dict,
) -> dict:
    """
    Record a trade signal. Status = pending until filled next open.
    """
    position = {
        "id":             str(uuid.uuid4())[:8],
        "ticker":         ticker,
        "direction":      direction,
        "signal_date":    signal_date.isoformat(),
        "entry_date":     None,
        "entry_price":    None,
        "stop_price":     stop_price,
        "target_price":   target_price,
        "exit_price":     None,
        "exit_date":      None,
        "status":         "pending",
        "days_held":      0,
        "conviction":     conviction,
        "thesis":         thesis,
        "final_pnl_pct":  None,
        "setup_types":    claude_json.get("setup_type", []),
        "stop_basis":     claude_json.get("stop_basis", ""),
        "target_basis":   claude_json.get("target_basis", ""),
        "learnings_ref":  claude_json.get("learnings_referenced", ""),
        "claude_json":    claude_json,
    }
    data = _load(run_id)
    data["pending"].append(position)
    _save(run_id, data)
    return position


def fill_pending(
    run_id:     str,
    trade_date: datetime,
    daily_bars: dict[str, "pd.DataFrame"],  # {ticker: df} for that date's opens
) -> tuple[list[dict], list[dict]]:
    """
    Try to fill all pending positions at today's open price.
    Returns (filled_list, skipped_list).

    Skipped if:
      - No bar data for that date
      - Open gaps through the stop (long: open < stop, short: open > stop)
    """
    import pandas as pd

    data = _load(run_id)
    filled, skipped = [], []

    still_pending = []
    for pos in data["pending"]:
        ticker = pos["ticker"]
        df = daily_bars.get(ticker)

        if df is None or df.empty:
            still_pending.append(pos)
            continue

        # Find the bar on or after trade_date
        trade_ts = pd.Timestamp(trade_date)
        future   = df[df.index >= trade_ts]
        if future.empty:
            still_pending.append(pos)
            continue

        entry_bar   = future.iloc[0]
        entry_price = float(entry_bar["open"])
        bar_date    = future.index[0]

        # Gap-through-stop check
        if pos["direction"] == "long" and entry_price <= pos["stop_price"]:
            pos["status"]     = "gap_skipped"
            pos["exit_date"]  = bar_date.isoformat()
            pos["exit_price"] = entry_price
            data["skipped"].append(pos)
            skipped.append(pos)
            continue

        if pos["direction"] == "short" and entry_price >= pos["stop_price"]:
            pos["status"]     = "gap_skipped"
            pos["exit_date"]  = bar_date.isoformat()
            pos["exit_price"] = entry_price
            data["skipped"].append(pos)
            skipped.append(pos)
            continue

        pos["entry_price"] = entry_price
        pos["entry_date"]  = bar_date.isoformat()
        pos["status"]      = "open"
        data["open"].append(pos)
        filled.append(pos)

    data["pending"] = still_pending
    _save(run_id, data)
    return filled, skipped


def advance_day(
    run_id:         str,
    trade_date:     datetime,
    daily_bars:     dict[str, "pd.DataFrame"],
    max_hold_days:  int = 10,
) -> list[dict]:
    """
    For all open positions, check if stop or target was hit on trade_date.
    Also handles max-hold time exit.

    Returns list of positions that were closed on this day.
    """
    import pandas as pd

    data       = _load(run_id)
    trade_ts   = pd.Timestamp(trade_date)
    closed_now = []
    still_open = []

    for pos in data["open"]:
        ticker = pos["ticker"]
        df     = daily_bars.get(ticker)

        if df is None or df.empty:
            still_open.append(pos)
            continue

        bar_row = df[df.index.date == trade_date.date()]
        if bar_row.empty:
            still_open.append(pos)
            continue

        bar         = bar_row.iloc[0]
        day_open    = float(bar["open"])
        day_high    = float(bar["high"])
        day_low     = float(bar["low"])
        day_close   = float(bar["close"])

        entry_dt    = datetime.fromisoformat(pos["entry_date"])
        days_held   = (trade_date - entry_dt).days
        stop        = pos["stop_price"]
        target      = pos["target_price"]
        direction   = pos["direction"]

        close_reason  = None
        close_price   = None

        if direction == "long":
            stop_hit   = day_low  <= stop
            target_hit = day_high >= target

            if stop_hit and target_hit:
                # Both hit — conservative: stop wins (assume worst case)
                close_reason = "stopped_out"
                close_price  = stop
            elif stop_hit:
                close_reason = "stopped_out"
                close_price  = stop
            elif target_hit:
                close_reason = "target_hit"
                close_price  = target

        else:  # short
            stop_hit   = day_high >= stop
            target_hit = day_low  <= target

            if stop_hit and target_hit:
                close_reason = "stopped_out"
                close_price  = stop
            elif stop_hit:
                close_reason = "stopped_out"
                close_price  = stop
            elif target_hit:
                close_reason = "target_hit"
                close_price  = target

        # Time exit
        if close_reason is None and days_held >= max_hold_days:
            close_reason = "time_exit"
            close_price  = day_close

        if close_reason:
            pos = _close_position(pos, close_reason, close_price, trade_date, days_held)
            data["closed"].append(pos)
            closed_now.append(pos)
        else:
            pos["days_held"] = days_held
            still_open.append(pos)

    data["open"] = still_open
    _save(run_id, data)
    return closed_now


def _close_position(
    pos:          dict,
    reason:       str,
    exit_price:   float,
    exit_date:    datetime,
    days_held:    int,
) -> dict:
    entry = pos["entry_price"]
    if pos["direction"] == "long":
        pnl = round((exit_price - entry) / entry * 100, 2)
    else:
        pnl = round((entry - exit_price) / entry * 100, 2)

    pos["status"]        = reason
    pos["exit_price"]    = exit_price
    pos["exit_date"]     = exit_date.isoformat()
    pos["days_held"]     = days_held
    pos["final_pnl_pct"] = pnl
    return pos


# ── Read helpers ──────────────────────────────────────────────────────────────

def get_open_positions(run_id: str) -> list[dict]:
    return _load(run_id)["open"]


def get_closed_positions(run_id: str) -> list[dict]:
    return _load(run_id)["closed"]


def get_all_positions(run_id: str) -> dict:
    return _load(run_id)


def summary_stats(run_id: str) -> dict:
    """Compute win rate, avg PnL, expectancy."""
    closed = get_closed_positions(run_id)
    if not closed:
        return {"total": 0}

    wins   = [p for p in closed if (p.get("final_pnl_pct") or 0) > 0]
    losses = [p for p in closed if (p.get("final_pnl_pct") or 0) <= 0]

    avg_win  = sum(p["final_pnl_pct"] for p in wins)   / len(wins)   if wins   else 0.0
    avg_loss = sum(p["final_pnl_pct"] for p in losses) / len(losses) if losses else 0.0
    win_rate = len(wins) / len(closed)
    exp      = round(win_rate * avg_win + (1 - win_rate) * avg_loss, 2)

    return {
        "total":      len(closed),
        "wins":       len(wins),
        "losses":     len(losses),
        "win_rate":   round(win_rate * 100, 1),
        "avg_win":    round(avg_win, 2),
        "avg_loss":   round(avg_loss, 2),
        "expectancy": exp,
        "total_pnl":  round(sum(p["final_pnl_pct"] for p in closed), 2),
    }
