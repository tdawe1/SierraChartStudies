#!/usr/bin/env python3
"""Grid sweep for the ORB retrace port (own fills; bypasses the engine).
Writes <out>/<name>/summary.json + params.json like bt.py sweep rows.

Example:
  python3 sweep_orb.py --data /home/user/bt-data/nqu26-1m.csv \\
      --out runs/opt-sep13/orb-nq --usd-per-pt 20
"""

import argparse
import itertools
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data import load_csv
from strategies import orb_retrace, orb_trade_pnl, pairs_metrics

GRID = {
    "breakout_ticks": [4, 8, 16],
    "vp_level": [0, 1, 2],
}


def main(argv=None):
    ap = argparse.ArgumentParser(description="ORB retrace grid sweep")
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--usd-per-pt", type=float, required=True)
    ap.add_argument("--tag-prefix", default="orb")
    ap.add_argument("--or-start", type=int, default=570)
    ap.add_argument("--or-end", type=int, default=585)
    ap.add_argument("--eod", type=int, default=955)
    ap.add_argument("--cutoffs", default="720,780")
    ap.add_argument("--frm", default="")
    ap.add_argument("--tick-size", type=float, default=0.25)
    ap.add_argument("--to", default="")
    a = ap.parse_args(argv)
    bars = load_csv(a.data)
    if a.frm:
        bars = [b for b in bars if b.stamp[:10] >= a.frm]
    if a.to:
        bars = [b for b in bars if b.stamp[:10] <= a.to]
    grid = dict(GRID)
    grid["entry_cutoff_min"] = [int(x) for x in a.cutoffs.split(",")]
    keys = sorted(grid)
    rows = []
    for combo in itertools.product(*(grid[k] for k in keys)):
        sp = dict(zip(keys, combo))
        name = "__".join(f"{k}={sp[k]}" for k in keys)
        d = os.path.join(a.out, f"{a.tag_prefix}_{name}")
        os.makedirs(d, exist_ok=True)
        trades = orb_retrace(bars, tick_size=a.tick_size,
                             or_start_min=a.or_start, or_end_min=a.or_end,
                             eod_flat_min=a.eod, **sp)
        pnls = [orb_trade_pnl(t, a.usd_per_pt) for t in trades]
        m = pairs_metrics(pnls)
        m["days"] = len({b.stamp[:10] for b in bars})
        json.dump({"metrics": m}, open(os.path.join(d, "summary.json"), "w"))
        # Persist every result-affecting input, not just the grid combo,
        # so a selected artefact reproduces exactly.
        json.dump({"grid": sp, "data": a.data, "frm": a.frm, "to": a.to,
                   "usd_per_pt": a.usd_per_pt, "tick_size": a.tick_size,
                   "or_start": a.or_start, "or_end": a.or_end, "eod": a.eod,
                   "cutoffs": a.cutoffs},
                  open(os.path.join(d, "params.json"), "w"), indent=1)
        rows.append((m["total_pnl"], m["trades"], m["profit_factor"], name))
    rows.sort(reverse=True)
    for total, n, pf, name in rows:
        print(f"{total:>12,.2f} n={n:>4} pf={pf:.3f} {name}")
    pos = sum(1 for r in rows if r[0] > 0)
    print(f"{len(rows)} combos, {pos} profitable")
    if rows:
        print(f"best: {rows[0][3]}  PnL ${rows[0][0]:,.2f}")


if __name__ == "__main__":
    main()
