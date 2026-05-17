"""
Shared API error types. Import these to distinguish rate limits from real errors.
A RateLimitError means: stop calling this API for this pipeline run, move on.
"""
import requests


class RateLimitError(Exception):
    """429 — quota hit. Caller should skip remaining calls to this API."""
    pass


def raise_for_status_smart(response: requests.Response, api_name: str) -> None:
    """
    Like raise_for_status() but converts 429 → RateLimitError with a clear message.
    Call this instead of r.raise_for_status() in any API client.
    """
    if response.status_code == 429:
        reset = response.headers.get("X-RateLimit-Reset", "unknown")
        msg   = response.json().get("message", "") if response.text.startswith("{") else response.text[:80]
        raise RateLimitError(f"{api_name} rate limit hit (429). {msg} Reset: {reset}")
    response.raise_for_status()
