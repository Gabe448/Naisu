"""
3-tier data pipeline.

Tier 1 — yfinance batch scan (all S&P 500):
  Score momentum. Take top 20.

Tier 2 — yfinance enrichment (top 20 only):
  Real-time price, 4H/1H bars resampled from hourly yf data, weekly pivots.

Tier 3 — LuminaFlow (top 20 only):
  GEX/DEX/VEX greek exposure.

All 20 candidates are passed to Claude analyst for free-form decisions.
No scoring gate here — Claude decides trade / watch / skip.
"""
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from scanner.sp500 import get_sp500_tickers
from scanner.yf_scanner import batch_scan
import data.yf_client as _yfc
from analysis.gex_analysis import analyze_exposure, extract_dex_bias

PIPELINE_TOP_N = int(os.environ.get("PIPELINE_TOP_N", 5))
_pipeline_lf_blocked_until = 0.0   # module-level rate limit guard


def run_pipeline(top_n: int = PIPELINE_TOP_N) -> list[dict]:
    """
    Full pipeline. Returns enriched candidates sorted by yf_score (desc),
    with scan_rank set (1 = highest momentum), ready for analyze_candidate().
    """
    sp500  = get_sp500_tickers()
    top_yf = batch_scan(sp500, top_n=top_n)

    if not top_yf:
        print("[pipeline] Tier 1 returned no candidates.")
        return []

    tickers = [c["ticker"] for c in top_yf]
    print(f"[pipeline] Enriching top {len(tickers)}: {', '.join(tickers[:10])}{'...' if len(tickers) > 10 else ''}")

    yf_by_ticker = {c["ticker"]: c for c in top_yf}
    enriched = []

    with ThreadPoolExecutor(max_workers=min(top_n, 15)) as pool:
        futures = {
            pool.submit(_enrich_ticker, ticker, yf_by_ticker[ticker]): ticker
            for ticker in tickers
        }
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                result = future.result()
                if result:
                    enriched.append(result)
            except Exception as e:
                print(f"[pipeline] Enrichment error for {ticker}: {e}", flush=True)

    enriched.sort(key=lambda c: c["yf_score"], reverse=True)

    # Assign scan rank (1 = highest momentum screener score)
    for i, candidate in enumerate(enriched, start=1):
        candidate["scan_rank"] = i

    print(f"[pipeline] {len(enriched)}/{top_n} candidates enriched and ranked.")
    return enriched


def _enrich_ticker(ticker: str, yf_candidate: dict) -> dict | None:
    """Enrich one ticker: real-time price + 4H/1H context + GEX/DEX."""
    import pandas as pd
    yf_price = yf_candidate["yf_metrics"]["current_price"]

    daily   = pd.DataFrame()
    four_h  = None
    one_h   = None
    pivots  = {}
    real_time_price = yf_price

    try:
        tf = _yfc.get_multi_tf_context(ticker)
        daily  = tf.get("daily", pd.DataFrame())
        four_h = tf.get("4h")
        one_h  = tf.get("1h")
        pivots = tf.get("weekly_pivots", {})
        if not daily.empty:
            real_time_price = float(daily["close"].iloc[-1])
    except Exception as e:
        print(f"[pipeline] yf context error for {ticker}: {e}")
        daily = yf_candidate["yf_metrics"].get("daily_df", pd.DataFrame())

    # ── Tier 3: LuminaFlow (falls back to stub on rate limit/error) ──────────
    import time as _time
    global _pipeline_lf_blocked_until
    if _time.time() >= _pipeline_lf_blocked_until:
        try:
            gex = analyze_exposure(ticker)
        except Exception as e:
            err = str(e).lower()
            if "429" in err or "rate" in err or "limit" in err:
                _pipeline_lf_blocked_until = _time.time() + 900
            gex = _yfc.get_stub_gex(ticker, real_time_price)
    else:
        gex = _yfc.get_stub_gex(ticker, real_time_price)

    flow_bias = extract_dex_bias(gex)

    # Merge computed metrics into yf_metrics for Claude
    yf_metrics = dict(yf_candidate["yf_metrics"])
    yf_metrics["daily_df"] = daily

    return {
        "ticker":          ticker,
        "yf_score":        yf_candidate["yf_score"],
        "yf_metrics":      yf_metrics,
        "real_time_price": real_time_price,
        "polygon_4h":      four_h,   # key kept for chart_renderer compat
        "polygon_1h":      one_h,
        "weekly_pivots":   pivots,
        "news":            [],
        "gex":             gex,
        "flow_bias":       flow_bias,
        "scan_rank":       0,        # filled by run_pipeline after sort
    }


def describe_pipeline_run(candidates: list[dict]) -> str:
    if not candidates:
        return "Pipeline returned 0 candidates."
    tickers = ", ".join(c["ticker"] for c in candidates[:10])
    suffix  = f" ... +{len(candidates)-10} more" if len(candidates) > 10 else ""
    scores  = [c["yf_score"] for c in candidates]
    return (
        f"Scanned S&P 500 → top {len(candidates)}: {tickers}{suffix} "
        f"(yf scores {min(scores)}-{max(scores)})"
    )
