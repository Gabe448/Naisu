"""
yfinance batch momentum scanner — Tier 1 of the data pipeline.

Downloads 3 months of daily bars for ALL S&P 500 tickers in one call.
Costs nothing. Scores each ticker 0-100. Returns top N candidates.

Scoring breakdown (100 pts):
  25 pts — Relative volume   (unusual volume = something's happening)
  20 pts — Setup quality     (pullback-to-MA or fresh MA cross — actionable NOW)
  20 pts — 10-day momentum   (recent directional move confirms trend exists)
  20 pts — MA trend alignment (MA20 and MA50 pointing same direction)
  15 pts — 52-week high proximity (breakout potential)

The key change vs pure momentum scoring: "setup quality" rewards stocks
pulling back to MA20/MA50 or crossing above them — those are the GEX flip
setups Claude is looking for. Pure momentum stocks that already ran score
lower here, surfacing fresher entries.

yfinance data is 15-min delayed — used ONLY for screening.
"""
import warnings
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore", category=FutureWarning)

# Rolling blacklist — tickers seen in recent scans are deprioritised
_recent_scan_tickers: list[set] = []   # list of sets, newest first
_BLACKLIST_DEPTH = 2                   # skip tickers seen in last 2 scans


def batch_scan(tickers: list[str], top_n: int = 10) -> list[dict]:
    """
    Download daily bars for all tickers, score each, return top N.
    Tickers seen in the last 2 scans are excluded to force fresh names.
    Typical runtime: 5-15 seconds for 500 tickers.
    """
    print(f"[yf_scanner] Downloading {len(tickers)} tickers...")

    # Chunk downloads to avoid timeouts — 200 per batch
    all_data = _download_in_chunks(tickers, chunk_size=200)
    if all_data is None or all_data.empty:
        print("[yf_scanner] Download failed — no data returned.")
        return []

    # Build blacklist from recent scans
    blacklist: set[str] = set()
    for past in _recent_scan_tickers[:_BLACKLIST_DEPTH]:
        blacklist |= past

    scored = []
    for ticker in tickers:
        try:
            df = _extract_ticker(all_data, ticker, tickers)
            if df is None or len(df) < 55:   # need 55 bars for 50MA + buffer
                continue
            score, metrics = _score_ticker(ticker, df)
            if score > 0:
                # Penalise recently-seen tickers instead of hard-excluding,
                # so the list still fills if market is very narrow
                if ticker in blacklist:
                    score = max(0, score - 30)
                scored.append({"ticker": ticker, "yf_score": score, "yf_metrics": metrics})
        except Exception:
            continue

    scored.sort(key=lambda x: x["yf_score"], reverse=True)
    top = scored[:top_n]

    # Update rolling blacklist
    _recent_scan_tickers.insert(0, {c["ticker"] for c in top})
    if len(_recent_scan_tickers) > _BLACKLIST_DEPTH + 1:
        _recent_scan_tickers.pop()

    print(f"[yf_scanner] Scored {len(scored)} tickers. Top {top_n}:")
    for c in top:
        m = c["yf_metrics"]
        print(
            f"  {c['ticker']:6} score={c['yf_score']:3} | "
            f"relVol={m['rel_volume']:.1f}x | "
            f"mom10d={m['momentum_10d']:+.1f}% | "
            f"setup={m['setup_type']}"
        )

    return top


def _download_in_chunks(tickers: list[str], chunk_size: int) -> Optional[pd.DataFrame]:
    """Download in chunks and concatenate to avoid HTTP timeouts."""
    chunks = [tickers[i:i + chunk_size] for i in range(0, len(tickers), chunk_size)]
    frames = []

    for i, chunk in enumerate(chunks):
        try:
            df = yf.download(
                tickers=chunk,
                period="3mo",
                interval="1d",
                group_by="ticker",
                auto_adjust=True,
                progress=False,
                threads=True,
            )
            if not df.empty:
                frames.append(df)
        except Exception as e:
            print(f"[yf_scanner] Chunk {i+1}/{len(chunks)} failed: {e}")

    if not frames:
        return None

    return pd.concat(frames, axis=1) if len(frames) > 1 else frames[0]


def _extract_ticker(data: pd.DataFrame, ticker: str, all_tickers: list[str]) -> Optional[pd.DataFrame]:
    """Extract single ticker from multi-index DataFrame."""
    if len(all_tickers) == 1:
        return data.dropna()

    if ticker not in data.columns.get_level_values(0):
        return None

    df = data[ticker].dropna(subset=["Close"])
    return df if not df.empty else None


def _score_ticker(ticker: str, df: pd.DataFrame) -> tuple[int, dict]:
    """
    Compute setup-quality score. Returns (score, metrics_dict).
    Rewards stocks with CURRENT actionable setups, not just past movers.
    """
    close  = df["Close"]
    volume = df["Volume"]
    high   = df["High"]

    current_price = float(close.iloc[-1])

    # ── Relative volume (0-25 pts) ────────────────────────────────────────────
    avg_vol_20 = float(volume.iloc[-21:-1].mean())
    today_vol  = float(volume.iloc[-1])
    rel_volume = today_vol / avg_vol_20 if avg_vol_20 > 0 else 1.0

    if rel_volume >= 3.0:
        vol_score = 25
    elif rel_volume >= 2.0:
        vol_score = 18
    elif rel_volume >= 1.5:
        vol_score = 12
    elif rel_volume >= 1.0:
        vol_score = 5
    else:
        vol_score = 0

    # ── Setup quality (0-20 pts) ──────────────────────────────────────────────
    # Rewards: pullback to MA20, MA cross, or tight consolidation near MA
    # These are the setups Claude can actually trade — not extended movers
    ma20 = float(close.rolling(20).mean().iloc[-1])
    ma50 = float(close.rolling(50).mean().iloc[-1])
    pct_vs_ma20 = (current_price - ma20) / ma20 * 100
    pct_vs_ma50 = (current_price - ma50) / ma50 * 100

    setup_score = 0
    setup_type  = "none"

    # Fresh MA20 cross (closed above MA20 in last 3 days after being below)
    if len(close) >= 5:
        was_below_ma20 = any(
            float(close.iloc[-(i+2)]) < float(close.rolling(20).mean().iloc[-(i+2)])
            for i in range(1, 4)
        )
        now_above_ma20 = current_price > ma20
        if was_below_ma20 and now_above_ma20:
            setup_score = 20
            setup_type  = "ma20_cross"

    # Tight pullback to MA20 (within 1.5% above)
    if setup_score == 0 and 0 <= pct_vs_ma20 <= 1.5:
        setup_score = 18
        setup_type  = "pullback_to_ma20"

    # Tight pullback to MA50 (within 2% above)
    if setup_score == 0 and 0 <= pct_vs_ma50 <= 2.0:
        setup_score = 15
        setup_type  = "pullback_to_ma50"

    # Consolidating just above MA20 (1.5-4%) — potential continuation
    if setup_score == 0 and 1.5 < pct_vs_ma20 <= 4.0:
        setup_score = 10
        setup_type  = "consolidation_above_ma20"

    # Extended (>8% above MA20) — likely needs pullback, low setup quality
    if setup_score == 0 and pct_vs_ma20 > 8.0:
        setup_score = 2
        setup_type  = "extended"
    elif setup_score == 0:
        setup_score = 5
        setup_type  = "neutral"

    # ── 10-day momentum (0-20 pts) ────────────────────────────────────────────
    if len(close) >= 11:
        momentum_10d = (current_price - float(close.iloc[-11])) / float(close.iloc[-11]) * 100
    else:
        momentum_10d = 0.0

    mom_abs = abs(momentum_10d)
    if mom_abs >= 8.0:
        mom_score = 20
    elif mom_abs >= 5.0:
        mom_score = 15
    elif mom_abs >= 3.0:
        mom_score = 10
    elif mom_abs >= 1.0:
        mom_score = 5
    else:
        mom_score = 0

    # ── MA trend alignment (0-20 pts) ─────────────────────────────────────────
    # Both MAs trending same direction = confirmed trend, not just noise
    ma20_slope = float(close.rolling(20).mean().iloc[-1]) - float(close.rolling(20).mean().iloc[-5])
    ma50_slope = float(close.rolling(50).mean().iloc[-1]) - float(close.rolling(50).mean().iloc[-5])

    both_up   = ma20_slope > 0 and ma50_slope > 0
    both_down = ma20_slope < 0 and ma50_slope < 0

    if both_up or both_down:
        trend_score = 20   # aligned — trend is real
    elif ma20_slope * ma50_slope > 0:
        trend_score = 12   # same direction, weak
    else:
        trend_score = 3    # diverging — choppy

    # ── 52-week high proximity (0-15 pts) ─────────────────────────────────────
    week52_high  = float(high.tail(252).max())
    dist_52w_pct = (week52_high - current_price) / week52_high * 100

    if dist_52w_pct <= 1.0:
        w52_score = 15
    elif dist_52w_pct <= 3.0:
        w52_score = 12
    elif dist_52w_pct <= 5.0:
        w52_score = 8
    elif dist_52w_pct <= 10.0:
        w52_score = 4
    else:
        w52_score = 0

    total = vol_score + setup_score + mom_score + trend_score + w52_score

    df_out = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    df_out.columns = ["open", "high", "low", "close", "volume"]
    df_out["ma20"] = df_out["close"].rolling(20).mean()
    df_out["ma50"] = df_out["close"].rolling(50).mean()

    metrics = {
        "current_price":  current_price,
        "ma20":           ma20,
        "ma50":           ma50,
        "rel_volume":     round(rel_volume, 2),
        "momentum_10d":   round(momentum_10d, 2),
        "pct_vs_ma20":    round(pct_vs_ma20, 2),
        "pct_vs_ma50":    round(pct_vs_ma50, 2),
        "week52_high":    round(week52_high, 2),
        "dist_52w_pct":   round(dist_52w_pct, 2),
        "setup_type":     setup_type,
        "score_breakdown": {
            "relative_volume": vol_score,
            "setup_quality":   setup_score,
            "momentum_10d":    mom_score,
            "ma_trend":        trend_score,
            "week52_proximity": w52_score,
        },
        "daily_df": df_out,
    }

    return total, metrics
