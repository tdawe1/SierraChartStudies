"""SCID tick -> backtester bar CSV. Stdlib only.

Sierra Chart stores intraday ticks in ``Data/*.scid``. This converts one
file to the bar CSV contract in ``data.py`` so the engine can run with no
chart open, no exporter wiring, no copy-paste::

    python3 scid.py ESU6.CME.scid bars_ESU6.csv --minutes 5
    python3 bt.py scid --scid ESU6.CME.scid --out bars_ESU6.csv

Layout (verified ESU6/NQU6: magic ``SCID``, hsize 56, rsize 40). Record
``<QffffIIII``: u64 micros since 1899-12-30 UTC, O/H/L/C f32, NumTrades,
Volume, BidVol, AskVol u32. Price encoding is per file generation:
legacy intraday files (ESU6.CME.scid: raw 766075.0) store hundredths of
a point (divisor 100); current SYM-YYYYMM files (ESU26-CME 7660.75,
NQU26-CME 29391.75, YMU26-CBOT 52574.0, YMU6.CBOT 52303.0) store whole
points (divisor 1). Stamps are UTC; pass tz_offset=-4 to join against
America/Chicago chart exports (EDT; winter months need -5).
Aggregation mirrors ``orion_full.stream_bars``: UTC-clock buckets
(5-min boundaries align with CT, a whole-hour offset), OHLC from tick
Closes, Volume/Bid/Ask summed, MaxDelta/MinDelta = intra-bar extremes of
cumulative (ask-bid) tick delta *including the 0 start* (Numbers-Bars
semantics ``orion_bar`` expects). Empty buckets are skipped, never filled.

Signals export as 0,0: offline strategies (``orion_bar``) compute their
own. Exact chart-signal replay (``signal_replay``) still needs the
exporter-wired CSV -- see ``exporter/BacktestExporter.cpp``.
"""

from __future__ import annotations

import csv
import os
import struct
import sys
from datetime import datetime, timedelta

EPOCH = datetime(1899, 12, 30)
HDR = struct.Struct("<4sIII")
REC = struct.Struct("<QffffIIII")

HEADER = ["DateTime", "Open", "High", "Low", "Close", "Volume",
          "BidVolume", "AskVolume", "MaxDelta", "MinDelta",
          "SignalLong", "SignalShort"]


SCID_DIVISOR_HELP = ("Raw f32 price divisor. Legacy intraday files "
 "(e.g. ESU6.CME.scid) store hundredths of a point (-> 100). "
 "Current SYM-YYYYMM / SYMU6 files (ESU26-CME, NQU26-CME, "
 "YMU26-CBOT, YMU6.CBOT: raw 7660.75 / 29391.75 / 52574.0) store "
 "whole points (-> 1). Pick per file; when unsure, convert one day "
 "and compare against the chart export or --check-dly before sweeping.")


def stream_bars(scid_path: str, minutes: int, divisor: float = 100.0,
                tz_offset: float = 0.0):
    if minutes < 1:
        raise ValueError(f"minutes must be >= 1 (got {minutes})")
    span = minutes * 60_000_000
    with open(scid_path, "rb") as f:
        head = f.read(16)
        if len(head) < 16:
            raise ValueError(f"{scid_path}: truncated header")
        magic, hsize, rsize, _ver = HDR.unpack(head)
        if magic != b"SCID":
            raise ValueError(f"{scid_path}: bad magic {magic!r}")
        if rsize != REC.size:
            raise ValueError(f"{scid_path}: unexpected record size {rsize}")
        f.seek(0, 2)
        total = (f.tell() - hsize) // rsize
        f.seek(hsize)
        key = None
        o = h = l = c = 0.0
        vol = bid = ask = cum = maxd = mind = 0
        done = 0
        step = 200_000
        for _ in range(0, total, step):
            chunk = f.read(step * rsize)
            for u64, _o, _h, _l, cc, _nt, v, b, a in REC.iter_unpack(chunk):
                k = (u64 // span) * span
                price = cc / divisor
                if key is None or k != key:
                    if key is not None:
                        yield (_stamp(key, tz_offset), o, h, l, c,
                               vol, bid, ask, maxd, mind)
                    key = k
                    o = h = l = c = price
                    vol = bid = ask = cum = maxd = mind = 0
                else:
                    if price > h:
                        h = price
                    if price < l:
                        l = price
                    c = price
                vol += v
                bid += b
                ask += a
                cum += a - b
                if cum > maxd:
                    maxd = cum
                if cum < mind:
                    mind = cum
            print(f"\rscid: {done}/{total} ticks", end="", file=sys.stderr)
        print(file=sys.stderr)
        if key is not None:
            yield (_stamp(key, tz_offset), o, h, l, c, vol, bid, ask, maxd, mind)


def stream_footprint(scid_path: str, minutes: int, divisor: float = 100.0,
                     tz_offset: float = 0.0):
    """Yield (stamp, {price: [bid, ask]}) per non-empty bucket: the Numbers
    Bars matrix rebuilt from raw ticks. Prices keyed by exact decoded
    float (same bits = same level; no rounding dust). Totals per bucket
    must equal stream_bars aggregates exactly (see tests/test_scid.py).
    """
    if minutes < 1:
        raise ValueError(f"minutes must be >= 1 (got {minutes})")
    span = minutes * 60_000_000
    with open(scid_path, "rb") as f:
        head = f.read(16)
        if len(head) < 16:
            raise ValueError(f"{scid_path}: truncated header")
        magic, hsize, rsize, _ver = HDR.unpack(head)
        if magic != b"SCID":
            raise ValueError(f"{scid_path}: bad magic {magic!r}")
        if rsize != REC.size:
            raise ValueError(f"{scid_path}: unexpected record size {rsize}")
        f.seek(0, 2)
        total = (f.tell() - hsize) // rsize
        f.seek(hsize)
        key = None
        levels: dict = {}
        step = 200_000
        for _ in range(0, total, step):
            chunk = f.read(step * rsize)
            for u64, _o, _h, _l, cc, _nt, v, b, a in REC.iter_unpack(chunk):
                k = (u64 // span) * span
                if key is None or k != key:
                    if key is not None:
                        yield (_stamp(key, tz_offset), levels)
                    key = k
                    levels = {}
                price = cc / divisor
                cell = levels.get(price)
                if cell is None:
                    levels[price] = [b, a]
                else:
                    cell[0] += b
                    cell[1] += a
        if key is not None:
            yield (_stamp(key, tz_offset), levels)


def _stamp(key: int, tz_offset: float = 0.0) -> str:
    return (EPOCH + timedelta(microseconds=key) +
            timedelta(hours=tz_offset)).strftime("%Y-%m-%d %H:%M")


def convert(scid_path: str, out_csv: str, minutes: int = 5,
            divisor: float = 100.0, tz_offset: float = 0.0,
            footprint_path: str = "") -> int:
    """Write the bar CSV (and optionally a footprint sidecar:
    stamp,price,bid,ask per traded level). Returns the bar count."""
    n = 0
    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        for stamp, o, h, l, c, vol, bid, ask, maxd, mind in \
                stream_bars(scid_path, minutes, divisor, tz_offset):
            w.writerow([stamp, f"{o:.2f}", f"{h:.2f}", f"{l:.2f}",
                        f"{c:.2f}", vol, bid, ask, maxd, mind, 0, 0])
            n += 1
    print(f"{n} bars -> {out_csv}")
    if footprint_path:
        m = 0
        with open(footprint_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["DateTime", "Price", "BidVolume", "AskVolume"])
            for stamp, levels in stream_footprint(scid_path, minutes,
                                                 divisor, tz_offset):
                for price in sorted(levels):
                    b, a = levels[price]
                    w.writerow([stamp, f"{price:.2f}", b, a])
                    m += 1
        print(f"{m} level rows -> {footprint_path}")
    return n


def check_dly(out_csv: str, dly_path: str, day: str) -> bool:
    """One-time scale check: converted UTC-day extremes vs the .dly row.

    ``day`` is ``YYYY/MM/DD`` (dly format). Values in .dly are hundredths,
    like the ticks. PASS on exact tick match; a small miss usually means
    the daily session boundary differs from the UTC day, a ~100x miss
    means the price scale is wrong.
    """
    want = day.replace("/", "-")
    his, los = [], []
    with open(out_csv, newline="") as f:
        for row in csv.DictReader(f):
            if (row.get("DateTime", "")[:10]) == want:
                his.append(float(row["High"]))
                los.append(row["Low"])
    if not his:
        print(f"check-dly: no bars on {want} in {out_csv}")
        return False
    hi, lo = max(his), min(float(x) for x in los)
    dhi = dlo = None
    with open(dly_path) as f:
        for row in csv.DictReader(f, skipinitialspace=True):
            if (row.get("Date", "") or "").strip() == day:
                dhi, dlo = float(row["High"]) / 100.0, float(row["Low"]) / 100.0
    if dhi is None:
        print(f"check-dly: no {day} row in {dly_path}")
        return False
    ok = abs(hi - dhi) < 0.005 and abs(lo - dlo) < 0.005
    print(f"check-dly {day}: bars H/L {hi:.2f}/{lo:.2f} "
          f"vs dly {dhi:.2f}/{dlo:.2f} [{'PASS' if ok else 'MISMATCH'}]")
    return ok


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="SCID tick -> backtester bar CSV")
    ap.add_argument("scid", help="input .scid tick file")
    ap.add_argument("out", help="output bar CSV")
    ap.add_argument("--minutes", type=int, default=5)
    ap.add_argument("--check-dly", default="",
                    help="verify one day, e.g. --check-dly 2026/09/10 "
                         "[--dly ESU6.CME.dly]")
    ap.add_argument("--dly", default="",
                    help=".dly check file (default: <scid-basename>.dly next to scid)")
    a = ap.parse_args(argv)
    if not os.path.exists(a.scid):
        print(f"error: {a.scid} not found", file=sys.stderr)
        return 1
    if a.check_dly:
        base = os.path.basename(a.scid)
        if base.endswith(".scid"):
            base = base[:-len(".scid")]
        dly = a.dly or os.path.join(os.path.dirname(a.scid), base + ".dly")
        if not check_dly(a.out, dly, a.check_dly):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
