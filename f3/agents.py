"""
The 7 F3 agents — each checks exactly one thing, independently.

When you automate F3 it's not one bot guessing — it's a team. Each agent grades
the setup on its single responsibility and returns a verdict. Six of them hold a
hard veto (if structure, news, liquidity, setup, timing, or risk says no, the
trade dies); Mnemosyne (memory) is advisory and only informs confidence.

    Atlas       Macro & structure — the higher-timeframe bias        (FRAME)
    Lumen       News & fundamentals — blackout around NFP/CPI/FOMC
    Hydra       Liquidity — finds the sweep and where stops sit       (FIND)
    Hermes      The setup — maps the FVG / order block entry          (FIND)
    Apollo      Timing — confirms you're in the killzone window
    Hephaestus  Risk — sizes the trade, locks daily loss & drawdown   (FIRE)
    Mnemosyne   Memory — logs every trade and recalls similar setups
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .config import F3Config, Killzone
from .ict import F3Analysis
from .models import AgentVerdict, Direction, ProposedTrade, Step


@dataclass
class GradeContext:
    """Everything the agents need beyond the price analysis itself."""

    now: datetime
    config: F3Config
    killzone: Killzone | None
    news_today: list[str]                 # high-impact events scheduled today
    trade: ProposedTrade | None
    trades_today: int
    daily_loss_pct: float                 # realised loss today (positive number)
    account_balance: float
    similar_setups: int = 0               # Mnemosyne: count of past lookalikes
    similar_win_rate: float = 0.0         # Mnemosyne: their historical win rate


# --------------------------------------------------------------------------- #
# Individual agents
# --------------------------------------------------------------------------- #
def atlas(a: F3Analysis, ctx: GradeContext) -> AgentVerdict:
    """Macro & structure — is the higher-timeframe bias clear? (FRAME)"""
    clear = a.bias is not Direction.NEUTRAL and a.last_break is not None
    if clear:
        reason = f"HTF bias {a.bias.value.upper()} from last break of structure"
        score = 90.0
    else:
        reason = "No clear break of structure — bias is neutral, stand down"
        score = 10.0
    return AgentVerdict("Atlas", "Macro & structure", Step.FRAME, clear, score, reason, veto=True)


def lumen(a: F3Analysis, ctx: GradeContext) -> AgentVerdict:
    """News & fundamentals — blackout around high-impact news."""
    blocked = bool(ctx.news_today)
    if blocked:
        reason = f"High-impact news today ({', '.join(ctx.news_today)}) — no trade"
        return AgentVerdict("Lumen", "News & fundamentals", None, False, 0.0, reason, veto=True)
    return AgentVerdict("Lumen", "News & fundamentals", None, True, 100.0,
                        "No high-impact news (NFP/CPI/FOMC) — clear to trade", veto=True)


def hydra(a: F3Analysis, ctx: GradeContext) -> AgentVerdict:
    """Liquidity — was an obvious high/low swept in our direction? (FIND)"""
    ok = a.sweep is not None and a.sweep.side is a.bias and a.bias is not Direction.NEUTRAL
    if ok:
        side = "sell-side" if a.bias is Direction.LONG else "buy-side"
        reason = f"{side} liquidity swept at {a.sweep.level:.2f} then reclaimed"
        score = 88.0
    else:
        reason = "No liquidity sweep aligned with bias — no zone, no trade"
        score = 15.0
    return AgentVerdict("Hydra", "Liquidity", Step.FIND, ok, score, reason, veto=True)


def hermes(a: F3Analysis, ctx: GradeContext) -> AgentVerdict:
    """The setup — is there an FVG/OB entry zone in discount/premium? (FIND)"""
    ok = a.zone is not None and a.in_discount_premium
    if ok:
        area = "discount" if a.bias is Direction.LONG else "premium"
        reason = f"{a.zone.kind} entry zone {a.zone.low:.2f}-{a.zone.high:.2f} in {area}"
        score = 85.0
    elif a.zone is not None:
        reason = f"{a.zone.kind} found but not in the discount/premium area — skip"
        score = 35.0
    else:
        reason = "No FVG/OB entry zone aligned with bias"
        score = 15.0
    return AgentVerdict("Hermes", "The setup", Step.FIND, ok, score, reason, veto=True)


def apollo(a: F3Analysis, ctx: GradeContext) -> AgentVerdict:
    """Timing — are we inside a killzone window?"""
    kz = ctx.killzone
    if kz is not None:
        return AgentVerdict("Apollo", "Timing", None, True, 95.0,
                            f"Inside {kz.name} killzone", veto=True)
    return AgentVerdict("Apollo", "Timing", None, False, 0.0,
                        "Outside the killzones — patience is part of the edge", veto=True)


def hephaestus(a: F3Analysis, ctx: GradeContext) -> AgentVerdict:
    """Risk — size the trade, enforce 2:1, daily loss cap and max trades. (FIRE)"""
    r = ctx.config.risk
    t = ctx.trade
    reasons: list[str] = []
    ok = True

    if t is None:
        return AgentVerdict("Hephaestus", "Risk", Step.FIRE, False, 0.0,
                            "No valid trade to size", veto=True)

    if t.reward_risk < r.min_reward_risk:
        ok = False
        reasons.append(f"R:R {t.reward_risk:.2f} < {r.min_reward_risk:.1f} minimum")
    else:
        reasons.append(f"R:R {t.reward_risk:.2f} (>= {r.min_reward_risk:.1f})")

    if ctx.trades_today >= r.max_trades_per_day:
        ok = False
        reasons.append(f"max {r.max_trades_per_day} trades/day reached")

    if ctx.daily_loss_pct >= r.daily_loss_cap_pct:
        ok = False
        reasons.append(f"daily loss cap {r.daily_loss_cap_pct:.1f}% hit — done for the day")

    # Stop must sit beyond the sweep / invalidation, never in noise.
    stop_beyond = (t.stop < t.sweep_level if t.direction is Direction.LONG
                   else t.stop > t.sweep_level)
    if not stop_beyond:
        ok = False
        reasons.append("stop is not beyond the sweep / invalidation")

    score = 90.0 if ok else 20.0
    return AgentVerdict("Hephaestus", "Risk", Step.FIRE, ok, score,
                        "; ".join(reasons), veto=True)


def mnemosyne(a: F3Analysis, ctx: GradeContext) -> AgentVerdict:
    """Memory — recall similar setups (advisory; never vetoes)."""
    if ctx.similar_setups == 0:
        return AgentVerdict("Mnemosyne", "Memory", None, True, 50.0,
                            "No similar setups on record yet — neutral", veto=False)
    wr = ctx.similar_win_rate
    passed = wr >= 0.5
    reason = (f"{ctx.similar_setups} similar setups logged, "
              f"{wr * 100:.0f}% historical win rate")
    return AgentVerdict("Mnemosyne", "Memory", None, passed, wr * 100, reason, veto=False)


# Order matters only for display; the CEO weighs all of them.
ALL_AGENTS = (atlas, lumen, hydra, hermes, apollo, hephaestus, mnemosyne)


def grade_all(a: F3Analysis, ctx: GradeContext) -> list[AgentVerdict]:
    """Each agent grades the setup independently."""
    return [agent(a, ctx) for agent in ALL_AGENTS]
