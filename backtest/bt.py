#!/usr/bin/env python3
"""bt -- headless study backtester and comparison system. Stdlib only.

Assessment workflow (all local, results persist in results.db)::

    python3 bt.py run --data ES.csv --params params.replay.json --out out/ --tag baseline
    python3 bt.py run --data ES.csv --params params.tuned.json --out out2/ --tag tuned --split frac:0.7
    python3 bt.py sweep --data ES.csv --params params.sweep.json --out out-sweep/
    python3 bt.py walkforward --data ES.csv --params params.replay.json --out out-wf --train 200 --test 50
    python3 bt.py compare --dataset ES.csv --out report.html   # + prints leaderboard

`compare` writes one self-contained report.html (inline SVG, no JS):
overlaid equity curves plus a leaderboard ranked by OOS total when a
split exists, else by total. Buy-and-hold baselines are added per
dataset unless --no-baseline.

Splits: signals are always computed once over the full history (every
strategy is causal -- past bars only, no lookahead); the portfolio is
then simulated separately on each segment starting flat. Walk-forward
does the same per test window and aggregates the out-of-sample trades.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import os
import shutil
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data import load_csv  # noqa: E402
from engine import REGIMES, Bar, EngineConfig, Trade, metrics, run  # noqa: E402
from strategies import STRATEGIES  # noqa: E402


def _strategy_params(params: dict) -> dict:
    return {k: v for k, v in params.get("strategy_params", {}).items()}


def _load_params(params_path: str, overrides: dict | None = None) -> dict:
    with open(params_path) as f:
        params = json.load(f)
    if overrides:
        params.setdefault("strategy_params", {}).update(
            overrides.get("strategy_params", {}))
        for k, v in overrides.items():
            if k != "strategy_params":
                params[k] = v
    return params


def _make_cfg(params: dict) -> EngineConfig:
    eng = params.get("engine", {})
    regime = eng.get("regime", "")
    if regime not in REGIMES:
        raise ValueError(
            f"params engine.regime={regime!r} invalid: declare exactly one of "
            f"{', '.join(REGIMES)} (no mixed runs)")
    return EngineConfig(
        tick_size=float(params.get("tick_size", eng.get("tick_size", 0.25))),
        tick_value=float(params.get("tick_value", eng.get("tick_value", 12.50))),
        qty=int(eng.get("qty", 1)),
        stop_ticks=int(eng.get("stop_ticks", 0)),
        target_ticks=int(eng.get("target_ticks", 0)),
        fee_per_side=float(eng.get("fee_per_side", 2.10)),
        slippage_ticks=float(eng.get("slippage_ticks", 1.0)),
        exec_mode=eng.get("exec_mode", "next_open"),
        allow_long=bool(eng.get("allow_long", True)),
        allow_short=bool(eng.get("allow_short", True)),
        exit_on_opposite=bool(eng.get("exit_on_opposite", True)),
        reverse_on_opposite=bool(eng.get("reverse_on_opposite", False)),
        max_hold_bars=int(eng.get("max_hold_bars", 0)),
        session_start=eng.get("session_start", ""),
        session_end=eng.get("session_end", ""),
        account_size=float(eng.get("account_size", 0.0)),
        risk_pct=float(eng.get("risk_pct", 0.0)),
        max_qty=int(eng.get("max_qty", 10)),
        daily_loss_limit=float(eng.get("daily_loss_limit", 0.0)),
        max_drawdown_limit=float(eng.get("max_drawdown_limit", 0.0)),
        profit_target=float(eng.get("profit_target", 0.0)),
        consistency_max_pct=float(eng.get("consistency_max_pct", 0.0)),
        trade_windows=tuple(eng.get("trade_windows", ())),
        symbols=tuple(eng.get("symbols", ())),
        max_trades_per_day=int(eng.get("max_trades_per_day", 0)),
        size_by_atr=bool(eng.get("size_by_atr", False)),
        atr_risk_mult=float(eng.get("atr_risk_mult", 1.0)),
        regime=regime,
    )


def split_bars(bars: list[Bar], split: str) -> tuple[list[Bar], list[Bar]]:
    """Split into (in-sample, out-of-sample). '' -> (all, []).

    'frac:0.7' cuts at 70% of bars; otherwise the string is a DateTime
    prefix -- OOS starts at the first bar whose stamp >= split.
    """
    if not split:
        return bars, []
    if split.startswith("frac:"):
        cut = int(len(bars) * float(split.split(":")[1]))
        return bars[:cut], bars[cut:]
    cut = next((i for i, b in enumerate(bars) if b.stamp >= split), len(bars))
    return bars[:cut], bars[cut:]


def _log(db: str | None, study: str, dataset: str, tag: str, split: str,
         params: dict, res: dict, out_dir: str,
         results_dir: str | None, bars: list | None = None,
         n_trials: int = 1) -> str:
    from store import (code_version, connect, dataset_provenance,
                       default_results_dir, log_run, make_id)
    run_id = make_id(study, dataset, {**params, "tag": tag, "split": split})
    dest = os.path.join(results_dir or default_results_dir(), run_id)
    os.makedirs(dest, exist_ok=True)
    for fn in ("trades.csv", "equity.csv", "summary.json", "report.txt"):
        src = os.path.join(out_dir, fn)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(dest, fn))
    with open(os.path.join(dest, "params.json"), "w") as f:
        json.dump({**params, "tag": tag, "split": split}, f, indent=2)
    prov = dataset_provenance(dataset, bars or [])
    sha = code_version()
    con = connect(db)
    try:
        for attempt in range(3):
            try:
                log_run(con, run_id, study, dataset, tag, split, params,
                        res["metrics"], res.get("is_metrics"),
                        res.get("oos_metrics"), artifact_dir=dest,
                        code_sha=sha, n_trials=n_trials, **prov)
                break
            except sqlite3.IntegrityError:
                # same-second rerun of identical params: keep both, suffix
                new_id = f"{run_id}-r{attempt + 1}"
                new_dest = os.path.join(os.path.dirname(dest), new_id)
                os.rename(dest, new_dest)
                run_id, dest = new_id, new_dest
    finally:
        con.close()
    return run_id


def _run_split(bars: list[Bar], signals: list[int], cfg: EngineConfig,
               split: str) -> dict:
    """Full-history run plus flat-restart IS/OOS segments when split."""
    res = run(bars, signals, cfg)
    is_bars, oos_bars = split_bars(bars, split)
    if oos_bars:
        n_is = len(is_bars)
        res["is_metrics"] = run(is_bars, signals[:n_is], cfg)["metrics"]
        res["oos_metrics"] = run(oos_bars, signals[n_is:], cfg)["metrics"]
    return res


def do_run(data_path: str, params_path: str, out_dir: str,
           overrides: dict | None = None, quiet: bool = False,
           tag: str = "", split: str = "", db: str | None = None,
           log: bool = True, results_dir: str | None = None,
           n_trials: int = 1) -> dict:
    params = _load_params(params_path, overrides)
    bars = load_csv(data_path)
    strat = STRATEGIES[params.get("strategy", "signal_replay")]
    signals = strat(bars, **_strategy_params(params))
    cfg = _make_cfg(params)
    res = _run_split(bars, signals, cfg, split)
    write_outputs(out_dir, res, params, quiet=quiet)
    if log:
        run_id = _log(db, params.get("strategy", "signal_replay"), data_path,
                      tag, split, params, res, out_dir, results_dir,
                      bars=bars, n_trials=n_trials)
        if not quiet:
            print(f"logged: {run_id}")
        res["run_id"] = run_id
    return res


def write_outputs(out_dir: str, res: dict, params: dict, quiet: bool = False) -> None:
    """Write one run's artifacts; equity.csv always lands (empty-safe)."""
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "trades.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["dir", "entry_idx", "entry_time", "entry", "exit_idx",
                    "exit_time", "exit", "reason", "pnl", "bars_held", "qty",
                    "regime"])
        for t in res["trades"]:
            w.writerow([t.direction, t.entry_idx, t.entry_stamp, t.entry_price,
                        t.exit_idx, t.exit_stamp, t.exit_price, t.exit_reason,
                        round(t.pnl, 2), t.bars_held, t.qty, t.regime])
    with open(os.path.join(out_dir, "equity.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["stamp", "equity"])
        for st, v in zip(res.get("stamps", []), res.get("equity", [])):
            w.writerow([st, round(v, 2)])
    with open(os.path.join(out_dir, "summary.json"), "w") as f:
        json.dump({"params": params, "metrics": res["metrics"],
                   "is_metrics": res.get("is_metrics"),
                   "oos_metrics": res.get("oos_metrics")}, f, indent=2)
    m = res["metrics"]
    pf = m["profit_factor"] if m["profit_factor"] is not None else "-"
    cm = m["calmar"] if m["calmar"] is not None else "-"
    lines = [
        f"strategy   : {params.get('strategy')}",
        f"trades     : {m['trades']}  (W {m['wins']} / L {m['losses']})",
        f"win rate   : {m['win_rate'] * 100:.1f}%",
        f"total PnL  : ${m['total_pnl']:,.2f}",
        f"expectancy : ${m['expectancy']:,.2f} / trade",
        f"avg win    : ${m['avg_win']:,.2f}   avg loss : ${m['avg_loss']:,.2f}",
        f"profit fac : {pf}",
        f"max DD     : ${m['max_drawdown']:,.2f}",
        f"calmar     : {cm}   sharpeT : {m['sharpe_trade']}",
    ]
    eng = params.get("engine", {})
    if eng.get("account_size") and eng.get("risk_pct"):
        lines.append(
            f"sizing     : risk {eng['risk_pct']}% of ${eng['account_size']:,.0f} "
            f"(daily halt ${eng.get('daily_loss_limit', 0):,.0f}, "
            f"DD halt ${eng.get('max_drawdown_limit', 0):,.0f})")
    if m.get("risk_halts"):
        lines.append(f"risk halts : {m['risk_halts']}")
    if eng.get("profit_target"):
        hit = f"hit {m.get('target_hit_stamp', '')}" if m.get("target_hit") else "not hit"
        lines.append(f"target     : ${eng['profit_target']:,.0f} {hit}")
    if eng.get("consistency_max_pct"):
        mark = "PASS" if m.get("consistency_ok") else "FAIL"
        lines.append(f"consistency: best day {m.get('best_day', '')} "
                     f"${m.get('best_day_pnl', 0):,.0f} ({m.get('best_day_pct', 0)}% "
                     f"<= {eng['consistency_max_pct']}%) {mark}")
    if res.get("is_metrics") and res.get("oos_metrics"):
        lines.append(f"IS  PnL    : ${res['is_metrics']['total_pnl']:,.2f} "
                     f"({res['is_metrics']['trades']} trades)")
        lines.append(f"OOS PnL    : ${res['oos_metrics']['total_pnl']:,.2f} "
                     f"({res['oos_metrics']['trades']} trades)")
    with open(os.path.join(out_dir, "report.txt"), "w") as f:
        f.write("\n".join(lines) + "\n")
    if not quiet:
        print("\n".join(lines))

SWEEP_MAX_COMBOS = 200



def do_sweep(data_path: str, params_path: str, out_dir: str,
             db: str | None = None, tag_prefix: str = "",
             log: bool = True, results_dir: str | None = None) -> None:
    with open(params_path) as f:
        base = json.load(f)
    grid = base.pop("grid", {})
    keys = sorted(grid)
    combos = list(itertools.product(*(grid[k] for k in keys)))
    if len(combos) > SWEEP_MAX_COMBOS:
        raise SystemExit(
            f"sweep budget exceeded: {len(combos)} combos (max "
            f"{SWEEP_MAX_COMBOS} per dataset per params file). Narrow the "
            "grid: every combo is a trial that inflates the winner.")
    rows = []
    for combo in combos:
        ov = {"strategy_params": dict(zip(keys, combo))}
        tag = "__".join(f"{k}={v}" for k, v in zip(keys, combo))
        if tag_prefix:
            tag = f"{tag_prefix} {tag}"
        sub = os.path.join(out_dir, tag.replace(" ", "_"))
        res = _run_with(data_path, base, ov, sub, tag=tag, split="",
                        db=db, log=log, results_dir=results_dir,
                        n_trials=len(combos) or 1)
        rows.append((res["metrics"]["total_pnl"], tag, res["metrics"]))
    rows.sort(reverse=True)
    with open(os.path.join(out_dir, "sweep.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["total_pnl", "params", "trades", "win_rate",
                    "profit_factor", "max_drawdown"])
        for pnl, tag, m in rows:
            w.writerow([pnl, tag, m["trades"], round(m["win_rate"], 3),
                        m["profit_factor"], m["max_drawdown"]])
    print(f"\nbest: {rows[0][1]}  PnL ${rows[0][0]:,.2f}" if rows else "empty grid")


def _run_with(data_path: str, base: dict, overrides: dict, out_dir: str,
              tag: str = "", split: str = "", db: str | None = None,
              log: bool = True, results_dir: str | None = None,
              n_trials: int = 1) -> dict:
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        merged = json.loads(json.dumps(base))
        merged.setdefault("strategy_params", {}).update(
            overrides.get("strategy_params", {}))
        json.dump(merged, f)
        tmp = f.name
    try:
        return do_run(data_path, tmp, out_dir, quiet=True, tag=tag,
                      split=split, db=db, log=log, results_dir=results_dir,
                      n_trials=n_trials)
    finally:
        os.unlink(tmp)

def _embargo_cut(bars: list[Bar], s: int, e: int, embargo_days: int) -> int:
    """First index in [s, e) past the first embargo_days distinct dates.

    Purged-embargo precaution (AFML ch. 7): test bars adjacent to the
    train/test boundary share serial correlation with it. 0 opts out.
    Returns e when the whole window is embargoed (caller skips the run).
    """
    if embargo_days <= 0:
        return s
    seen: list[str] = []
    for i in range(s, e):
        d = bars[i].stamp[:10]
        if d not in seen:
            seen.append(d)
        if len(seen) > embargo_days:
            return i
    return e



def _walkforward_result(bars: list[Bar], signals: list[int], cfg: EngineConfig,
                        train: int, test: int, step: int,
                        embargo_days: int = 1) -> dict:
    """Rolling train/test loop; aggregate the out-of-sample trades."""
    if train < 1 or test < 1 or step < 1:
        raise ValueError(
            f"walkforward needs train/test/step >= 1 "
            f"(got {train}/{test}/{step})")
    all_trades: list[Trade] = []
    equity = [0.0]
    stamps = [bars[0].stamp if bars else ""]
    cum = 0.0
    windows = []
    s = max(train, 1)
    while s < len(bars):
        e = min(s + test, len(bars))
        cut = _embargo_cut(bars, s, e, embargo_days)
        if cut >= e:
            windows.append({"start": bars[s].stamp, "end": bars[e - 1].stamp,
                            "trades": 0, "pnl": 0.0, "dropped": e - s})
        else:
            seg = run(bars[cut:e], signals[cut:e], cfg)
            for t in seg["trades"]:
                all_trades.append(t)
            # stitch: shift segment equity by running total
            base = cum
            for v, st in zip(seg["equity"][1:], seg["stamps"][1:]):
                cum = base + (v - seg["equity"][0])
                equity.append(cum)
                stamps.append(st)
            windows.append({"start": bars[cut].stamp, "end": bars[e - 1].stamp,
                            "trades": len(seg["trades"]),
                            "pnl": seg["metrics"]["total_pnl"],
                            "dropped": cut - s})
        if e >= len(bars):
            break
        s += step
    res = {"trades": all_trades, "equity": equity, "stamps": stamps,
           "metrics": metrics(all_trades, equity), "windows": windows,
           "oos_metrics": metrics(all_trades, equity)}
    res["metrics"]["regime"] = cfg.regime
    res["metrics"]["by_regime"] = {cfg.regime: metrics(all_trades, equity)}
    return res


def do_walkforward(data_path: str, params_path: str, out_dir: str,
                   train: int = 200, test: int = 50, step: int = 50,
                   embargo_days: int = 1,
                   tag: str = "", db: str | None = None, log: bool = True,
                   results_dir: str | None = None) -> dict:
    """Rolling train/test windows; aggregate the out-of-sample trades."""
    params = _load_params(params_path)
    bars = load_csv(data_path)
    strat = STRATEGIES[params.get("strategy", "signal_replay")]
    signals = strat(bars, **_strategy_params(params))
    cfg = _make_cfg(params)
    res = _walkforward_result(bars, signals, cfg, train, test, step,
                              embargo_days)
    windows = res["windows"]
    all_trades = res["trades"]
    write_outputs(out_dir, res, params, quiet=True)
    with open(os.path.join(out_dir, "windows.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["start", "end", "trades", "pnl", "dropped"])
        for wd in windows:
            w.writerow([wd["start"], wd["end"], wd["trades"], wd["pnl"],
                        wd.get("dropped", 0)])
    print(f"walkforward: {len(windows)} windows, {len(all_trades)} OOS trades, "
          f"PnL ${res['metrics']['total_pnl']:,.2f}")
    if log:
        log_params = {**params, "walkforward": {"train": train, "test": test,
                                                "step": step,
                                                "embargo_days": embargo_days}}
        run_id = _log(db, params.get("strategy", "signal_replay"), data_path,
                      tag or f"wf train={train} test={test} step={step}",
                      split="walkforward", params=log_params, res=res,
                      out_dir=out_dir, results_dir=results_dir, bars=bars)
        print(f"logged: {run_id}")
        res["run_id"] = run_id
    return res

PROMOTE_MIN_OOS_TRADES = 30


def do_promote(db: str | None, run_id: str) -> bool:
    """Promotion gate for new signals. All four must PASS:

    OOS expectancy > 0, >= 30 OOS trades, costs on (fee and slippage),
    OOS total beats the dataset buy-and-hold baseline. OOS evidence is
    the walkforward aggregate or a run --split segment. Prints per-check
    PASS/FAIL; returns True only when everything passes.
    """
    from store import connect, get_run
    con = connect(db)
    try:
        row = get_run(con, run_id)
    finally:
        con.close()
    if row is None:
        raise SystemExit(f"unknown run id: {run_id}")
    params = row["params"] or {}
    eng = params.get("engine", {})
    split = row["split"] or ""
    if split == "walkforward":
        oos, oos_src = row["metrics"], "walkforward aggregate (all OOS)"
    elif row["oos_metrics"]:
        oos, oos_src = row["oos_metrics"], f"split {split}"
    else:
        oos, oos_src = None, ""
    oos = oos or {}
    checks: list[tuple[str, bool, str]] = []
    checks.append(("oos-evidence", bool(row["metrics"] if split == "walkforward"
                                        else row["oos_metrics"]),
                   oos_src if oos_src else "no --split / walkforward OOS; "
                   "rerun with OOS evidence first"))
    exp = oos.get("expectancy", 0.0)
    checks.append(("oos-expectancy>0", exp > 0, f"${exp:,.2f}/trade"))
    n = oos.get("trades", 0)
    checks.append((f"oos-trades>={PROMOTE_MIN_OOS_TRADES}",
                   n >= PROMOTE_MIN_OOS_TRADES, f"{n} trades"))
    costs = eng.get("fee_per_side", 0) > 0 and eng.get("slippage_ticks", 0) > 0
    checks.append(("costs-on", costs,
                   f"fee={eng.get('fee_per_side')} slip={eng.get('slippage_ticks')}"))
    tick_size = float(params.get("tick_size", eng.get("tick_size", 0.25)))
    tick_value = float(params.get("tick_value", eng.get("tick_value", 12.50)))
    base = _baselines([row["dataset"]], tick_size, tick_value)
    if base and oos_src:
        b = base[0]["metrics"]["total_pnl"]
        checks.append(("beats-hold", oos.get("total_pnl", 0.0) > b,
                       f"OOS ${oos.get('total_pnl', 0.0):,.2f} vs "
                       f"hold ${b:,.2f}"))
    elif not oos_src:
        checks.append(("beats-hold", False, "no OOS evidence to compare"))
    else:
        checks.append(("beats-hold", False, "dataset missing, no baseline"))
    ok = all(passed for _, passed, _ in checks)
    for name, passed, detail in checks:
        print(f"{'PASS' if passed else 'FAIL'}  {name:16s} {detail}")
    if ok:
        print(f"run {run_id} PROMOTED -- tag new signals promoted:{run_id}")
    else:
        print(f"run {run_id} NOT promoted")
    return ok

VERIFY_IGNORE_KEYS = {"regime", "by_regime"}


MATRIX_MIN_TRADES = 20


def do_matrix(data_path: str, params_paths: list[str], out: str,
              sessions: list | None = None) -> list[dict]:
    """Strategy x session matrix: which strategy works when.

    Each params file carries its own strategy, strategy_params, engine
    config, and regime, so every cell inherits single-regime semantics.
    Sessions slice entries only (trade_windows override); exits are still
    managed, so cells do not sum to the whole-session run. Cells with
    fewer than MATRIX_MIN_TRADES trades are flagged n_low and excluded
    from ranking, never averaged away. Analysis view: nothing is logged
    to the store.
    """
    from journal import DEFAULT_SESSIONS
    windows = sessions or [(n, f"{s}-{e}") for n, s, e in DEFAULT_SESSIONS]
    bars = load_csv(data_path)
    cells = []
    for pp in params_paths:
        params = _load_params(pp)
        strat = STRATEGIES[params.get("strategy", "signal_replay")]
        signals = strat(bars, **_strategy_params(params))
        for name, rng in windows:
            merged = json.loads(json.dumps(params))
            eng = merged.setdefault("engine", {})
            eng["trade_windows"] = [rng]
            eng["session_start"] = ""
            eng["session_end"] = ""
            cfg = _make_cfg(merged)
            m = run(bars, signals, cfg)["metrics"]
            cells.append({
                "strategy": params.get("strategy", "signal_replay"),
                "regime": cfg.regime,
                "session": name, "range": rng,
                "trades": m["trades"], "wins": m["wins"],
                "win_rate": round(m["win_rate"], 3),
                "expectancy": m["expectancy"],
                "profit_factor": m["profit_factor"],
                "total_pnl": m["total_pnl"],
                "max_drawdown": m["max_drawdown"],
                "n_low": m["trades"] < MATRIX_MIN_TRADES,
            })
    ranked = sorted((c for c in cells if not c["n_low"]),
                    key=lambda c: c["expectancy"], reverse=True)
    for i, c in enumerate(ranked, 1):
        c["rank"] = i
    lines = [f"{'rank':>4}  {'strategy':<14} {'session':<10} "
             f"{'n':>4}  {'win%':>5}  {'exp':>8}  {'pf':>6}  {'pnl':>9}"]
    for c in ranked:
        pf = f"{c['profit_factor']:>6.3f}" if c["profit_factor"] is not None else f"{'-':>6}"
        lines.append(f"{c['rank']:>4}  {c['strategy']:<14} {c['session']:<10} "
                     f"{c['trades']:>4}  {c['win_rate'] * 100:>4.1f}% "
                     f"${c['expectancy']:>7.2f}  {pf} "
                     f"${c['total_pnl']:>8.2f}")
    low = [c for c in cells if c["n_low"]]
    if low:
        lines.append(f"n_low (<{MATRIX_MIN_TRADES} trades, unranked): " +
                     ", ".join(f"{c['strategy']}/{c['session']}"
                               f"({c['trades']})" for c in low))
    print("\n".join(lines))
    if out:
        with open(out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["rank", "strategy", "regime",
                                              "session", "range", "trades",
                                              "wins", "win_rate", "expectancy",
                                              "profit_factor", "total_pnl",
                                              "max_drawdown", "n_low"])
            w.writeheader()
            for c in cells:
                w.writerow({k: c.get(k, "") for k in w.fieldnames})
        print(f"\nwrote {out}")
    return cells


def do_verify(db: str | None, run_id: str) -> bool:
    """Reproduce a logged run from stored params + dataset. Checks:

    dataset sha256 matches (skipped with a note for pre-audit rows that
    store no hash), then re-executes the engine and requires identical
    metrics (IS/OOS included). regime/by_regime are tagging, not
    simulation, and are excluded from the comparison. Prints PASS/FAIL
    per check; True only when everything passes.
    """
    from store import connect, dataset_provenance, get_run
    con = connect(db)
    try:
        row = get_run(con, run_id)
    finally:
        con.close()
    if row is None:
        raise SystemExit(f"unknown run id: {run_id}")
    params = dict(row["params"] or {})
    dataset = row["dataset"]
    split = row["split"] or ""
    results: list[tuple[str, bool, str]] = []
    stored_sha = row.get("data_sha256") or ""
    try:
        actual_sha = dataset_provenance(dataset, [])["data_sha256"]
    except OSError as e:
        actual_sha, hash_err = "", str(e)
    if stored_sha:
        results.append(("data-sha256", actual_sha == stored_sha,
                        actual_sha[:12] if actual_sha else f"unreadable: {hash_err}"))
    else:
        print("SKIP   data-sha256      (pre-audit run: no hash stored)")
    try:
        bars = load_csv(dataset)
    except (OSError, ValueError) as e:
        print(f"FAIL   reload           ({e})")
        return False
    eng = params.setdefault("engine", {})
    eng.setdefault("regime", (row["metrics"] or {}).get("regime",
                                                        "mean-reversion"))
    strat = STRATEGIES[params.get("strategy", "signal_replay")]
    signals = strat(bars, **_strategy_params(params))
    cfg = _make_cfg(params)
    recomputed: dict[str, dict] = {}
    if split == "walkforward":
        wf = params.get("walkforward")
        if not wf:
            print("FAIL   recompute        (walkforward spec not logged; "
                  "pre-audit run)")
            return False
        res = _walkforward_result(bars, signals, cfg, wf["train"],
                                  wf["test"], wf["step"],
                                  wf.get("embargo_days", 1))
        recomputed = {"metrics": res["metrics"]}
    elif split:
        res = _run_split(bars, signals, cfg, split)
        recomputed = {"metrics": res["metrics"],
                      "is_metrics": res.get("is_metrics") or {},
                      "oos_metrics": res.get("oos_metrics") or {}}
    else:
        recomputed = {"metrics": run(bars, signals, cfg)["metrics"]}
    ok = True
    for key, fresh in recomputed.items():
        stored = row[key] or {}
        fresh_cmp = {k: v for k, v in fresh.items()
                     if k not in VERIFY_IGNORE_KEYS}
        stored_cmp = {k: v for k, v in stored.items()
                      if k not in VERIFY_IGNORE_KEYS}
        same = fresh_cmp == stored_cmp
        if same:
            detail = "identical"
        else:
            missing = [k for k in stored_cmp if k not in fresh_cmp]
            mismatch = [k for k in stored_cmp
                        if k in fresh_cmp and fresh_cmp[k] != stored_cmp[k]]
            added = [k for k in fresh_cmp if k not in stored_cmp]
            # Pre-audit rows predate newer metric keys: pass when every
            # stored key reproduces, and report what was added since.
            same = not missing and not mismatch
            detail = ((f"all {len(stored_cmp)} stored keys match"
                       + (f"; added since: {sorted(added)}" if added else ""))
                      if same else f"differs: {sorted(missing + mismatch)}")
        ok = ok and same
        results.append((f"recompute-{key}", same, detail))
    for name, passed, detail in results:
        print(f"{'PASS' if passed else 'FAIL'}  {name:16s} {detail}")
        ok = ok and passed
    print(f"run {run_id} [{'VERIFIED' if ok else 'MISMATCH'}]")
    return ok


def _attach_equity(runs: list[dict]) -> None:
    for r in runs:
        pts: list[tuple[str, float]] = []
        eq = os.path.join(r.get("artifact_dir", ""), "equity.csv")
        if eq and os.path.exists(eq):
            with open(eq) as f:
                for row in csv.DictReader(f):
                    try:
                        pts.append((row["stamp"], float(row["equity"])))
                    except (ValueError, KeyError):
                        continue
        r["equity"] = pts


def _baselines(datasets: list[str], tick_size: float,
               tick_value: float) -> list[dict]:
    out = []
    for ds in datasets:
        try:
            bars = load_csv(ds)
        except (OSError, ValueError):
            continue
        if not bars:
            continue
        pnl = (bars[-1].close - bars[0].open) / tick_size * tick_value \
            if tick_size else 0.0
        out.append({
            "id": f"baseline-hold-{os.path.basename(ds)}", "study": "baseline_hold",
            "dataset": ds, "tag": "buy-and-hold, no costs",
            "metrics": {"trades": 1, "wins": int(pnl > 0),
                        "losses": int(pnl <= 0),
                        "win_rate": 1.0 if pnl > 0 else 0.0,
                        "total_pnl": round(pnl, 2), "avg_win": round(max(pnl, 0), 2),
                        "avg_loss": round(-min(pnl, 0), 2), "profit_factor": 0.0,
                        "expectancy": round(pnl, 2), "max_drawdown": 0.0,
                        "calmar": 0.0, "sharpe_trade": 0.0,
                        "max_consec_losses": 0, "avg_bars_held": len(bars)},
            "is_metrics": None, "oos_metrics": None,
            "equity": [(bars[0].stamp, 0.0), (bars[-1].stamp, round(pnl, 2))],
        })
    return out


def do_compare(db: str | None, dataset: str, study: str, out_html: str,
               top: int = 0, baseline: bool = True,
               tick_size: float = 0.25, tick_value: float = 12.50) -> list[dict]:
    from report import build_html, leaderboard_text, rank_key
    from store import connect, list_runs
    con = connect(db)
    try:
        runs = list_runs(con, dataset=dataset, study=study)
    finally:
        con.close()
    _attach_equity(runs)
    if baseline:
        seen = sorted({r["dataset"] for r in runs})
        runs += _baselines(seen, tick_size, tick_value)
    if top:
        runs = sorted(runs, key=rank_key, reverse=True)[:top]
    print(leaderboard_text(runs))
    with open(out_html, "w") as f:
        f.write(build_html(runs))
    print(f"\nreport: {out_html}")
    return runs


def do_sessions(journal: str, point_value: float = 1.0,
                sessions: list | None = None, out: str = "") -> list[dict]:
    """Rank time-of-day sessions from a trading journal."""
    from journal import load_journal, report_text, summarize
    summary = summarize(load_journal(journal, point_value), sessions)
    print(report_text(summary))
    if out:
        with open(out, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["session", "trades", "wins", "win_rate",
                        "total_pnl", "avg"])
            for s in summary:
                w.writerow([s["session"], s["trades"], s["wins"],
                            round(s["win_rate"], 3), s["total_pnl"], s["avg"]])
        print(f"\nwrote {out}")
    return summary


def do_demo(out_html: str, workdir: str = "demo-out",
            db: str | None = None) -> list[dict]:
    """First look: sample matrix across studies/datasets, then compare."""
    here = os.path.dirname(os.path.abspath(__file__))
    chop = os.path.join(here, "sample_data.csv")
    trend = os.path.join(here, "sample_trend.csv")
    replay = os.path.join(here, "params.replay.json")
    orion = os.path.join(here, "params.orion.json")
    matrix = [
        (chop, replay, "chop", "demo-replay-chop", ""),
        (trend, replay, "trend", "demo-replay-trend", ""),
        (chop, replay, "chop-split", "demo-replay-split", "frac:0.5"),
        (chop, orion, "orion-chop", "demo-orion-chop", ""),
        (trend, orion, "orion-trend", "demo-orion-trend", ""),
    ]
    for data, params, sub, tag, split in matrix:
        do_run(data, params, os.path.join(workdir, sub), quiet=True,
               tag=tag, split=split, db=db)
    do_walkforward(chop, replay, os.path.join(workdir, "wf"),
                   train=4, test=4, step=4, tag="demo-wf-chop", db=db)
    return do_compare(db, "sample", "", out_html)


def main() -> None:
    ap = argparse.ArgumentParser(description="Study backtester + comparison system")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_common(p):
        p.add_argument("--db", default=None, help="results db (default backtest/results.db)")
        p.add_argument("--results-dir", default=None)
        p.add_argument("--no-log", action="store_true", help="skip results store")

    p = sub.add_parser("run", help="single backtest")
    p.add_argument("--data", required=True)
    p.add_argument("--params", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--tag", default="")
    p.add_argument("--split", default="", help="'frac:0.7' or 'YYYY-MM-DD[ HH:MM]' OOS start")
    add_common(p)

    p = sub.add_parser("sweep", help="grid sweep over strategy_params")
    p.add_argument("--data", required=True)
    p.add_argument("--params", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--tag-prefix", default="")
    add_common(p)

    p = sub.add_parser("walkforward", help="rolling train/test, aggregate OOS")
    p.add_argument("--data", required=True)
    p.add_argument("--params", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--train", type=int, default=200)
    p.add_argument("--test", type=int, default=50)
    p.add_argument("--step", type=int, default=50)
    p.add_argument("--embargo-days", type=int, default=1,
                   help="drop this many leading days per test window (0=off)")
    p.add_argument("--tag", default="")
    add_common(p)

    p = sub.add_parser("compare", help="leaderboard + HTML report from the store")
    p.add_argument("--dataset", default="", help="filter (substring of data path)")
    p.add_argument("--study", default="", help="filter (exact strategy name)")
    p.add_argument("--out", required=True, help="report.html path")
    p.add_argument("--top", type=int, default=0)
    p.add_argument("--no-baseline", action="store_true")
    p.add_argument("--tick-size", type=float, default=0.25)
    p.add_argument("--tick-value", type=float, default=12.50)
    p.add_argument("--db", default=None)

    p = sub.add_parser("demo", help="run the sample matrix + report (first look)")
    p.add_argument("--out", default="demo-report.html", help="report path")
    p.add_argument("--workdir", default="demo-out", help="per-run output dir")
    p.add_argument("--db", default=None)

    p = sub.add_parser("sessions", help="rank time-of-day sessions from a journal CSV")
    p.add_argument("--journal", required=True)
    p.add_argument("--point-value", type=float, default=1.0)
    p.add_argument("--session", action="append", default=[],
                   help="NAME=HH:MM-HH:MM (repeatable, overrides defaults)")
    p.add_argument("--out", default="", help="optional summary CSV path")
    p = sub.add_parser("matrix", help="strategy x session matrix (entries-only slices)")
    p.add_argument("--data", required=True)
    p.add_argument("--params", action="append", required=True,
                   help="run params file, repeatable (carries strategy+regime)")
    p.add_argument("--session", action="append", default=[],
                   help="NAME=HH:MM-HH:MM (repeatable, defaults to journal sessions)")
    p.add_argument("--out", default="", help="matrix CSV path")

    p = sub.add_parser("guide", help="interactive params builder for a first run")
    p.add_argument("--out", default="params.guide.json")

    p = sub.add_parser("promote", help="promotion gate for new signals (OOS checks)")
    p.add_argument("--run", required=True, help="run id in the store")
    p.add_argument("--db", default=None)

    p = sub.add_parser("verify", help="reproduce a logged run (audit check)")
    p.add_argument("--run", required=True, help="run id in the store")
    p.add_argument("--db", default=None)

    p = sub.add_parser("remote", help="run on a remote box over SSH")
    p.add_argument("--host", required=True)
    p.add_argument("--data", required=True)
    p.add_argument("--params", required=True)
    p.add_argument("--remote-dir", default="/tmp/bt")
    p.add_argument("--job", default="job")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--notify", default="", help="email results on completion")
    p.add_argument("--sender", default="bt@localhost")
    p = sub.add_parser("scid", help="convert Sierra .scid ticks to bar CSV (no chart needed)")
    p.add_argument("--scid", required=True, help="input .scid tick file")
    p.add_argument("--out", required=True, help="output bar CSV")
    p.add_argument("--minutes", type=int, default=5, help="bar size in minutes")
    p.add_argument("--divisor", type=float, default=100.0, help="raw tick price divisor (legacy hundredths files: 100; current SYM-YYYYMM points files: 1)")
    p.add_argument("--tz-offset", type=float, default=0.0, help="hours added to output stamps (chart join: -4 EDT)")
    p.add_argument("--footprint", default="", help="also write per-level footprint sidecar CSV here")
    p.add_argument("--check-dly", default="", help="verify one day YYYY/MM/DD against the .dly row")
    p.add_argument("--dly", default="", help=".dly check file (default: <scid>.dly)")

    a = ap.parse_args()
    if a.cmd == "run":
        do_run(a.data, a.params, a.out, tag=a.tag, split=a.split,
               db=a.db, log=not a.no_log, results_dir=a.results_dir)
    elif a.cmd == "sweep":
        do_sweep(a.data, a.params, a.out, db=a.db, tag_prefix=a.tag_prefix,
                 log=not a.no_log, results_dir=a.results_dir)
    elif a.cmd == "walkforward":
        do_walkforward(a.data, a.params, a.out, train=a.train, test=a.test,
                       step=a.step, embargo_days=a.embargo_days, tag=a.tag,
                       db=a.db, log=not a.no_log, results_dir=a.results_dir)
    elif a.cmd == "compare":
        do_compare(a.db, a.dataset, a.study, a.out, top=a.top,
                   baseline=not a.no_baseline,
                   tick_size=a.tick_size, tick_value=a.tick_value)
    elif a.cmd == "demo":
        do_demo(a.out, workdir=a.workdir, db=a.db)
    elif a.cmd == "sessions":
        spec = []
        for s in a.session:
            name, _, rng = s.partition("=")
            start, _, end = rng.partition("-")
            spec.append((name.strip(), start.strip(), end.strip()))
        do_sessions(a.journal, point_value=a.point_value,
                    sessions=spec or None, out=a.out)
    elif a.cmd == "matrix":
        spec = []
        for s in a.session:
            name, _, rng = s.partition("=")
            spec.append((name.strip(), rng.strip()))
        do_matrix(a.data, a.params, a.out, sessions=spec or None)
    elif a.cmd == "guide":
        from guide import run_guide
        run_guide(a.out)
    elif a.cmd == "promote":
        if not do_promote(a.db, a.run):
            raise SystemExit(1)
    elif a.cmd == "verify":
        if not do_verify(a.db, a.run):
            raise SystemExit(1)
    elif a.cmd == "remote":
        import tempfile
        from remote import build_bundle, run_remote
        src = os.path.dirname(os.path.abspath(__file__))
        with tempfile.NamedTemporaryFile(suffix=".tgz", delete=False) as f:
            bundle = f.name
        build_bundle(src, a.data, a.params, bundle, job_name=a.job)
        cmds, local_out = run_remote(a.host, bundle, remote_dir=a.remote_dir,
                                     job_name=a.job, dry_run=a.dry_run,
                                     notify=a.notify or None)
        for c in cmds:
            print(c)
        os.unlink(bundle)
        if a.notify and not a.dry_run:
            from notify import send
            rep = os.path.join(local_out, "report.txt")
            body = open(rep).read() if os.path.exists(rep) else "(no report found)"
            try:
                print(send(a.notify, f"[bt] {a.job} complete", body,
                           sender=a.sender))
            except RuntimeError as e:
                print(f"warning: {e}")
    elif a.cmd == "scid":
        from scid import convert, check_dly
        import os as _os
        n = convert(a.scid, a.out, a.minutes, a.divisor, a.tz_offset,
                    a.footprint)
        from data import load_csv
        bars = load_csv(a.out)  # fail fast if the contract broke
        if len(bars) != n:
            raise RuntimeError(f"wrote {n} bars but loaded {len(bars)}")
        print(f"validated: {len(bars)} bars load clean "
              f"({bars[0].stamp} -> {bars[-1].stamp})")
        if a.check_dly:
            base = _os.path.basename(a.scid)
            if base.endswith(".scid"):
                base = base[:-len(".scid")]
            dly = a.dly or _os.path.join(_os.path.dirname(a.scid), base + ".dly")
            if not check_dly(a.out, dly, a.check_dly):
                raise SystemExit(1)


if __name__ == "__main__":
    main()
