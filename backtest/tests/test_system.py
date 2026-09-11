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


class TestStore(unittest.TestCase):
    def test_round_trip(self):
        con = sqlite3.connect(":memory:")
        con.execute(__import__("store", fromlist=["x"]).SCHEMA)
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
        cmds = run_remote("h", "b.tgz", dry_run=True, notify="a@b.c")
        self.assertTrue(any(c.startswith("email a@b.c") for c in cmds))


if __name__ == "__main__":
    unittest.main()
