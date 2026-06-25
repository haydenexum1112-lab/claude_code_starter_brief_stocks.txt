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

STARTING_EQUITY = 100_000.0
ACCOUNT_RISK_PCT = 0.005   # risk 0.5% of equity per trade
MAX_RISK_DOLLARS = 500.0   # hard cap: never risk more than $500 per trade
MAX_SHARES = 200           # hard cap on position size
REWARD_RATIO = 2.0         # target = 2x the risk (2:1 R:R)
LOOKBACK_DAYS = 182        # ~6 months
MIN_ORB_RANGE = 0.0        # filter disabled — it hurt results (curve-fit, not edge)
GAP_DIRECTION_FILTER = False  # filter disabled — too restrictive, no real edge

STRATEGY = "vwap"          # "vwap" = TJR VWAP reclaim, "orb" = opening range breakout
VWAP_MIN_BARS_OTHER_SIDE = 1  # price must spend >=1 bar on the other side before a reclaim counts


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
    prev_close: float | None = None

    for date, day in df.groupby(df.index.date):
        day = day.sort_index()
        if len(day) < 2:
            prev_close = day["close"].iloc[-1] if len(day) else prev_close
            continue

        orb_bar = day.iloc[0]
        orb_high = orb_bar["high"]
        orb_low = orb_bar["low"]
        orb_open = orb_bar["open"]

        if orb_high <= orb_low:
            prev_close = day["close"].iloc[-1]
            continue

        # Skip low-range days
        if (orb_high - orb_low) < MIN_ORB_RANGE:
            prev_close = day["close"].iloc[-1]
            continue

        # Determine gap direction (today's open vs yesterday's close)
        if GAP_DIRECTION_FILTER and prev_close is not None:
            gap_up = orb_open > prev_close
            gap_down = orb_open < prev_close
        else:
            gap_up = gap_down = True  # no filter if we can't compute

        triggered = False
        for ts, bar in day.iloc[1:].iterrows():
            if triggered:
                break
            close = bar["close"]
            signal = None
            stop = None
            target = None

            if close > orb_high and (not GAP_DIRECTION_FILTER or gap_up):
                signal = "buy"
                stop = orb_low
                risk = close - stop
                target = close + REWARD_RATIO * risk
            elif close < orb_low and (not GAP_DIRECTION_FILTER or gap_down):
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

        prev_close = day["close"].iloc[-1]

    if not results:
        return pd.DataFrame()

    out = pd.DataFrame(results).set_index("ts")
    out.index = out.index.tz_convert("UTC")
    return out


# ---------------------------------------------------------------------------
# TJR VWAP Reclaim Signal generation
# ---------------------------------------------------------------------------

def _vwap_reclaim_signals(df: pd.DataFrame) -> pd.DataFrame:
    """
    Intraday VWAP resets each day.
    Bullish reclaim: price was trading BELOW VWAP, then a candle closes back
      ABOVE VWAP with momentum (green candle). Enter long, trend continuation.
    Bearish loss:    price was trading ABOVE VWAP, then a candle closes back
      BELOW VWAP with momentum (red candle). Enter short.
    Stop = the pullback low/high (last 2 bars). Target = entry +/- 2 * risk.
    First valid reclaim per day only (one trade/day).
    """
    df = df.copy()
    df.index = df.index.tz_convert(ET)

    results = []

    for date, day in df.groupby(df.index.date):
        day = day.sort_index()
        if len(day) < 4:
            continue

        # Intraday VWAP (resets each day)
        typical = (day["high"] + day["low"] + day["close"]) / 3.0
        cum_vol = day["volume"].cumsum()
        cum_pv = (typical * day["volume"]).cumsum()
        vwap = cum_pv / cum_vol.replace(0, np.nan)

        bars = list(day.iterrows())
        below_count = 0
        above_count = 0
        triggered = False

        for i in range(1, len(bars)):
            if triggered:
                break
            ts, bar = bars[i]
            _, prev_bar = bars[i - 1]
            v = vwap.iloc[i]
            pv = vwap.iloc[i - 1]
            if pd.isna(v) or pd.isna(pv):
                continue

            close = bar["close"]
            pclose = prev_bar["close"]

            # Track how long price has held one side of VWAP
            if pclose < pv:
                below_count += 1
                above_count = 0
            elif pclose > pv:
                above_count += 1
                below_count = 0

            signal = stop = target = None

            # Bullish reclaim: came from below, closes above VWAP, green candle
            if (pclose < pv and close > v and close > bar["open"]
                    and below_count >= VWAP_MIN_BARS_OTHER_SIDE):
                stop = min(bar["low"], prev_bar["low"])
                risk = close - stop
                if risk <= 0:
                    continue
                signal = "buy"
                target = close + REWARD_RATIO * risk

            # Bearish loss: came from above, closes below VWAP, red candle
            elif (pclose > pv and close < v and close < bar["open"]
                    and above_count >= VWAP_MIN_BARS_OTHER_SIDE):
                stop = max(bar["high"], prev_bar["high"])
                risk = stop - close
                if risk <= 0:
                    continue
                signal = "sell"
                target = close - REWARD_RATIO * risk

            if signal:
                triggered = True
                results.append({
                    "ts": ts,
                    "signal": signal,
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

def _close_position(position: dict, exit_price: float, exit_ts, exit_reason: str,
                    ticker: str, equity: float) -> tuple[dict, float]:
    entry = position["entry"]
    qty = position["qty"]
    if position["action"] == "buy":
        pnl = (exit_price - entry) * qty
    else:
        pnl = (entry - exit_price) * qty
    equity += pnl
    trade = {
        "ticker": ticker,
        "action": position["action"],
        "entry": entry,
        "exit": exit_price,
        "qty": qty,
        "pnl": pnl,
        "exit_reason": exit_reason,
        "entry_time": position["entry_time"],
        "exit_time": exit_ts,
    }
    return trade, equity


def _simulate(ticker: str, signals: pd.DataFrame, prices: pd.DataFrame,
              equity: float) -> tuple[list[dict], float]:
    trades = []
    prices_et = prices.copy()
    prices_et.index = prices_et.index.tz_convert(ET)

    for signal_ts, row in signals.iterrows():
        signal_ts_et = signal_ts.tz_convert(ET)
        if signal_ts not in prices.index:
            continue

        stop = row["stop"]
        target = row["target"]
        entry_price = prices.loc[signal_ts, "close"]
        risk_per_share = abs(entry_price - stop)
        if risk_per_share <= 0:
            continue
        dollar_risk = min(equity * ACCOUNT_RISK_PCT, MAX_RISK_DOLLARS)
        qty = max(1, min(int(dollar_risk / risk_per_share), MAX_SHARES))

        position = {
            "action": row["signal"],
            "entry": entry_price,
            "stop": stop,
            "target": target,
            "qty": qty,
            "entry_time": signal_ts,
        }

        # Scan bars after entry until end of that trading day
        trade_date = signal_ts_et.date()
        day_bars = prices_et[prices_et.index.date == trade_date]
        after_entry = day_bars[day_bars.index > signal_ts_et]

        closed = False
        for bar_ts_et, bar in after_entry.iterrows():
            bar_ts_utc = bar_ts_et.tz_convert("UTC")
            hit_target = (position["action"] == "buy" and bar["high"] >= target) or \
                         (position["action"] == "sell" and bar["low"] <= target)
            hit_stop = (position["action"] == "buy" and bar["low"] <= stop) or \
                       (position["action"] == "sell" and bar["high"] >= stop)

            if hit_target:
                trade, equity = _close_position(position, target, bar_ts_utc, "target", ticker, equity)
                trades.append(trade)
                closed = True
                break
            elif hit_stop:
                trade, equity = _close_position(position, stop, bar_ts_utc, "stop_loss", ticker, equity)
                trades.append(trade)
                closed = True
                break

        if not closed:
            # Close at end of day (last bar's close price)
            if len(after_entry):
                last_bar = after_entry.iloc[-1]
                last_ts_utc = after_entry.index[-1].tz_convert("UTC")
                trade, equity = _close_position(position, last_bar["close"], last_ts_utc, "end_of_day", ticker, equity)
            else:
                # Signal was the last bar of the day — close at entry bar close
                trade, equity = _close_position(position, entry_price, signal_ts, "end_of_day", ticker, equity)
            trades.append(trade)

    return trades, equity


# ---------------------------------------------------------------------------
# Results summary
# ---------------------------------------------------------------------------

def _summarise(all_trades: list[dict], starting_equity: float, final_equity: float) -> None:
    strat_name = "TJR VWAP Reclaim" if STRATEGY == "vwap" else "ORB"
    print("\n" + "=" * 60)
    print(f"BACKTEST RESULTS — {strat_name} Strategy — Past {LOOKBACK_DAYS} Days")
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
    start = end - timedelta(days=LOOKBACK_DAYS)

    print(f"Fetching data from {start.date()} to {end.date()} ({LOOKBACK_DAYS} days)...")
    print(f"Starting equity: ${STARTING_EQUITY:,.0f}")
    if STRATEGY == "vwap":
        print(f"Strategy: TJR VWAP Reclaim — 15min bars, {REWARD_RATIO}:1 R:R, end-of-day close")
    else:
        print(f"Strategy: Opening Range Breakout — 15min range, {REWARD_RATIO}:1 R:R, min range ${MIN_ORB_RANGE:.2f}, gap filter={'on' if GAP_DIRECTION_FILTER else 'off'}")

    tf_15m = TimeFrame(15, TimeFrameUnit.Minute)

    all_trades: list[dict] = []
    equity = STARTING_EQUITY

    for ticker in ("QQQ",):
        print(f"  Backtesting {ticker}...")
        try:
            df = _fetch(ticker, tf_15m, start, end)
            signals = _vwap_reclaim_signals(df) if STRATEGY == "vwap" else _orb_signals(df)
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
