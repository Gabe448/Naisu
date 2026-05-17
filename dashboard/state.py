"""
Dashboard state — reads/writes dashboard_state.json.

The bot writes to this file after every scan.
The Flask dashboard reads from it (no bot required to view data).
Live market data (SPY/QQQ/VIX) is fetched fresh on demand via yfinance.
"""
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf
import warnings
warnings.filterwarnings("ignore")

STATE_PATH = Path(os.environ.get("DASHBOARD_STATE_PATH", "dashboard_state.json"))
MAX_SIGNALS = 20


# ── Read/write state file ─────────────────────────────────────────────────────

def _load() -> dict:
    if STATE_PATH.exists():
        try:
            with open(STATE_PATH, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "bot_status": {},
        "watchlist_candidates": [],
        "recent_signals": [],
    }


def _save(data: dict) -> None:
    with open(STATE_PATH, "w") as f:
        json.dump(data, f, indent=2, default=str)


# ── Bot writes these ──────────────────────────────────────────────────────────

def update_scan_state(candidates: list[dict], signal_count: int, max_signals: int) -> None:
    """Called by bot after every pipeline run."""
    data = _load()
    data["bot_status"] = {
        "is_running":            True,
        "last_scan":             datetime.now().isoformat(),
        "next_scan_seconds":     600,
        "signal_count_this_week": signal_count,
        "max_signals_per_week":  max_signals,
    }
    data["watchlist_candidates"] = [
        {
            "ticker":       c["ticker"],
            "yf_score":     c["yf_score"],
            "price":        c.get("real_time_price") or c["yf_metrics"].get("current_price"),
            "rel_volume":   c["yf_metrics"].get("rel_volume"),
            "momentum_10d": c["yf_metrics"].get("momentum_10d"),
            "pct_vs_ma20":  c["yf_metrics"].get("pct_vs_ma20"),
            "pct_vs_ma50":  c["yf_metrics"].get("pct_vs_ma50"),
            "dist_52w_pct": c["yf_metrics"].get("dist_52w_pct"),
            "gex_regime":   c.get("gex", {}).get("gex_regime", "unknown"),
            "dex_bias":     c.get("flow_bias", {}).get("bias", "neutral"),
            "score_breakdown": c["yf_metrics"].get("score_breakdown", {}),
            "scanned_at":   datetime.now().isoformat(),
        }
        for c in candidates
    ]
    _save(data)


def add_signal_to_history(signal: dict) -> None:
    """Called by bot when a high-conviction signal is posted."""
    data = _load()
    signals = data.get("recent_signals", [])
    entry = {
        "ticker":           signal.get("ticker"),
        "direction":        signal.get("direction"),
        "entry":            signal.get("entry"),
        "stop":             signal.get("stop"),
        "target":           signal.get("target"),
        "rr_ratio":         signal.get("rr_ratio"),
        "conviction_score": signal.get("conviction_score"),
        "yf_score":         signal.get("yf_score"),
        "gex_regime":       signal.get("gex_regime"),
        "flow_bias":        signal.get("flow_bias"),
        "thesis":           signal.get("thesis", "")[:300],
        "posted_at":        datetime.now().isoformat(),
        "outcome":          None,   # filled when position closes
    }
    signals.insert(0, entry)
    data["recent_signals"] = signals[:MAX_SIGNALS]
    _save(data)


def mark_bot_offline() -> None:
    data = _load()
    if "bot_status" in data:
        data["bot_status"]["is_running"] = False
    _save(data)


# ── Dashboard reads these ─────────────────────────────────────────────────────

def get_bot_status() -> dict:
    data = _load()
    status = data.get("bot_status", {})
    if not status:
        return {"is_running": False, "last_scan": None, "next_scan_seconds": None,
                "signal_count_this_week": 0, "max_signals_per_week": 15}

    # Compute live next-scan countdown
    last = status.get("last_scan")
    if last:
        elapsed = (datetime.now() - datetime.fromisoformat(last)).total_seconds()
        status["next_scan_seconds"] = max(0, 600 - int(elapsed))

    return status


def get_watchlist() -> list[dict]:
    return _load().get("watchlist_candidates", [])


def get_recent_signals() -> list[dict]:
    return _load().get("recent_signals", [])


def get_positions() -> list[dict]:
    """Read open + recently closed positions from position_store."""
    try:
        from memory.position_store import get_open_positions, get_closed_positions
        open_pos   = get_open_positions()
        closed_pos = get_closed_positions()[-5:]   # last 5 closed
        return {"open": open_pos, "closed": closed_pos}
    except Exception as e:
        return {"open": [], "closed": [], "error": str(e)}


def get_scorecard() -> dict:
    """All-time P&L stats from position_store."""
    try:
        from memory.position_store import get_closed_positions
        closed = get_closed_positions()
        recent = closed  # all-time
        if not recent:
            return {"trades": 0, "wins": 0, "losses": 0, "win_rate": 0, "total_pnl": 0}
        wins   = [p for p in recent if p.get("final_pnl_pct", 0) > 0]
        losses = [p for p in recent if p.get("final_pnl_pct", 0) <= 0]
        return {
            "trades":    len(recent),
            "wins":      len(wins),
            "losses":    len(losses),
            "win_rate":  round(len(wins) / len(recent) * 100, 1),
            "avg_win":   round(sum(p["final_pnl_pct"] for p in wins) / len(wins), 2) if wins else 0,
            "avg_loss":  round(sum(p["final_pnl_pct"] for p in losses) / len(losses), 2) if losses else 0,
            "total_pnl": round(sum(p.get("final_pnl_pct", 0) for p in recent), 2),
        }
    except Exception as e:
        return {"trades": 0, "error": str(e)}


def get_market_context() -> dict:
    """Live SPY, QQQ, VIX via yfinance (15-min delayed)."""
    tickers = {"SPY": "SPY", "QQQ": "QQQ", "VIX": "^VIX"}
    result = {}
    for label, symbol in tickers.items():
        try:
            df = yf.download(symbol, period="2d", interval="1d",
                             auto_adjust=True, progress=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            if len(df) >= 2:
                prev  = float(df["Close"].iloc[-2])
                curr  = float(df["Close"].iloc[-1])
                chg   = ((curr - prev) / prev) * 100
                result[label] = {"price": round(curr, 2), "change_pct": round(chg, 2)}
            elif len(df) == 1:
                curr = float(df["Close"].iloc[-1])
                result[label] = {"price": round(curr, 2), "change_pct": 0.0}
        except Exception as e:
            result[label] = {"price": None, "change_pct": None, "error": str(e)}

    result["updated_at"] = datetime.now().strftime("%H:%M:%S ET")
    return result


def get_chart_data(ticker: str) -> dict:
    """
    OHLCV + MA20/50 for lightweight-charts.
    GEX levels overlaid if LuminaFlow is configured.
    """
    try:
        raw = yf.download(ticker, period="3mo", interval="1d",
                          auto_adjust=True, progress=False, threads=False)

        # Flatten MultiIndex — yf wraps single tickers inconsistently
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)

        df = raw[["Open", "High", "Low", "Close", "Volume"]].copy()
        df = df.dropna(subset=["Close"])

        if df.empty:
            return {"ticker": ticker, "error": "no data returned", "candles": []}

        # Strip timezone so strftime works cleanly
        if hasattr(df.index, "tz") and df.index.tz is not None:
            df.index = df.index.tz_localize(None)

        candles, ma20_data, ma50_data = [], [], []
        ma20 = df["Close"].rolling(20).mean()
        ma50 = df["Close"].rolling(50).mean()

        for i, (idx, row) in enumerate(df.iterrows()):
            t = pd.Timestamp(idx).strftime("%Y-%m-%d")
            candles.append({
                "time":  t,
                "open":  round(float(row["Open"]),  2),
                "high":  round(float(row["High"]),  2),
                "low":   round(float(row["Low"]),   2),
                "close": round(float(row["Close"]), 2),
            })
            if not pd.isna(ma20.iloc[i]):
                ma20_data.append({"time": t, "value": round(float(ma20.iloc[i]), 2)})
            if not pd.isna(ma50.iloc[i]):
                ma50_data.append({"time": t, "value": round(float(ma50.iloc[i]), 2)})

        gex_levels, flip = _fetch_gex_levels(ticker)

        return {
            "ticker":     ticker,
            "candles":    candles,
            "ma20":       ma20_data,
            "ma50":       ma50_data,
            "gex_levels": gex_levels,
            "gex_flip":   flip,
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e), "candles": []}


def _fetch_gex_levels(ticker: str) -> tuple[list, float | None]:
    """Try to get GEX levels from LuminaFlow. Gracefully skip if unavailable."""
    try:
        from analysis.gex_analysis import analyze_exposure
        gex = analyze_exposure(ticker)
        levels = []
        for lv in gex.get("key_levels", [])[:5]:
            if lv.get("strike"):
                levels.append({
                    "price": lv["strike"],
                    "type":  "call_wall" if lv.get("gex", 0) > 0 else "put_wall",
                })
        for w in gex.get("call_walls", [])[:2]:
            levels.append({"price": w, "type": "call_wall"})
        for w in gex.get("put_walls", [])[:2]:
            levels.append({"price": w, "type": "put_wall"})
        return levels, gex.get("gex_flip")
    except Exception:
        return [], None
