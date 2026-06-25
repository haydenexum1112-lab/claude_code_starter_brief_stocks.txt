from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from trading_bot.config import (
    ADX_PERIOD,
    ADX_THRESHOLD,
    ALPACA_API_KEY,
    ALPACA_SECRET_KEY,
    ALPACA_DATA_FEED,
    ATR_PERIOD,
    BB_PERIOD,
    BB_STD,
    MA_FAST,
    MA_SLOW,
    STOP_LOSS_PCT,
    ACCOUNT_RISK_PCT,
)
from trading_bot.indicators import adx, atr, bollinger_bands, sma, true_range
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.data.enums import DataFeed

ET = ZoneInfo("America/New_York")
logging.basicConfig(level=logging.WARNING)

_client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
_feed = DataFeed.IEX if ALPACA_DATA_FEED.lower() == "iex" else DataFeed.SIP

STARTING_EQUITY = 25_000.0
STOP_LOSS_FRAC = STOP_LOSS_PCT / 100  # matches config


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
# Signal generation (vectorised — returns a Series of "buy"/"sell"/None)
# ---------------------------------------------------------------------------

def _mr_signals(df: pd.DataFrame) -> pd.Series:
    close, high, low = df["close"], df["high"], df["low"]
    upper, _, lower = bollinger_bands(close, BB_PERIOD, BB_STD)
    adx_s = adx(high, low, close, ADX_PERIOD)
    vol_avg = df["volume"].rolling(20).mean()
    high_volume = df["volume"] > vol_avg  # volume confirmation

    signals = pd.Series(index=df.index, dtype=object)
    signals[close <= lower] = "buy"
    signals[close >= upper] = "sell"
    signals[adx_s > ADX_THRESHOLD] = None  # suppress in strong trends
    signals[~high_volume] = None           # require above-average volume
    return signals


def _tf_signals(df: pd.DataFrame) -> pd.Series:
    close = df["close"]
    fast = sma(close, MA_FAST)
    slow = sma(close, MA_SLOW)

    bull = (fast.shift(1) < slow.shift(1)) & (fast >= slow)
    bear = (fast.shift(1) > slow.shift(1)) & (fast <= slow)

    signals = pd.Series(index=df.index, dtype=object)
    signals[bull] = "buy"
    signals[bear] = "sell"
    return signals


# ---------------------------------------------------------------------------
# Trade simulation
# ---------------------------------------------------------------------------

def _simulate(ticker: str, signals: pd.Series, prices: pd.DataFrame,
               equity: float, active_positions: dict) -> tuple[list[dict], float]:
    """
    Walk forward through signals, open/close positions with 1% stop loss.
    Returns list of closed trade dicts and updated equity.
    """
    trades = []
    position = None  # {"action": "buy"/"sell", "entry": float, "qty": int, "entry_time": dt}

    atr_s = atr(prices["high"], prices["low"], prices["close"], ATR_PERIOD)

    for ts, signal in signals.items():
        price = prices.loc[ts, "close"]
        atr_val = atr_s.loc[ts]

        # Check stop loss and profit target on open position
        if position:
            entry = position["entry"]
            if position["action"] == "buy":
                stop_price = entry * (1 - STOP_LOSS_FRAC)
                target_price = entry * (1 + STOP_LOSS_FRAC * 1.5)  # 1.5:1 reward:risk
                hit_stop = price <= stop_price
                hit_target = price >= target_price
            else:
                stop_price = entry * (1 + STOP_LOSS_FRAC)
                target_price = entry * (1 - STOP_LOSS_FRAC * 1.5)  # 1.5:1 reward:risk
                hit_stop = price >= stop_price
                hit_target = price <= target_price

            exit_price = None
            exit_reason = None
            if hit_target:
                exit_price = target_price
                exit_reason = "take_profit"
            elif hit_stop:
                exit_price = stop_price
                exit_reason = "stop_loss"

            if exit_price is not None:
                if position["action"] == "buy":
                    pnl_per_share = exit_price - entry
                else:
                    pnl_per_share = entry - exit_price
                pnl = pnl_per_share * position["qty"]
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
                active_positions.pop(ticker, None)
                position = None

        # Open new position on signal
        if signal in ("buy", "sell") and not position:
            if not atr_val or atr_val <= 0 or np.isnan(atr_val):
                continue

            dollar_risk = equity * ACCOUNT_RISK_PCT
            qty = max(1, int(dollar_risk / atr_val))
            position = {
                "action": signal,
                "entry": price,
                "qty": qty,
                "entry_time": ts,
            }
            active_positions[ticker] = signal

        # Close on opposite signal
        elif signal in ("buy", "sell") and position and signal != position["action"]:
            if position["action"] == "buy":
                pnl_per_share = price - position["entry"]
            else:
                pnl_per_share = position["entry"] - price
            pnl = pnl_per_share * position["qty"]
            equity += pnl
            trades.append({
                "ticker": ticker,
                "action": position["action"],
                "entry": position["entry"],
                "exit": price,
                "qty": position["qty"],
                "pnl": pnl,
                "exit_reason": "signal_flip",
                "entry_time": position["entry_time"],
                "exit_time": ts,
            })
            active_positions.pop(ticker, None)
            position = None

    # Close any open position at end of period
    if position:
        last_price = prices["close"].iloc[-1]
        last_ts = prices.index[-1]
        if position["action"] == "buy":
            pnl_per_share = last_price - position["entry"]
        else:
            pnl_per_share = position["entry"] - last_price
        pnl = pnl_per_share * position["qty"]
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
        active_positions.pop(ticker, None)

    return trades, equity


# ---------------------------------------------------------------------------
# Results summary
# ---------------------------------------------------------------------------

def _summarise(all_trades: list[dict], starting_equity: float, final_equity: float) -> None:
    print("\n" + "=" * 60)
    print("BACKTEST RESULTS — Past 12 Months")
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

    # Max drawdown
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
    print("BY TICKER")
    print(f"{'─'*60}")
    for ticker, grp in df.groupby("ticker"):
        t_wins = grp[grp["pnl"] > 0]
        wr = len(t_wins) / len(grp) * 100
        print(f"  {ticker:<5}  trades={len(grp):>3}  win%={wr:>5.1f}  P&L=${grp['pnl'].sum():>+9,.2f}")

    print(f"\n{'─'*60}")
    print("TRADE LOG")
    print(f"{'─'*60}")
    for _, t in df.iterrows():
        entry_dt = t["entry_time"]
        if hasattr(entry_dt, "strftime"):
            entry_str = entry_dt.strftime("%Y-%m-%d")
        else:
            entry_str = str(entry_dt)[:10]
        action = str(t["action"]) if t["action"] else "?"
        result = "WIN " if t["pnl"] > 0 else "LOSS"
        print(
            f"  {t['ticker']:<5} {action.upper():<5} {entry_str}  "
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

    tf_15m = TimeFrame(15, TimeFrameUnit.Minute)
    tf_4h = TimeFrame(4, TimeFrameUnit.Hour)

    all_trades: list[dict] = []
    equity = STARTING_EQUITY
    active_positions: dict[str, str] = {}

    # Mean Reversion — QQQ on 15m bars (SPY dropped: consistently unprofitable in backtest)
    for ticker in ("QQQ",):
        print(f"  Backtesting {ticker} (Mean Reversion 15m)...")
        try:
            df = _fetch(ticker, tf_15m, start, end)
            signals = _mr_signals(df)
            trades, equity = _simulate(ticker, signals, df, equity, active_positions)
            all_trades.extend(trades)
            print(f"    {len(trades)} trades generated")
        except Exception as e:
            print(f"    ERROR: {e}")

    # Trend Following — GLD, USO on 4h bars
    for ticker in ("GLD", "USO"):
        print(f"  Backtesting {ticker} (Trend Following 4h)...")
        try:
            df = _fetch(ticker, tf_4h, start, end)
            signals = _tf_signals(df)
            trades, equity = _simulate(ticker, signals, df, equity, active_positions)
            all_trades.extend(trades)
            print(f"    {len(trades)} trades generated")
        except Exception as e:
            print(f"    ERROR: {e}")

    _summarise(all_trades, STARTING_EQUITY, equity)


if __name__ == "__main__":
    run_backtest()
