"""
Backtesting CLI.

Usage:
  # Full 6-month backtest (calls Claude — costs API credits)
  python -m backtest.run_backtest

  # Custom date range
  python -m backtest.run_backtest --start 2024-10-01 --end 2025-04-01

  # Dry-run (score only, no Claude calls, free)
  python -m backtest.run_backtest --dry-run

  # Faster run: fewer candidates per day
  python -m backtest.run_backtest --top-n 10 --dry-run

  # Resume / compare: re-use a specific run_id
  python -m backtest.run_backtest --run-id bt_20241001_abc123

  # Print stats for an existing run without re-running
  python -m backtest.run_backtest --stats bt_20241001_abc123

Cost estimate:
  Full run with Claude: ~500 tickers × ~100 trading days × 0.3 pass rate × ~$0.04/call ≈ ~$600
  With top-n=10 and real pass rates (~5%): ~$20-40 for 6 months
  Dry-run: $0
"""
import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Trading Bot Backtester")

    parser.add_argument(
        "--start",
        type=str,
        default=None,
        help="Start date YYYY-MM-DD (default: 6 months ago)",
    )
    parser.add_argument(
        "--end",
        type=str,
        default=None,
        help="End date YYYY-MM-DD (default: today)",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=20,
        help="Max candidates passed to Claude per day (default: 20)",
    )
    parser.add_argument(
        "--max-positions",
        type=int,
        default=5,
        help="Max concurrent simulated positions (default: 5)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=20,
        help="Closed trades before writing Obsidian learnings batch (default: 20)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip Claude calls — score-only mode, free, fast",
    )
    parser.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="Custom run ID (default: auto-generated)",
    )
    parser.add_argument(
        "--stats",
        type=str,
        default=None,
        metavar="RUN_ID",
        help="Print summary stats for an existing run (no new backtest)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-day progress output",
    )
    parser.add_argument(
        "--tickers",
        type=str,
        default=None,
        help="Comma-separated ticker list (default: full S&P 500)",
    )
    parser.add_argument(
        "--from-dryrun",
        type=str,
        default=None,
        metavar="RUN_ID",
        help="Run Claude only on candidates from a prior dry-run (e.g. bt_20251013_db4037). "
             "Cheapest Claude mode: N_signals calls total instead of top_n * trading_days.",
    )

    args = parser.parse_args()

    # ── Stats-only mode ───────────────────────────────────────────────────────
    if args.stats:
        _print_existing_stats(args.stats)
        return

    _load_env()

    # ── From dry-run mode ─────────────────────────────────────────────────────
    if args.from_dryrun:
        from backtest.engine import run_backtest_from_dryrun
        import json

        # Show how many signals are in that dry-run before spending money
        results_dir  = Path(__file__).parent / "results"
        store_path   = results_dir / f"positions_{args.from_dryrun}.json"
        if not store_path.exists():
            print(f"ERROR: dry-run positions not found at {store_path}")
            sys.exit(1)

        with open(store_path) as f:
            data = json.load(f)
        n_signals = len(data.get("closed", [])) + len(data.get("open", [])) + len(data.get("pending", []))
        est_cost  = n_signals * 0.04
        print(f"\nDry-run source: {args.from_dryrun}")
        print(f"Signals to evaluate with Claude: {n_signals}")
        print(f"Estimated cost: ~${est_cost:.2f} ({n_signals} calls x $0.04)")
        resp = input("Continue? [y/N]: ").strip().lower()
        if resp != "y":
            print("Aborted.")
            return

        stats = run_backtest_from_dryrun(
            dryrun_run_id  = args.from_dryrun,
            max_positions  = args.max_positions,
            batch_size     = args.batch_size,
            run_id         = args.run_id,
            progress       = not args.quiet,
        )
        print(f"\nRun ID: {stats['run_id']}")
        return

    # ── Standard run ─────────────────────────────────────────────────────────
    end_date   = datetime.strptime(args.end,   "%Y-%m-%d") if args.end   else datetime.now()
    start_date = datetime.strptime(args.start, "%Y-%m-%d") if args.start else end_date - timedelta(days=182)

    if start_date >= end_date:
        print("ERROR: start date must be before end date")
        sys.exit(1)

    tickers = None
    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        print(f"[run_backtest] Custom ticker list: {tickers}")

    if not args.dry_run:
        days = (end_date - start_date).days
        trading_days = int(days * 5 / 7)
        est_calls    = trading_days * args.top_n * 0.3
        est_cost     = est_calls * 0.04
        print(f"\n[WARNING] CLAUDE API WARNING")
        print(f"   ~{int(est_calls)} Claude calls estimated -> ~${est_cost:.0f}")
        print(f"   Use --dry-run to skip Claude calls (free score-only mode)")
        print(f"   Use --from-dryrun <run_id> for cheapest Claude mode (~$5)\n")
        resp = input("Continue? [y/N]: ").strip().lower()
        if resp != "y":
            print("Aborted.")
            return

    from backtest.engine import run_backtest

    stats = run_backtest(
        start_date    = start_date,
        end_date      = end_date,
        tickers       = tickers,
        top_n         = args.top_n,
        max_positions = args.max_positions,
        batch_size    = args.batch_size,
        skip_claude   = args.dry_run,
        run_id        = args.run_id,
        progress      = not args.quiet,
    )

    print(f"\nRun ID: {stats['run_id']}")
    print(f"Results saved to: backtest/results/")


def _print_existing_stats(run_id: str) -> None:
    """Print summary for a completed backtest run."""
    import json

    results_dir = Path(__file__).parent / "results"
    summary_path = results_dir / f"summary_{run_id}.json"

    if not summary_path.exists():
        print(f"ERROR: No summary found for run_id '{run_id}'")
        print(f"Expected: {summary_path}")
        return

    with open(summary_path, "r") as f:
        stats = json.load(f)

    print(f"\n{'='*60}")
    print(f"BACKTEST RESULTS — {run_id}")
    print(f"Period:    {stats.get('start_date', '?')} → {stats.get('end_date', '?')}")
    print(f"Trades:    {stats.get('total', 0)}")
    print(f"Win Rate:  {stats.get('win_rate', 0)}%  ({stats.get('wins', 0)}W / {stats.get('losses', 0)}L)")
    print(f"Avg Win:   {stats.get('avg_win', 0):+.2f}%")
    print(f"Avg Loss:  {stats.get('avg_loss', 0):+.2f}%")
    print(f"Expect.:   {stats.get('expectancy', 0):+.2f}% / trade")
    print(f"Total PnL: {stats.get('total_pnl', 0):+.2f}%")
    print(f"Signals:   {stats.get('signals', 0)}")
    print(f"Batches:   {stats.get('batches', 0)}")
    print(f"{'='*60}\n")

    # Individual positions
    from backtest.position_sim import get_closed_positions
    closed = get_closed_positions(run_id)
    if closed:
        print(f"{'Ticker':<8} {'Dir':<6} {'Entry':>8} {'Exit':>8} {'PnL':>8} {'Days':>5} {'Status'}")
        print("-" * 60)
        for p in closed[-30:]:  # last 30
            print(
                f"{p['ticker']:<8} {p['direction']:<6} "
                f"${p.get('entry_price', 0):>7.2f} ${p.get('exit_price', 0):>7.2f} "
                f"{p.get('final_pnl_pct', 0):>+7.2f}% {p.get('days_held', 0):>5} "
                f"{p.get('status', '?')}"
            )


def _load_env() -> None:
    """Load .env from project root if python-dotenv is available."""
    try:
        from dotenv import load_dotenv
        env_path = Path(__file__).parent.parent / ".env"
        if env_path.exists():
            load_dotenv(env_path)
            print(f"[run_backtest] Loaded .env from {env_path}")
    except ImportError:
        pass  # dotenv not installed, assume env vars are already set


if __name__ == "__main__":
    # Allow running as: python backtest/run_backtest.py
    # Add project root to path
    project_root = Path(__file__).parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    main()
