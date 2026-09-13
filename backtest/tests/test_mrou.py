"""Exactness gate for the MROU offline port.

mrou_ou must reproduce the chart-exported Buy/Sell markers bit-for-bit
on the chart window before any bulk sweep is trusted. Needs the live
capture CSV; skipped without it.
"""

import unittest
from pathlib import Path

CSV = Path("/home/user/.wine/drive_c/SierraChart/Data/bt_mrou_ym13.csv")


@unittest.skipIf(not CSV.exists(), "needs chart capture bt_mrou_ym13.csv")
class MrouExactTests(unittest.TestCase):
    def test_markers_match_chart_export(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from data import load_csv
        from strategies import mrou_ou
        bars = load_csv(str(CSV))
        sig = mrou_ou(bars, tick_size=1.0)
        self.assertEqual(len(bars), len(sig), "signal/bar count mismatch")
        for b, s in zip(bars, sig):
            want = (1 if b.signal_long else (-1 if b.signal_short else 0))
            self.assertEqual(s, want, f"marker mismatch at {b.stamp}")


if __name__ == "__main__":
    unittest.main()
