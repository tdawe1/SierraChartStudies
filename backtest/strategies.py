"""Strategies. Each strategy is a pure function over closed bars.

Convention: `signals[i]` is the signal *after bar i closes* (+1 long,
-1 short, 0 flat) using only bars[0..i]. The engine fills at the next
open by default, so there is no lookahead.

Two strategies ship:

- signal_replay  -- EXACT mode. Replays SignalLong/SignalShort columns
  exported from the chart (see exporter/BacktestExporter.cpp). Use this
  to backtest ANY custom study bit-for-bit as drawn: the chart decides,
  the engine only simulates fills and costs.
- orion_bar      -- SWEEP mode. Standalone port of the Orion trigger math
  (orion_core.h helpers: can_trigger / climax_reached / rebound_hit /
  extreme_broken / in_session) driven by bar-level delta + Numbers Bars
  MaxDelta/MinDelta columns. Setup (stacked VAP absorption) is reduced
  to a directable bar-delta gate, so this is an approximation for fast
  parameter sweeps -- confirm winners with signal_replay.
"""

from __future__ import annotations

try:
    from .engine import Bar
except ImportError:
    from engine import Bar


# --- orion_core.h ports (keep in sync; mirror the C++ semantics) --------

def in_session(t: str, start: str, end: str, enabled: bool) -> bool:
    if not enabled:
        return True
    if start == end:
        return True
    if start < end:
        return start <= t <= end
    return t >= start or t <= end


def trigger_bar_ok(index: int, armed_bar: int, armed_dir: int) -> bool:
    return armed_dir != 0 and armed_bar >= 0 and index > armed_bar


def arm_in_lifetime(index: int, armed_bar: int, lifetime_bars: int) -> bool:
    if lifetime_bars < 1:
        lifetime_bars = 1
    return index - armed_bar <= lifetime_bars


def can_trigger(index: int, armed_bar: int, armed_dir: int, lifetime_bars: int) -> bool:
    return (trigger_bar_ok(index, armed_bar, armed_dir)
            and arm_in_lifetime(index, armed_bar, lifetime_bars))


def bar_delta_supports(ask_minus_bid: float, threshold: float, is_short: bool) -> bool:
    if is_short:
        if ask_minus_bid >= 0:
            return False
        return ask_minus_bid <= -threshold
    if ask_minus_bid <= 0:
        return False
    return ask_minus_bid >= threshold


def climax_reached(extreme_delta: float, climax_min: float, is_short: bool) -> bool:
    if climax_min <= 0:
        return True
    if is_short:
        return extreme_delta >= climax_min
    return extreme_delta <= -climax_min


def rebound_hit(climax_delta: float, current_delta: float, is_short: bool,
                mode: int, rebound_abs: float, rebound_pct: float) -> bool:
    need = rebound_abs
    if mode == 1:
        need = abs(climax_delta) * (rebound_pct / 100.0)
    if need < 0:
        need = 0
    if is_short:
        return current_delta <= climax_delta - need
    return current_delta >= climax_delta + need


def extreme_broken(high: float, low: float, armed_extreme: float,
                   tick_size: float, break_ticks: int, is_short: bool) -> bool:
    if break_ticks <= 0:
        return False
    pad = break_ticks * tick_size
    if is_short:
        return high > armed_extreme + pad
    return low < armed_extreme - pad


def entry_gates_pass(b: Bar, tick_size: float, require_relvol_above: float,
                     block_atr_above_mult: float) -> bool:
    """Volatility/volume entry gates. Zeros = off (always passes).

    require_relvol_above: minimum RelVol50 (1.0 = average volume).
    block_atr_above_mult: ATR ceiling in multiples of tick size.
    """
    if require_relvol_above > 0 and b.relvol < require_relvol_above:
        return False
    if (block_atr_above_mult > 0 and tick_size > 0
            and (b.atr / tick_size) > block_atr_above_mult):
        return False
    return True


# --- strategies -----------------------------------------------------------

def signal_replay(bars: list[Bar], **kw) -> list[int]:
    """Replay chart-exported signals. Long wins ties (matches engine)."""
    out = []
    for b in bars:
        s = 0
        if b.signal_long:
            s = 1
        elif b.signal_short:
            s = -1
        out.append(s)
    return out


def orion_bar(bars: list[Bar],
              tick_size: float = 0.25,
              setup_min_delta: float = 0.0,
              climax_min: float = 0.0,
              rebound_mode: int = 1,
              rebound_abs: float = 100.0,
              rebound_pct: float = 50.0,
              lifetime_bars: int = 3,
              invalidate_ticks: int = 0,
              allow_long: bool = True,
              allow_short: bool = True,
              session_enabled: bool = False,
              session_start: str = "08:30",
              session_end: str = "15:00",
              require_relvol_above: float = 0.0,
              block_atr_above_mult: float = 0.0,
              **kw) -> list[int]:
    """Approximate Orion trigger on bar-level columns.

    Setup: a with-trend bar whose |delta| clears setup_min_delta arms the
    direction at the bar extreme (high for shorts, low for longs).
    Trigger: within lifetime, a max/min-delta climax followed by a
    rebound of the bar delta. Invalidation breaks the arm.
    """
    sigs = [0] * len(bars)
    armed_dir = 0
    armed_bar = -1
    armed_extreme = 0.0
    armed_climax = 0.0

    for i, b in enumerate(bars):
        t = b.stamp[11:16] if len(b.stamp) >= 16 else ""
        if not in_session(t, session_start, session_end, session_enabled):
            armed_dir, armed_bar = 0, -1
            continue
        delta = b.askvol - b.bidvol
        is_short = armed_dir < 0

        # trigger check comes first (a rebound can fire on the break bar)
        if can_trigger(i, armed_bar, armed_dir, lifetime_bars):
            extreme = b.maxdelta if is_short else b.mindelta
            if climax_reached(extreme, climax_min, is_short) and rebound_hit(
                    armed_climax if armed_climax else extreme, delta,
                    is_short, rebound_mode, rebound_abs, rebound_pct):
                if (armed_dir > 0 and allow_long) or (armed_dir < 0 and allow_short):
                    if entry_gates_pass(b, tick_size, require_relvol_above,
                                        block_atr_above_mult):
                        sigs[i] = armed_dir
                        armed_dir, armed_bar = 0, -1
                        continue
                    # gate blocked: no signal, but keep the arm alive and
                    # track the climax like a non-trigger bar
                    if is_short and extreme > armed_climax:
                        armed_climax = extreme
                    elif not is_short and extreme < armed_climax:
                        armed_climax = extreme
                    continue
            if extreme_broken(b.high, b.low, armed_extreme, tick_size,
                              invalidate_ticks, is_short):
                armed_dir, armed_bar = 0, -1
            elif not arm_in_lifetime(i, armed_bar, lifetime_bars):
                armed_dir, armed_bar = 0, -1
            else:
                # track the strongest climax seen while armed
                if is_short and extreme > armed_climax:
                    armed_climax = extreme
                elif not is_short and extreme < armed_climax:
                    armed_climax = extreme
                continue

        # setup: arm one direction (stronger delta wins ties to short? no:
        # long wins ties -- mirror pick_setup_direction's bar_delta>=0 rule)
        long_ok = allow_long and bar_delta_supports(delta, setup_min_delta, False)
        short_ok = allow_short and bar_delta_supports(delta, setup_min_delta, True)
        if long_ok or short_ok:
            armed_dir = -1 if (short_ok and not long_ok) or (
                long_ok and short_ok and delta < 0) else 1
            if armed_dir < 0 and not short_ok:
                armed_dir = 1
            if armed_dir > 0 and not long_ok:
                armed_dir = -1
            armed_bar = i
            armed_extreme = b.high if armed_dir < 0 else b.low
            armed_climax = b.maxdelta if armed_dir < 0 else b.mindelta
    return sigs


STRATEGIES = {"signal_replay": signal_replay, "orion_bar": orion_bar}
