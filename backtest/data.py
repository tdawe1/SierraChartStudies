"""CSV loading for the backtester. Stdlib only.

Accepted input (case-insensitive headers, extra columns ignored):

    DateTime, Open, High, Low, Close [, Volume, BidVolume, AskVolume,
      MaxDelta, MinDelta, SetupLong, SetupShort, SignalLong, SignalShort,
      ATR14, RelVol50, BidClose, AskClose]
BidClose/AskClose (BacktestExporter v2) are carried as context only;
the engines never fill off them (see exporter/BacktestExporter.cpp).

DateTime may be "YYYY-MM-DD HH:MM" or with seconds. Sierra Chart's
"Export Bar Data" writes Date/Time in separate columns or epoch; use the
template below -- export, then rename to this contract:

    DateTime = Date + " " + Time (24h)
    Open/High/Low/Close = bar prices
    Volume = bar volume (0 if unavailable)
    BidVolume/AskVolume = Numbers Bars totals (0 if unavailable)
    MaxDelta/MinDelta = Numbers Bars Calculated Values subgraphs
      (intra-bar max/min of ask-bid; 0 if unwired)
    SetupLong/SetupShort, SignalLong/SignalShort = chart-exported study
      markers (1/0). See exporter/BacktestExporter.cpp.
Missing optional columns default to 0 (ATR14) and 1.0 (RelVol50, so
un-gated runs behave). Rows with bad prices are skipped.
"""

import csv

try:
    from .engine import Bar
except ImportError:  # `python bt.py` run from inside backtest/
    from engine import Bar
_ALIASES = {
    "datetime": "stamp", "date_time": "stamp", "time": "stamp",
    "date": "stamp", "timestamp": "stamp",
    "o": "open", "open": "open",
    "h": "high", "high": "high",
    "l": "low", "low": "low",
    "c": "close", "close": "close", "last": "close",
    "v": "volume", "volume": "volume", "vol": "volume",
    "bidvolume": "bidvol", "bid_volume": "bidvol", "bidvol": "bidvol",
    "askvolume": "askvol", "ask_volume": "askvol", "askvol": "askvol",
    "maxdelta": "maxdelta", "max_delta": "maxdelta",
    "mindelta": "mindelta", "min_delta": "mindelta",
    "setuplong": "setup_long", "setup_long": "setup_long",
    "setupshort": "setup_short", "setup_short": "setup_short",
    "signallong": "signal_long", "signal_long": "signal_long",
    "signalshort": "signal_short", "signal_short": "signal_short",
    "atr": "atr", "atr14": "atr",
    "relvol": "relvol", "relvol50": "relvol", "rel_vol": "relvol",
}


def _num(row: dict, key: str) -> float:
    try:
        return float((row.get(key, "") or "0").strip() or 0)
    except (ValueError, AttributeError):
        return 0.0


def load_csv(path: str) -> list[Bar]:
    bars: list[Bar] = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(f"no header row in {path}")
        norm = {h: _ALIASES.get(h.strip().lower().replace(" ", ""), None)
                for h in reader.fieldnames}
        idx = 0
        date_col = time_col = None
        for h in reader.fieldnames:
            k = h.strip().lower()
            if k == "date":
                date_col = h
            elif k == "time":
                time_col = h
        for raw in reader:
            row = {norm[h]: (raw[h] or "") for h in reader.fieldnames if norm[h]}
            if date_col is not None and time_col is not None and not row.get("stamp"):
                row["stamp"] = f"{raw[date_col].strip()} {raw[time_col].strip()}"
            o, h_, l_, c = (_num(row, "open"), _num(row, "high"),
                            _num(row, "low"), _num(row, "close"))
            if not (h_ > 0 and l_ > 0 and h_ >= l_):
                continue
            bars.append(Bar(
                idx=idx, stamp=(row.get("stamp", "") or "").strip(),
                open=o, high=h_, low=l_, close=c,
                volume=_num(row, "volume"), bidvol=_num(row, "bidvol"),
                askvol=_num(row, "askvol"), maxdelta=_num(row, "maxdelta"),
                mindelta=_num(row, "mindelta"),
                setup_long=int(_num(row, "setup_long") > 0),
                setup_short=int(_num(row, "setup_short") > 0),
                signal_long=int(_num(row, "signal_long") > 0),
                signal_short=int(_num(row, "signal_short") > 0),
                symbol=(row.get("symbol", "") or "").strip(),
                atr=_num(row, "atr"),
                relvol=_num(row, "relvol") if "relvol" in row else 1.0,
            ))
            idx += 1
    if not bars:
        raise ValueError(f"no usable bars in {path}")
    return bars
