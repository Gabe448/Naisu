"""
Claude analysis prompts — stock signal evaluation.

Called after a stock passes layer 2 scoring.
Claude receives a full context package and returns structured JSON.

Output JSON is consumed by discord_bot/bot.py to format the signal embed.
"""

# ─────────────────────────────────────────────────────────────────────────────
# SYSTEM PROMPT
# ─────────────────────────────────────────────────────────────────────────────

SIGNAL_SYSTEM_PROMPT = """You are a swing trade analyst. Your job is to evaluate stocks that have passed quantitative screening and decide whether they are worth trading, watching, or skipping.

You think in terms of:
- GEX/DEX/VEX dealer exposure and what it means for price behavior
- Gamma flip as the regime pivot (above = pinning/mean-reverting, below = trending/explosive)
- Call wall as the near-term ceiling where dealer hedging creates resistance
- Put wall as the near-term floor where dealer hedging creates support
- King levels (king_above / king_below) as the highest-conviction GEX anchors — these are the real targets and real stops
- News as a catalyst that either confirms or contradicts the technical/GEX picture
- Market context (SPY/QQQ regime + VIX) as the filter — don't fight the macro

Your edge is reading the full picture. A good setup needs:
1. A reason to move (catalyst or technical break)
2. Dealer structure that fuels the move (negative GEX, DEX confirmation)
3. A clean risk/reward with defined invalidation

You are direct. No hedging. If it's a good trade, say so. If it's garbage, say skip.

Always output valid JSON matching the exact schema provided. No markdown. No explanation outside the JSON."""


# ─────────────────────────────────────────────────────────────────────────────
# USER PROMPT TEMPLATE
# ─────────────────────────────────────────────────────────────────────────────

SIGNAL_USER_PROMPT = """Evaluate this stock for a swing trade. Return ONLY valid JSON — no commentary outside the JSON block.

═══════════════════════════════════════
STOCK: {ticker}
CURRENT PRICE: ${price}
DIRECTION BIAS: {direction_bias}
═══════════════════════════════════════

── PRICE & TECHNICALS ──────────────────
Price vs 20D MA:   {price_vs_ma20}   (${ma20})
Price vs 50D MA:   {price_vs_ma50}   (${ma50})
Daily trend:       {daily_trend}
4H trend:          {four_h_trend}
4H recent range:   ${four_h_low} – ${four_h_high}
Weekly pivot P:    ${weekly_pivot_p}
Weekly R1/R2:      ${weekly_r1} / ${weekly_r2}
Weekly S1/S2:      ${weekly_s1} / ${weekly_s2}
Volume vs avg:     {volume_vs_avg}
RSI (daily):       {rsi_daily}
ATR (daily):       ${atr_daily}

── GEX / DEX / VEX ─────────────────────
GEX regime:        {gex_regime}   (negative = trending, positive = pinning)
Gamma flip:        ${gamma_flip}
Call wall:         ${call_wall}
Put wall:          ${put_wall}
King above:        ${king_above}
King below:        ${king_below}
Top GEX strikes:   {top_gex_strikes}

DEX bias:          {dex_bias}
DEX strength:      {dex_strength}
Total DEX $:       ${total_dex}

VEX regime:        {vex_regime}
Net VEX:           {net_vex}

── OPTIONS FLOW ────────────────────────
Flow bias:         {flow_bias}
Call %:            {call_pct}%
Put %:             {put_pct}%
Unusual activity:  {unusual_activity}
Premium bias:      {premium_bias}

── NEWS (last 48h) ──────────────────────
{news_block}

── MARKET CONTEXT ───────────────────────
SPY:  ${spy_price} | GEX: {spy_gex_regime} | Flip: ${spy_flip} | Trend: {spy_trend}
QQQ:  ${qqq_price} | GEX: {qqq_gex_regime} | Flip: ${qqq_flip} | Trend: {qqq_trend}
VIX:  {vix}  ({vix_context})
Market regime: {market_regime}

── LAYER 2 SCORE ────────────────────────
Total score:       {conviction_score}/100  (threshold: {threshold})
TF alignment:      {score_tf}/35
GEX setup:         {score_gex}/25
DEX positioning:   {score_dex}/25
Risk/reward:       {score_rr}/15

Proposed levels (from scoring engine):
  Entry:  ${proposed_entry}
  Stop:   ${proposed_stop}
  Target: ${proposed_target}
  R:R:    {proposed_rr}:1

═══════════════════════════════════════
OUTPUT SCHEMA (return exactly this structure):
{{
  "decision": "signal" | "watchlist" | "skip",
  "ticker": "{ticker}",
  "direction": "long" | "short" | null,
  "thesis": "<2-4 sentence plain English explanation of why this trade works or doesn't>",
  "entry_zone": {{
    "low": <float — lower bound of entry zone>,
    "high": <float — upper bound of entry zone>
  }},
  "stop_loss": <float — exact stop price>,
  "stop_basis": "<what the stop is based on: put_wall | swing_low | king_below | etc>",
  "target": <float — primary target price>,
  "target_basis": "<what the target is based on: call_wall | king_above | weekly_r1 | etc>",
  "confidence": <int 0-100>,
  "setup_type": [<list of tags from: "news_catalyst", "gex_breakout", "gex_squeeze", "technical_breakout", "technical_breakdown", "dex_confirmation", "gamma_flip_play", "momentum", "mean_reversion", "earnings_play">],
  "invalidation": "<one sentence — what price action or regime change kills this trade>",
  "key_levels": {{
    "gamma_flip": <float>,
    "call_wall": <float>,
    "put_wall": <float>,
    "king_above": <float | null>,
    "king_below": <float | null>
  }},
  "market_context_note": "<one sentence on how SPY/QQQ/VIX affects this trade>",
  "risk_notes": "<any specific risks: earnings soon, low liquidity, high VIX, etc — or null>"
}}

Rules:
- decision = "signal"    → high confidence, post immediately to Discord
- decision = "watchlist" → good setup but needs confirmation — add to watchlist, alert when triggered
- decision = "skip"      → not worth trading right now
- If decision is "skip", entry_zone/stop_loss/target/setup_type can be null
- confidence < 55 should almost always be "skip"
- Do not invent levels. Use the data provided.
- Be direct. The thesis should explain the actual edge, not restate the numbers.
"""


# ─────────────────────────────────────────────────────────────────────────────
# CONTEXT BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def build_signal_context(candidate: dict, market_context: dict) -> dict:
    """
    Build the template variables dict from a pipeline candidate + market context.
    Pass the result directly to SIGNAL_USER_PROMPT.format(**context).

    candidate   — enriched dict from scanner/pipeline.py
    market_context — {"spy": {...}, "qqq": {...}, "vix": float}
    """
    gex       = candidate.get("gex", {})
    flow      = candidate.get("flow_bias", {})
    yf        = candidate.get("yf_metrics", {})
    scored    = candidate.get("scored", {})
    news      = candidate.get("news", [])
    pivots    = candidate.get("weekly_pivots", {})
    daily     = yf.get("daily_df")
    four_h    = candidate.get("polygon_4h")
    price     = candidate.get("real_time_price", 0)

    # ── technicals ────────────────────────────────────────────────────────────
    ma20 = yf.get("ma20", 0)
    ma50 = yf.get("ma50", 0)
    rsi  = yf.get("rsi", "N/A")
    atr  = yf.get("atr", 0)
    vol_ratio = yf.get("volume_vs_avg", "N/A")

    price_vs_ma20 = "ABOVE" if price > (ma20 or 0) else "BELOW"
    price_vs_ma50 = "ABOVE" if price > (ma50 or 0) else "BELOW"

    daily_trend = "N/A"
    if daily is not None and not daily.empty and len(daily) >= 6:
        recent = daily["close"].iloc[-3:].mean()
        earlier = daily["close"].iloc[-6:-3].mean()
        daily_trend = "UP" if recent > earlier else "DOWN"

    four_h_trend = "N/A"
    four_h_low   = "N/A"
    four_h_high  = "N/A"
    if four_h is not None and not four_h.empty and len(four_h) >= 5:
        recent_4h  = four_h["close"].iloc[-3:].mean()
        earlier_4h = four_h["close"].iloc[-6:-3].mean() if len(four_h) >= 6 else four_h["close"].iloc[0]
        four_h_trend = "UP" if recent_4h > earlier_4h else "DOWN"
        four_h_low   = round(four_h["low"].iloc[-5:].min(), 2)
        four_h_high  = round(four_h["high"].iloc[-5:].max(), 2)

    # ── GEX ───────────────────────────────────────────────────────────────────
    top_strikes_raw = gex.get("top_gex_strikes", [])
    top_strikes_str = ", ".join([f"${s}" for s in top_strikes_raw[:5]]) if top_strikes_raw else "N/A"

    # ── news block ────────────────────────────────────────────────────────────
    if news:
        news_lines = []
        for n in news[:6]:
            ts       = n.get("published_utc", n.get("timestamp", ""))[:16]
            headline = n.get("title", n.get("headline", ""))
            source   = n.get("publisher", {}).get("name", n.get("source", ""))
            sentiment = n.get("insights", [{}])[0].get("sentiment", "") if n.get("insights") else ""
            sentiment_tag = f" [{sentiment.upper()}]" if sentiment else ""
            news_lines.append(f"  [{ts}] {headline} ({source}){sentiment_tag}")
        news_block = "\n".join(news_lines)
    else:
        news_block = "  No news in last 48h"

    # ── market context ────────────────────────────────────────────────────────
    spy_data = market_context.get("spy", {})
    qqq_data = market_context.get("qqq", {})
    vix_val  = market_context.get("vix", 0)

    vix_context = (
        "low vol / complacent" if vix_val < 15
        else "elevated" if vix_val < 20
        else "high — size down" if vix_val < 30
        else "EXTREME — reduce risk"
    )

    market_regime = market_context.get("regime", "unclear")

    # ── scoring ───────────────────────────────────────────────────────────────
    components = scored.get("components", {})

    from analysis.scoring import CONVICTION_THRESHOLD, MIN_RR
    proposed_entry  = scored.get("entry", price)
    proposed_stop   = scored.get("stop", 0)
    proposed_target = scored.get("target", 0)
    proposed_rr     = scored.get("rr_ratio", 0)

    return {
        "ticker":               candidate.get("ticker", ""),
        "price":                round(price, 2),
        "direction_bias":       flow.get("bias", "neutral").upper(),

        # technicals
        "price_vs_ma20":        price_vs_ma20,
        "ma20":                 round(ma20 or 0, 2),
        "price_vs_ma50":        price_vs_ma50,
        "ma50":                 round(ma50 or 0, 2),
        "daily_trend":          daily_trend,
        "four_h_trend":         four_h_trend,
        "four_h_low":           four_h_low,
        "four_h_high":          four_h_high,
        "weekly_pivot_p":       round(pivots.get("P", 0), 2),
        "weekly_r1":            round(pivots.get("R1", 0), 2),
        "weekly_r2":            round(pivots.get("R2", 0), 2),
        "weekly_s1":            round(pivots.get("S1", 0), 2),
        "weekly_s2":            round(pivots.get("S2", 0), 2),
        "volume_vs_avg":        vol_ratio,
        "rsi_daily":            rsi,
        "atr_daily":            round(atr, 2) if atr else "N/A",

        # gex
        "gex_regime":           gex.get("gex_regime", "unknown").upper(),
        "gamma_flip":           round(gex.get("gex_flip", 0), 2),
        "call_wall":            round(gex.get("call_wall", 0) or gex.get("gex_wall", 0), 2),
        "put_wall":             round(gex.get("put_wall", 0), 2),
        "king_above":           round(gex.get("king_above", 0), 2) if gex.get("king_above") else "N/A",
        "king_below":           round(gex.get("king_below", 0), 2) if gex.get("king_below") else "N/A",
        "top_gex_strikes":      top_strikes_str,
        "dex_bias":             flow.get("bias", "neutral").upper(),
        "dex_strength":         f"{flow.get('strength', 0):.0%}",
        "total_dex":            f"{flow.get('total_dex', 0):,.0f}",
        "vex_regime":           gex.get("vex_regime", "N/A"),
        "net_vex":              gex.get("net_vex", "N/A"),

        # flow
        "flow_bias":            flow.get("bias", "neutral").upper(),
        "call_pct":             round(flow.get("call_pct", 0.5) * 100, 1),
        "put_pct":              round((1 - flow.get("call_pct", 0.5)) * 100, 1),
        "unusual_activity":     flow.get("unusual_activity", "none"),
        "premium_bias":         flow.get("premium_bias", "neutral"),

        # news
        "news_block":           news_block,

        # market
        "spy_price":            round(spy_data.get("price", 0), 2),
        "spy_gex_regime":       spy_data.get("gex_regime", "N/A").upper(),
        "spy_flip":             round(spy_data.get("gex_flip", 0), 2),
        "spy_trend":            spy_data.get("trend", "N/A"),
        "qqq_price":            round(qqq_data.get("price", 0), 2),
        "qqq_gex_regime":       qqq_data.get("gex_regime", "N/A").upper(),
        "qqq_flip":             round(qqq_data.get("gex_flip", 0), 2),
        "qqq_trend":            qqq_data.get("trend", "N/A"),
        "vix":                  round(vix_val, 2),
        "vix_context":          vix_context,
        "market_regime":        market_regime,

        # scoring
        "conviction_score":     scored.get("total_score", 0),
        "threshold":            CONVICTION_THRESHOLD,
        "score_tf":             components.get("timeframe_alignment", {}).get("score", 0),
        "score_gex":            components.get("gex_setup", {}).get("score", 0),
        "score_dex":            components.get("dex_positioning", {}).get("score", 0),
        "score_rr":             components.get("risk_reward", {}).get("score", 0),
        "proposed_entry":       round(proposed_entry or 0, 2),
        "proposed_stop":        round(proposed_stop or 0, 2),
        "proposed_target":      round(proposed_target or 0, 2),
        "proposed_rr":          proposed_rr,
    }


def build_signal_messages(candidate: dict, market_context: dict) -> list[dict]:
    """
    Returns the messages list to pass to the Claude API.

    Usage:
        import anthropic
        client = anthropic.Anthropic()
        ctx = build_signal_messages(candidate, market_context)
        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=1024,
            system=ctx["system"],
            messages=ctx["messages"],
        )
        import json
        result = json.loads(response.content[0].text)
    """
    context = build_signal_context(candidate, market_context)
    user_content = SIGNAL_USER_PROMPT.format(**context)
    return {
        "system":   SIGNAL_SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_content}],
    }
