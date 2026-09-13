#!/usr/bin/env python3
"""Headless walk-forward paper trader: all candidate ORB systems.

Deterministic and idempotent per system: every run converts the latest
ticks, replays the frozen config over full history, and appends only
trades not already in that system's ledger. Safe to run daily.

  python3 paper_orb.py --run   # update all ledgers in SYSTEMS

Systems: nq-all (primary, replicated), nq-wt (weekday hypothesis,
forward test only), ym-val (secondary, replicated IS+OOS).
"""

import csv
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_HERE = os.path.dirname(os.path.abspath(__file__))
_DATA = "/home/user/.wine/drive_c/SierraChart/Data"
_BASE_CFG = dict(or_start_min=510, or_end_min=525, breakout_ticks=8,
                vp_level=2, entry_cutoff_min=720, eod_flat_min=895,
                use_shorts=True)
SYSTEMS = {
    # name: (scid, divisor, usd/pt, tick value, cfg override, ledger dir)
    "nq-all": (f"{_DATA}/NQU26-CME.scid", 1.0, 20.0, 5.0, {},
               "paper-orb-nq"),
    "nq-wt": (f"{_DATA}/NQU26-CME.scid", 1.0, 20.0, 5.0,
              {"trade_days": (2, 3)}, "paper-orb-nq-wt"),
    "ym-val": (f"{_DATA}/YMU26-CBOT.scid", 1.0, 5.0, 5.0,
               {"tick_size": 1.0}, "paper-orb-ym"),
}
FEE = 2.10


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="ORB paper trader (all systems)")
    ap.add_argument("--run", action="store_true")
    a = ap.parse_args(argv)
    if not a.run:
        ap.print_help()
        return 0
    from scid import convert
    from data import load_csv
    from strategies import orb_retrace, orb_trade_pnl
    for name, (scid, divisor, usd_pt, _tv, override, ledger_dir) in SYSTEMS.items():
        bars_path = f"/home/user/bt-data/paper-{name}.csv"
        ledger = os.path.join(_HERE, "runs", ledger_dir, "ledger.csv")
        convert(scid, bars_path, 1, divisor, -4.0)
        bars = load_csv(bars_path)
        cfg = dict(_BASE_CFG)
        cfg.setdefault("tick_size", 0.25)
        cfg.update(override)
        trades = orb_retrace(bars, **cfg)
        os.makedirs(os.path.dirname(ledger), exist_ok=True)
        seen = set()
        if os.path.exists(ledger):
            with open(ledger, newline="") as f:
                for r in csv.DictReader(f):
                    seen.add((r["entry_stamp"], r["dir"]))
        n_new = 0
        cum = 0.0
        if seen:
            with open(ledger, newline="") as f:
                rows = list(csv.DictReader(f))
                cum = float(rows[-1]["cum_pnl"]) if rows else 0.0
        else:
            with open(ledger, "w", newline="") as f:
                csv.writer(f).writerow(
                    ["entry_stamp", "dir", "entry_price", "exit_stamp",
                     "exit_price", "reason", "pnl", "cum_pnl"])
        with open(ledger, "a", newline="") as f:
            w = csv.writer(f)
            for t in trades:
                key = (bars[t["entry"]].stamp, str(t["dir"]))
                if key in seen:
                    continue
                pnl = orb_trade_pnl(t, usd_pt, FEE)
                cum += pnl
                w.writerow([bars[t["entry"]].stamp, t["dir"],
                            f"{t['entry_price']:.2f}", bars[t["exit"]].stamp,
                            f"{t['exit_price']:.2f}", t["reason"],
                            f"{pnl:.2f}", f"{cum:.2f}"])
                n_new += 1
        print(f"paper-orb [{name}]: {len(trades)} replayed, "
              f"{n_new} new, cum ${cum:,.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
