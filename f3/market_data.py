"""
Market data for the F3 engine.

Live feeds (broker / data vendor) are intentionally pluggable — this repo runs
in a restricted sandbox, so the engine is fed candles from one of:

  * load_csv()           — your own OHLC export (time,open,high,low,close,volume)
  * synthetic_series()   — a deterministic random walk for smoke-testing
  * textbook_long_setup()— a clean, reproducible bullish F3 setup for the demo

To wire a real feed, write a function that returns `list[Candle]` (oldest→newest,
timezone-aware ET timestamps) and hand it to F3Engine.evaluate().
"""
from __future__ import annotations

import csv
import random
from datetime import datetime, timedelta

from .config import ET
from .models import Candle


def load_csv(path: str) -> list[Candle]:
    """Load candles from a CSV with header: time,open,high,low,close,volume.

    `time` may be an ISO-8601 string or a UNIX epoch (seconds). Naive timestamps
    are assumed to be ET.
    """
    out: list[Candle] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            raw = row["time"].strip()
            try:
                ts = datetime.fromtimestamp(float(raw), tz=ET)
            except ValueError:
                ts = datetime.fromisoformat(raw)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=ET)
            out.append(Candle(
                time=ts,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume", 0) or 0),
            ))
    out.sort(key=lambda c: c.time)
    return out


def _times(count: int, end: datetime, minutes: int) -> list[datetime]:
    """`count` timestamps `minutes` apart, finishing at `end` (inclusive)."""
    return [end - timedelta(minutes=minutes * (count - 1 - i)) for i in range(count)]


def _candle(t: datetime, o: float, c: float, wup: float, wdn: float,
            vol: float = 1000.0) -> Candle:
    hi = max(o, c) + wup
    lo = min(o, c) - wdn
    return Candle(t, round(o, 2), round(hi, 2), round(lo, 2), round(c, 2), vol)


def synthetic_series(count: int = 200, *, start: float = 20_000.0,
                     drift: float = 0.0, vol: float = 12.0, seed: int = 7,
                     end: datetime | None = None, minutes: int = 5) -> list[Candle]:
    """A deterministic random-walk OHLC series for smoke-testing."""
    rng = random.Random(seed)
    end = end or datetime.now(ET)
    times = _times(count, end, minutes)
    candles: list[Candle] = []
    price = start
    for t in times:
        o = price
        c = o + drift + rng.gauss(0, vol)
        wick = abs(rng.gauss(0, vol)) * 0.5
        candles.append(_candle(t, o, c, wick, wick))
        price = c
    return candles


def textbook_long_setup(end: datetime | None = None, minutes: int = 5) -> list[Candle]:
    """
    A clean, reproducible bullish F3 setup ending inside the New York AM killzone:

      FRAME — an uptrend whose last break of structure is bullish (bias = LONG).
      FIND  — a pullback into discount that sweeps a prior swing low and leaves a
              bullish fair-value gap as the entry zone.
      FIRE  — a lower-timeframe shift back up after the sweep.

    Default `end` is the next 09:45 ET (NY AM sweet spot) so the timing agent
    passes. Hand this straight to F3Engine.evaluate() to see a FIRE.
    """
    if end is None:
        now = datetime.now(ET)
        end = now.replace(hour=9, minute=45, second=0, microsecond=0)
    candles = _long_setup_candles()
    times = _times(len(candles), end, minutes)
    return [Candle(t, c.open, c.high, c.low, c.close, c.volume)
            for t, c in zip(times, candles)]


def _long_setup_candles(base: float = 20_000.0) -> list[Candle]:
    """The untimed candles of one clean bullish F3 setup (FRAME→FIND→FIRE)."""
    # Anchor walk (close prices). Each leg is several candles so swing fractals
    # form cleanly. Turning points become swing highs/lows.
    legs: list[tuple[float, int]] = [
        (base + 0,    1),   # start
        (base - 100,  4),   # swing low  L1
        (base + 50,   4),   # swing high H1
        (base - 50,   4),   # swing low  L2 (higher low)
        (base + 120,  5),   # bullish BOS above H1 → bias LONG, new high H2
        (base + 10,   4),   # pullback begins, into discount
    ]
    closes: list[float] = []
    prev = legs[0][0]
    for target, steps in legs[1:]:
        for k in range(1, steps + 1):
            closes.append(prev + (target - prev) * k / steps)
        prev = target

    candles: list[Candle] = []
    o = legs[0][0]
    for c in closes:
        pad = abs(c - o) * 0.3 + 3
        candles.append(_candle(datetime.now(ET), o, c, pad, pad))
        o = c

    # --- The sweep + FVG sequence (FIND) -------------------------------------
    swept_low = base - 50           # L2
    c1 = _candle(datetime.now(ET), o, base - 20, 4, 6)              # candle[i-1]
    c2 = _candle(datetime.now(ET), base - 20, base - 25, 3, 40)     # sweep candle
    c2 = Candle(c2.time, c2.open, base - 16, swept_low - 15, base - 25, c2.volume)
    c3 = _candle(datetime.now(ET), base - 25, base + 30, 8, 3)      # displacement up
    c3 = Candle(c3.time, c3.open, max(c3.high, base + 35), c1.high + 2, base + 30, c3.volume)
    candles.extend([c1, c2, c3])

    # --- LTF shift back up (FIRE confirmation) -------------------------------
    o = c3.close
    for tgt in (base + 15, base + 45, base + 75):
        candles.append(_candle(datetime.now(ET), o, tgt, 6, 6))
        o = tgt
    return candles


def _resolution_candles(o: float, *, win: bool, risk: float = 110.0,
                        count: int = 8) -> list[Candle]:
    """Candles after the signal that walk price to the target (win) or stop (loss)."""
    # entry ≈ o (last close), target ≈ entry + 2*risk, stop ≈ entry - risk
    dest = o + 2.4 * risk if win else o - 1.4 * risk
    out: list[Candle] = []
    prev = o
    for k in range(1, count + 1):
        c = o + (dest - o) * k / count
        pad = abs(c - prev) * 0.2 + 2
        out.append(_candle(datetime.now(ET), prev, c, pad, pad))
        prev = c
    return out


def synthetic_history(days: int = 12, *, minutes: int = 5, seed: int = 11,
                      win_rate: float = 0.6) -> list[Candle]:
    """
    A multi-day series for the backtester: one clean F3 long setup per weekday
    inside the NY AM killzone, followed by candles that resolve to target or stop
    (≈`win_rate` winners, seeded). ILLUSTRATIVE ONLY — it exercises the trade
    accounting and risk rules; it is **not** a performance prediction. Replace
    with real OHLC (load_csv / F3_DATA_DIR) for genuine results.
    """
    rng = random.Random(seed)
    # Start each day at 07:30 ET so the ~26-candle setup completes inside the
    # New York AM killzone (08:30–11:00) where the timing agent allows entries.
    today = datetime.now(ET).replace(hour=7, minute=30, second=0, microsecond=0)
    out: list[Candle] = []
    d = 0
    built = 0
    while built < days:
        day_start = today - timedelta(days=(days * 2 - d))
        d += 1
        if day_start.weekday() >= 5:   # skip weekends
            continue
        built += 1
        base = 20_000.0 + rng.uniform(-300, 300)
        setup = _long_setup_candles(base)
        win = rng.random() < win_rate
        resolution = _resolution_candles(setup[-1].close, win=win)
        day = setup + resolution
        times = [day_start + timedelta(minutes=minutes * i) for i in range(len(day))]
        out.extend(Candle(t, c.open, c.high, c.low, c.close, c.volume)
                   for t, c in zip(times, day))
    return out
