"""
Conviction scorer — final 0-100 score combining all analysis layers.

  >= 70  (CONVICTION_THRESHOLD) → post signal to Discord
  50-69                         → watchlist candidate
  < 50                          → skip

Weights:
  Confluence score (signals + pattern)  50%
  YF momentum score                     25%
  Tradeable flag (RR >= 2 + threshold)  25%
  Penalty: fewer than 3 signals         -10 pts each missing signal
"""
from analysis.confluence import ConfluenceResult


def conviction_score(
    confluence: ConfluenceResult,
    yf_score:   int   = 0,
) -> int:
    """Combine confluence + yf momentum into a single 0-100 score."""
    # Component weights
    conf_component = confluence.confluence_score * 0.50      # 0–50
    yf_component   = (yf_score / 100) * 25                  # 0–25
    tradeable_bonus = 25 if confluence.tradeable else 0      # 0–25

    # Penalty for too few signals (strategy requires 3+)
    missing = max(0, 3 - confluence.signals_count)
    penalty = missing * 10

    raw = conf_component + yf_component + tradeable_bonus - penalty
    return max(0, min(100, round(raw)))


def grade(score: int) -> str:
    """Letter grade for quick Discord display."""
    if score >= 80:
        return "A — High conviction"
    if score >= 70:
        return "B — Tradeable"
    if score >= 50:
        return "C — Watchlist"
    return "D — Skip"
