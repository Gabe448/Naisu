# Trading Strategy — Confluence-Based Swing Trading

> This is a starting point, not a rulebook. The bot must learn from every trade outcome, document learnings in Obsidian, re-read learnings before every analysis, and evolve the strategy over time. Deviations that work should be noted and incorporated.

---

## CORE RULES

- Need **3+ confirming signals** before any trade. Never trade a single indicator.
- Minimum **1:2 R/R** on every trade. No exceptions.

---

## PATTERNS

### Reversal
- Head & shoulders
- Double top / double bottom
- Falling wedge

### Continuation
- Cup & handle
- Ascending / descending triangle

### Bilateral (use caution)
- Symmetrical triangle
- Megaphone — avoid

---

## ENTRY RULES

| Signal | Condition |
|--------|-----------|
| Level rejections | 3+ rejections at same level = reversal likely |
| Wick + volume | Long wicks at extremes with elevated volume = reversal signal |
| EMA reclaim/loss | EMA reclaim or loss with volume = trend confirmation |
| Volume threshold | 1.5x+ average volume required for all signals |
| Failed breakout | Trade the opposite direction |
| 200 EMA filter | Macro trend filter — don't fight it |

---

## EXIT RULES

| Target | Size | Condition |
|--------|------|-----------|
| T1 | 25–40% of position | First resistance — lock profit |
| T2 | 40–50% | Major resistance |
| T3 | 20–25% (runner) | Trail stop after T2 hit |

- Exit before earnings / FOMC / CPI if uncertain about direction.

---

## STOP LOSS RULES

- Place stops below pattern invalidation only.
- Never move a stop against the position.
- Trail stops only in the profit direction.
- Typical range: **3–5%**, adjusted for volatility.

---

## GEX (Gamma Exposure)

- Use for **directional confluence only**.
- King nodes used for strike selection.
- GEX is **not** used as a price target.

---

## TIMEFRAMES

| Purpose | Timeframe |
|---------|-----------|
| Context / trend | Daily, 4h |
| Entry trigger | 1h, 15min |

---

## LEARNING PROTOCOL

1. After every trade, document the outcome and key learnings in Obsidian.
2. Before every analysis session, re-read prior learnings.
3. Deviations from these rules that produce positive outcomes → note them → incorporate into this strategy.
4. This document should evolve. Update it when the evidence warrants.
