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
        cfg = EngineConfig(regime="mean-reversion", tick_size=1.0, tick_value=10.0, qty=1,
                           stop_ticks=0, target_ticks=5,
                           fee_per_side=0, slippage_ticks=0)
        res = run(bars, [1, 0, 0], cfg)
        self.assertEqual(len(res["trades"]), 1)
        # entered at bar1 open 100, target 105 hit on bar1
        self.assertAlmostEqual(res["trades"][0].pnl, 50.0)

    def test_ambiguous_bar_takes_stop_first(self):
        bars = [bar(idx=0, open=100, high=100, low=100, close=100),
                bar(idx=1, open=100, high=110, low=90, close=105)]
        cfg = EngineConfig(regime="mean-reversion", tick_size=1.0, tick_value=10.0, qty=1,
                           stop_ticks=5, target_ticks=5,
                           fee_per_side=0, slippage_ticks=0)
        res = run(bars, [1, 0], cfg)
        self.assertEqual(res["trades"][0].exit_reason, "stop")

    def test_costs_applied(self):
        bars = [bar(idx=0, open=100, high=100, low=100, close=100),
                bar(idx=1, open=100, high=100, low=100, close=102)]
        cfg = EngineConfig(regime="mean-reversion", tick_size=1.0, tick_value=10.0, qty=2,
                           fee_per_side=5.0, slippage_ticks=0)
        res = run(bars, [1, 0], cfg)
        # 2pt * $10 * 2qty - 2*$5*2 = 40-20
        self.assertAlmostEqual(res["trades"][0].pnl, 20.0)

    def test_no_lookahead_close_exec(self):
        bars = [bar(idx=0, open=100, high=100, low=100, close=100),
                bar(idx=1, open=100, high=100, low=100, close=100)]
        cfg = EngineConfig(regime="mean-reversion", exec_mode="close", fee_per_side=0, slippage_ticks=0)
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

class TestGates(unittest.TestCase):
    def test_relvol_gate_blocks_and_passes(self):
        setup = bar(idx=0, bidvol=100, askvol=900, maxdelta=900,
                    mindelta=-100)
        trig = bar(idx=1, bidvol=400, askvol=600, maxdelta=500,
                   mindelta=-200)
        kw = dict(setup_min_delta=500, tick_size=1.0)
        self.assertEqual(orion_bar([setup, trig], **kw)[1], 1)
        self.assertEqual(
            orion_bar([setup, trig], require_relvol_above=2.0, **kw)[1], 0)
        trig_hi = bar(idx=1, bidvol=400, askvol=600, maxdelta=500,
                      mindelta=-200, relvol=3.0)
        self.assertEqual(
            orion_bar([setup, trig_hi], require_relvol_above=2.0,
                      **kw)[1], 1)

    def test_atr_ceiling_blocks_and_passes(self):
        setup = bar(idx=0, bidvol=100, askvol=900, maxdelta=900,
                    mindelta=-100)
        kw = dict(setup_min_delta=500, tick_size=1.0,
                  block_atr_above_mult=5.0)
        hot = bar(idx=1, bidvol=400, askvol=600, maxdelta=500,
                  mindelta=-200, atr=10.0)
        calm = bar(idx=1, bidvol=400, askvol=600, maxdelta=500,
                   mindelta=-200, atr=2.0)
        self.assertEqual(orion_bar([setup, hot], **kw)[1], 0)
        self.assertEqual(orion_bar([setup, calm], **kw)[1], 1)

    def test_neutral_gates_keep_sample_signals(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        bars = load_csv(os.path.join(here, "sample_data.csv"))
        kw = dict(setup_min_delta=200, rebound_pct=50, lifetime_bars=3)
        self.assertEqual(orion_bar(bars, **kw),
                         orion_bar(bars, require_relvol_above=1.0,
                                   block_atr_above_mult=1000000.0, **kw))

    def test_atr_relvol_columns_parse(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "b.csv")
            with open(p, "w") as f:
                f.write("DateTime,Open,High,Low,Close,ATR14,RelVol50\n"
                        "2026-08-03 09:00,100,101,99,100,1.5,2.0\n")
            b = load_csv(p)[0]
            self.assertEqual((b.atr, b.relvol), (1.5, 2.0))
            q = os.path.join(td, "c.csv")
            with open(q, "w") as f:
                f.write("DateTime,Open,High,Low,Close\n"
                        "2026-08-03 09:00,100,101,99,100\n")
            c = load_csv(q)[0]
            self.assertEqual((c.atr, c.relvol), (0.0, 1.0))


class TestRisk(unittest.TestCase):
    def test_daily_loss_halts_day(self):
        bars = [bar(idx=0, stamp="2026-08-03 09:00", open=100, high=100, low=90, close=90),
                bar(idx=1, stamp="2026-08-03 09:05", open=90, high=90, low=80, close=80),
                bar(idx=2, stamp="2026-08-03 09:10", open=80, high=85, low=80, close=85),
                bar(idx=3, stamp="2026-08-04 09:00", open=85, high=90, low=85, close=90),
                bar(idx=4, stamp="2026-08-04 09:05", open=90, high=95, low=90, close=95)]
        cfg = EngineConfig(regime="mean-reversion", tick_size=1.0, tick_value=10.0, qty=1,
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
        cfg = EngineConfig(regime="mean-reversion", tick_size=1.0, tick_value=10.0, qty=1,
                           fee_per_side=0, slippage_ticks=0,
                           max_drawdown_limit=60.0)
        res = run(bars, [1, 1, 1, 1], cfg)
        self.assertIn("risk-dd", [t.exit_reason for t in res["trades"]])
        self.assertEqual(len(res["trades"]), 1)

    def test_risk_pct_sizes_from_stop(self):
        bars = [bar(idx=0, open=100, high=100, low=100, close=100),
                bar(idx=1, open=100, high=110, low=100, close=109)]
        cfg = EngineConfig(regime="mean-reversion", tick_size=1.0, tick_value=10.0, qty=1,
                           stop_ticks=5, fee_per_side=0, slippage_ticks=0,
                           account_size=10000.0, risk_pct=1.0, max_qty=10)
        # risk $100 / ($50 stop) = 2 contracts
        res = run(bars, [1, 0], cfg)
        self.assertEqual(res["trades"][0].qty, 2)

    def test_consistency_and_target(self):
        bars = [bar(idx=0, stamp="2026-08-03 09:00", open=100, high=100, low=100, close=100),
                bar(idx=1, stamp="2026-08-03 09:05", open=100, high=110, low=100, close=110),
                bar(idx=2, stamp="2026-08-03 09:10", open=110, high=110, low=110, close=110),
                bar(idx=3, stamp="2026-08-04 09:00", open=110, high=110, low=110, close=110),
                bar(idx=4, stamp="2026-08-04 09:05", open=110, high=112, low=110, close=112),
                bar(idx=5, stamp="2026-08-04 09:10", open=112, high=112, low=112, close=112)]
        cfg = EngineConfig(regime="mean-reversion", tick_size=1.0, tick_value=10.0, qty=1,
                           fee_per_side=0, slippage_ticks=0, max_hold_bars=1,
                           profit_target=100.0, consistency_max_pct=30.0)
        res = run(bars, [1, 0, 0, 1, 0, 0], cfg)
        m = res["metrics"]
        self.assertTrue(m["target_hit"])
        # days: +100, +20 -> best 100/120 = 83% > 30% -> FAIL
        self.assertEqual(m["best_day"], "2026-08-03")
        self.assertAlmostEqual(m["best_day_pct"], 83.3, places=1)
        self.assertFalse(m["consistency_ok"])

    def test_trade_windows_gate_entries(self):
        bars = [bar(idx=0, stamp="2026-08-03 07:00", open=100, high=100, low=100, close=100),
                bar(idx=1, stamp="2026-08-03 09:00", open=100, high=100, low=100, close=100),
                bar(idx=2, stamp="2026-08-03 09:05", open=100, high=110, low=100, close=110)]
        cfg = EngineConfig(regime="mean-reversion", tick_size=1.0, tick_value=10.0, qty=1,
                           fee_per_side=0, slippage_ticks=0,
                           trade_windows=("08:30-11:00",))
        res = run(bars, [1, 0, 0], cfg)
        self.assertEqual(len(res["trades"]), 0)  # 07:00 signal blocked, nothing pending
        res2 = run(bars, [0, 1, 0], cfg)
        self.assertEqual(len(res2["trades"]), 1)

    def test_symbols_and_day_cap(self):
        bars = [bar(idx=0, symbol="ES", open=100, high=100, low=100, close=100),
                bar(idx=1, symbol="NQ", open=100, high=110, low=100, close=110),
                bar(idx=2, symbol="ES", open=110, high=120, low=110, close=120),
                bar(idx=3, symbol="ES", open=120, high=130, low=120, close=130)]
        cfg = EngineConfig(regime="mean-reversion", tick_size=1.0, tick_value=10.0, qty=1,
                           fee_per_side=0, slippage_ticks=0,
                           symbols=("ES",), max_trades_per_day=1)
        res = run(bars, [0, 1, 1, 1], cfg)
        got = [t.entry_stamp and bars[t.entry_idx].symbol for t in res["trades"]]
        self.assertTrue(all(s == "ES" for s in got))
        self.assertEqual(len(res["trades"]), 1)  # day cap blocks the rest


class TestRegime(unittest.TestCase):
    def test_missing_regime_rejected(self):
        with self.assertRaises(ValueError):
            EngineConfig()

    def test_mixed_regime_rejected(self):
        with self.assertRaises(ValueError):
            EngineConfig(regime="mixed")

    def test_trade_inherits_run_regime(self):
        bars = [bar(idx=0, open=100, high=100, low=100, close=100),
                bar(idx=1, open=100, high=110, low=100, close=110)]
        cfg = EngineConfig(regime="trend", tick_size=1.0, tick_value=10.0,
                           qty=1, fee_per_side=0, slippage_ticks=0)
        res = run(bars, [1, 0], cfg)
        self.assertEqual(res["metrics"]["regime"], "trend")
        self.assertEqual(res["trades"][0].regime, "trend")
        self.assertIn("trend", res["metrics"]["by_regime"])

class TestSizing(unittest.TestCase):
    def test_stop_and_atr_conflict(self):
        with self.assertRaises(ValueError):
            EngineConfig(regime="mean-reversion", size_by_atr=True,
                         stop_ticks=12, account_size=10000.0, risk_pct=1.0)

    def test_atr_sizes_from_fill_bar(self):
        bars = [bar(idx=0, open=100, high=100, low=100, close=100),
                bar(idx=1, open=100, high=110, low=100, close=109, atr=5.0)]
        cfg = EngineConfig(regime="mean-reversion", tick_size=1.0,
                           tick_value=10.0, qty=1, fee_per_side=0,
                           slippage_ticks=0, size_by_atr=True,
                           account_size=10000.0, risk_pct=1.0, max_qty=10)
        # risk $100 / (5pt x $10) = 2 contracts
        res = run(bars, [1, 0], cfg)
        self.assertEqual(res["trades"][0].qty, 2)

    def test_zero_atr_falls_back_to_one(self):
        bars = [bar(idx=0, open=100, high=100, low=100, close=100),
                bar(idx=1, open=100, high=110, low=100, close=109)]
        cfg = EngineConfig(regime="mean-reversion", tick_size=1.0,
                           tick_value=10.0, qty=1, fee_per_side=0,
                           slippage_ticks=0, size_by_atr=True,
                           account_size=10000.0, risk_pct=1.0, max_qty=10)
        with self.assertWarns(UserWarning):
            res = run(bars, [1, 0], cfg)
        self.assertEqual(res["trades"][0].qty, 1)

    def test_symbol_less_with_filter_warns_once(self):
        bars = [bar(idx=0, open=100, high=100, low=100, close=100),
                bar(idx=1, open=100, high=100, low=100, close=100)]
        cfg = EngineConfig(regime="mean-reversion", symbols=("ES",))
        with self.assertWarns(UserWarning):
            run(bars, [1, 0], cfg)

if __name__ == "__main__":
    unittest.main()
