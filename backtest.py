from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from trading_bot.config import (
    ALPACA_API_KEY,
    ALPACA_SECRET_KEY,
    ALPACA_DATA_FEED,
)
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.data.enums import DataFeed

ET = ZoneInfo("America/New_York")
logging.basicConfig(level=logging.WARNING)

_client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
_feed = DataFeed.IEX if ALPACA_DATA_FEED.lower() == "iex" else DataFeed.SIP

STARTING_EQUITY = 25_000.0
ACCOUNT_RISK_PCT = 0.005   # risk 0.5% of equity per trade
REWARD_RATIO = 2.0         # target = 2x the risk (2:1 R:R)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def _fetch(ticker: str, timeframe: TimeFrame, start: datetime, end: datetime) -> pd.DataFrame:
    req = StockBarsRequest(
        symbol_or_symbols=ticker,
        timeframe=timeframe,
        start=start,
        end=end,
        feed=_feed,
    )
    bars = _client.get_stock_bars(req)
    df = bars.df
    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(ticker, level=0)
    df.index = pd.to_datetime(df.index, utc=True)
    df.columns = [c.lower() for c in df.columns]
    return df


# ---------------------------------------------------------------------------
# ORB Signal generation
# ---------------------------------------------------------------------------

def _orb_signals(df: pd.DataFrame) -> pd.DataFrame:
    """
    First bar of each day (9:30 ET) defines the opening range.
    Breakout above range high = buy signal; below range low = sell signal.
    Stop = opposite side of range. Target = entry +/- REWARD_RATIO * risk.
    Only the first breakout per day is taken.
    """
    df = df.copy()
    df.index = df.index.tz_convert(ET)

    results = []

    for date, day in df.groupby(df.index.date):
        day = day.sort_index()
        if len(day) < 2:
            continue

        orb_bar = day.iloc[0]
        orb_high = orb_bar["high"]
        orb_low = orb_bar["low"]

        if orb_high <= orb_low:
            continue

        triggered = False
        for ts, bar in day.iloc[1:].iterrows():
            if triggered:
                break
            close = bar["close"]
            signal = None
            stop = None
            target = None

            if close > orb_high:
                signal = "buy"
                stop = orb_low
                risk = close - stop
                target = close + REWARD_RATIO * risk
            elif close < orb_low:
                signal = "sell"
                stop = orb_high
                risk = stop - close
                target = close - REWARD_RATIO * risk

            if signal:
                triggered = True
                results.append({
                    "ts": ts,
                    "signal": signal,
                    "orb_high": orb_high,
                    "orb_low": orb_low,
                    "stop": stop,
                    "target": target,
                })

    if not results:
        return pd.DataFrame()

    out = pd.DataFrame(results).set_index("ts")
    out.index = out.index.tz_convert("UTC")
    return out


# ---------------------------------------------------------------------------
# Trade simulation
# ---------------------------------------------------------------------------

def _simulate(ticker: str, signals: pd.DataFrame, prices: pd.DataFrame,
              equity: float) -> tuple[list[dict], float]:
    trades = []
    position = None

    for ts, row in signals.iterrows():
        if ts not in prices.index:
            continue
        price = prices.loc[ts, "close"]

        # Check open position for stop or target hit on entry bar
        if position:
            entry = position["entry"]
            stop = position["stop"]
            target = position["target"]
            high = prices.loc[ts, "high"]
            low = prices.loc[ts, "low"]

            hit_target = (position["action"] == "buy" and high >= target) or \
                         (position["action"] == "sell" and low <= target)
            hit_stop = (position["action"] == "buy" and low <= stop) or \
                       (position["action"] == "sell" and high >= stop)

            exit_price = None
            exit_reason = None
            if hit_target:
                exit_price = target
                exit_reason = "target"
            elif hit_stop:
                exit_price = stop
                exit_reason = "stop_loss"

            if exit_price is not None:
                if position["action"] == "buy":
                    pnl = (exit_price - entry) * position["qty"]
                else:
                    pnl = (entry - exit_price) * position["qty"]
                equity += pnl
                trades.append({
                    "ticker": ticker,
                    "action": position["action"],
                    "entry": entry,
                    "exit": exit_price,
                    "qty": position["qty"],
                    "pnl": pnl,
                    "exit_reason": exit_reason,
                    "entry_time": position["entry_time"],
                    "exit_time": ts,
                })
                position = None

        # Open new position on signal (one trade per day enforced by _orb_signals)
        if row["signal"] in ("buy", "sell") and not position:
            stop = row["stop"]
            target = row["target"]
            risk_per_share = abs(price - stop)
            if risk_per_share <= 0:
                continue
            dollar_risk = equity * ACCOUNT_RISK_PCT
            qty = max(1, int(dollar_risk / risk_per_share))
            position = {
                "action": row["signal"],
                "entry": price,
                "stop": stop,
                "target": target,
                "qty": qty,
                "entry_time": ts,
            }

    # Close open position at end of period
    if position:
        last_price = prices["close"].iloc[-1]
        last_ts = prices.index[-1]
        if position["action"] == "buy":
            pnl = (last_price - position["entry"]) * position["qty"]
        else:
            pnl = (position["entry"] - last_price) * position["qty"]
        equity += pnl
        trades.append({
            "ticker": ticker,
            "action": position["action"],
            "entry": position["entry"],
            "exit": last_price,
            "qty": position["qty"],
            "pnl": pnl,
            "exit_reason": "end_of_period",
            "entry_time": position["entry_time"],
            "exit_time": last_ts,
        })

    return trades, equity


# ---------------------------------------------------------------------------
# Results summary
# ---------------------------------------------------------------------------

def _summarise(all_trades: list[dict], starting_equity: float, final_equity: float) -> None:
    print("\n" + "=" * 60)
    print("BACKTEST RESULTS — ORB Strategy — Past 12 Months")
    print("=" * 60)

    if not all_trades:
        print("No trades were generated.")
        return

    df = pd.DataFrame(all_trades)

    total_pnl = final_equity - starting_equity
    total_pct = total_pnl / starting_equity * 100
    wins = df[df["pnl"] > 0]
    losses = df[df["pnl"] <= 0]
    win_rate = len(wins) / len(df) * 100

    avg_win = wins["pnl"].mean() if len(wins) else 0
    avg_loss = losses["pnl"].mean() if len(losses) else 0
    profit_factor = abs(wins["pnl"].sum() / losses["pnl"].sum()) if losses["pnl"].sum() != 0 else float("inf")

    equity_curve = [starting_equity]
    running = starting_equity
    for _, row in df.iterrows():
        running += row["pnl"]
        equity_curve.append(running)
    equity_arr = np.array(equity_curve)
    peak = np.maximum.accumulate(equity_arr)
    drawdown = (equity_arr - peak) / peak * 100
    max_dd = drawdown.min()

    print(f"\nStarting equity:  ${starting_equity:>10,.2f}")
    print(f"Final equity:     ${final_equity:>10,.2f}")
    print(f"Total P&L:        ${total_pnl:>+10,.2f}  ({total_pct:+.2f}%)")
    print(f"Max drawdown:     {max_dd:.2f}%")
    print(f"\nTotal trades:     {len(df)}")
    print(f"Winners:          {len(wins)}  ({win_rate:.1f}%)")
    print(f"Losers:           {len(losses)}  ({100-win_rate:.1f}%)")
    print(f"Avg win:          ${avg_win:>+,.2f}")
    print(f"Avg loss:         ${avg_loss:>+,.2f}")
    print(f"Profit factor:    {profit_factor:.2f}")

    print(f"\n{'─'*60}")
    print("BY EXIT REASON")
    print(f"{'─'*60}")
    for reason, grp in df.groupby("exit_reason"):
        r_wins = grp[grp["pnl"] > 0]
        wr = len(r_wins) / len(grp) * 100
        print(f"  {reason:<18}  trades={len(grp):>3}  win%={wr:>5.1f}  P&L=${grp['pnl'].sum():>+9,.2f}")

    print(f"\n{'─'*60}")
    print("TRADE LOG")
    print(f"{'─'*60}")
    for _, t in df.iterrows():
        entry_dt = t["entry_time"]
        entry_str = entry_dt.strftime("%Y-%m-%d") if hasattr(entry_dt, "strftime") else str(entry_dt)[:10]
        action = str(t["action"]).upper()
        result = "WIN " if t["pnl"] > 0 else "LOSS"
        print(
            f"  {t['ticker']:<5} {action:<5} {entry_str}  "
            f"entry=${t['entry']:>8.2f}  exit=${t['exit']:>8.2f}  "
            f"qty={t['qty']:>4}  P&L=${t['pnl']:>+8.2f}  [{result}] [{t['exit_reason']}]"
        )
    print("=" * 60)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_backtest() -> None:
    end = datetime.now(ET).replace(hour=16, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=365)

    print(f"Fetching data from {start.date()} to {end.date()}...")
    print(f"Strategy: Opening Range Breakout — 15min range, {REWARD_RATIO}:1 R:R")

    tf_15m = TimeFrame(15, TimeFrameUnit.Minute)

    all_trades: list[dict] = []
    equity = STARTING_EQUITY

    for ticker in ("QQQ",):
        print(f"  Backtesting {ticker}...")
        try:
            df = _fetch(ticker, tf_15m, start, end)
            signals = _orb_signals(df)
            if signals.empty:
                print(f"    No signals generated")
                continue
            trades, equity = _simulate(ticker, signals, df, equity)
            all_trades.extend(trades)
            print(f"    {len(trades)} trades generated")
        except Exception as e:
            print(f"    ERROR: {e}")

    _summarise(all_trades, STARTING_EQUITY, equity)


if __name__ == "__main__":
    run_backtest()
