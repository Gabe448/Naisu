"""
yfinance batch momentum scanner — Tier 1 of the data pipeline.

Downloads 3 months of daily bars for ALL S&P 500 tickers in one call.
Costs nothing. Scores each ticker 0-100. Returns top N candidates.

Scoring breakdown (100 pts):
  25 pts — Relative volume   (unusual volume = something's happening)
  25 pts — 10-day momentum   (recent directional move)
  20 pts — 20-day MA position (medium-term trend alignment)
  15 pts — 50-day MA position (longer-term trend context)
  15 pts — 52-week high proximity (breakout potential)

yfinance data is 15-min delayed — used ONLY for screening.
Polygon handles real-time pricing for the top candidates.
"""
import warnings
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore", category=FutureWarning)


def batch_scan(tickers: list[str], top_n: int = 10) -> list[dict]:
    """
    Download daily bars for all tickers, score each, return top N.
    Typical runtime: 5-15 seconds for 500 tickers.
    """
    print(f"[yf_scanner] Downloading {len(tickers)} tickers...")

    # Chunk downloads to avoid timeouts — 200 per batch
    all_data = _download_in_chunks(tickers, chunk_size=200)
    if all_data is None or all_data.empty:
        print("[yf_scanner] Download failed — no data returned.")
        return []

    scored = []
    for ticker in tickers:
        try:
            df = _extract_ticker(all_data, ticker, tickers)
            if df is None or len(df) < 55:   # need 55 bars for 50MA + buffer
                continue
            score, metrics = _score_ticker(ticker, df)
            if score > 0:
                scored.append({"ticker": ticker, "yf_score": score, "yf_metrics": metrics})
        except Exception:
            continue

    scored.sort(key=lambda x: x["yf_score"], reverse=True)
    top = scored[:top_n]

    print(f"[yf_scanner] Scored {len(scored)} tickers. Top {top_n}:")
    for c in top:
        m = c["yf_metrics"]
        print(
            f"  {c['ticker']:6} score={c['yf_score']:3} | "
            f"relVol={m['rel_volume']:.1f}x | "
            f"mom10d={m['momentum_10d']:+.1f}% | "
            f"vs20MA={m['pct_vs_ma20']:+.1f}%"
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
        # Single ticker download — no multi-index
        return data.dropna()

    if ticker not in data.columns.get_level_values(0):
        return None

    df = data[ticker].dropna(subset=["Close"])
    return df if not df.empty else None


def _score_ticker(ticker: str, df: pd.DataFrame) -> tuple[int, dict]:
    """
    Compute momentum score. Returns (score, metrics_dict).
    Higher score = more interesting for swing setup screening.
    """
    close  = df["Close"]
    volume = df["Volume"]
    high   = df["High"]

    current_price = float(close.iloc[-1])

    # ── Relative volume (0-25 pts) ────────────────────────────────────────────
    avg_vol_20   = float(volume.iloc[-21:-1].mean())   # prior 20 days (exclude today)
    today_vol    = float(volume.iloc[-1])
    rel_volume   = today_vol / avg_vol_20 if avg_vol_20 > 0 else 1.0

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

    # ── 10-day momentum (0-25 pts) ────────────────────────────────────────────
    if len(close) >= 11:
        momentum_10d = (current_price - float(close.iloc[-11])) / float(close.iloc[-11]) * 100
    else:
        momentum_10d = 0.0

    mom_abs = abs(momentum_10d)   # score magnitude — direction handled by signal builder
    if mom_abs >= 8.0:
        mom_score = 25
    elif mom_abs >= 5.0:
        mom_score = 18
    elif mom_abs >= 3.0:
        mom_score = 12
    elif mom_abs >= 1.0:
        mom_score = 6
    else:
        mom_score = 0

    # ── 20-day MA position (0-20 pts) ─────────────────────────────────────────
    ma20 = float(close.rolling(20).mean().iloc[-1])
    pct_vs_ma20 = (current_price - ma20) / ma20 * 100

    if abs(pct_vs_ma20) >= 2.0:
        ma20_score = 20   # strong trend either way
    elif abs(pct_vs_ma20) >= 0.5:
        ma20_score = 12
    else:
        ma20_score = 4

    # ── 50-day MA position (0-15 pts) ─────────────────────────────────────────
    ma50 = float(close.rolling(50).mean().iloc[-1])
    pct_vs_ma50 = (current_price - ma50) / ma50 * 100

    if abs(pct_vs_ma50) >= 3.0:
        ma50_score = 15
    elif abs(pct_vs_ma50) >= 1.0:
        ma50_score = 10
    else:
        ma50_score = 3

    # ── 52-week high proximity (0-15 pts) ────────────────────────────────────
    week52_high  = float(high.tail(252).max())
    dist_52w_pct = (week52_high - current_price) / week52_high * 100

    if dist_52w_pct <= 1.0:
        w52_score = 15   # at or breaking 52W high
    elif dist_52w_pct <= 3.0:
        w52_score = 12
    elif dist_52w_pct <= 5.0:
        w52_score = 8
    elif dist_52w_pct <= 10.0:
        w52_score = 4
    else:
        w52_score = 0

    total = vol_score + mom_score + ma20_score + ma50_score + w52_score

    # Build a cleaned DataFrame suitable for downstream use (chart, scoring)
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
        "score_breakdown": {
            "relative_volume": vol_score,
            "momentum_10d":    mom_score,
            "ma20_position":   ma20_score,
            "ma50_position":   ma50_score,
            "week52_proximity": w52_score,
        },
        "daily_df": df_out,   # passed to chart renderer + conviction scorer
    }

    return total, metrics
