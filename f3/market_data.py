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

    # Anchor walk (close prices). Each leg is several candles so swing fractals
    # form cleanly. Turning points become swing highs/lows.
    legs: list[tuple[float, int]] = [
        (20_000, 1),   # start
        (19_900, 4),   # swing low  L1
        (20_050, 4),   # swing high H1
        (19_950, 4),   # swing low  L2 (higher low)
        (20_120, 5),   # bullish BOS above H1 → bias LONG, new high H2
        (20_010, 4),   # pullback begins, into discount
    ]
    closes: list[float] = []
    prev = legs[0][0]
    for target, steps in legs[1:]:
        for k in range(1, steps + 1):
            closes.append(prev + (target - prev) * k / steps)
        prev = target

    candles: list[Candle] = []
    # Build the trend/pullback candles first (timestamps assigned at the end).
    o = legs[0][0]
    for c in closes:
        up = abs(c - o) * 0.3 + 3
        dn = abs(c - o) * 0.3 + 3
        candles.append(_candle(datetime.now(ET), o, c, up, dn))
        o = c

    # --- The sweep + FVG sequence (FIND) -------------------------------------
    # 1) down candle, 2) sweep candle (wicks below L2=19,950 then closes back
    #    above it), 3) up displacement candle leaving a gap above candle 1's high.
    c1 = _candle(datetime.now(ET), o, 19_980, 4, 6)           # candle[i-1]
    c2 = _candle(datetime.now(ET), 19_980, 19_975, 3, 40)     # sweep: low 19,935
    # force the sweep wick below the swept swing low and close back above it
    c2 = Candle(c2.time, c2.open, 19_984, 19_935, 19_975, c2.volume)
    c3 = _candle(datetime.now(ET), 19_975, 20_030, 8, 3)      # displacement up
    # bullish FVG: c1.high < c3.low  → widen c3's low above c1's high
    c3 = Candle(c3.time, c3.open, max(c3.high, 20_035), c1.high + 2, 20_030, c3.volume)
    candles.extend([c1, c2, c3])

    # --- LTF shift back up (FIRE confirmation) -------------------------------
    o = c3.close
    for target in (20_015, 20_045, 20_075):  # minor pullback then break up
        candles.append(_candle(datetime.now(ET), o, target, 6, 6))
        o = target

    # Assign evenly spaced timestamps ending at `end`.
    times = _times(len(candles), end, minutes)
    return [Candle(t, c.open, c.high, c.low, c.close, c.volume)
            for t, c in zip(times, candles)]
