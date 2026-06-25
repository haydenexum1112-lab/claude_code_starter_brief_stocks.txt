from __future__ import annotations
import logging

from trading_bot import data_fetcher, webhook
from trading_bot.config import (
    MEAN_REVERSION_TICKERS,
    TREND_FOLLOWING_TICKERS,
    VWAP_TICKERS,
)
from trading_bot.risk_manager import RiskManager
from trading_bot.strategies import mean_reversion, trend_following, vwap_reclaim
from trading_bot.tracker import daily_log

logger = logging.getLogger(__name__)

_risk = RiskManager()


def _process_signal(signal: dict, equity: float) -> None:
    payload, reason = _risk.evaluate(signal, equity)
    approved = payload is not None
    daily_log.record(
        ticker=signal["ticker"],
        action=signal["action"],
        entry_price=signal["entry_price"],
        quantity=payload["quantity"] if approved else 0,
        approved=approved,
        reason=reason,
    )
    if approved:
        webhook.send(payload)


def _process_vwap_signal(signal: dict, equity: float) -> None:
    payload, reason = _risk.evaluate_vwap(signal, equity)
    approved = payload is not None
    daily_log.record(
        ticker=signal["ticker"],
        action=signal["action"],
        entry_price=signal["entry_price"],
        quantity=payload["quantity"] if approved else 0,
        approved=approved,
        reason=reason,
    )
    if approved:
        webhook.send(payload)


def run_vwap_reclaim() -> None:
    """Live TJR VWAP reclaim across QQQ/SPY/IWM/DIA on 15-minute bar closes."""
    logger.info("=== VWAP Reclaim check (QQQ/SPY/IWM/DIA 15m) ===")
    try:
        equity = data_fetcher.get_account_equity()
        logger.info(f"Account equity: ${equity:,.2f}")
    except Exception as exc:
        logger.error(f"Could not fetch account equity: {exc}")
        return

    for ticker in VWAP_TICKERS:
        try:
            df = data_fetcher.get_15min_bars(ticker)
            signal = vwap_reclaim.evaluate(df, ticker)
            if signal is not None:
                _process_vwap_signal(signal, equity)
        except Exception as exc:
            logger.error(f"{ticker} VWAP error: {exc}", exc_info=True)


def flatten_all() -> None:
    """End-of-day flatten — exit every open position so we never hold overnight."""
    logger.info("=== End-of-day flatten (exit all VWAP positions) ===")
    for ticker in VWAP_TICKERS:
        try:
            webhook.send({"ticker": ticker, "action": "exit"})
        except Exception as exc:
            logger.error(f"{ticker} flatten error: {exc}", exc_info=True)


def run_mean_reversion() -> None:
    logger.info("=== Mean Reversion check (SPY/QQQ 15m) ===")
    try:
        equity = data_fetcher.get_account_equity()
        logger.info(f"Account equity: ${equity:,.2f}")
    except Exception as exc:
        logger.error(f"Could not fetch account equity: {exc}")
        return

    for ticker in MEAN_REVERSION_TICKERS:
        try:
            df = data_fetcher.get_15min_bars(ticker)
            signal = mean_reversion.evaluate(df, ticker)
            if signal is not None:
                _process_signal(signal, equity)
        except Exception as exc:
            logger.error(f"{ticker} MR error: {exc}", exc_info=True)


def run_trend_following() -> None:
    logger.info("=== Trend Following check (GLD/USO 4h) ===")
    try:
        equity = data_fetcher.get_account_equity()
        logger.info(f"Account equity: ${equity:,.2f}")
    except Exception as exc:
        logger.error(f"Could not fetch account equity: {exc}")
        return

    for ticker in TREND_FOLLOWING_TICKERS:
        try:
            df = data_fetcher.get_4hour_bars(ticker)
            signal = trend_following.evaluate(df, ticker)
            if signal is not None:
                _process_signal(signal, equity)
        except Exception as exc:
            logger.error(f"{ticker} TF error: {exc}", exc_info=True)
