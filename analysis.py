"""
analysis.py — Claude receives the top 10 scanner candidates and decides:
  SIGNAL   → trade thesis + entry/stop/targets
  WATCHLIST → worth monitoring, no entry yet
  SKIP     → not interesting

Claude reads Obsidian learnings before every analysis run so the
strategy evolves with every trade outcome documented in memory.py.
"""

import anthropic
import json
import os
import requests
from datetime import datetime
from pathlib import Path

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
LUMINAFLOW_TOKEN = os.getenv("LUMINAFLOW_TOKEN", "")
OBSIDIAN_VAULT_PATH = Path(os.getenv("OBSIDIAN_VAULT_PATH", ""))

SYSTEM_PROMPT = """You are a professional swing/day trader with deep technical analysis expertise.

STARTING STRATEGY (evolve this — it is not a rulebook):

CONFLUENCE: Require 3+ signals before any trade. Never trade a single indicator. Minimum 1:2 R/R.

PATTERNS: Head & shoulders, double top/bottom, falling wedge, cup & handle, ascending/descending triangle, symmetrical triangle.

ENTRY RULES:
- 3+ rejections at a level = reversal likely
- Long wicks at extremes with volume = reversal signal
- EMA reclaim/loss with volume = trend confirmation
- Volume 1.5x+ required
- Failed breakouts = trade the opposite direction
- 200 EMA = macro trend filter
- Fresh breakout from 10+ day base with volume = SIGNAL NOW, do not wait for pullback
- Pullback entries only for stocks already extended with no clear base

EXIT RULES:
- Target 1 (25-40% position): first resistance
- Target 2 (40-50% position): major resistance
- Target 3 (20-25% position): runner, trail stop
- Exit before earnings/FOMC/CPI

STOPS: Below pattern invalidation only. Never move stop against position. Trail in profit direction only.

GEX: Use for directional confluence only, not as price targets.

TIMEFRAME: Daily/4h for context, 1h/15min for entry timing.

DECISION RULES:
- Output exactly one decision per ticker: SIGNAL, WATCHLIST, or SKIP
- For SIGNAL: provide entry zone, stop, target 1, target 2, and a concise thesis
- For WATCHLIST: explain what you're waiting for
- For SKIP: one sentence why

IMPORTANT: You will receive learnings from past trades. Read them carefully and let them influence your analysis. If a past mistake is relevant to a current setup, call it out explicitly."""


def _load_obsidian_learnings() -> str:
    """Load recent trade learnings from Obsidian vault."""
    learnings_dir = OBSIDIAN_VAULT_PATH / "Trading" / "Learnings"
    if not learnings_dir.exists():
        return ""

    notes = sorted(learnings_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    excerpts = []
    for note in notes[:10]:
        try:
            text = note.read_text(encoding="utf-8")
            # Grab the first 300 chars of each note as a summary
            excerpt = text[:300].strip()
            excerpts.append(f"### {note.stem}\n{excerpt}")
        except Exception:
            continue

    if not excerpts:
        return ""

    return "## PAST TRADE LEARNINGS (read before analysis)\n\n" + "\n\n".join(excerpts)


def _get_gex(ticker: str) -> dict:
    """Fetch GEX data from LuminaFlow."""
    if not LUMINAFLOW_TOKEN:
        return {}
    try:
        url = f"https://api.luminaflow.io/v1/gex/{ticker}"
        headers = {"Authorization": f"Bearer {LUMINAFLOW_TOKEN}"}
        resp = requests.get(url, headers=headers, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            return {
                "net_gex": data.get("net_gex"),
                "call_wall": data.get("call_wall"),
                "put_wall": data.get("put_wall"),
                "gamma_flip": data.get("gamma_flip"),
                "bias": data.get("bias"),
            }
    except Exception:
        pass
    return {}


def _get_news(ticker: str) -> list[str]:
    """Fetch recent news headlines from Polygon."""
    polygon_key = os.getenv("POLYGON_API_KEY", "")
    if not polygon_key:
        return []
    try:
        url = f"https://api.polygon.io/v2/reference/news?ticker={ticker}&limit=5&apiKey={polygon_key}"
        resp = requests.get(url, timeout=8)
        articles = resp.json().get("results", [])
        return [a.get("title", "") for a in articles if a.get("title")]
    except Exception:
        return []


def _build_candidate_block(candidate: dict) -> str:
    gex = _get_gex(candidate["ticker"])
    news = _get_news(candidate["ticker"])

    lines = [
        f"TICKER: {candidate['ticker']}",
        f"Price: ${candidate['price']}  Score: {candidate['score']}",
        f"Volume ratio: {candidate['vol_ratio']}x  RSI: {candidate['rsi']}  5d return: {candidate['ret_5d']}%",
        f"Scanner signals: {candidate['reasons']}",
    ]

    if gex:
        lines.append(
            f"GEX — bias: {gex.get('bias', 'n/a')}  net: {gex.get('net_gex', 'n/a')}  "
            f"gamma flip: {gex.get('gamma_flip', 'n/a')}  "
            f"call wall: {gex.get('call_wall', 'n/a')}  put wall: {gex.get('put_wall', 'n/a')}"
        )

    if news:
        lines.append("Recent news:")
        for headline in news[:3]:
            lines.append(f"  - {headline}")

    return "\n".join(lines)


def analyze(candidates: list[dict]) -> list[dict]:
    """
    Send top candidates to Claude. Returns a list of decision dicts:
      {ticker, decision, thesis, entry, stop, target1, target2, raw}
    """
    if not candidates:
        return []

    learnings = _load_obsidian_learnings()

    candidate_blocks = "\n\n---\n\n".join(
        _build_candidate_block(c) for c in candidates
    )

    user_message = f"""{learnings}

## CANDIDATES FOR TODAY — {datetime.now().strftime("%Y-%m-%d %H:%M")}

{candidate_blocks}

---

For each ticker above, output a JSON array. Each element:
{{
  "ticker": "AAPL",
  "decision": "SIGNAL" | "WATCHLIST" | "SKIP",
  "thesis": "...",
  "entry": "...",
  "stop": "...",
  "target1": "...",
  "target2": "..."
}}

Only SIGNAL entries need entry/stop/targets. Set those fields to null for WATCHLIST/SKIP.
Output ONLY the JSON array, no other text."""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    raw = response.content[0].text.strip()

    try:
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        decisions = json.loads(raw.strip())
    except json.JSONDecodeError:
        # Fallback: return raw text wrapped in a single result
        return [{"ticker": "ERROR", "decision": "SKIP", "thesis": raw, "entry": None, "stop": None, "target1": None, "target2": None}]

    return decisions
