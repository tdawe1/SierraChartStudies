"""Math verification for the MondayDip + GoldenCross ports (no chart
truth: both were silent/unconfigured on the 20-day 10-min window).
All expectations hand-computed from the C++ rules.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine import Bar
from strategies import monday_dip, golden_cross


def dbar(date, o, h, l, c, v=1000):
    return Bar(idx=0, stamp=f"{date} 16:00", open=o, high=h, low=l,
               close=c, volume=v, bidvol=v // 2, askvol=v - v // 2)


class MondayDipTests(unittest.TestCase):
    def test_washout_entry_and_bounce_exit(self):
        # 2026-09-07 is a Monday
        bars = [dbar(f"2026-09-0{d}", 90 + d, 0, 0, 90 + d) for d in (1, 2, 3, 4, 5)]
        bars.append(dbar("2026-09-07", 104, 105, 99, 100))   # -3.8% Monday washout
        bars.append(dbar("2026-09-08", 100, 102, 99, 101))   # bounce over close[4]=98
        sig = monday_dip(bars, trend_len=5, dip_factor=0.97,
                         exit_bars=10, roc_len=2)
        self.assertEqual(sig[5], 1)
        self.assertEqual(sig[6], -1)
        self.assertEqual(sum(1 for s in sig if s != 0), 2)

    def test_tuesday_washout_ignored(self):
        bars = [dbar(f"2026-09-0{d}", 90 + d, 0, 0, 90 + d) for d in (1, 2, 3, 4, 5)]
        bars.append(dbar("2026-09-08", 104, 105, 99, 100))   # Tuesday, not Monday
        sig = monday_dip(bars, trend_len=5, dip_factor=0.97,
                         exit_bars=10, roc_len=2)
        self.assertEqual(sig, [0] * len(bars))

    def test_time_stop_exit(self):
        bars = [dbar(f"2026-09-0{d}", 90 + d, 0, 0, 90 + d) for d in (1, 2, 3, 4, 5)]
        bars.append(dbar("2026-09-07", 104, 105, 99, 100))
        for k in range(8, 12):  # 94s never bounce (close[4] is 95)
            bars.append(dbar(f"2026-09-{k:02d}", 94, 95, 93, 94))
        sig = monday_dip(bars, trend_len=5, dip_factor=0.97,
                         exit_bars=3, roc_len=2)
        self.assertEqual(sig[5], 1)
        self.assertEqual(sig[8], -1)  # held 5,6,7 -> timeout at 8


class GoldenCrossTests(unittest.TestCase):
    def test_cross_entry_and_death_exit(self):
        closes = [100, 100, 100, 100, 100, 106, 107, 107, 107, 100, 99]
        bars = [dbar(f"2026-09-{i + 1:02d}", c, c, c, c) for i, c in enumerate(closes)]
        sig = golden_cross(bars, fast_len=3, slow_len=5,
                           max_extension=1.05, index_slow_len=5)
        self.assertEqual(sig[5], 1)
        self.assertEqual(sig[9], -1)
        pos = 0
        for s in sig:  # long-only: exits (-1) only ever close an open long
            if s == 1:
                self.assertEqual(pos, 0)
                pos = 1
            elif s == -1:
                self.assertEqual(pos, 1)
                pos = 0
        self.assertEqual(pos, 0)

    def test_extension_guard_blocks_chase(self):
        closes = [100, 100, 100, 100, 100, 107, 107, 107, 107, 100, 99]
        bars = [dbar(f"2026-09-{i + 1:02d}", c, c, c, c) for i, c in enumerate(closes)]
        sig = golden_cross(bars, fast_len=3, slow_len=5,
                           max_extension=1.05, index_slow_len=5)
        self.assertEqual(sig[5], 0)  # 107 >= 1.05 * 101

    def test_bear_regime_blocks(self):
        closes = [110, 108, 106, 104, 102, 103, 104, 105, 106, 100, 99]
        bars = [dbar(f"2026-09-{i + 1:02d}", c, c, c, c) for i, c in enumerate(closes)]
        sig = golden_cross(bars, fast_len=3, slow_len=5,
                           max_extension=2.0, index_slow_len=5)
        # close[5]=103 vs idx SMA(110,108,106,104,103)=106.2 -> halt
        self.assertEqual(sig[5], 0)


if __name__ == "__main__":
    unittest.main()
