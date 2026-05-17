"""
Daily brief prompts — three Claude calls that run on schedule:

  1. Morning Brief    (~9:31 AM ET) — market structure + open positions + game plan
  2. EOD Recap        (~4:05 PM ET) — what happened, what was right/wrong, lessons
  3. Next Day Outlook (~4:30 PM ET) — what to watch tomorrow, setups forming

Each function returns a {"system": ..., "messages": [...]} dict ready for the Claude API.
All three read from scan_history (today's signals) and open_positions.

Usage:
    import anthropic, json
    from analysis.daily_brief_prompts import build_morning_brief_messages

    client = anthropic.Anthropic()
    payload = build_morning_brief_messages(open_positions, scan_history, market_context)
    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=2048,
        system=payload["system"],
        messages=payload["messages"],
    )
    result = json.loads(response.content[0].text)
"""

# ─────────────────────────────────────────────────────────────────────────────
# SHARED SYSTEM PROMPT
# ─────────────────────────────────────────────────────────────────────────────

_DAILY_SYSTEM_PROMPT = """You are a swing trade analyst running a disciplined, GEX-aware trading operation.

You track positions using GEX/DEX/VEX dealer exposure, multi-timeframe technicals, and options flow.
Your job in daily briefs is to:
- Be honest about what is working and what isn't
- Flag any regime changes that threaten open positions
- Identify the 1-3 best setups worth watching
- Give a clear, actionable game plan — not a wall of text

You are direct. No fluff. No "great setup!" cheerleading.
If a position is in trouble, say so. If the market is unclear, say that.

Always output valid JSON matching the schema provided. No markdown. No text outside the JSON."""


# ─────────────────────────────────────────────────────────────────────────────
# 1. MORNING BRIEF
# ─────────────────────────────────────────────────────────────────────────────

_MORNING_BRIEF_PROMPT = """Generate a morning brief for today's trading session. Return ONLY valid JSON.

DATE: {date}
TIME: Market open

═══════════════════════════════════════
MARKET CONTEXT
═══════════════════════════════════════
SPY:  ${spy_price} | GEX: {spy_gex_regime} | Flip: ${spy_flip} | vs yesterday: {spy_change_pct}%
QQQ:  ${qqq_price} | GEX: {qqq_gex_regime} | Flip: ${qqq_flip} | vs yesterday: {qqq_change_pct}%
VIX:  {vix} ({vix_change_pct}% vs yesterday) — {vix_context}
Pre-market move: {premarket_context}
Market regime: {market_regime}
Key macro events today: {macro_events}

═══════════════════════════════════════
OPEN POSITIONS ({position_count} open)
═══════════════════════════════════════
{positions_block}

═══════════════════════════════════════
TODAY'S WATCHLIST / SETUPS FROM SCAN
═══════════════════════════════════════
{watchlist_block}

═══════════════════════════════════════
OUTPUT SCHEMA:
{{
  "date": "{date}",
  "brief_type": "morning",
  "market_summary": "<2-3 sentences: what is the market doing and why it matters today>",
  "market_regime": "risk_on" | "risk_off" | "mixed" | "unclear",
  "positions": [
    {{
      "ticker": "<ticker>",
      "direction": "long" | "short",
      "pnl_pct": <float>,
      "days_held": <int>,
      "status": "on_track" | "near_stop" | "near_target" | "thesis_broken",
      "thesis_check": "<one sentence — is the thesis still valid?>",
      "action": "hold" | "tighten_stop" | "take_partial" | "close" | "add",
      "action_reason": "<why>"
    }}
  ],
  "top_watchlist": [
    {{
      "ticker": "<ticker>",
      "direction": "long" | "short",
      "setup_type": "<tag>",
      "trigger": "<what needs to happen for this to become a signal>",
      "key_level": <float>
    }}
  ],
  "game_plan": "<3-5 sentences: specific, actionable focus for today>",
  "risk_flags": ["<any specific risks to be aware of today>"],
  "conviction_level": "high" | "medium" | "low"
}}
"""


def _build_positions_block(positions: list[dict]) -> str:
    if not positions:
        return "  No open positions."
    lines = []
    for p in positions:
        ticker    = p.get("ticker", "?")
        direction = p.get("direction", "?").upper()
        entry     = p.get("entry_price", 0)
        current   = p.get("current_price", entry)
        stop      = p.get("stop_price", 0)
        target    = p.get("target_price", 0)
        pnl       = p.get("pnl_pct", 0)
        days      = p.get("days_held", 0)
        score     = p.get("conviction_score", "?")
        dist_stop = round(abs(current - stop) / current * 100, 1) if stop and current else "?"
        dist_tgt  = round(abs(target - current) / current * 100, 1) if target and current else "?"
        thesis_ok = p.get("thesis_check", "not checked")

        lines.append(
            f"  {ticker} {direction} | Entry ${entry} → Current ${current} | "
            f"PnL {pnl:+.1f}% | Day {days} | Score {score}\n"
            f"    Stop ${stop} ({dist_stop}% away) | Target ${target} ({dist_tgt}% away)\n"
            f"    GEX/DEX status: {thesis_ok}"
        )
    return "\n\n".join(lines)


def _build_watchlist_block(signals: list[dict]) -> str:
    if not signals:
        return "  No high-conviction setups from morning scan."
    lines = []
    for s in signals[:8]:
        ticker    = s.get("ticker", "?")
        direction = s.get("direction", "?").upper()
        score     = s.get("conviction_score", 0)
        decision  = s.get("claude_decision", s.get("decision", "watchlist")).upper()
        entry     = s.get("entry", s.get("proposed_entry", 0))
        target    = s.get("target", s.get("proposed_target", 0))
        stop      = s.get("stop_loss", s.get("proposed_stop", 0))
        setup_types = ", ".join(s.get("setup_type", [])) if s.get("setup_type") else "N/A"
        lines.append(
            f"  [{decision}] {ticker} {direction} | Score {score}/100 | Entry ~${entry} | "
            f"Stop ${stop} | Target ${target}\n"
            f"    Setup: {setup_types}"
        )
    return "\n".join(lines)


def build_morning_brief_messages(
    open_positions: list[dict],
    scan_history:   list[dict],
    market_context: dict,
) -> dict:
    """Build morning brief payload for Claude API."""
    import datetime
    today = datetime.date.today().strftime("%Y-%m-%d")

    spy = market_context.get("spy", {})
    qqq = market_context.get("qqq", {})
    vix = market_context.get("vix", 0)

    vix_context = (
        "low vol / complacent" if vix < 15
        else "elevated" if vix < 20
        else "high — size down" if vix < 30
        else "EXTREME — reduce risk"
    )

    user_content = _MORNING_BRIEF_PROMPT.format(
        date            = today,
        spy_price       = round(spy.get("price", 0), 2),
        spy_gex_regime  = spy.get("gex_regime", "N/A").upper(),
        spy_flip        = round(spy.get("gex_flip", 0), 2),
        spy_change_pct  = round(spy.get("change_pct", 0), 2),
        qqq_price       = round(qqq.get("price", 0), 2),
        qqq_gex_regime  = qqq.get("gex_regime", "N/A").upper(),
        qqq_flip        = round(qqq.get("gex_flip", 0), 2),
        qqq_change_pct  = round(qqq.get("change_pct", 0), 2),
        vix             = round(vix, 2),
        vix_change_pct  = round(market_context.get("vix_change_pct", 0), 2),
        vix_context     = vix_context,
        premarket_context = market_context.get("premarket_context", "No pre-market data"),
        market_regime   = market_context.get("regime", "unclear"),
        macro_events    = market_context.get("macro_events", "None scheduled"),
        position_count  = len(open_positions),
        positions_block = _build_positions_block(open_positions),
        watchlist_block = _build_watchlist_block(scan_history),
    )
    return {
        "system":   _DAILY_SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_content}],
    }


# ─────────────────────────────────────────────────────────────────────────────
# 2. EOD RECAP
# ─────────────────────────────────────────────────────────────────────────────

_EOD_RECAP_PROMPT = """Generate an end-of-day recap. Return ONLY valid JSON.

DATE: {date}
TIME: Market close

═══════════════════════════════════════
TODAY'S MARKET ACTION
═══════════════════════════════════════
SPY:  ${spy_open} open → ${spy_close} close ({spy_day_pct}%) | GEX: {spy_gex_regime} | Range: ${spy_low}–${spy_high}
QQQ:  ${qqq_open} open → ${qqq_close} close ({qqq_day_pct}%) | GEX: {qqq_gex_regime} | Range: ${qqq_low}–${qqq_high}
VIX:  {vix_open} → {vix_close} ({vix_move})
Market regime: {market_regime}
Volume: {market_volume_context}

═══════════════════════════════════════
TODAY'S SIGNALS (scanned during session)
═══════════════════════════════════════
Total signals fired: {signals_fired}
Total watchlist alerts: {watchlist_alerts}
{signals_block}

═══════════════════════════════════════
OPEN POSITIONS (end of day state)
═══════════════════════════════════════
{positions_block}

═══════════════════════════════════════
CLOSED TODAY
═══════════════════════════════════════
{closed_today_block}

═══════════════════════════════════════
OUTPUT SCHEMA:
{{
  "date": "{date}",
  "brief_type": "eod_recap",
  "market_summary": "<2-3 sentences: what happened today, any surprises>",
  "day_grade": "A" | "B" | "C" | "D" | "F",
  "day_grade_reason": "<one sentence why>",
  "positions_update": [
    {{
      "ticker": "<ticker>",
      "direction": "long" | "short",
      "pnl_pct": <float>,
      "status": "on_track" | "near_stop" | "near_target" | "thesis_broken" | "stopped_out" | "target_hit",
      "what_happened": "<one sentence on today's price action for this position>",
      "tomorrow_action": "hold" | "tighten_stop" | "take_partial" | "close" | "add",
      "stop_updated": <float | null>
    }}
  ],
  "signals_review": [
    {{
      "ticker": "<ticker>",
      "decision": "signal" | "watchlist" | "skip",
      "outcome": "worked" | "failed" | "pending" | "not_triggered",
      "note": "<brief note on how the signal played out>"
    }}
  ],
  "what_worked": "<one paragraph on what the model got right today>",
  "what_failed": "<one paragraph on where the model was wrong or missed>",
  "key_lesson": "<the single most important thing to take away from today>",
  "closed_pnl_summary": {{
    "wins": <int>,
    "losses": <int>,
    "total_pnl_pct": <float>,
    "best_trade": "<ticker or null>",
    "worst_trade": "<ticker or null>"
  }}
}}
"""


def _build_signals_block(signals: list[dict]) -> str:
    if not signals:
        return "  No signals generated today."
    lines = []
    for s in signals[:10]:
        ticker   = s.get("ticker", "?")
        decision = s.get("claude_decision", s.get("decision", "?")).upper()
        direction = s.get("direction", "?").upper()
        score    = s.get("conviction_score", s.get("confidence", 0))
        setup    = ", ".join(s.get("setup_type", [])) if s.get("setup_type") else "N/A"
        time_str = s.get("timestamp", s.get("time", ""))[:16]
        lines.append(
            f"  [{time_str}] [{decision}] {ticker} {direction} | Score {score}/100 | {setup}"
        )
    return "\n".join(lines)


def _build_closed_block(closed_positions: list[dict]) -> str:
    if not closed_positions:
        return "  No positions closed today."
    lines = []
    for p in closed_positions:
        ticker    = p.get("ticker", "?")
        direction = p.get("direction", "?").upper()
        pnl       = p.get("final_pnl_pct", 0)
        reason    = p.get("close_reason", "manual")
        entry     = p.get("entry_price", 0)
        exit_p    = p.get("exit_price", p.get("current_price", 0))
        days      = p.get("days_held", 0)
        lines.append(
            f"  {ticker} {direction} | Entry ${entry} → Exit ${exit_p} | "
            f"PnL {pnl:+.2f}% | {days}d | Reason: {reason}"
        )
    return "\n".join(lines)


def build_eod_recap_messages(
    open_positions:    list[dict],
    closed_today:      list[dict],
    todays_signals:    list[dict],
    market_context:    dict,
) -> dict:
    """Build EOD recap payload for Claude API."""
    import datetime
    today = datetime.date.today().strftime("%Y-%m-%d")

    spy = market_context.get("spy", {})
    qqq = market_context.get("qqq", {})
    vix_open  = market_context.get("vix_open", 0)
    vix_close = market_context.get("vix", 0)
    vix_move  = f"{round(vix_close - vix_open, 2):+.2f}" if vix_open else "N/A"

    signals_fired     = sum(1 for s in todays_signals if s.get("decision") == "signal")
    watchlist_alerts  = sum(1 for s in todays_signals if s.get("decision") == "watchlist")

    user_content = _EOD_RECAP_PROMPT.format(
        date                  = today,
        spy_open              = round(spy.get("open", 0), 2),
        spy_close             = round(spy.get("price", 0), 2),
        spy_day_pct           = round(spy.get("change_pct", 0), 2),
        spy_low               = round(spy.get("day_low", 0), 2),
        spy_high              = round(spy.get("day_high", 0), 2),
        spy_gex_regime        = spy.get("gex_regime", "N/A").upper(),
        qqq_open              = round(qqq.get("open", 0), 2),
        qqq_close             = round(qqq.get("price", 0), 2),
        qqq_day_pct           = round(qqq.get("change_pct", 0), 2),
        qqq_low               = round(qqq.get("day_low", 0), 2),
        qqq_high              = round(qqq.get("day_high", 0), 2),
        qqq_gex_regime        = qqq.get("gex_regime", "N/A").upper(),
        vix_open              = round(vix_open, 2),
        vix_close             = round(vix_close, 2),
        vix_move              = vix_move,
        market_regime         = market_context.get("regime", "unclear"),
        market_volume_context = market_context.get("volume_context", "N/A"),
        signals_fired         = signals_fired,
        watchlist_alerts      = watchlist_alerts,
        signals_block         = _build_signals_block(todays_signals),
        positions_block       = _build_positions_block(open_positions),
        closed_today_block    = _build_closed_block(closed_today),
    )
    return {
        "system":   _DAILY_SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_content}],
    }


# ─────────────────────────────────────────────────────────────────────────────
# 3. NEXT DAY OUTLOOK
# ─────────────────────────────────────────────────────────────────────────────

_NEXT_DAY_PROMPT = """Generate a next-day trading outlook. Return ONLY valid JSON.

DATE: {today} (outlook for {tomorrow})

═══════════════════════════════════════
CLOSE-OF-DAY MARKET STATE
═══════════════════════════════════════
SPY:  ${spy_price} | GEX: {spy_gex_regime} | Flip: ${spy_flip} | King above: ${spy_king_above} | King below: ${spy_king_below}
QQQ:  ${qqq_price} | GEX: {qqq_gex_regime} | Flip: ${qqq_flip} | King above: ${qqq_king_above} | King below: ${qqq_king_below}
VIX:  {vix} — {vix_context}
SPY above flip: {spy_above_flip}
Market regime: {market_regime}

Macro events tomorrow: {macro_events_tomorrow}
Earnings tomorrow (notable): {earnings_tomorrow}

═══════════════════════════════════════
TOP SETUPS ENTERING TOMORROW
(from today's full scan — watchlist + near-signals)
═══════════════════════════════════════
{setups_block}

═══════════════════════════════════════
OPEN POSITIONS ENTERING TOMORROW
═══════════════════════════════════════
{positions_block}

═══════════════════════════════════════
TODAY'S SCAN STATS
═══════════════════════════════════════
Stocks scanned: {stocks_scanned}
Passed layer 1: {passed_l1}
Passed layer 2: {passed_l2}
Signals fired:  {signals_fired}
Avg conviction: {avg_conviction}

═══════════════════════════════════════
OUTPUT SCHEMA:
{{
  "date": "{tomorrow}",
  "brief_type": "next_day_outlook",
  "macro_context": "<one sentence on macro / overnight risk heading into tomorrow>",
  "market_bias": "bullish" | "bearish" | "neutral" | "cautious",
  "market_bias_reason": "<one sentence why>",
  "key_levels_to_watch": {{
    "spy": {{
      "bull_case": <float — price SPY needs to hold/break for bulls>,
      "bear_case": <float — price SPY breaks for bears>,
      "gamma_flip": <float>
    }},
    "qqq": {{
      "bull_case": <float>,
      "bear_case": <float>,
      "gamma_flip": <float>
    }}
  }},
  "top_setups": [
    {{
      "ticker": "<ticker>",
      "direction": "long" | "short",
      "setup_type": "<tag>",
      "thesis": "<one sentence — why this is interesting tomorrow>",
      "trigger": "<exact price level or condition that activates this setup>",
      "entry_zone": {{"low": <float>, "high": <float>}},
      "stop": <float>,
      "target": <float>,
      "confidence": <int 0-100>,
      "time_sensitivity": "premarket" | "open" | "intraday" | "any"
    }}
  ],
  "positions_to_monitor": [
    {{
      "ticker": "<ticker>",
      "direction": "long" | "short",
      "priority": "high" | "medium" | "low",
      "what_to_watch": "<specific price action or level to watch tomorrow>",
      "contingency": "<what to do if X happens>"
    }}
  ],
  "scan_focus_tomorrow": "<which sectors, themes, or conditions to prioritize in tomorrow's scan>",
  "overall_game_plan": "<3-5 sentences: specific focus for tomorrow's session>",
  "risk_level": "high" | "medium" | "low",
  "risk_level_reason": "<why>"
}}
"""


def _build_setups_block(candidates: list[dict]) -> str:
    """
    Candidates are stocks that scored well today but weren't traded —
    either watchlist decisions or near-threshold scores.
    """
    if not candidates:
        return "  No high-quality setups rolling into tomorrow."
    lines = []
    for c in candidates[:10]:
        ticker    = c.get("ticker", "?")
        direction = c.get("direction", c.get("direction_bias", "?")).upper()
        score     = c.get("conviction_score", c.get("confidence", 0))
        decision  = c.get("claude_decision", c.get("decision", "watchlist")).upper()
        setup     = ", ".join(c.get("setup_type", [])) if c.get("setup_type") else "N/A"
        entry     = c.get("entry", c.get("proposed_entry", 0))
        target    = c.get("target", c.get("proposed_target", 0))
        thesis    = c.get("thesis", "")[:120]
        lines.append(
            f"  [{decision}] {ticker} {direction} | Score {score}/100 | Entry ~${entry} | Target ${target}\n"
            f"    Setup: {setup}\n"
            f"    {thesis}"
        )
    return "\n\n".join(lines)


def build_next_day_outlook_messages(
    open_positions:       list[dict],
    todays_candidates:    list[dict],  # all watchlist + near-signal stocks from today's scan
    market_context:       dict,
    scan_stats:           dict,
) -> dict:
    """Build next-day outlook payload for Claude API."""
    import datetime
    today    = datetime.date.today()
    tomorrow = today + datetime.timedelta(days=1)

    # Skip weekends for tomorrow label
    while tomorrow.weekday() >= 5:
        tomorrow += datetime.timedelta(days=1)

    spy = market_context.get("spy", {})
    qqq = market_context.get("qqq", {})
    vix = market_context.get("vix", 0)

    vix_context = (
        "low vol / complacent" if vix < 15
        else "elevated" if vix < 20
        else "high — size down" if vix < 30
        else "EXTREME — reduce risk"
    )

    spy_above_flip = "YES" if spy.get("price", 0) > spy.get("gex_flip", 0) else "NO"

    # Sort candidates: signals first, then watchlist, then by score
    decision_order = {"signal": 0, "watchlist": 1, "skip": 2}
    sorted_candidates = sorted(
        [c for c in todays_candidates if c.get("decision") != "skip"],
        key=lambda x: (
            decision_order.get(x.get("decision", "watchlist"), 1),
            -x.get("conviction_score", x.get("confidence", 0))
        )
    )

    user_content = _NEXT_DAY_PROMPT.format(
        today                 = today.strftime("%Y-%m-%d"),
        tomorrow              = tomorrow.strftime("%Y-%m-%d"),
        spy_price             = round(spy.get("price", 0), 2),
        spy_gex_regime        = spy.get("gex_regime", "N/A").upper(),
        spy_flip              = round(spy.get("gex_flip", 0), 2),
        spy_king_above        = round(spy.get("king_above", 0), 2) if spy.get("king_above") else "N/A",
        spy_king_below        = round(spy.get("king_below", 0), 2) if spy.get("king_below") else "N/A",
        qqq_price             = round(qqq.get("price", 0), 2),
        qqq_gex_regime        = qqq.get("gex_regime", "N/A").upper(),
        qqq_flip              = round(qqq.get("gex_flip", 0), 2),
        qqq_king_above        = round(qqq.get("king_above", 0), 2) if qqq.get("king_above") else "N/A",
        qqq_king_below        = round(qqq.get("king_below", 0), 2) if qqq.get("king_below") else "N/A",
        vix                   = round(vix, 2),
        vix_context           = vix_context,
        spy_above_flip        = spy_above_flip,
        market_regime         = market_context.get("regime", "unclear"),
        macro_events_tomorrow = market_context.get("macro_events_tomorrow", "None scheduled"),
        earnings_tomorrow     = market_context.get("earnings_tomorrow", "None notable"),
        setups_block          = _build_setups_block(sorted_candidates),
        positions_block       = _build_positions_block(open_positions),
        stocks_scanned        = scan_stats.get("stocks_scanned", 0),
        passed_l1             = scan_stats.get("passed_l1", 0),
        passed_l2             = scan_stats.get("passed_l2", 0),
        signals_fired         = scan_stats.get("signals_fired", 0),
        avg_conviction        = round(scan_stats.get("avg_conviction", 0), 1),
    )
    return {
        "system":   _DAILY_SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_content}],
    }
