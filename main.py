from __future__ import annotations
import logging
import logging.handlers
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from trading_bot.bot import run_vwap_reclaim, flatten_all
from trading_bot.reporter import send_morning_report, send_evening_report
from trading_bot.strategies import vwap_reclaim
from trading_bot.tracker import daily_log

ET = ZoneInfo("America/New_York")
log = logging.getLogger(__name__)


class _ETFormatter(logging.Formatter):
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        dt = datetime.fromtimestamp(record.created, tz=ET)
        return dt.strftime("%Y-%m-%d %H:%M:%S ET")


def setup_logging() -> None:
    fmt = "%(asctime)s %(levelname)-8s %(name)s — %(message)s"
    formatter = _ETFormatter(fmt)
    handlers: list[logging.Handler] = [
        logging.StreamHandler(),
        logging.handlers.RotatingFileHandler(
            "trading_bot.log", maxBytes=10_000_000, backupCount=5, encoding="utf-8"
        ),
    ]
    for h in handlers:
        h.setFormatter(formatter)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in handlers:
        root.addHandler(h)


def is_market_open() -> bool:
    now = datetime.now(ET)
    if now.weekday() >= 5:  # Saturday or Sunday
        return False
    minutes = now.hour * 60 + now.minute
    return 9 * 60 + 30 <= minutes < 16 * 60


def vwap_job() -> None:
    if not is_market_open():
        log.debug("Market closed — skipping VWAP reclaim check")
        return
    run_vwap_reclaim()


def flatten_job() -> None:
    # Go flat before the close so we never hold overnight (matches the backtest).
    log.info("End-of-day flatten")
    flatten_all()


def morning_report_job() -> None:
    daily_log.reset()       # fresh slate for the new trading day
    vwap_reclaim.reset()    # allow one fresh signal per ticker today
    log.info("Sending morning report")
    send_morning_report()


def evening_report_job() -> None:
    log.info("Sending evening report")
    send_evening_report()


if __name__ == "__main__":
    setup_logging()
    log.info("Trading bot starting")

    scheduler = BlockingScheduler(timezone=ET)

    # VWAP Reclaim: fire at the close of every 15-minute bar during market hours
    scheduler.add_job(
        vwap_job,
        CronTrigger(
            minute="0,15,30,45",
            hour="9-15",
            day_of_week="mon-fri",
            timezone=ET,
        ),
        id="vwap_reclaim",
        name="VWAP Reclaim QQQ/SPY/IWM/DIA (15m)",
        misfire_grace_time=60,
    )

    # End-of-day flatten: 3:55 PM ET — exit all positions before the close
    scheduler.add_job(
        flatten_job,
        CronTrigger(hour="15", minute="55", day_of_week="mon-fri", timezone=ET),
        id="eod_flatten",
        name="End-of-Day Flatten (3:55 PM ET)",
        misfire_grace_time=120,
    )

    # Morning report: 9:00 AM ET — resets daily log, sends market overview
    scheduler.add_job(
        morning_report_job,
        CronTrigger(hour="9", minute="0", day_of_week="mon-fri", timezone=ET),
        id="morning_report",
        name="Morning Report (9:00 AM ET)",
        misfire_grace_time=300,
    )

    # Evening report: 4:30 PM ET — daily P&L, trades, open positions
    scheduler.add_job(
        evening_report_job,
        CronTrigger(hour="16", minute="30", day_of_week="mon-fri", timezone=ET),
        id="evening_report",
        name="Evening Report (4:30 PM ET)",
        misfire_grace_time=300,
    )

    log.info(
        "Scheduler running — "
        "VWAP Reclaim: every 15m (9:00–16:00 ET weekdays) | "
        "EOD flatten: 3:55 PM ET | "
        "Reports: 9:00 AM and 4:30 PM ET"
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Trading bot stopped")
