"""sweep_scof materialization: signals align with filtered bars, not raw rows."""

import csv
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from data import load_csv
from sweep_scof import main as sweep_main

HERE = Path(__file__).resolve().parent

ROWS = [
    ("2026-08-03 08:30", 100, 102, 100, 101),
    ("2026-08-03 08:35", 101, 103, 101, 102),
    ("2026-08-03 08:40", 102, 101, 103, 102),  # bad OHLC: dropped by load_csv
    ("2026-08-03 08:45", 102, 104, 102, 103),
    ("2026-08-03 08:50", 103, 105, 103, 104),
]


class SweepScofTests(unittest.TestCase):
    def test_signals_cover_accepted_bars_only(self):
        with tempfile.TemporaryDirectory() as td:
            bars = os.path.join(td, "bars.csv")
            with open(bars, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["DateTime", "Open", "High", "Low", "Close",
                            "Volume"])
                for stamp, o, h, l, c in ROWS:
                    w.writerow([stamp, o, h, l, c, 10])
            fp = os.path.join(td, "fp.csv")
            with open(fp, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["DateTime", "Price", "BidVolume", "AskVolume"])
                for stamp, o, h, l, c in ROWS:
                    w.writerow([stamp, c, 4, 6])
            out = os.path.join(td, "out")
            sweep_main(["--bars", bars, "--fp", fp, "--params",
                        str(HERE.parent / "params.replay.json"),
                        "--out", out, "--tick-size", "1.0"])
            accepted = load_csv(bars)
            self.assertEqual(len(accepted), len(ROWS) - 1)
            combos = sorted(os.listdir(out))
            self.assertTrue(combos)
            for combo in combos:
                sig_path = os.path.join(out, combo, "signals.csv")
                sig_bars = load_csv(sig_path)
                self.assertEqual(len(sig_bars), len(accepted))
                self.assertEqual([b.stamp for b in sig_bars],
                                 [b.stamp for b in accepted])


if __name__ == "__main__":
    unittest.main()
