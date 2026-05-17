"""
Backtesting engine — replays 6 months of trading days through the full pipeline.

Pipeline per day:
  1. Slice daily history to [scan_day - 3mo, scan_day] (NO lookahead)
  2. Run Tier 1 momentum scoring on daily slice → top N candidates
  3. For each candidate, build full data dict:
       - daily bars (sliced)
       - 4H bars (resampled from sliced hourly)
       - 1H bars (sliced hourly, last 5d)
       - stub GEX (no historical LuminaFlow)
       - market context (SPY/QQQ/VIX from historical bars)
  4. Call Claude analyze_candidate() → trade / watch / skip
  5. If "trade" → queue simulated position (fills next trading day's open)
  6. Advance all open positions (check stop/target/time-exit)
  7. For each closed position → call claude_reviewer.review_closed_trade()
  8. Every BATCH_SIZE closed trades → call claude_reviewer.write_batch_learnings()

Trading constraints enforced:
  - NYSE market days only (weekdays, skip known major holidays approximately)
  - Max MAX_OPEN_POSITIONS concurrent positions
  - Min 2:1 R:R (hard gate, same as live bot)

Notes on GEX:
  LuminaFlow has no historical API. We use yf_client.get_stub_gex() which
  generates price-relative placeholder levels. Claude is told this in the prompt
  and must rely on price action + momentum data instead.
"""
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


class _CreditExhausted(Exception):
    """Raised when the Anthropic API returns a credit/rate-limit error.
    Caught by the main loop to flush results and exit cleanly."""

import pandas as pd

from backtest.data_loader import (
    load_history,
    resample_4h,
    slice_daily,
    slice_hourly,
)
from backtest import position_sim as psim
from backtest import claude_reviewer as reviewer

# ── Config ────────────────────────────────────────────────────────────────────

TOP_N_CANDIDATES  = 20     # candidates passed to Claude each day
MAX_OPEN_POSITIONS = 5     # hard limit, same as live bot
MIN_RR_RATIO       = 2.0   # minimum risk:reward to accept a "trade" signal
MAX_HOLD_DAYS      = 10    # force-close after N trading days
BATCH_SIZE         = 20    # closed trades before writing Obsidian batch learnings


# ── Main entry ────────────────────────────────────────────────────────────────

def run_backtest(
    start_date:         datetime,
    end_date:           datetime,
    tickers:            Optional[list[str]] = None,
    top_n:              int  = TOP_N_CANDIDATES,
    max_positions:      int  = MAX_OPEN_POSITIONS,
    batch_size:         int  = BATCH_SIZE,
    skip_claude:        bool = False,   # dry-run: score only, skip Claude API calls
    run_id:             Optional[str] = None,
    progress:           bool = True,
) -> dict:
    """
    Run a full backtest from start_date to end_date.

    tickers: list of S&P 500 tickers to use, or None to load from scanner.sp500
    skip_claude: if True, skip Claude calls (score-only mode, much faster + free)

    Returns summary dict with stats and run_id.
    """
    if run_id is None:
        run_id = f"bt_{start_date.strftime('%Y%m%d')}_{uuid.uuid4().hex[:6]}"

    if tickers is None:
        from scanner.sp500 import get_sp500_tickers
        tickers = get_sp500_tickers()

    print(f"\n{'='*60}")
    print(f"BACKTEST RUN: {run_id}")
    print(f"Period:  {start_date.date()} → {end_date.date()}")
    print(f"Tickers: {len(tickers)}")
    print(f"Top-N:   {top_n}  |  Max positions: {max_positions}")
    print(f"Claude:  {'SKIP (dry-run)' if skip_claude else 'LIVE (costs money)'}")
    print(f"{'='*60}\n")

    # ── Phase 1: Pre-download all historical data ─────────────────────────────
    print("[engine] Phase 1: downloading historical data...")

    # Add a 3-month lead-in so day-1 scoring has a full rolling window
    data_start = start_date - timedelta(days=95)

    daily_all = load_history(tickers + ["SPY", "QQQ", "^VIX"],
                             start=data_start, end=end_date, timeframe="1d",
                             progress=progress)

    hourly_all = load_history(tickers + ["SPY", "QQQ"],
                              start=data_start, end=end_date, timeframe="1h",
                              progress=progress)

    print(f"[engine] Daily data: {len(daily_all)} tickers | Hourly: {len(hourly_all)} tickers\n")

    # ── Phase 2: Build trading calendar ──────────────────────────────────────
    trading_days = _get_trading_days(start_date, end_date, daily_all)
    print(f"[engine] {len(trading_days)} trading days to replay\n")

    # ── Phase 3: Replay loop ──────────────────────────────────────────────────
    pending_reviews: list[dict]  = []
    batch_number:    int         = 0
    total_signals:   int         = 0
    total_skips:     int         = 0
    total_closed:    int         = 0

    credit_exhausted = False

    try:
        for day_idx, scan_day in enumerate(trading_days):
            next_day = _next_trading_day(scan_day, trading_days, day_idx)

            # ── Fill pending signals at today's open (BEFORE advance_day) ──────
            # Order matters: fill at today's open, then check stop/target against
            # today's high/low. Positions that fill and immediately hit stop on
            # the same bar are correctly caught.
            psim.fill_pending(
                run_id     = run_id,
                trade_date = scan_day,
                daily_bars = daily_all,
            )

            # ── Advance existing positions (check stop/target on scan_day) ────
            closed_today = psim.advance_day(
                run_id        = run_id,
                trade_date    = scan_day,
                daily_bars    = daily_all,
                max_hold_days = MAX_HOLD_DAYS,
            )

            for closed_pos in closed_today:
                total_closed += 1
                ticker       = closed_pos["ticker"]
                ticker_daily = daily_all.get(ticker, pd.DataFrame())

                if not skip_claude:
                    try:
                        review = reviewer.review_closed_trade(closed_pos, ticker_daily)
                        if review:
                            pending_reviews.append(review)
                            _log_review(run_id, review)
                    except _CreditExhausted:
                        raise

                if not skip_claude and len(pending_reviews) >= batch_size:
                    batch_number += 1
                    try:
                        reviewer.write_batch_learnings(pending_reviews, run_id, batch_number)
                    except _CreditExhausted:
                        raise
                    pending_reviews = []

            if progress:
                open_count = len(psim.get_open_positions(run_id))
                print(f"[engine] {scan_day.date()} | open={open_count} | signals={total_signals} | closed={total_closed}")

            if next_day is None:
                continue   # last day — no new signals to queue

            # ── Check position limit before scanning ──────────────────────────
            open_count = len(psim.get_open_positions(run_id))
            if open_count >= max_positions:
                if progress:
                    print(f"  ↳ Max positions ({max_positions}) reached — skipping scan")
                continue

            # ── Tier 1: momentum scoring ──────────────────────────────────────
            candidates = _score_candidates(tickers, daily_all, scan_day, top_n)
            if not candidates:
                continue

            # ── Tier 2 + 3: Claude analysis ───────────────────────────────────
            market_ctx = _build_market_context(daily_all, hourly_all, scan_day)
            slots      = max_positions - open_count
            new_signals = 0

            for rank, cand in enumerate(candidates):
                if new_signals >= slots:
                    break

                ticker    = cand["ticker"]
                full_cand = _build_candidate(
                    ticker     = ticker,
                    cand       = cand,
                    daily_all  = daily_all,
                    hourly_all = hourly_all,
                    scan_day   = scan_day,
                )
                if full_cand is None:
                    continue

                if skip_claude:
                    if rank < 5:
                        _queue_score_only_signal(run_id, full_cand, scan_day)
                        new_signals  += 1
                        total_signals += 1
                    continue

                result = _run_claude(full_cand, market_ctx, rank)  # raises _CreditExhausted
                if result is None:
                    continue

                result = _resolve_watch(result)  # promote watch→trade if setup is complete

                if result.get("decision") == "trade":
                    if _try_queue_signal(run_id, result, full_cand, scan_day):
                        new_signals   += 1
                        total_signals += 1
                else:
                    total_skips += 1

    except _CreditExhausted as exc:
        credit_exhausted = True
        print(f"\n[engine] ⚠️  CREDIT EXHAUSTED — stopping early. Saving collected results.")
        print(f"[engine]    {exc}")
        print(f"[engine]    Days completed: {day_idx + 1}/{len(trading_days)} | "
              f"Signals: {total_signals} | Closed: {total_closed}")

    # ── Final batch flush (runs whether we finished or were interrupted) ──────
    if not skip_claude and pending_reviews:
        batch_number += 1
        try:
            reviewer.write_batch_learnings(pending_reviews, run_id, batch_number)
        except Exception as e:
            print(f"[engine] Final batch write failed ({e}) — reviews logged to JSONL only")

    # ── Summary ───────────────────────────────────────────────────────────────
    stats = psim.summary_stats(run_id)
    stats["run_id"]            = run_id
    stats["start_date"]        = start_date.isoformat()
    stats["end_date"]          = end_date.isoformat()
    stats["signals"]           = total_signals
    stats["batches"]           = batch_number
    stats["skip_claude"]       = skip_claude
    stats["credit_exhausted"]  = credit_exhausted
    stats["days_completed"]    = day_idx + 1 if "day_idx" in dir() else 0

    _print_summary(stats)
    _save_summary(run_id, stats)

    return stats


# ── From dry-run entry point ─────────────────────────────────────────────────

def run_backtest_from_dryrun(
    dryrun_run_id:  str,
    max_positions:  int  = MAX_OPEN_POSITIONS,
    batch_size:     int  = BATCH_SIZE,
    run_id:         Optional[str] = None,
    progress:       bool = True,
) -> dict:
    """
    Run Claude analysis ONLY on the candidates the dry-run already selected.

    Instead of re-scanning 500 tickers each day, loads the dry-run's positions
    file, groups signals by date, and calls Claude on those specific ticker+date
    combos. ~N_signals total Claude calls (e.g. 125) instead of top_n × days.

    The dry-run's stop/target levels are REPLACED by Claude's own levels if
    Claude decides "trade". If Claude says "skip", that signal is dropped.
    """
    import json as _json

    # ── Load dry-run signals ──────────────────────────────────────────────────
    store_path = psim.RESULTS_DIR / f"positions_{dryrun_run_id}.json"
    if not store_path.exists():
        raise FileNotFoundError(f"Dry-run positions not found: {store_path}")

    with open(store_path, "r", encoding="utf-8") as f:
        dryrun_data = _json.load(f)

    all_dryrun = (
        dryrun_data.get("closed", []) +
        dryrun_data.get("open", []) +
        dryrun_data.get("pending", [])
    )
    if not all_dryrun:
        raise ValueError(f"No signals found in dry-run {dryrun_run_id}")

    # Group by signal_date → {date: [(ticker, dry_pos)]}
    from collections import defaultdict
    signals_by_date: dict[str, list[dict]] = defaultdict(list)
    for pos in all_dryrun:
        sig_date = pos["signal_date"][:10]   # "YYYY-MM-DD"
        signals_by_date[sig_date].append(pos)

    # Derive date range and unique tickers from the dry-run
    all_dates   = sorted(signals_by_date.keys())
    start_date  = datetime.fromisoformat(all_dates[0])
    end_date    = datetime.fromisoformat(all_dates[-1]) + timedelta(days=MAX_HOLD_DAYS + 5)
    all_tickers = list({pos["ticker"] for pos in all_dryrun})

    if run_id is None:
        run_id = f"bt_claude_{dryrun_run_id[:16]}_{uuid.uuid4().hex[:4]}"

    print(f"\n{'='*60}")
    print(f"BACKTEST FROM DRY-RUN: {run_id}")
    print(f"Source dry-run: {dryrun_run_id}")
    print(f"Signals to evaluate: {len(all_dryrun)} across {len(all_dates)} days")
    print(f"Unique tickers: {len(all_tickers)}")
    print(f"Max positions: {max_positions}")
    print(f"{'='*60}\n")

    # ── Download data only for tickers in the dry-run ────────────────────────
    print("[engine] Downloading historical data for dry-run tickers...")
    data_start = start_date - timedelta(days=95)

    daily_all  = load_history(all_tickers + ["SPY", "QQQ", "^VIX"],
                              start=data_start, end=end_date, timeframe="1d",
                              progress=progress)
    hourly_all = load_history(all_tickers + ["SPY", "QQQ"],
                              start=data_start, end=end_date, timeframe="1h",
                              progress=progress)

    print(f"[engine] Data ready. Starting replay...\n")

    # ── Replay loop ───────────────────────────────────────────────────────────
    trading_days    = _get_trading_days(start_date, end_date, daily_all)
    pending_reviews: list[dict] = []
    batch_number    = 0
    total_signals   = 0
    total_closed    = 0
    credit_exhausted = False
    day_idx          = 0

    try:
        for day_idx, scan_day in enumerate(trading_days):
            next_day   = _next_trading_day(scan_day, trading_days, day_idx)
            date_str   = scan_day.strftime("%Y-%m-%d")
            todays_signals = signals_by_date.get(date_str, [])

            # Fill then advance (correct order)
            psim.fill_pending(run_id=run_id, trade_date=scan_day, daily_bars=daily_all)

            closed_today = psim.advance_day(
                run_id=run_id, trade_date=scan_day,
                daily_bars=daily_all, max_hold_days=MAX_HOLD_DAYS,
            )

            for closed_pos in closed_today:
                total_closed += 1
                if not False:  # skip_claude is always False here
                    try:
                        review = reviewer.review_closed_trade(
                            closed_pos, daily_all.get(closed_pos["ticker"], pd.DataFrame())
                        )
                        if review:
                            pending_reviews.append(review)
                            _log_review(run_id, review)
                    except _CreditExhausted:
                        raise

                if len(pending_reviews) >= batch_size:
                    batch_number += 1
                    try:
                        reviewer.write_batch_learnings(pending_reviews, run_id, batch_number)
                    except _CreditExhausted:
                        raise
                    pending_reviews = []

            if progress:
                open_count = len(psim.get_open_positions(run_id))
                print(
                    f"[engine] {date_str} | open={open_count} | "
                    f"signals={total_signals} | closed={total_closed}"
                    + (f" | {len(todays_signals)} to evaluate" if todays_signals else "")
                )

            if next_day is None or not todays_signals:
                continue

            # ── Claude analysis: only this day's dry-run candidates ───────────
            open_count = len(psim.get_open_positions(run_id))
            slots      = max_positions - open_count
            if slots <= 0:
                if progress:
                    print(f"  ↳ Max positions reached — skipping {len(todays_signals)} candidates")
                continue

            market_ctx   = _build_market_context(daily_all, hourly_all, scan_day)
            new_signals  = 0

            for dry_pos in todays_signals:
                if new_signals >= slots:
                    break

                ticker    = dry_pos["ticker"]
                # Reconstruct a minimal candidate dict from dry-run data + historical bars
                daily_slice  = slice_daily(daily_all.get(ticker, pd.DataFrame()), scan_day)
                if daily_slice.empty:
                    continue

                import data.yf_client as _yfc
                price        = float(daily_slice["close"].iloc[-1])
                hourly_slice = slice_hourly(hourly_all.get(ticker, pd.DataFrame()), scan_day)
                four_h       = resample_4h(hourly_slice).tail(10) if not hourly_slice.empty else pd.DataFrame()
                one_h        = hourly_slice.tail(10) if not hourly_slice.empty else pd.DataFrame()

                # Re-score so yf_metrics is populated
                from scanner.yf_scanner import _score_ticker
                df_yf = daily_slice.rename(columns={
                    "open": "Open", "high": "High",
                    "low": "Low", "close": "Close", "volume": "Volume",
                })
                try:
                    score, metrics = _score_ticker(ticker, df_yf)
                except Exception:
                    score, metrics = dry_pos.get("conviction", 50), {}

                metrics["daily_df"] = daily_slice
                full_cand = {
                    "ticker":          ticker,
                    "yf_score":        score,
                    "yf_metrics":      metrics,
                    "real_time_price": price,
                    "polygon_4h":      four_h,
                    "polygon_1h":      one_h,
                    "weekly_pivots":   _compute_pivots_from_daily(daily_slice),
                    "news":            [],
                    "gex":             _yfc.get_stub_gex(ticker, price),
                    "flow_bias":       _yfc.get_momentum_flow_bias(ticker, daily_slice),
                }

                result = _run_claude(full_cand, market_ctx, rank=0)
                if result is None:
                    continue

                result = _resolve_watch(result)

                if result.get("decision") == "trade":
                    if _try_queue_signal(run_id, result, full_cand, scan_day):
                        new_signals   += 1
                        total_signals += 1

    except _CreditExhausted as exc:
        credit_exhausted = True
        print(f"\n[engine] ⚠️  CREDIT EXHAUSTED — saving collected results.")
        print(f"[engine]    {exc}")

    # Final batch flush
    if pending_reviews:
        batch_number += 1
        try:
            reviewer.write_batch_learnings(pending_reviews, run_id, batch_number)
        except Exception as e:
            print(f"[engine] Final batch write failed: {e}")

    stats = psim.summary_stats(run_id)
    stats.update({
        "run_id": run_id, "dryrun_source": dryrun_run_id,
        "start_date": start_date.isoformat(), "end_date": end_date.isoformat(),
        "signals": total_signals, "batches": batch_number,
        "credit_exhausted": credit_exhausted, "days_completed": day_idx + 1,
    })
    _print_summary(stats)
    _save_summary(run_id, stats)
    return stats


# ── Scoring replay (Tier 1) ───────────────────────────────────────────────────

def _score_candidates(
    tickers:   list[str],
    daily_all: dict[str, pd.DataFrame],
    scan_day:  datetime,
    top_n:     int,
) -> list[dict]:
    """
    Re-run Tier 1 momentum scoring on historical daily slices.
    Matches yf_scanner._score_ticker() exactly.
    """
    from scanner.yf_scanner import _score_ticker

    scored = []
    for ticker in tickers:
        df_full = daily_all.get(ticker)
        if df_full is None or df_full.empty:
            continue

        df_slice = slice_daily(df_full, scan_day, lookback_days=90)
        if len(df_slice) < 55:
            continue

        # _score_ticker expects columns: Open, High, Low, Close, Volume (capitalised)
        df_yf = df_slice.rename(columns={
            "open": "Open", "high": "High",
            "low":  "Low",  "close": "Close", "volume": "Volume",
        })

        try:
            score, metrics = _score_ticker(ticker, df_yf)
            if score > 0:
                scored.append({
                    "ticker":     ticker,
                    "yf_score":   score,
                    "yf_metrics": metrics,
                })
        except Exception:
            continue

    scored.sort(key=lambda x: x["yf_score"], reverse=True)
    return scored[:top_n]


# ── Candidate builder (Tier 2) ────────────────────────────────────────────────

def _build_candidate(
    ticker:     str,
    cand:       dict,
    daily_all:  dict[str, pd.DataFrame],
    hourly_all: dict[str, pd.DataFrame],
    scan_day:   datetime,
) -> Optional[dict]:
    """Build full candidate dict matching what pipeline.py produces."""
    try:
        import data.yf_client as _yfc

        daily_slice  = slice_daily(daily_all.get(ticker, pd.DataFrame()), scan_day)
        hourly_slice = slice_hourly(hourly_all.get(ticker, pd.DataFrame()), scan_day)

        if daily_slice.empty:
            return None

        four_h = resample_4h(hourly_slice).tail(10) if not hourly_slice.empty else pd.DataFrame()
        one_h  = hourly_slice.tail(10)              if not hourly_slice.empty else pd.DataFrame()

        # Weekly pivots from historical daily
        pivots = _compute_pivots_from_daily(daily_slice)

        price = float(daily_slice["close"].iloc[-1])
        gex   = _yfc.get_stub_gex(ticker, price)   # no historical LuminaFlow

        yf_metrics = cand["yf_metrics"].copy()
        yf_metrics["daily_df"] = daily_slice

        return {
            "ticker":          ticker,
            "yf_score":        cand["yf_score"],
            "yf_metrics":      yf_metrics,
            "real_time_price": price,
            "polygon_4h":      four_h,
            "polygon_1h":      one_h,
            "weekly_pivots":   pivots,
            "news":            [],          # no historical news in free tier
            "gex":             gex,
            "flow_bias":       _yfc.get_momentum_flow_bias(ticker, daily_slice),
            "_scan_day":       scan_day,    # metadata for debugging
        }
    except Exception as e:
        print(f"[engine] _build_candidate error for {ticker}: {e}")
        return None


# ── Market context builder ────────────────────────────────────────────────────

def _build_market_context(
    daily_all:  dict[str, pd.DataFrame],
    hourly_all: dict[str, pd.DataFrame],
    scan_day:   datetime,
) -> dict:
    """Build SPY/QQQ/VIX context from historical data (no live calls)."""
    ctx: dict = {"spy": {}, "qqq": {}, "vix": 0.0, "regime": "unclear"}

    for sym, key in [("SPY", "spy"), ("QQQ", "qqq")]:
        df = daily_all.get(sym)
        if df is None or df.empty:
            continue
        sliced = slice_daily(df, scan_day, lookback_days=30)
        if sliced.empty:
            continue
        price  = float(sliced["close"].iloc[-1])
        recent = sliced["close"].iloc[-3:].mean()
        earlier= sliced["close"].iloc[-6:-3].mean() if len(sliced) >= 6 else price
        trend  = "UP" if recent > earlier else "DOWN"
        ctx[key] = {
            "price":      price,
            "gex_regime": "unknown",
            "gex_flip":   0,
            "trend":      trend,
            "flow_bias":  "bullish" if trend == "UP" else "bearish",
        }

    vix_df = daily_all.get("^VIX")
    if vix_df is not None and not vix_df.empty:
        vix_slice = slice_daily(vix_df, scan_day, lookback_days=5)
        if not vix_slice.empty:
            ctx["vix"] = float(vix_slice["close"].iloc[-1])

    spy_t = ctx["spy"].get("trend", "")
    qqq_t = ctx["qqq"].get("trend", "")
    vix   = ctx["vix"]
    if spy_t == "UP" and qqq_t == "UP":
        ctx["regime"] = "risk_on" if vix < 20 else "choppy_bull"
    elif spy_t == "DOWN" and qqq_t == "DOWN":
        ctx["regime"] = "risk_off" if vix > 20 else "mild_pullback"
    else:
        ctx["regime"] = "mixed"

    return ctx


# ── Claude call wrapper ───────────────────────────────────────────────────────

_BACKTEST_SYSTEM_PROMPT_SUFFIX = """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BACKTEST MODE — CRITICAL OVERRIDE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
You are evaluating a HISTORICAL setup. The date has already passed.

This changes the decision rules:
- "watch" is BANNED. Do not use it. In live trading you watch and wait for a trigger.
  In backtesting the market has already opened — the trigger either fired or it didn't.
  You must commit: "trade" or "skip".
- GEX data is synthetic (price-relative stubs only). Do not penalise the setup for
  missing GEX confirmation. Base your decision on price structure and momentum data.
- No news is available for historical dates. Treat absence of news as neutral.
- If the setup has clear structure (momentum, MA position, volume, S/R) and the
  R:R math works, that is a "trade". Uncertainty about a live trigger is not a
  reason to skip — it is a reason to assign lower confidence (40-65 range).
- Only skip if price action is genuinely ambiguous, R:R < 2:1, or the setup
  contradicts your core rules (e.g. fighting a strong trend, stop inside noise).
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""


def _run_claude(candidate: dict, market_ctx: dict, rank: int) -> Optional[dict]:
    """Call Claude with a backtest-specific system prompt that bans 'watch'.
    Raises _CreditExhausted if the API returns a credit or billing error."""
    try:
        import json
        import anthropic as _ant
        from analysis.claude_analyst import (
            _SYSTEM_PROMPT, _format_raw_data, _render_chart_b64,
            load_full_learnings, load_trade_history,
        )

        candidate_with_note = candidate.copy()
        candidate_with_note["_backtest_note"] = (
            "BACKTEST: GEX is synthetic. No news. Decide trade or skip only."
        )

        backtest_system = _SYSTEM_PROMPT + _BACKTEST_SYSTEM_PROMPT_SUFFIX

        learnings     = load_full_learnings()
        trade_history = load_trade_history(n=20)
        raw_data      = _format_raw_data(candidate_with_note, market_ctx, rank)
        chart_b64     = _render_chart_b64(candidate_with_note)

        user_content = [
            {
                "type": "text",
                "text": (
                    f"Analyze this candidate for a swing trade.\n\n"
                    f"{'='*55}\nCOMPLETE TRADE MEMORY\n{'='*55}\n{learnings}\n\n"
                    f"{'='*55}\nRECENT TRADE HISTORY\n{'='*55}\n{trade_history}\n\n"
                    f"{'='*55}\nRAW MARKET DATA\n{'='*55}\n{raw_data}"
                ),
            }
        ]
        if chart_b64:
            user_content.append({"type": "text", "text": "Chart (daily candles, MA20/50, volume):"})
            user_content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": chart_b64}})
        else:
            user_content.append({"type": "text", "text": "(Chart unavailable)"})
        user_content.append({"type": "text", "text": "Return ONLY valid JSON. No text outside the JSON object."})

        ticker = candidate_with_note.get("ticker", "?")
        from utils.claude_limiter import acquire as _rl_acquire, log_call as _rl_log
        _BACKTEST_MODEL = "claude-sonnet-4-6"
        _rl_acquire(f"_run_claude({ticker})")
        response = _ant.Anthropic().messages.create(
            model      = _BACKTEST_MODEL,
            max_tokens = 2048,
            system     = backtest_system,
            messages   = [{"role": "user", "content": user_content}],
        )
        _rl_log(f"_run_claude({ticker})", _BACKTEST_MODEL,
                response.usage.input_tokens, response.usage.output_tokens)

        raw_text = response.content[0].text.strip()
        if raw_text.startswith("```"):
            parts    = raw_text.split("```")
            raw_text = parts[1] if len(parts) > 1 else raw_text
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]

        result = json.loads(raw_text.strip())
        result["ticker"] = ticker
        print(
            f"[engine] {ticker}: {result.get('decision','?').upper()} "
            f"conf={result.get('confidence','?')} dir={result.get('direction','?')}",
            flush=True,
        )
        return result

    except _CreditExhausted:
        raise
    except RuntimeError as e:
        print(f"[engine] {e}", flush=True)
        return None
    except Exception as e:
        msg = str(e).lower()
        if "credit" in msg or "billing" in msg or getattr(e, "status_code", None) in (402, 403):
            raise _CreditExhausted(f"Anthropic credit/billing error: {e}") from e
        print(f"[engine] Claude error for {candidate.get('ticker', '?')}: {e}")
        return None


# ── Watch promotion ──────────────────────────────────────────────────────────

def _resolve_watch(result: dict) -> dict:
    """
    Last-resort safety net: if Claude still returns 'watch' despite the prompt,
    promote to 'trade' when the setup is complete and confidence is usable.

    Criteria for promotion:
      - Has entry, stop, t1 all set
      - Confidence >= 55 (Claude thinks it's real, just hedging on trigger)
      - R:R >= MIN_RR_RATIO

    If not promotable, demote to 'skip' so credits aren't wasted on a position
    that will never fill.
    """
    if result.get("decision") != "watch":
        return result

    entry = result.get("entry")
    stop  = result.get("stop")
    t1    = result.get("t1")
    conf  = result.get("confidence", 0)

    if entry and stop and t1 and conf >= 55:
        risk   = abs(entry - stop)
        reward = abs(t1 - entry)
        if risk > 0 and reward / risk >= MIN_RR_RATIO:
            result = result.copy()
            result["decision"] = "trade"
            result["thesis"]   = (result.get("thesis") or "") + " [watch→trade: backtest promotion, setup complete]"
            print(f"[engine] {result.get('ticker','?')}: watch→trade (conf={conf}, R:R={reward/risk:.1f})")
            return result

    # Can't promote — demote to skip
    result = result.copy()
    result["decision"] = "skip"
    return result


# ── Signal queueing ───────────────────────────────────────────────────────────

def _try_queue_signal(
    run_id:    str,
    result:    dict,
    candidate: dict,
    scan_day:  datetime,
) -> bool:
    """
    Validate R:R and queue position if acceptable.
    Returns True if signal was queued.
    """
    ticker    = result.get("ticker", candidate["ticker"])
    direction = result.get("direction")
    entry     = result.get("entry")
    stop      = result.get("stop")
    t1        = result.get("t1")

    if not all([direction, entry, stop, t1]):
        return False

    # R:R check
    risk   = abs(entry - stop)
    reward = abs(t1 - entry)
    if risk <= 0:
        return False
    rr = reward / risk
    if rr < MIN_RR_RATIO:
        return False

    target = result.get("t2") or t1  # use T2 if available, else T1

    psim.queue_signal(
        run_id       = run_id,
        ticker       = ticker,
        direction    = direction,
        signal_date  = scan_day,
        stop_price   = stop,
        target_price = target,
        conviction   = result.get("confidence", 0),
        thesis       = result.get("thesis", ""),
        claude_json  = result,
    )

    print(
        f"  ↳ SIGNAL: {ticker} {direction.upper()} "
        f"entry=${entry:.2f} stop=${stop:.2f} target=${target:.2f} "
        f"R:R={rr:.1f} conf={result.get('confidence', '?')}"
    )
    return True


def _queue_score_only_signal(run_id: str, candidate: dict, scan_day: datetime) -> None:
    """
    Dry-run mode: queue a signal based on momentum score + ATR-derived stops.

    Entry direction: long if 10-day momentum > 0, short otherwise.
    Stop:   1.5 × ATR(14) from entry — survives normal daily noise per ticker.
    Target: 3.0 × ATR(14) from entry — 2:1 R:R anchored to actual volatility.

    Fallback if ATR unavailable: swing structure (5-bar recent low/high).
    Second fallback: 2% stop / 4% target (only if no price data at all).
    """
    ticker    = candidate["ticker"]
    daily_df  = candidate.get("yf_metrics", {}).get("daily_df")
    price     = candidate.get("real_time_price", 0)
    if price <= 0:
        return

    direction = "long" if (candidate["yf_metrics"].get("momentum_10d") or 0) > 0 else "short"

    stop, target = _atr_stop_target(daily_df, price, direction)

    if stop is None or target is None:
        # Fallback 1: swing structure
        stop, target = _swing_stop_target(daily_df, price, direction)

    if stop is None or target is None:
        # Fallback 2: flat % (last resort only)
        stop   = round(price * (0.98 if direction == "long" else 1.02), 2)
        target = round(price * (1.04 if direction == "long" else 0.96), 2)

    psim.queue_signal(
        run_id       = run_id,
        ticker       = ticker,
        direction    = direction,
        signal_date  = scan_day,
        stop_price   = stop,
        target_price = target,
        conviction   = candidate["yf_score"],
        thesis       = f"Dry-run | score={candidate['yf_score']} | ATR stop @ ${stop} | target @ ${target}",
        claude_json  = {},
    )


def _atr_stop_target(
    daily_df,
    price: float,
    direction: str,
    atr_period: int = 14,
    stop_mult:  float = 1.5,
    target_mult: float = 3.0,
) -> tuple:
    """
    Compute stop/target using ATR(14).
    ATR uses Wilder's smoothing: same as TradingView default.
    Returns (stop, target) or (None, None) if data insufficient.
    """
    if daily_df is None or len(daily_df) < atr_period + 1:
        return None, None

    try:
        hi = daily_df["high"]
        lo = daily_df["low"]
        cl = daily_df["close"]

        prev_close = cl.shift(1)
        tr = pd.concat([
            hi - lo,
            (hi - prev_close).abs(),
            (lo - prev_close).abs(),
        ], axis=1).max(axis=1)

        # Wilder smoothing (EMA with alpha = 1/period)
        atr = tr.ewm(alpha=1 / atr_period, adjust=False).mean().iloc[-1]

        if atr <= 0 or pd.isna(atr):
            return None, None

        if direction == "long":
            stop   = round(price - stop_mult * atr, 2)
            target = round(price + target_mult * atr, 2)
        else:
            stop   = round(price + stop_mult * atr, 2)
            target = round(price - target_mult * atr, 2)

        # Sanity: stop must not be within 0.5% of price (too tight = noise)
        if abs(price - stop) / price < 0.005:
            return None, None

        return stop, target
    except Exception:
        return None, None


def _swing_stop_target(daily_df, price: float, direction: str) -> tuple:
    """
    Fallback stop: recent 5-bar swing low (long) or swing high (short).
    Target: 2× risk from entry.
    """
    if daily_df is None or len(daily_df) < 6:
        return None, None
    try:
        recent = daily_df.tail(6).iloc[:-1]   # exclude today's bar
        if direction == "long":
            swing_stop = round(float(recent["low"].min()), 2)
        else:
            swing_stop = round(float(recent["high"].max()), 2)

        risk = abs(price - swing_stop)
        if risk <= 0 or risk / price > 0.15:   # ignore if > 15% away (too wide)
            return None, None

        if direction == "long":
            target = round(price + 2 * risk, 2)
        else:
            target = round(price - 2 * risk, 2)

        return swing_stop, target
    except Exception:
        return None, None


# ── Trading calendar ──────────────────────────────────────────────────────────

def _get_trading_days(
    start: datetime,
    end:   datetime,
    daily_all: dict[str, pd.DataFrame],
) -> list[datetime]:
    """
    Derive actual trading days from SPY daily bars.
    Cleanest approach: if SPY traded that day, it's a market day.
    """
    spy = daily_all.get("SPY")
    if spy is not None and not spy.empty:
        mask = (spy.index >= pd.Timestamp(start)) & (spy.index <= pd.Timestamp(end))
        return [d.to_pydatetime() for d in spy.index[mask]]

    # Fallback: weekdays only
    days = []
    cur  = start
    while cur <= end:
        if cur.weekday() < 5:  # Mon–Fri
            days.append(cur)
        cur += timedelta(days=1)
    return days


def _next_trading_day(
    current:  datetime,
    calendar: list[datetime],
    idx:      int,
) -> Optional[datetime]:
    if idx + 1 < len(calendar):
        return calendar[idx + 1]
    return None


# ── Pivot computation ─────────────────────────────────────────────────────────

def _compute_pivots_from_daily(daily: pd.DataFrame) -> dict:
    """Compute weekly pivots from historical daily bars. No lookahead."""
    if daily.empty or len(daily) < 5:
        return {}
    idx    = pd.to_datetime(daily.index)
    week   = idx.isocalendar().week.values
    year   = idx.isocalendar().year.values
    cur_w, cur_y = week[-1], year[-1]
    mask   = (week != cur_w) | (year != cur_y)
    prev   = daily[mask]
    if prev.empty:
        return {}
    H  = prev["high"].max()
    L  = prev["low"].min()
    C  = float(prev["close"].iloc[-1])
    P  = (H + L + C) / 3
    return {
        "P": P, "R1": 2*P - L, "R2": P + (H-L),
        "S1": 2*P - H, "S2": P - (H-L),
        "prev_high": H, "prev_low": L,
    }


# ── Output helpers ────────────────────────────────────────────────────────────

def _log_review(run_id: str, review: dict) -> None:
    """Append individual trade review to run log file."""
    from backtest.position_sim import RESULTS_DIR
    import json
    path = RESULTS_DIR / f"reviews_{run_id}.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(review) + "\n")


def _print_summary(stats: dict) -> None:
    print(f"\n{'='*60}")
    print(f"BACKTEST COMPLETE — {stats['run_id']}")
    print(f"  Trades:      {stats.get('total', 0)}")
    print(f"  Wins:        {stats.get('wins', 0)}  ({stats.get('win_rate', 0)}%)")
    print(f"  Losses:      {stats.get('losses', 0)}")
    print(f"  Avg Win:     {stats.get('avg_win', 0):+.2f}%")
    print(f"  Avg Loss:    {stats.get('avg_loss', 0):+.2f}%")
    print(f"  Expectancy:  {stats.get('expectancy', 0):+.2f}% per trade")
    print(f"  Total PnL:   {stats.get('total_pnl', 0):+.2f}%")
    print(f"  Signals:     {stats.get('signals', 0)}")
    print(f"  Batches:     {stats.get('batches', 0)}")
    print(f"{'='*60}\n")


def _save_summary(run_id: str, stats: dict) -> None:
    import json
    from backtest.position_sim import RESULTS_DIR
    path = RESULTS_DIR / f"summary_{run_id}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, default=str)
    print(f"[engine] Summary saved → {path}")
