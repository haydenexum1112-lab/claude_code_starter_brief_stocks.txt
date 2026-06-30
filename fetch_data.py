#!/usr/bin/env python3
"""
Download REAL intraday futures data for the F3 backtester.

Pulls NQ (Nasdaq) and GC (Gold) futures from Yahoo Finance as 5-minute candles
and writes NQ.csv / GC.csv in the format the backtester reads
(time,open,high,low,close,volume — timestamps in America/New_York so the
killzone windows line up).

Usage:
    pip install yfinance
    python fetch_data.py
    python run_f3.py backtest NQ.csv NQ
    python run_f3.py backtest GC.csv GC

Notes:
  * Yahoo serves only ~60 days of free 5-minute history — a short sample, enough
    for a first real-data look, not a final verdict. For longer/cleaner history
    use a paid data vendor or your broker's export and save it in the same CSV
    format.
  * This is free delayed data for research. It won't perfectly match your
    broker's prices, but it's plenty to see whether the strategy has an edge.
"""
from __future__ import annotations

import csv
import sys

TICKERS = {"NQ": "NQ=F", "GC": "GC=F"}   # Yahoo Finance futures symbols


def main() -> None:
    try:
        import yfinance as yf
    except ImportError:
        print("yfinance isn't installed. Run:  pip install yfinance")
        sys.exit(1)

    for market, symbol in TICKERS.items():
        print(f"Downloading {market} ({symbol}) — 5-minute candles, ~60 days...")
        try:
            df = yf.Ticker(symbol).history(period="60d", interval="5m")
        except Exception as exc:
            print(f"  Download failed for {symbol}: {exc}")
            continue
        if df is None or df.empty:
            print(f"  No data returned for {symbol} — skipping.")
            continue

        # Make sure timestamps are in New York time (the killzones are ET).
        idx = df.index
        try:
            idx = idx.tz_convert("America/New_York")
        except (TypeError, AttributeError):
            idx = idx.tz_localize("UTC").tz_convert("America/New_York")

        out = f"{market}.csv"
        rows = 0
        with open(out, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["time", "open", "high", "low", "close", "volume"])
            for ts, row in zip(idx, df.itertuples(index=False)):
                # skip any bar with missing OHLC
                if any(v != v for v in (row.Open, row.High, row.Low, row.Close)):
                    continue
                vol = int(row.Volume) if row.Volume == row.Volume else 0
                w.writerow([ts.isoformat(), round(row.Open, 2), round(row.High, 2),
                            round(row.Low, 2), round(row.Close, 2), vol])
                rows += 1
        print(f"  Wrote {out}  ({rows} candles)")

    print("\nNow run the real-data backtest:")
    print("  python run_f3.py backtest NQ.csv NQ")
    print("  python run_f3.py backtest GC.csv GC")


if __name__ == "__main__":
    main()
