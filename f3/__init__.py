"""
F3 Strategy — "Claude Trader" Strategy Playbook implementation.

The F3 framework strips trading down to three mechanical steps — Frame, Find,
Fire — built on ICT structure & liquidity concepts. This package implements the
exact playbook:

  * FRAME — higher-timeframe bias from the last break of structure, inside a killzone.
  * FIND  — a liquidity sweep + a fair-value-gap / order-block entry zone in the
            discount/premium area aligned with bias.
  * FIRE  — a lower-timeframe shift in structure, a stop beyond the sweep, and a
            minimum 2:1 reward:risk, sized to a fixed small risk %.

It is run by 7 specialised agents — Atlas, Lumen, Hydra, Hermes, Apollo,
Hephaestus and Mnemosyne — each grading one thing, with Claude as the CEO that
weighs all seven and gives the final confirmation. Nothing fires unless the team
agrees and Claude signs off.

Flow:  price data → 7 agents grade it → Claude confirms → risk check → alert/execute.
"""
from __future__ import annotations

from .models import (
    Candle,
    Direction,
    AgentVerdict,
    F3Decision,
    ProposedTrade,
    Step,
)
from .engine import F3Engine
from .config import F3Config, RiskRules

__all__ = [
    "Candle",
    "Direction",
    "AgentVerdict",
    "F3Decision",
    "ProposedTrade",
    "Step",
    "F3Engine",
    "F3Config",
    "RiskRules",
]

__version__ = "1.0.0"
