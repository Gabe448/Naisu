#!/usr/bin/env python3
"""
discuss.py - Market thesis discussion using Claude Code (subscription, no API credits).
Run: python discuss.py
Type 'exit' to end and save transcript to Obsidian.
"""

import subprocess
import sys
from datetime import datetime
from pathlib import Path

OBSIDIAN_VAULT = Path(r"C:\Users\GabeG29\Downloads\SecondBrain")
THESIS_DIR = OBSIDIAN_VAULT / "03-knowledge" / "trading" / "thesis"

TRADER_PERSONA = (
    "You are a skeptical senior trader with 20+ years across equities, derivatives, macro, and crypto. "
    "Your job is NOT to be supportive - it is to be right and keep the other person honest. "
    "Rules: Have strong opinions and state them directly. Find the single weakest link in every thesis and attack it specifically. "
    "Challenge timing as hard as you challenge the thesis. Ask at most ONE sharp question per response. "
    "Reference real dynamics: positioning, flows, macro regime, earnings cycle, sentiment, short interest. "
    "If a trade is crowded, say it. If it needs 3 things to go right, say it. "
    "Be brief - 3 to 6 sentences max. Never say great point or interesting or anything that sounds like assistant praise. "
    "You are not here to make the user feel smart. You are here to prevent bad trades."
)


def ask_trader(conversation: list[dict], new_message: str) -> str:
    history = ""
    for turn in conversation:
        label = "User" if turn["role"] == "user" else "Trader"
        history += f"{label}: {turn['content']}\n\n"

    prompt = (
        f"{history}"
        f"User: {new_message}\n\n"
        f"Trader (respond in character - skeptical, direct, max 6 sentences):"
    )

    result = subprocess.run(
        ["claude", "-p", prompt, "--append-system-prompt", TRADER_PERSONA],
        capture_output=True,
        text=True,
        encoding="utf-8",
        shell=True,
    )

    if result.returncode != 0:
        err = result.stderr.strip() or result.stdout.strip()
        return f"[Error: {err}]"

    return result.stdout.strip()


def save_to_obsidian(conversation: list[dict], date_str: str) -> str:
    THESIS_DIR.mkdir(parents=True, exist_ok=True)

    # Extract a rough topic from the first user message
    first_msg = conversation[0]["content"] if conversation else "trading-thesis"
    words = [w.lower() for w in first_msg.split() if w.isalpha() and len(w) > 3]
    slug = "-".join(words[:4]) if words else "trading-thesis"

    filepath = THESIS_DIR / f"{date_str}-{slug}.md"

    lines = [
        "---",
        f"date: {date_str}",
        f"tags: [trading, thesis]",
        "---",
        "",
        f"# Thesis Discussion - {date_str}",
        "",
        "## Transcript",
        "",
    ]

    for turn in conversation:
        label = "**YOU**" if turn["role"] == "user" else "**TRADER**"
        lines.append(f"{label}: {turn['content']}")
        lines.append("")

    filepath.write_text("\n".join(lines), encoding="utf-8")
    return str(filepath)


def main():
    conversation: list[dict] = []
    date_str = datetime.now().strftime("%Y-%m-%d")

    print()
    print("-" * 58)
    print("  THESIS DISCUSSION - Skeptical senior trader mode")
    print("  No hand-holding. Bring a real idea.")
    print("  Type 'exit' to end and save to Obsidian.")
    print("-" * 58)
    print()

    while True:
        try:
            user_input = input("YOU: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit"):
            break

        print("\nTRADER: ", end="", flush=True)
        response = ask_trader(conversation, user_input)
        print(response)
        print()

        conversation.append({"role": "user", "content": user_input})
        conversation.append({"role": "assistant", "content": response})

    if len(conversation) < 2:
        print("Nothing to save - session too short.")
        return

    try:
        path = save_to_obsidian(conversation, date_str)
        print(f"\nSaved to: {path}")
    except Exception as exc:
        print(f"\nFailed to save: {exc}")


if __name__ == "__main__":
    main()
