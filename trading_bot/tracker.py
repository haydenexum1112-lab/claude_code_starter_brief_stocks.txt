from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


@dataclass
class SignalRecord:
    ticker: str
    action: str
    entry_price: float
    quantity: int
    time: datetime
    approved: bool
    reason: str  # "approved", "correlation_filter", "invalid_atr"


class DailyLog:
    """
    In-memory log of every signal evaluated during the trading day.
    Reset each morning before the first MR check.
    """

    def __init__(self) -> None:
        self._records: list[SignalRecord] = []

    def record(
        self,
        ticker: str,
        action: str,
        entry_price: float,
        quantity: int,
        approved: bool,
        reason: str,
    ) -> None:
        self._records.append(
            SignalRecord(
                ticker=ticker,
                action=action,
                entry_price=entry_price,
                quantity=quantity,
                time=datetime.now(ET),
                approved=approved,
                reason=reason,
            )
        )

    def reset(self) -> None:
        self._records.clear()

    @property
    def approved(self) -> list[SignalRecord]:
        return [r for r in self._records if r.approved]

    @property
    def rejected(self) -> list[SignalRecord]:
        return [r for r in self._records if not r.approved]

    @property
    def all(self) -> list[SignalRecord]:
        return list(self._records)


# Module-level singleton shared across bot.py, reporter.py
daily_log = DailyLog()
