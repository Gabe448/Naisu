"""
Macro event filter — hard filter #2.

Trading is blocked within 30 minutes of any scheduled macro event.
Macro events file: macro_events.json at project root.

File format:
[
  {"name": "CPI Release",   "datetime": "2026-04-15 08:30"},
  {"name": "FOMC Decision", "datetime": "2026-04-30 14:00"},
  {"name": "NFP",           "datetime": "2026-05-02 08:30"}
]
Datetimes are US/Eastern (ET). 24h format.

To update: edit macro_events.json before each week.
The bot reads the file live — no restart needed.
"""
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytz

ET = pytz.timezone("America/New_York")
MACRO_WINDOW_MINUTES = 30


def _events_path() -> Path:
    return Path(os.environ.get("MACRO_EVENTS_PATH", "macro_events.json"))


def _load_events() -> list[dict]:
    path = _events_path()
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[macro_filter] Failed to load macro_events.json: {e}")
        return []


def is_macro_window() -> tuple[bool, str]:
    """
    Returns (True, event_name) if current ET time is within
    MACRO_WINDOW_MINUTES of any scheduled macro event.
    Returns (False, "") otherwise.
    """
    events = _load_events()
    if not events:
        return False, ""

    now_et = datetime.now(ET)
    window = timedelta(minutes=MACRO_WINDOW_MINUTES)

    for event in events:
        try:
            dt_str   = event.get("datetime", "")
            event_dt = ET.localize(datetime.strptime(dt_str, "%Y-%m-%d %H:%M"))
            if abs(now_et - event_dt) <= window:
                name = event.get("name", "macro event")
                print(f"[macro_filter] Blocked: within {MACRO_WINDOW_MINUTES}min of {name}", flush=True)
                return True, name
        except Exception:
            continue

    return False, ""


def get_upcoming_events(hours_ahead: int = 48) -> list[dict]:
    """
    Return events in the next N hours. Used by morning brief and on-ready message.
    Each dict includes _dt_formatted for display.
    """
    events = _load_events()
    now_et = datetime.now(ET)
    cutoff = now_et + timedelta(hours=hours_ahead)

    upcoming = []
    for event in events:
        try:
            dt_str   = event.get("datetime", "")
            event_dt = ET.localize(datetime.strptime(dt_str, "%Y-%m-%d %H:%M"))
            if now_et <= event_dt <= cutoff:
                out = dict(event)
                out["_dt_formatted"] = event_dt.strftime("%a %b %d %H:%M ET")
                upcoming.append(out)
        except Exception:
            continue

    return sorted(upcoming, key=lambda e: e.get("datetime", ""))
