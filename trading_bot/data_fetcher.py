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
TIMEFRAME_1DAY = TimeFrame(1, TimeFrameUnit.Day)

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


def get_daily_bars(ticker: str, limit: int = 210) -> pd.DataFrame:
    return get_bars(ticker, TIMEFRAME_1DAY, limit=limit)


def get_account_equity() -> float:
    account = _trading_client.get_account()
    return float(account.equity)


def get_account_info() -> dict:
    """Returns equity, last_equity, and today's P&L from GET /v2/account."""
    account = _trading_client.get_account()
    equity = float(account.equity)
    last_equity = float(account.last_equity)
    pnl = equity - last_equity
    pnl_pct = (pnl / last_equity * 100) if last_equity else 0.0
    return {
        "equity": equity,
        "last_equity": last_equity,
        "daily_pnl": pnl,
        "daily_pnl_pct": pnl_pct,
    }


def get_positions() -> list[dict]:
    """Returns open positions from GET /v2/positions."""
    positions = _trading_client.get_all_positions()
    return [
        {
            "ticker": p.symbol,
            "side": p.side.value,
            "qty": float(p.qty),
            "avg_entry": float(p.avg_entry_price),
            "current_price": float(p.current_price),
            "unrealized_pl": float(p.unrealized_pl),
            "unrealized_plpc": float(p.unrealized_plpc) * 100,
        }
        for p in positions
    ]
