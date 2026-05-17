"""
main.py — entry point for the Swing Trading Analyst Bot.

Usage:
  python main.py              # run the Discord bot
  python main.py --dashboard  # run the web dashboard at localhost:5000
  python main.py --scan       # one-shot watchlist scan, print to console
  python main.py --brief      # print morning brief to console (no Discord)
  python main.py --weekly     # write weekly review to Obsidian now
"""
import argparse
import sys
from dotenv import load_dotenv

load_dotenv()


def run_bot():
    from discord_bot.bot import run
    run()


def run_scan():
    from analysis.signal_builder import scan_watchlist
    from analysis.chart_renderer import render_chart

    print("=" * 60)
    print("SWING TRADE SCANNER")
    print("=" * 60)

    signals = scan_watchlist()
    if not signals:
        print("No high-conviction setups found on current watchlist.")
        return

    print(f"\n{len(signals)} signal(s) found:\n")
    for sig in signals:
        direction = sig["direction"].upper()
        print(f"  {sig['ticker']:6} {direction:5} | Score: {sig['conviction_score']}/100 "
              f"| R:R: {sig['rr_ratio']}:1 | Entry: ${sig['entry']} "
              f"| Stop: ${sig['stop']} | Target: ${sig['target']}")
        print(f"         GEX: {sig['gex_regime']} | Flow: {sig['flow_bias']} "
              f"| Flip: ${sig.get('gex_flip', '?')}")
        print(f"  Thesis: {sig['thesis'][:120]}...")
        print()


def run_brief():
    from analysis.position_manager import get_position_summary
    from analysis.morning_brief import build_morning_brief

    print("=" * 60)
    print("MORNING BRIEF")
    print("=" * 60)

    positions = get_position_summary()
    if not positions:
        print("No open positions.\n")
    else:
        for pos in positions:
            pnl  = pos.get("pnl_pct", 0)
            days = pos.get("days_held", 0)
            print(f"  {pos['ticker']:6} {pos['direction'].upper()} | "
                  f"Entry: ${pos['entry_price']} | Current: ${pos['current_price']} | "
                  f"PnL: {pnl:+.2f}% | Day {days}")
            print(f"         Stop: ${pos['stop_price']} | Target: ${pos['target_price']}")
            print()


def run_weekly():
    from memory.position_store import get_weekly_stats, get_open_positions
    from memory.obsidian_writer import write_weekly_review

    stats   = get_weekly_stats()
    open_ps = get_open_positions()
    write_weekly_review(stats, open_ps)
    print(f"Weekly review written.")
    print(f"  Trades:    {stats.get('trades', 0)}")
    print(f"  Win Rate:  {stats.get('win_rate', 0)}%")
    print(f"  Avg Win:   {stats.get('avg_win', 0):+.2f}%")
    print(f"  Avg Loss:  {stats.get('avg_loss', 0):+.2f}%")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Swing Trading Analyst Bot")
    parser.add_argument("--scan",      action="store_true", help="One-shot watchlist scan")
    parser.add_argument("--brief",     action="store_true", help="Morning brief to console")
    parser.add_argument("--weekly",    action="store_true", help="Write weekly review now")
    parser.add_argument("--dashboard", action="store_true", help="Run web dashboard at localhost:5000")
    parser.add_argument("--port",      type=int, default=5000, help="Dashboard port (default 5000)")
    args = parser.parse_args()

    if args.scan:
        run_scan()
    elif args.brief:
        run_brief()
    elif args.weekly:
        run_weekly()
    elif args.dashboard:
        import threading, webbrowser
        from dashboard.app import app
        port = args.port
        print(f"Dashboard running at http://localhost:{port}")
        threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{port}")).start()
        app.run(host="0.0.0.0", port=port, debug=False)
    else:
        run_bot()
