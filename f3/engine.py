"""
F3Engine — the orchestration.

    price data → 7 agents grade it → Claude confirms → risk check → alert/execute

The engine runs the exact F3 rules on a market's candles, produces the mechanical
trade, has the seven agents grade it, lets Claude (the CEO) sign off, sizes the
position to the risk rule, and fills in the daily checklist. The result is a
single F3Decision: FIRE or SKIP, with the full audit trail.
"""
from __future__ import annotations

import logging

from . import agents, ceo, ict
from .config import F3Config
from .models import (
    AgentVerdict,
    Candle,
    Direction,
    F3Decision,
    ProposedTrade,
    Step,
)

log = logging.getLogger(__name__)


def _build_trade(market: str, a: ict.F3Analysis, cfg: F3Config) -> ProposedTrade | None:
    """Turn a complete FRAME→FIND analysis into the mechanical trade."""
    if a.bias is Direction.NEUTRAL or a.sweep is None or a.zone is None:
        return None

    buffer_frac = cfg.stop_buffer_frac
    min_rr = cfg.risk.min_reward_risk

    if a.bias is Direction.LONG:
        entry = a.zone.high
        buffer = buffer_frac * abs(entry - a.sweep.extreme)
        stop = a.sweep.extreme - buffer
        risk = entry - stop
        if risk <= 0:
            return None
        target = entry + min_rr * risk
    else:  # SHORT
        entry = a.zone.low
        buffer = buffer_frac * abs(a.sweep.extreme - entry)
        stop = a.sweep.extreme + buffer
        risk = stop - entry
        if risk <= 0:
            return None
        target = entry - min_rr * risk

    # Safety: refuse a stop tighter than the minimum — a near-zero risk would
    # blow position size up and let a tiny move become a huge loss.
    if risk < cfg.min_risk_frac * entry:
        return None

    return ProposedTrade(
        market=market,
        direction=a.bias,
        entry=round(entry, 2),
        stop=round(stop, 2),
        target=round(target, 2),
        sweep_level=round(a.sweep.level, 2),
        zone_low=round(a.zone.low, 2),
        zone_high=round(a.zone.high, 2),
    )


def _checklist(a: ict.F3Analysis, ctx: agents.GradeContext,
               trade: ProposedTrade | None) -> dict[str, bool]:
    """The daily checklist, straight from the playbook. Any missing → SKIP."""
    min_rr = ctx.config.risk.min_reward_risk
    cap = ctx.config.risk.daily_loss_cap_pct
    return {
        "In a killzone?": ctx.killzone is not None,
        "Bias clear?": a.bias is not Direction.NEUTRAL and a.last_break is not None,
        "Liquidity swept?": a.sweep is not None and a.sweep.side is a.bias,
        "Price in an FVG/OB?": a.zone is not None and a.in_discount_premium,
        "LTF confirmation?": a.ltf_confirmed,
        "2:1+ R:R?": trade is not None and trade.reward_risk >= min_rr,
        "Within daily loss cap?": ctx.daily_loss_pct < cap,
    }


class F3Engine:
    """Evaluate one market and decide FIRE or SKIP."""

    def __init__(self, config: F3Config | None = None) -> None:
        self.config = config or F3Config()

    def evaluate(
        self,
        market: str,
        candles: list[Candle],
        *,
        account_balance: float,
        trades_today: int = 0,
        daily_loss_pct: float = 0.0,
        news_today: list[str] | None = None,
        similar_setups: int = 0,
        similar_win_rate: float = 0.0,
    ) -> F3Decision:
        cfg = self.config
        if len(candles) < cfg.htf_swing_width * 2 + 5:
            raise ValueError(f"Need more candles to analyze {market}")

        now = candles[-1].time
        killzone = cfg.killzone_for(now.timetz().replace(tzinfo=None))

        a = ict.analyze(
            market, candles,
            htf_width=cfg.htf_swing_width,
            ltf_width=cfg.ltf_swing_width,
            lookback=cfg.lookback,
            min_zone_frac=cfg.min_zone_frac,
            require_extreme_sweep=cfg.require_extreme_sweep,
            extreme_tol_frac=cfg.extreme_tol_frac,
        )
        trade = _build_trade(market, a, cfg)

        ctx = agents.GradeContext(
            now=now,
            config=cfg,
            killzone=killzone,
            news_today=news_today or [],
            trade=trade,
            trades_today=trades_today,
            daily_loss_pct=daily_loss_pct,
            account_balance=account_balance,
            similar_setups=similar_setups,
            similar_win_rate=similar_win_rate,
        )

        verdicts: list[AgentVerdict] = agents.grade_all(a, ctx)
        cv = ceo.confirm(verdicts, trade, cfg.ceo_model) if trade is not None \
            else ceo.confirm(verdicts, _placeholder_trade(market), cfg.ceo_model)

        checklist = _checklist(a, ctx, trade)
        fire = cv.approved and all(checklist.values())

        position_size = 0.0
        risk_dollars = 0.0
        notes: list[str] = list(a.notes)
        if fire and trade is not None:
            risk_dollars = account_balance * cfg.risk.risk_per_trade_pct / 100.0
            per_unit = trade.risk_per_unit
            position_size = round(risk_dollars / per_unit, 4) if per_unit > 0 else 0.0
            notes.append(
                f"Sized to {cfg.risk.risk_per_trade_pct:.2f}% "
                f"(${risk_dollars:,.2f}) → {position_size} units"
            )

        return F3Decision(
            market=market,
            fire=fire,
            trade=trade if fire else trade,  # keep trade for context even on skip
            verdicts=verdicts,
            checklist=checklist,
            ceo_approved=cv.approved,
            ceo_confidence=cv.confidence,
            ceo_reasoning=cv.reasoning,
            position_size=position_size,
            risk_dollars=risk_dollars,
            notes=notes,
        )


def _placeholder_trade(market: str) -> ProposedTrade:
    """A dummy trade so the CEO can still issue a (vetoed) verdict when no setup."""
    return ProposedTrade(market, Direction.NEUTRAL, 0, 0, 0, 0, 0, 0)
