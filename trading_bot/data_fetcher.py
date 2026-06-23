import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.data.enums import DataFeed
from alpaca.trading.client import TradingClient

from trading_bot.config import (
    ALPACA_API_KEY,
    ALPACA_SECRET_KEY,
    ALPACA_DATA_FEED,
    IS_PAPER,
)

_data_client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
_trading_client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=IS_PAPER)

TIMEFRAME_15MIN = TimeFrame(15, TimeFrameUnit.Minute)
TIMEFRAME_4HOUR = TimeFrame(4, TimeFrameUnit.Hour)

_feed = DataFeed.IEX if ALPACA_DATA_FEED.lower() == "iex" else DataFeed.SIP


def get_bars(ticker: str, timeframe: TimeFrame, limit: int = 250) -> pd.DataFrame:
    request = StockBarsRequest(
        symbol_or_symbols=ticker,
        timeframe=timeframe,
        limit=limit,
        feed=_feed,
    )
    bars = _data_client.get_stock_bars(request)
    df = bars.df
    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(ticker, level=0)
    df.index = pd.to_datetime(df.index, utc=True)
    df.columns = [c.lower() for c in df.columns]
    return df


def get_15min_bars(ticker: str) -> pd.DataFrame:
    return get_bars(ticker, TIMEFRAME_15MIN, limit=250)


def get_4hour_bars(ticker: str) -> pd.DataFrame:
    return get_bars(ticker, TIMEFRAME_4HOUR, limit=250)


def get_account_equity() -> float:
    account = _trading_client.get_account()
    return float(account.equity)
