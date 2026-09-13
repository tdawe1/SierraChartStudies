#!/usr/bin/env python3
"""Grid sweep for the StatArb pairs port (two datasets; bt.py sweep is
single-series). Each combo: statarb_spread trades -> pairs_trade_pnl ->
pairs_metrics, written as <out>/<name>/summary.json (same shape as
bt.py sweep rows) plus params.json. Stdlib only.

Example:
  python3 sweep_pairs.py --leg1 /home/user/bt-data/esu26-10m.csv \\
      --leg2 /home/user/bt-data/nqu26-10m.csv --out runs/opt-sep13/pairs-esnq \\
      --leg1-usd-pt 50 --leg2-usd-pt 20
"""

import argparse
import itertools
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data import load_csv
from strategies import statarb_spread, pairs_trade_pnl, pairs_metrics

GRID = {
    "hedge_lookback": [30, 60],
    "z_lookback": [30, 60],
    "entry_z": [1.5, 2.0, 2.5],
    "min_corr": [0.5, 0.7],
}


def main(argv=None):
    ap = argparse.ArgumentParser(description="StatArb pairs grid sweep")
    ap.add_argument("--leg1", required=True)
    ap.add_argument("--leg2", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--leg1-usd-pt", type=float, required=True)
    ap.add_argument("--leg2-usd-pt", type=float, required=True)
    ap.add_argument("--leg1-tick-value", type=float, default=12.50)
    ap.add_argument("--leg2-tick-value", type=float, default=5.0)
    ap.add_argument("--tag-prefix", default="pairs")
    a = ap.parse_args(argv)
    b1 = load_csv(a.leg1)
    b2 = load_csv(a.leg2)
    keys = sorted(GRID)
    rows = []
    for combo in itertools.product(*(GRID[k] for k in keys)):
        sp = dict(zip(keys, combo))
        name = "__".join(f"{k}={sp[k]}" for k in keys)
        d = os.path.join(a.out, f"{a.tag_prefix}_{name}")
        os.makedirs(d, exist_ok=True)
        trades = statarb_spread(b1, b2, **sp)
        pnls = [pairs_trade_pnl(t, b1, b2, a.leg1_usd_pt, a.leg2_usd_pt,
                                leg1_tick_value=a.leg1_tick_value,
                                leg2_tick_value=a.leg2_tick_value)
                for t in trades]
        m = pairs_metrics(pnls)
        json.dump({"metrics": m}, open(os.path.join(d, "summary.json"), "w"))
        json.dump(sp, open(os.path.join(d, "params.json"), "w"), indent=1)
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
