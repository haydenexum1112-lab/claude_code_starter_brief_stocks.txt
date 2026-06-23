import logging

import pandas as pd

from trading_bot.config import ADX_PERIOD, ADX_THRESHOLD, ATR_PERIOD, BB_PERIOD, BB_STD
from trading_bot.indicators import adx, atr, bollinger_bands

logger = logging.getLogger(__name__)


def evaluate(df: pd.DataFrame, ticker: str) -> dict | None:
    """
    Bollinger Band mean reversion on 15-minute bars.

    Long when price touches the lower band, short when it touches the upper band.
    Suppressed when ADX > 25 (strong directional trend).

    Returns a signal dict or None.
    """
    min_bars = BB_PERIOD + ATR_PERIOD + 5
    if len(df) < min_bars:
        logger.warning(f"{ticker} MR: only {len(df)} bars available, need {min_bars} — skipping")
        return None

    close, high, low = df["close"], df["high"], df["low"]

    upper, _, lower = bollinger_bands(close, BB_PERIOD, BB_STD)
    adx_series = adx(high, low, close, ADX_PERIOD)
    atr_val = atr(high, low, close, ATR_PERIOD).iloc[-1]

    last_close = close.iloc[-1]
    last_adx = adx_series.iloc[-1]
    last_upper = upper.iloc[-1]
    last_lower = lower.iloc[-1]

    if last_adx > ADX_THRESHOLD:
        logger.info(
            f"{ticker} MR: ADX={last_adx:.1f} > {ADX_THRESHOLD} — "
            "strong trend detected, mean reversion suppressed"
        )
        return None

    if last_close <= last_lower:
        logger.info(
            f"{ticker} MR: close={last_close:.2f} <= lower_band={last_lower:.2f}, "
            f"ADX={last_adx:.1f} — LONG signal"
        )
        return {"ticker": ticker, "action": "buy", "entry_price": last_close, "atr": atr_val}

    if last_close >= last_upper:
        logger.info(
            f"{ticker} MR: close={last_close:.2f} >= upper_band={last_upper:.2f}, "
            f"ADX={last_adx:.1f} — SHORT signal"
        )
        return {"ticker": ticker, "action": "sell", "entry_price": last_close, "atr": atr_val}

    logger.debug(
        f"{ticker} MR: close={last_close:.2f} inside bands "
        f"[{last_lower:.2f}, {last_upper:.2f}], ADX={last_adx:.1f} — no signal"
    )
    return None
