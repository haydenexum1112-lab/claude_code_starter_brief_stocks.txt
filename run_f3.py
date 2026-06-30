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
  python run_f3.py demo [CSV] [MARKET]
                             Show the most recent REAL setup the engine would have
                             fired on your downloaded data (default NQ.csv).
  python run_f3.py backtest CSV [MARKET]
                             Backtest on REAL historical OHLC — reports win rate,
                             avg R, return and max drawdown. Real data required.

  Get real data first:  pip install yfinance && python fetch_data.py
"""
from __future__ import annotations

import logging
import sys

from f3.config import F3Config
from f3.engine import F3Engine
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
        # Honest demo: show the most recent REAL setup the engine would have fired
        # on your downloaded data. No made-up examples.
        import os
        from f3.market_data import load_csv
        from f3.backtest import _group_by_day
        args = sys.argv[2:]
        path = next((a for a in args if a.lower().endswith(".csv")), "NQ.csv")
        market = next((a for a in args if not a.lower().endswith(".csv")), "NQ")
        if not os.path.exists(path):
            print(f"No real data found at {path!r}.\n"
                  "Download it first:  pip install yfinance && python fetch_data.py\n"
                  "Then:  python run_f3.py demo NQ.csv NQ")
            sys.exit(1)
        candles = load_csv(path)
        engine = F3Engine(cfg)
        last_fire = None
        for day in list(_group_by_day(candles).values())[-10:]:  # last ~10 sessions
            for i in range(cfg.htf_swing_width * 2 + 5, len(day)):
                try:
                    d = engine.evaluate(market, day[: i + 1], account_balance=50_000)
                except ValueError:
                    continue
                if d.fire:
                    last_fire = (d, day[i].time)
        if last_fire is None:
            print(f"No FIRE in the last sessions of real {market} data — the system "
                  "is being patient (that's the point). Try the backtest for the full picture.")
        else:
            d, when = last_fire
            print(f"Most recent REAL setup it would have fired — {market} at "
                  f"{when:%Y-%m-%d %H:%M ET}:")
            _print_decision(d)
    elif cmd == "backtest":
        import os
        from f3.backtest import F3Backtester
        from f3.market_data import load_csv
        args = sys.argv[2:]
        path = next((a for a in args if a.lower().endswith(".csv")), None)
        market = next((a for a in args if not a.lower().endswith(".csv")), "NQ")
        if path is None or not os.path.exists(path):
            print("Honest backtest needs REAL data — no synthetic results.\n"
                  "Download it:  pip install yfinance && python fetch_data.py\n"
                  "Then run:     python run_f3.py backtest NQ.csv NQ")
            sys.exit(1)
        candles = load_csv(path)
        print(f"Backtesting {market} on {path} ({len(candles)} real candles)\n")
        slip = {"NQ": 0.5, "GC": 0.2}.get(market.upper(), 0.5)
        bt = F3Backtester(cfg, slippage_points=slip, commission=4.0)
        result = bt.run(market, candles)
        print(result.report())
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
