"""
LuminaFlow client — GEX, DEX, VEX options greek exposure data.

Auth:   X-Session-Token header (not Bearer)
Param:  symbol=SPY (not ticker=)
Base:   https://api.luminaflow.app/api/export

Response shape (GEX example):
  data['spot']       — current price
  data['net_gex']    — total net gamma exposure
  data['king_above'] — dominant strike above spot (GEX flip / pin level)
  data['strikes']    — list of {strike, gex} dicts

DEX uses net_dex / strikes[].dex
VEX uses net_vex / strikes[].vex
"""
import os
import requests
from data.api_errors import raise_for_status_smart


def _base() -> str:
    url = os.environ.get("LUMINAFLOW_BASE_URL", "https://api.luminaflow.app/api/export").rstrip("/")
    # Strip trailing /gex if the env var already includes it — _get() appends it
    if url.endswith("/gex"):
        url = url[:-4]
    return url


def _headers() -> dict:
    return {"X-Session-Token": os.environ["LUMINAFLOW_API_KEY"]}


def _get(ticker: str) -> dict:
    """Always hits /gex — the single endpoint that returns gex, dex, and vex data."""
    url = f"{_base()}/gex"
    r = requests.get(url, headers=_headers(), params={"symbol": ticker}, timeout=10)
    raise_for_status_smart(r, "LuminaFlow")
    return r.json()


def get_gex(ticker: str) -> dict:
    return _get(ticker)


def get_dex(ticker: str) -> dict:
    """Returns the same response as get_gex — caller reads net_dex / strikes[].dex."""
    return _get(ticker)


def get_vex(ticker: str) -> dict:
    """Returns the same response as get_gex — caller reads net_vex / strikes[].vex."""
    return _get(ticker)


def get_full_exposure(ticker: str) -> dict:
    """Single API call — all three exposure types live in the same response."""
    data = _get(ticker)
    return {"gex": data, "dex": data, "vex": data}
