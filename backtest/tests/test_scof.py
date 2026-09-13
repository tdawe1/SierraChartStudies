"""Exactness gate for the SCOF absorption port.

scof_absorption must reproduce the chart's 4 absorption markers
bit-for-bit on the capture window (footprint from the same ticks via
scid stream_footprint). Needs the capture CSV + footprint sidecar;
skipped without them.
"""

import csv
import sys
import unittest
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CSV = Path("/home/user/.wine/drive_c/SierraChart/Data/bt_scof_ym13.csv")
FP = Path("/home/user/bt-data/ymu26-10m-fp-levels.csv")


def load_footprint(path):
    fp = defaultdict(list)
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            fp[" ".join(r["DateTime"].split())].append(
                (round(float(r["Price"])), float(r["BidVolume"]),
                 float(r["AskVolume"])))
    return {k: sorted(v) for k, v in fp.items()}


@unittest.skipIf(not CSV.exists(), "needs chart capture bt_scof_ym13.csv")
@unittest.skipIf(not FP.exists(), "needs footprint sidecar")
class ScofExactTests(unittest.TestCase):
    def test_markers_match_chart_export(self):
        from data import load_csv
        from strategies import scof_absorption
        bars = load_csv(str(CSV))
        fp = load_footprint(str(FP))
        foot = [fp.get(" ".join(b.stamp.split())[:16], []) for b in bars]
        missing = sum(1 for f in foot if not f)
        sig = scof_absorption(bars, foot, tick_size=1.0)
        for b, s in zip(bars, sig):
            want = (1 if b.signal_long else (-1 if b.signal_short else 0))
            self.assertEqual(s, want, f"marker mismatch at {b.stamp}")
        print(f"\n[test] {len(bars)} bars, {missing} without footprint, "
              f"{sum(1 for s in sig if s)} port markers")


if __name__ == "__main__":
    unittest.main()
