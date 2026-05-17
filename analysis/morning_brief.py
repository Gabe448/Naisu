"""
Morning brief — runs at market open, posts a structured daily briefing to Discord.

Covers:
  1. Open positions: current price, PnL, distance to stop/target, days held
  2. Thesis check: has anything changed since entry? (GEX regime shift, flow reversal)
  3. Market context: SPY/QQQ GEX regime + weekly pivot position
  4. Watchlist scan: any NEW high-conviction setups forming pre-market?
"""
import discord
from datetime import datetime

import data.yf_client as _yfc
from analysis.gex_analysis import analyze_exposure, extract_dex_bias
from analysis.position_manager import get_position_summary


MARKET_INDICES = ["SPY", "QQQ"]


def build_morning_brief() -> list[discord.Embed]:
    """Returns a list of embeds to send to Discord."""
    embeds = []

    # ── Header ───────────────────────────────────────────────────────────────
    header = discord.Embed(
        title=f"Morning Brief — {datetime.now().strftime('%A, %B %d %Y')}",
        color=discord.Color.blurple(),
        description="Market open. Reviewing positions and structure.",
    )

    # ── Market context ────────────────────────────────────────────────────────
    market_lines = []
    for idx in MARKET_INDICES:
        try:
            price = _yfc.get_price(idx)
            tf    = _yfc.get_multi_tf_context(idx)
            daily = tf["daily"]
            bias  = _yfc.get_momentum_flow_bias(idx, daily)
            ma20  = round(float(daily["ma20"].iloc[-1]), 2) if not daily.empty else "?"
            market_lines.append(
                f"**{idx}** ${price:.2f} | MA20: ${ma20} | Flow: {bias['bias'].upper()} ({bias['strength']:.0%})"
            )
        except Exception as e:
            market_lines.append(f"**{idx}** — error ({e})")

    header.add_field(
        name="Market Structure",
        value="\n".join(market_lines) or "No data",
        inline=False,
    )
    embeds.append(header)

    # ── Open positions ────────────────────────────────────────────────────────
    positions = get_position_summary()
    if not positions:
        pos_embed = discord.Embed(
            title="Open Positions",
            description="No open positions.",
            color=discord.Color.light_grey(),
        )
        embeds.append(pos_embed)
    else:
        for pos in positions:
            embeds.append(_position_embed(pos))

    return embeds


def _position_embed(pos: dict) -> discord.Embed:
    ticker    = pos["ticker"]
    direction = pos["direction"].upper()
    entry     = pos["entry_price"]
    stop      = pos["stop_price"]
    target    = pos["target_price"]
    current   = pos["current_price"]
    pnl       = pos["pnl_pct"]
    days      = pos["days_held"]
    score     = pos.get("conviction_score", "?")

    color = discord.Color.green() if pnl >= 0 else discord.Color.red()

    dist_to_stop   = round(abs(current - stop) / current * 100, 2)
    dist_to_target = round(abs(target - current) / current * 100, 2)

    # Thesis validity check
    thesis_status = _check_thesis_still_valid(pos, current)

    embed = discord.Embed(
        title=f"{ticker} — {direction} | Day {days}",
        color=color,
    )
    embed.add_field(name="Entry",    value=f"${entry}",         inline=True)
    embed.add_field(name="Current",  value=f"${current}",       inline=True)
    embed.add_field(name="PnL",      value=f"{pnl:+.2f}%",      inline=True)
    embed.add_field(name="Stop",     value=f"${stop} ({dist_to_stop}% away)",   inline=True)
    embed.add_field(name="Target",   value=f"${target} ({dist_to_target}% away)", inline=True)
    embed.add_field(name="Score",    value=str(score),           inline=True)
    embed.add_field(name="Thesis Check", value=thesis_status,   inline=False)

    updates = pos.get("thesis_updates", [])
    if updates:
        last = updates[-1]
        embed.add_field(name="Last Update", value=last["update"][:200], inline=False)

    return embed


def _check_thesis_still_valid(pos: dict, current_price: float) -> str:
    """
    Quick validity check: has GEX regime or DEX flow flipped since entry?
    Returns a plain-English status string.
    """
    ticker    = pos["ticker"]
    direction = pos["direction"]
    issues    = []

    try:
        gex = analyze_exposure(ticker)
        regime = gex["gex_regime"]
        if direction == "long" and regime == "positive":
            issues.append("GEX flipped POSITIVE (mean-reverting — reduce size or tighten stop)")
        elif direction == "short" and regime == "negative":
            issues.append("GEX still negative but short — confirm dealer positioning")

        dex_bias = extract_dex_bias(gex)
        bias     = dex_bias.get("bias", "neutral")
        strength = dex_bias.get("strength", 0.0)
        if direction == "long" and bias == "bearish" and strength >= 0.4:
            issues.append(f"DEX turned BEARISH ({strength:.0%}) — dealers repositioning short")
        elif direction == "short" and bias == "bullish" and strength >= 0.4:
            issues.append(f"DEX turned BULLISH ({strength:.0%}) — monitor for squeeze")
    except Exception as e:
        return f"Could not verify ({e})"

    return " | ".join(issues) if issues else "Thesis intact — no regime change detected."
