"""
test_discord_post.py - one-shot: run pipeline, Claude analysis, post to Discord.

Bypasses the 70-conviction threshold so we can test on any market day.
Posts to DISCORD_CHANNEL_ID with a [TEST] label.

Usage:
  python test_discord_post.py              # top 3 pipeline, posts best candidate
  python test_discord_post.py --ticker NVDA  # force a specific ticker
  python test_discord_post.py --top 5     # scan top 5 and pick best
"""
import argparse
import asyncio
import json
import os
import sys

import discord
from dotenv import load_dotenv
load_dotenv()


def _strip_fence(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.startswith("json"):
            raw = raw[4:]
    return raw.strip()


def run_pipeline_and_analyze(ticker_override: str | None, top_n: int) -> tuple[dict, dict]:
    """
    Run the full pipeline + Claude.
    Returns (candidate, claude_result).
    Bypasses conviction threshold - takes the top scorer.
    """
    from analysis.prompts import build_signal_messages
    from analysis.scoring import score_signal, CONVICTION_THRESHOLD
    from analysis.signal_builder import _compute_trade_levels
    from analysis.gex_analysis import get_nearest_gex_level
    import anthropic
    import pandas as pd

    # ── Pipeline ───────────────────────────────────────────────────────────────
    if ticker_override:
        from scanner.pipeline import _enrich_ticker
        from scanner.yf_scanner import batch_scan
        print(f"[pipeline] Scanning single ticker: {ticker_override}")
        # yfinance single-ticker download breaks — add SPY as dummy second ticker
        dummy = "SPY" if ticker_override != "SPY" else "QQQ"
        yf_results = batch_scan([ticker_override, dummy], top_n=2)
        yf_results = [c for c in yf_results if c["ticker"] == ticker_override]
        if not yf_results:
            print(f"No yfinance data for {ticker_override}. Trying with dummy metrics.")
            # Minimal fallback so we can still hit Polygon/LuminaFlow
            yf_results = [{"ticker": ticker_override, "yf_score": 0, "yf_metrics": {
                "current_price": 0, "ma20": 0, "ma50": 0, "rel_volume": 1.0,
                "momentum_10d": 0.0, "pct_vs_ma20": 0.0, "pct_vs_ma50": 0.0,
                "week52_high": 0.0, "dist_52w_pct": 0.0, "score_breakdown": {},
                "daily_df": pd.DataFrame(),
            }}]
        candidate = _enrich_ticker(ticker_override, yf_results[0])
        if not candidate:
            raise RuntimeError(f"Could not enrich {ticker_override}")
    else:
        from scanner.pipeline import run_pipeline
        print(f"[pipeline] Running full pipeline (top {top_n})...")
        candidates = run_pipeline(top_n=top_n)
        if not candidates:
            raise RuntimeError("Pipeline returned no candidates")
        candidate = candidates[0]   # highest yf_score

    ticker    = candidate["ticker"]
    price     = candidate.get("real_time_price", 0)
    yf        = candidate.get("yf_metrics", {})
    daily     = yf.get("daily_df", pd.DataFrame())
    four_h_raw = candidate.get("polygon_4h")
    one_h_raw  = candidate.get("polygon_1h")
    four_h = four_h_raw if four_h_raw is not None and not (hasattr(four_h_raw, "empty") and four_h_raw.empty) else pd.DataFrame()
    one_h  = one_h_raw  if one_h_raw  is not None and not (hasattr(one_h_raw,  "empty") and one_h_raw.empty)  else pd.DataFrame()
    pivots    = candidate.get("weekly_pivots") or {}
    gex       = candidate.get("gex", {})
    flow_bias = candidate.get("flow_bias", {})

    print(f"[pipeline] Selected: {ticker}  ${price}  GEX: {gex.get('gex_regime','?')}  DEX: {flow_bias.get('bias','?')}")

    # ── Score (best-effort — levels may be None if data is thin) ──────────────
    entry, stop, target = _compute_trade_levels(price, daily, four_h, one_h, gex, pivots, flow_bias)
    if entry is None:
        # Weekend / no 4H data fallback: use GEX walls as levels
        entry  = price
        stop   = gex.get("put_wall") or round(price * 0.97, 2)
        target = gex.get("call_wall") or round(price * 1.05, 2)

    scored = score_signal(
        ticker=ticker, price=price, daily_df=daily, four_h_df=four_h,
        gex=gex, flow_bias=flow_bias, entry=entry, stop=stop, target=target,
    )

    candidate["scored"] = {
        "total_score": scored["total_score"],
        "entry":       entry,
        "stop":        stop,
        "target":      target,
        "rr_ratio":    scored["rr_ratio"],
        "direction":   scored["direction"],
        "components":  scored["components"],
    }

    print(f"[scoring]  Score: {scored['total_score']}/100  "
          f"Entry: ${entry}  Stop: ${stop}  Target: ${target}  R:R: {scored['rr_ratio']}:1")

    # ── Market context ─────────────────────────────────────────────────────────
    from data.polygon_client import get_snapshot
    from analysis.gex_analysis import analyze_exposure

    market_ctx = {"spy": {}, "qqq": {}, "vix": 20, "regime": "unclear"}
    for idx in ["SPY", "QQQ"]:
        try:
            snap = get_snapshot(idx)
            p    = snap.day.close if snap and snap.day else 0
            g    = analyze_exposure(idx)
            market_ctx[idx.lower()] = {
                "price":      p,
                "gex_regime": g.get("gex_regime", "?"),
                "gex_flip":   g.get("gex_flip"),
                "king_above": g.get("king_above"),
                "king_below": g.get("king_below"),
                "trend":      "N/A",
            }
        except Exception as e:
            print(f"  [market ctx] {idx}: {e}")

    try:
        vix_snap = get_snapshot("VIX")
        market_ctx["vix"] = vix_snap.day.close if vix_snap and vix_snap.day else 20
    except Exception:
        pass

    # ── Claude ─────────────────────────────────────────────────────────────────
    print(f"[claude]   Calling Claude for {ticker}...")
    client  = anthropic.Anthropic()
    payload = build_signal_messages(candidate, market_ctx)
    resp    = client.messages.create(
        model      = "claude-opus-4-6",
        max_tokens = 1200,
        system     = payload["system"],
        messages   = payload["messages"],
    )
    raw    = _strip_fence(resp.content[0].text)
    result = json.loads(raw)

    print(f"[claude]   Decision: {result.get('decision','?').upper()}  "
          f"Confidence: {result.get('confidence','?')}/100")
    print(f"[claude]   Setup: {result.get('setup_type', [])}")
    print(f"[claude]   Thesis: {result.get('thesis','')[:120]}")

    return candidate, result, market_ctx


def build_claude_embed(candidate: dict, result: dict, test: bool = True) -> discord.Embed:
    """Build a rich Discord embed from Claude's JSON output."""
    ticker    = result.get("ticker", candidate["ticker"])
    decision  = result.get("decision", "skip").upper()
    direction = (result.get("direction") or "").upper()
    confidence = result.get("confidence", 0)
    thesis    = result.get("thesis", "No thesis.")
    entry_z   = result.get("entry_zone") or {}
    stop      = result.get("stop_loss")
    stop_b    = result.get("stop_basis", "")
    target    = result.get("target")
    target_b  = result.get("target_basis", "")
    setup     = result.get("setup_type", [])
    inv       = result.get("invalidation", "")
    mkt_note  = result.get("market_context_note", "")
    risk      = result.get("risk_notes", "")
    key_lvls  = result.get("key_levels", {})

    gex = candidate.get("gex", {})
    scored = candidate.get("scored", {})

    # Color by decision
    if decision == "SIGNAL":
        color = discord.Color.green() if direction == "LONG" else discord.Color.red()
    elif decision == "WATCHLIST":
        color = discord.Color.gold()
    else:
        color = discord.Color.greyple()

    prefix = "[TEST] " if test else ""
    emoji  = {"SIGNAL": "SIGNAL", "WATCHLIST": "WATCH", "SKIP": "SKIP"}.get(decision, decision)

    title = f"{prefix}{emoji}: {ticker}"
    if direction:
        title += f" {direction}"

    embed = discord.Embed(
        title       = title,
        color       = color,
        description = f"**Confidence: {confidence}/100**  |  Score: {scored.get('total_score', '?')}/100\n{thesis}",
    )

    # Trade levels
    if decision != "SKIP" and entry_z:
        embed.add_field(
            name  = "Entry Zone",
            value = f"${entry_z.get('low','?')} - ${entry_z.get('high','?')}",
            inline = True,
        )
    if stop is not None:
        embed.add_field(name="Stop Loss", value=f"${stop}\n*{stop_b}*", inline=True)
    if target is not None:
        embed.add_field(name="Target",    value=f"${target}\n*{target_b}*", inline=True)

    # RR
    if stop and target and entry_z.get("low"):
        entry_mid = (entry_z.get("low", 0) + entry_z.get("high", 0)) / 2
        if entry_mid != stop:
            rr = round(abs(target - entry_mid) / abs(entry_mid - stop), 2)
            embed.add_field(name="R:R", value=f"{rr}:1", inline=True)

    # GEX context
    embed.add_field(
        name  = "GEX / DEX",
        value = (
            f"Regime: **{gex.get('gex_regime','?').upper()}**\n"
            f"Flip: ${key_lvls.get('gamma_flip') or gex.get('gex_flip') or 'N/A'}\n"
            f"Call wall: ${key_lvls.get('call_wall') or gex.get('call_wall') or 'N/A'}\n"
            f"Put wall: ${key_lvls.get('put_wall') or gex.get('put_wall') or 'N/A'}"
        ),
        inline = True,
    )

    # King levels
    king_a = key_lvls.get("king_above") or gex.get("king_above")
    king_b = key_lvls.get("king_below") or gex.get("king_below")
    if king_a or king_b:
        embed.add_field(
            name  = "King Levels",
            value = f"Above: ${king_a or 'N/A'}\nBelow: ${king_b or 'N/A'}",
            inline = True,
        )

    # Setup tags
    if setup:
        embed.add_field(name="Setup Type", value=", ".join(setup), inline=False)

    # Invalidation
    if inv:
        embed.add_field(name="Invalidation", value=inv, inline=False)

    # Market note
    if mkt_note:
        embed.add_field(name="Market Context", value=mkt_note, inline=False)

    # Risk notes
    if risk:
        embed.add_field(name="Risk", value=risk, inline=False)

    # Score breakdown in footer
    comp = scored.get("components", {})
    if comp:
        breakdown = "  |  ".join([
            f"{k.replace('_',' ').title()}: {v.get('score','?')}/{v.get('max','?')}"
            for k, v in comp.items()
        ])
        embed.set_footer(text=breakdown)

    return embed


async def post_to_discord(embed: discord.Embed):
    channel_id = int(os.environ["DISCORD_CHANNEL_ID"])
    token      = os.environ["DISCORD_BOT_TOKEN"]

    intents = discord.Intents.default()
    client  = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        try:
            channel = client.get_channel(channel_id)
            if not channel:
                print(f"[discord] Channel {channel_id} not found - fetching...")
                channel = await client.fetch_channel(channel_id)
            await channel.send(embed=embed)
            print(f"[discord] Posted to #{channel.name}")
        except Exception as e:
            print(f"[discord] Post failed: {e}")
        finally:
            await client.close()

    await client.start(token)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", type=str, default=None, help="Force a specific ticker")
    parser.add_argument("--top",    type=int, default=3,    help="Pipeline top N (default 3)")
    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("  DISCORD TEST POST")
    print("=" * 60 + "\n")

    # Check keys
    for k in ["POLYGON_API_KEY", "LUMINAFLOW_API_KEY", "ANTHROPIC_API_KEY", "DISCORD_BOT_TOKEN", "DISCORD_CHANNEL_ID"]:
        if not os.environ.get(k):
            print(f"Missing: {k}")
            sys.exit(1)

    try:
        candidate, claude_result, market_ctx = run_pipeline_and_analyze(
            ticker_override = args.ticker.upper() if args.ticker else None,
            top_n           = args.top,
        )
    except json.JSONDecodeError as e:
        print(f"Claude returned invalid JSON: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Pipeline/analysis error: {e}")
        import traceback; traceback.print_exc()
        sys.exit(1)

    embed = build_claude_embed(candidate, claude_result, test=True)

    print("\n[discord] Posting embed...")
    asyncio.run(post_to_discord(embed))
    print("[discord] Done.\n")


if __name__ == "__main__":
    main()
