"""
Daily state — what already happened today.

Keeps the count of trades fired and the realised loss so the risk agent
(Hephaestus) can enforce "max trades/day" and the "daily loss cap → done for the
day" rule across separate runs. Persisted to a small JSON file so a stateless
cloud runner (GitHub Actions) can carry it between 15-minute ticks via a cache.

This is Mnemosyne's ledger: every trade logged, ready to be recalled.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime

from .config import ET

log = logging.getLogger(__name__)

DEFAULT_PATH = os.getenv("F3_STATE_PATH", "f3_state.json")


@dataclass
class LoggedDecision:
    time: str
    market: str
    status: str            # FIRE / SKIP
    direction: str
    entry: float
    stop: float
    target: float
    reward_risk: float
    ceo_confidence: float
    reason: str


@dataclass
class DailyState:
    date: str = ""
    trades_today: int = 0
    realized_loss_pct: float = 0.0
    decisions: list[LoggedDecision] = field(default_factory=list)
    path: str = DEFAULT_PATH

    # -- persistence --------------------------------------------------------- #
    @classmethod
    def load(cls, path: str = DEFAULT_PATH) -> "DailyState":
        today = datetime.now(ET).strftime("%Y-%m-%d")
        try:
            with open(path, encoding="utf-8") as fh:
                raw = json.load(fh)
            if raw.get("date") == today:
                st = cls(
                    date=raw["date"],
                    trades_today=raw.get("trades_today", 0),
                    realized_loss_pct=raw.get("realized_loss_pct", 0.0),
                    decisions=[LoggedDecision(**d) for d in raw.get("decisions", [])],
                    path=path,
                )
                return st
        except FileNotFoundError:
            pass
        except Exception as exc:
            log.warning("Could not read state %s (%s) — starting fresh", path, exc)
        # New day / no file → fresh slate.
        return cls(date=today, path=path)

    def save(self) -> None:
        data = asdict(self)
        data.pop("path", None)
        try:
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
        except Exception as exc:
            log.warning("Could not save state %s (%s)", self.path, exc)

    # -- updates ------------------------------------------------------------- #
    def record(self, decision) -> None:
        """Log any decision (FIRE or SKIP) for the evening report."""
        t = decision.trade
        self.decisions.append(LoggedDecision(
            time=datetime.now(ET).strftime("%I:%M %p ET"),
            market=decision.market,
            status=decision.status,
            direction=(t.direction.value if t else "—"),
            entry=(t.entry if t else 0.0),
            stop=(t.stop if t else 0.0),
            target=(t.target if t else 0.0),
            reward_risk=(round(t.reward_risk, 2) if t else 0.0),
            ceo_confidence=decision.ceo_confidence,
            reason=decision.ceo_reasoning,
        ))
        if decision.fire:
            self.trades_today += 1

    @property
    def fires(self) -> list[LoggedDecision]:
        return [d for d in self.decisions if d.status == "FIRE"]
