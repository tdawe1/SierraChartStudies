#!/usr/bin/env python3
"""Headless walk-forward paper trader: all candidate ORB systems.

Deterministic and idempotent per system: every run converts the latest
ticks, replays the frozen config over full history, and appends only
trades not already in that system's ledger. Safe to run daily.

  python3 paper_orb.py --run   # update all ledgers in SYSTEMS
  python3 paper_orb.py --run --skip-convert  # reuse existing CSVs, no scid import

Contract rollover: override any system's .scid path without editing code:

  PAPER_ORB_SCID_NQ_ALL=/path/to/NQZ26-CME.scid python3 paper_orb.py --run

(env name is PAPER_ORB_SCID_ + system name, uppercased, '-' -> '_').

Prerequisite for conversion: a `scid` module exposing
`convert(scid_path, csv_path, bar_minutes, divisor, tz_offset)`.
Without it, use --skip-convert with pre-converted per-system CSVs at
/home/user/bt-data/paper-<name>.csv.

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

def _scid_path(name: str, default: str) -> str:
    env = "PAPER_ORB_SCID_" + name.upper().replace("-", "_")
    return os.environ.get(env, default)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="ORB paper trader (all systems)")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--skip-convert", action="store_true",
                    help="reuse existing per-system CSVs instead of converting .scid")
    a = ap.parse_args(argv)
    if not a.run:
        ap.print_help()
        return 0
    try:
        from scid import convert
    except ImportError:
        convert = None
    from data import load_csv
    from strategies import orb_retrace, orb_trade_pnl
    if convert is None and not a.skip_convert:
        print("paper-orb: no 'scid' converter module available; "
              "pre-convert the bars or re-run with --skip-convert",
              file=sys.stderr)
        return 2
    for name, (scid, divisor, usd_pt, _tv, override, ledger_dir) in SYSTEMS.items():
        bars_path = f"/home/user/bt-data/paper-{name}.csv"
        ledger = os.path.join(_HERE, "runs", ledger_dir, "ledger.csv")
        if a.skip_convert:
            if not os.path.exists(bars_path):
                print(f"paper-orb [{name}]: {bars_path} missing; "
                      f"convert {scid} first", file=sys.stderr)
                continue
        else:
            try:
                convert(_scid_path(name, scid), bars_path, 1, divisor, -4.0)
            except OSError as e:
                # Dead/missing contract must not block later systems.
                print(f"paper-orb [{name}]: convert failed ({e}); skipping",
                      file=sys.stderr)
                continue
        try:
            bars = load_csv(bars_path)
        except (OSError, ValueError) as e:
            print(f"paper-orb [{name}]: cannot load {bars_path} ({e}); skipping",
                  file=sys.stderr)
            continue
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
