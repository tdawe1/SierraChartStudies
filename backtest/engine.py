"""Headless futures backtest engine. Stdlib only.

Execution model (deliberately boring and explicit):
- Signals are computed on CLOSED bars, using only data up to bar i.
- Default fill is next bar's open (realistic: you cannot trade a closed
  bar's close after seeing it without slippage assumptions). `--exec close`
  fills at the signal bar close for signal-replay comparisons.
- One position at a time (max_positions=1). Opposite signal closes and,
  if enabled, reverses on the next open.
- Stops/targets are checked against each bar's high/low. If both are
  touched in the same bar, the loss is assumed first (conservative).
- Costs: per-side commission + slippage in ticks, applied on every fill.

Cost model inputs are per-contract USD and ticks; point value converts
price distance to USD: pnl = direction * (exit - entry) / tick_size
* tick_value * qty - fees.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Bar:
    idx: int
    stamp: str = ""
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 0.0
    bidvol: float = 0.0
    askvol: float = 0.0
    maxdelta: float = 0.0
    mindelta: float = 0.0
    setup_long: int = 0
    setup_short: int = 0
    signal_long: int = 0
    signal_short: int = 0
    symbol: str = ""

    @property
    def delta(self) -> float:
        return self.askvol - self.bidvol


@dataclass
class EngineConfig:
    tick_size: float = 0.25
    tick_value: float = 12.50  # USD per tick per contract (ES default)
    qty: int = 1
    stop_ticks: int = 0       # 0 = off
    target_ticks: int = 0     # 0 = off
    fee_per_side: float = 2.10
    slippage_ticks: float = 1.0
    exec_mode: str = "next_open"  # or "close"
    allow_long: bool = True
    allow_short: bool = True
    exit_on_opposite: bool = True
    reverse_on_opposite: bool = False
    max_hold_bars: int = 0    # 0 = off
    session_start: str = ""   # "HH:MM", empty = off
    session_end: str = ""
    # Prop risk controls (0/empty = off). Sized from the stop: qty =
    # floor(account*risk_pct/100 / (stop_ticks*tick_value + fees)).
    account_size: float = 0.0
    risk_pct: float = 0.0
    max_qty: int = 10
    daily_loss_limit: float = 0.0  # intraday: halt entries for the day, flatten
    max_drawdown_limit: float = 0.0  # trailing: vs peak marked equity, stops run
    profit_target: float = 0.0  # reported: first bar marked equity >= target
    consistency_max_pct: float = 0.0  # best-day share of profit-days total, 0=off
    trade_windows: tuple = ()  # ("08:30-11:00", ...) entries only inside; empty=off
    symbols: tuple = ()  # allowlist matched against Bar.symbol; empty=all
    max_trades_per_day: int = 0  # entries per day cap, 0=off


@dataclass
class Trade:
    direction: int
    entry_idx: int
    entry_stamp: str
    entry_price: float
    exit_idx: int = -1
    exit_stamp: str = ""
    exit_price: float = 0.0
    exit_reason: str = ""
    pnl: float = 0.0
    bars_held: int = 0
    qty: int = 1


@dataclass
class Position:
    direction: int
    entry_idx: int
    entry_stamp: str
    entry_price: float
    stop_price: float = 0.0
    target_price: float = 0.0
    qty: int = 1


def _in_session(stamp: str, start: str, end: str) -> bool:
    if not start or not end:
        return True
    t = stamp[11:16] if len(stamp) >= 16 else ""
    if not t:
        return True
    if start <= end:
        return start <= t <= end
    return t >= start or t <= end


def _in_windows(stamp: str, cfg: EngineConfig) -> bool:
    """Legacy single session folds into the windows list when set."""
    windows = list(cfg.trade_windows)
    if cfg.session_start and cfg.session_end:
        windows.append(f"{cfg.session_start}-{cfg.session_end}")
    if not windows:
        return True
    t = stamp[11:16] if len(stamp) >= 16 else ""
    if not t:
        return True
    for w in windows:
        start, _, end = w.partition("-")
        if start.strip() <= t <= end.strip() or (
                end.strip() < start.strip() and (t >= start.strip() or t <= end.strip())):
            return True
    return False


def _slip_price(price: float, direction: int, side: str, cfg: EngineConfig) -> float:
    slip = cfg.slippage_ticks * cfg.tick_size
    if side == "entry":
        return price + slip * direction
    return price - slip * direction


def _entry_qty(cfg: EngineConfig) -> int:
    """Contracts such that a full stop loses ~risk_pct of account."""
    if (cfg.account_size > 0 and cfg.risk_pct > 0 and cfg.stop_ticks > 0
            and cfg.tick_value > 0):
        per_contract = cfg.stop_ticks * cfg.tick_value + 2 * cfg.fee_per_side
        if per_contract > 0:
            return max(1, min(cfg.max_qty,
                              int(cfg.account_size * cfg.risk_pct / 100.0 // per_contract)))
    return cfg.qty


def run(bars: list[Bar], signals: list[int], cfg: EngineConfig) -> dict:
    """Run the simulation. signals[i] in {-1, 0, 1}, closed-bar signal."""
    trades: list[Trade] = []
    equity: list[float] = [0.0]
    curve_stamps: list[str] = [bars[0].stamp if bars else ""]
    pos: Position | None = None
    pending: int = 0  # entry signal waiting for next open
    pending_stamp: str = ""
    pending_idx: int = -1
    cum = 0.0
    cur_day, day_start, halted_day = "", 0.0, ""
    dead, peak = False, 0.0
    day_pnls: dict[str, float] = {}
    day_entries: dict[str, int] = {}
    target_hit_stamp = ""
    def close_pos(bar: Bar, price: float, reason: str) -> None:
        nonlocal cum, pos
        assert pos is not None
        raw = pos.direction * (price - pos.entry_price)
        ticks = raw / cfg.tick_size if cfg.tick_size else 0.0
        pnl = ticks * cfg.tick_value * pos.qty - 2 * cfg.fee_per_side * pos.qty
        cum += pnl
        trades.append(Trade(
            direction=pos.direction, entry_idx=pos.entry_idx,
            entry_stamp=pos.entry_stamp, entry_price=pos.entry_price,
            exit_idx=bar.idx, exit_stamp=bar.stamp, exit_price=price,
            exit_reason=reason, pnl=pnl, bars_held=bar.idx - pos.entry_idx,
            qty=pos.qty,
        ))
        pos = None
        equity.append(cum)
        curve_stamps.append(bar.stamp)

    for i, bar in enumerate(bars):
        sig = signals[i] if i < len(signals) else 0
        day = bar.stamp[:10]
        if day != cur_day:
            if cur_day:
                day_pnls[cur_day] = day_pnls.get(cur_day, 0.0) + (cum - day_start)
            cur_day, day_start, halted_day = day, cum, ""

        blocked = dead or halted_day == day
        # 1. Fill pending entry at this bar's open (unless gated).
        if pending and pos is None and not blocked:
            if _in_windows(bar.stamp, cfg):
                fill = _slip_price(bar.open, pending, "entry", cfg)
                stop = fill - pending * cfg.stop_ticks * cfg.tick_size if cfg.stop_ticks else 0.0
                target = fill + pending * cfg.target_ticks * cfg.tick_size if cfg.target_ticks else 0.0
                pos = Position(direction=pending, entry_idx=bar.idx,
                               entry_stamp=bar.stamp, entry_price=fill,
                               stop_price=stop, target_price=target,
                               qty=_entry_qty(cfg))
                day_entries[day] = day_entries.get(day, 0) + 1
            pending = 0
        elif blocked:
            pending = 0

        # 2. Manage open position against this bar's range.
        if pos is not None:
            exited = False
            d = pos.direction
            long = d > 0
            stop_hit = (cfg.stop_ticks and
                        ((long and bar.low <= pos.stop_price) or
                         (not long and bar.high >= pos.stop_price)))
            tgt_hit = (cfg.target_ticks and
                       ((long and bar.high >= pos.target_price) or
                        (not long and bar.low <= pos.target_price)))
            if stop_hit:  # conservative: stop first on ambiguous bars
                close_pos(bar, _slip_price(pos.stop_price, d, "exit", cfg), "stop")
                exited = True
            elif tgt_hit:
                close_pos(bar, _slip_price(pos.target_price, d, "exit", cfg), "target")
                exited = True
            elif cfg.max_hold_bars and (bar.idx - pos.entry_idx) >= cfg.max_hold_bars:
                close_pos(bar, _slip_price(bar.close, d, "exit", cfg), "time")
                exited = True
            elif cfg.exit_on_opposite and sig != 0 and sig != d:
                if (d < 0 and cfg.allow_long) or (d > 0 and cfg.allow_short):
                    close_pos(bar, _slip_price(bar.close, d, "exit", cfg), "opposite")
                    exited = True
                    if cfg.reverse_on_opposite:
                        pending, pending_stamp, pending_idx = sig, bar.stamp, bar.idx
            _ = exited

        # 3. Prop risk check on marked-to-market equity, then new entries.
        if pos is not None and cfg.tick_size:
            unreal = (pos.direction * (bar.close - pos.entry_price)
                      / cfg.tick_size * cfg.tick_value * pos.qty)
        else:
            unreal = 0.0
        marked = cum + unreal
        peak = max(peak, marked)
        if cfg.profit_target > 0 and not target_hit_stamp and marked >= cfg.profit_target:
            target_hit_stamp = bar.stamp
        if not dead and cfg.max_drawdown_limit > 0 and marked - peak <= -cfg.max_drawdown_limit:
            if pos is not None:
                close_pos(bar, _slip_price(bar.close, pos.direction, "exit", cfg), "risk-dd")
            dead, pending, blocked = True, 0, True
        elif (not dead and halted_day != day and cfg.daily_loss_limit > 0
                and marked - day_start <= -cfg.daily_loss_limit):
            if pos is not None:
                close_pos(bar, _slip_price(bar.close, pos.direction, "exit", cfg), "risk-daily")
            halted_day, pending, blocked = day, 0, True

        # 4. New signal -> pending (next-open) or immediate (close) entry.
        allowed_symbol = not cfg.symbols or bar.symbol in cfg.symbols
        under_day_cap = not cfg.max_trades_per_day or day_entries.get(day, 0) < cfg.max_trades_per_day
        if sig != 0 and pos is None and not pending and not blocked:
            if (sig > 0 and not cfg.allow_long) or (sig < 0 and not cfg.allow_short):
                pass
            elif not allowed_symbol or not under_day_cap:
                pass
            elif not _in_windows(bar.stamp, cfg):
                pass
            elif cfg.exec_mode == "close":
                fill = _slip_price(bar.close, sig, "entry", cfg)
                stop = fill - sig * cfg.stop_ticks * cfg.tick_size if cfg.stop_ticks else 0.0
                target = fill + sig * cfg.target_ticks * cfg.tick_size if cfg.target_ticks else 0.0
                pos = Position(direction=sig, entry_idx=bar.idx,
                               entry_stamp=bar.stamp, entry_price=fill,
                               stop_price=stop, target_price=target,
                               qty=_entry_qty(cfg))
                day_entries[day] = day_entries.get(day, 0) + 1
            else:
                pending, pending_stamp, pending_idx = sig, bar.stamp, bar.idx

    # 5. Flush open position at last close; close the day ledger.
    if pos is not None:
        close_pos(bars[-1], _slip_price(bars[-1].close, pos.direction, "exit", cfg), "eod")
    if cur_day:
        day_pnls[cur_day] = day_pnls.get(cur_day, 0.0) + (cum - day_start)
    m = metrics(trades, equity)
    m.update(_rule_metrics(day_pnls, cfg, target_hit_stamp))
    return {"trades": trades, "equity": equity, "stamps": curve_stamps,
            "metrics": m, "day_pnls": day_pnls}


def _rule_metrics(day_pnls: dict[str, float], cfg: EngineConfig,
                  target_hit_stamp: str) -> dict:
    """Prop pass/fail extras: profit target, best-day consistency."""
    profit_days = {d: p for d, p in day_pnls.items() if p > 0}
    best_day, best_pnl = "", 0.0
    for d, p in profit_days.items():
        if p > best_pnl:
            best_day, best_pnl = d, p
    denom = sum(profit_days.values())
    best_pct = (100.0 * best_pnl / denom) if denom > 0 else 0.0
    return {
        "target_hit": bool(target_hit_stamp),
        "target_hit_stamp": target_hit_stamp,
        "best_day": best_day,
        "best_day_pnl": round(best_pnl, 2),
        "best_day_pct": round(best_pct, 1),
        "consistency_ok": (best_pct <= cfg.consistency_max_pct
                           if cfg.consistency_max_pct > 0 and denom > 0 else True),
    }


def metrics(trades: list[Trade], equity: list[float]) -> dict:
    n = len(trades)
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    gross_w = sum(t.pnl for t in wins)
    gross_l = -sum(t.pnl for t in losses)
    total = sum(t.pnl for t in trades)
    runup = equity[0] if equity else 0.0
    dd = 0.0
    for v in equity:
        runup = max(runup, v)
        dd = min(dd, v - runup)
    avg_w = gross_w / len(wins) if wins else 0.0
    avg_l = (-gross_l / len(losses)) if losses else 0.0
    consec = worst_consec = 0
    for t in trades:
        consec = consec + 1 if t.pnl <= 0 else 0
        worst_consec = max(worst_consec, consec)
    mean = total / n if n else 0.0
    var = sum((t.pnl - mean) ** 2 for t in trades) / n if n else 0.0
    sharpe = (mean / (var ** 0.5)) if n > 1 and var > 0 else 0.0
    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / n) if n else 0.0,
        "total_pnl": round(total, 2),
        "avg_win": round(avg_w, 2),
        "avg_loss": round(avg_l, 2),
        "profit_factor": round(gross_w / gross_l, 3) if gross_l > 0 else 0.0,
        "expectancy": round(total / n, 2) if n else 0.0,
        "max_drawdown": round(dd, 2),
        "calmar": round(total / -dd, 3) if dd < 0 else 0.0,
        "sharpe_trade": round(sharpe, 3),
        "max_consec_losses": worst_consec,
        "avg_bars_held": round(sum(t.bars_held for t in trades) / n, 1) if n else 0.0,
        "risk_halts": sum(1 for t in trades if t.exit_reason.startswith("risk")),
    }
