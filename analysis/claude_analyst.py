"""
Claude analyst — the new decision layer.

No scoring rules. No thresholds. No gates except the 3 hard filters.

Claude receives:
  - Full trade learnings (learnings.md from Obsidian)
  - Recent closed trade history (last 20)
  - Raw price bars: daily (20), 4H (10), 1H (10)
  - Raw volume data embedded in price bars
  - All GEX/DEX/VEX dealer exposure data
  - News (if available)
  - Chart image (PNG, base64 encoded)
  - Market context (SPY, QQQ, VIX)

Claude decides: trade / watch / skip
Hard filters applied by caller (bot.py), not here:
  - Market must be open
  - No trading 30min around macro events
  - Max 5 concurrent positions
"""
import base64
import json
import os
from typing import Optional

import anthropic

from memory.position_store import get_closed_positions
from memory.obsidian_writer import _vault_path
from utils.claude_limiter import acquire as _rl_acquire, log_call as _rl_log
from analysis.prompts import SIGNAL_SYSTEM_PROMPT


# ── Client ────────────────────────────────────────────────────────────────────

_anthropic_client: Optional[anthropic.Anthropic] = None


def _client() -> anthropic.Anthropic:
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.Anthropic()
    return _anthropic_client


# ── Memory loaders ────────────────────────────────────────────────────────────

def load_full_learnings() -> str:
    """Read the complete learnings.md from Obsidian vault."""
    path = _vault_path() / "learnings.md"
    if not path.exists():
        return "No trade learnings recorded yet."
    try:
        text = path.read_text(encoding="utf-8").strip()
        return text if text else "No trade learnings recorded yet."
    except Exception:
        return "Could not load learnings."


def load_trade_history(n: int = 20) -> str:
    """Return last N closed trades as structured text."""
    try:
        closed = get_closed_positions()
        recent = closed[-n:] if len(closed) >= n else closed
        if not recent:
            return "No closed trades yet."

        lines = ["## Recent Closed Trades (oldest → newest)\n"]
        for p in recent:
            pnl    = p.get("final_pnl_pct", 0)
            icon   = "WIN " if pnl > 0 else "LOSS"
            thesis = (p.get("thesis", "") or "")[:300]
            lines.append(
                f"[{icon}] {p['ticker']} {p.get('direction','?').upper()} | "
                f"Entry: ${p.get('entry_price','?')} → Exit: ${p.get('exit_price','?')} | "
                f"PnL: {pnl:+.2f}% | Days: {p.get('days_held','?')} | "
                f"Status: {p.get('status','?').replace('_',' ')}\n"
                f"  Thesis: {thesis}"
            )
        return "\n".join(lines)
    except Exception:
        return "Could not load trade history."


# ── Market context ────────────────────────────────────────────────────────────

def get_market_context() -> dict:
    """Fetch SPY, QQQ, VIX context. Used to pass macro backdrop to Claude."""
    try:
        import data.yf_client as _yfc
        from analysis.gex_analysis import analyze_exposure, extract_dex_bias
        import yfinance as yf

        context = {"spy": {}, "qqq": {}, "regime": "unclear"}

        for sym, key in [("SPY", "spy"), ("QQQ", "qqq")]:
            try:
                tf    = _yfc.get_multi_tf_context(sym)
                daily = tf.get("daily")
                price = float(daily["close"].iloc[-1]) if daily is not None and not daily.empty else 0
                gex   = _yfc.get_stub_gex(sym, price)
                try:
                    gex = analyze_exposure(sym)
                except Exception:
                    pass
                flow = extract_dex_bias(gex)
                recent = daily["close"].iloc[-3:].mean() if daily is not None and len(daily) >= 3 else price
                earlier = daily["close"].iloc[-6:-3].mean() if daily is not None and len(daily) >= 6 else price
                trend = "UP" if recent > earlier else "DOWN"
                context[key] = {
                    "price":      price,
                    "gex_regime": gex.get("gex_regime", "N/A"),
                    "gex_flip":   gex.get("gex_flip", 0),
                    "trend":      trend,
                    "flow_bias":  flow.get("bias", "neutral"),
                }
            except Exception:
                pass

        # Regime inference from SPY/QQQ trend only
        spy_trend = context["spy"].get("trend", "")
        qqq_trend = context["qqq"].get("trend", "")
        if spy_trend == "UP" and qqq_trend == "UP":
            context["regime"] = "risk_on"
        elif spy_trend == "DOWN" and qqq_trend == "DOWN":
            context["regime"] = "risk_off"
        else:
            context["regime"] = "mixed"

        return context
    except Exception as e:
        print(f"[claude_analyst] market_context error: {e}")
        return {"spy": {}, "qqq": {}, "regime": "unclear"}


# ── Chart encoding ────────────────────────────────────────────────────────────

def _render_chart_b64(candidate: dict) -> Optional[str]:
    """Render chart → base64 PNG string. Returns None on failure."""
    try:
        from analysis.chart_renderer import render_chart
        ticker  = candidate["ticker"]
        daily   = candidate.get("yf_metrics", {}).get("daily_df")
        gex     = candidate.get("gex", {})
        pivots  = candidate.get("weekly_pivots", {})

        if daily is None or (hasattr(daily, "empty") and daily.empty):
            return None

        buf = render_chart(ticker, daily, gex, pivots)
        if buf is None:
            return None
        buf.seek(0)
        raw = buf.read()
        return base64.standard_b64encode(raw).decode("utf-8")
    except Exception as e:
        print(f"[claude_analyst] chart render failed for {candidate.get('ticker','?')}: {e}")
        return None


# ── Raw data formatter ────────────────────────────────────────────────────────

def _format_raw_data(candidate: dict, market_context: dict, scan_rank: int = 0) -> str:
    """
    Format ALL raw data as structured text. No scoring, no interpretation.
    Claude reads this and makes its own judgments.
    """
    ticker = candidate.get("ticker", "?")
    price  = candidate.get("real_time_price", 0)
    gex    = candidate.get("gex", {})
    flow   = candidate.get("flow_bias", {})
    yf     = candidate.get("yf_metrics", {})
    pivots = candidate.get("weekly_pivots", {})
    news   = candidate.get("news", [])
    daily  = yf.get("daily_df")
    four_h = candidate.get("polygon_4h")
    one_h  = candidate.get("polygon_1h")

    # ── Daily bars (last 20) ──────────────────────────────────────────────────
    daily_rows = "N/A"
    if daily is not None and not daily.empty:
        rows = []
        tail = daily.tail(20)
        for idx, row in tail.iterrows():
            vol = row.get("volume", 0)
            rows.append(
                f"  {str(idx)[:10]}: "
                f"O:{row.get('open', 0):.2f} H:{row.get('high', 0):.2f} "
                f"L:{row.get('low', 0):.2f} C:{row.get('close', 0):.2f} "
                f"V:{vol:,.0f}"
            )
        daily_rows = "\n".join(rows)

    # ── 4H bars (last 10) ─────────────────────────────────────────────────────
    four_h_rows = "N/A"
    if four_h is not None and not four_h.empty:
        rows = []
        for idx, row in four_h.tail(10).iterrows():
            rows.append(
                f"  {str(idx)[:16]}: "
                f"O:{row.get('open', 0):.2f} H:{row.get('high', 0):.2f} "
                f"L:{row.get('low', 0):.2f} C:{row.get('close', 0):.2f} "
                f"V:{row.get('volume', 0):,.0f}"
            )
        four_h_rows = "\n".join(rows)

    # ── 1H bars (last 10) ─────────────────────────────────────────────────────
    one_h_rows = "N/A"
    if one_h is not None and not one_h.empty:
        rows = []
        for idx, row in one_h.tail(10).iterrows():
            rows.append(
                f"  {str(idx)[:16]}: "
                f"O:{row.get('open', 0):.2f} H:{row.get('high', 0):.2f} "
                f"L:{row.get('low', 0):.2f} C:{row.get('close', 0):.2f} "
                f"V:{row.get('volume', 0):,.0f}"
            )
        one_h_rows = "\n".join(rows)

    # ── GEX strikes (top 10 by absolute GEX) ─────────────────────────────────
    strikes_raw = gex.get("strikes", [])
    strikes_str = "N/A"
    if strikes_raw:
        top_strikes = sorted(strikes_raw, key=lambda x: abs(x.get("gex", 0)), reverse=True)[:10]
        strikes_str = "\n".join([
            f"  ${s.get('strike', '?')}: "
            f"GEX={s.get('gex', 0):,.0f}  "
            f"DEX={s.get('dex', 0):,.0f}  "
            f"VEX={s.get('vex', 0):,.0f}"
            for s in top_strikes
        ])

    # ── News ──────────────────────────────────────────────────────────────────
    news_str = "No news in last 48h"
    if news:
        lines = []
        for n in news[:8]:
            ts        = n.get("published_utc", n.get("timestamp", ""))[:16]
            headline  = n.get("title", n.get("headline", "No title"))
            source    = n.get("publisher", {}).get("name", n.get("source", ""))
            sentiment = ""
            if n.get("insights"):
                sentiment = n["insights"][0].get("sentiment", "")
            tag = f" [{sentiment.upper()}]" if sentiment else ""
            lines.append(f"  [{ts}] {headline} ({source}){tag}")
        news_str = "\n".join(lines)

    # ── Market context ────────────────────────────────────────────────────────
    spy = market_context.get("spy", {})
    qqq = market_context.get("qqq", {})

    # ── Technical metrics ─────────────────────────────────────────────────────
    ma20 = yf.get("ma20")
    ma50 = yf.get("ma50")
    rsi  = yf.get("rsi", "N/A")
    atr  = yf.get("atr")

    def fmt(v):
        return f"{v:.2f}" if v is not None else "N/A"

    sections = [
        f"═══════════════════════════════════════════════════",
        f"TICKER: {ticker}   PRICE: ${price:.2f}   RANK: #{scan_rank}/50",
        f"(YF momentum score {candidate.get('yf_score', 'N/A')} — raw screen only, not a signal)",
        f"",
        f"── DAILY BARS (last 20, OHLCV) ──────────────────────",
        daily_rows,
        f"",
        f"── 4-HOUR BARS (last 10, OHLCV) ─────────────────────",
        four_h_rows,
        f"",
        f"── 1-HOUR BARS (last 10, OHLCV) ─────────────────────",
        one_h_rows,
        f"",
        f"── COMPUTED TECHNICALS ───────────────────────────────",
        f"  MA20:          ${fmt(ma20)}",
        f"  MA50:          ${fmt(ma50)}",
        f"  RSI (daily):   {rsi}",
        f"  ATR (daily):   ${fmt(atr)}",
        f"  Vol vs avg:    {yf.get('volume_vs_avg', 'N/A')}",
        f"  10d move:      {yf.get('ten_day_move_pct', 'N/A')}%",
        f"  52W high dist: {yf.get('pct_from_52w_high', 'N/A')}%",
        f"",
        f"── WEEKLY PIVOTS ─────────────────────────────────────",
        f"  P:   ${pivots.get('P', 'N/A')}",
        f"  R1:  ${pivots.get('R1', 'N/A')}  |  R2: ${pivots.get('R2', 'N/A')}",
        f"  S1:  ${pivots.get('S1', 'N/A')}  |  S2: ${pivots.get('S2', 'N/A')}",
        f"",
        f"── GEX / DEX / VEX (DEALER EXPOSURE) ────────────────",
        f"  GEX Regime:    {gex.get('gex_regime', 'N/A')}",
        f"  Gamma Flip:    ${gex.get('gex_flip', 'N/A')}",
        f"  Call Wall:     ${gex.get('call_wall', gex.get('gex_wall', 'N/A'))}",
        f"  Put Wall:      ${gex.get('put_wall', 'N/A')}",
        f"  King Above:    ${gex.get('king_above', 'N/A')}",
        f"  King Below:    ${gex.get('king_below', 'N/A')}",
        f"  Net VEX:       {gex.get('net_vex', 'N/A')}",
        f"",
        f"  Top GEX Strikes:",
        strikes_str,
        f"",
        f"── DEX (DEALER DELTA) ────────────────────────────────",
        f"  Bias:          {flow.get('bias', 'N/A')}",
        f"  Strength:      {flow.get('strength', 0):.0%}",
        f"  Call %:        {flow.get('call_pct', 0) * 100:.1f}%",
        f"  Put %:         {(1 - flow.get('call_pct', 0.5)) * 100:.1f}%",
        f"  Unusual:       {flow.get('unusual_activity', 'none')}",
        f"",
        f"── NEWS (last 48h) ───────────────────────────────────",
        news_str,
        f"",
        f"── MARKET CONTEXT ────────────────────────────────────",
        f"  SPY:  ${spy.get('price', 0):.2f} | GEX: {spy.get('gex_regime', 'N/A')} | Flip: ${spy.get('gex_flip', 0):.2f} | Trend: {spy.get('trend', 'N/A')}",
        f"  QQQ:  ${qqq.get('price', 0):.2f} | GEX: {qqq.get('gex_regime', 'N/A')} | Flip: ${qqq.get('gex_flip', 0):.2f} | Trend: {qqq.get('trend', 'N/A')}",
        f"  Regime: {market_context.get('regime', 'N/A')}",
        f"═══════════════════════════════════════════════════",
    ]
    return "\n".join(str(s) for s in sections)


# ── Main analysis entry point ─────────────────────────────────────────────────

def analyze_candidate(
    candidate:      dict,
    market_context: dict,
    scan_rank:      int = 0,
    model:          str = "claude-sonnet-4-6",
) -> Optional[dict]:
    """
    Send raw data + full memory to Claude. Returns Claude's decision dict.

    This replaces scoring.py entirely. All 50 pipeline candidates pass through here.
    Claude decides trade / watch / skip based on raw data and its own reasoning.

    Returns None on API error or JSON parse failure.
    """
    ticker = candidate.get("ticker", "?")
    try:
        # ── Load memory ───────────────────────────────────────────────────────
        learnings     = load_full_learnings()
        trade_history = load_trade_history(n=20)

        # ── Build raw data text ───────────────────────────────────────────────
        raw_data = _format_raw_data(candidate, market_context, scan_rank)

        # ── Build message content ─────────────────────────────────────────────
        user_content = [
            {
                "type": "text",
                "text": (
                    f"Analyze this candidate for a swing trade.\n\n"
                    f"{'='*55}\n"
                    f"COMPLETE TRADE MEMORY — ALL PAST LEARNINGS\n"
                    f"{'='*55}\n"
                    f"{learnings}\n\n"
                    f"{'='*55}\n"
                    f"RECENT TRADE HISTORY (last 20 closed)\n"
                    f"{'='*55}\n"
                    f"{trade_history}\n\n"
                    f"{'='*55}\n"
                    f"RAW MARKET DATA\n"
                    f"{'='*55}\n"
                    f"{raw_data}"
                ),
            }
        ]

        # Attach chart image if available
        chart_b64 = _render_chart_b64(candidate)
        if chart_b64:
            user_content.append({
                "type": "text",
                "text": "Chart image (daily candles, MA20/50, GEX levels, volume):",
            })
            user_content.append({
                "type": "image",
                "source": {
                    "type":       "base64",
                    "media_type": "image/png",
                    "data":       chart_b64,
                },
            })
        else:
            user_content.append({
                "type": "text",
                "text": "(Chart unavailable — reason from price data only)",
            })

        user_content.append({
            "type": "text",
            "text": "Return ONLY valid JSON. No text outside the JSON object.",
        })

        # ── API call ──────────────────────────────────────────────────────────
        _rl_acquire(f"analyze_candidate({ticker})")
        response = _client().messages.create(
            model=model,
            max_tokens=2048,
            system=SIGNAL_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
        )
        _rl_log(f"analyze_candidate({ticker})", model,
                response.usage.input_tokens, response.usage.output_tokens)

        raw_text = response.content[0].text.strip()

        # Strip markdown fences if Claude wraps in them
        if raw_text.startswith("```"):
            parts = raw_text.split("```")
            raw_text = parts[1] if len(parts) > 1 else raw_text
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]
        raw_text = raw_text.strip()

        result = json.loads(raw_text)
        result["ticker"] = ticker  # always ensure ticker is set

        print(
            f"[claude_analyst] {ticker}: {result.get('decision','?').upper()} "
            f"| confidence={result.get('confidence','?')} "
            f"| direction={result.get('direction','?')} "
            f"| {', '.join(result.get('setup_type', []))}",
            flush=True,
        )
        return result

    except RuntimeError as e:
        print(f"[claude_analyst] {e}", flush=True)
        return None
    except json.JSONDecodeError as e:
        print(f"[claude_analyst] JSON parse error for {ticker}: {e}", flush=True)
        return None
    except anthropic.APIError as e:
        print(f"[claude_analyst] API error for {ticker}: {e}", flush=True)
        return None
    except Exception as e:
        print(f"[claude_analyst] Unexpected error for {ticker}: {e}", flush=True)
        return None


# ── On-demand ticker builder ──────────────────────────────────────────────────

def build_candidate_for_ticker(ticker: str) -> Optional[dict]:
    """
    Build a full candidate dict for a single ticker.
    Used by !signal command and on-demand analysis.
    """
    import pandas as pd
    import data.yf_client as _yfc
    from analysis.gex_analysis import analyze_exposure, extract_dex_bias
    from scanner.flow_scanner import get_ticker_flow_bias

    try:
        tf    = _yfc.get_multi_tf_context(ticker)
        daily = tf.get("daily", pd.DataFrame())
        four_h = tf.get("4h")
        one_h  = tf.get("1h")
        pivots = tf.get("weekly_pivots", {})

        if daily.empty:
            return None

        price = float(daily["close"].iloc[-1])

        # Compute basic metrics
        closes = daily["close"]
        ma20 = float(closes.rolling(20).mean().iloc[-1]) if len(closes) >= 20 else None
        ma50 = float(closes.rolling(50).mean().iloc[-1]) if len(closes) >= 50 else None

        vol_avg = daily["volume"].tail(20).mean() if "volume" in daily.columns else 0
        vol_cur = daily["volume"].iloc[-1] if "volume" in daily.columns else 0
        vol_ratio = round(vol_cur / vol_avg, 2) if vol_avg > 0 else 0

        ten_day_move = None
        if len(closes) >= 10:
            old = closes.iloc[-10]
            ten_day_move = round((price - old) / old * 100, 2) if old > 0 else None

        high_52w = closes.tail(252).max() if len(closes) >= 252 else closes.max()
        pct_from_high = round((price - high_52w) / high_52w * 100, 2) if high_52w > 0 else None

        yf_metrics = {
            "ticker":          ticker,
            "current_price":   price,
            "daily_df":        daily,
            "ma20":            ma20,
            "ma50":            ma50,
            "volume_vs_avg":   f"{vol_ratio:.1f}x",
            "rsi":             None,
            "atr":             None,
            "ten_day_move_pct": ten_day_move,
            "pct_from_52w_high": pct_from_high,
        }

        # GEX
        try:
            gex = analyze_exposure(ticker)
        except Exception:
            gex = _yfc.get_stub_gex(ticker, price)

        try:
            flow_bias = get_ticker_flow_bias(ticker)
        except Exception:
            flow_bias = extract_dex_bias(gex)

        return {
            "ticker":          ticker,
            "yf_score":        0,
            "yf_metrics":      yf_metrics,
            "real_time_price": price,
            "polygon_4h":      four_h,
            "polygon_1h":      one_h,
            "weekly_pivots":   pivots,
            "news":            [],
            "gex":             gex,
            "flow_bias":       flow_bias,
            "scan_rank":       0,
        }
    except Exception as e:
        print(f"[claude_analyst] build_candidate_for_ticker error for {ticker}: {e}")
        return None
