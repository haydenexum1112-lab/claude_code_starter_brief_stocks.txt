from __future__ import annotations
import logging

import pandas as pd

from trading_bot.config import ATR_PERIOD, MA_FAST, MA_SLOW
from trading_bot.indicators import atr, sma

logger = logging.getLogger(__name__)


def evaluate(df: pd.DataFrame, ticker: str) -> dict | None:
    """
    50/200 SMA crossover on 4-hour bars.

    Fires only on bar closes where a crossover just occurred — not on every bar.
    Long on golden cross (50 crosses above 200), short on death cross.

    Returns a signal dict or None.
    """
    min_bars = MA_SLOW + 2
    if len(df) < min_bars:
        logger.warning(f"{ticker} TF: only {len(df)} bars available, need {min_bars} — skipping")
        return None

    close, high, low = df["close"], df["high"], df["low"]

    fast = sma(close, MA_FAST)
    slow = sma(close, MA_SLOW)
    atr_val = atr(high, low, close, ATR_PERIOD).iloc[-1]

    prev_fast, curr_fast = fast.iloc[-2], fast.iloc[-1]
    prev_slow, curr_slow = slow.iloc[-2], slow.iloc[-1]
    last_close = close.iloc[-1]

    golden_cross = prev_fast < prev_slow and curr_fast >= curr_slow
    death_cross = prev_fast > prev_slow and curr_fast <= curr_slow

    if golden_cross:
        logger.info(
            f"{ticker} TF: 50MA={curr_fast:.2f} crossed ABOVE 200MA={curr_slow:.2f} "
            f"at close={last_close:.2f} — LONG signal"
        )
        return {"ticker": ticker, "action": "buy", "entry_price": last_close, "atr": atr_val}

    if death_cross:
        logger.info(
            f"{ticker} TF: 50MA={curr_fast:.2f} crossed BELOW 200MA={curr_slow:.2f} "
            f"at close={last_close:.2f} — SHORT signal"
        )
        return {"ticker": ticker, "action": "sell", "entry_price": last_close, "atr": atr_val}

    logger.debug(
        f"{ticker} TF: 50MA={curr_fast:.2f}, 200MA={curr_slow:.2f} — no crossover"
    )
    return None
