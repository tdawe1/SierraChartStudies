"""Tests for scid.py (synthetic ticks, stdlib only).

Run:  python3 -m unittest discover -s tests -v   (from backtest/)
"""

import csv
import os
import struct
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data import load_csv  # noqa: E402
from scid import check_dly, convert, stream_bars  # noqa: E402

EPOCH = datetime(1899, 12, 30)
HDR = struct.Struct("<4sIII")
REC = struct.Struct("<QffffIIII")
def micros(y, mo, d, h=0, mi=0, s=0):
    return int(((datetime(y, mo, d, h, mi, s) - EPOCH).total_seconds()
                * 1_000_000))


def write_scid(path, rows):
    """rows: (ts_micros, o,h,l,c in points, nt, vol, bidv, askv)."""
    with open(path, "wb") as f:
        f.write(HDR.pack(b"SCID", 56, 40, 1))
        f.write(bytes(56 - HDR.size))
        for ts, o, h, l, c, nt, v, b, a in rows:
            f.write(REC.pack(ts, o * 100.0, h * 100.0, l * 100.0,
                             c * 100.0, nt, v, b, a))


class TestScid(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.scid = os.path.join(self.tmp.name, "t.scid")
        self.out = os.path.join(self.tmp.name, "bars.csv")

    def tearDown(self):
        self.tmp.cleanup()

    def test_ohlc_from_close_stream(self):
        # continuation ticks carry O=0: open/high/low must come from Closes
        write_scid(self.scid, [
            (micros(2026, 9, 10, 9, 0, 10), 0, 0, 0, 100.0, 1, 2, 1, 1),
            (micros(2026, 9, 10, 9, 1, 10), 0, 0, 0, 101.5, 1, 3, 1, 2),
            (micros(2026, 9, 10, 9, 2, 10), 0, 0, 0, 99.5, 1, 1, 1, 0),
        ])
        (bar,) = list(stream_bars(self.scid, 5))
        _, o, h, l, c, vol, bid, ask, _maxd, _mind = bar
        self.assertEqual((o, h, l, c), (100.0, 101.5, 99.5, 99.5))
        self.assertEqual((vol, bid, ask), (6, 3, 3))

    def test_delta_includes_zero_start(self):
        # one ask tick: max=1, min stays 0. One bid tick: max stays 0, min=-1.
        write_scid(self.scid, [
            (micros(2026, 9, 10, 9, 0, 10), 0, 0, 0, 100.0, 1, 1, 0, 1),
            (micros(2026, 9, 10, 9, 6, 10), 0, 0, 0, 100.0, 1, 1, 1, 0),
        ])
        bars = list(stream_bars(self.scid, 5))
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0][8:], (1, 0))
        self.assertEqual(bars[1][8:], (0, -1))

    def test_empty_buckets_skipped(self):
        write_scid(self.scid, [
            (micros(2026, 9, 10, 9, 0, 10), 0, 0, 0, 100.0, 1, 1, 1, 0),
            (micros(2026, 9, 10, 9, 20, 10), 0, 0, 0, 101.0, 1, 1, 0, 1),
        ])
        bars = list(stream_bars(self.scid, 5))
        self.assertEqual([b[0] for b in bars],
                         ["2026-09-10 09:00", "2026-09-10 09:20"])

    def test_bad_magic_rejected(self):
        with open(self.scid, "wb") as f:
            f.write(HDR.pack(b"XXXX", 56, 40, 1))
            f.write(bytes(56 - HDR.size))
        with self.assertRaises(ValueError):
            list(stream_bars(self.scid, 5))

    def test_convert_validates_against_loader(self):
        write_scid(self.scid, [
            (micros(2026, 9, 10, 9, 0, 10), 0, 0, 0, 100.0, 1, 4, 2, 2),
            (micros(2026, 9, 10, 9, 6, 10), 0, 0, 0, 102.0, 1, 6, 3, 3),
        ])
        self.assertEqual(convert(self.scid, self.out, 5), 2)
        bars = load_csv(self.out)
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[1].close, 102.0)
        self.assertEqual((bars[0].signal_long, bars[0].signal_short), (0, 0))

    def test_check_dly_pass(self):
        write_scid(self.scid, [
            (micros(2026, 9, 10, 9, 0, 10), 0, 0, 0, 100.0, 1, 1, 1, 0),
            (micros(2026, 9, 10, 9, 6, 10), 0, 0, 0, 102.0, 1, 1, 0, 1),
        ])
        convert(self.scid, self.out, 5)
        dly = os.path.join(self.tmp.name, "t.dly")
        with open(dly, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["Date", "Open", "High", "Low", "Close",
                        "Volume", "OpenInterest"])
            w.writerow(["2026/09/10", 10000.0, 10200.0, 10000.0, 10200.0,
                        2, 0])
        self.assertTrue(check_dly(self.out, dly, "2026/09/10"))


if __name__ == "__main__":
    unittest.main()
