"""System tests: store, splits, walk-forward, report. Stdlib unittest."""

import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bt
from data import load_csv
from engine import Bar
from report import build_html, leaderboard_text
from store import connect, list_runs, log_run, make_id


def bar(**kw):
    d = dict(idx=0, stamp="2026-08-03 09:00", open=100.0, high=101.0,
             low=99.0, close=100.5, volume=1000, bidvol=400, askvol=600,
             maxdelta=700, mindelta=-500)
    d.update(kw)
    return Bar(**d)


class TestSplit(unittest.TestCase):
    def test_frac(self):
        bars = [bar(idx=i, stamp=f"2026-08-03 09:{i:02d}") for i in range(10)]
        isb, oos = bt.split_bars(bars, "frac:0.7")
        self.assertEqual((len(isb), len(oos)), (7, 3))

    def test_date_prefix(self):
        bars = [bar(idx=i, stamp=f"2026-08-03 09:{i:02d}") for i in range(10)]
        isb, oos = bt.split_bars(bars, "2026-08-03 09:04")
        self.assertEqual((len(isb), len(oos)), (4, 6))

    def test_empty(self):
        bars = [bar()]
        isb, oos = bt.split_bars(bars, "")
        self.assertEqual((len(isb), len(oos)), (1, 0))


class TestConfigWiring(unittest.TestCase):
    def test_make_cfg_passes_symbols(self):
        cfg = bt._make_cfg({"engine": {"regime": "mean-reversion",
                                       "symbols": ["ES"]}})
        self.assertEqual(cfg.symbols, ("ES",))

    def test_make_cfg_requires_regime(self):
        with self.assertRaises(ValueError):
            bt._make_cfg({"engine": {}})

class TestStore(unittest.TestCase):
    def test_round_trip(self):
        import store
        con = store.connect(":memory:")
        rid = make_id("s", "d", {"a": 1})
        log_run(con, rid, "s", "d", "t", "", {"a": 1},
                {"total_pnl": 5.0}, {"total_pnl": 9.0}, None, artifact_dir="/x")
        rows = list_runs(con)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["metrics"]["total_pnl"], 5.0)
        self.assertEqual(rows[0]["is_metrics"]["total_pnl"], 9.0)
        self.assertIsNone(rows[0]["oos_metrics"])
        self.assertEqual(list_runs(con, study="nope"), [])
        con.close()


class TestReport(unittest.TestCase):
    def test_html_and_leaderboard(self):
        runs = [
            {"id": "a", "study": "s1", "dataset": "d.csv", "tag": "",
             "metrics": {"trades": 2, "win_rate": 0.5, "total_pnl": 100.0,
                         "expectancy": 50.0, "profit_factor": 2.0,
                         "max_drawdown": -10.0, "calmar": 10.0,
                         "sharpe_trade": 1.0, "max_consec_losses": 1},
             "is_metrics": {"total_pnl": 200.0}, "oos_metrics": {"total_pnl": -50.0},
             "equity": [("t", 0.0), ("t", 100.0)]},
            {"id": "b", "study": "s2", "dataset": "d.csv", "tag": "",
             "metrics": {"trades": 1, "win_rate": 1.0, "total_pnl": 10.0,
                         "expectancy": 10.0, "profit_factor": 0.0,
                         "max_drawdown": 0.0, "calmar": 0.0,
                         "sharpe_trade": 0.0, "max_consec_losses": 0},
             "is_metrics": None, "oos_metrics": None, "equity": []},
        ]
        txt = leaderboard_text(runs)
        self.assertIn("s2", txt.splitlines()[1])  # b total=10 beats a OOS=-50
        self.assertIn("s1", txt.splitlines()[2])
        page = build_html(runs)
        self.assertIn("<svg", page)
        self.assertIn("&#9888;", page)  # overfit flag on run a
        self.assertIn('name="viewport"', page)
        self.assertIn('class="cards"', page)
        self.assertIn('class="tablewrap"', page)


class TestWalkforward(unittest.TestCase):
    def test_aggregates_oos(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        data = os.path.join(here, "sample_data.csv")
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "r.db")
            res = bt.do_walkforward(data, os.path.join(here, "params.replay.json"),
                                    os.path.join(td, "wf"), train=4, test=4,
                                    step=4, db=db)
            self.assertGreater(len(res["windows"]), 1)
            self.assertEqual(sum(w["trades"] for w in res["windows"]),
                             len(res["trades"]))
            rows = list_runs(connect(db))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["split"], "walkforward")


class TestJournal(unittest.TestCase):
    def test_session_buckets(self):
        from journal import session_of, summarize
        self.assertEqual(session_of("2026-08-03 09:15"), "morning")
        self.assertEqual(session_of("2026-08-03 22:00"), "overnight")
        self.assertEqual(session_of("2026-08-03 14:00"), "afternoon")
        rows = [{"stamp": "2026-08-03 09:00", "pnl": 100.0},
                {"stamp": "2026-08-03 09:30", "pnl": -50.0},
                {"stamp": "2026-08-03 14:00", "pnl": 200.0}]
        s = summarize(rows)
        self.assertEqual(s[0]["session"], "afternoon")
        self.assertEqual(s[1]["total_pnl"], 50.0)

    def test_sample_journal_loads(self):
        from journal import load_journal, summarize
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        rows = load_journal(os.path.join(here, "sample_journal.csv"))
        self.assertEqual(len(rows), 10)
        self.assertEqual(summarize(rows)[0]["session"], "morning")


class TestGuideNotify(unittest.TestCase):
    def test_guide_builds_risk_params(self):
        from guide import build_guide_params
        p = build_guide_params({"strategy": "1", "size_mode": "risk",
                                "account_size": 50000, "risk_pct": 1.0,
                                "daily_loss_limit": 1000,
                                "max_drawdown_limit": 2000})
        eng = p["engine"]
        self.assertEqual((eng["account_size"], eng["risk_pct"],
                          eng["daily_loss_limit"]), (50000, 1.0, 1000))

    def test_notify_message_and_dry_run(self):
        from notify import build_message, describe
        msg = build_message("a@b.c", "done", "body")
        self.assertEqual(msg["To"], "a@b.c")
        self.assertIn("a@b.c", describe("a@b.c"))
        from remote import run_remote
        cmds, local_out = run_remote("h", "b.tgz", dry_run=True, notify="a@b.c")
        self.assertTrue(any(c.startswith("email a@b.c") for c in cmds))
        self.assertTrue(local_out.startswith("./out-"))


class TestPromote(unittest.TestCase):
    def test_unknown_run_exits(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(SystemExit):
                bt.do_promote(os.path.join(td, "r.db"), "no-such-run")

    def test_no_oos_evidence_fails(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "r.db")
            res = bt.do_run(os.path.join(here, "sample_data.csv"),
                            os.path.join(here, "params.replay.json"),
                            os.path.join(td, "out"), quiet=True, tag="t",
                            db=db, results_dir=os.path.join(td, "res"))
            self.assertFalse(bt.do_promote(db, res["run_id"]))

    def test_walkforward_oos_path_runs(self):
        # gate evaluates the walkforward aggregate; sample data is tiny so
        # the verdict itself is not asserted, only that the path executes
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "r.db")
            res = bt.do_walkforward(
                os.path.join(here, "sample_data.csv"),
                os.path.join(here, "params.replay.json"),
                os.path.join(td, "wf"), train=4, test=4, step=4,
                db=db, results_dir=os.path.join(td, "res"))
            self.assertIsInstance(bt.do_promote(db, res["run_id"]), bool)

class TestAudit(unittest.TestCase):
    def test_old_db_migrates(self):
        import store
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "old.db")
            raw = sqlite3.connect(db)
            raw.execute(store.SCHEMA)  # pre-audit schema, no ALTERs
            raw.commit()
            raw.close()
            con = store.connect(db)
            cols = {r[1] for r in con.execute("PRAGMA table_info(runs)")}
            con.close()
        self.assertIn("code_sha", cols)
        self.assertIn("n_trials", cols)

    def test_append_only_not_replace(self):
        import store
        con = sqlite3.connect(":memory:")
        con.execute(store.SCHEMA)
        con.commit()
        # simulate the migrated schema the same way connect() does
        for name, typ in store.AUDIT_COLS:
            con.execute(f"ALTER TABLE runs ADD COLUMN {name} {typ}")
        store.log_run(con, "r1", "s", "d", "t", "", {"a": 1},
                      {"total_pnl": 5.0}, None, None, artifact_dir="/x")
        with self.assertRaises(sqlite3.IntegrityError):
            store.log_run(con, "r1", "s", "d", "t", "", {"a": 1},
                          {"total_pnl": 6.0}, None, None, artifact_dir="/y")
        con.close()

    def test_verify_passes_and_detects_tamper(self):
        import shutil
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as td:
            data = os.path.join(td, "es.csv")
            shutil.copy(os.path.join(here, "sample_data.csv"), data)
            db = os.path.join(td, "r.db")
            res = bt.do_run(data, os.path.join(here, "params.replay.json"),
                            os.path.join(td, "out"), quiet=True, tag="t",
                            db=db, results_dir=os.path.join(td, "res"))
            self.assertTrue(bt.do_verify(db, res["run_id"]))
            with open(data, "a") as f:  # tamper: append a bar
                f.write("2026-08-04 09:00,100,101,99,100,500,200,300,0,0,0,0\n")
            self.assertFalse(bt.do_verify(db, res["run_id"]))

class TestMatrix(unittest.TestCase):
    def test_cells_and_n_low(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "matrix.csv")
            cells = bt.do_matrix(
                os.path.join(here, "sample_data.csv"),
                [os.path.join(here, "params.replay.json"),
                 os.path.join(here, "params.orion.json")],
                out, sessions=[("morning", "08:30-11:00"),
                               ("midday", "11:00-13:30")])
            self.assertEqual(len(cells), 4)
            # tiny sample: every cell is n_low and unranked
            self.assertTrue(all(c["n_low"] for c in cells))
            self.assertTrue(all("rank" not in c for c in cells))
            self.assertTrue(os.path.exists(out))
            self.assertEqual({c["regime"] for c in cells}, {"mean-reversion"})

class TestAntiOverfit(unittest.TestCase):
    def test_sweep_budget_fails_fast(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as td:
            pp = os.path.join(td, "big.json")
            with open(pp, "w") as f:
                json.dump({"strategy": "orion_bar", "strategy_params": {},
                           "engine": {"regime": "mean-reversion"},
                           "grid": {"setup_min_delta": list(range(201))}}, f)
            with self.assertRaises(SystemExit):
                bt.do_sweep(os.path.join(here, "sample_data.csv"), pp,
                            os.path.join(td, "sw"))

    def test_embargo_cut_purges_head_days(self):
        from engine import Bar
        bars = [Bar(idx=i, stamp=f"2026-08-0{3 + (i // 2)} 09:00")
                for i in range(6)]
        self.assertEqual(bt._embargo_cut(bars, 0, 6, 1), 2)
        self.assertEqual(bt._embargo_cut(bars, 0, 6, 0), 0)
        self.assertEqual(bt._embargo_cut(bars, 0, 6, 9), 6)

    def test_walkforward_embargo_runs(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as td:
            res = bt.do_walkforward(
                os.path.join(here, "sample_data.csv"),
                os.path.join(here, "params.replay.json"),
                os.path.join(td, "wf"), train=4, test=4, step=4,
                embargo_days=1, log=False)
            self.assertTrue(all("dropped" in w for w in res["windows"]))
            res0 = bt.do_walkforward(
                os.path.join(here, "sample_data.csv"),
                os.path.join(here, "params.replay.json"),
                os.path.join(td, "wf0"), train=4, test=4, step=4,
                embargo_days=0, log=False)
            self.assertGreaterEqual(len(res0["trades"]),
                                    len(res["trades"]))

    def test_compare_shows_trials(self):
        from report import build_html, leaderboard_text
        runs = [{"id": "a", "study": "s", "dataset": "d.csv", "tag": "",
                 "n_trials": 200, "metrics": {"trades": 1, "win_rate": 1.0,
                 "total_pnl": 5.0, "profit_factor": 0.0, "max_drawdown": 0.0},
                 "is_metrics": None, "oos_metrics": None, "equity": []}]
        text = leaderboard_text(runs)
        self.assertIn("trials", text)
        self.assertIn("200", text)
        self.assertIn("<th>Trials</th>", build_html(runs))

if __name__ == "__main__":
    unittest.main()
