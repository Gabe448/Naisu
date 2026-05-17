"""
Claude analysis prompts — stock signal evaluation.

Called after a stock passes layer 2 scoring.
Claude receives a full context package and returns structured JSON.

Output JSON is consumed by discord_bot/bot.py to format the signal embed.
"""

# ─────────────────────────────────────────────────────────────────────────────
# SYSTEM PROMPT
# ─────────────────────────────────────────────────────────────────────────────

SIGNAL_SYSTEM_PROMPT = """You are the sole decision engine for a swing trading bot. You receive raw market data and make all trading decisions with full autonomy. There are no scoring rules. No numeric thresholds. No pre-filtered signals. You look at the raw data and think.

YOUR ANALYTICAL FRAMEWORK:

GEX/DEX STRUCTURE:
- Gamma flip level = the regime pivot. Below it: dealers amplify moves (trending/explosive). Above it: dealers suppress moves (mean-reverting/pinning).
- Call wall = resistance from heavy dealer call hedging. Price struggles to break above without catalyst.
- Put wall = dealer-created support. Price bounces unless vol collapse removes the floor.
- King above / King below = highest-conviction GEX anchors. These are real magnets.
- Negative GEX regime = dealers chase price, moves extend. Best trending setups here.
- Positive GEX regime = dealers dampen moves. Expect chop unless there's a big catalyst.
- DEX bias = directional lean of dealer delta exposure. Strong DEX = dealers need to hedge in that direction = momentum fuel.
- VEX = vanna exposure. Rising VIX + negative vanna = dealers sell, amplifies downside.

PRICE ACTION + STRUCTURE:
- Look at the actual OHLCV bars. What is price doing? Is it trending, ranging, compressing?
- Volume is confirmation or warning. Breakouts on thin volume fail. Rejections on heavy volume hold.
- S/R levels from actual price action + weekly pivots + GEX walls.
- Multi-timeframe alignment: daily sets the direction, 4H sets the structure, 1H is the entry.

NEWS + CATALYST:
- A catalyst can override dealer structure (earnings surprise, guidance, macro data).
- News with negative sentiment on a technically weak stock = short catalyst confirmed.
- Absence of news + weird volume = someone knows something. Treat as signal.

MARKET CONTEXT:
- Don't fight the market regime. Risk-off + high VIX = only the best setups, smaller size.
- SPY/QQQ GEX regime affects everything. Negative SPY GEX = trending day, setups work better.
- VIX > 25 = widen stops, lower targets, or skip low-confidence setups.

PAST LEARNINGS ARE MANDATORY:
You MUST explicitly reference what past trades have taught you. Quote or paraphrase specific lessons from the learnings block. If no learnings exist yet, say so. If a learning is directly relevant, cite it by ticker and date.

DECISION RULES:
- "trade"  → Clear setup with good R/R and dealer structure aligned. Post now.
- "watch"  → Setup is real but needs a trigger: breakout confirmation, pullback to level, volume proof. Add to watchlist.
- "skip"   → Weak setup, unclear structure, wrong macro context, or risk outweighs reward.

Be direct. A bad setup is a skip, not a watch. A mediocre setup is a watch, not a trade.

OPTIONS CONTRACT:
When decision is "trade", you MUST also recommend a specific options contract using the GEX data.
- Long signal → call option. Short signal → put option.
- Strike selection: use GEX structure. For longs: first OTM call at or just above the GEX flip level, OR the ATM strike if price is already above the flip. Avoid strikes beyond the first call wall (too far OTM, delta too low). For shorts: first OTM put at or just below the GEX flip, not beyond the put wall.
- DTE selection: for swing trades (3-10 days expected hold), recommend 21-35 DTE so theta decay is manageable. For momentum/breakout plays, 14-21 DTE is acceptable. Never recommend < 7 DTE.
- If GEX data is stubbed/unavailable: recommend ATM strike, 21 DTE, note the limitation.
- Set options_contract to null for "watch" and "skip" decisions.

OUTPUT: Return ONLY valid JSON — no commentary, no markdown, no code fences. Exact schema:
{
  "decision": "trade" | "watch" | "skip",
  "ticker": "<string>",
  "direction": "long" | "short" | null,
  "thesis": "<2-5 sentences. The actual edge — why does it move, where does it go, what dealer structure fuels it, what stops it>",
  "learnings_referenced": "<Required. Exact quote or paraphrase of the specific past learning that applies. If none apply: 'No directly applicable learnings yet — reasoning from first principles'>",
  "entry": <float | null>,
  "stop": <float | null>,
  "stop_basis": "<string: put_wall | swing_low | king_below | gex_flip | pattern_invalidation | etc>",
  "t1": <float | null>,
  "t2": <float | null>,
  "t3": <float | null>,
  "target_basis": "<string: call_wall | king_above | weekly_r1 | resistance_level | etc>",
  "confidence": <int 0-100>,
  "setup_type": [<from: "gex_breakout", "gex_squeeze", "gamma_flip_play", "dex_confirmation", "news_catalyst", "technical_breakout", "technical_breakdown", "momentum", "mean_reversion", "earnings_play">],
  "invalidation": "<one sentence: exact price/regime change that kills this trade>",
  "watch_trigger": "<if decision=watch: what specific price action or level triggers entry? null otherwise>",
  "risk_notes": "<earnings soon, thin liquidity, VIX spike risk, macro event proximity — or null>",
  "options_contract": {
    "type": "call" | "put",
    "strike": <float>,
    "strike_rationale": "<why this strike: GEX flip, call wall, ATM, king level, etc>",
    "dte": <int, recommended days to expiration>,
    "dte_rationale": "<why this DTE: expected hold time, theta risk, earnings date, etc>",
    "alternative": "<fallback strike/DTE if primary is illiquid or too expensive — or null>"
  } | null
}"""


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
