from __future__ import annotations
import logging
import smtplib
import socket
from datetime import datetime
from email.message import EmailMessage
from zoneinfo import ZoneInfo

from trading_bot.config import (
    GMAIL_ADDRESS,
    GMAIL_APP_PASSWORD,
    MY_EMAIL,
    ATR_PERIOD,
    VWAP_TICKERS,
)
from trading_bot import data_fetcher
from trading_bot.indicators import atr, sma
from trading_bot.tracker import daily_log

ET = ZoneInfo("America/New_York")
logger = logging.getLogger(__name__)

ALL_TICKERS = VWAP_TICKERS


# ---------------------------------------------------------------------------
# SMTP
# ---------------------------------------------------------------------------

def _send_email(subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = MY_EMAIL
    msg.set_content(body)
    try:
        # Resolve to IPv4 explicitly — some environments don't support IPv6
        ip = socket.getaddrinfo("smtp.gmail.com", 465, socket.AF_INET)[0][4][0]
        with smtplib.SMTP_SSL(ip, 465) as smtp:
            smtp.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            smtp.send_message(msg)
        logger.info(f"Email sent: {subject!r} -> {MY_EMAIL}")
    except Exception as exc:
        logger.error(f"Email FAILED ({subject!r}): {exc}")


# ---------------------------------------------------------------------------
# Morning report
# ---------------------------------------------------------------------------

def _ticker_overview(ticker: str) -> str:
    df = data_fetcher.get_daily_bars(ticker, limit=210)
    close = df["close"]
    high = df["high"]
    low = df["low"]

    last = close.iloc[-1]
    prev = close.iloc[-2]
    pct = (last - prev) / prev * 100
    atr_series = atr(high, low, close, ATR_PERIOD)
    atr_now = atr_series.iloc[-1]
    atr_avg20 = atr_series.rolling(20).mean().iloc[-1]
    vol_note = "elevated" if atr_now > atr_avg20 * 1.5 else "normal"

    lines = [
        f"{ticker}",
        f"  Last close:  ${last:.2f}",
        f"  Prev close:  ${prev:.2f}  ({pct:+.2f}%)",
        f"  ATR(14):     ${atr_now:.2f}  (volatility: {vol_note})",
    ]

    trend20 = "above" if last > sma(close, 20).iloc[-1] else "below"
    lines.append(f"  20-day trend: price is {trend20} the 20-day average")
    lines.append("  Strategy:    TJR VWAP Reclaim (15m, 2:1 R:R, flat by close)")

    return "\n".join(lines)


def _correlation_note() -> str:
    try:
        spy_df = data_fetcher.get_daily_bars("SPY", limit=10)
        qqq_df = data_fetcher.get_daily_bars("QQQ", limit=10)
        spy_ret = spy_df["close"].pct_change().dropna().tail(5)
        qqq_ret = qqq_df["close"].pct_change().dropna().tail(5)
        corr = spy_ret.corr(qqq_ret)
        if corr > 0.90:
            return (
                f"SPY/QQQ 5-day return correlation: {corr:.2f} (very high)\n"
                "  -> The four ETFs move together; expect correlated days where most signals agree."
            )
        return f"SPY/QQQ 5-day return correlation: {corr:.2f} (normal)"
    except Exception as exc:
        return f"Correlation data unavailable: {exc}"


def send_morning_report() -> None:
    now = datetime.now(ET)
    date_str = now.strftime("%A, %B %d, %Y")
    time_str = now.strftime("%I:%M %p ET")

    sections = [
        f"Trading Bot - Morning Report",
        f"Date: {date_str}",
        f"Generated: {time_str}",
        "",
        "=" * 50,
        "MARKET OVERVIEW",
        "=" * 50,
    ]

    for ticker in ALL_TICKERS:
        try:
            sections.append(_ticker_overview(ticker))
        except Exception as exc:
            sections.append(f"{ticker}  (data unavailable: {exc})")
        sections.append("")

    sections += [
        "=" * 50,
        "VOLATILITY AND CORRELATION",
        "=" * 50,
        _correlation_note(),
        "",
        "=" * 50,
        "SCHEDULER",
        "=" * 50,
        "VWAP Reclaim checks: every 15 minutes from 9:00 AM - 4:00 PM ET",
        "End-of-day flatten: 3:55 PM ET (no overnight positions)",
        "",
    ]

    _send_email("Trading Bot - Morning Report", "\n".join(sections))


# ---------------------------------------------------------------------------
# Evening report
# ---------------------------------------------------------------------------

def send_evening_report() -> None:
    now = datetime.now(ET)
    date_str = now.strftime("%A, %B %d, %Y")
    time_str = now.strftime("%I:%M %p ET")

    sections = [
        f"Trading Bot - Evening Report",
        f"Date: {date_str}",
        f"Generated: {time_str}",
        "",
    ]

    # P&L
    try:
        acct = data_fetcher.get_account_info()
        pnl = acct["daily_pnl"]
        pnl_pct = acct["daily_pnl_pct"]
        sign = "+" if pnl >= 0 else ""
        sections += [
            "=" * 50,
            "DAILY P&L",
            "=" * 50,
            f"Account equity:  ${acct['equity']:,.2f}",
            f"Daily P&L:       {sign}${pnl:,.2f}  ({sign}{pnl_pct:.2f}%)",
            "",
        ]
    except Exception as exc:
        sections += ["DAILY P&L", f"  (unavailable: {exc})", ""]

    # Trade summary
    approved = daily_log.approved
    rejected = daily_log.rejected
    sections += [
        "=" * 50,
        "TRADE SUMMARY",
        "=" * 50,
        f"Signals approved: {len(approved)}",
        f"Signals rejected: {len(rejected)}",
        "",
    ]

    if approved:
        sections += ["APPROVED TRADES", "-" * 30]
        for i, rec in enumerate(approved, 1):
            t = rec.time.strftime("%I:%M %p")
            sections.append(
                f"{i}. {rec.ticker:<5} {rec.action.upper():<5} "
                f"@ ${rec.entry_price:.2f}  qty={rec.quantity}  [{t}]"
            )
        sections.append("")

    if rejected:
        sections += ["REJECTED SIGNALS", "-" * 30]
        for i, rec in enumerate(rejected, 1):
            t = rec.time.strftime("%I:%M %p")
            sections.append(
                f"{i}. {rec.ticker:<5} {rec.action.upper():<5} [{t}]  "
                f"BLOCKED: {rec.reason}"
            )
        sections.append("")

    if not approved and not rejected:
        sections += ["No signals were generated today.", ""]

    # Open positions
    sections += [
        "=" * 50,
        "OPEN POSITIONS",
        "=" * 50,
    ]
    try:
        positions = data_fetcher.get_positions()
        if positions:
            for p in positions:
                sign = "+" if p["unrealized_pl"] >= 0 else ""
                sections.append(
                    f"{p['ticker']:<5}  {p['side'].upper():<6}  "
                    f"{p['qty']:.0f} shares  "
                    f"entry ${p['avg_entry']:.2f}  "
                    f"current ${p['current_price']:.2f}  "
                    f"unrealized P&L: {sign}${p['unrealized_pl']:.2f} ({sign}{p['unrealized_plpc']:.2f}%)"
                )
        else:
            sections.append("No open positions.")
    except Exception as exc:
        sections.append(f"(positions unavailable: {exc})")

    sections.append("")

    _send_email("Trading Bot - Evening Report", "\n".join(sections))
