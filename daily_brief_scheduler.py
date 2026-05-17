"""
daily_brief_scheduler.py — Weekday 9:00 AM ET morning brief via Claude.

Collects:
  - Open positions + PnL
  - Watchlist tickers
  - SPY / QQQ pre-market price movement vs. yesterday's close
  - Yesterday's post-market summary (extended-hours OHLCV)
  - Recent news for SPY, QQQ, positions, and watchlist tickers
  - VIX + market regime

Sends everything to Claude with the brief prompt, then posts the response
to Discord (DISCORD_MORNING_CHANNEL_ID) and prints to console.

Usage:
  python daily_brief_scheduler.py          # run scheduler (blocking)
  python daily_brief_scheduler.py --now    # fire immediately and exit
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, date

import pytz
import requests
from dotenv import load_dotenv

load_dotenv()

ET = pytz.timezone("America/New_York")

# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are a sharp, direct swing trading analyst.
You receive structured market data every morning before the open.
Your job: write a concise, actionable daily brief.

Structure your response exactly like this:

## POSITIONS
For each open position: current status, distance to stop/target, and one-sentence thesis check.

## STOCKS TO WATCH TODAY
Top 3-5 tickers with a one-line reason each. Be specific — mention the level or catalyst.

## PREMARKET MARKET MOVEMENT
SPY and QQQ premarket action vs. yesterday's close. What it means for today's session.

## PREMARKET NEWS
Key headlines from the last 12 hours that could move markets or individual names.

## YESTERDAY'S POST-MARKET SUMMARY
What happened in after-hours. Any names that moved significantly and why.

Be direct. No filler. If nothing is interesting, say so.
Keep the whole brief under 600 words."""


# ── Data gathering ─────────────────────────────────────────────────────────────

def _gather_positions() -> tuple[list[dict], str]:
    """Fetch open positions with live prices."""
    try:
        from analysis.position_manager import get_position_summary
        positions = get_position_summary()
    except Exception as e:
        print(f"[brief] position fetch error: {e}", flush=True)
        positions = []

    if not positions:
        return [], "No open positions."

    lines = []
    for p in positions:
        ticker  = p.get("ticker", "?")
        dir_    = p.get("direction", "?").upper()
        entry   = p.get("entry_price", 0)
        current = p.get("current_price", entry)
        stop    = p.get("stop_price", 0)
        target  = p.get("target_price", 0)
        pnl     = p.get("pnl_pct", 0)
        days    = p.get("days_held", 0)
        score   = p.get("conviction_score", "?")

        dist_stop   = round(abs(current - stop)   / current * 100, 1) if stop   and current else "?"
        dist_target = round(abs(target - current) / current * 100, 1) if target and current else "?"

        lines.append(
            f"  {ticker} {dir_} | Entry ${entry} → Current ${current} | "
            f"PnL {pnl:+.1f}% | Day {days} | Score {score}\n"
            f"    Stop ${stop} ({dist_stop}% away) | Target ${target} ({dist_target}% away)"
        )

    return positions, "\n\n".join(lines)


def _gather_watchlist() -> str:
    """Get the current watchlist."""
    try:
        from scanner.watchlist import get_watchlist
        tickers = get_watchlist()
        if not tickers:
            return "Watchlist empty."
        return ", ".join(tickers)
    except Exception as e:
        print(f"[brief] watchlist error: {e}", flush=True)
        return "Could not load watchlist."


def _gather_premarket(tickers: list[str] = None) -> str:
    """
    Fetch pre-market prices and compare to yesterday's close using yfinance.
    Also returns yesterday's after-hours move.
    """
    import yfinance as yf

    if tickers is None:
        tickers = ["SPY", "QQQ"]

    lines = []
    afterhours_lines = []

    for ticker in tickers:
        try:
            # 2-day download with pre/post market to capture extended hours
            tkr  = yf.Ticker(ticker)
            hist = tkr.history(period="2d", interval="1m", prepost=True)

            if hist.empty:
                lines.append(f"  {ticker}: no data")
                continue

            now_et = datetime.now(ET)

            # Yesterday's regular close (~4:00 PM yesterday)
            yesterday = now_et.date() - timedelta(days=1)
            # Go back further if yesterday was weekend
            while yesterday.weekday() >= 5:
                yesterday -= timedelta(days=1)

            yday_close_rows = hist[
                (hist.index.date == yesterday) &
                (hist.index.hour >= 15) &
                (hist.index.hour < 16)
            ]
            prev_close = float(yday_close_rows["Close"].iloc[-1]) if not yday_close_rows.empty else None

            # Yesterday's after-hours (4:00 PM – 8:00 PM ET)
            ah_rows = hist[
                (hist.index.date == yesterday) &
                (hist.index.hour >= 16) &
                (hist.index.hour < 20)
            ]
            if not ah_rows.empty and prev_close:
                ah_open  = float(ah_rows["Close"].iloc[0])
                ah_close = float(ah_rows["Close"].iloc[-1])
                ah_pct   = round((ah_close - prev_close) / prev_close * 100, 2)
                afterhours_lines.append(
                    f"  {ticker} AH: ${prev_close:.2f} → ${ah_close:.2f} ({ah_pct:+.2f}%) "
                    f"| Range ${ah_rows['Low'].min():.2f}–${ah_rows['High'].max():.2f}"
                )

            # Today's pre-market (4:00 AM – 9:00 AM ET today)
            today = now_et.date()
            pm_rows = hist[
                (hist.index.date == today) &
                (hist.index.hour >= 4) &
                (hist.index.hour < 9)
            ]
            if pm_rows.empty:
                # Fallback: last row before regular market open
                pm_rows = hist[hist.index.date == today]

            if not pm_rows.empty and prev_close:
                pm_last = float(pm_rows["Close"].iloc[-1])
                pm_pct  = round((pm_last - prev_close) / prev_close * 100, 2)
                pm_high = float(pm_rows["High"].max())
                pm_low  = float(pm_rows["Low"].min())
                lines.append(
                    f"  {ticker}: prev close ${prev_close:.2f} → premarket ${pm_last:.2f} "
                    f"({pm_pct:+.2f}%) | PM range ${pm_low:.2f}–${pm_high:.2f}"
                )
            else:
                lines.append(f"  {ticker}: no premarket data (prev close ${prev_close:.2f})" if prev_close else f"  {ticker}: no data")

        except Exception as e:
            lines.append(f"  {ticker}: error ({e})")

    return (
        "\n".join(lines) or "No premarket data.",
        "\n".join(afterhours_lines) or "No after-hours data."
    )


def _gather_news(tickers: list[str]) -> str:
    """Fetch recent news headlines from Polygon for given tickers."""
    try:
        from data.polygon_client import get_news
    except Exception:
        return "News unavailable (polygon_client not loaded)."

    all_news = []
    seen_titles = set()

    for ticker in tickers[:6]:  # cap to avoid rate limits
        try:
            items = get_news(ticker, limit=5)
            for item in items:
                # polygon-api-client returns objects with attributes
                title     = getattr(item, "title", None) or item.get("title", "") if hasattr(item, "get") else getattr(item, "title", "")
                pub_utc   = getattr(item, "published_utc", None) or ""
                publisher = ""
                try:
                    pub_obj = getattr(item, "publisher", None)
                    if pub_obj:
                        publisher = getattr(pub_obj, "name", "") or ""
                except Exception:
                    pass

                if not title or title in seen_titles:
                    continue
                seen_titles.add(title)

                # Only show news from last 18 hours
                if pub_utc:
                    try:
                        from datetime import timezone
                        pub_dt = datetime.fromisoformat(pub_utc.replace("Z", "+00:00"))
                        cutoff = datetime.now(timezone.utc) - timedelta(hours=18)
                        if pub_dt < cutoff:
                            continue
                        ts = pub_dt.astimezone(ET).strftime("%H:%M ET")
                    except Exception:
                        ts = pub_utc[:16]
                else:
                    ts = ""

                src = f" ({publisher})" if publisher else ""
                all_news.append(f"  [{ts}] [{ticker}] {title}{src}")

        except Exception as e:
            print(f"[brief] news fetch failed for {ticker}: {e}", flush=True)

    if not all_news:
        return "No relevant news in the last 18 hours."

    # Sort by time (they're already strings starting with [HH:MM ET])
    all_news.sort(reverse=True)
    return "\n".join(all_news[:15])


def _gather_market_context() -> dict:
    """Fetch SPY/QQQ/VIX context."""
    try:
        from analysis.claude_analyst import get_market_context
        return get_market_context()
    except Exception as e:
        print(f"[brief] market context error: {e}", flush=True)
        return {"spy": {}, "qqq": {}, "vix": 0.0, "regime": "unclear"}


# ── Claude call ───────────────────────────────────────────────────────────────

def build_brief_prompt(
    positions_block:  str,
    watchlist_block:  str,
    premarket_block:  str,
    afterhours_block: str,
    news_block:       str,
    market_context:   dict,
) -> str:
    spy  = market_context.get("spy", {})
    qqq  = market_context.get("qqq", {})
    vix  = market_context.get("vix", 0)
    today = date.today().strftime("%A, %B %d %Y")

    return f"""DATE: {today}
TIME: Pre-market (before 9:30 AM ET)

═══════════════════════════════════════
MARKET CONTEXT
═══════════════════════════════════════
SPY:  ${spy.get('price', 0):.2f} | GEX: {spy.get('gex_regime', 'N/A').upper()} | Flip: ${spy.get('gex_flip', 0):.2f} | Trend: {spy.get('trend', 'N/A')}
QQQ:  ${qqq.get('price', 0):.2f} | GEX: {qqq.get('gex_regime', 'N/A').upper()} | Flip: ${qqq.get('gex_flip', 0):.2f} | Trend: {qqq.get('trend', 'N/A')}
VIX:  {vix:.2f}
Regime: {market_context.get('regime', 'unclear')}

═══════════════════════════════════════
PRE-MARKET MOVEMENT (vs yesterday close)
═══════════════════════════════════════
{premarket_block}

═══════════════════════════════════════
YESTERDAY'S AFTER-HOURS
═══════════════════════════════════════
{afterhours_block}

═══════════════════════════════════════
OPEN POSITIONS
═══════════════════════════════════════
{positions_block}

═══════════════════════════════════════
WATCHLIST
═══════════════════════════════════════
{watchlist_block}

═══════════════════════════════════════
NEWS (last 18 hours)
═══════════════════════════════════════
{news_block}

═══════════════════════════════════════

Create a daily brief recap of all current positions, stocks to watch for the day, and a summary of market movement during premarket and news during premarket and yesterdays post market."""


def call_claude(prompt: str) -> str:
    """Send the brief prompt to Claude and return the text response."""
    import anthropic
    from utils.claude_limiter import acquire as _rl_acquire, log_call as _rl_log

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    _model = "claude-sonnet-4-6"
    _rl_acquire("call_claude(morning_brief)")
    response = client.messages.create(
        model=_model,
        max_tokens=1500,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    _rl_log("call_claude(morning_brief)", _model,
            response.usage.input_tokens, response.usage.output_tokens)
    return response.content[0].text.strip()


# ── Discord posting ────────────────────────────────────────────────────────────

def post_to_discord(text: str) -> None:
    """Post the brief to the Discord morning channel via webhook or bot token."""
    channel_id = os.environ.get("DISCORD_MORNING_CHANNEL_ID") or os.environ.get("DISCORD_CHANNEL_ID")
    bot_token  = os.environ.get("DISCORD_BOT_TOKEN")

    if not channel_id or not bot_token:
        print("[brief] Discord not configured — skipping post.", flush=True)
        return

    # Discord has a 2000-char message limit — split if needed
    header = f"**--- Morning Brief | {date.today().strftime('%A %b %d')} ---**\n"
    full   = header + text
    chunks = []
    while len(full) > 1900:
        # Split at last newline before 1900
        split_at = full.rfind("\n", 0, 1900)
        if split_at == -1:
            split_at = 1900
        chunks.append(full[:split_at])
        full = full[split_at:].lstrip("\n")
    chunks.append(full)

    url     = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    headers = {
        "Authorization": f"Bot {bot_token}",
        "Content-Type": "application/json",
    }

    for chunk in chunks:
        if not chunk.strip():
            continue
        resp = requests.post(url, headers=headers, json={"content": chunk}, timeout=10)
        if not resp.ok:
            print(f"[brief] Discord post failed: {resp.status_code} {resp.text}", flush=True)
        else:
            print("[brief] Posted chunk to Discord.", flush=True)


# ── Main job ───────────────────────────────────────────────────────────────────

def run_morning_brief() -> None:
    """Gather data, call Claude, post to Discord, print to console."""
    now_et = datetime.now(ET)
    print(f"[brief] Running morning brief at {now_et.strftime('%Y-%m-%d %H:%M:%S ET')}", flush=True)

    # 1. Gather data
    positions, positions_block = _gather_positions()
    watchlist_block = _gather_watchlist()

    market_ctx = _gather_market_context()

    # Build ticker list for premarket + news: always SPY/QQQ + positions + watchlist
    position_tickers = [p["ticker"] for p in positions]
    try:
        from scanner.watchlist import get_watchlist
        watch_tickers = get_watchlist()
    except Exception:
        watch_tickers = []
    all_tickers = list(dict.fromkeys(["SPY", "QQQ"] + position_tickers + watch_tickers))

    premarket_block, afterhours_block = _gather_premarket(["SPY", "QQQ"])
    news_block = _gather_news(all_tickers)

    # 2. Build prompt
    prompt = build_brief_prompt(
        positions_block  = positions_block,
        watchlist_block  = watchlist_block,
        premarket_block  = premarket_block,
        afterhours_block = afterhours_block,
        news_block       = news_block,
        market_context   = market_ctx,
    )

    # 3. Call Claude
    print("[brief] Calling Claude...", flush=True)
    try:
        brief_text = call_claude(prompt)
    except Exception as e:
        print(f"[brief] Claude API error: {e}", flush=True)
        return

    # 4. Print to console
    print("\n" + "=" * 60)
    print(brief_text)
    print("=" * 60 + "\n", flush=True)

    # 5. Post to Discord
    post_to_discord(brief_text)


# ── Scheduler ─────────────────────────────────────────────────────────────────

def run_scheduler() -> None:
    """Block and run the Mon–Fri 9:00 AM ET cron job."""
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = BlockingScheduler(timezone=ET)
    trigger   = CronTrigger(
        day_of_week = "mon-fri",
        hour        = 9,
        minute      = 0,
        second      = 0,
        timezone    = ET,
    )

    scheduler.add_job(
        run_morning_brief,
        trigger  = trigger,
        id       = "morning_brief",
        name     = "Morning Brief (Mon-Fri 9:00 AM ET)",
        misfire_grace_time = 300,  # 5 min grace if process was delayed
    )

    print(f"[brief] Scheduler started. Next job: Mon–Fri at 9:00 AM ET.", flush=True)
    print(f"[brief] Current time: {datetime.now(ET).strftime('%Y-%m-%d %H:%M:%S %Z')}", flush=True)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("[brief] Scheduler stopped.", flush=True)


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Daily Morning Brief Scheduler")
    parser.add_argument(
        "--now",
        action="store_true",
        help="Fire the brief immediately and exit (skip scheduler)",
    )
    args = parser.parse_args()

    if args.now:
        run_morning_brief()
    else:
        run_scheduler()
