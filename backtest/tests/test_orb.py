"""Math verification for the ORB retrace port (no chart truth exists;
all expectations hand-computed from the C++ rules). Tick = 1.0.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine import Bar
from strategies import orb_retrace, orb_trade_pnl, _value_area_70, pairs_metrics

DAY = "2026-09-01"


def m(mins, o, h, l, c, v=10):
    return Bar(idx=0, stamp=f"{DAY} {mins // 60:02d}:{mins % 60:02d}",
               open=o, high=h, low=l, close=c, volume=v,
               bidvol=v // 2, askvol=v - v // 2)


def or_bars():
    return [m(570, 100, 102, 100, 101), m(571, 101, 103, 101, 102),
            m(572, 100, 101, 99, 100), m(573, 101, 102, 100, 101),
            m(574, 100, 101, 99, 100)]


class OrbMathTests(unittest.TestCase):
    def test_value_area_hand(self):
        # b0:1.333 b1:2.667 b2:3.333 b3:2.0 b4:0.667 (see module note)
        buckets = [4 / 3, 8 / 3, 10 / 3, 2.0, 2 / 3]
        self.assertEqual(_value_area_70(buckets), (2, 3, 1))

    def test_long_target(self):
        bars = or_bars() + [m(575, 103, 104.5, 103.5, 104),
                            m(576, 104, 104.2, 101.0, 102),
                            m(577, 102, 105.0, 102.0, 104)]
        tr = orb_retrace(bars, tick_size=1.0, or_start_min=570,
                         or_end_min=575, breakout_ticks=0, vp_level=0)
        self.assertEqual(len(tr), 1)
        t = tr[0]
        self.assertEqual(t["dir"], 1)
        self.assertAlmostEqual(t["entry_price"], 101.5)
        self.assertAlmostEqual(t["exit_price"], 104.5)
        self.assertEqual(t["reason"], "target")
        self.assertAlmostEqual(orb_trade_pnl(t, 20.0, fee_per_side=0.0), 60.0)

    def test_short_target(self):
        bars = or_bars() + [m(575, 100, 100.5, 97.5, 98),
                            m(576, 98, 102.0, 97.0, 100),
                            m(577, 100, 100.5, 96.0, 97)]
        tr = orb_retrace(bars, tick_size=1.0, or_start_min=570,
                         or_end_min=575, breakout_ticks=0, vp_level=0)
        self.assertEqual(len(tr), 1)
        t = tr[0]
        self.assertEqual(t["dir"], -1)
        self.assertAlmostEqual(t["entry_price"], 101.5)
        self.assertAlmostEqual(t["exit_price"], 96.5)
        self.assertEqual(t["reason"], "target")

    def test_use_shorts_false_never_shorts(self):
        # Same bars as test_short_target (a clean short setup), but with
        # shorts disabled no short may open on any bar.
        bars = or_bars() + [m(575, 100, 100.5, 97.5, 98),
                            m(576, 98, 102.0, 97.0, 100),
                            m(577, 100, 100.5, 96.0, 97)]
        tr = orb_retrace(bars, tick_size=1.0, or_start_min=570,
                         or_end_min=575, breakout_ticks=0, vp_level=0,
                         use_shorts=False)
        self.assertTrue(all(t["dir"] != -1 for t in tr))

    def test_eod_flat(self):
        bars = or_bars() + [m(575, 103, 104.5, 103.5, 104),
                            m(576, 104, 104.2, 101.0, 102),
                            m(577, 102, 103.0, 102.0, 102.5),
                            m(578, 102.5, 103.0, 102.0, 102.8)]
        tr = orb_retrace(bars, tick_size=1.0, or_start_min=570,
                         or_end_min=575, breakout_ticks=0, vp_level=0,
                         eod_flat_min=578)
        self.assertEqual(len(tr), 1)
        self.assertEqual(tr[0]["reason"], "eod")
        self.assertAlmostEqual(tr[0]["exit_price"], 102.8)


    def test_state_resets_each_day(self):
        base = (or_bars() + [m(575, 103, 104.5, 103.5, 104),
                             m(576, 104, 104.2, 101.0, 102),
                             m(577, 102, 105.0, 102.0, 104)])
        day2 = [Bar(idx=0, stamp=b.stamp.replace("2026-09-01", "2026-09-02"),
                    open=b.open, high=b.high, low=b.low, close=b.close,
                    volume=b.volume, bidvol=b.bidvol, askvol=b.askvol)
                for b in base]
        tr = orb_retrace(base + day2, tick_size=1.0, or_start_min=570,
                         or_end_min=575, breakout_ticks=0, vp_level=0)
        self.assertEqual(len(tr), 2)
        self.assertEqual(len({t["entry"] for t in tr}), 2)

    def test_trade_days_gate(self):
        # 2026-09-01 Tue + 2026-09-02 Wed identical breakout days
        base = (or_bars() + [m(575, 103, 104.5, 103.5, 104),
                             m(576, 104, 104.2, 101.0, 102),
                             m(577, 102, 105.0, 102.0, 104)])
        day2 = [Bar(idx=0, stamp=b.stamp.replace("2026-09-01", "2026-09-02"),
                    open=b.open, high=b.high, low=b.low, close=b.close,
                    volume=b.volume, bidvol=b.bidvol, askvol=b.askvol)
                for b in base]
        both = base + day2
        self.assertEqual(len(orb_retrace(both, tick_size=1.0,
                                         or_start_min=570, or_end_min=575,
                                         breakout_ticks=0, vp_level=0)), 2)
        wed_only = orb_retrace(both, tick_size=1.0, or_start_min=570,
                               or_end_min=575, breakout_ticks=0, vp_level=0,
                               trade_days=(2,))
        self.assertEqual(len(wed_only), 1)
        self.assertTrue(both[wed_only[0]["entry"]].stamp.startswith("2026-09-02"))

    def test_cutoff_blocks_late_entry(self):
        bars = or_bars() + [m(575, 103, 104.5, 103.5, 104),
                            m(576, 104, 104.2, 101.0, 102)]
        tr = orb_retrace(bars, tick_size=1.0, or_start_min=570,
                         or_end_min=575, breakout_ticks=0, vp_level=0,
                         entry_cutoff_min=576)
        self.assertEqual(tr, [])

    def test_no_breakout_no_trade(self):
        bars = or_bars() + [m(575 + k, 101, 102, 100, 101) for k in range(30)]
        tr = orb_retrace(bars, tick_size=1.0, or_start_min=570,
                         or_end_min=575, breakout_ticks=0, vp_level=0)
        self.assertEqual(tr, [])


if __name__ == "__main__":
    unittest.main()
