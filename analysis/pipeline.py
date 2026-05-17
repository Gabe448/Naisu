"""
Analysis pipeline — ties every analysis layer together for a single ticker.

Usage:
    from analysis.pipeline import analyse_ticker
    result = analyse_ticker("NVDA", daily_df, gex_result=None)
    if result["tradeable"]:
        post_to_discord(format_trade_card(result["trade"]))

Pipeline stages:
  1. compute_emas        — add EMA 20/50/100/200 to DataFrame
  2. get_key_levels      — cluster pivot highs/lows into S/R levels
  3. scan_patterns       — detect chart patterns (H&S, wedge, triangle…)
  4. run_all_signals     — fire the 5 rule-based signals
  5. parse_gex_context   — extract GEX regime + DEX bias from LuminaFlow
  6. score_confluence    — combine pattern + signals + DEX (need 3+)
  7. conviction_score    — 0-100 final score
  8. build_trade         — entry / stop / T1 / T2 / T3 / R:R
"""
from __future__ import annotations

import pandas as pd

from analysis.confluence import ConfluenceResult, score_confluence
from analysis.gex_analysis import GexContext, extract_dex_bias, parse_gex_context
from analysis.levels import compute_emas, get_key_levels, nearest_resistance, nearest_support
from analysis.patterns import PatternResult, scan_patterns
from analysis.scorer import conviction_score, grade
from analysis.signals import run_all_signals
from analysis.trade_builder import TradeIdea, build_trade, compute_atr, format_trade_card


def analyse_ticker(
    ticker:      str,
    df:          pd.DataFrame,
    gex_result:  dict | None = None,
    yf_score:    int         = 0,
    conviction_threshold: int = 70,
) -> dict:
    """
    Full analysis for one ticker.

    Args:
        ticker      — symbol string
        df          — daily OHLCV DataFrame with columns open/high/low/close/volume
        gex_result  — raw LuminaFlow GEX API response (optional)
        yf_score    — 0-100 momentum score from yf_scanner
        conviction_threshold — min score to flag as tradeable

    Returns a dict with keys:
        ticker, direction, conviction, grade, tradeable,
        pattern, confluence, gex, trade, trade_card, signals
    """
    if len(df) < 30:
        return _empty_result(ticker, "Insufficient data (< 30 bars)")

    # ── 1. EMAs ───────────────────────────────────────────────────────────────
    df_ema = compute_emas(df)

    # ── 2. S/R levels ─────────────────────────────────────────────────────────
    levels = get_key_levels(df_ema, window=5)

    # ── 3. Patterns ───────────────────────────────────────────────────────────
    patterns = scan_patterns(df_ema)
    top_pattern: PatternResult | None = patterns[0] if patterns else None

    # ── 4. Signals ────────────────────────────────────────────────────────────
    current  = float(df_ema["close"].iloc[-1])
    support  = nearest_support(levels, current)
    resist   = nearest_resistance(levels, current)
    key_level = support or resist   # use whichever is closer

    signals = run_all_signals(df_ema, key_level=key_level)

    # ── 5. GEX / DEX ──────────────────────────────────────────────────────────
    gex_ctx: GexContext | None = None
    dex_bias: dict | None      = None

    if gex_result is not None:
        gex_result["ticker"] = ticker
        gex_ctx  = parse_gex_context(ticker, gex_result)
        dex_bias = extract_dex_bias(gex_result)

    # ── 6. Confluence ─────────────────────────────────────────────────────────
    confluence = score_confluence(
        ticker=ticker,
        df=df_ema,
        pattern=top_pattern,
        signals=signals,
        dex_bias=dex_bias,
    )

    # ── 7. Conviction score ───────────────────────────────────────────────────
    score  = conviction_score(confluence, yf_score=yf_score)
    letter = grade(score)

    # ── 8. Trade structure ────────────────────────────────────────────────────
    atr     = compute_atr(df_ema)
    trigger = top_pattern.trigger if top_pattern else current

    thesis_parts = []
    if top_pattern:
        thesis_parts.append(top_pattern.description)
    thesis_parts += [n for sig in signals if sig.fired for n in sig.notes[:1]]
    thesis = " | ".join(thesis_parts) or f"{ticker} confluence setup"

    trade = build_trade(
        ticker=ticker,
        direction=confluence.direction if confluence.direction != "neutral" else "long",
        entry=current,
        levels=levels,
        atr=atr,
        trigger_level=trigger,
        conviction=score,
        thesis=thesis,
        conviction_threshold=conviction_threshold,
    )

    trade_card = format_trade_card(trade) if confluence.direction != "neutral" else ""

    return {
        "ticker":     ticker,
        "direction":  confluence.direction,
        "conviction": score,
        "grade":      letter,
        "tradeable":  trade.tradeable,
        "pattern":    top_pattern,
        "confluence": confluence,
        "gex":        gex_ctx,
        "trade":      trade,
        "trade_card": trade_card,
        "signals":    signals,
    }


def _empty_result(ticker: str, reason: str) -> dict:
    return {
        "ticker":     ticker,
        "direction":  "neutral",
        "conviction": 0,
        "grade":      "D — Skip",
        "tradeable":  False,
        "pattern":    None,
        "confluence": None,
        "gex":        None,
        "trade":      None,
        "trade_card": "",
        "signals":    [],
        "reason":     reason,
    }
