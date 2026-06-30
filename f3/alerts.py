"""
Alerts & execution — the last step of the flow: alert / execute.

Two outbound channels, both optional and fail-safe (they log and no-op if the
matching env vars aren't set, so the engine always runs):

  * execute_trade()  — auto-fires the trade to a broker webhook (TradersPost-style).
                       This is what makes every trade automatic — you don't lift a
                       finger; the system makes them all.
  * send_morning_report() / send_evening_report() — Gmail emails, morning and night.

Configure via environment (see .env.example):
  F3_WEBHOOK_URL          broker webhook that receives the JSON order
  F3_EXECUTE=1            actually POST orders (default off = dry-run/log only)
  GMAIL_ADDRESS / GMAIL_APP_PASSWORD / MY_EMAIL    morning & night emails
"""
from __future__ import annotations

import logging
import os
import smtplib
import socket
from datetime import datetime
from email.message import EmailMessage

from .config import ET, F3Config
from .models import F3Decision

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Execution — auto-fire to the broker webhook
# --------------------------------------------------------------------------- #
def execute_trade(decision: F3Decision) -> bool:
    """
    POST a FIRE decision to the broker webhook so the trade is placed
    automatically. Returns True if the order was sent (or accepted in dry-run).
    """
    if not decision.fire or decision.trade is None:
        return False

    t = decision.trade
    payload = {
        "ticker": t.market,
        "action": "buy" if t.direction.value == "long" else "sell",
        "quantity": decision.position_size,
        "entry": t.entry,
        "stopLoss": {"type": "stop", "price": t.stop},
        "takeProfit": {"type": "limit", "price": t.target},
        "sentiment": t.direction.value,
        "strategy": "F3",
        "time": datetime.now(ET).isoformat(),
    }

    url = os.getenv("F3_WEBHOOK_URL")
    execute = os.getenv("F3_EXECUTE", "0").lower() in ("1", "true", "yes")

    if not url or not execute:
        log.info("FIRE %s %s (dry-run — set F3_WEBHOOK_URL and F3_EXECUTE=1 to send): %s",
                 t.market, t.direction.value, payload)
        return True
    try:
        import requests
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        log.info("Order sent to broker [%s]: %s %s", resp.status_code,
                 t.market, t.direction.value)
        return True
    except Exception as exc:
        log.error("Broker webhook FAILED for %s: %s", t.market, exc)
        return False


# --------------------------------------------------------------------------- #
# Email (Gmail SMTP) — morning & night
# --------------------------------------------------------------------------- #
def _send_email(subject: str, body: str) -> None:
    sender = os.getenv("GMAIL_ADDRESS")
    password = os.getenv("GMAIL_APP_PASSWORD")
    to = os.getenv("MY_EMAIL")
    if not (sender and password and to):
        log.info("Email not configured — would have sent %r:\n%s", subject, body)
        return
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    msg.set_content(body)
    try:
        ip = socket.getaddrinfo("smtp.gmail.com", 465, socket.AF_INET)[0][4][0]
        with smtplib.SMTP_SSL(ip, 465) as smtp:
            smtp.login(sender, password)
            smtp.send_message(msg)
        log.info("Email sent: %r -> %s", subject, to)
    except Exception as exc:
        log.error("Email FAILED (%r): %s", subject, exc)


def _hr(title: str) -> list[str]:
    return ["=" * 52, title, "=" * 52]


def send_morning_report(cfg: F3Config) -> None:
    """Morning email — the plan, the killzones, the rules and the checklist."""
    now = datetime.now(ET)
    kz = ", ".join(f"{k.name} ({k.start:%H:%M}–{k.end:%H:%M} ET)" for k in cfg.killzones)
    r = cfg.risk
    lines = [
        "F3 Strategy — Morning Report",
        f"Date: {now:%A, %B %d, %Y}",
        f"Generated: {now:%I:%M %p ET}",
        "",
        *_hr("TODAY'S PLAN"),
        f"Markets:    {', '.join(cfg.markets)}",
        f"Killzones:  {kz}",
        "Outside the killzones and around NFP/CPI/FOMC — we do nothing.",
        "",
        *_hr("THE RULES THAT KEEP US FUNDED"),
        f"Risk per trade:   {r.risk_per_trade_pct:.2f}% of account",
        f"Reward:risk:      minimum {r.min_reward_risk:.1f}:1, every time",
        f"Daily loss cap:   {r.daily_loss_cap_pct:.1f}% → done for the day",
        f"Max trades/day:   {r.max_trades_per_day}",
        "Stop:             beyond the sweep / invalidation — never moved against the trade",
        "",
        *_hr("THE 7 AGENTS + CLAUDE (CEO)"),
        "Atlas · Lumen · Hydra · Hermes · Apollo · Hephaestus · Mnemosyne",
        "Each grades one thing; Claude weighs all seven and signs off.",
        "Nothing fires unless the team agrees and Claude confirms.",
        "",
        "One good setup, run with perfect discipline. Boring on purpose.",
        "",
    ]
    _send_email("F3 Strategy — Morning Report", "\n".join(lines))


def send_evening_report(cfg: F3Config, state) -> None:
    """Night email — every decision the desk made today, fires and skips."""
    now = datetime.now(ET)
    fires = state.fires
    lines = [
        "F3 Strategy — Evening Report",
        f"Date: {now:%A, %B %d, %Y}",
        f"Generated: {now:%I:%M %p ET}",
        "",
        *_hr("TODAY'S DESK ACTIVITY"),
        f"Setups evaluated: {len(state.decisions)}",
        f"Trades fired:     {len(fires)} / {cfg.risk.max_trades_per_day} max",
        f"Daily loss used:  {state.realized_loss_pct:.2f}% / {cfg.risk.daily_loss_cap_pct:.1f}% cap",
        "",
    ]
    if fires:
        lines += _hr("TRADES FIRED")
        for i, d in enumerate(fires, 1):
            lines.append(
                f"{i}. {d.market} {d.direction.upper()} @ {d.entry:.2f}  "
                f"stop {d.stop:.2f}  target {d.target:.2f}  "
                f"R:R {d.reward_risk:.2f}  [{d.time}]"
            )
            lines.append(f"   CEO ({d.ceo_confidence:.0f}%): {d.reason}")
        lines.append("")
    skips = [d for d in state.decisions if d.status == "SKIP"]
    if skips:
        lines += _hr("SETUPS SKIPPED")
        for i, d in enumerate(skips, 1):
            lines.append(f"{i}. {d.market} [{d.time}] — {d.reason}")
        lines.append("")
    if not state.decisions:
        lines += ["No setups today. Patience is part of the edge.", ""]
    _send_email("F3 Strategy — Evening Report", "\n".join(lines))
