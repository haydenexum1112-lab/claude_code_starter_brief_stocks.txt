from __future__ import annotations
import logging
from datetime import date
from zoneinfo import ZoneInfo

import pandas as pd

from trading_bot.config import REWARD_RATIO
from trading_bot.indicators import vwap as vwap_indicator

ET = ZoneInfo("America/New_York")
logger = logging.getLogger(__name__)

# One trade per ticker per day — matches the backtest's "first reclaim only".
_last_signal_day: dict[str, date] = {}


def reset() -> None:
    """Clear the per-day signal memory. Call at the start of each trading day."""
    _last_signal_day.clear()


def evaluate(df: pd.DataFrame, ticker: str) -> dict | None:
    """
    TJR VWAP reclaim on 15-minute bars (mirrors backtest._vwap_reclaim_signals).

    Bullish reclaim: price was trading BELOW the intraday VWAP, then the latest
      closed bar closes back ABOVE it with momentum (green candle) -> buy.
    Bearish loss:    price was trading ABOVE the intraday VWAP, then the latest
      closed bar closes back BELOW it with momentum (red candle) -> sell.

    Stop = pullback low/high (last two bars). Target = entry +/- REWARD_RATIO * risk.
    Only the first valid reclaim per ticker per day is taken.

    Returns a signal dict (with entry/stop/target prices) or None.
    """
    if len(df) < 4:
        return None

    df = df.copy()
    df.index = df.index.tz_convert(ET)

    today = df.index[-1].date()
    if _last_signal_day.get(ticker) == today:
        return None  # already fired for this ticker today

    # The two most recent CLOSED bars must both be from today's session, so the
    # VWAP comparison is within a single intraday VWAP (resets each morning).
    if df.index[-2].date() != today:
        return None

    vwap_series = vwap_indicator(df["high"], df["low"], df["close"], df["volume"])
    v = vwap_series.iloc[-1]
    pv = vwap_series.iloc[-2]
    if pd.isna(v) or pd.isna(pv):
        return None

    bar = df.iloc[-1]
    prev_bar = df.iloc[-2]
    close = float(bar["close"])
    pclose = float(prev_bar["close"])

    signal: dict | None = None

    # Bullish reclaim: came from below, closes above VWAP, green candle
    if pclose < pv and close > v and close > bar["open"]:
        stop = float(min(bar["low"], prev_bar["low"]))
        risk = close - stop
        if risk <= 0:
            return None
        signal = {
            "ticker": ticker,
            "action": "buy",
            "entry_price": close,
            "stop_price": stop,
            "target_price": close + REWARD_RATIO * risk,
            "risk_per_share": risk,
        }

    # Bearish loss: came from above, closes below VWAP, red candle
    elif pclose > pv and close < v and close < bar["open"]:
        stop = float(max(bar["high"], prev_bar["high"]))
        risk = stop - close
        if risk <= 0:
            return None
        signal = {
            "ticker": ticker,
            "action": "sell",
            "entry_price": close,
            "stop_price": stop,
            "target_price": close - REWARD_RATIO * risk,
            "risk_per_share": risk,
        }

    if signal is not None:
        _last_signal_day[ticker] = today
        logger.info(
            f"{ticker} VWAP reclaim: {signal['action'].upper()} @ {close:.2f} "
            f"(VWAP={v:.2f}, stop={signal['stop_price']:.2f}, "
            f"target={signal['target_price']:.2f})"
        )
        return signal

    logger.debug(
        f"{ticker} VWAP: close={close:.2f} vs VWAP={v:.2f} "
        f"(prev close={pclose:.2f} vs prev VWAP={pv:.2f}) — no reclaim"
    )
    return None
