# Naisu — Trading Bot Project Context

## Who I Am
- Builder. Trader. Student. Creator.
- High risk tolerance. Short term focus. Data first.

## Active Projects
- **Naisu (Trading Bot)** — S&P 500 momentum scanner → Claude analyst → Discord signals
- Paper trading before real money. Alpaca for stocks (free API).

## Decisions Made
- Top 10 candidates per scan, every 60 minutes during market hours
- LuminaFlow for GEX/DEX/VEX options data
- Claude makes all final trade decisions — no hard scoring thresholds
- Max 5 concurrent positions
- Obsidian vault for trade journaling and learnings

## Bot Commands
- `!signal <TICKER>` — on-demand Claude analysis
- `!chart <TICKER>` — daily chart with GEX overlays
- `!positions` — list open trades
- `!add / !remove <TICKER>` — manage watchlist
- `!scan` — trigger a manual scan now
- `!macro` — upcoming macro events
- `!weekly` — trigger weekly review
