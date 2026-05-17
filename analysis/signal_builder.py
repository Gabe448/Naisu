"""
Signal builder — confluence-gated swing trade engine (strategy.md).

Two entry points:
  build_swing_signal(ticker)        — on-demand (fetches all data fresh)
  build_signal_from_candidate(c)    — pipeline path (data already fetched)

Flow:
  1. Load recent trade learnings from Obsidian
  2. Multi-timeframe data (1D context, 4H structure, 1H entry)
  3. Detect confluence signals — GATE: need MIN_CONFLUENCE (default 3) to proceed
  4. Detect chart pattern (type, invalidation level, bilateral flags)
  5. Reject megaphone patterns immediately
  6. Compute entry / stop (pattern invalidation) / T1 / T2 / T3
  7. Score → filter by conviction threshold

Strategy evolves: bot writes learnings after every close, reads them here.
Weights and thresholds live in scoring.py — adjust there, not here.
"""
import os

import numpy as np
import pandas as pd

import data.yf_client as _yfc
from analysis.gex_analysis import analyze_exposure as _analyze_exposure, extract_dex_bias as _extract_dex_bias, get_nearest_gex_level
from analysis.scoring import score_signal, CONVICTION_THRESHOLD
from scanner.watchlist import get_watchlist

MIN_CONFLUENCE = int(os.environ.get("MIN_CONFLUENCE", 3))
VOLUME_MULT    = float(os.environ.get("VOLUME_MULT", 1.5))


def _get_price(ticker):
    return _yfc.get_price(ticker)

def _get_tf(ticker):
    return _yfc.get_multi_tf_context(ticker)

_lf_rate_limited_until = 0.0   # epoch seconds — skip LuminaFlow until this time

def _get_gex(ticker, price):
    import time
    global _lf_rate_limited_until
    if time.time() < _lf_rate_limited_until:
        return _yfc.get_stub_gex(ticker, price)
    try:
        return _analyze_exposure(ticker)
    except Exception as e:
        err = str(e).lower()
        if "429" in err or "rate" in err or "limit" in err:
            _lf_rate_limited_until = time.time() + 900  # back off 15 min
            print(f"[gex] LuminaFlow rate-limited — using stub GEX for 15 min", flush=True)
        return _yfc.get_stub_gex(ticker, price)

def _get_flow(ticker, gex):
    """Derive DEX bias from the LuminaFlow GEX result — no second API call."""
    return _extract_dex_bias(gex)


# ── Public entry points ───────────────────────────────────────────────────────

def build_swing_signal(ticker: str) -> dict | None:
    """Full swing signal pipeline for one ticker. Returns None if not high conviction."""
    learnings = _load_learnings()

    tf     = _get_tf(ticker)
    daily  = tf["daily"]
    four_h = tf["4h"]
    one_h  = tf["1h"]
    pivots = tf["weekly_pivots"]

    price = float(daily["close"].iloc[-1]) if not daily.empty else _get_price(ticker)
    if not price:
        return None

    gex       = _get_gex(ticker, price)
    flow_bias = _get_flow(ticker, gex)

    signals = _detect_signals(price, daily, four_h, one_h, gex, flow_bias)
    if len(signals) < MIN_CONFLUENCE:
        return None

    pattern = _detect_pattern(price, daily, four_h)
    if pattern and pattern.get("avoid"):
        return None

    entry, stop, t1, t2, t3 = _compute_trade_levels(
        price, daily, four_h, one_h, gex, pivots, flow_bias, signals, pattern
    )
    if entry is None:
        return None

    nearest_gex_level = get_nearest_gex_level(price, gex)

    scored = score_signal(
        ticker=ticker, price=price, daily_df=daily, four_h_df=four_h,
        gex=gex, flow_bias=flow_bias, entry=entry, stop=stop,
        t1=t1, t2=t2, t3=t3, signals=signals, pattern=pattern,
    )

    if scored["total_score"] < CONVICTION_THRESHOLD:
        return None

    thesis = _generate_thesis(scored, gex, flow_bias, pivots, nearest_gex_level, signals, pattern, learnings)

    return _build_result(ticker, price, entry, stop, t1, t2, t3, scored,
                         gex, pivots, flow_bias, nearest_gex_level, signals, pattern, thesis, daily)


def build_signal_from_candidate(candidate: dict) -> dict | None:
    """Pipeline path — build signal from pre-enriched candidate. Skips data fetching."""
    learnings = _load_learnings()

    ticker    = candidate["ticker"]
    price     = candidate["real_time_price"]
    yf        = candidate["yf_metrics"]
    daily     = yf["daily_df"]
    _fh = candidate.get("polygon_4h")
    _oh = candidate.get("polygon_1h")
    four_h = _fh if _fh is not None else pd.DataFrame()
    one_h  = _oh if _oh is not None else pd.DataFrame()
    pivots    = candidate.get("weekly_pivots") or {}
    gex       = candidate["gex"]
    flow_bias = candidate["flow_bias"]

    signals = _detect_signals(price, daily, four_h, one_h, gex, flow_bias)
    if len(signals) < MIN_CONFLUENCE:
        return None

    pattern = _detect_pattern(price, daily, four_h)
    if pattern and pattern.get("avoid"):
        return None

    nearest_gex_level = get_nearest_gex_level(price, gex)
    entry, stop, t1, t2, t3 = _compute_trade_levels(
        price, daily, four_h, one_h, gex, pivots, flow_bias, signals, pattern
    )
    if entry is None:
        return None

    scored = score_signal(
        ticker=ticker, price=price, daily_df=daily, four_h_df=four_h,
        gex=gex, flow_bias=flow_bias, entry=entry, stop=stop,
        t1=t1, t2=t2, t3=t3, signals=signals, pattern=pattern,
    )
    if not scored["is_high_conviction"]:
        return None

    thesis = _generate_thesis(scored, gex, flow_bias, pivots, nearest_gex_level, signals, pattern, learnings)

    result = _build_result(ticker, price, entry, stop, t1, t2, t3, scored,
                           gex, pivots, flow_bias, nearest_gex_level, signals, pattern, thesis, daily)
    result["yf_score"] = candidate["yf_score"]
    result["news"]     = candidate.get("news", [])
    return result


def scan_watchlist() -> list[dict]:
    """Watchlist-only scan (used by on-demand !scan command)."""
    signals = []
    for ticker in get_watchlist():
        try:
            sig = build_swing_signal(ticker)
            if sig:
                signals.append(sig)
        except Exception as e:
            print(f"[scanner] {ticker} error: {e}")
    signals.sort(key=lambda s: s["conviction_score"], reverse=True)
    return signals


# ── Signal detection ──────────────────────────────────────────────────────────

def _detect_signals(
    price:     float,
    daily:     pd.DataFrame,
    four_h:    pd.DataFrame,
    one_h:     pd.DataFrame,
    gex:       dict,
    flow_bias: dict,
) -> list[dict]:
    """
    Detect individual confluence signals.
    Each returns {name, direction, strength, notes} or None.
    Confluence gate: caller requires MIN_CONFLUENCE (default 3) to trade.
    """
    entry_tf = one_h if not one_h.empty else four_h

    detectors = [
        _signal_ema_reclaim(price, entry_tf),
        _signal_volume_spike(daily, four_h, one_h),
        _signal_multi_rejection(price, daily, four_h),
        _signal_long_wick(daily, four_h),
        _signal_failed_breakout(price, daily, four_h),
        _signal_gex_confluence(price, gex, flow_bias),
        _signal_dex_alignment(flow_bias),
        _signal_tf_alignment(price, daily, four_h),
    ]
    return [s for s in detectors if s is not None]


def _signal_ema_reclaim(price: float, df: pd.DataFrame) -> dict | None:
    """EMA20 reclaim or loss with volume confirmation."""
    if df.empty or len(df) < 22 or "volume" not in df.columns:
        return None

    ema20      = df["close"].ewm(span=20, adjust=False).mean()
    avg_vol    = df["volume"].iloc[-21:-1].mean()
    curr_vol   = df["volume"].iloc[-1]
    prev_close = df["close"].iloc[-2]
    curr_close = df["close"].iloc[-1]
    prev_ema   = ema20.iloc[-2]
    curr_ema   = ema20.iloc[-1]

    if avg_vol == 0 or curr_vol < avg_vol * 1.2:
        return None

    ratio = curr_vol / avg_vol
    if prev_close < prev_ema and curr_close > curr_ema:
        return {"name": "ema_reclaim", "direction": "long",  "strength": ratio,
                "notes": f"EMA20 reclaim ({ratio:.1f}x vol)"}
    if prev_close > prev_ema and curr_close < curr_ema:
        return {"name": "ema_reclaim", "direction": "short", "strength": ratio,
                "notes": f"EMA20 loss ({ratio:.1f}x vol)"}
    return None


def _signal_volume_spike(
    daily: pd.DataFrame, four_h: pd.DataFrame, one_h: pd.DataFrame
) -> dict | None:
    """Volume ≥ 1.5x 20-bar average on any timeframe."""
    for df in [one_h, four_h, daily]:
        if df.empty or "volume" not in df.columns or len(df) < 22:
            continue
        avg  = df["volume"].iloc[-21:-1].mean()
        curr = df["volume"].iloc[-1]
        if avg > 0 and curr >= avg * VOLUME_MULT:
            last = df.iloc[-1]
            direction = "long" if last["close"] >= last["open"] else "short"
            return {"name": "volume_spike", "direction": direction,
                    "strength": curr / avg, "notes": f"vol {curr/avg:.1f}x avg"}
    return None


def _signal_multi_rejection(price: float, daily: pd.DataFrame, four_h: pd.DataFrame) -> dict | None:
    """3+ touches at an S/R zone → reversal likely."""
    for df in [four_h, daily]:
        if df.empty or len(df) < 15:
            continue
        lookback  = df.iloc[-30:] if len(df) >= 30 else df
        tolerance = price * 0.01
        highs = lookback["high"].values
        lows  = lookback["low"].values

        for h in highs:
            if abs(h - price) / price <= 0.02:
                touches = sum(1 for hh in highs if abs(hh - h) <= tolerance)
                if touches >= 3:
                    return {"name": "multi_rejection", "direction": "short",
                            "strength": min(touches / 3, 2.0),
                            "notes": f"{touches} rejections at resistance ${h:.2f}"}

        for l in lows:
            if abs(l - price) / price <= 0.02:
                touches = sum(1 for ll in lows if abs(ll - l) <= tolerance)
                if touches >= 3:
                    return {"name": "multi_rejection", "direction": "long",
                            "strength": min(touches / 3, 2.0),
                            "notes": f"{touches} bounces at support ${l:.2f}"}
    return None


def _signal_long_wick(daily: pd.DataFrame, four_h: pd.DataFrame) -> dict | None:
    """Wick > 50% of total range at a recent extreme = reversal signal."""
    for df in [four_h, daily]:
        if df.empty or len(df) < 10:
            continue
        last       = df.iloc[-1]
        total      = last["high"] - last["low"]
        if total == 0:
            continue
        body_top   = max(last["close"], last["open"])
        body_bot   = min(last["close"], last["open"])
        upper_wick = last["high"] - body_top
        lower_wick = body_bot - last["low"]
        recent_high = df["high"].iloc[-10:-1].max()
        recent_low  = df["low"].iloc[-10:-1].min()

        if upper_wick / total >= 0.5 and last["high"] >= recent_high * 0.99:
            return {"name": "long_wick", "direction": "short",
                    "strength": upper_wick / total,
                    "notes": f"upper wick {upper_wick/total:.0%} at extreme"}
        if lower_wick / total >= 0.5 and last["low"] <= recent_low * 1.01:
            return {"name": "long_wick", "direction": "long",
                    "strength": lower_wick / total,
                    "notes": f"lower wick {lower_wick/total:.0%} at extreme"}
    return None


def _signal_failed_breakout(price: float, daily: pd.DataFrame, four_h: pd.DataFrame) -> dict | None:
    """Price broke a level then reversed → trade opposite."""
    for df in [four_h, daily]:
        if df.empty or len(df) < 13:
            continue
        lookback     = df.iloc[-12:-1]
        recent_high  = lookback["high"].max()
        recent_low   = lookback["low"].min()
        last = df.iloc[-1]
        prev = df.iloc[-2]

        if prev["high"] > recent_high and last["close"] < recent_high:
            return {"name": "failed_breakout", "direction": "short", "strength": 1.5,
                    "notes": f"failed breakout above ${recent_high:.2f}"}
        if prev["low"] < recent_low and last["close"] > recent_low:
            return {"name": "failed_breakout", "direction": "long", "strength": 1.5,
                    "notes": f"failed breakdown below ${recent_low:.2f}"}
    return None


def _signal_gex_confluence(price: float, gex: dict, flow_bias: dict) -> dict | None:
    """GEX regime as directional confluence (not used as target)."""
    regime = gex.get("gex_regime", "positive")
    flip   = gex.get("gex_flip")
    bias   = flow_bias.get("bias", "neutral")

    if regime == "negative" and bias in ("bullish", "bearish"):
        direction = "long" if bias == "bullish" else "short"
        return {"name": "gex_confluence", "direction": direction, "strength": 1.0,
                "notes": f"negative GEX + {bias} flow (trending regime)"}

    if flip and abs(price - flip) / price <= 0.02 and bias in ("bullish", "bearish"):
        direction = "long" if bias == "bullish" else "short"
        return {"name": "gex_confluence", "direction": direction, "strength": 0.8,
                "notes": f"price at GEX flip ${flip:.2f} with {bias} flow"}

    return None


def _signal_dex_alignment(flow_bias: dict) -> dict | None:
    """Strong DEX positioning = structural dealer lean = confluence."""
    bias     = flow_bias.get("bias", "neutral")
    strength = float(flow_bias.get("strength", 0))
    if bias == "neutral" or strength < 0.3:
        return None
    direction = "long" if bias == "bullish" else "short"
    return {"name": "dex_alignment", "direction": direction, "strength": strength,
            "notes": f"DEX {bias} ({strength:.0%})"}


def _signal_tf_alignment(price: float, daily: pd.DataFrame, four_h: pd.DataFrame) -> dict | None:
    """1D + 4H timeframe alignment."""
    if daily.empty or four_h.empty or len(daily) < 6 or len(four_h) < 6:
        return None

    daily_up = daily["close"].iloc[-3:].mean() > daily["close"].iloc[-6:-3].mean()
    four_h_up = four_h["close"].iloc[-3:].mean() > four_h["close"].iloc[-6:-3].mean()

    if daily_up != four_h_up:
        return None

    direction = "long" if daily_up else "short"
    label     = "bullish" if daily_up else "bearish"

    last  = daily.iloc[-1]
    ma20  = last.get("ma20")
    ma50  = last.get("ma50")
    notes = f"1D + 4H {label}"
    if ma20 and ma50 and not pd.isna(ma20) and not pd.isna(ma50):
        if daily_up and price > ma20 and price > ma50:
            notes += ", above MA20 + MA50"
        elif not daily_up and price < ma20 and price < ma50:
            notes += ", below MA20 + MA50"

    return {"name": "tf_alignment", "direction": direction, "strength": 1.0, "notes": notes}


# ── Pattern detection ─────────────────────────────────────────────────────────

def _detect_pattern(price: float, daily: pd.DataFrame, four_h: pd.DataFrame) -> dict | None:
    """Detect primary chart pattern. Returns pattern dict or None."""
    if daily.empty or len(daily) < 20:
        return None
    df = daily.iloc[-40:] if len(daily) >= 40 else daily

    return (
        _pattern_double_bottom(price, df)
        or _pattern_double_top(price, df)
        or _pattern_falling_wedge(price, df)
        or _pattern_ascending_triangle(price, df)
        or _pattern_descending_triangle(price, df)
        or _pattern_head_shoulders(price, df)
        or _pattern_symmetrical_triangle(price, df)
        or _pattern_megaphone(price, df)
    )


def _local_highs(df: pd.DataFrame, window: int = 3) -> list[float]:
    h = df["high"].values
    return [float(h[i]) for i in range(window, len(h) - window)
            if h[i] >= max(h[max(0, i-window):i+window+1]) * 0.999]


def _local_lows(df: pd.DataFrame, window: int = 3) -> list[float]:
    l = df["low"].values
    return [float(l[i]) for i in range(window, len(l) - window)
            if l[i] <= min(l[max(0, i-window):i+window+1]) * 1.001]


def _pattern_double_bottom(price: float, df: pd.DataFrame) -> dict | None:
    lows = _local_lows(df)
    if len(lows) < 2:
        return None
    l1, l2 = lows[-2], lows[-1]
    avg = (l1 + l2) / 2
    if abs(l1 - l2) / avg > 0.02:
        return None
    if not (avg * 1.01 < price < avg * 1.12):
        return None
    return {"name": "double_bottom", "type": "reversal", "direction": "long",
            "invalidation": round(avg * 0.995, 2),
            "notes": f"double bottom ~${avg:.2f}", "avoid": False, "strangle": False}


def _pattern_double_top(price: float, df: pd.DataFrame) -> dict | None:
    highs = _local_highs(df)
    if len(highs) < 2:
        return None
    h1, h2 = highs[-2], highs[-1]
    avg = (h1 + h2) / 2
    if abs(h1 - h2) / avg > 0.02:
        return None
    if not (avg * 0.88 < price < avg * 0.99):
        return None
    return {"name": "double_top", "type": "reversal", "direction": "short",
            "invalidation": round(avg * 1.005, 2),
            "notes": f"double top ~${avg:.2f}", "avoid": False, "strangle": False}


def _pattern_falling_wedge(price: float, df: pd.DataFrame) -> dict | None:
    highs = _local_highs(df, window=2)
    lows  = _local_lows(df, window=2)
    if len(highs) < 3 or len(lows) < 3:
        return None
    h1, h2, h3 = highs[-3], highs[-2], highs[-1]
    l1, l2, l3 = lows[-3], lows[-2], lows[-1]
    if not (h1 > h2 > h3 and l1 > l2 > l3):
        return None
    if (h3 - l3) >= (h1 - l1) * 0.9:   # must be converging
        return None
    return {"name": "falling_wedge", "type": "reversal", "direction": "long",
            "invalidation": round(l3 * 0.995, 2),
            "notes": "falling wedge — lower highs + lower lows converging (bullish break expected)",
            "avoid": False, "strangle": False}


def _pattern_ascending_triangle(price: float, df: pd.DataFrame) -> dict | None:
    highs = _local_highs(df, window=2)
    lows  = _local_lows(df, window=2)
    if len(highs) < 2 or len(lows) < 2:
        return None
    h1, h2 = highs[-2], highs[-1]
    l1, l2 = lows[-2], lows[-1]
    flat_top     = abs(h1 - h2) / max(h1, h2) <= 0.015
    rising_lows  = l2 > l1 * 1.005
    if not (flat_top and rising_lows):
        return None
    resistance = (h1 + h2) / 2
    return {"name": "ascending_triangle", "type": "continuation", "direction": "long",
            "invalidation": round(l2 * 0.995, 2),
            "notes": f"ascending triangle — resistance ${resistance:.2f}, rising lows",
            "avoid": False, "strangle": False}


def _pattern_descending_triangle(price: float, df: pd.DataFrame) -> dict | None:
    highs = _local_highs(df, window=2)
    lows  = _local_lows(df, window=2)
    if len(highs) < 2 or len(lows) < 2:
        return None
    h1, h2 = highs[-2], highs[-1]
    l1, l2 = lows[-2], lows[-1]
    falling_highs = h2 < h1 * 0.995
    flat_bottom   = abs(l1 - l2) / max(l1, l2) <= 0.015
    if not (falling_highs and flat_bottom):
        return None
    support = (l1 + l2) / 2
    return {"name": "descending_triangle", "type": "continuation", "direction": "short",
            "invalidation": round(h2 * 1.005, 2),
            "notes": f"descending triangle — support ${support:.2f}, falling highs",
            "avoid": False, "strangle": False}


def _pattern_head_shoulders(price: float, df: pd.DataFrame) -> dict | None:
    highs = _local_highs(df, window=3)
    lows  = _local_lows(df, window=3)
    if len(highs) < 3:
        return None
    ls, head, rs = highs[-3], highs[-2], highs[-1]
    if not (head > ls and head > rs):
        return None
    if abs(ls - rs) / max(ls, rs) > 0.05:
        return None
    neckline = (lows[-2] + lows[-1]) / 2 if len(lows) >= 2 else ls * 0.95
    if price > neckline * 1.02:
        return None
    return {"name": "head_and_shoulders", "type": "reversal", "direction": "short",
            "invalidation": round(head * 1.005, 2),
            "notes": f"H&S — head ${head:.2f}, neckline ~${neckline:.2f}",
            "avoid": False, "strangle": False}


def _pattern_symmetrical_triangle(price: float, df: pd.DataFrame) -> dict | None:
    highs = _local_highs(df, window=2)
    lows  = _local_lows(df, window=2)
    if len(highs) < 2 or len(lows) < 2:
        return None
    h1, h2 = highs[-2], highs[-1]
    l1, l2 = lows[-2], lows[-1]
    if not (h2 < h1 * 0.995 and l2 > l1 * 1.005):
        return None
    return {"name": "symmetrical_triangle", "type": "bilateral", "direction": "neutral",
            "invalidation": None,
            "notes": f"symmetrical triangle — use strangle, apex ~${(h2+l2)/2:.2f}",
            "avoid": False, "strangle": True}


def _pattern_megaphone(price: float, df: pd.DataFrame) -> dict | None:
    highs = _local_highs(df, window=2)
    lows  = _local_lows(df, window=2)
    if len(highs) < 2 or len(lows) < 2:
        return None
    h1, h2 = highs[-2], highs[-1]
    l1, l2 = lows[-2], lows[-1]
    if not (h2 > h1 * 1.005 and l2 < l1 * 0.995):
        return None
    return {"name": "megaphone", "type": "bilateral", "direction": "neutral",
            "invalidation": None,
            "notes": "megaphone — avoid (no edge)",
            "avoid": True, "strangle": False}


# ── Level computation ─────────────────────────────────────────────────────────

def _compute_trade_levels(
    price:     float,
    daily:     pd.DataFrame,
    four_h:    pd.DataFrame,
    one_h:     pd.DataFrame,
    gex:       dict,
    pivots:    dict,
    flow_bias: dict,
    signals:   list[dict] | None = None,
    pattern:   dict | None = None,
) -> tuple[float | None, float | None, float | None, float | None, float | None]:
    """
    Returns (entry, stop, t1, t2, t3).

    Stop  = pattern invalidation level (not GEX)
    T1    = first S/R level (25-40% position — take partial here)
    T2    = major S/R level (40-50% position — primary target)
    T3    = extended runner (20-25% — trail stop from T2)

    GEX walls used for directional confluence only, not as targets.
    """
    if daily.empty or four_h.empty:
        return None, None, None, None, None

    signals = signals or []
    direction_votes = [s["direction"] for s in signals if s["direction"] in ("long", "short")]
    longs  = direction_votes.count("long")
    shorts = direction_votes.count("short")

    if longs > shorts:
        direction = "bullish"
    elif shorts > longs:
        direction = "bearish"
    else:
        bias = flow_bias.get("bias", "neutral")
        if bias == "bullish":
            direction = "bullish"
        elif bias == "bearish":
            direction = "bearish"
        else:
            return None, None, None, None, None

    entry = price

    # Stop: pattern invalidation > swing structure fallback
    if pattern and pattern.get("invalidation"):
        stop = pattern["invalidation"]
    elif direction == "bullish":
        recent_lows = four_h["low"].iloc[-5:] if len(four_h) >= 5 else four_h["low"]
        stop = round(float(recent_lows.min()) * 0.998, 2)
    else:
        recent_highs = four_h["high"].iloc[-5:] if len(four_h) >= 5 else four_h["high"]
        stop = round(float(recent_highs.max()) * 1.002, 2)

    risk = abs(entry - stop)
    if risk == 0:
        return None, None, None, None, None

    # Hard cap: stop can't be more than 8% from entry (avoids hair-trigger stops
    # that create astronomically high R:R just from tight risk math)
    stop_pct = risk / entry
    if stop_pct > 0.08:
        return None, None, None, None, None

    # Realistic target ceiling: 12% max move from entry for large-caps
    # (swing trades, not position trades)
    MAX_TARGET_PCT = 0.12

    swing_highs = _swing_levels(four_h, daily, "high")
    swing_lows  = _swing_levels(four_h, daily, "low")

    if direction == "bullish":
        # Only use S/R levels within a realistic distance
        candidates = sorted({
            h for h in swing_highs
            if price * 1.005 < h <= price * (1 + MAX_TARGET_PCT)
        })
        for level in [pivots.get("R1"), pivots.get("R2")]:
            if level and price * 1.005 < level <= price * (1 + MAX_TARGET_PCT):
                candidates = sorted(set(candidates) | {level})

        t1 = round(candidates[0], 2) if len(candidates) >= 1 else round(entry + risk * 2, 2)
        t2 = round(candidates[1], 2) if len(candidates) >= 2 else round(entry + risk * 3, 2)
        # T3: modest extension — half the T1→T2 range beyond T2, capped at MAX_TARGET_PCT
        t3 = round(min(t2 + (t2 - t1) * 0.5, entry * (1 + MAX_TARGET_PCT)), 2)

    else:
        candidates = sorted({
            l for l in swing_lows
            if price * (1 - MAX_TARGET_PCT) <= l < price * 0.995
        }, reverse=True)
        for level in [pivots.get("S1"), pivots.get("S2")]:
            if level and price * (1 - MAX_TARGET_PCT) <= level < price * 0.995:
                candidates = sorted((set(candidates) | {level}), reverse=True)

        t1 = round(candidates[0], 2) if len(candidates) >= 1 else round(entry - risk * 2, 2)
        t2 = round(candidates[1], 2) if len(candidates) >= 2 else round(entry - risk * 3, 2)
        t3 = round(max(t2 - (t1 - t2) * 0.5, entry * (1 - MAX_TARGET_PCT)), 2)

    # Enforce min 2:1 R:R on T2
    rr = abs(t2 - entry) / risk
    if rr < 2.0:
        return None, None, None, None, None

    # Reject trades where R:R is absurdly high (>5:1) — stop is too tight
    if rr > 5.0:
        return None, None, None, None, None

    return entry, stop, t1, t2, t3


def _swing_levels(four_h: pd.DataFrame, daily: pd.DataFrame, col: str) -> list[float]:
    """Significant swing high or low levels from 4H and daily."""
    levels = []
    for df, window in [(four_h, 3), (daily, 5)]:
        if df.empty or len(df) < window * 2 + 1:
            continue
        vals = df[col].values
        for i in range(window, len(vals) - window):
            seg = vals[max(0, i - window): i + window + 1]
            if col == "high" and vals[i] >= max(seg) * 0.999:
                levels.append(round(float(vals[i]), 2))
            elif col == "low" and vals[i] <= min(seg) * 1.001:
                levels.append(round(float(vals[i]), 2))
    return list(set(levels))


# ── Thesis generation ─────────────────────────────────────────────────────────

def _generate_thesis(
    scored:            dict,
    gex:               dict,
    flow_bias:         dict,
    pivots:            dict,
    nearest_gex_level: dict,
    signals:           list[dict],
    pattern:           dict | None,
    learnings:         list[str],
) -> str:
    direction = scored["direction"].upper()
    score     = scored["total_score"]
    rr        = scored["rr_ratio"]

    lines = [f"**{direction}** | Score {score}/100 | R:R {rr}:1"]

    if pattern and not pattern.get("avoid"):
        action = " → USE STRANGLE" if pattern.get("strangle") else ""
        lines.append(
            f"Pattern: {pattern['type'].title()} — {pattern['name'].replace('_', ' ').title()}{action}"
            f" | {pattern['notes']}"
        )

    sig_names = ", ".join(s["name"] for s in signals)
    lines.append(f"Confluence ({len(signals)}): {sig_names}")

    for s in signals:
        lines.append(f"  • {s['notes']}")

    regime = gex.get("gex_regime", "?").upper()
    flip   = gex.get("gex_flip", "?")
    dex_b  = flow_bias.get("bias", "?")
    dex_s  = flow_bias.get("strength", 0)
    lines.append(f"GEX: {regime} (flip ${flip}) | DEX: {dex_b} ({dex_s:.0%}) — directional confluence only")

    p  = pivots.get("P")
    r1 = pivots.get("R1")
    s1 = pivots.get("S1")
    if p:
        lines.append(f"Pivots: P=${p:.2f} | R1=${r1:.2f} | S1=${s1:.2f}")

    if learnings:
        lines.append(f"Learning: {learnings[0]}")

    return "\n".join(lines)


# ── Result builder ────────────────────────────────────────────────────────────

def _build_result(
    ticker, price, entry, stop, t1, t2, t3, scored,
    gex, pivots, flow_bias, nearest_gex_level, signals, pattern, thesis, daily,
) -> dict:
    return {
        "ticker":            ticker,
        "price":             price,
        "direction":         scored["direction"],
        "entry":             entry,
        "stop":              stop,
        "t1":                t1,
        "t2":                t2,
        "t3":                t3,
        "target":            t2,         # backward compat
        "rr_ratio":          scored["rr_ratio"],
        "conviction_score":  scored["total_score"],
        "signal_count":      len(signals),
        "signals":           signals,
        "pattern":           pattern["name"] if pattern else "none",
        "pattern_type":      pattern["type"] if pattern else "none",
        "pattern_strangle":  pattern.get("strangle", False) if pattern else False,
        "gex_regime":        gex["gex_regime"],
        "gex_flip":          gex["gex_flip"],
        "gex_wall":          gex["gex_wall"],
        "nearest_gex_level": nearest_gex_level,
        "weekly_pivots":     pivots,
        "flow_bias":         flow_bias["bias"],
        "flow_call_pct":     flow_bias["call_pct"],
        "components":        scored["components"],
        "thesis":            thesis,
        "daily_df":          daily,
        "gex_full":          gex,
    }


# ── Learnings loader ──────────────────────────────────────────────────────────

def _load_learnings(n: int = 3) -> list[str]:
    """Load recent trade learnings from Obsidian. Informs current analysis."""
    try:
        from memory.obsidian_writer import get_recent_learnings
        return get_recent_learnings(n)
    except Exception:
        return []
