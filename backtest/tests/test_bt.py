"""Backtester smoke tests. Stdlib unittest, no deps.

Run:  python3 -m unittest discover -s tests -v   (from backtest/)
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data import load_csv
from engine import Bar, EngineConfig, run
from strategies import (bar_delta_supports, can_trigger, climax_reached,
                        extreme_broken, in_session, orion_bar, rebound_hit,
                        signal_replay)


def bar(**kw):
    d = dict(idx=0, stamp="2026-08-03 09:00", open=100.0, high=101.0,
             low=99.0, close=100.5, volume=1000, bidvol=400, askvol=600,
             maxdelta=700, mindelta=-500)
    d.update(kw)
    return Bar(**d)


class TestCorePorts(unittest.TestCase):
    def test_session_wraps_midnight(self):
        self.assertTrue(in_session("23:00", "22:00", "02:00", True))
        self.assertFalse(in_session("12:00", "22:00", "02:00", True))
        self.assertTrue(in_session("12:00", "08:00", "16:00", False))

    def test_zero_delta_passes_neither_side(self):
        # mirrors orion_core: threshold 0 needs a strict sign
        self.assertFalse(bar_delta_supports(0, 0, False))
        self.assertFalse(bar_delta_supports(0, 0, True))
        self.assertTrue(bar_delta_supports(5, 0, False))
        self.assertTrue(bar_delta_supports(-5, 0, True))

    def test_rebound_modes(self):
        self.assertTrue(rebound_hit(1000, 400, True, 1, 0, 50))
        self.assertFalse(rebound_hit(1000, 600, True, 1, 0, 50))
        self.assertTrue(rebound_hit(-1000, -400, False, 1, 0, 50))
        self.assertTrue(rebound_hit(500, 100, True, 0, 100, 0))

    def test_climax_and_break(self):
        self.assertTrue(climax_reached(800, 500, True))
        self.assertFalse(climax_reached(800, 900, True))
        self.assertTrue(extreme_broken(102, 99, 100, 1.0, 1, True))
        self.assertFalse(extreme_broken(100, 99, 100, 1.0, 0, True))

    def test_trigger_needs_later_bar(self):
        self.assertFalse(can_trigger(5, 5, 1, 3))
        self.assertTrue(can_trigger(6, 5, 1, 3))
        self.assertFalse(can_trigger(9, 5, 1, 3))


class TestEngine(unittest.TestCase):
    def test_next_open_entry_and_target(self):
        bars = [bar(idx=0, open=100, high=100, low=100, close=100),
                bar(idx=1, open=100, high=110, low=100, close=109),
                bar(idx=2, open=109, high=109, low=109, close=109)]
        cfg = EngineConfig(tick_size=1.0, tick_value=10.0, qty=1,
                           stop_ticks=0, target_ticks=5,
                           fee_per_side=0, slippage_ticks=0)
        res = run(bars, [1, 0, 0], cfg)
        self.assertEqual(len(res["trades"]), 1)
        # entered at bar1 open 100, target 105 hit on bar1
        self.assertAlmostEqual(res["trades"][0].pnl, 50.0)

    def test_ambiguous_bar_takes_stop_first(self):
        bars = [bar(idx=0, open=100, high=100, low=100, close=100),
                bar(idx=1, open=100, high=110, low=90, close=105)]
        cfg = EngineConfig(tick_size=1.0, tick_value=10.0, qty=1,
                           stop_ticks=5, target_ticks=5,
                           fee_per_side=0, slippage_ticks=0)
        res = run(bars, [1, 0], cfg)
        self.assertEqual(res["trades"][0].exit_reason, "stop")

    def test_costs_applied(self):
        bars = [bar(idx=0, open=100, high=100, low=100, close=100),
                bar(idx=1, open=100, high=100, low=100, close=102)]
        cfg = EngineConfig(tick_size=1.0, tick_value=10.0, qty=2,
                           fee_per_side=5.0, slippage_ticks=0)
        res = run(bars, [1, 0], cfg)
        # 2pt * $10 * 2qty - 2*$5*2 = 40-20
        self.assertAlmostEqual(res["trades"][0].pnl, 20.0)

    def test_no_lookahead_close_exec(self):
        bars = [bar(idx=0, open=100, high=100, low=100, close=100),
                bar(idx=1, open=100, high=100, low=100, close=100)]
        cfg = EngineConfig(exec_mode="close", fee_per_side=0, slippage_ticks=0)
        res = run(bars, [1, 0], cfg)
        self.assertEqual(res["trades"][0].entry_idx, 0)


class TestStrategies(unittest.TestCase):
    def test_signal_replay_passthrough(self):
        bars = [bar(signal_long=1), bar(signal_short=1), bar()]
        self.assertEqual(signal_replay(bars), [1, -1, 0])

    def test_orion_bar_uses_only_past(self):
        bars = [bar(idx=0, bidvol=100, askvol=900, maxdelta=900, mindelta=-100),
                bar(idx=1, bidvol=400, askvol=600, maxdelta=500, mindelta=-200),
                bar(idx=2, bidvol=450, askvol=550, maxdelta=400, mindelta=-200)]
        sigs = orion_bar(bars, setup_min_delta=500, climax_min=0,
                         rebound_mode=1, rebound_pct=50, lifetime_bars=3)
        self.assertEqual(sigs[0], 0)  # setup bar never signals itself
        self.assertEqual(len(sigs), 3)

    def test_sample_csv_loads(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        bars = load_csv(os.path.join(here, "sample_data.csv"))
        self.assertEqual(len(bars), 12)
        self.assertEqual(signal_replay(bars), [0, 1, 0, -1, 0, 1, 0, -1, 0, 1, 0, -1])


class TestRisk(unittest.TestCase):
    def test_daily_loss_halts_day(self):
        bars = [bar(idx=0, stamp="2026-08-03 09:00", open=100, high=100, low=90, close=90),
                bar(idx=1, stamp="2026-08-03 09:05", open=90, high=90, low=80, close=80),
                bar(idx=2, stamp="2026-08-03 09:10", open=80, high=85, low=80, close=85),
                bar(idx=3, stamp="2026-08-04 09:00", open=85, high=90, low=85, close=90),
                bar(idx=4, stamp="2026-08-04 09:05", open=90, high=95, low=90, close=95)]
        cfg = EngineConfig(tick_size=1.0, tick_value=10.0, qty=1,
                           fee_per_side=0, slippage_ticks=0,
                           daily_loss_limit=50.0)
        res = run(bars, [1, 1, 1, 1, 0], cfg)
        reasons = [t.exit_reason for t in res["trades"]]
        self.assertIn("risk-daily", reasons)
        days = sorted({t.entry_stamp[:10] for t in res["trades"]})
        self.assertEqual(days, ["2026-08-03", "2026-08-04"])
        self.assertGreaterEqual(res["metrics"]["risk_halts"], 1)

    def test_max_drawdown_stops_run(self):
        bars = [bar(idx=i, stamp=f"2026-08-03 09:{i:02d}",
                    open=100, high=100, low=90, close=90) for i in range(4)]
        cfg = EngineConfig(tick_size=1.0, tick_value=10.0, qty=1,
                           fee_per_side=0, slippage_ticks=0,
                           max_drawdown_limit=60.0)
        res = run(bars, [1, 1, 1, 1], cfg)
        self.assertIn("risk-dd", [t.exit_reason for t in res["trades"]])
        self.assertEqual(len(res["trades"]), 1)

    def test_risk_pct_sizes_from_stop(self):
        bars = [bar(idx=0, open=100, high=100, low=100, close=100),
                bar(idx=1, open=100, high=110, low=100, close=109)]
        cfg = EngineConfig(tick_size=1.0, tick_value=10.0, qty=1,
                           stop_ticks=5, fee_per_side=0, slippage_ticks=0,
                           account_size=10000.0, risk_pct=1.0, max_qty=10)
        # risk $100 / ($50 stop) = 2 contracts
        res = run(bars, [1, 0], cfg)
        self.assertEqual(res["trades"][0].qty, 2)


if __name__ == "__main__":
    unittest.main()
