"""
ICT structure & liquidity primitives — the mechanical guts of F3.

This module turns a series of candles into the objects the strategy reasons
about: swing points, break of structure (bias), liquidity sweeps, fair value
gaps, order blocks, the premium/discount dealing range, and a lower-timeframe
market-structure shift used as the FIRE confirmation. `analyze()` ties them
together into a single F3Analysis the agents grade.

The implementation is deliberately mechanical and dependency-free (pure stdlib)
so the same rules run identically every time — "boring on purpose".
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .models import Candle, Direction


# --------------------------------------------------------------------------- #
# Swing points
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Swing:
    index: int
    price: float
    is_high: bool


def swing_points(candles: list[Candle], width: int) -> list[Swing]:
    """
    Fractal swing highs/lows: a high is a swing high if its high is strictly
    greater than the `width` candles on each side (mirror for lows).
    """
    swings: list[Swing] = []
    n = len(candles)
    for i in range(width, n - width):
        hi = candles[i].high
        lo = candles[i].low
        is_high = all(candles[i - j].high < hi and candles[i + j].high < hi
                      for j in range(1, width + 1))
        is_low = all(candles[i - j].low > lo and candles[i + j].low > lo
                     for j in range(1, width + 1))
        if is_high:
            swings.append(Swing(i, hi, True))
        if is_low:
            swings.append(Swing(i, lo, False))
    return swings


# --------------------------------------------------------------------------- #
# Break of structure → higher-timeframe bias (FRAME)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class StructureBreak:
    index: int        # candle that broke structure (close beyond the swing)
    level: float      # the swing level that was broken
    direction: Direction


def structure_breaks(candles: list[Candle], width: int) -> list[StructureBreak]:
    """
    Walk the candles tracking the most recent confirmed swing high/low. A close
    above the active swing high is a bullish BOS; a close below the active swing
    low is a bearish BOS. Returns every break in order.
    """
    swings = swing_points(candles, width)
    # Map the candle index at which each swing becomes "confirmed" (width bars later).
    highs = [(s.index + width, s.price) for s in swings if s.is_high]
    lows = [(s.index + width, s.price) for s in swings if not s.is_high]

    breaks: list[StructureBreak] = []
    active_high: float | None = None
    active_low: float | None = None
    hi_ptr = lo_ptr = 0

    for i, c in enumerate(candles):
        # Promote any swings confirmed up to and including bar i.
        while hi_ptr < len(highs) and highs[hi_ptr][0] <= i:
            active_high = highs[hi_ptr][1]
            hi_ptr += 1
        while lo_ptr < len(lows) and lows[lo_ptr][0] <= i:
            active_low = lows[lo_ptr][1]
            lo_ptr += 1

        if active_high is not None and c.close > active_high:
            breaks.append(StructureBreak(i, active_high, Direction.LONG))
            active_high = None  # consumed; wait for the next swing high
        if active_low is not None and c.close < active_low:
            breaks.append(StructureBreak(i, active_low, Direction.SHORT))
            active_low = None
    return breaks


def htf_bias(candles: list[Candle], width: int) -> tuple[Direction, StructureBreak | None]:
    """Bias = direction of the last clear break of structure."""
    breaks = structure_breaks(candles, width)
    if not breaks:
        return Direction.NEUTRAL, None
    last = breaks[-1]
    return last.direction, last


# --------------------------------------------------------------------------- #
# Premium / discount dealing range
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DealingRange:
    low: float
    high: float

    @property
    def equilibrium(self) -> float:
        return (self.low + self.high) / 2.0

    def is_discount(self, price: float) -> bool:
        return price < self.equilibrium

    def is_premium(self, price: float) -> bool:
        return price > self.equilibrium


def dealing_range(candles: list[Candle], width: int) -> DealingRange | None:
    """The most recent swing low → swing high pair defines the dealing range."""
    swings = swing_points(candles, width)
    last_high = next((s for s in reversed(swings) if s.is_high), None)
    last_low = next((s for s in reversed(swings) if not s.is_high), None)
    if last_high is None or last_low is None:
        return None
    return DealingRange(low=last_low.price, high=last_high.price)


# --------------------------------------------------------------------------- #
# Liquidity sweep (FIND)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class LiquiditySweep:
    index: int             # candle that swept the level
    level: float           # the swing level whose stops were taken
    extreme: float         # the wick extreme beyond the level
    side: Direction        # LONG  = sell-side swept (good for longs)
    #                        SHORT = buy-side swept (good for shorts)


def find_sweep(candles: list[Candle], width: int, lookback: int,
               bias: Direction, require_extreme: bool = False,
               extreme_tol_frac: float = 0.25) -> LiquiditySweep | None:
    """
    Look for the most recent run on an obvious high/low (where stops sit) that
    was rejected — price wicks beyond a prior swing then closes back inside.

    For a LONG bias we want sell-side liquidity below taken (a swing low swept
    then reclaimed); for a SHORT bias, buy-side liquidity above.

    With `require_extreme`, only count it as a real liquidity raid if the swept
    level sits near the bottom (longs) / top (shorts) of the recent swing range —
    i.e. an *obvious* level where stops actually pile up, not a tiny interior wiggle.
    """
    swings = swing_points(candles, width)
    n = len(candles)
    start = max(width, n - lookback)

    def near_extreme(level: float, prior: list[Swing], low_side: bool) -> bool:
        if not require_extreme:
            return True
        lows = [s.price for s in prior if not s.is_high]
        highs = [s.price for s in prior if s.is_high]
        if not lows or not highs:
            return True
        rng = max(highs) - min(lows)
        if rng <= 0:
            return True
        band = extreme_tol_frac * rng
        if low_side:
            return level <= min(lows) + band
        return level >= max(highs) - band

    best: LiquiditySweep | None = None
    for i in range(start, n):
        c = candles[i]
        prior = [s for s in swings if s.index < i]
        if bias in (Direction.LONG, Direction.NEUTRAL):
            for s in prior:
                if not s.is_high and c.low < s.price <= c.close \
                        and near_extreme(s.price, prior, low_side=True):
                    best = LiquiditySweep(i, s.price, c.low, Direction.LONG)
        if bias in (Direction.SHORT, Direction.NEUTRAL):
            for s in prior:
                if s.is_high and c.high > s.price >= c.close \
                        and near_extreme(s.price, prior, low_side=False):
                    best = LiquiditySweep(i, s.price, c.high, Direction.SHORT)
    return best


# --------------------------------------------------------------------------- #
# Fair value gap & order block (FIND — the entry zone)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Zone:
    low: float
    high: float
    kind: str          # "FVG" or "OB"
    index: int
    direction: Direction

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2.0


def fair_value_gaps(candles: list[Candle], lookback: int) -> list[Zone]:
    """
    3-candle FVG. Bullish: candle[i-1].high < candle[i+1].low (gap below price).
    Bearish: candle[i-1].low > candle[i+1].high (gap above price).
    """
    zones: list[Zone] = []
    n = len(candles)
    start = max(1, n - lookback)
    for i in range(start, n - 1):
        a, c = candles[i - 1], candles[i + 1]
        if a.high < c.low:
            zones.append(Zone(a.high, c.low, "FVG", i, Direction.LONG))
        elif a.low > c.high:
            zones.append(Zone(c.high, a.low, "FVG", i, Direction.SHORT))
    return zones


def order_blocks(candles: list[Candle], lookback: int) -> list[Zone]:
    """
    Order block: the last opposite-colour candle before a displacement that
    breaks the prior candle's range. Bullish OB = last down candle before an up
    move; bearish OB = last up candle before a down move.
    """
    zones: list[Zone] = []
    n = len(candles)
    start = max(1, n - lookback)
    for i in range(start, n - 1):
        prev, nxt = candles[i], candles[i + 1]
        if not prev.is_bullish and nxt.is_bullish and nxt.close > prev.high:
            zones.append(Zone(prev.low, prev.high, "OB", i, Direction.LONG))
        elif prev.is_bullish and not nxt.is_bullish and nxt.close < prev.low:
            zones.append(Zone(prev.low, prev.high, "OB", i, Direction.SHORT))
    return zones


def entry_zone(candles: list[Candle], lookback: int, bias: Direction,
               dr: DealingRange | None, min_zone_frac: float = 0.0) -> Zone | None:
    """
    The setup zone: the most recent FVG/OB aligned with bias that sits in the
    discount (for longs) or premium (for shorts) half of the dealing range, and
    is at least `min_zone_frac` of price wide (filters out noise-sized gaps).
    """
    if bias is Direction.NEUTRAL:
        return None
    candidates = [z for z in (fair_value_gaps(candles, lookback)
                              + order_blocks(candles, lookback))
                  if z.direction is bias]
    if min_zone_frac > 0 and candles:
        price = candles[-1].close
        candidates = [z for z in candidates
                      if (z.high - z.low) >= min_zone_frac * price]
    if dr is not None:
        if bias is Direction.LONG:
            candidates = [z for z in candidates if dr.is_discount(z.mid)]
        else:
            candidates = [z for z in candidates if dr.is_premium(z.mid)]
    if not candidates:
        return None
    return max(candidates, key=lambda z: z.index)  # most recent


# --------------------------------------------------------------------------- #
# Lower-timeframe market-structure shift (FIRE confirmation)
# --------------------------------------------------------------------------- #
def ltf_shift(candles: list[Candle], width: int, after_index: int,
              bias: Direction) -> bool:
    """
    After the sweep, did price shift structure back in the bias direction on the
    lower timeframe? Approximated as a break of a minor swing (small fractal)
    occurring at or after the sweep candle.
    """
    if bias is Direction.NEUTRAL:
        return False
    for sb in structure_breaks(candles, width):
        if sb.index >= after_index and sb.direction is bias:
            return True
    return False


# --------------------------------------------------------------------------- #
# Full analysis
# --------------------------------------------------------------------------- #
@dataclass
class F3Analysis:
    market: str
    candles: list[Candle]
    bias: Direction
    last_break: StructureBreak | None
    dealing_range: DealingRange | None
    sweep: LiquiditySweep | None
    zone: Zone | None
    ltf_confirmed: bool
    in_discount_premium: bool
    notes: list[str] = field(default_factory=list)

    @property
    def last_price(self) -> float:
        return self.candles[-1].close


def analyze(market: str, candles: list[Candle], *, htf_width: int,
            ltf_width: int, lookback: int, min_zone_frac: float = 0.0,
            require_extreme_sweep: bool = False,
            extreme_tol_frac: float = 0.25) -> F3Analysis:
    """Run the full FRAME→FIND chain and return everything the agents grade."""
    bias, last_break = htf_bias(candles, htf_width)
    dr = dealing_range(candles, htf_width)
    sweep = find_sweep(candles, htf_width, lookback, bias,
                       require_extreme=require_extreme_sweep,
                       extreme_tol_frac=extreme_tol_frac)
    zone = entry_zone(candles, lookback, bias, dr, min_zone_frac=min_zone_frac)

    ltf_confirmed = False
    if sweep is not None:
        ltf_confirmed = ltf_shift(candles, ltf_width, sweep.index, bias)

    in_dp = False
    if zone is not None and dr is not None:
        in_dp = (dr.is_discount(zone.mid) if bias is Direction.LONG
                 else dr.is_premium(zone.mid))

    return F3Analysis(
        market=market,
        candles=candles,
        bias=bias,
        last_break=last_break,
        dealing_range=dr,
        sweep=sweep,
        zone=zone,
        ltf_confirmed=ltf_confirmed,
        in_discount_premium=in_dp,
    )
