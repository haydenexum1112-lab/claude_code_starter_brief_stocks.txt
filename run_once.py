"""
Stateless single-tick entry point for GitHub Actions.

GitHub's scheduler fires this script once every 15 minutes during market hours.
Each run is a brand-new process with NO memory of previous runs, so unlike
main.py (the long-running APScheduler version) we can't rely on in-memory state:

  * "one trade per ticker per day" is enforced by the broker instead
    (data_fetcher.has_traded_today), not by vwap_reclaim's in-memory dedup.
  * There's no daily_log to reset and no morning vwap_reclaim.reset() needed —
    the broker is the single source of truth for what already happened today.

The script looks at the current ET time and does exactly one of:
  * send the morning report   (~9:00 AM ET)
  * run the VWAP reclaim scan  (9:30 AM - 3:30 PM ET, on each 15m bar close)
  * flatten all positions      (3:45 PM ET and later, before the close)
  * send the evening report    (~4:30 PM ET)
  * nothing                    (outside those windows / weekends)
"""
from __future__ import annotations
import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from trading_bot.bot import run_vwap_reclaim, flatten_all
from trading_bot.reporter import send_morning_report, send_evening_report

ET = ZoneInfo("America/New_York")
log = logging.getLogger(__name__)


class _ETFormatter(logging.Formatter):
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        dt = datetime.fromtimestamp(record.created, tz=ET)
        return dt.strftime("%Y-%m-%d %H:%M:%S ET")


def setup_logging() -> None:
    # On GitHub Actions we only want stdout — the runner captures it as the job log.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_ETFormatter("%(asctime)s %(levelname)-8s %(name)s — %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def main() -> None:
    setup_logging()
    now = datetime.now(ET)
    minutes = now.hour * 60 + now.minute

    if now.weekday() >= 5:  # Saturday / Sunday
        log.info("Weekend — nothing to do.")
        return

    # Morning report window: 9:00-9:14 ET
    if 9 * 60 <= minutes < 9 * 60 + 15:
        log.info("Morning report window")
        send_morning_report()
        return

    # Evening report window: 4:30-4:44 PM ET
    if 16 * 60 + 30 <= minutes < 16 * 60 + 45:
        log.info("Evening report window")
        send_evening_report()
        return

    # Outside regular trading hours (before 9:30 or at/after 4:00) — do nothing.
    if minutes < 9 * 60 + 30 or minutes >= 16 * 60:
        log.info(f"Outside trading hours ({now:%H:%M} ET) — nothing to do.")
        return

    # End-of-day flatten: 3:45 PM ET onward — exit everything before the close.
    if minutes >= 15 * 60 + 45:
        log.info(f"Flatten window ({now:%H:%M} ET)")
        flatten_all()
        return

    # Regular session, 9:30 AM - 3:30 PM ET — scan for VWAP reclaims.
    log.info(f"VWAP reclaim scan ({now:%H:%M} ET)")
    run_vwap_reclaim()


if __name__ == "__main__":
    main()
