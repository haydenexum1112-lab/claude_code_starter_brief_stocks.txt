"""
Tests for the F3 engine. Run with:  python -m pytest f3/tests -q
(or plain `python -m unittest f3.tests.test_f3`)

These run fully offline — no Anthropic key needed; the CEO falls back to the
deterministic consensus, which enforces the same hard veto rules.
"""
from __future__ import annotations

import unittest
from datetime import datetime, time

from f3.config import ET, F3Config
from f3.engine import F3Engine
from f3.market_data import textbook_long_setup
from f3.models import Direction
from f3 import ict


def _ny_am_end() -> datetime:
    return datetime.now(ET).replace(hour=9, minute=45, second=0, microsecond=0)


class TextbookSetupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.candles = textbook_long_setup(end=_ny_am_end())
        self.decision = F3Engine().evaluate("NQ", self.candles, account_balance=50_000)

    def test_fires(self) -> None:
        self.assertEqual(self.decision.status, "FIRE")
        self.assertTrue(self.decision.ceo_approved)

    def test_all_agents_pass(self) -> None:
        for v in self.decision.verdicts:
            self.assertTrue(v.passed, f"{v.agent} should pass: {v.reason}")

    def test_full_checklist(self) -> None:
        self.assertTrue(all(self.decision.checklist.values()), self.decision.checklist)

    def test_trade_is_long_with_min_rr(self) -> None:
        t = self.decision.trade
        self.assertIsNotNone(t)
        self.assertIs(t.direction, Direction.LONG)
        self.assertGreaterEqual(t.reward_risk, 2.0)
        # stop sits beyond the sweep (below it for a long)
        self.assertLess(t.stop, t.sweep_level)

    def test_position_sized_to_half_percent(self) -> None:
        self.assertAlmostEqual(self.decision.risk_dollars, 250.0, places=2)
        self.assertGreater(self.decision.position_size, 0)


class VetoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.candles = textbook_long_setup(end=_ny_am_end())

    def test_news_blackout_kills_trade(self) -> None:
        d = F3Engine().evaluate("NQ", self.candles, account_balance=50_000,
                                news_today=["CPI"])
        self.assertEqual(d.status, "SKIP")
        self.assertFalse(d.ceo_approved)
        lumen = next(v for v in d.verdicts if v.agent == "Lumen")
        self.assertFalse(lumen.passed)

    def test_outside_killzone_skips(self) -> None:
        # Move the setup to 13:00 ET — outside both killzones.
        end = datetime.now(ET).replace(hour=13, minute=0, second=0, microsecond=0)
        candles = textbook_long_setup(end=end)
        d = F3Engine().evaluate("NQ", candles, account_balance=50_000)
        self.assertEqual(d.status, "SKIP")
        apollo = next(v for v in d.verdicts if v.agent == "Apollo")
        self.assertFalse(apollo.passed)

    def test_daily_loss_cap_blocks(self) -> None:
        d = F3Engine().evaluate("NQ", self.candles, account_balance=50_000,
                                daily_loss_pct=2.5)
        self.assertEqual(d.status, "SKIP")
        heph = next(v for v in d.verdicts if v.agent == "Hephaestus")
        self.assertFalse(heph.passed)

    def test_max_trades_blocks(self) -> None:
        d = F3Engine().evaluate("NQ", self.candles, account_balance=50_000,
                                trades_today=3)
        self.assertEqual(d.status, "SKIP")


class IctPrimitiveTests(unittest.TestCase):
    def test_killzones(self) -> None:
        cfg = F3Config()
        self.assertIsNotNone(cfg.killzone_for(time(9, 0)))    # NY AM
        self.assertIsNotNone(cfg.killzone_for(time(3, 0)))    # London
        self.assertIsNone(cfg.killzone_for(time(13, 0)))      # dead zone

    def test_bias_detected(self) -> None:
        candles = textbook_long_setup(end=_ny_am_end())
        bias, brk = ict.htf_bias(candles, width=3)
        self.assertIs(bias, Direction.LONG)
        self.assertIsNotNone(brk)


if __name__ == "__main__":
    unittest.main()
