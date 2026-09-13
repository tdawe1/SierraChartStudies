"""Math verification for the StatArb pairs port (no chart truth exists;
these pin the OLS/z/pricer arithmetic by hand and on synthetic pairs).
"""

import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine import Bar
from strategies import statarb_spread, pairs_trade_pnl, pairs_metrics


def mkbar(stamp, o, h, l, c, v=100):
    return Bar(idx=0, stamp=stamp, open=o, high=h, low=l, close=c,
               volume=v, bidvol=v // 2, askvol=v - v // 2)


def stamps(n):
    return [f"2026-09-{1 + d:02d} {9 + (i * 10) // 60:02d}:{(i * 10) % 60:02d}"
            for i, d in zip(range(n), [i // 100 for i in range(n)])]


class StatArbMathTests(unittest.TestCase):
    def test_beta_recovers_known_hedge(self):
        rnd = random.Random(7)
        w = 0.0
        l2 = []
        for _ in range(200):
            w += rnd.gauss(0, 1)
            l2.append(100.0 + w)
        s = 0.0
        l1 = []
        for x in l2:
            s = 0.9 * s + rnd.gauss(0, 0.5)
            l1.append(2.0 * x + s)
        st = stamps(200)
        b1 = [mkbar(s, c, c, c, c) for s, c in zip(st, l1)]
        b2 = [mkbar(s, c, c, c, c) for s, c in zip(st, l2)]
        tr = statarb_spread(b1, b2, hedge_lookback=60, z_lookback=60,
                            entry_z=0.5, min_corr=0.0, min_stdev_ticks=0.0)
        self.assertGreater(len(tr), 5, "cointegrated pair must trade")
        for t in tr[:10]:
            self.assertAlmostEqual(t["beta"], 2.0, delta=0.3)

    def test_flat_spread_never_trades(self):
        st = stamps(120)
        b1 = [mkbar(s, 100.0, 100.0, 100.0, 100.0) for s in st]
        b2 = [mkbar(s, 50.0, 50.0, 50.0, 50.0) for s in st]
        tr = statarb_spread(b1, b2, entry_z=0.5, min_corr=0.0,
                            min_stdev_ticks=0.0)
        self.assertEqual(tr, [])

    def test_pricer_hand_computed(self):
        st = stamps(4)
        b1 = [mkbar(st[0], 100, 100, 100, 100), mkbar(st[1], 100, 100, 100, 110),
              mkbar(st[2], 110, 110, 110, 108), mkbar(st[3], 108, 108, 108, 108)]
        b2 = [mkbar(st[0], 50, 50, 50, 50), mkbar(st[1], 50, 50, 50, 52),
              mkbar(st[2], 52, 52, 52, 51), mkbar(st[3], 51, 51, 51, 51)]
        tr = {"dir": 1, "entry": 1, "exit": 2, "beta": 2.0}
        # fills at next opens: leg1 110 -> 108 (-2pt); leg2 52 -> 51 (-1pt x2 = -2)
        # spread flat: gross 0 - costs
        pnl = pairs_trade_pnl(tr, b1, b2, 50.0, 20.0, fee_per_side=2.10,
                              slip_ticks=0.0, leg1_tick_value=0.0,
                              leg2_tick_value=0.0)
        q2 = 2.0 * (50.0 / 20.0)
        self.assertAlmostEqual(pnl, 0.0 - 2 * 2.10 - 2 * q2 * 2.10, places=6)

    def test_metrics_hand_computed(self):
        m = pairs_metrics([100.0, -50.0, -50.0, 200.0])
        self.assertEqual(m["trades"], 4)
        self.assertAlmostEqual(m["total_pnl"], 200.0)
        self.assertAlmostEqual(m["win_rate"], 0.5)
        self.assertAlmostEqual(m["profit_factor"], 3.0)
        self.assertAlmostEqual(m["max_drawdown"], -100.0)
        self.assertAlmostEqual(m["expectancy"], 50.0)

    def test_empty_metrics(self):
        m = pairs_metrics([])
        self.assertEqual(m["trades"], 0)


if __name__ == "__main__":
    unittest.main()
