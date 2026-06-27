"""
F3 backtester — does the strategy actually work?

Runs the **exact same F3 engine** over historical candles, day by day, the way
the live bot would see them. When the engine FIREs, it simulates the trade to its
stop or target and books the result, enforcing the same risk rules (0.5% sizing,
daily loss cap, max trades/day, flat by end of session). The output is the report
you actually care about before risking a funded account:

    trades, win rate, average R, expectancy, total return, max drawdown.

This proves the *mechanics and the accounting*. Whether the strategy is
profitable depends entirely on the data you feed it — run it on real NQ/GC OHLC
(load_csv / F3_DATA_DIR), not the synthetic history, before drawing conclusions.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from .config import F3Config
from .engine import F3Engine
from .models import Candle, Direction

log = logging.getLogger(__name__)


@dataclass
class Trade:
    market: str
    entry_time: str
    direction: str
    entry: float
    stop: float
    target: float
    exit: float
    r_multiple: float       # +2 win, -1 loss, fractional if closed at session end
    pnl: float
    outcome: str            # "win" / "loss" / "eod"
    balance_after: float


@dataclass
class BacktestResult:
    start_balance: float
    end_balance: float
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)

    # -- summary stats ------------------------------------------------------- #
    @property
    def n(self) -> int:
        return len(self.trades)

    @property
    def wins(self) -> list[Trade]:
        return [t for t in self.trades if t.r_multiple > 0]

    @property
    def losses(self) -> list[Trade]:
        return [t for t in self.trades if t.r_multiple <= 0]

    @property
    def win_rate(self) -> float:
        return len(self.wins) / self.n if self.n else 0.0

    @property
    def avg_r(self) -> float:
        return sum(t.r_multiple for t in self.trades) / self.n if self.n else 0.0

    @property
    def total_return_pct(self) -> float:
        return (self.end_balance / self.start_balance - 1) * 100 if self.start_balance else 0.0

    @property
    def profit_factor(self) -> float:
        gains = sum(t.pnl for t in self.wins)
        pains = -sum(t.pnl for t in self.losses)
        return gains / pains if pains > 0 else float("inf")

    @property
    def max_drawdown_pct(self) -> float:
        peak = self.start_balance
        mdd = 0.0
        for eq in self.equity_curve:
            peak = max(peak, eq)
            mdd = max(mdd, (peak - eq) / peak)
        return mdd * 100

    def report(self) -> str:
        lines = [
            "=" * 56,
            "F3 BACKTEST RESULTS",
            "=" * 56,
            f"Start balance:   ${self.start_balance:,.2f}",
            f"End balance:     ${self.end_balance:,.2f}",
            f"Total return:    {self.total_return_pct:+.2f}%",
            f"Max drawdown:    {self.max_drawdown_pct:.2f}%",
            "-" * 56,
            f"Trades:          {self.n}",
            f"Win rate:        {self.win_rate * 100:.1f}%  "
            f"({len(self.wins)}W / {len(self.losses)}L)",
            f"Average R:       {self.avg_r:+.2f}R per trade",
            f"Profit factor:   {self.profit_factor:.2f}",
            "=" * 56,
        ]
        if self.trades:
            lines.append("Trade log:")
            for i, t in enumerate(self.trades, 1):
                lines.append(
                    f"{i:2d}. {t.market} {t.direction.upper():<5} "
                    f"entry {t.entry:.1f} → exit {t.exit:.1f}  "
                    f"{t.r_multiple:+.2f}R  ${t.pnl:+,.0f}  "
                    f"[{t.outcome}]  ({t.entry_time})"
                )
        else:
            lines.append("No trades fired over this data.")
        return "\n".join(lines)


def _group_by_day(candles: list[Candle]) -> dict[date, list[Candle]]:
    days: dict[date, list[Candle]] = defaultdict(list)
    for c in candles:
        days[c.time.date()].append(c)
    return dict(sorted(days.items()))


class F3Backtester:
    def __init__(self, config: F3Config | None = None,
                 start_balance: float = 50_000.0) -> None:
        self.config = config or F3Config()
        self.start_balance = start_balance
        self.engine = F3Engine(self.config)

    def run(self, market: str, candles: list[Candle]) -> BacktestResult:
        cfg = self.config
        balance = self.start_balance
        result = BacktestResult(self.start_balance, balance)
        result.equity_curve.append(balance)
        min_bars = cfg.htf_swing_width * 2 + 5

        for _, day in _group_by_day(candles).items():
            trades_today = 0
            loss_pct_today = 0.0
            seen_entries: list[float] = []   # don't re-enter the same zone twice
            i = min_bars
            while i < len(day):
                if trades_today >= cfg.risk.max_trades_per_day:
                    break
                if loss_pct_today >= cfg.risk.daily_loss_cap_pct:
                    break
                window = day[: i + 1]
                try:
                    decision = self.engine.evaluate(
                        market, window,
                        account_balance=balance,
                        trades_today=trades_today,
                        daily_loss_pct=loss_pct_today,
                    )
                except ValueError:
                    i += 1
                    continue

                if not decision.fire or decision.trade is None:
                    i += 1
                    continue

                entry = decision.trade.entry
                tol = max(1.0, entry * 0.0005)
                if any(abs(entry - e) <= tol for e in seen_entries):
                    i += 1               # same setup already traded today — skip
                    continue
                seen_entries.append(entry)

                trade, exit_i = self._simulate(decision, day, i, balance)
                if trade is None:           # never filled within the session
                    i += 1
                    continue

                balance = trade.balance_after
                result.trades.append(trade)
                result.equity_curve.append(balance)
                trades_today += 1
                if trade.pnl < 0:
                    loss_pct_today += abs(trade.pnl) / balance * 100
                i = exit_i + 1              # resume scanning after the trade closes

        result.end_balance = balance
        return result

    def _simulate(self, decision, day: list[Candle], signal_i: int,
                  balance: float):
        """Walk forward from the signal bar to the stop or target."""
        t = decision.trade
        risk_dollars = balance * self.config.risk.risk_per_trade_pct / 100.0
        long = t.direction is Direction.LONG
        entry = t.entry
        entry_time = day[signal_i].time.strftime("%Y-%m-%d %H:%M ET")

        for j in range(signal_i + 1, len(day)):
            c = day[j]
            hit_stop = c.low <= t.stop if long else c.high >= t.stop
            hit_target = c.high >= t.target if long else c.low <= t.target
            if hit_stop:                    # conservative: stop before target
                return self._close(t, entry_time, entry, t.stop, -1.0,
                                   risk_dollars, balance, "loss"), j
            if hit_target:
                r = t.reward_risk
                return self._close(t, entry_time, entry, t.target, r,
                                   risk_dollars, balance, "win"), j

        # End of session — flat by the close (no overnight risk).
        last = day[-1].close
        per_unit = t.risk_per_unit
        r = ((last - entry) if long else (entry - last)) / per_unit if per_unit else 0.0
        return self._close(t, entry_time, entry, last, r, risk_dollars,
                           balance, "eod"), len(day) - 1

    def _close(self, t, entry_time, entry, exit_price, r, risk_dollars,
               balance, outcome):
        pnl = risk_dollars * r
        return Trade(
            market=t.market,
            entry_time=entry_time,
            direction=t.direction.value,
            entry=entry,
            stop=t.stop,
            target=t.target,
            exit=round(exit_price, 2),
            r_multiple=round(r, 2),
            pnl=round(pnl, 2),
            outcome=outcome,
            balance_after=round(balance + pnl, 2),
        )
