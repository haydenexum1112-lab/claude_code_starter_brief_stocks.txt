"""Shared data types for the F3 engine."""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime


class Direction(enum.Enum):
    """Trade / bias direction."""

    LONG = "long"
    SHORT = "short"
    NEUTRAL = "neutral"

    @property
    def opposite(self) -> "Direction":
        if self is Direction.LONG:
            return Direction.SHORT
        if self is Direction.SHORT:
            return Direction.LONG
        return Direction.NEUTRAL


class Step(enum.Enum):
    """Which of the three F3 steps an agent serves."""

    FRAME = "FRAME"
    FIND = "FIND"
    FIRE = "FIRE"


@dataclass(frozen=True)
class Candle:
    """One OHLC bar. `time` must be timezone-aware (ET)."""

    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def is_bullish(self) -> bool:
        return self.close >= self.open

    @property
    def range(self) -> float:
        return self.high - self.low


@dataclass
class AgentVerdict:
    """One agent's independent grade of a proposed setup."""

    agent: str          # e.g. "Atlas"
    role: str           # what it checks
    step: Step | None   # which F3 step it serves (None for memory/advisory)
    passed: bool        # hard pass/fail for its single responsibility
    score: float        # 0–100 confidence in its own check
    reason: str         # one-line explanation
    veto: bool = False  # if True and not passed, the trade dies regardless

    def __str__(self) -> str:
        mark = "✓" if self.passed else "✗"
        step = f"[{self.step.value}]" if self.step else "[CHECK]"
        return f"{mark} {self.agent:<11} {step:<7} {self.score:5.1f}  {self.reason}"


@dataclass
class ProposedTrade:
    """The mechanical trade the F3 rules produce, before the CEO signs off."""

    market: str
    direction: Direction
    entry: float
    stop: float
    target: float
    sweep_level: float
    zone_low: float
    zone_high: float

    @property
    def risk_per_unit(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def reward_per_unit(self) -> float:
        return abs(self.target - self.entry)

    @property
    def reward_risk(self) -> float:
        r = self.risk_per_unit
        return self.reward_per_unit / r if r > 0 else 0.0


@dataclass
class F3Decision:
    """The final output: FIRE or SKIP, with the full audit trail."""

    market: str
    fire: bool
    trade: ProposedTrade | None
    verdicts: list[AgentVerdict]
    checklist: dict[str, bool]
    ceo_approved: bool
    ceo_confidence: float
    ceo_reasoning: str
    position_size: float = 0.0
    risk_dollars: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "FIRE" if self.fire else "SKIP"
