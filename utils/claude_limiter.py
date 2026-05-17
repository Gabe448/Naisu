"""
Shared rate limiter and call logger for every Claude API call in the bot.

Rate limit: 20 calls per rolling 60-minute window (all callers combined).
Call acquire() before every API call. It raises RuntimeError if the limit is hit.
Call log_call() immediately after, passing the response usage object.
"""
import threading
import time
from collections import deque

_LIMIT  = 20       # max Claude calls per rolling hour
_WINDOW = 3600     # seconds

_lock: threading.Lock = threading.Lock()
_call_times: deque    = deque()


def acquire(caller: str) -> None:
    """
    Record intent to call Claude. Raises RuntimeError if at 20 calls/hour.
    Call this before every anthropic.messages.create().
    """
    with _lock:
        now = time.time()
        while _call_times and _call_times[0] < now - _WINDOW:
            _call_times.popleft()
        if len(_call_times) >= _LIMIT:
            oldest   = _call_times[0]
            resets_in = int(oldest + _WINDOW - now)
            mins, secs = divmod(resets_in, 60)
            raise RuntimeError(
                f"[CLAUDE] Rate limit hit in {caller}: {_LIMIT} calls/hour reached. "
                f"Resets in {mins}m {secs}s."
            )
        _call_times.append(now)
        used = len(_call_times)

    print(
        f"[CLAUDE] {caller} | calling Claude ({used}/{_LIMIT} this hour)",
        flush=True,
    )


def log_call(caller: str, model: str, in_tok: int, out_tok: int) -> None:
    """
    Print a structured cost line after every successful Claude API response.
    Pass response.usage.input_tokens and response.usage.output_tokens.
    """
    with _lock:
        now = time.time()
        while _call_times and _call_times[0] < now - _WINDOW:
            _call_times.popleft()
        used = len(_call_times)

    # Rough cost estimate (USD) — update if pricing changes
    _COST = {
        "claude-opus-4-6":          (15.0, 75.0),   # $/M in, $/M out
        "claude-opus-4-7":          (15.0, 75.0),
        "claude-sonnet-4-6":        (3.0,  15.0),
        "claude-haiku-4-5":         (0.8,   4.0),
        "claude-haiku-4-5-20251001":(0.8,   4.0),
    }
    in_rate, out_rate = _COST.get(model, (15.0, 75.0))
    cost = (in_tok * in_rate + out_tok * out_rate) / 1_000_000

    print(
        f"[CLAUDE] {caller} | model={model} | "
        f"in={in_tok:,} out={out_tok:,} total={in_tok+out_tok:,} | "
        f"~${cost:.4f} | calls_this_hour={used}/{_LIMIT}",
        flush=True,
    )


def calls_this_hour() -> int:
    """Return the current call count for the rolling hour window."""
    with _lock:
        now = time.time()
        while _call_times and _call_times[0] < now - _WINDOW:
            _call_times.popleft()
        return len(_call_times)
