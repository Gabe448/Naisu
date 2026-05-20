"""
Discord bot — Claude-driven swing trading analyst.

Decision flow (automated scan, every 10 min):
  1. Hard filter: market open?
  2. Hard filter: within 30min of macro event?
  3. Hard filter: already 5 concurrent positions?
  4. Run pipeline → top 50 candidates (raw data, no scoring)
  5. Claude analyzes each candidate with full memory + chart image
  6. trade  → post signal, open position
     watch  → add to watchlist, post brief note
     skip   → silent

Commands:
  !signal <TICKER>   — on-demand Claude analysis of any ticker
  !chart <TICKER>    — daily chart with GEX + MA overlays
  !positions         — list all open positions
  !add <TICKER>      — add to watchlist
  !remove <TICKER>   — remove from watchlist
  !watchlist         — show current watchlist
  !open <TICKER> <direction> <entry> <stop> <target>  — manually open position
  !close <ID> <exit_price>                             — manually close position
  !weekly            — trigger weekly review now
  !macro             — show upcoming macro events
"""
import asyncio
import os
import ssl
import aiohttp
import discord
from discord.ext import commands, tasks
from datetime import datetime
import pytz
import anthropic as _anthropic

from utils.claude_limiter import acquire as _rl_acquire, log_call as _rl_log
import data.yf_client as _yfc
from analysis.claude_analyst import analyze_candidate, build_candidate_for_ticker, get_market_context
from analysis.macro_filter import is_macro_window, get_upcoming_events
from scanner.pipeline import run_pipeline, describe_pipeline_run
from analysis.position_manager import check_all_positions, get_position_summary
from analysis.morning_brief import build_morning_brief
from analysis.chart_renderer import render_chart
from scanner.watchlist import get_watchlist, add_ticker, remove_ticker
from scanner.Manual_watchlist import get_manual_watchlist
from memory.position_store import (
    get_open_positions, open_position, close_position, get_weekly_stats, get_closed_positions
)
from memory.obsidian_writer import (
    log_signal, log_position_opened, log_position_closed, log_trade_learning,
    write_weekly_review, log_note
)


ET = pytz.timezone("America/New_York")

intents = discord.Intents.all()


class TradingBot(commands.Bot):
    async def setup_hook(self):
        # Python 3.14 changed SSL internals; explicit context fixes the handshake reset
        ssl_ctx = ssl.create_default_context()
        self.http.connector = aiohttp.TCPConnector(ssl=ssl_ctx)


bot = TradingBot(command_prefix="!", intents=intents)

MAX_CONCURRENT_POSITIONS = 5   # Hard limit — the ONLY position gate
MAX_SIGNALS_PER_WEEK     = 20  # Soft cap to prevent runaway spend

_signals_this_week:    set[str] = set()
_signal_count_this_week: int    = 0
_announced = False


# ── Hard filters ──────────────────────────────────────────────────────────────

def _is_market_open() -> bool:
    """Hard filter 1: NYSE regular hours Mon-Fri 9:30-16:00 ET."""
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    t = now.hour * 60 + now.minute
    return 9 * 60 + 30 <= t < 16 * 60


def _position_count() -> int:
    return len(get_open_positions())


def _has_open_position(ticker: str) -> bool:
    return any(p["ticker"] == ticker for p in get_open_positions())


# ── Signal posting ────────────────────────────────────────────────────────────

async def _post_signal(channel, decision: dict, candidate: dict, label: str = "") -> None:
    """Post Claude's trade decision as a Discord embed + chart, open position."""
    global _signal_count_this_week
    ticker = decision["ticker"]

    if _has_open_position(ticker):
        print(f"[bot] Skipping {ticker} — already have open position", flush=True)
        return

    _signals_this_week.add(ticker)
    _signal_count_this_week += 1

    embed = _signal_embed(decision)
    prefix = f"**{label}** " if label else ""
    await channel.send(f"{prefix}", embed=embed) if label else await channel.send(embed=embed)

    # Chart
    try:
        daily  = candidate.get("yf_metrics", {}).get("daily_df")
        gex    = candidate.get("gex", {})
        pivots = candidate.get("weekly_pivots", {})
        if daily is not None and not daily.empty:
            chart_buf = render_chart(ticker, daily, gex, pivots)
            chart_buf.seek(0)
            await channel.send(file=discord.File(fp=chart_buf, filename=f"{ticker}_chart.png"))
    except Exception as e:
        await channel.send(f"*(Chart render failed: {e})*")

    # Open position — use t2 as primary target for stop/target monitoring
    entry  = decision.get("entry")   or candidate.get("real_time_price", 0)
    stop   = decision.get("stop")    or 0
    target = decision.get("t2")      or decision.get("t1") or 0

    pos = open_position(
        ticker=ticker,
        direction=decision.get("direction", "long"),
        entry_price=entry,
        stop_price=stop,
        target_price=target,
        conviction_score=decision.get("confidence", 0),
        thesis=decision.get("thesis", ""),
        signal=decision,
    )

    # Build a signal dict shaped for obsidian_writer.log_signal
    _log_sig = {
        "ticker":        ticker,
        "direction":     decision.get("direction", "long"),
        "price":         candidate.get("real_time_price", 0),
        "entry":         entry,
        "stop":          stop,
        "target":        target,
        "rr_ratio":      _compute_rr(entry, stop, target),
        "conviction_score": decision.get("confidence", 0),
        "gex_regime":    candidate.get("gex", {}).get("gex_regime", "N/A"),
        "flow_bias":     candidate.get("flow_bias", {}).get("bias", "neutral"),
        "flow_call_pct": candidate.get("flow_bias", {}).get("call_pct", 0.5),
        "thesis":        decision.get("thesis", ""),
    }
    log_signal(_log_sig)
    log_position_opened(pos)

    try:
        from dashboard.state import add_signal_to_history
        add_signal_to_history(_log_sig)
    except Exception:
        pass

    print(
        f"[bot] Signal posted: {ticker} {decision.get('direction','?').upper()} "
        f"entry=${entry} stop=${stop} t2=${target} "
        f"confidence={decision.get('confidence','?')}",
        flush=True,
    )


async def _post_watch(channel, decision: dict, label: str = "") -> None:
    """Post a watchlist add for a 'watch' decision."""
    ticker  = decision["ticker"]
    trigger = decision.get("watch_trigger") or "No trigger specified"
    thesis  = decision.get("thesis", "")[:500]
    conf    = decision.get("confidence", 0)

    add_ticker(ticker)

    color = discord.Color.yellow()
    embed = discord.Embed(
        title=f"WATCH: {ticker} — {(decision.get('direction') or 'TBD').upper()}",
        color=color,
        description=f"**Confidence: {conf}/100** | Added to watchlist",
    )
    embed.add_field(name="Entry Trigger", value=trigger,          inline=False)
    embed.add_field(name="Thesis",        value=thesis,           inline=False)
    lr = decision.get("learnings_referenced", "")
    if lr:
        embed.add_field(name="Past Learning Applied", value=lr[:500], inline=False)
    embed.add_field(name="Setup Type", value=", ".join(decision.get("setup_type", [])) or "N/A", inline=True)

    prefix = f"**{label}** " if label else ""
    await channel.send(f"{prefix}", embed=embed) if label else await channel.send(embed=embed)
    print(f"[bot] Watch added: {ticker} | trigger: {trigger}", flush=True)


def _compute_rr(entry: float, stop: float, target: float) -> float:
    try:
        risk   = abs(entry - stop)
        reward = abs(target - entry)
        return round(reward / risk, 2) if risk > 0 else 0
    except Exception:
        return 0


# ── Startup ───────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    global _announced
    print(f"[bot] Online: {bot.user}", flush=True)
    if not _announced:
        _announced = True
        channel_id = int(os.environ["DISCORD_CHANNEL_ID"])
        channel = bot.get_channel(channel_id)
        if channel:
            upcoming = get_upcoming_events(hours_ahead=48)
            macro_str = (
                " | ".join(f"{e['name']} @ {e['_dt_formatted']}" for e in upcoming[:3])
                if upcoming else "None in next 48h"
            )
            await channel.send(
                f"**JC Claude Analyst Online**\n"
                f"Scan: every 60min | Top 10 candidates → Claude decides\n"
                f"Hard limits: market open, no trading 30min ±macro, max {MAX_CONCURRENT_POSITIONS} concurrent positions\n"
                f"Upcoming macro events: {macro_str}"
            )
        market_scan.start()
        position_monitor.start()
        morning_brief_task.start()
        friday_review.start()

# ── Mention handler ───────────────────────────────────────────────────────────

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _read_file(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""

_TRADE_SUMMARY = _read_file(
    os.environ.get("SUMMARY_MD_PATH") or os.path.join(_REPO_ROOT, "context", "summary.md")
)
_CLAUDE_MD = _read_file(
    os.environ.get("CLAUDE_MD_PATH") or os.path.join(_REPO_ROOT, "context", "project.md")
)

_MENTION_SYSTEM = (
    "You are Naisu, a swing trading assistant. You only discuss trading — stocks, options, GEX/DEX, "
    "technical analysis, risk management, market structure, and related topics. "
    "If asked about anything unrelated to trading, politely decline and redirect to trading. "
    "Be direct and conversational. No filler. Reference the rulebook when relevant.\n\n"
    "=== TRADING RULEBOOK ===\n"
    + (_TRADE_SUMMARY or "(no rulebook loaded)")
    + "\n=== END RULEBOOK ===\n\n"
    "=== PROJECT CONTEXT ===\n"
    + (_CLAUDE_MD or "(no project context loaded)")
    + "\n=== END PROJECT CONTEXT ==="
)

# Per-channel conversation history — in-memory, max 20 messages (10 turns) per channel
_chat_history: dict[int, list[dict]] = {}
_CHAT_MAX_MESSAGES = 20

_mention_client: _anthropic.Anthropic | None = None


def _get_mention_client() -> _anthropic.Anthropic:
    global _mention_client
    if _mention_client is None:
        _mention_client = _anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _mention_client


def _build_mention_context() -> str:
    """Attach a quick snapshot of positions + market regime as context."""
    lines = []
    try:
        from analysis.position_manager import get_position_summary
        positions = get_position_summary()
        if positions:
            for p in positions:
                pnl = p.get("pnl_pct", 0)
                lines.append(
                    f"{p['ticker']} {p['direction'].upper()} | PnL {pnl:+.1f}% | "
                    f"Stop ${p['stop_price']} | Target ${p['target_price']}"
                )
        else:
            lines.append("No open positions.")
    except Exception:
        lines.append("Positions unavailable.")

    try:
        from analysis.claude_analyst import get_market_context
        ctx = get_market_context()
        spy = ctx.get("spy", {})
        qqq = ctx.get("qqq", {})
        lines.append(
            f"SPY ${spy.get('price', 0):.2f} GEX:{spy.get('gex_regime','?')} | "
            f"QQQ ${qqq.get('price', 0):.2f} GEX:{qqq.get('gex_regime','?')} | "
            f"Regime: {ctx.get('regime','?')}"
        )
    except Exception:
        pass

    return "\n".join(lines)


@bot.listen("on_message")
async def on_message(message: discord.Message) -> None:
    print(f"[on_message] author={message.author} bot={message.author.bot} content={message.content!r} bot_user={bot.user}", flush=True)

    # Ignore messages from the bot itself
    if message.author.bot:
        return

    # Check if bot is mentioned — use raw content ID match as it's most reliable
    bot_id   = bot.user.id if bot.user else None
    content  = message.content or ""
    mentioned = (
        bot_id is not None and (
            f"<@{bot_id}>" in content or f"<@!{bot_id}>" in content
        )
    )
    print(f"[on_message] bot_id={bot_id} mentioned={mentioned}", flush=True)

    if not mentioned:
        return

    print(f"[bot] Mention from {message.author} in #{message.channel}: {content!r}", flush=True)

    # Strip all @mentions to isolate the question
    import re
    question = re.sub(r"<@!?\d+>", "", content).strip()

    if not question:
        await message.reply("What's up?", mention_author=False)
        return

    async with message.channel.typing():
        loop = asyncio.get_event_loop()
        try:
            _MENTION_MODEL = "claude-haiku-4-5-20251001"

            # Prepend live context (positions + market) to the user's message
            context   = await loop.run_in_executor(None, _build_mention_context)
            user_text = f"[Live context]\n{context}\n\n{question}" if context else question

            # Build history for this channel
            channel_id = message.channel.id
            history = _chat_history.setdefault(channel_id, [])
            history.append({"role": "user", "content": user_text})

            _rl_acquire("on_message(mention)")
            response = await loop.run_in_executor(
                None,
                lambda: _get_mention_client().messages.create(
                    model      = _MENTION_MODEL,
                    max_tokens = 600,
                    system     = _MENTION_SYSTEM,
                    messages   = history,
                ),
            )
            _rl_log("on_message(mention)", _MENTION_MODEL,
                    response.usage.input_tokens, response.usage.output_tokens)

            reply = response.content[0].text.strip()

            # Store assistant reply and trim history
            history.append({"role": "assistant", "content": reply})
            if len(history) > _CHAT_MAX_MESSAGES:
                _chat_history[channel_id] = history[-_CHAT_MAX_MESSAGES:]

            print(f"[bot] Mention reply: {reply!r}", flush=True)
        except RuntimeError as e:
            reply = f"Rate limit: {e}"
            print(f"[bot] Mention rate limit: {e}", flush=True)
        except Exception as e:
            reply = f"Error: {e}"
            print(f"[bot] Mention error: {e}", flush=True)

    await message.reply(reply, mention_author=False)


# ── Scheduled tasks ───────────────────────────────────────────────────────────

@tasks.loop(minutes=60, reconnect=True)
async def market_scan():
    """
    Every 10 min during market hours:
    Hard filter 1: market open?
    Hard filter 2: macro event window?
    Hard filter 3: position limit (5)?
    Then: pipeline → 50 candidates → Claude analyzes all → trade/watch/skip
    """
    # Hard filter 1: market hours
    if not _is_market_open():
        return

    channel_id = int(os.environ["DISCORD_CHANNEL_ID"])
    channel    = bot.get_channel(channel_id)
    if not channel:
        return

    # Hard filter 2: macro event window
    in_window, event_name = is_macro_window()
    if in_window:
        print(f"[market_scan] Skipping scan — macro event window: {event_name}", flush=True)
        return

    # Hard filter 3: position limit
    if _position_count() >= MAX_CONCURRENT_POSITIONS:
        print(f"[market_scan] At position limit ({MAX_CONCURRENT_POSITIONS}). No new trades.", flush=True)
        return

    global _signal_count_this_week
    if _signal_count_this_week >= MAX_SIGNALS_PER_WEEK:
        return

    # Fetch market context once for all candidates
    loop = asyncio.get_event_loop()
    try:
        market_ctx = await loop.run_in_executor(None, get_market_context)
    except Exception as e:
        print(f"[market_scan] Market context fetch failed: {e}", flush=True)
        market_ctx = {"spy": {}, "qqq": {}, "vix": 0.0, "regime": "unclear"}

    # Run pipeline
    try:
        candidates = await loop.run_in_executor(None, run_pipeline)
    except Exception as e:
        print(f"[market_scan] Pipeline error: {e}", flush=True)
        return

    if not candidates:
        return

    print(f"[market_scan] {describe_pipeline_run(candidates)}", flush=True)
    try:
        from dashboard.state import update_scan_state
        update_scan_state(candidates, _signal_count_this_week, MAX_SIGNALS_PER_WEEK)
    except Exception:
        pass

    eligible = [
        c for c in candidates
        if c["ticker"] not in _signals_this_week and not _has_open_position(c["ticker"])
    ]

    tickers_str = " | ".join(c["ticker"] for c in eligible)
    spy = market_ctx.get("spy", {})
    await channel.send(
        f"🔍 **Scan** — SPY ${spy.get('price', 0):.2f} {spy.get('gex_regime','?').upper()} GEX | "
        f"Regime: {market_ctx.get('regime','?')}\n"
        f"Analyzing: `{tickers_str}`"
    )

    results = []   # (ticker, action, confidence, thesis_snippet)
    trade_found = False

    for candidate in eligible:
        ticker = candidate["ticker"]

        if _position_count() >= MAX_CONCURRENT_POSITIONS:
            break
        in_window, event_name = is_macro_window()
        if in_window:
            break

        try:
            decision = await loop.run_in_executor(
                None, analyze_candidate, candidate, market_ctx, candidate.get("scan_rank", 0)
            )
        except Exception as e:
            print(f"[market_scan] Claude analysis failed for {ticker}: {e}", flush=True)
            results.append((ticker, "error", 0, str(e)[:80]))
            continue

        if not decision:
            results.append((ticker, "error", 0, "no response"))
            continue

        action     = decision.get("decision", "skip")
        confidence = decision.get("confidence", 0)
        thesis     = (decision.get("thesis") or "")[:80]
        results.append((ticker, action, confidence, thesis))

        if action == "trade":
            trade_found = True
            await _post_signal(channel, decision, candidate)
            if _signal_count_this_week >= MAX_SIGNALS_PER_WEEK:
                await channel.send(f"**Weekly signal cap ({MAX_SIGNALS_PER_WEEK}) reached. Resetting Monday.**")

        elif action == "watch":
            if ticker not in get_watchlist():
                add_ticker(ticker)

        if trade_found or _signal_count_this_week >= MAX_SIGNALS_PER_WEEK:
            break

    # Post compact results summary
    icons = {"trade": "🟢", "watch": "👁", "skip": "⬜", "error": "❌"}
    lines = []
    for ticker, action, conf, thesis in results:
        icon = icons.get(action, "⬜")
        lines.append(f"{icon} **{ticker}** {action.upper()} {conf}/100 — {thesis}")

    if lines:
        summary = "\n".join(lines)
        if not trade_found:
            summary += "\n\n_No trade taken this scan._"
        await channel.send(summary[:2000])

    # ── Manual watchlist scan ────────────────────────────────────────────────
    if _position_count() >= MAX_CONCURRENT_POSITIONS or _signal_count_this_week >= MAX_SIGNALS_PER_WEEK:
        return

    in_window, _ = is_macro_window()
    if in_window:
        return

    manual = get_manual_watchlist()
    print(f"[manual_scan] Checking {len(manual)} tickers: {', '.join(manual)}", flush=True)

    for ticker in manual:
        if ticker in _signals_this_week or _has_open_position(ticker):
            continue
        if _position_count() >= MAX_CONCURRENT_POSITIONS:
            break

        try:
            candidate = await loop.run_in_executor(None, build_candidate_for_ticker, ticker)
            if not candidate:
                continue
            decision = await loop.run_in_executor(
                None, analyze_candidate, candidate, market_ctx, 0
            )
        except Exception as e:
            print(f"[manual_scan] {ticker} error: {e}", flush=True)
            continue

        if not decision:
            continue

        action = decision.get("decision")
        if action == "trade":
            await _post_signal(channel, decision, candidate, label="Manual Watchlist")
        elif action == "watch":
            pass  # Already on manual watchlist, no need to re-post


@market_scan.before_loop
async def before_market_scan():
    """Wait 60 min before the first scan so startup doesn't immediately burn credits."""
    await asyncio.sleep(3600)


@tasks.loop(minutes=10)
async def position_monitor():
    """Check all open positions for stop/target hits every 10 min."""
    alerts_channel_id = int(os.environ.get("DISCORD_ALERTS_CHANNEL_ID", os.environ["DISCORD_CHANNEL_ID"]))
    channel = bot.get_channel(alerts_channel_id)
    if not channel:
        return

    loop = asyncio.get_event_loop()
    alerts = await loop.run_in_executor(None, check_all_positions)
    for alert in alerts:
        embed = _position_alert_embed(alert)
        await channel.send(embed=embed)

        closed = get_closed_positions()
        matching = [p for p in closed if p["id"] == alert.get("position_id")]
        if matching:
            pos  = matching[0]
            won  = pos.get("final_pnl_pct", 0) > 0
            reason = pos.get("status", "?").replace("_", " ")
            log_position_closed(pos)
            log_trade_learning(
                ticker=pos["ticker"],
                direction=pos["direction"],
                setup=pos.get("thesis", "")[:300],
                what_worked=f"R:R held, {reason}" if won else "N/A",
                what_failed="N/A" if won else f"Stopped out — {reason}",
                outcome=f"{pos.get('final_pnl_pct', 0):+.2f}% in {pos.get('days_held', 0)} days",
            )
            print(
                f"[bot] Position closed + learning written: {pos['ticker']} "
                f"{pos.get('final_pnl_pct', 0):+.2f}%",
                flush=True,
            )


@tasks.loop(minutes=1)
async def morning_brief_task():
    """Post morning brief at 9:00 AM ET (30 min before open) on weekdays."""
    now_et = datetime.now(ET)
    if now_et.weekday() >= 5:
        return
    if not (now_et.hour == 9 and now_et.minute == 0):
        return

    channel_id = int(os.environ.get("DISCORD_MORNING_CHANNEL_ID", os.environ["DISCORD_CHANNEL_ID"]))
    channel = bot.get_channel(channel_id)
    if not channel:
        return

    # Show upcoming macro events in the brief
    upcoming = get_upcoming_events(hours_ahead=48)
    if upcoming:
        macro_lines = "\n".join(f"  {e['name']} @ {e['_dt_formatted']}" for e in upcoming)
        await channel.send(f"**Macro Events (next 48h):**\n{macro_lines}")

    await channel.send("**--- Morning Brief ---**")
    try:
        loop = asyncio.get_event_loop()
        embeds = await loop.run_in_executor(None, build_morning_brief)
        for embed in embeds:
            await channel.send(embed=embed)
    except Exception as e:
        await channel.send(f"Morning brief error: {e}")
    print("[bot] Morning brief posted", flush=True)


@tasks.loop(minutes=1)
async def friday_review():
    """Post weekly review to Discord every Friday at 4:30 PM ET."""
    now_et = datetime.now(ET)
    if now_et.weekday() != 4:
        return
    if not (now_et.hour == 16 and now_et.minute == 30):
        return

    channel_id = int(os.environ["DISCORD_CHANNEL_ID"])
    channel    = bot.get_channel(channel_id)
    if not channel:
        return

    stats    = get_weekly_stats()
    open_pos = get_open_positions()
    write_weekly_review(stats, open_pos)

    trades   = stats.get("trades", 0)
    wins     = stats.get("wins", 0)
    losses   = stats.get("losses", 0)
    wr       = stats.get("win_rate", 0)
    avg_win  = stats.get("avg_win", 0)
    avg_loss = stats.get("avg_loss", 0)
    expectancy = round((wr / 100 * avg_win) + ((1 - wr / 100) * avg_loss), 2)

    color = discord.Color.green() if expectancy >= 0 else discord.Color.red()
    embed = discord.Embed(
        title=f"Weekly Review — {now_et.strftime('%B %d, %Y')}",
        color=color,
    )
    embed.add_field(name="Trades",     value=str(trades),           inline=True)
    embed.add_field(name="Win Rate",   value=f"{wr}%",              inline=True)
    embed.add_field(name="Expectancy", value=f"{expectancy:+.2f}%", inline=True)
    embed.add_field(name="Wins",       value=str(wins),             inline=True)
    embed.add_field(name="Losses",     value=str(losses),           inline=True)
    embed.add_field(name="Avg Win",    value=f"{avg_win:+.2f}%",    inline=True)
    embed.add_field(name="Avg Loss",   value=f"{avg_loss:+.2f}%",   inline=True)

    positions = stats.get("positions", [])
    if positions:
        trade_lines = []
        for p in positions:
            icon = "✅" if p.get("final_pnl_pct", 0) > 0 else "❌"
            trade_lines.append(
                f"{icon} **{p['ticker']}** {p['direction'].upper()} "
                f"| {p.get('final_pnl_pct', 0):+.2f}% "
                f"| {p.get('days_held', 0)}d "
                f"| {p.get('status','?').replace('_',' ')}"
            )
        embed.add_field(name="Trades This Week", value="\n".join(trade_lines)[:1000], inline=False)

    if open_pos:
        open_lines = [
            f"**{p['ticker']}** {p['direction'].upper()} | "
            f"Entry ${p['entry_price']} | PnL {p.get('pnl_pct',0):+.2f}% | Day {p.get('days_held',0)}"
            for p in open_pos
        ]
        embed.add_field(name="Still Open", value="\n".join(open_lines)[:500], inline=False)

    embed.set_footer(text="Full review written to Obsidian. Signal cap resetting Monday.")
    await channel.send("**--- Weekly Review ---**", embed=embed)

    global _signals_this_week, _signal_count_this_week
    _signals_this_week       = set()
    _signal_count_this_week  = 0
    print("[bot] Weekly review posted, signal cap reset", flush=True)


# ── Commands ──────────────────────────────────────────────────────────────────

@bot.command(name="signal")
async def signal_cmd(ctx, ticker: str):
    """!signal TSLA — full Claude swing analysis on demand."""
    ticker = ticker.upper()
    await ctx.send(f"Claude analyzing {ticker}... (fetching data + chart)")
    try:
        loop = asyncio.get_event_loop()
        candidate = await loop.run_in_executor(None, build_candidate_for_ticker, ticker)
        if not candidate:
            await ctx.send(f"**{ticker}** — failed to fetch data.")
            return

        market_ctx = await loop.run_in_executor(None, get_market_context)
        decision   = await loop.run_in_executor(
            None, analyze_candidate, candidate, market_ctx, 0, "claude-sonnet-4-6"
        )
        if not decision:
            await ctx.send(f"**{ticker}** — Claude analysis failed.")
            return

        embed = _signal_embed(decision)
        await ctx.send(embed=embed)

        # Always post chart for on-demand signals regardless of decision
        try:
            daily  = candidate.get("yf_metrics", {}).get("daily_df")
            gex    = candidate.get("gex", {})
            pivots = candidate.get("weekly_pivots", {})
            if daily is not None and not daily.empty:
                chart_buf = render_chart(ticker, daily, gex, pivots)
                chart_buf.seek(0)
                await ctx.send(file=discord.File(fp=chart_buf, filename=f"{ticker}_chart.png"))
        except Exception as e:
            await ctx.send(f"*(Chart render failed: {e})*")

    except Exception as e:
        await ctx.send(f"Error: {e}")


@bot.command(name="scan")
async def scan_cmd(ctx):
    """!scan — run full pipeline, post the top trade from Claude's analysis."""
    await ctx.send("Running full S&P 500 scan (top 10 → Claude)... ~30 sec")
    try:
        in_window, event_name = is_macro_window()
        if in_window:
            await ctx.send(f"**Scan blocked** — within 30min of {event_name}.")
            return

        loop = asyncio.get_event_loop()
        market_ctx = await loop.run_in_executor(None, get_market_context)
        candidates = await loop.run_in_executor(None, run_pipeline)
        if not candidates:
            await ctx.send("No candidates passed Tier 1 scan.")
            return

        await ctx.send(f"**{describe_pipeline_run(candidates)}**\nClaude analyzing...")

        best_trade  = None
        best_watch  = None
        best_cand_t = None
        best_cand_w = None

        for candidate in candidates:
            decision = await loop.run_in_executor(
                None, analyze_candidate, candidate, market_ctx, candidate.get("scan_rank", 0)
            )
            if not decision:
                continue
            action = decision.get("decision")
            if action == "trade" and best_trade is None:
                best_trade  = decision
                best_cand_t = candidate
            elif action == "watch" and best_watch is None:
                best_watch  = decision
                best_cand_w = candidate
            if best_trade:
                break  # found a trade, skip remaining

        if best_trade:
            await ctx.send("**Best trade setup:**")
            embed = _signal_embed(best_trade)
            await ctx.send(embed=embed)
            try:
                daily  = best_cand_t.get("yf_metrics", {}).get("daily_df")
                gex    = best_cand_t.get("gex", {})
                pivots = best_cand_t.get("weekly_pivots", {})
                if daily is not None and not daily.empty:
                    chart_buf = render_chart(best_trade["ticker"], daily, gex, pivots)
                    chart_buf.seek(0)
                    await ctx.send(file=discord.File(fp=chart_buf, filename=f"{best_trade['ticker']}_chart.png"))
            except Exception as e:
                await ctx.send(f"*(Chart failed: {e})*")
        elif best_watch:
            await ctx.send("**No trades — best watchlist setup:**")
            await _post_watch(ctx, best_watch)
        else:
            await ctx.send("**Claude said skip on all top candidates.** No setups right now.")

    except Exception as e:
        await ctx.send(f"Scan error: {e}")


@bot.command(name="chart")
async def chart_cmd(ctx, ticker: str):
    """!chart NVDA — daily chart with MA overlays and GEX levels."""
    ticker = ticker.upper()
    await ctx.send(f"Rendering chart for {ticker}...")
    try:
        tf    = _yfc.get_multi_tf_context(ticker)
        price = float(tf["daily"]["close"].iloc[-1]) if not tf["daily"].empty else 0.0
        gex   = _yfc.get_stub_gex(ticker, price)
        buf   = render_chart(ticker, tf["daily"], gex, tf["weekly_pivots"])
        buf.seek(0)
        await ctx.send(file=discord.File(fp=buf, filename=f"{ticker}_chart.png"))
    except Exception as e:
        await ctx.send(f"Chart error: {e}")


@bot.command(name="positions")
async def positions_cmd(ctx):
    """!positions — list all open swing trades."""
    positions = get_position_summary()
    if not positions:
        await ctx.send("No open positions.")
        return
    for pos in positions:
        embed = _open_position_embed(pos)
        await ctx.send(embed=embed)


@bot.command(name="open")
async def open_cmd(ctx, ticker: str, direction: str, entry: float, stop: float, target: float):
    """!open NVDA long 875 850 925 — manually record a position."""
    ticker    = ticker.upper()
    direction = direction.lower()
    if direction not in ("long", "short"):
        await ctx.send("Direction must be `long` or `short`.")
        return

    rr     = _compute_rr(entry, stop, target)
    thesis = f"Manually entered {direction} on {ticker}. R:R {rr:.1f}:1"
    pos    = open_position(
        ticker=ticker, direction=direction, entry_price=entry,
        stop_price=stop, target_price=target,
        conviction_score=0, thesis=thesis, signal={},
    )
    log_position_opened(pos)
    await ctx.send(
        f"**Position opened:** {ticker} {direction.upper()} | "
        f"Entry: ${entry} | Stop: ${stop} | Target: ${target} | "
        f"R:R: {rr:.1f}:1 | ID: `{pos['id']}`"
    )


@bot.command(name="close")
async def close_cmd(ctx, position_id: str, exit_price: float):
    """!close abc12345 880.50 — manually close a position."""
    closed = close_position(position_id, "manually_closed", exit_price)
    if not closed:
        await ctx.send(f"Position `{position_id}` not found.")
        return
    log_position_closed(closed)
    await ctx.send(
        f"**Position closed:** {closed['ticker']} | "
        f"Exit: ${exit_price} | PnL: {closed['final_pnl_pct']:+.2f}% | "
        f"Days: {closed['days_held']}"
    )


@bot.command(name="add")
async def add_cmd(ctx, ticker: str):
    """!add AMZN — add ticker to watchlist."""
    add_ticker(ticker)
    await ctx.send(f"**{ticker.upper()}** added to watchlist.")


@bot.command(name="remove")
async def remove_cmd(ctx, ticker: str):
    """!remove AMZN — remove from watchlist."""
    remove_ticker(ticker)
    await ctx.send(f"**{ticker.upper()}** removed from watchlist.")


@bot.command(name="watchlist")
async def watchlist_cmd(ctx):
    """!watchlist — show current tickers."""
    tickers = get_watchlist()
    await ctx.send(f"**Watchlist ({len(tickers)}):** {', '.join(tickers) if tickers else 'empty'}")


@bot.command(name="weekly")
async def weekly_cmd(ctx):
    """!weekly — trigger weekly review write to Obsidian now."""
    stats   = get_weekly_stats()
    open_ps = get_open_positions()
    write_weekly_review(stats, open_ps)
    await ctx.send(
        f"Weekly review written to Obsidian. "
        f"{stats.get('trades', 0)} trades | {stats.get('win_rate', 0)}% WR"
    )


@bot.command(name="macro")
async def macro_cmd(ctx):
    """!macro — show upcoming macro events (next 48h)."""
    upcoming = get_upcoming_events(hours_ahead=168)  # 1 week
    if not upcoming:
        await ctx.send("No macro events scheduled in the next 7 days. Add them to `macro_events.json`.")
        return
    lines = [f"**Macro Events (next 7 days):**"]
    for e in upcoming:
        lines.append(f"  `{e['_dt_formatted']}` — {e['name']}")
    await ctx.send("\n".join(lines))


# ── Embed builders ────────────────────────────────────────────────────────────

def _signal_embed(decision: dict) -> discord.Embed:
    """Build Discord embed from Claude's decision dict."""
    ticker    = decision.get("ticker", "?")
    direction = (decision.get("direction") or "?").upper()
    action    = decision.get("decision", "?").upper()
    confidence = decision.get("confidence", 0)

    if action == "TRADE":
        color = discord.Color.green() if direction == "LONG" else discord.Color.red()
        if confidence >= 85:
            color = discord.Color.gold()
        icon  = "🟢" if direction == "LONG" else "🔴"
        title = f"{icon} {action}: {ticker} — {direction}"
    elif action == "WATCH":
        color = discord.Color.yellow()
        title = f"👁 WATCH: {ticker} — {direction}"
    else:
        color = discord.Color.dark_grey()
        title = f"⬜ SKIP: {ticker}"

    embed = discord.Embed(
        title=title,
        color=color,
        description=f"**Confidence: {confidence}/100**",
    )

    entry  = decision.get("entry")
    stop   = decision.get("stop")
    t1     = decision.get("t1")
    t2     = decision.get("t2")
    t3     = decision.get("t3")

    if entry:
        embed.add_field(name="Entry",     value=f"${entry}",                     inline=True)
    if stop:
        embed.add_field(name="Stop",      value=f"${stop} ({decision.get('stop_basis','')})", inline=True)
    if entry and stop and t2:
        rr = _compute_rr(entry, stop, t2)
        embed.add_field(name="R:R",       value=f"{rr}:1",                       inline=True)
    if t1:
        embed.add_field(name="T1",        value=f"${t1}",                        inline=True)
    if t2:
        embed.add_field(name="T2",        value=f"${t2} ({decision.get('target_basis','')})", inline=True)
    if t3:
        embed.add_field(name="T3",        value=f"${t3}",                        inline=True)

    setup_types = ", ".join(decision.get("setup_type", [])) or "N/A"
    embed.add_field(name="Setup Type",    value=setup_types,                     inline=False)

    thesis = (decision.get("thesis") or "")[:900]
    embed.add_field(name="Thesis",        value=thesis,                          inline=False)

    lr = (decision.get("learnings_referenced") or "")[:500]
    if lr:
        embed.add_field(name="Past Learning Applied", value=lr,                  inline=False)

    inv = decision.get("invalidation")
    if inv:
        embed.add_field(name="Invalidation", value=inv[:200],                   inline=False)

    if action == "WATCH":
        trigger = decision.get("watch_trigger")
        if trigger:
            embed.add_field(name="Watch Trigger", value=trigger[:200],           inline=False)

    # Options contract recommendation (from LuminaFlow GEX strike analysis)
    opts = decision.get("options_contract")
    if opts and action == "TRADE":
        strike     = opts.get("strike", "?")
        dte        = opts.get("dte", "?")
        opt_type   = (opts.get("type") or "?").upper()
        alt        = opts.get("alternative")
        stk_why    = opts.get("strike_rationale", "")
        dte_why    = opts.get("dte_rationale", "")
        opt_value  = f"**{opt_type} ${strike} — {dte}DTE**\n"
        if stk_why:
            opt_value += f"Strike: {stk_why}\n"
        if dte_why:
            opt_value += f"Expiry: {dte_why}"
        if alt:
            opt_value += f"\nAlt: {alt}"
        embed.add_field(name="Options Contract", value=opt_value[:500], inline=False)

    risk = decision.get("risk_notes")
    if risk:
        embed.set_footer(text=f"Risk: {risk[:200]}")

    return embed


def _open_position_embed(pos: dict) -> discord.Embed:
    pnl   = pos.get("pnl_pct", 0)
    color = discord.Color.green() if pnl >= 0 else discord.Color.red()
    embed = discord.Embed(
        title=f"{pos['ticker']} — {pos['direction'].upper()} | Day {pos['days_held']}",
        color=color,
    )
    embed.add_field(name="Entry",   value=f"${pos['entry_price']}",   inline=True)
    embed.add_field(name="Current", value=f"${pos['current_price']}", inline=True)
    embed.add_field(name="PnL",     value=f"{pnl:+.2f}%",            inline=True)
    embed.add_field(name="Stop",    value=f"${pos['stop_price']}",   inline=True)
    embed.add_field(name="Target",  value=f"${pos['target_price']}", inline=True)
    embed.add_field(name="ID",      value=f"`{pos['id']}`",          inline=True)
    return embed


def _position_alert_embed(alert: dict) -> discord.Embed:
    event   = alert["type"]
    is_stop = "STOPPED" in event
    color   = discord.Color.red() if is_stop else discord.Color.gold()
    pnl     = alert.get("pnl_pct", 0)

    embed = discord.Embed(
        title=f"{'🛑' if is_stop else '🎯'} {event}: {alert['ticker']}",
        color=color,
    )
    embed.add_field(name="Direction",  value=alert["direction"].upper(), inline=True)
    embed.add_field(name="Exit Price", value=f"${alert['price']}",      inline=True)
    embed.add_field(name="PnL",        value=f"{pnl:+.2f}%",           inline=True)
    embed.add_field(name="Entry",      value=f"${alert['entry']}",     inline=True)
    embed.add_field(name="Days Held",  value=str(alert["days_held"]),  inline=True)
    return embed




def run():
    bot.run(os.environ["DISCORD_BOT_TOKEN"])
