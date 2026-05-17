"""
Trade structure builder.

Implements the strategy's trim structure and stop management:
  Target 1 (35% trim)  — first resistance / tested level
  Target 2 (45% trim)  — major resistance / measured move
  Target 3 (20% runner) — extended target, trail stop after T2

Stop: below pattern invalidation. Never move against position.
R/R minimum 1:2 — if below, WAIT or refine.
"""
from dataclasses import dataclass, field

from analysis.levels import Level, nearest_resistance, nearest_support

MIN_RR: float = 2.0   # from .env CONVICTION_THRESHOLD companion rule


@dataclass
class TradeIdea:
    ticker:        str
    direction:     str        # "long" | "short"
    entry:         float
    stop:          float
    target1:       float      # 35% trim
    target2:       float      # 45% trim
    target3:       float      # 20% runner
    rr_ratio:      float      # risk/reward to T2
    trigger_level: float      # pattern breakout / breakdown level
    conviction:    int        # 0–100
    thesis:        str
    tradeable:     bool       # RR >= 2.0 and conviction >= threshold
    trim_pcts:     dict       = field(default_factory=lambda: {"t1": 0.35, "t2": 0.45, "t3": 0.20})
    notes:         list[str]  = field(default_factory=list)


def build_trade(
    ticker:               str,
    direction:            str,
    entry:                float,
    levels:               list[Level],
    atr:                  float,
    trigger_level:        float,
    conviction:           int,
    thesis:               str,
    conviction_threshold: int = 70,
) -> TradeIdea:
    """
    Build a full trade structure from entry + S/R levels + ATR.

    ATR is used to size the stop when no clean level exists nearby.
    """
    if direction == "long":
        stop, t1, t2, t3 = _build_long(entry, levels, atr)
    else:
        stop, t1, t2, t3 = _build_short(entry, levels, atr)

    risk   = abs(entry - stop)
    reward = abs(t2 - entry)
    rr     = round(reward / risk, 2) if risk > 0 else 0.0

    tradeable = rr >= MIN_RR and conviction >= conviction_threshold

    notes = [
        f"Risk: ${risk:.2f} ({risk / entry:.1%})",
        f"R/R to T2: {rr:.1f}x {'✅' if rr >= MIN_RR else '❌ below 2:1 minimum'}",
        f"Conviction: {conviction}/100 {'✅' if conviction >= conviction_threshold else '⚠️ below threshold'}",
    ]
    if not tradeable:
        notes.append("⛔ WAIT — setup does not meet full checklist")

    return TradeIdea(
        ticker=ticker,
        direction=direction,
        entry=round(entry, 2),
        stop=round(stop,  2),
        target1=round(t1, 2),
        target2=round(t2, 2),
        target3=round(t3, 2),
        rr_ratio=rr,
        trigger_level=round(trigger_level, 2),
        conviction=conviction,
        thesis=thesis,
        tradeable=tradeable,
        notes=notes,
    )


# ── Level helpers ─────────────────────────────────────────────────────────────

def _build_long(
    entry: float,
    levels: list[Level],
    atr: float,
) -> tuple[float, float, float, float]:
    """Long: stop below support, targets at resistance levels above."""
    support = nearest_support(levels, entry)
    stop    = (support * 0.995) if support else (entry - atr * 1.5)
    stop    = max(stop, entry * 0.95)   # hard cap 5%

    resistances = sorted(l.price for l in levels if l.price > entry * 1.01)
    risk        = entry - stop

    if len(resistances) >= 2:
        t1, t2 = resistances[0], resistances[1]
    elif len(resistances) == 1:
        t1 = resistances[0]
        t2 = entry + risk * 3.0
    else:
        t1 = entry + risk * 2.0
        t2 = entry + risk * 3.0

    t3 = t2 + (t2 - entry) * 0.5   # runner: 50% extension beyond T2

    return stop, t1, t2, t3


def _build_short(
    entry: float,
    levels: list[Level],
    atr: float,
) -> tuple[float, float, float, float]:
    """Short: stop above resistance, targets at support levels below."""
    resistance = nearest_resistance(levels, entry)
    stop       = (resistance * 1.005) if resistance else (entry + atr * 1.5)
    stop       = min(stop, entry * 1.05)   # hard cap 5%

    supports = sorted(
        (l.price for l in levels if l.price < entry * 0.99),
        reverse=True,
    )
    risk = stop - entry

    if len(supports) >= 2:
        t1, t2 = supports[0], supports[1]
    elif len(supports) == 1:
        t1 = supports[0]
        t2 = entry - risk * 3.0
    else:
        t1 = entry - risk * 2.0
        t2 = entry - risk * 3.0

    t3 = t2 - (entry - t2) * 0.5

    return stop, t1, t2, t3


# ── ATR ───────────────────────────────────────────────────────────────────────

def compute_atr(df, period: int = 14) -> float:
    """Average True Range — used for stop sizing when no clean level exists."""
    import pandas as pd

    high  = df["high"]
    low   = df["low"]
    prev  = df["close"].shift(1)

    tr = pd.concat([
        high - low,
        (high - prev).abs(),
        (low  - prev).abs(),
    ], axis=1).max(axis=1)

    return float(tr.rolling(period).mean().iloc[-1])


# ── Discord formatting ────────────────────────────────────────────────────────

def format_trade_card(trade: TradeIdea) -> str:
    """Human-readable trade card for Discord embeds."""
    arrow  = "📈 LONG" if trade.direction == "long" else "📉 SHORT"
    status = "✅ TRADEABLE" if trade.tradeable else "⚠️ WAIT — refine setup"

    return (
        f"**{trade.ticker}** — {arrow}\n"
        f"```\n"
        f"Entry:    ${trade.entry:.2f}   (trigger ${trade.trigger_level:.2f})\n"
        f"Stop:     ${trade.stop:.2f}\n"
        f"Target 1: ${trade.target1:.2f}  (trim 35%)\n"
        f"Target 2: ${trade.target2:.2f}  (trim 45%)\n"
        f"Target 3: ${trade.target3:.2f}  (runner 20%)\n"
        f"R/R:      {trade.rr_ratio:.1f}:1\n"
        f"Score:    {trade.conviction}/100\n"
        f"```\n"
        f"**Thesis:** {trade.thesis}\n"
        f"**Status:** {status}"
    )
