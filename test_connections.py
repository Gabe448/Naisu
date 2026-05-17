"""
API connection tester — run this before starting the bot.
Tests Polygon, LuminaFlow, and Discord without starting any loops.

Usage: python test_connections.py
"""
import os
import sys
import json
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

PASS = "  [PASS]"
FAIL = "  [FAIL]"
WARN = "  [WARN]"
SEP  = "-" * 50


def header(title: str):
    print(f"\n{SEP}")
    print(f"  {title}")
    print(SEP)


def check_env_vars():
    header("ENV VARS")
    required = {
        "POLYGON_API_KEY":     "Polygon.io",
        "LUMINAFLOW_API_KEY":  "LuminaFlow",
        "DISCORD_BOT_TOKEN":   "Discord bot token",
        "DISCORD_CHANNEL_ID":  "Discord channel",
    }
    all_ok = True
    for key, label in required.items():
        val = os.environ.get(key, "")
        if val and val not in ("your_polygon_api_key_here", "your_luminaflow_api_key_here",
                               "your_discord_bot_token_here"):
            print(f"{PASS} {label}: {key}={val[:8]}...")
        else:
            print(f"{FAIL} {label}: {key} not set or is placeholder")
            all_ok = False
    return all_ok


def test_polygon():
    header("POLYGON.IO")
    api_key = os.environ.get("POLYGON_API_KEY", "")
    if not api_key:
        print(f"{FAIL} POLYGON_API_KEY not set — skipping")
        return False

    try:
        import requests
        # Test 1: Ticker snapshot for SPY
        r = requests.get(
            f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers/SPY",
            params={"apiKey": api_key},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            ticker = data.get("ticker", {})
            day    = ticker.get("day", {})
            price  = day.get("c", "?")
            vol    = day.get("v", "?")
            print(f"{PASS} Snapshot: SPY close=${price}, volume={vol:,}" if isinstance(vol, (int, float)) else f"{PASS} Snapshot: SPY close=${price}")
        elif r.status_code == 403:
            print(f"{FAIL} 403 Forbidden — API key invalid or plan doesn't include this endpoint")
            return False
        elif r.status_code == 429:
            print(f"{WARN} 429 Rate limited — key works but hit limit")
        else:
            print(f"{FAIL} HTTP {r.status_code}: {r.text[:120]}")
            return False

        # Test 2: Daily bars (what the scanner uses)
        from datetime import datetime, timedelta
        to_d   = datetime.now().strftime("%Y-%m-%d")
        from_d = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
        r2 = requests.get(
            f"https://api.polygon.io/v2/aggs/ticker/SPY/range/1/day/{from_d}/{to_d}",
            params={"apiKey": api_key, "adjusted": "true", "limit": 5},
            timeout=10,
        )
        if r2.status_code == 200:
            bars = r2.json().get("results", [])
            print(f"{PASS} Daily OHLCV: {len(bars)} bars returned for SPY")
        else:
            print(f"{WARN} Daily OHLCV: HTTP {r2.status_code}")

        # Test 3: 4H bars (intraday — requires Starter plan or above)
        r3 = requests.get(
            f"https://api.polygon.io/v2/aggs/ticker/SPY/range/4/hour/{from_d}/{to_d}",
            params={"apiKey": api_key, "adjusted": "true", "limit": 10},
            timeout=10,
        )
        if r3.status_code == 200:
            bars = r3.json().get("results", [])
            print(f"{PASS} 4H bars:     {len(bars)} bars returned for SPY")
        elif r3.status_code == 403:
            print(f"{WARN} 4H bars: 403 — intraday requires Starter plan or above")
        else:
            print(f"{WARN} 4H bars: HTTP {r3.status_code}")

        # Test 4: News
        r4 = requests.get(
            f"https://api.polygon.io/v2/reference/news",
            params={"apiKey": api_key, "ticker": "SPY", "limit": 3},
            timeout=10,
        )
        if r4.status_code == 200:
            articles = r4.json().get("results", [])
            print(f"{PASS} News:        {len(articles)} articles returned for SPY")
        else:
            print(f"{WARN} News: HTTP {r4.status_code}")

        return True

    except Exception as e:
        print(f"{FAIL} Exception: {e}")
        return False


def test_luminaflow():
    header("LUMINAFLOW")
    api_key  = os.environ.get("LUMINAFLOW_API_KEY", "")
    base_url = os.environ.get("LUMINAFLOW_BASE_URL", "https://api.luminaflow.app/api/export")

    if not api_key or api_key == "your_luminaflow_api_key_here":
        print(f"{FAIL} LUMINAFLOW_API_KEY not set — skipping")
        return False

    # Strip any endpoint suffix the user may have included in the base URL
    for suffix in ("/gex", "/dex", "/vex"):
        if base_url.endswith(suffix):
            base_url = base_url[: -len(suffix)]
    base_url = base_url.rstrip("/")

    try:
        import requests
        # Correct auth: X-Session-Token header, symbol= query param
        headers = {"X-Session-Token": api_key}

        # Test GEX endpoint
        r = requests.get(f"{base_url}/gex", headers=headers,
                         params={"symbol": "SPY"}, timeout=10)
        if r.status_code == 200:
            data      = r.json()
            net_gex   = data.get("net_gex", "?")
            spot      = data.get("spot", "?")
            king      = data.get("king_above", "?")
            strikes   = len(data.get("strikes", []))
            print(f"{PASS} GEX:  spot=${spot}, net_gex={net_gex}, king_above=${king}, {strikes} strikes")
        elif r.status_code == 401:
            print(f"{FAIL} 401 Unauthorized — LUMINAFLOW_API_KEY invalid or wrong header format")
            return False
        elif r.status_code == 404:
            print(f"{FAIL} 404 — endpoint not found. Base URL: {base_url}")
            return False
        else:
            print(f"{FAIL} GEX: HTTP {r.status_code} — {r.text[:120]}")
            return False

        # Test DEX endpoint
        r2 = requests.get(f"{base_url}/dex", headers=headers,
                          params={"symbol": "SPY"}, timeout=10)
        if r2.status_code == 200:
            data2   = r2.json()
            net_dex = data2.get("net_dex", "?")
            print(f"{PASS} DEX:  net_dex={net_dex}")
        else:
            print(f"{WARN} DEX: HTTP {r2.status_code}")

        # Test VEX endpoint
        r3 = requests.get(f"{base_url}/vex", headers=headers,
                          params={"symbol": "SPY"}, timeout=10)
        if r3.status_code == 200:
            data3   = r3.json()
            net_vex = data3.get("net_vex", "?")
            print(f"{PASS} VEX:  net_vex={net_vex}")
        else:
            print(f"{WARN} VEX: HTTP {r3.status_code}")

        return True

    except requests.exceptions.ConnectionError:
        print(f"{FAIL} Connection refused — check LUMINAFLOW_BASE_URL={base_url}")
        return False
    except Exception as e:
        print(f"{FAIL} Exception: {e}")
        return False


def test_discord():
    header("DISCORD")
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not token or token == "your_discord_bot_token_here":
        print(f"{FAIL} DISCORD_BOT_TOKEN not set — skipping")
        return False

    try:
        import requests
        headers = {"Authorization": f"Bot {token}"}

        # Validate token via /users/@me (no gateway connection needed)
        r = requests.get(
            "https://discord.com/api/v10/users/@me",
            headers=headers,
            timeout=10,
        )
        if r.status_code == 200:
            bot = r.json()
            name = bot.get("username", "?")
            bid  = bot.get("id", "?")
            print(f"{PASS} Token valid — bot: {name}#{bot.get('discriminator','0')} (ID: {bid})")
        elif r.status_code == 401:
            print(f"{FAIL} 401 Unauthorized — DISCORD_BOT_TOKEN invalid")
            return False
        else:
            print(f"{FAIL} HTTP {r.status_code}: {r.text[:120]}")
            return False

        # Check channel access
        channel_id = os.environ.get("DISCORD_CHANNEL_ID", "")
        if channel_id:
            r2 = requests.get(
                f"https://discord.com/api/v10/channels/{channel_id}",
                headers=headers,
                timeout=10,
            )
            if r2.status_code == 200:
                ch = r2.json()
                print(f"{PASS} Channel access: #{ch.get('name','?')} in guild {ch.get('guild_id','?')}")
            elif r2.status_code == 403:
                print(f"{WARN} Channel {channel_id}: 403 — bot lacks access. Check server permissions.")
            elif r2.status_code == 404:
                print(f"{FAIL} Channel {channel_id}: 404 — channel not found. Check DISCORD_CHANNEL_ID.")
            else:
                print(f"{WARN} Channel check: HTTP {r2.status_code}")

        return True

    except Exception as e:
        print(f"{FAIL} Exception: {e}")
        return False


def test_yfinance():
    header("YFINANCE (Tier 1 scanner)")
    try:
        import yfinance as yf
        spy = yf.download("SPY", period="5d", interval="1d", progress=False)
        if not spy.empty:
            # Flatten MultiIndex columns if present (yfinance >=0.2.x)
            if isinstance(spy.columns, pd.MultiIndex):
                spy.columns = spy.columns.get_level_values(0)
            close = float(spy["Close"].iloc[-1])
            vol   = int(spy["Volume"].iloc[-1])
            print(f"{PASS} SPY close=${close:.2f}, volume={vol:,}")
            print(f"{PASS} {len(spy)} daily bars downloaded")
        else:
            print(f"{FAIL} Empty DataFrame returned")
            return False

        # Quick multi-ticker test (simulates the broad scan)
        sample = ["AAPL", "MSFT", "NVDA"]
        df2 = yf.download(sample, period="5d", interval="1d",
                          group_by="ticker", progress=False)
        if not df2.empty:
            print(f"{PASS} Multi-ticker download OK ({len(sample)} tickers)")
        return True
    except ImportError:
        print(f"{FAIL} yfinance not installed — run: pip install -r requirements.txt")
        return False
    except Exception as e:
        print(f"{FAIL} Exception: {e}")
        return False


if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("  TRADING BOT — API CONNECTION TEST")
    print("=" * 50)

    results = {
        "env_vars":    check_env_vars(),
        "yfinance":    test_yfinance(),
        "polygon":     test_polygon(),
        "luminaflow":  test_luminaflow(),
        "discord":     test_discord(),
    }

    header("SUMMARY")
    all_pass = True
    for name, ok in results.items():
        status = PASS if ok else FAIL
        print(f"{status} {name}")
        if not ok:
            all_pass = False

    print()
    if all_pass:
        print("  All checks passed. Run: python main.py")
    else:
        print("  Fix the failed checks above, then re-run this script.")
    print()
