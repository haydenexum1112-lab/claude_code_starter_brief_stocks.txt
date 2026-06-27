"""
F3 configuration — the markets, killzones, news blackout and risk rules.

Everything here comes straight from the playbook. The values are the defaults
the strategy ships with; tune them by constructing F3Config / RiskRules with
your own numbers, but the *rules* (min 2:1, stop beyond the sweep, daily loss
cap, never move a stop against the trade) are the part that keeps you funded.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from zoneinfo import ZoneInfo

# The playbook quotes times in EST. We use America/New_York so the windows track
# the actual New York session across daylight-saving changes.
ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class Killzone:
    """A window of the day where setups are allowed. Outside it: do nothing."""

    name: str
    start: time
    end: time

    def contains(self, t: time) -> bool:
        return self.start <= t < self.end


# The only times we trade. Outside the killzones — no setup, no trade.
KILLZONES: tuple[Killzone, ...] = (
    # London open — ~02:00–05:00 EST
    Killzone("London Open", time(2, 0), time(5, 0)),
    # New York AM — ~08:30–11:00 EST  ← the sweet spot
    Killzone("New York AM", time(8, 30), time(11, 0)),
)

# Cleanest markets for this style.
MARKETS: tuple[str, ...] = ("NQ", "GC")

# High-impact news around which we stand down entirely (no setup, no trade).
HIGH_IMPACT_NEWS: tuple[str, ...] = ("NFP", "CPI", "FOMC")


@dataclass(frozen=True)
class RiskRules:
    """
    The part that keeps you funded. Break these and the strategy doesn't matter.
    """

    # Fixed small % of the account per trade (start 0.5%).
    risk_per_trade_pct: float = 0.5
    # Minimum reward:risk, every time.
    min_reward_risk: float = 2.0
    # Hit the daily loss cap → done for the day, no exceptions (% of account).
    daily_loss_cap_pct: float = 2.0
    # Capped — no over-trading, no revenge.
    max_trades_per_day: int = 3
    # Never move a stop against the trade. Ever.
    allow_moving_stop_against_trade: bool = False


@dataclass(frozen=True)
class F3Config:
    """Top-level knobs for the engine. Sensible ICT defaults out of the box."""

    markets: tuple[str, ...] = MARKETS
    killzones: tuple[Killzone, ...] = KILLZONES
    risk: RiskRules = field(default_factory=RiskRules)

    # Fractal width for higher-timeframe swing points (FRAME / structure).
    htf_swing_width: int = 3
    # Fractal width for the lower-timeframe shift used as FIRE confirmation.
    ltf_swing_width: int = 1
    # How many recent candles to scan for a liquidity sweep / FVG / order block.
    lookback: int = 60
    # A swept level must be retaken by the close — small buffer past the wick for
    # the protective stop, expressed as a fraction of the swept-leg range.
    stop_buffer_frac: float = 0.10

    # Claude (the CEO) model id. Adaptive thinking is used automatically.
    ceo_model: str = "claude-opus-4-8"

    def killzone_for(self, t: time) -> Killzone | None:
        for kz in self.killzones:
            if kz.contains(t):
                return kz
        return None
