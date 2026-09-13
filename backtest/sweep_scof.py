#!/usr/bin/env python3
"""Grid sweep for the SCOF absorption port (needs footprint sidecar;
bt.py sweep is single-series). Each combo: port signals -> materialized
CSV -> bt.do_run (signal_replay, logged). Stdlib only.

Example:
  python3 sweep_scof.py --bars /home/user/bt-data/ymu26-10m.csv \\
      --fp /home/user/bt-data/ymu26-10m-fp-levels.csv \\
      --params runs/opt-sep13/scof_base.json --out runs/opt-sep13/scof-ym
"""

import argparse
import csv
import itertools
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data import load_csv
from strategies import scof_absorption
from bt import do_run

GRID = {
    "min_opp_levels": [2, 3],
    "enable_tapering": [True, False],
    "enable_diag": [True, False],
    "min_growth_pct": [5.0, 8.0],
    "diag_ratio_pct": [200.0, 300.0],
}


def load_footprint(path, tick_size=1.0):
    fp = defaultdict(list)
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            fp[r["DateTime"]].append(
                (round(float(r["Price"]) / tick_size), float(r["BidVolume"]),
                 float(r["AskVolume"])))
    return fp


def main(argv=None):
    ap = argparse.ArgumentParser(description="SCOF absorption grid sweep")
    ap.add_argument("--bars", required=True)
    ap.add_argument("--fp", required=True)
    ap.add_argument("--params", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--tag-prefix", default="scof")
    ap.add_argument("--tick-size", type=float, default=1.0)
    a = ap.parse_args(argv)
    bars = load_csv(a.bars)
    fp = load_footprint(a.fp, a.tick_size)
    foot = [sorted(fp.get(b.stamp, [])) for b in bars]
    keys = sorted(GRID)
    print(f"{len(bars)} bars, "
          f"{sum(1 for f in foot if not f)} without footprint")
    for combo in itertools.product(*(GRID[k] for k in keys)):
        sp = dict(zip(keys, combo))
        name = "__".join(f"{k}={sp[k]}" for k in keys)
        d = os.path.join(a.out, f"{a.tag_prefix}_{name}")
        os.makedirs(d, exist_ok=True)
        sig = scof_absorption(bars, foot, tick_size=a.tick_size, **sp)
        assert len(sig) == len(bars), "signal/bar length mismatch"
        sig_path = os.path.join(d, "signals.csv")
        # Materialize from the accepted bars (not the raw file): load_csv
        # drops bad-OHLC rows, so zipping signals onto raw rows would
        # silently shift every post-drop signal. do_run reloads this file
        # through load_csv, which accepts this exact column contract.
        with open(sig_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=[
                "DateTime", "Open", "High", "Low", "Close", "Volume",
                "BidVolume", "AskVolume", "MaxDelta", "MinDelta",
                "SignalLong", "SignalShort"])
            w.writeheader()
            for b, s in zip(bars, sig):
                w.writerow({
                    "DateTime": b.stamp, "Open": b.open, "High": b.high,
                    "Low": b.low, "Close": b.close, "Volume": b.volume,
                    "BidVolume": b.bidvol, "AskVolume": b.askvol,
                    "MaxDelta": b.maxdelta, "MinDelta": b.mindelta,
                    "SignalLong": "1" if s == 1 else "0",
                    "SignalShort": "1" if s == -1 else "0"})
        n = sum(1 for s in sig if s)
        res = do_run(sig_path, a.params, os.path.join(d, "run"),
                     tag=f"{a.tag_prefix} {name}", quiet=True)
        m = res["metrics"]
        pf = m["profit_factor"]
        print(f"{m['total_pnl']:>12,.2f} n={m['trades']:>4} "
              f"pf={pf if pf is not None else '-':} {name}")


if __name__ == "__main__":
    main()
