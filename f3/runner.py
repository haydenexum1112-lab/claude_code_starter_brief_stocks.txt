"""
F3 single-tick runner — stateless, cloud-friendly.

A cloud scheduler (GitHub Actions) fires this once on a schedule. Each run looks
at the current ET time and does exactly one of:

  * send the morning report   (~07:30 ET)
  * scan the markets & auto-fire trades   (inside a killzone)
  * send the evening report   (~16:30 ET)
  * nothing                   (outside those windows / weekends)

Because trades fire to the broker webhook automatically, you never place a trade
by hand — the system makes them all — and because it runs in the cloud, it keeps
working with your computer off.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime

from . import alerts
from .config import ET, F3Config
from .engine import F3Engine
from .market_data import load_csv, synthetic_series
from .models import Candle, F3Decision
from .state import DailyState

log = logging.getLogger(__name__)


def _account_balance() -> float:
    try:
        return float(os.getenv("F3_ACCOUNT_BALANCE", "50000"))
    except ValueError:
        return 50_000.0


def _news_today() -> list[str]:
    raw = os.getenv("F3_NEWS_TODAY", "").strip()
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


def get_candles(market: str) -> list[Candle]:
    """
    Load candles for a market. Plug your live futures feed in here.

    Resolution order:
      1. CSV at $F3_DATA_DIR/<market>.csv (time,open,high,low,close,volume)
      2. a deterministic synthetic series (safe default for dry-runs)
    """
    data_dir = os.getenv("F3_DATA_DIR")
    if data_dir:
        path = os.path.join(data_dir, f"{market}.csv")
        if os.path.exists(path):
            return load_csv(path)
    seed = abs(hash(market)) % 1000
    return synthetic_series(count=200, seed=seed, end=datetime.now(ET))


def scan_markets(cfg: F3Config, state: DailyState) -> list[F3Decision]:
    """Run the F3 engine on every market and auto-fire anything that passes."""
    engine = F3Engine(cfg)
    balance = _account_balance()
    news = _news_today()
    decisions: list[F3Decision] = []

    for market in cfg.markets:
        try:
            candles = get_candles(market)
            decision = engine.evaluate(
                market, candles,
                account_balance=balance,
                trades_today=state.trades_today,
                daily_loss_pct=state.realized_loss_pct,
                news_today=news,
                similar_setups=len([d for d in state.decisions if d.market == market]),
            )
        except Exception as exc:
            log.error("Evaluate %s failed: %s", market, exc)
            continue

        log.info("%s → %s (CEO %.0f%%): %s", market, decision.status,
                 decision.ceo_confidence, decision.ceo_reasoning)
        if decision.fire:
            alerts.execute_trade(decision)
        state.record(decision)
        decisions.append(decision)

    state.save()
    return decisions


def run_tick(cfg: F3Config | None = None) -> None:
    """Decide what to do based on the current ET time, then do exactly that."""
    cfg = cfg or F3Config()
    now = datetime.now(ET)
    minutes = now.hour * 60 + now.minute

    if now.weekday() >= 5:  # weekend
        log.info("Weekend — nothing to do.")
        return

    state = DailyState.load()

    # Morning report window: 07:00–07:59 ET (before the NY AM killzone).
    if 7 * 60 <= minutes < 8 * 60:
        log.info("Morning report window")
        alerts.send_morning_report(cfg)
        return

    # Evening report window: 16:30–17:29 ET.
    if 16 * 60 + 30 <= minutes < 17 * 60 + 30:
        log.info("Evening report window")
        alerts.send_evening_report(cfg, state)
        return

    # Inside a killzone → scan & auto-fire. (The timing agent re-checks too.)
    kz = cfg.killzone_for(now.timetz().replace(tzinfo=None))
    if kz is not None:
        log.info("In %s killzone (%s ET) — scanning %s",
                 kz.name, now.strftime("%H:%M"), ", ".join(cfg.markets))
        scan_markets(cfg, state)
        return

    log.info("Outside killzones/report windows (%s ET) — nothing to do.",
             now.strftime("%H:%M"))
