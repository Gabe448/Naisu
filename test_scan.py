"""
test_scan.py — one full scan cycle, terminal only, no Discord.

Flow:
  1. Pipeline: yfinance broad scan → Polygon enrichment → LuminaFlow GEX
  2. Score each candidate
  3. Call Claude for any that pass conviction threshold
  4. Print everything structured to console

Usage:
  python test_scan.py              # full SP500 pipeline (top 10)
  python test_scan.py --watchlist  # watchlist-only, skips pipeline
  python test_scan.py --top 5      # override PIPELINE_TOP_N
  python test_scan.py --no-claude  # skip Claude, just print scores
"""
import argparse
import json
import os
import sys
import textwrap
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

# ── helpers ───────────────────────────────────────────────────────────────────

LINE = "-" * 64
DLINE = "=" * 64


def header(title: str):
    print(f"\n{DLINE}")
    print(f"  {title}")
    print(DLINE)


def subheader(title: str):
    print(f"\n{LINE}")
    print(f"  {title}")
    print(LINE)


def print_candidate(c: dict, idx: int):
    ticker  = c["ticker"]
    price   = c.get("real_time_price", 0)
    yf_sc   = c.get("yf_score", 0)
    flow    = c.get("flow_bias", {})
    gex     = c.get("gex", {})
    scored  = c.get("scored", {})

    print(f"\n  [{idx}] {ticker:6}  ${price:<8.2f}  YF Score: {yf_sc}")
    print(f"       GEX: {gex.get('gex_regime','?').upper():<10} "
          f"Flip: ${gex.get('gex_flip') or 'N/A'}  "
          f"Call wall: ${gex.get('call_wall') or gex.get('gex_wall') or 'N/A'}  "
          f"Put wall: ${gex.get('put_wall') or 'N/A'}")
    print(f"       DEX: {flow.get('bias','neutral').upper():<10} "
          f"Strength: {flow.get('strength', 0):.0%}  "
          f"King^: ${gex.get('king_above') or 'N/A'}  "
          f"Kingv: ${gex.get('king_below') or 'N/A'}")

    if scored:
        total = scored.get("total_score", 0)
        rr    = scored.get("rr_ratio", 0)
        entry = scored.get("entry", 0)
        stop  = scored.get("stop", 0)
        tgt   = scored.get("target", 0)
        comp  = scored.get("components", {})
        print(f"       Score: {total}/100  R:R {rr}:1  "
              f"Entry: ${entry}  Stop: ${stop}  Target: ${tgt}")
        print(f"       TF:{comp.get('timeframe_alignment',{}).get('score','?')}/35  "
              f"GEX:{comp.get('gex_setup',{}).get('score','?')}/25  "
              f"DEX:{comp.get('dex_positioning',{}).get('score','?')}/25  "
              f"RR:{comp.get('risk_reward',{}).get('score','?')}/15")

    news = c.get("news", [])
    if news:
        print(f"       News ({len(news)}):")
        for n in news[:3]:
            ts  = n.get("published_utc", n.get("timestamp", ""))[:16]
            ttl = n.get("title", n.get("headline", ""))[:90]
            print(f"         [{ts}] {ttl}")


def print_claude_result(ticker: str, result: dict):
    decision   = result.get("decision", "?").upper()
    direction  = (result.get("direction") or "").upper()
    confidence = result.get("confidence", 0)
    thesis     = result.get("thesis", "")
    entry_z    = result.get("entry_zone", {})
    stop       = result.get("stop_loss")
    stop_basis = result.get("stop_basis", "")
    target     = result.get("target")
    tgt_basis  = result.get("target_basis", "")
    setup_type = result.get("setup_type", [])
    inv        = result.get("invalidation", "")
    risk_notes = result.get("risk_notes", "")
    mkt_note   = result.get("market_context_note", "")

    color = {"SIGNAL": "[GO]", "WATCHLIST": "[WATCH]", "SKIP": "[SKIP]"}.get(decision, "[?]")

    print(f"\n  {color} CLAUDE -> {ticker}: {decision}  |  {direction}  |  Confidence: {confidence}/100")
    print(f"  Setup: {', '.join(setup_type) if setup_type else 'N/A'}")
    print()

    if thesis:
        for line in textwrap.wrap(thesis, width=60, initial_indent="  ", subsequent_indent="  "):
            print(line)

    if decision != "SKIP":
        e_low  = entry_z.get("low", "?")
        e_high = entry_z.get("high", "?")
        print(f"\n  Entry zone:  ${e_low} - ${e_high}")
        print(f"  Stop:        ${stop}  ({stop_basis})")
        print(f"  Target:      ${target}  ({tgt_basis})")

    if inv:
        print(f"\n  Invalidation: {inv}")
    if mkt_note:
        print(f"  Market note:  {mkt_note}")
    if risk_notes:
        print(f"  Risk:         {risk_notes}")


# ── market context builder ────────────────────────────────────────────────────

def get_market_context() -> dict:
    """Pull SPY, QQQ, VIX context for the prompts."""
    from data.polygon_client import get_snapshot
    from analysis.gex_analysis import analyze_exposure

    ctx = {"spy": {}, "qqq": {}, "vix": 20, "regime": "unclear"}

    for idx in ["SPY", "QQQ"]:
        try:
            snap = get_snapshot(idx)
            price = snap.day.close if snap and snap.day else 0
            gex = analyze_exposure(idx)
            key = idx.lower()
            ctx[key] = {
                "price":       price,
                "gex_regime":  gex.get("gex_regime", "unknown"),
                "gex_flip":    gex.get("gex_flip"),
                "king_above":  gex.get("king_above"),
                "king_below":  gex.get("king_below"),
                "trend":       "N/A",
            }
        except Exception as e:
            print(f"  [market ctx] {idx} error: {e}")

    try:
        snap = get_snapshot("VIX")
        ctx["vix"] = snap.day.close if snap and snap.day else 20
    except Exception:
        pass

    # Determine regime
    spy_above_flip = (
        ctx["spy"].get("price", 0) > (ctx["spy"].get("gex_flip") or 0)
        if ctx["spy"].get("gex_flip") else None
    )
    spy_regime = ctx["spy"].get("gex_regime", "unknown")
    ctx["regime"] = (
        "trending_bullish" if spy_regime == "negative" and spy_above_flip
        else "trending_bearish" if spy_regime == "negative" and spy_above_flip is False
        else "pinning" if spy_regime == "positive"
        else "unclear"
    )

    return ctx


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Test scan cycle - no Discord")
    parser.add_argument("--watchlist", action="store_true", help="Watchlist-only scan")
    parser.add_argument("--top",       type=int, default=None, help="Override PIPELINE_TOP_N")
    parser.add_argument("--no-claude", action="store_true", help="Skip Claude analysis")
    args = parser.parse_args()

    header(f"TEST SCAN  {datetime.now().strftime('%Y-%m-%d  %H:%M:%S')}")

    # ── Check env ──────────────────────────────────────────────────────────────
    missing = [k for k in ["POLYGON_API_KEY", "LUMINAFLOW_API_KEY", "ANTHROPIC_API_KEY"]
               if not os.environ.get(k)]
    if missing:
        print(f"\n  [WARNING] Missing env vars: {', '.join(missing)}")
        if "ANTHROPIC_API_KEY" in missing and not args.no_claude:
            print("     Claude analysis will be skipped (--no-claude implied)")
            args.no_claude = True
        if any(k in missing for k in ["POLYGON_API_KEY", "LUMINAFLOW_API_KEY"]):
            print("     Cannot run pipeline without Polygon + LuminaFlow keys.")
            sys.exit(1)

    # ── Tier 1 + 2 + 3 pipeline ───────────────────────────────────────────────
    if args.watchlist:
        subheader("MODE: Watchlist scan (skipping SP500 pipeline)")
        from scanner.watchlist import get_watchlist
        from scanner.pipeline import _enrich_ticker
        from scanner.yf_scanner import batch_scan

        tickers = get_watchlist()
        print(f"  Watchlist: {', '.join(tickers)}")
        yf_results = batch_scan(tickers, top_n=len(tickers))
        yf_by_ticker = {c["ticker"]: c for c in yf_results}
        candidates = []
        for t in tickers:
            if t not in yf_by_ticker:
                print(f"  [{t}] no yf data - skipped")
                continue
            try:
                enriched = _enrich_ticker(t, yf_by_ticker[t])
                if enriched:
                    candidates.append(enriched)
            except Exception as e:
                print(f"  [{t}] enrichment error: {e}")
    else:
        top_n = args.top or int(os.environ.get("PIPELINE_TOP_N", 10))
        subheader(f"MODE: Full SP500 pipeline  ->  top {top_n}")
        from scanner.pipeline import run_pipeline
        candidates = run_pipeline(top_n=top_n)

    print(f"\n  Pipeline returned {len(candidates)} candidate(s).")

    if not candidates:
        print("  Nothing to score. Check API keys and connectivity.")
        sys.exit(0)

    # ── Score each candidate ──────────────────────────────────────────────────
    subheader("SCORING")
    from analysis.signal_builder import build_signal_from_candidate
    from analysis.scoring import CONVICTION_THRESHOLD

    scored_candidates  = []
    high_conviction    = []

    for c in candidates:
        try:
            sig = build_signal_from_candidate(c)
            if sig:
                # Attach scored dict back onto candidate for prompt builder
                c["scored"] = {
                    "total_score": sig["conviction_score"],
                    "entry":       sig["entry"],
                    "stop":        sig["stop"],
                    "target":      sig["target"],
                    "rr_ratio":    sig["rr_ratio"],
                    "direction":   sig["direction"],
                    "components":  sig["components"],
                    "is_high_conviction": True,
                }
                c["signal"] = sig
                high_conviction.append(c)
            else:
                # Still attach partial score if we can
                c["scored"] = {}
            scored_candidates.append(c)
        except Exception as e:
            c["scored"] = {}
            scored_candidates.append(c)
            print(f"  [{c['ticker']}] scoring error: {e}")

    for i, c in enumerate(scored_candidates, 1):
        print_candidate(c, i)

    print(f"\n  High-conviction (>= {CONVICTION_THRESHOLD}): {len(high_conviction)}")

    # ── Market context (needed for Claude prompt) ─────────────────────────────
    if not args.no_claude and high_conviction:
        subheader("MARKET CONTEXT")
        print("  Fetching SPY / QQQ / VIX...")
        try:
            market_ctx = get_market_context()
            spy = market_ctx.get("spy", {})
            qqq = market_ctx.get("qqq", {})
            print(f"  SPY: ${spy.get('price','?')}  GEX: {spy.get('gex_regime','?').upper()}  "
                  f"Flip: ${spy.get('gex_flip','?')}")
            print(f"  QQQ: ${qqq.get('price','?')}  GEX: {qqq.get('gex_regime','?').upper()}  "
                  f"Flip: ${qqq.get('gex_flip','?')}")
            print(f"  VIX: {market_ctx.get('vix','?')}  Regime: {market_ctx.get('regime','?')}")
        except Exception as e:
            print(f"  Market context error: {e}")
            market_ctx = {"spy": {}, "qqq": {}, "vix": 20, "regime": "unclear"}

    # ── Claude analysis ───────────────────────────────────────────────────────
    if args.no_claude:
        subheader("SKIPPING CLAUDE (--no-claude)")
    elif not high_conviction:
        subheader("CLAUDE - no high-conviction candidates to analyze")
    else:
        subheader(f"CLAUDE ANALYSIS  ({len(high_conviction)} candidate(s))")
        import anthropic
        from analysis.prompts import build_signal_messages

        client = anthropic.Anthropic()

        for c in high_conviction:
            ticker = c["ticker"]
            print(f"\n  Calling Claude for {ticker}...")
            try:
                payload  = build_signal_messages(c, market_ctx)
                response = client.messages.create(
                    model      = "claude-opus-4-6",
                    max_tokens = 1024,
                    system     = payload["system"],
                    messages   = payload["messages"],
                )
                raw = response.content[0].text.strip()

                # Strip any accidental markdown fences
                if raw.startswith("```"):
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]

                result = json.loads(raw)
                c["claude_result"] = result
                print_claude_result(ticker, result)

            except json.JSONDecodeError as e:
                print(f"  [{ticker}] Claude returned invalid JSON: {e}")
                print(f"  Raw: {raw[:300]}")
            except Exception as e:
                print(f"  [{ticker}] Claude error: {e}")

    # ── Summary ───────────────────────────────────────────────────────────────
    header("SCAN SUMMARY")

    signals   = [c for c in high_conviction if c.get("claude_result", {}).get("decision") == "signal"]
    watchlist = [c for c in high_conviction if c.get("claude_result", {}).get("decision") == "watchlist"]
    skipped   = [c for c in high_conviction if c.get("claude_result", {}).get("decision") == "skip"]
    no_claude = [c for c in high_conviction if "claude_result" not in c]

    print(f"\n  Scanned:          {len(candidates)}")
    print(f"  High-conviction:  {len(high_conviction)}")
    print(f"  Claude SIGNAL:    {len(signals)}")
    print(f"  Claude WATCHLIST: {len(watchlist)}")
    print(f"  Claude SKIP:      {len(skipped)}")
    if no_claude:
        print(f"  No Claude result: {len(no_claude)}")

    if signals:
        print(f"\n  [GO] SIGNALS:")
        for c in signals:
            r  = c["claude_result"]
            ez = r.get("entry_zone", {})
            print(f"     {c['ticker']:6} {(r.get('direction') or '').upper():5}  "
                  f"Entry ${ez.get('low','?')}-${ez.get('high','?')}  "
                  f"Stop ${r.get('stop_loss','?')}  "
                  f"Target ${r.get('target','?')}  "
                  f"Conf {r.get('confidence','?')}/100")

    if watchlist:
        print(f"\n  [WATCH] WATCHLIST:")
        for c in watchlist:
            r = c["claude_result"]
            print(f"     {c['ticker']:6}  {r.get('thesis','')[:80]}")

    print(f"\n  Done  {datetime.now().strftime('%H:%M:%S')}\n")


if __name__ == "__main__":
    main()
