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


def _rolling_mean_stdev(xs: list[float]) -> tuple[float, float]:
    """Sample mean/stdev (ddof=1); mirrors RollingMeanStdev in
    MeanReversionOU.cpp."""
    n = len(xs)
    mean = sum(xs) / n
    var = sum((x - mean) ** 2 for x in xs) / (n - 1)
    return mean, var ** 0.5


def _ou_half_life(closes: list[float], mean: float) -> float:
    """AR(1) half-life in bars; -1 when undefined. Mirrors HalfLifeBars."""
    n = len(closes)
    if n < 10:
        return -1.0
    sxx = sxy = 0.0
    for k in range(1, n):
        x = closes[k - 1] - mean
        sxx += x * x
        sxy += x * (closes[k] - closes[k - 1])
    if sxx <= 0.0:
        return -1.0
    lam = sxy / sxx
    if lam >= 0.0:
        return -1.0
    return -0.6931471805599453 / lam


def mrou_ou(bars: list[Bar],
            tick_size: float = 1.0,
            lookback: int = 60,
            entry_z: float = 2.0,
            exit_z: float = 0.5,
            min_stdev_ticks: float = 2.0,
            max_hold_bars: int = 0,
            hl_mult: float = 2.0,
            hl_min: int = 2,
            hl_max: int = 200,
            use_session: bool = False,
            session_start: str = "08:30",
            session_end: str = "15:00",
            allow_long: bool = True,
            allow_short: bool = True) -> list[int]:
    """Study-faithful port of MeanReversionOU.cpp (single-asset OU /
    z-score mean reversion with half-life time-stop and stdev-floor
    gates). Emits the study's own Buy/Sell entry markers (+1/-1) so it
    replays through the engine exactly like chart-exported signals via
    signal_replay. Exactness gate: on the chart window the marker bars
    must equal the exporter's SignalLong/SignalShort columns bit-for-bit
    (see tests/test_mrou.py); only then sweep on bulk data.
    """
    import math
    sigs = [0] * len(bars)
    if lookback < 2:
        return sigs
    closes = [b.close for b in bars]
    sd_floor = min_stdev_ticks * tick_size
    pos = 0
    entry_bar = -1
    hold_limit = 0
    for i, b in enumerate(bars):
        if i < lookback - 1:
            continue
        win = closes[i - lookback + 1:i + 1]
        mean, sd = _rolling_mean_stdev(win)
        hl = _ou_half_life(win, mean)
        healthy = (hl > 0.0 and hl_min <= hl <= hl_max
                   and sd >= sd_floor and sd > 0.0)
        if sd <= 0.0:
            continue
        z = (b.close - mean) / sd
        t = b.stamp[11:16] if len(b.stamp) >= 16 else ""
        session_ok = in_session(t, session_start, session_end, use_session)
        if pos != 0:
            mean_touched = (pos > 0 and z >= -exit_z) or \
                (pos < 0 and z <= exit_z)
            opposite = (pos > 0 and z >= entry_z) or \
                (pos < 0 and z <= -entry_z)
            timed_out = hold_limit > 0 and (i - entry_bar) >= hold_limit
            if mean_touched or timed_out or opposite:
                pos, entry_bar, hold_limit = 0, -1, 0
                if not opposite:
                    continue
            else:
                continue
        if not healthy or not session_ok:
            continue
        hold = max_hold_bars
        if hold <= 0:
            hold = math.ceil(hl * hl_mult)
            if hold < hl_min:
                hold = hl_min
            if hold > hl_max * 2:
                hold = hl_max * 2
        if allow_long and z <= -entry_z:
            pos, entry_bar, hold_limit = 1, i, hold
            sigs[i] = 1
        elif allow_short and z >= entry_z:
            pos, entry_bar, hold_limit = -1, i, hold
            sigs[i] = -1
    return sigs


def statarb_spread(bars1: list[Bar], bars2: list[Bar],
                   hedge_lookback: int = 60,
                   z_lookback: int = 60,
                   entry_z: float = 2.0,
                   exit_z: float = 0.5,
                   min_corr: float = 0.7,
                   min_stdev_ticks: float = 2.0,
                   leg1_tick: float = 0.25,
                   hl_min: int = 2,
                   hl_max: int = 200,
                   hl_mult: float = 2.0,
                   max_hold_bars: int = 0,
                   allow_long: bool = True,
                   allow_short: bool = True) -> list[dict]:
    """Study-faithful port of StatArbPairs.cpp (rolling-OLS hedge, z-scored
    spread, correlation + half-life gates, same exit state machine).
    Leg2 joins on bar stamp (same period/session required, as on-chart).
    Returns spread TRADES (the study carries its own exits; no engine
    stop/target applies): each has dir (+1 = long leg1 / short leg2),
    entry/exit bar indices into bars1, and the entry beta. Fills price at
    the next open of each leg (see pairs_trade_pnl); a signal on the last
    bar is dropped (no fill). No chart truth exists (never wired), so this
    is approximation-tier: math verified by synthetic unit tests.
    """
    import math
    trades: list[dict] = []
    leg2_by_stamp = {b.stamp: b.close for b in bars2}
    closes1 = [b.close for b in bars1]
    need = max(hedge_lookback, z_lookback)
    sd_floor = min_stdev_ticks * leg1_tick
    pos = 0
    entry_idx = -1
    entry_beta = 0.0
    hold_limit = 0

    def leg2_window(end: int, length: int, offset: int):
        out = []
        for k in range(length):
            c = leg2_by_stamp.get(bars1[end - length + 1 + k].stamp)
            if c is None:
                return None
            out.append(c)
        return out

    for i in range(len(bars1)):
        if i < need - 1:
            continue
        win1 = closes1[i - hedge_lookback + 1:i + 1]
        leg2h = leg2_window(i, hedge_lookback, need - hedge_lookback)
        if leg2h is None:
            continue
        mx = sum(leg2h) / hedge_lookback
        my = sum(win1) / hedge_lookback
        sxx = sxy = syy = 0.0
        for x0, y0 in zip(leg2h, win1):
            dx, dy = x0 - mx, y0 - my
            sxx += dx * dx
            sxy += dx * dy
            syy += dy * dy
        if sxx <= 0.0 or syy <= 0.0:
            continue
        beta = sxy / sxx
        intercept = my - beta * mx
        corr = sxy / math.sqrt(sxx * syy)
        zoff_win = closes1[i - z_lookback + 1:i + 1]
        leg2z = leg2_window(i, z_lookback, need - z_lookback)
        if leg2z is None:
            continue
        spreads = [a - (beta * b + intercept)
                   for a, b in zip(zoff_win, leg2z)]
        smean = sum(spreads) / z_lookback
        ssd = (sum((s - smean) ** 2 for s in spreads) /
               (z_lookback - 1)) ** 0.5 if z_lookback > 1 else 0.0
        if ssd <= 0.0:
            continue
        z = (spreads[-1] - smean) / ssd
        hxx = hxy = 0.0
        for k in range(1, z_lookback):
            prev = spreads[k - 1] - smean
            hxx += prev * prev
            hxy += prev * (spreads[k] - spreads[k - 1])
        hl = -1.0
        if hxx > 0.0:
            lam = hxy / hxx
            if lam < 0.0:
                hl = -0.6931471805599453 / lam
        healthy = (corr >= min_corr and ssd >= sd_floor
                   and hl > 0.0 and hl_min <= hl <= hl_max)
        if pos != 0:
            mean_touched = (pos > 0 and z >= -exit_z) or \
                (pos < 0 and z <= exit_z)
            opposite = (pos > 0 and z >= entry_z) or \
                (pos < 0 and z <= -entry_z)
            timed_out = hold_limit > 0 and (i - entry_idx) >= hold_limit
            if mean_touched or timed_out or opposite:
                if i + 1 < len(bars1):
                    trades.append({"dir": pos, "entry": entry_idx,
                                   "exit": i, "beta": entry_beta})
                pos, entry_idx, hold_limit = 0, -1, 0
                if not opposite:
                    continue
            else:
                continue
        if not healthy:
            continue
        hold = max_hold_bars
        if hold <= 0:
            hold = math.ceil(hl * hl_mult)
            hold = min(max(hold, hl_min), hl_max * 2)
        if allow_long and z <= -entry_z:
            pos, entry_idx, entry_beta, hold_limit = 1, i, beta, hold
        elif allow_short and z >= entry_z:
            pos, entry_idx, entry_beta, hold_limit = -1, i, beta, hold
    return trades


def pairs_trade_pnl(trade: dict, bars1: list[Bar], bars2: list[Bar],
                    leg1_usd_per_pt: float, leg2_usd_per_pt: float,
                    fee_per_side: float = 2.10,
                    slip_ticks: float = 1.0,
                    leg1_tick_value: float = 12.50,
                    leg2_tick_value: float = 5.0) -> float:
    """$ P&L of one spread trade, filled at next opens, leg2 sized
    dollar-neutral per leg1 point (fractional: live rounds to lots).
    """
    leg2_by_stamp = {b.stamp: b for b in bars2}
    e1, x1 = bars1[trade["entry"]], bars1[trade["exit"]]
    e2 = leg2_by_stamp.get(e1.stamp)
    x2 = leg2_by_stamp.get(x1.stamp)
    if e2 is None or x2 is None:
        return 0.0
    e1o = bars1[trade["entry"] + 1].open if trade["entry"] + 1 < len(bars1) \
        else e1.close
    x1o = bars1[trade["exit"] + 1].open if trade["exit"] + 1 < len(bars1) \
        else x1.close
    je = next((k for k, b in enumerate(bars2) if b.stamp == e1.stamp), None)
    jx = next((k for k, b in enumerate(bars2) if b.stamp == x1.stamp), None)
    e2o = bars2[je + 1].open if je is not None and je + 1 < len(bars2) \
        else e2.close
    x2o = bars2[jx + 1].open if jx is not None and jx + 1 < len(bars2) \
        else x2.close
    beta = trade["beta"]
    q2 = beta * (leg1_usd_per_pt / leg2_usd_per_pt)
    gross = trade["dir"] * ((x1o - e1o) - beta * (x2o - e2o)) \
        * leg1_usd_per_pt
    costs = 2.0 * (fee_per_side + slip_ticks * leg1_tick_value) + \
        2.0 * abs(q2) * (fee_per_side + slip_ticks * leg2_tick_value)
    return gross - costs


def pairs_metrics(pnls: list[float]) -> dict:
    """Minimal trade-list metrics (mirrors the engine's headline keys)."""
    n = len(pnls)
    if not n:
        return {"trades": 0, "total_pnl": 0.0, "win_rate": 0.0,
                "profit_factor": 0.0, "max_drawdown": 0.0,
                "expectancy": 0.0}
    wins = [p for p in pnls if p > 0]
    losses = [-p for p in pnls if p <= 0]
    cum = peak = 0.0
    maxdd = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        maxdd = min(maxdd, cum - peak)
    gross_loss = sum(losses)
    return {"trades": n, "total_pnl": cum,
            "win_rate": len(wins) / n,
            "profit_factor": sum(wins) / gross_loss if gross_loss else None,
            "max_drawdown": maxdd, "expectancy": cum / n}


def _value_area_70(bucket_vol: list[float]) -> tuple[int, int, int]:
    """(poc, vah, val) bucket indices; mirrors ValueArea70 (first-max POC,
    expand toward the heavier side, ties go up)."""
    nb = len(bucket_vol)
    poc = 0
    total = 0.0
    for b, v in enumerate(bucket_vol):
        total += v
        if v > bucket_vol[poc]:
            poc = b
    up = dn = poc
    inside = bucket_vol[poc]
    need = total * 0.70
    while inside < need and (up + 1 < nb or dn - 1 >= 0):
        up_vol = bucket_vol[up + 1] if up + 1 < nb else -1.0
        dn_vol = bucket_vol[dn - 1] if dn - 1 >= 0 else -1.0
        if up_vol >= dn_vol:
            up += 1
            inside += bucket_vol[up]
        else:
            dn -= 1
            inside += bucket_vol[dn]
    return poc, up, dn


def _bar_minutes(stamp: str) -> int:
    """Minutes since midnight, tolerant of single/double-space stamps."""
    parts = stamp.split()
    hhmm = parts[1][:5] if len(parts) > 1 else "00:00"
    return int(hhmm[:2]) * 60 + int(hhmm[3:5])

def orb_retrace(bars: list[Bar],
                tick_size: float = 0.25,
                or_start_min: int = 570,
                or_end_min: int = 585,
                breakout_ticks: int = 8,
                vp_level: int = 0,
                entry_cutoff_min: int = 720,
                eod_flat_min: int = 955,
                use_shorts: bool = True,
                trade_days: tuple = (0, 1, 2, 3, 4)) -> list[dict]:
    """Study-faithful port of ORBRetrace.cpp (opening-range breakout +
    volume-profile retrace, per-day state machine). Returns TRADES with
    faithful fills (see orb_trade_pnl). trade_days gates ENTRIES by weekday
    (Mon=0; default all): validated Wed/Thu-only IS+OOS, PF 2.0-3.0.
    """
    trades: list[dict] = []
    day = None
    phase = 0  # 0 collect, 1 armed, 2 wait-long, 3 wait-short, 6 done
    pos = 0
    or_start_idx = -1
    or_end_idx = -1
    or_high = or_low = 0.0
    stop = target = 0.0
    def close_trade(i: int, price: float, reason: str):
        trades.append({"dir": pos, "entry": entry_idx,
                       "entry_price": entry_price, "exit": i,
                       "exit_price": price, "reason": reason,
                       "stop": stop, "target": target})

    for i, b in enumerate(bars):
        d = b.stamp[:10]
        mins = _bar_minutes(b.stamp)
        if d != day:
            if pos != 0:
                close_trade(i, b.open, "stale-flat")
            day = d
            phase = 0
            pos = 0
            or_start_idx, or_end_idx = -1, -1
            or_high = or_low = 0.0
        if or_start_min <= mins < or_end_min:
            if or_start_idx < 0:
                or_start_idx, or_high, or_low = i, b.high, b.low
            else:
                or_high, or_low = max(or_high, b.high), min(or_low, b.low)
            or_end_idx = i
            continue
        if or_start_idx < 0 or or_end_idx < or_start_idx:
            continue
        n_or = or_end_idx - or_start_idx + 1
        if n_or < 2:
            continue
        n_buckets = int((or_high - or_low) / tick_size) + 1
        if n_buckets < 1 or n_buckets > 4096:
            continue
        buckets = [0.0] * n_buckets
        for k in range(or_start_idx, or_end_idx + 1):
            bk = bars[k]
            v = bk.volume / n_or
            lo = max(int((bk.low - or_low) / tick_size), 0)
            hi = min(int((bk.high - or_low) / tick_size), n_buckets - 1)
            for bb in range(lo, hi + 1):
                buckets[bb] += v / (hi - lo + 1)
        poc_b, vah_b, val_b = _value_area_70(buckets)
        poc = or_low + (poc_b + 0.5) * tick_size
        vah = min(or_low + (vah_b + 0.5) * tick_size, or_high)
        val = max(or_low + (val_b + 0.5) * tick_size, or_low)
        level = poc if vp_level == 0 else (vah if vp_level == 1 else val)
        thr = breakout_ticks * tick_size
        if pos == 1:
            if b.low <= stop or b.high >= target or mins >= eod_flat_min:
                if b.low <= stop:
                    price, reason = stop, "stop"
                elif b.high >= target:
                    price, reason = target, "target"
                else:
                    price, reason = b.close, "eod"
                close_trade(i, price, reason)
                pos, phase = 0, 6
            continue
        if pos == -1:
            if b.high >= stop or b.low <= target or mins >= eod_flat_min:
                if b.high >= stop:
                    price, reason = stop, "stop"
                elif b.low <= target:
                    price, reason = target, "target"
                else:
                    price, reason = b.close, "eod"
                close_trade(i, price, reason)
                pos, phase = 0, 6
            continue
        if phase == 6 or mins >= eod_flat_min:
            phase = 6
            continue
        if phase == 0:
            phase = 1
        if phase == 1:
            if b.close > or_high + thr:
                phase = 2
            elif b.close < or_low - thr:
                phase = 3
            continue
        if mins >= entry_cutoff_min:
            continue
        if phase == 2:
            if b.low <= level and _weekday(b.stamp) in trade_days:
                pos, phase = 1, 4
                stop = or_low
                target = or_high + (or_high - level)
                entry_idx, entry_price = i, level
        elif phase == 3:
            if not use_shorts:
                phase = 6
                continue
            if b.high >= level and _weekday(b.stamp) in trade_days:
                pos, phase = -1, 5
                stop = or_high
                target = or_low - (level - or_low)
                entry_idx, entry_price = i, level
    return trades


def orb_trade_pnl(trade: dict, usd_per_pt: float,
                  fee_per_side: float = 2.10) -> float:

    """$ P&L of one ORB trade at its faithful fill prices (fees only;
    limits/stops assumed touched-at-price)."""
    gross = trade["dir"] * (trade["exit_price"] - trade["entry_price"]) \
        * usd_per_pt
    return gross - 2.0 * fee_per_side


def _weekday(stamp: str) -> int:
    """Monday=0..Sunday=6 from the bar date (chart-time stamps)."""
    from datetime import datetime
    return datetime.strptime(stamp[:10], "%Y-%m-%d").weekday()


def monday_dip(bars: list[Bar],
               trend_len: int = 200,
               dip_factor: float = 0.97,
               exit_bars: int = 10,
               roc_len: int = 5) -> list[int]:
    """Study-faithful port of MondayDipBuy.cpp (Monday washout above a
    rising slow SMA; bounce-or-time exits). Long-only; exits arrive as -1
    with the engine recipe: stops/targets OFF, allow both sides (exits
    need it), exit_on_opposite. No chart truth (silent 20-day window),
    so approximation-tier: math verified by synthetic unit tests.
    NOTE: designed for daily bars (a -3% 10-min bar is a crash); run on
    *-daily.csv, not intraday.
    """
    sigs = [0] * len(bars)
    need = max(trend_len, roc_len + 1)
    closes = [b.close for b in bars]
    pos = 0
    entry_bar = -1
    for i, b in enumerate(bars):
        if i < need:
            continue
        sma_cur = sum(closes[i - trend_len + 1:i + 1]) / trend_len
        sma_prev = sum(closes[i - trend_len:i]) / trend_len
        if pos != 0:
            bounced = i >= 2 and b.close > closes[i - 2]
            if bounced or (i - entry_bar) >= exit_bars:
                pos, entry_bar = 0, -1
                sigs[i] = -1
            continue
        if (_weekday(b.stamp) == 0
                and b.close < dip_factor * b.open
                and b.close > sma_prev
                and sma_cur > sma_prev):
            pos, entry_bar = 1, i
            sigs[i] = 1
    return sigs


def golden_cross(bars: list[Bar],
                 fast_len: int = 50,
                 slow_len: int = 200,
                 max_extension: float = 1.05,
                 index_slow_len: int = 0) -> list[int]:
    """Study-faithful port of GoldenCrossRegime.cpp with a self-index
    regime (close above its own slow SMA; the chart variant reads a second
    chart, which also explains its silent unconfigured window). Long-only;
    death-cross exits arrive as -1 (engine recipe per the study docstring).
    Designed for daily bars. Approximation-tier: synthetic-tested.
    index_slow_len 0 follows slow_len (short histories).
    """
    sigs = [0] * len(bars)
    if index_slow_len <= 0:
        index_slow_len = slow_len
    need = max(slow_len, fast_len, index_slow_len)
    closes = [b.close for b in bars]

    def sma(end: int, length: int) -> float:
        return sum(closes[end - length + 1:end + 1]) / length

    pos = 0
    for i, b in enumerate(bars):
        if i < need:
            continue
        idx_sma = sma(i, index_slow_len)
        regime_ok = b.close > idx_sma
        fast_cur, fast_prev = sma(i, fast_len), sma(i - 1, fast_len)
        slow_cur, slow_prev = sma(i, slow_len), sma(i - 1, slow_len)
        golden = fast_prev <= slow_prev and fast_cur > slow_cur
        death = fast_prev >= slow_prev and fast_cur < slow_cur
        if pos != 0:
            if death:
                pos = 0
                sigs[i] = -1
            continue
        if regime_ok and golden and b.close < max_extension * slow_cur:
            pos = 1
            sigs[i] = 1
    return sigs

FP_EPSILON = 1e-4


def _scof_streak(levels: list[tuple[int, float, float]], is_bullish: bool,
                 min_growth_pct: float, diag_ratio_pct: float,
                 counter_delta_mag: float, enable_tapering: bool,
                 enable_diag: bool, max_levels: int) -> dict:
    """Tick-contiguous opposite-delta streak walk; mirrors
    ScanAbsorptionStreak (Fixes A4/A5, B7/B8, 2, 3, 6). levels sorted
    ascending by price_ticks as (ticks, bid, ask)."""
    out = {"consec": 0, "med_growth": 0.0, "max_diag": 0.0,
           "tap_ok": not enable_tapering, "diag_ok": not enable_diag,
           "absorbed": 0.0, "ratio_pct": 0.0}
    n = len(levels)
    if n == 0:
        return out
    bar_vol = sum(b + a for _, b, a in levels)
    populated = sum(1 for _, b, a in levels if b + a > 0.0)
    order = range(n) if is_bullish else range(n - 1, -1, -1)
    growths: list[float] = []
    consec = 0
    prev_agg = 0.0
    prev_ticks = 0
    have_prev = False
    diag_met = False
    absorbed = 0.0
    for li in order:
        ticks, bid, ask = levels[li]
        if bid == 0.0 and ask == 0.0:
            break
        if have_prev:
            want = prev_ticks + 1 if is_bullish else prev_ticks - 1
            if ticks != want:
                break
        agg = bid if is_bullish else ask
        delta = ask - bid
        counter = (delta <= -counter_delta_mag) if is_bullish \
            else (delta >= counter_delta_mag)
        if not counter:
            break
        consec += 1
        absorbed += agg
        if enable_tapering and prev_agg > 0.0 and agg > 0.0 and len(growths) < 64:
            growths.append((agg - prev_agg) / prev_agg * 100.0)
        if agg > 0.0:
            prev_agg = agg
        if enable_diag:
            ni = li + 1 if is_bullish else li - 1
            if 0 <= ni < n:
                nt, nb, na = levels[ni]
                want_next = ticks + 1 if is_bullish else ticks - 1
                nxt = na if is_bullish else nb
                if nt == want_next and nxt > 0.0:
                    ratio = agg / nxt * 100.0
                    out["max_diag"] = max(out["max_diag"], ratio)
                    if ratio + FP_EPSILON >= diag_ratio_pct:
                        diag_met = True
        prev_ticks, have_prev = ticks, True
    out["consec"] = consec
    out["absorbed"] = absorbed
    if growths:
        growths.sort()
        m = len(growths)
        out["med_growth"] = growths[m // 2] if m % 2 else \
            0.5 * (growths[m // 2 - 1] + growths[m // 2])
    if enable_tapering:
        out["tap_ok"] = len(growths) > 0 and \
            out["med_growth"] + FP_EPSILON >= min_growth_pct
    if enable_diag:
        out["diag_ok"] = diag_met
    if consec > 0 and populated > 0 and bar_vol > 0.0:
        out["ratio_pct"] = (absorbed / consec) / (bar_vol / populated) * 100.0
    return out


def scof_absorption(bars: list[Bar], footprints: list[list[tuple[int, float, float]]],
                    tick_size: float = 1.0,
                    min_opp_levels: int = 3,
                    prior_candles: int = 2,
                    require_exhaustion: bool = True,
                    require_delta_div: bool = True,
                    require_candle: bool = True,
                    min_vol_extreme: float = 0.0,
                    new_extreme_n: int = 0,
                    enable_tapering: bool = True,
                    min_growth_pct: float = 8.0,
                    enable_diag: bool = True,
                    diag_ratio_pct: float = 300.0,
                    counter_delta_mag: float = 1.0,
                    exhaust_max_opp_pct: float = 0.0,
                    min_relvol_pct: float = 0.0,
                    relvol_period: int = 20,
                    new_extreme_tol: int = 0,
                    lookback: int = 750) -> list[int]:
    """Study-faithful port of SCOFA-v1206 EvaluateAbsorption (absorption
    core only; sweeps/slingshots/runs excluded). footprints[i] is the
    bar's [(price_ticks, bid, ask)] ascending (see scid stream_footprint).
    Emits +1/-1 absorption markers. Exactness gate: must reproduce the
    chart's 4 markers on the capture window (tests/test_scof.py).
    """
    sigs = [0] * len(bars)
    n = len(bars)
    for i in range(n):
        if lookback > 0 and (n - i) > lookback:
            continue
        b = bars[i]
        levels = footprints[i] if i < len(footprints) else []
        for is_bullish, want in ((True, 1), (False, -1)):
            if require_candle:
                if is_bullish and b.close <= b.open:
                    continue
                if not is_bullish and b.close >= b.open:
                    continue
            bar_delta = b.askvol - b.bidvol
            div_ok = (bar_delta < 0.0) if is_bullish else (bar_delta > 0.0)
            if require_delta_div and not div_ok:
                continue
            if min_relvol_pct > 0:
                m = min(relvol_period, i)
                if m > 0:
                    avg = sum(x.volume for x in bars[i - m:i]) / m
                    if avg > 0.0 and b.volume < avg * min_relvol_pct / 100.0:
                        continue
            if new_extreme_n > 0:
                tol = new_extreme_tol * tick_size
                half = tick_size * 0.5
                ok = True
                for k in range(max(0, i - new_extreme_n), i):
                    if is_bullish and bars[k].low < b.low - tol + half:
                        ok = False
                        break
                    if not is_bullish and bars[k].high > b.high + tol - half:
                        ok = False
                        break
                if not ok:
                    continue
            found = 0
            for k in range(1, max(prior_candles, 10) + 1):
                if i - k < 0:
                    break
                pb = bars[i - k]
                counter = (pb.close < pb.open) if is_bullish \
                    else (pb.close > pb.open)
                if counter:
                    found += 1
                else:
                    break
            if prior_candles > 0 and found < prior_candles:
                continue
            if not levels:
                continue
            if is_bullish:
                et, eb, ea = levels[0]
                dom, opp = eb, ea
            else:
                et, eb, ea = levels[-1]
                dom, opp = ea, eb
            vol_extreme = dom
            exh_ok = dom > 0.0 and \
                opp * 100.0 <= exhaust_max_opp_pct * dom
            if require_exhaustion and not exh_ok:
                continue
            if min_vol_extreme > 0 and vol_extreme < min_vol_extreme:
                continue
            st = _scof_streak(levels, is_bullish, min_growth_pct,
                              diag_ratio_pct, counter_delta_mag,
                              enable_tapering, enable_diag,
                              min_opp_levels + 10)
            if st["consec"] < min_opp_levels:
                continue
            if enable_tapering and not st["tap_ok"]:
                continue
            if enable_diag and not st["diag_ok"]:
                continue
            sigs[i] = want
            break
    return sigs

STRATEGIES = {"signal_replay": signal_replay, "orion_bar": orion_bar,
              "mrou_ou": mrou_ou, "monday_dip": monday_dip,
              "golden_cross": golden_cross}
