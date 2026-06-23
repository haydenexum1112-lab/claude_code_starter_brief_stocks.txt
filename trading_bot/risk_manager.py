import logging

from trading_bot.config import ACCOUNT_RISK_PCT, STOP_LOSS_PCT

logger = logging.getLogger(__name__)

# Correlated pairs subject to the concentration filter
_CORRELATED_PAIRS: dict[str, str] = {"SPY": "QQQ", "QQQ": "SPY"}


class RiskManager:
    """
    Gate that every signal must pass before it reaches the webhook.

    Rules applied in order:
    1. Correlation filter  — block if correlated partner is already in the same direction
    2. ATR validity check  — block if ATR is zero or negative
    3. Position sizing     — quantity = floor(equity * 1% / ATR), minimum 1
    4. Attach hard stop    — 1% stop loss on every approved trade
    """

    def __init__(self) -> None:
        self._active: dict[str, str] = {}  # ticker -> last approved direction

    def evaluate(self, signal: dict, account_equity: float) -> dict | None:
        ticker = signal["ticker"]
        action = signal["action"]
        atr_val = signal["atr"]

        partner = _CORRELATED_PAIRS.get(ticker)
        if partner and self._active.get(partner) == action:
            logger.info(
                f"BLOCKED {ticker} {action}: correlation filter — "
                f"{partner} is already {action}"
            )
            return None

        if atr_val <= 0:
            logger.warning(f"BLOCKED {ticker}: ATR={atr_val:.4f} is invalid, cannot size position")
            return None

        dollar_risk = account_equity * ACCOUNT_RISK_PCT
        quantity = max(1, int(dollar_risk / atr_val))

        self._active[ticker] = action

        approved = {
            "ticker": ticker,
            "action": action,
            "orderType": "market",
            "quantity": quantity,
            "stopLoss": {"type": "stop", "percent": STOP_LOSS_PCT},
        }
        logger.info(
            f"APPROVED {ticker} {action}: qty={quantity}, stop={STOP_LOSS_PCT}%, "
            f"ATR=${atr_val:.2f}, equity=${account_equity:,.0f}, "
            f"dollar_risk=${dollar_risk:.2f}"
        )
        return approved

    def clear_position(self, ticker: str) -> None:
        """Call when a position is closed to re-enable that ticker's correlation slot."""
        self._active.pop(ticker, None)
