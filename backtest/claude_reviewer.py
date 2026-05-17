"""
Claude post-trade reviewer.

Two jobs:
  1. After each closed position — show Claude what actually happened.
     Claude writes a structured learning (what it expected vs what occurred).

  2. After every N closed trades (batch) — Claude synthesizes the batch into
     a cohesive learning block and appends it to Obsidian learnings.md.

The learning format matches what the live system already reads, so backtested
learnings feed directly into live Claude analysis going forward.
"""
import json
from datetime import datetime
from typing import Optional

import anthropic
import pandas as pd

from utils.claude_limiter import acquire as _rl_acquire, log_call as _rl_log

_client_instance: Optional[anthropic.Anthropic] = None


def _client() -> anthropic.Anthropic:
    global _client_instance
    if _client_instance is None:
        _client_instance = anthropic.Anthropic()
    return _client_instance


# ── Single-trade review ───────────────────────────────────────────────────────

def review_closed_trade(
    position:   dict,
    daily_bars: pd.DataFrame,
) -> Optional[dict]:
    """
    Show Claude the original analysis + what actually happened after entry.
    Returns a structured learning dict, or None on API error.

    position: closed position dict from position_sim
    daily_bars: FULL daily history for the ticker (not sliced)
    """
    ticker    = position["ticker"]
    direction = position["direction"]
    entry_dt  = datetime.fromisoformat(position["entry_date"])
    exit_dt   = datetime.fromisoformat(position["exit_date"])
    entry_px  = position["entry_price"]
    exit_px   = position["exit_price"]
    pnl       = position.get("final_pnl_pct", 0)
    status    = position["status"]
    thesis    = position.get("thesis", "")
    stop_px   = position["stop_price"]
    target_px = position["target_price"]

    # Build post-entry price action (entry date → exit date + 3 days)
    post_entry = _slice_post_entry(daily_bars, entry_dt, exit_dt)

    outcome_str = (
        f"{'WIN' if pnl > 0 else 'LOSS'} — {status.replace('_', ' ').upper()}\n"
        f"Entry: ${entry_px:.2f} on {entry_dt.date()}\n"
        f"Exit:  ${exit_px:.2f} on {exit_dt.date()}\n"
        f"PnL:   {pnl:+.2f}% over {position.get('days_held', '?')} days\n"
        f"Stop was: ${stop_px:.2f} | Target was: ${target_px:.2f}"
    )

    post_entry_str = "No post-entry bars available."
    if not post_entry.empty:
        rows = []
        for idx, row in post_entry.iterrows():
            rows.append(
                f"  {str(idx)[:10]}: "
                f"O:{row.get('open', 0):.2f} H:{row.get('high', 0):.2f} "
                f"L:{row.get('low', 0):.2f} C:{row.get('close', 0):.2f} "
                f"V:{row.get('volume', 0):,.0f}"
            )
        post_entry_str = "\n".join(rows)

    prompt = f"""You made a trade call during backtesting. Here is what happened.

ORIGINAL ANALYSIS
─────────────────
Ticker: {ticker}
Direction: {direction.upper()}
Thesis: {thesis}
Setup types: {', '.join(position.get('setup_types', []))}
Stop basis: {position.get('stop_basis', 'N/A')}
Target basis: {position.get('target_basis', 'N/A')}
Learnings you cited at the time: {position.get('learnings_ref', 'none')}

ACTUAL OUTCOME
──────────────
{outcome_str}

PRICE ACTION AFTER ENTRY (what actually happened)
──────────────────────────────────────────────────
{post_entry_str}

Now write a structured learning from this trade. Be specific — not generic.
Reference exactly what your thesis said vs what price actually did.
If you were wrong, say WHY the thesis failed. If you were right, say WHAT confirmed it.

Return ONLY valid JSON. No text outside the JSON.
{{
  "verdict": "correct_thesis" | "wrong_thesis" | "right_idea_wrong_timing" | "wrong_direction" | "stopped_by_noise",
  "what_worked": "<specific price action or structure that confirmed the setup, or null>",
  "what_failed": "<specific reason the thesis broke down, or null>",
  "key_lesson": "<one concrete rule to apply next time — cite the ticker and setup type>",
  "stop_quality": "too_tight" | "appropriate" | "too_wide",
  "setup_grade": "A" | "B" | "C" | "D" | "F",
  "apply_to_future": "<one sentence: what should change in your analysis of this setup type>"
}}"""

    try:
        _REVIEW_MODEL = "claude-opus-4-6"
        _rl_acquire(f"review_closed_trade({ticker})")
        response = _client().messages.create(
            model=_REVIEW_MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        _rl_log(f"review_closed_trade({ticker})", _REVIEW_MODEL,
                response.usage.input_tokens, response.usage.output_tokens)
        raw = response.content[0].text.strip()
        raw = _strip_fences(raw)
        result = json.loads(raw)
        result["ticker"]    = ticker
        result["trade_id"]  = position["id"]
        result["pnl"]       = pnl
        result["direction"] = direction
        result["date"]      = exit_dt.strftime("%Y-%m-%d")
        return result
    except RuntimeError as e:
        print(f"[claude_reviewer] {e}", flush=True)
        return None
    except Exception as e:
        if _is_credit_error(e):
            raise  # re-raise so engine can catch and flush
        print(f"[claude_reviewer] review_closed_trade error for {ticker}: {e}")
        return None


# ── Batch learning synthesis ──────────────────────────────────────────────────

def write_batch_learnings(
    trade_reviews: list[dict],
    run_id:        str,
    batch_number:  int,
) -> Optional[str]:
    """
    After N trades, synthesize all individual reviews into a batch summary.
    Writes the summary to Obsidian learnings.md.
    Returns the written text, or None on failure.

    trade_reviews: list of dicts returned by review_closed_trade()
    """
    if not trade_reviews:
        return None

    wins   = [r for r in trade_reviews if (r.get("pnl") or 0) > 0]
    losses = [r for r in trade_reviews if (r.get("pnl") or 0) <= 0]
    win_rate = len(wins) / len(trade_reviews) * 100

    reviews_text = "\n\n".join([
        f"Trade {i+1}: {r['ticker']} {r['direction'].upper()} | "
        f"PnL: {r.get('pnl', 0):+.2f}% | "
        f"Grade: {r.get('setup_grade', '?')} | "
        f"Verdict: {r.get('verdict', '?')}\n"
        f"  Key lesson: {r.get('key_lesson', '')}\n"
        f"  What worked: {r.get('what_worked', 'N/A')}\n"
        f"  What failed: {r.get('what_failed', 'N/A')}\n"
        f"  Apply to future: {r.get('apply_to_future', '')}"
        for i, r in enumerate(trade_reviews)
    ])

    prompt = f"""You are reviewing a batch of {len(trade_reviews)} backtested trades.
Win rate: {win_rate:.1f}% ({len(wins)}W / {len(losses)}L)

INDIVIDUAL TRADE REVIEWS:
{reviews_text}

Your job: Write a concise, actionable learning block for the trading journal.
Focus on PATTERNS across the batch — what setups consistently worked, what failed, and why.
This will be read before every future trade analysis, so make it dense and specific.

Format it EXACTLY like this (this is markdown that goes into Obsidian):

### BACKTEST-{run_id} Batch {batch_number} — {datetime.now().strftime('%Y-%m-%d')} ({len(trade_reviews)} trades)
- **Win rate:** {win_rate:.1f}%
- **Best setup:** <which setup type had the best results>
- **Worst setup:** <which setup type underperformed>
- **Stop quality:** <were stops generally too tight, appropriate, or too wide>
- **Pattern 1:** <specific repeating observation across winning trades>
- **Pattern 2:** <specific repeating observation across losing trades>
- **Rule going forward:** <one concrete actionable rule derived from this batch>
- **Avoid:** <one specific setup condition that should be skipped based on this data>

Write ONLY the markdown block above. No JSON. No preamble. Start with the ### header."""

    try:
        _BATCH_MODEL = "claude-opus-4-6"
        _rl_acquire(f"write_batch_learnings(batch={batch_number})")
        response = _client().messages.create(
            model=_BATCH_MODEL,
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        _rl_log(f"write_batch_learnings(batch={batch_number})", _BATCH_MODEL,
                response.usage.input_tokens, response.usage.output_tokens)
        learning_text = response.content[0].text.strip()
        _append_to_obsidian(learning_text)
        print(f"[claude_reviewer] Batch {batch_number} learnings written to Obsidian ({len(trade_reviews)} trades)")
        return learning_text
    except Exception as e:
        if _is_credit_error(e):
            raise
        print(f"[claude_reviewer] write_batch_learnings error: {e}")
        return None


# ── Obsidian write ────────────────────────────────────────────────────────────

def _append_to_obsidian(text: str) -> None:
    """Append to the live learnings.md that the trading bot reads."""
    try:
        from memory.obsidian_writer import _vault_path
        path = _vault_path() / "learnings.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            if path.stat().st_size == 0:
                f.write("# Trade Learnings\n> Re-read before every analysis. Updated after every close.\n")
            f.write(f"\n{text}\n")
    except Exception as e:
        print(f"[claude_reviewer] Obsidian write error: {e}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _slice_post_entry(
    daily_df: pd.DataFrame,
    entry_dt: datetime,
    exit_dt:  datetime,
) -> pd.DataFrame:
    """Return daily bars from entry date through exit date + 2 extra bars."""
    if daily_df is None or daily_df.empty:
        return pd.DataFrame()
    entry_ts = pd.Timestamp(entry_dt)
    exit_ts  = pd.Timestamp(exit_dt)
    mask     = (daily_df.index >= entry_ts) & (daily_df.index <= exit_ts + pd.Timedelta(days=3))
    return daily_df[mask]


def _strip_fences(text: str) -> str:
    if text.startswith("```"):
        parts = text.split("```")
        text  = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    return text.strip()


def _is_credit_error(exc: Exception) -> bool:
    """Return True if the exception signals exhausted credits / billing block."""
    msg = str(exc).lower()
    return (
        "credit" in msg
        or "billing" in msg
        or getattr(exc, "status_code", None) in (402, 403)
    )
