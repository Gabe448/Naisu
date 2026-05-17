"""
Obsidian writer — logs signals, positions, and weekly reviews to your vault.

File structure in vault:
  Trading-Bot/
    YYYY-MM-DD.md        ← daily trade log
    weekly/YYYY-WNN.md   ← weekly review (every Friday)
    positions/TICKER-ID.md  ← individual position journals
"""
import os
from datetime import datetime, timedelta
from pathlib import Path


def _vault_path() -> Path:
    base   = os.environ.get("OBSIDIAN_VAULT_PATH", ".")
    folder = os.environ.get("OBSIDIAN_TRADING_FOLDER", "Trading-Bot")
    return Path(base) / folder


def _today_note_path() -> Path:
    date_str = datetime.now().strftime("%Y-%m-%d")
    return _vault_path() / f"{date_str}.md"


def _ensure(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


# ── Daily log ─────────────────────────────────────────────────────────────────

def log_signal(signal: dict) -> None:
    """Log a new high-conviction swing signal to today's daily note."""
    path = _today_note_path()
    _ensure(path)
    now = datetime.now().strftime("%H:%M")

    lines = [
        f"\n## Signal: {signal['ticker']} ({signal['direction'].upper()}) — {now}",
        f"- **Price:** ${signal.get('price', '?')}",
        f"- **Entry:** ${signal.get('entry', '?')} | **Stop:** ${signal.get('stop', '?')} | **Target:** ${signal.get('target', '?')}",
        f"- **R:R:** {signal.get('rr_ratio', '?')}:1",
        f"- **Conviction:** {signal.get('conviction_score', '?')}/100",
        f"- **GEX Regime:** {signal.get('gex_regime', '?').upper()}",
        f"- **Flow Bias:** {signal.get('flow_bias', '?')} ({signal.get('flow_call_pct', 0)*100:.0f}% calls)",
        f"\n**Thesis:**\n{signal.get('thesis', '')}",
        "\n---",
    ]

    with open(path, "a", encoding="utf-8") as f:
        if path.stat().st_size == 0:
            f.write(f"# Trading Log — {datetime.now().strftime('%Y-%m-%d')}\n")
        f.write("\n".join(lines) + "\n")


def log_position_opened(position: dict) -> None:
    """Create a position journal file."""
    ticker = position["ticker"]
    pid    = position["id"]
    path   = _vault_path() / "positions" / f"{ticker}-{pid}.md"
    _ensure(path)

    lines = [
        f"# Position: {ticker} ({position['direction'].upper()})",
        f"**Opened:** {position['entry_date']}",
        f"**Entry:** ${position['entry_price']} | **Stop:** ${position['stop_price']} | **Target:** ${position['target_price']}",
        f"**Conviction:** {position.get('conviction_score', '?')}/100",
        f"\n## Thesis",
        position.get("thesis", "No thesis recorded."),
        "\n## Updates\n",
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def log_position_closed(position: dict) -> None:
    """Append close details to position journal and today's daily note."""
    ticker = position["ticker"]
    pid    = position["id"]

    # Update position file
    pos_path = _vault_path() / "positions" / f"{ticker}-{pid}.md"
    if pos_path.exists():
        close_entry = (
            f"\n## Closed\n"
            f"- **Date:** {position.get('exit_date', '?')}\n"
            f"- **Exit Price:** ${position.get('exit_price', '?')}\n"
            f"- **Reason:** {position.get('status', '?').replace('_', ' ').title()}\n"
            f"- **Final PnL:** {position.get('final_pnl_pct', 0):+.2f}%\n"
        )
        with open(pos_path, "a", encoding="utf-8") as f:
            f.write(close_entry)

    # Also append to daily log
    day_path = _today_note_path()
    _ensure(day_path)
    now = datetime.now().strftime("%H:%M")
    entry = (
        f"\n## Closed: {ticker} — {now}\n"
        f"- Status: {position.get('status', '?').replace('_', ' ').title()}\n"
        f"- Exit: ${position.get('exit_price', '?')} | PnL: {position.get('final_pnl_pct', 0):+.2f}%\n"
        f"- Days held: {position.get('days_held', '?')}\n---\n"
    )
    with open(day_path, "a", encoding="utf-8") as f:
        f.write(entry)


def log_trade_learning(
    ticker:       str,
    direction:    str,
    setup:        str,
    what_worked:  str,
    what_failed:  str,
    deviation:    str = "",
    outcome:      str = "",
) -> None:
    """
    Write a trade learning after every closed trade.
    Read back by signal_builder._load_learnings() before every analysis.
    """
    path = _vault_path() / "learnings.md"
    _ensure(path)
    now   = datetime.now().strftime("%Y-%m-%d")
    entry = (
        f"\n### {now} — {ticker} {direction.upper()}\n"
        f"- **Setup:** {setup}\n"
        f"- **What worked:** {what_worked}\n"
        f"- **What failed:** {what_failed}\n"
        f"- **Outcome:** {outcome}\n"
    )
    if deviation:
        entry += f"- **Deviation:** {deviation}\n"
    with open(path, "a", encoding="utf-8") as f:
        if path.stat().st_size == 0:
            f.write("# Trade Learnings\n> Re-read before every analysis. Updated after every close.\n")
        f.write(entry)


def get_recent_learnings(n: int = 3) -> list[str]:
    """
    Return last N learning summaries as strings.
    Called by signal_builder at the start of every analysis.
    """
    path = _vault_path() / "learnings.md"
    if not path.exists():
        return []
    try:
        text    = path.read_text(encoding="utf-8")
        blocks  = [b.strip() for b in text.split("###") if b.strip()]
        recent  = blocks[-n:] if len(blocks) >= n else blocks
        return [b.split("\n")[0] for b in recent]  # first line = date + ticker
    except Exception:
        return []


def log_note(title: str, body: str) -> None:
    """Freeform note to today's daily log."""
    path = _today_note_path()
    _ensure(path)
    now   = datetime.now().strftime("%H:%M")
    entry = f"\n## {title} — {now}\n{body}\n---\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(entry)


# ── Weekly review ─────────────────────────────────────────────────────────────

def write_weekly_review(stats: dict, open_positions: list[dict]) -> None:
    """
    Write a weekly review note every Friday.
    Stats come from position_store.get_weekly_stats().
    This is the self-refinement loop — what worked and why.
    """
    now  = datetime.now()
    week = now.isocalendar().week
    year = now.isocalendar().year
    path = _vault_path() / "weekly" / f"{year}-W{week:02d}.md"
    _ensure(path)

    trades     = stats.get("trades", 0)
    wins       = stats.get("wins", 0)
    losses     = stats.get("losses", 0)
    win_rate   = stats.get("win_rate", 0)
    avg_win    = stats.get("avg_win", 0)
    avg_loss   = stats.get("avg_loss", 0)
    positions  = stats.get("positions", [])

    # Expectancy = (win_rate * avg_win) + (loss_rate * avg_loss)
    loss_rate  = 1 - (win_rate / 100)
    expectancy = round((win_rate / 100 * avg_win) + (loss_rate * avg_loss), 2)

    lines = [
        f"# Weekly Review — Week {week}, {year}",
        f"**Date:** {now.strftime('%Y-%m-%d')}",
        "",
        "## Performance",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Trades | {trades} |",
        f"| Wins   | {wins} ({win_rate}%) |",
        f"| Losses | {losses} |",
        f"| Avg Win | {avg_win:+.2f}% |",
        f"| Avg Loss | {avg_loss:+.2f}% |",
        f"| Expectancy | {expectancy:+.2f}% per trade |",
        "",
    ]

    # Individual trade breakdown
    if positions:
        lines += ["## Trade Log", "| Ticker | Direction | Entry | Exit | PnL | Days | Status |",
                  "|--------|-----------|-------|------|-----|------|--------|"]
        for p in positions:
            lines.append(
                f"| {p['ticker']} | {p['direction']} | ${p['entry_price']} "
                f"| ${p.get('exit_price','?')} | {p.get('final_pnl_pct',0):+.2f}% "
                f"| {p.get('days_held','?')} | {p.get('status','?')} |"
            )
        lines.append("")

    # What worked
    lines += [
        "## What Worked",
        _analyze_what_worked(positions),
        "",
        "## What Didn't",
        _analyze_what_didnt(positions),
        "",
        "## Open Positions Entering Next Week",
    ]
    if open_positions:
        for pos in open_positions:
            lines.append(
                f"- **{pos['ticker']}** {pos['direction']} | "
                f"Entry: ${pos['entry_price']} | "
                f"Current: ${pos.get('current_price','?')} | "
                f"PnL: {pos.get('pnl_pct',0):+.2f}% | "
                f"Day {pos.get('days_held',0)}"
            )
    else:
        lines.append("- No open positions.")

    lines += [
        "",
        "## Scoring System Notes",
        "Reflect here: which scoring components were highest in winning trades vs losing trades?",
        "Adjust CONVICTION_THRESHOLD or component weights in analysis/scoring.py if needed.",
        "",
        "---",
        f"*Generated by Trading Bot on {now.strftime('%Y-%m-%d %H:%M')}*",
    ]

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"[obsidian] Weekly review written → {path}")


def _analyze_what_worked(positions: list[dict]) -> str:
    wins = [p for p in positions if p.get("final_pnl_pct", 0) > 0]
    if not wins:
        return "No winning trades this week."

    common = []
    directions = [p.get("direction") for p in wins]
    if directions.count("long") > directions.count("short"):
        common.append("Long setups outperformed")
    elif directions.count("short") > directions.count("long"):
        common.append("Short setups outperformed")

    high_score_wins = [p for p in wins if p.get("conviction_score", 0) >= 80]
    if len(high_score_wins) > len(wins) * 0.6:
        common.append("High-conviction (80+) setups had strong hit rate")

    return "\n".join(f"- {c}" for c in common) if common else "- Review manually."


def _analyze_what_didnt(positions: list[dict]) -> str:
    losses = [p for p in positions if p.get("final_pnl_pct", 0) <= 0]
    if not losses:
        return "No losing trades this week."

    notes = []
    low_score_losses = [p for p in losses if p.get("conviction_score", 100) < 75]
    if low_score_losses:
        notes.append(f"{len(low_score_losses)} losses had sub-75 conviction score — consider raising threshold")

    short_holds = [p for p in losses if p.get("days_held", 99) <= 1]
    if short_holds:
        notes.append(f"{len(short_holds)} positions stopped out within 1 day — check entry timing vs 1H structure")

    return "\n".join(f"- {n}" for n in notes) if notes else "- Review manually."
