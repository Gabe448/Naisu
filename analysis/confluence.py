"""
Confluence aggregator — the strategy's core filter.

Rule: need 3+ confirming signals to generate a tradeable idea.
Never trade on a single indicator.

Combines:
  - Chart pattern          (1 signal, weighted by confidence)
  - Rule-based signals     (up to 6 signals)
  - DEX bias               (1 signal, from LuminaFlow)

Direction is determined by the net weight of bullish vs bearish evidence.
"""
from dataclasses import dataclass, field

from analysis.patterns import PatternBias, PatternResult
from analysis.signals import SignalResult


# Weights per signal type — higher = more conviction when it fires
CONFLUENCE_WEIGHTS: dict[str, float] = {
    "pattern":             1.0,
    "volume_capitulation": 0.90,
    "multiple_rejections": 0.80,
    "ema_reclaim":         0.80,
    "failed_breakout":     0.80,
    "rejection_wicks":     0.70,
    "dex_bias":            0.70,
    "trend_alignment":     0.60,
}


@dataclass
class ConfluenceResult:
    ticker:           str
    direction:        str              # "long" | "short" | "neutral"
    signals_fired:    list[str]        = field(default_factory=list)
    signals_count:    int              = 0
    confluence_score: float            = 0.0    # 0–100
    pattern:          PatternResult | None = None
    signal_details:   list[SignalResult]   = field(default_factory=list)
    tradeable:        bool             = False   # True if 3+ signals + clear direction
    notes:            list[str]        = field(default_factory=list)


def score_confluence(
    ticker:   str,
    df,
    pattern:  PatternResult | None,
    signals:  list[SignalResult],
    dex_bias: dict | None = None,
) -> ConfluenceResult:
    """
    Combine pattern + signals + DEX into a single ConfluenceResult.
    Direction determined by balance of bullish vs bearish weighted evidence.
    """
    fired_names: list[str] = []
    bullish_wt  = 0.0
    bearish_wt  = 0.0
    total_wt    = 0.0
    notes: list[str] = []

    # ── Pattern ───────────────────────────────────────────────────────────────
    if pattern is not None:
        w = CONFLUENCE_WEIGHTS["pattern"] * pattern.confidence
        fired_names.append(f"pattern:{pattern.pattern.value}")
        total_wt += w

        if pattern.bias == PatternBias.BULLISH:
            bullish_wt += w
            notes.append(f"📐 {pattern.description} → bullish")
        elif pattern.bias == PatternBias.BEARISH:
            bearish_wt += w
            notes.append(f"📐 {pattern.description} → bearish")
        else:
            # Bilateral — split weight
            bullish_wt += w / 2
            bearish_wt += w / 2
            notes.append(f"📐 {pattern.description} → bilateral")

    # ── Rule-based signals ────────────────────────────────────────────────────
    for sig in signals:
        if not sig.fired:
            continue

        w = CONFLUENCE_WEIGHTS.get(sig.name, 0.5) * sig.strength
        fired_names.append(sig.name)
        total_wt += w

        if sig.name in ("ema_reclaim", "trend_alignment"):
            bull = any(
                kw in n.lower()
                for n in sig.notes
                for kw in ("bullish", "reclaimed", "above ema")
            )
            bear = any(
                kw in n.lower()
                for n in sig.notes
                for kw in ("bearish", "lost", "below ema")
            )
            if bull:
                bullish_wt += w
            elif bear:
                bearish_wt += w

        elif sig.name == "rejection_wicks":
            if any("upper" in n.lower() for n in sig.notes):
                bearish_wt += w
            if any("lower" in n.lower() for n in sig.notes):
                bullish_wt += w

        elif sig.name in ("multiple_rejections", "failed_breakout"):
            if any(
                kw in n.lower()
                for n in sig.notes
                for kw in ("bearish", "breakdown", "broke above")
            ):
                bearish_wt += w
            elif any(
                kw in n.lower()
                for n in sig.notes
                for kw in ("bullish", "above", "broke below")
            ):
                bullish_wt += w

        elif sig.name == "volume_capitulation":
            # Exhaustion at lows → bullish reversal
            bullish_wt += w

    # ── DEX bias ──────────────────────────────────────────────────────────────
    if dex_bias and dex_bias.get("bias") != "neutral":
        w = CONFLUENCE_WEIGHTS["dex_bias"] * float(dex_bias.get("strength", 0.5))
        fired_names.append("dex_bias")
        total_wt += w

        if dex_bias["bias"] == "bullish":
            bullish_wt += w
            notes.append(f"📊 DEX bullish (strength {dex_bias['strength']:.2f})")
        else:
            bearish_wt += w
            notes.append(f"📊 DEX bearish (strength {dex_bias['strength']:.2f})")

    # ── Direction decision ────────────────────────────────────────────────────
    if bullish_wt > bearish_wt * 1.2:
        direction = "long"
    elif bearish_wt > bullish_wt * 1.2:
        direction = "short"
    else:
        direction = "neutral"

    # ── Confluence score (0–100) ──────────────────────────────────────────────
    # Max realistic total_wt ≈ 5.0 (pattern 1.0 + 4 strong signals at ~1.0 each)
    confluence_score = round(min(total_wt / 5.0 * 100, 100), 1)
    tradeable        = len(fired_names) >= 3 and direction != "neutral"

    return ConfluenceResult(
        ticker=ticker,
        direction=direction,
        signals_fired=fired_names,
        signals_count=len(fired_names),
        confluence_score=confluence_score,
        pattern=pattern,
        signal_details=signals,
        tradeable=tradeable,
        notes=notes,
    )
