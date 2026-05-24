"""
discord_bot.py — Posts formatted signals and watchlist items to Discord.
Called by main.py after analysis.py returns decisions.
"""

import discord
import os
import asyncio
from datetime import datetime

SIGNAL_CHANNEL_ID = int(os.getenv("DISCORD_SIGNAL_CHANNEL_ID", "0"))
WATCHLIST_CHANNEL_ID = int(os.getenv("DISCORD_WATCHLIST_CHANNEL_ID", "0"))

intents = discord.Intents.default()
_client = discord.Client(intents=intents)


def _signal_embed(decision: dict, scan_data: dict) -> discord.Embed:
    ticker = decision["ticker"]
    embed = discord.Embed(
        title=f"SIGNAL — {ticker}",
        color=0x00FF88,
        timestamp=datetime.utcnow(),
    )
    embed.add_field(name="Price", value=f"${scan_data.get('price', 'n/a')}", inline=True)
    embed.add_field(name="Volume", value=f"{scan_data.get('vol_ratio', 'n/a')}x avg", inline=True)
    embed.add_field(name="RSI", value=str(scan_data.get("rsi", "n/a")), inline=True)
    embed.add_field(name="Entry Zone", value=decision.get("entry") or "n/a", inline=True)
    embed.add_field(name="Stop", value=decision.get("stop") or "n/a", inline=True)
    embed.add_field(name="Target 1", value=decision.get("target1") or "n/a", inline=True)
    embed.add_field(name="Target 2", value=decision.get("target2") or "n/a", inline=True)
    embed.add_field(name="Scanner signals", value=scan_data.get("reasons", "n/a"), inline=False)
    embed.add_field(name="Thesis", value=(decision.get("thesis") or "")[:1024], inline=False)
    embed.set_footer(text="Trading Bot • Not financial advice")
    return embed


def _watchlist_embed(decision: dict, scan_data: dict) -> discord.Embed:
    ticker = decision["ticker"]
    embed = discord.Embed(
        title=f"WATCHLIST — {ticker}",
        color=0xFFAA00,
        timestamp=datetime.utcnow(),
    )
    embed.add_field(name="Price", value=f"${scan_data.get('price', 'n/a')}", inline=True)
    embed.add_field(name="Volume", value=f"{scan_data.get('vol_ratio', 'n/a')}x avg", inline=True)
    embed.add_field(name="RSI", value=str(scan_data.get("rsi", "n/a")), inline=True)
    embed.add_field(name="Watching for", value=(decision.get("thesis") or "")[:1024], inline=False)
    embed.set_footer(text="Trading Bot • Not financial advice")
    return embed


async def _post_decisions(decisions: list[dict], scan_map: dict):
    await _client.wait_until_ready()

    sig_channel = _client.get_channel(SIGNAL_CHANNEL_ID)
    watch_channel = _client.get_channel(WATCHLIST_CHANNEL_ID)

    for decision in decisions:
        ticker = decision.get("ticker", "")
        scan_data = scan_map.get(ticker, {})
        d = decision.get("decision", "SKIP")

        if d == "SIGNAL" and sig_channel:
            embed = _signal_embed(decision, scan_data)
            await sig_channel.send(embed=embed)

        elif d == "WATCHLIST" and watch_channel:
            embed = _watchlist_embed(decision, scan_data)
            await watch_channel.send(embed=embed)


def post(decisions: list[dict], candidates: list[dict]):
    """
    Synchronous entry point called from main.py.
    Spins up the bot, posts, then closes.
    """
    scan_map = {c["ticker"]: c for c in candidates}
    token = os.getenv("DISCORD_BOT_TOKEN", "")
    if not token:
        print("[discord] No DISCORD_BOT_TOKEN set — skipping post")
        return

    async def _run():
        @_client.event
        async def on_ready():
            await _post_decisions(decisions, scan_map)
            await _client.close()

        await _client.start(token)

    asyncio.run(_run())
