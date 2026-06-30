#!/usr/bin/env python3
"""
F3 Strategy — command-line entry point.

Usage:
  python run_f3.py tick      Run one scheduled tick (what the cloud cron calls):
                             morning email / scan & auto-fire / night email,
                             chosen by the current ET time. Default if no command.
  python run_f3.py scan      Force a market scan right now (ignores the clock).
  python run_f3.py morning   Send the morning report now.
  python run_f3.py night     Send the evening report now.
  python run_f3.py demo      Run the engine on a clean textbook setup and print
                             the full agent board + Claude's sign-off (no network).
  python run_f3.py backtest [CSV] [MARKET]
                             Backtest the strategy. With a CSV of historical OHLC
                             it reports win rate, avg R, return and max drawdown.
                             With no CSV it runs on a synthetic history (mechanics
                             check only — not a performance prediction).
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime

from f3.config import ET, F3Config
from f3.engine import F3Engine
from f3.market_data import textbook_long_setup
from f3.runner import run_tick, scan_markets
from f3 import alerts
from f3.state import DailyState


def _setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s — %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def _print_decision(d) -> None:
    print(f"\n{'='*60}\n{d.market}: {d.status}   (Claude CEO {d.ceo_confidence:.0f}% "
          f"— {'APPROVED' if d.ceo_approved else 'VETOED'})\n{'='*60}")
    if d.trade:
        t = d.trade
        print(f"{t.direction.value.upper():<5} entry {t.entry:.2f}  stop {t.stop:.2f}  "
              f"target {t.target:.2f}  R:R {t.reward_risk:.2f}")
        if d.fire:
            print(f"Size: {d.position_size} units (risk ${d.risk_dollars:,.2f})")
    print("\nThe 7 agents:")
    for v in d.verdicts:
        print(" ", v)
    print("\nDaily checklist:")
    for k, v in d.checklist.items():
        print("  ", "☑" if v else "☐", k)
    print(f"\nCEO: {d.ceo_reasoning}")
    print("ALL CHECKED → FIRE" if d.fire else "ANY MISSING → SKIP")


def main() -> None:
    _setup_logging()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "tick"
    cfg = F3Config()

    if cmd == "tick":
        run_tick(cfg)
    elif cmd == "scan":
        scan_markets(cfg, DailyState.load())
    elif cmd == "morning":
        alerts.send_morning_report(cfg)
    elif cmd == "night":
        alerts.send_evening_report(cfg, DailyState.load())
    elif cmd == "demo":
        end = datetime.now(ET).replace(hour=9, minute=45, second=0, microsecond=0)
        candles = textbook_long_setup(end=end)
        decision = F3Engine(cfg).evaluate("NQ", candles, account_balance=50_000)
        _print_decision(decision)
    elif cmd == "backtest":
        from f3.backtest import F3Backtester
        from f3.market_data import load_csv, synthetic_history
        args = [a for a in sys.argv[2:]]
        if args and args[0].lower().endswith(".csv"):
            candles = load_csv(args[0])
            market = args[1] if len(args) > 1 else "NQ"
            print(f"Backtesting {market} on {args[0]} ({len(candles)} candles)\n")
        else:
            market = args[0] if args else "NQ"
            candles = synthetic_history(days=12)
            print("Backtesting on SYNTHETIC history — illustrative only, NOT a\n"
                  "performance prediction. Pass a real CSV: run_f3.py backtest data.csv NQ\n")
        # Realistic costs per market: slippage (price points against you) + commission.
        slip = {"NQ": 0.5, "GC": 0.2}.get(market.upper(), 0.5)
        bt = F3Backtester(cfg, slippage_points=slip, commission=4.0)
        result = bt.run(market, candles)
        print(result.report())
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
