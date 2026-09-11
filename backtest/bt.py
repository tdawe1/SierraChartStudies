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
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data import load_csv  # noqa: E402
from engine import Bar, EngineConfig, Trade, metrics, run  # noqa: E402
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
         results_dir: str | None) -> str:
    from store import connect, default_results_dir, log_run, make_id
    run_id = make_id(study, dataset, {**params, "tag": tag, "split": split})
    dest = os.path.join(results_dir or default_results_dir(), run_id)
    os.makedirs(dest, exist_ok=True)
    for fn in ("trades.csv", "equity.csv", "summary.json", "report.txt"):
        src = os.path.join(out_dir, fn)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(dest, fn))
    con = connect(db)
    try:
        log_run(con, run_id, study, dataset, tag, split, params,
                res["metrics"], res.get("is_metrics"), res.get("oos_metrics"),
                artifact_dir=dest)
    finally:
        con.close()
    return run_id


def do_run(data_path: str, params_path: str, out_dir: str,
           overrides: dict | None = None, quiet: bool = False,
           tag: str = "", split: str = "", db: str | None = None,
           log: bool = True, results_dir: str | None = None) -> dict:
    params = _load_params(params_path, overrides)
    bars = load_csv(data_path)
    strat = STRATEGIES[params.get("strategy", "signal_replay")]
    signals = strat(bars, **_strategy_params(params))
    cfg = _make_cfg(params)
    is_bars, oos_bars = split_bars(bars, split)
    res = run(bars, signals, cfg)
    if oos_bars:
        n_is = len(is_bars)
        res["is_metrics"] = run(is_bars, signals[:n_is], cfg)["metrics"]
        res["oos_metrics"] = run(oos_bars, signals[n_is:], cfg)["metrics"]
    write_outputs(out_dir, res, params, quiet=quiet)
    if log:
        run_id = _log(db, params.get("strategy", "signal_replay"), data_path,
                      tag, split, params, res, out_dir, results_dir)
        if not quiet:
            print(f"logged: {run_id}")
        res["run_id"] = run_id
    return res


def write_outputs(out_dir: str, res: dict, params: dict, quiet: bool = False) -> None:
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "trades.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["dir", "entry_idx", "entry_time", "entry", "exit_idx",
                    "exit_time", "exit", "reason", "pnl", "bars_held", "qty"])
        for t in res["trades"]:
            w.writerow([t.direction, t.entry_idx, t.entry_stamp, t.entry_price,
                        t.exit_idx, t.exit_stamp, t.exit_price, t.exit_reason,
                        round(t.pnl, 2), t.bars_held, t.qty])
    with open(os.path.join(out_dir, "summary.json"), "w") as f:
        json.dump({"params": params, "metrics": res["metrics"],
                   "is_metrics": res.get("is_metrics"),
                   "oos_metrics": res.get("oos_metrics")}, f, indent=2)
    m = res["metrics"]
    lines = [
        f"strategy   : {params.get('strategy')}",
        f"trades     : {m['trades']}  (W {m['wins']} / L {m['losses']})",
        f"win rate   : {m['win_rate'] * 100:.1f}%",
        f"total PnL  : ${m['total_pnl']:,.2f}",
        f"expectancy : ${m['expectancy']:,.2f} / trade",
        f"avg win    : ${m['avg_win']:,.2f}   avg loss : ${m['avg_loss']:,.2f}",
        f"profit fac : {m['profit_factor']}",
        f"max DD     : ${m['max_drawdown']:,.2f}",
        f"calmar     : {m['calmar']}   sharpeT : {m['sharpe_trade']}",
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
        lines.append(f"IS  PnL    : ${res['is_metrics']['total_pnl']:,.2f} "
                     f"({res['is_metrics']['trades']} trades)")
        lines.append(f"OOS PnL    : ${res['oos_metrics']['total_pnl']:,.2f} "
                     f"({res['oos_metrics']['trades']} trades)")
    with open(os.path.join(out_dir, "report.txt"), "w") as f:
        f.write("\n".join(lines) + "\n")
    if not quiet:
        print("\n".join(lines))


def do_sweep(data_path: str, params_path: str, out_dir: str,
             db: str | None = None, tag_prefix: str = "",
             log: bool = True, results_dir: str | None = None) -> None:
    with open(params_path) as f:
        base = json.load(f)
    grid = base.pop("grid", {})
    keys = sorted(grid)
    rows = []
    for combo in itertools.product(*(grid[k] for k in keys)):
        ov = {"strategy_params": dict(zip(keys, combo))}
        tag = "__".join(f"{k}={v}" for k, v in zip(keys, combo))
        if tag_prefix:
            tag = f"{tag_prefix} {tag}"
        sub = os.path.join(out_dir, tag.replace(" ", "_"))
        res = _run_with(data_path, base, ov, sub, tag=tag, split="",
                        db=db, log=log, results_dir=results_dir)
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
              log: bool = True, results_dir: str | None = None) -> dict:
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        merged = json.loads(json.dumps(base))
        merged.setdefault("strategy_params", {}).update(
            overrides.get("strategy_params", {}))
        json.dump(merged, f)
        tmp = f.name
    try:
        return do_run(data_path, tmp, out_dir, quiet=True, tag=tag,
                      split=split, db=db, log=log, results_dir=results_dir)
    finally:
        os.unlink(tmp)


def do_walkforward(data_path: str, params_path: str, out_dir: str,
                   train: int = 200, test: int = 50, step: int = 50,
                   tag: str = "", db: str | None = None, log: bool = True,
                   results_dir: str | None = None) -> dict:
    """Rolling train/test windows; aggregate the out-of-sample trades."""
    params = _load_params(params_path)
    bars = load_csv(data_path)
    strat = STRATEGIES[params.get("strategy", "signal_replay")]
    signals = strat(bars, **_strategy_params(params))
    cfg = _make_cfg(params)
    all_trades: list[Trade] = []
    equity = [0.0]
    stamps = [bars[0].stamp if bars else ""]
    cum = 0.0
    windows = []
    s = max(train, 1)
    while s < len(bars):
        e = min(s + test, len(bars))
        seg = run(bars[s:e], signals[s:e], cfg)
        for t in seg["trades"]:
            all_trades.append(t)
        # stitch: shift segment equity by running total
        base = cum
        for v, st in zip(seg["equity"][1:], seg["stamps"][1:]):
            cum = base + (v - seg["equity"][0])
            equity.append(cum)
            stamps.append(st)
        windows.append({"start": bars[s].stamp, "end": bars[e - 1].stamp,
                        "trades": len(seg["trades"]),
                        "pnl": seg["metrics"]["total_pnl"]})
        if e >= len(bars):
            break
        s += step
    res = {"trades": all_trades, "equity": equity, "stamps": stamps,
           "metrics": metrics(all_trades, equity), "windows": windows,
           "oos_metrics": metrics(all_trades, equity)}
    write_outputs(out_dir, res, params, quiet=True)
    with open(os.path.join(out_dir, "windows.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["start", "end", "trades", "pnl"])
        for wd in windows:
            w.writerow([wd["start"], wd["end"], wd["trades"], wd["pnl"]])
    print(f"walkforward: {len(windows)} windows, {len(all_trades)} OOS trades, "
          f"PnL ${res['metrics']['total_pnl']:,.2f}")
    if log:
        run_id = _log(db, params.get("strategy", "signal_replay"), data_path,
                      tag or f"wf train={train} test={test} step={step}",
                      split="walkforward", params=params, res=res,
                      out_dir=out_dir, results_dir=results_dir)
        print(f"logged: {run_id}")
        res["run_id"] = run_id
    return res


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

    p = sub.add_parser("guide", help="interactive params builder for a first run")
    p.add_argument("--out", default="params.guide.json")

    p = sub.add_parser("remote", help="run on a remote box over SSH")
    p.add_argument("--host", required=True)
    p.add_argument("--data", required=True)
    p.add_argument("--params", required=True)
    p.add_argument("--remote-dir", default="/tmp/bt")
    p.add_argument("--job", default="job")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--notify", default="", help="email results on completion")
    p.add_argument("--sender", default="bt@localhost")

    a = ap.parse_args()
    if a.cmd == "run":
        do_run(a.data, a.params, a.out, tag=a.tag, split=a.split,
               db=a.db, log=not a.no_log, results_dir=a.results_dir)
    elif a.cmd == "sweep":
        do_sweep(a.data, a.params, a.out, db=a.db, tag_prefix=a.tag_prefix,
                 log=not a.no_log, results_dir=a.results_dir)
    elif a.cmd == "walkforward":
        do_walkforward(a.data, a.params, a.out, train=a.train, test=a.test,
                       step=a.step, tag=a.tag, db=a.db, log=not a.no_log,
                       results_dir=a.results_dir)
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
    elif a.cmd == "guide":
        from guide import run_guide
        run_guide(a.out)
    elif a.cmd == "remote":
        import tempfile
        from remote import build_bundle, run_remote
        src = os.path.dirname(os.path.abspath(__file__))
        with tempfile.NamedTemporaryFile(suffix=".tgz", delete=False) as f:
            bundle = f.name
        build_bundle(src, a.data, a.params, bundle, job_name=a.job)
        for c in run_remote(a.host, bundle, remote_dir=a.remote_dir,
                            job_name=a.job, dry_run=a.dry_run,
                            notify=a.notify or None):
            print(c)
        os.unlink(bundle)
        if a.notify and not a.dry_run:
            from notify import send
            outdir = f"./out-{a.job}"
            rep = os.path.join(outdir, "report.txt")
            body = open(rep).read() if os.path.exists(rep) else "(no report found)"
            try:
                print(send(a.notify, f"[bt] {a.job} complete", body,
                           sender=a.sender))
            except RuntimeError as e:
                print(f"warning: {e}")


if __name__ == "__main__":
    main()
