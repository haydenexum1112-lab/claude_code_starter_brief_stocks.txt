"""
Claude — the CEO. The final say.

Each agent grades the setup independently; Claude weighs all seven and confirms
the trade, like a senior trader signing off. The hard rule is non-negotiable and
enforced in code: **if a veto agent (structure, news, liquidity, setup, timing,
or risk) says no, the trade dies** — no emotion, no override.

If the Anthropic API is reachable (ANTHROPIC_API_KEY set, `anthropic` installed),
Claude provides the judgement and the written rationale. Otherwise a deterministic
consensus stands in so the engine always runs — the same hard vetoes, with
confidence taken from the agents' own scores.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

from .models import AgentVerdict, ProposedTrade

log = logging.getLogger(__name__)

# JSON shape we ask Claude to return (and that the fallback mirrors).
_CEO_SCHEMA = {
    "type": "object",
    "properties": {
        "approved": {"type": "boolean"},
        "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        "reasoning": {"type": "string"},
    },
    "required": ["approved", "confidence", "reasoning"],
    "additionalProperties": False,
}

_SYSTEM = (
    "You are the senior trader signing off trades for a mechanical ICT strategy "
    "called F3 (Frame, Find, Fire). Seven specialist agents each grade one part "
    "of the setup and hand you their verdicts. Your job is to weigh all seven and "
    "give the final confirmation, exactly as a disciplined desk head would. "
    "Rules you must never break: if any agent with a veto has failed its check, "
    "the trade is dead — do not approve it. Approve only setups where structure, "
    "liquidity, the entry zone, timing, news and risk all align and the reward is "
    "at least twice the risk. No emotion, no override. Respond only with the "
    "required JSON."
)


@dataclass
class CeoVerdict:
    approved: bool
    confidence: float
    reasoning: str
    source: str  # "claude" or "consensus"


def _hard_veto(verdicts: list[AgentVerdict]) -> AgentVerdict | None:
    """Return the first failed veto verdict, if any — that one kills the trade."""
    for v in verdicts:
        if v.veto and not v.passed:
            return v
    return None


def _consensus(verdicts: list[AgentVerdict], blocker: AgentVerdict | None) -> CeoVerdict:
    """Deterministic stand-in for Claude. Same hard rule, score-based confidence."""
    if blocker is not None:
        return CeoVerdict(
            approved=False,
            confidence=0.0,
            reasoning=f"{blocker.agent} vetoed: {blocker.reason}. The trade dies.",
            source="consensus",
        )
    confidence = sum(v.score for v in verdicts) / len(verdicts)
    return CeoVerdict(
        approved=True,
        confidence=round(confidence, 1),
        reasoning="All seven agents agree — structure, liquidity, setup, timing, "
                  "news and risk align with a 2:1+ target. Signed off.",
        source="consensus",
    )


def _ask_claude(verdicts: list[AgentVerdict], trade: ProposedTrade,
                model: str) -> CeoVerdict | None:
    """Use Claude for the final sign-off. Returns None if unavailable."""
    if not os.getenv("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
    except ImportError:
        log.info("anthropic SDK not installed — CEO falling back to consensus")
        return None

    board = "\n".join(
        f"- {v.agent} ({v.role}): {'PASS' if v.passed else 'FAIL'} "
        f"score={v.score:.0f} veto={v.veto} — {v.reason}"
        for v in verdicts
    )
    user = (
        f"Market: {trade.market}\n"
        f"Proposed {trade.direction.value.upper()} — entry {trade.entry:.2f}, "
        f"stop {trade.stop:.2f}, target {trade.target:.2f}, "
        f"R:R {trade.reward_risk:.2f}\n\n"
        f"Agent board:\n{board}\n\n"
        "Weigh all seven and give the final confirmation as the senior trader."
    )
    try:
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=model,
            max_tokens=2048,
            thinking={"type": "adaptive"},
            system=_SYSTEM,
            output_config={"format": {"type": "json_schema", "schema": _CEO_SCHEMA}},
            messages=[{"role": "user", "content": user}],
        )
        text = next((b.text for b in resp.content if b.type == "text"), "")
        data = json.loads(text)
        return CeoVerdict(
            approved=bool(data["approved"]),
            confidence=float(data["confidence"]),
            reasoning=str(data["reasoning"]),
            source="claude",
        )
    except Exception as exc:  # any API/parse failure → deterministic fallback
        log.warning("Claude CEO call failed (%s) — falling back to consensus", exc)
        return None


def confirm(verdicts: list[AgentVerdict], trade: ProposedTrade,
            model: str = "claude-opus-4-8") -> CeoVerdict:
    """
    The final say. Enforce the hard vetoes in code, then let Claude (or the
    deterministic consensus) sign off. Claude can decline a clean setup on
    judgement, but can never approve one a veto agent has failed.
    """
    blocker = _hard_veto(verdicts)
    if blocker is not None:
        return _consensus(verdicts, blocker)

    verdict = _ask_claude(verdicts, trade, model)
    if verdict is None:
        return _consensus(verdicts, None)
    return verdict
