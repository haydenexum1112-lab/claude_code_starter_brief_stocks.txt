import os
from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise EnvironmentError(
            f"Required env var {key!r} is not set. Check your .env file."
        )
    return val


ALPACA_API_KEY: str = _require("ALPACA_API_KEY")
ALPACA_SECRET_KEY: str = _require("ALPACA_SECRET_KEY")
ALPACA_BASE_URL: str = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
TRADERSPOST_WEBHOOK_URL: str = _require("TRADERSPOST_STOCKS_WEBHOOK_URL")
ALPACA_DATA_FEED: str = os.getenv("ALPACA_DATA_FEED", "iex")

IS_PAPER: bool = "paper" in ALPACA_BASE_URL.lower()

MEAN_REVERSION_TICKERS: list[str] = ["SPY", "QQQ"]
TREND_FOLLOWING_TICKERS: list[str] = ["GLD", "USO"]

# Indicators
BB_PERIOD: int = 20
BB_STD: float = 2.0
ADX_PERIOD: int = 14
ADX_THRESHOLD: float = 25.0
MA_FAST: int = 50
MA_SLOW: int = 200
ATR_PERIOD: int = 14

# Risk
STOP_LOSS_PCT: float = 1.0      # percent passed to TradersPost (1 = 1%)
ACCOUNT_RISK_PCT: float = 0.01  # fraction of equity risked per trade

# Reporting
GMAIL_ADDRESS: str = _require("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD: str = _require("GMAIL_APP_PASSWORD")
MY_EMAIL: str = _require("MY_EMAIL")
