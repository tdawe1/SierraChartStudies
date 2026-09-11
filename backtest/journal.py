"""Trading-journal time-of-day analysis. Stdlib only.

Journal CSV contract (case-insensitive headers, extra columns ignored):

    DateTime, Symbol, Side, Qty, Entry, Exit, PnL

DateTime "YYYY-MM-DD HH:MM[:SS]". Side: long/short (or buy/sell, 1/-1).
If PnL is blank, it is recomputed as side*(Exit-Entry)*Qty (price points;
pass --point-value to scale to currency).

Sessions bucket the trade by entry time ("HH:MM" ranges, wrap allowed):

    overnight  18:00-08:29   (default)
    morning    08:30-11:00
    midday     11:00-13:30
    afternoon  13:30-16:00
    evening    16:00-18:00

Override with --sessions NAME=START-END (repeatable). Output ranks
sessions by total PnL so you can see which times of day suit the study,
then restrict entries with the engine's session_start/session_end.
"""

from __future__ import annotations

import csv

DEFAULT_SESSIONS = [
    ("overnight", "18:00", "08:29"),
    ("morning", "08:30", "11:00"),
    ("midday", "11:00", "13:30"),
    ("afternoon", "13:30", "16:00"),
    ("evening", "16:00", "18:00"),
]

_ALIASES = {
    "datetime": "stamp", "date_time": "stamp", "time": "stamp",
    "date": "stamp", "timestamp": "stamp", "entry_time": "stamp",
    "symbol": "symbol", "instrument": "symbol",
    "side": "side", "direction": "side", "action": "side",
    "qty": "qty", "quantity": "qty", "contracts": "qty",
    "entry": "entry", "entry_price": "entry",
    "exit": "exit", "exit_price": "exit",
    "pnl": "pnl", "profit": "pnl", "net": "pnl",
}


def _in_range(t: str, start: str, end: str) -> bool:
    if start <= end:
        return start <= t <= end
    return t >= start or t <= end


def session_of(stamp: str, sessions=None) -> str:
    t = stamp[11:16] if len(stamp) >= 16 else stamp
    for name, start, end in (sessions or DEFAULT_SESSIONS):
        if _in_range(t, start, end):
            return name
    return "other"


def _side(raw: str) -> int:
    s = (raw or "").strip().lower()
    if s in ("long", "buy", "b", "1"):
        return 1
    if s in ("short", "sell", "s", "-1"):
        return -1
    return 0


def _num(v) -> float:
    try:
        return float(str(v or "0").strip() or 0)
    except ValueError:
        return 0.0


def load_journal(path: str, point_value: float = 1.0) -> list[dict]:
    rows = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(f"no header row in {path}")
        norm = {h: _ALIASES.get(h.strip().lower().replace(" ", ""), None)
                for h in reader.fieldnames}
        for raw in reader:
            row = {norm[h]: (raw[h] or "") for h in reader.fieldnames if norm[h]}
            stamp = (row.get("stamp", "") or "").strip()
            if not stamp:
                continue
            side = _side(row.get("side", ""))
            qty = _num(row.get("qty", "1")) or 1.0
            entry, exit = _num(row.get("entry", "")), _num(row.get("exit", ""))
            pnl = _num(row.get("pnl", ""))
            if not row.get("pnl", "").strip() and side and entry and exit:
                pnl = side * (exit - entry) * qty * point_value
            rows.append({"stamp": stamp, "symbol": row.get("symbol", ""),
                         "side": side, "qty": qty, "pnl": pnl})
    if not rows:
        raise ValueError(f"no usable rows in {path}")
    return rows


def summarize(rows: list[dict], sessions=None) -> list[dict]:
    by: dict[str, list[float]] = {}
    for r in rows:
        by.setdefault(session_of(r["stamp"], sessions), []).append(r["pnl"])
    out = []
    for name, pnls in by.items():
        wins = sum(1 for p in pnls if p > 0)
        out.append({"session": name, "trades": len(pnls),
                    "wins": wins, "win_rate": wins / len(pnls),
                    "total_pnl": round(sum(pnls), 2),
                    "avg": round(sum(pnls) / len(pnls), 2)})
    out.sort(key=lambda d: d["total_pnl"], reverse=True)
    return out


def report_text(summary: list[dict]) -> str:
    lines = [f"{'session':<10} {'n':>3} {'win%':>4} {'total$':>10} {'avg$':>8}",
             "-" * 39]
    for s in summary:
        lines.append(f"{s['session']:<10} {s['trades']:>3} "
                     f"{s['win_rate'] * 100:>3.0f}% {s['total_pnl']:>10,.0f} "
                     f"{s['avg']:>8,.0f}")
    lines.append(f"\nbest window: {summary[0]['session']}" if summary else "")
    return "\n".join(lines)
