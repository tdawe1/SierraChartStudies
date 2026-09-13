"""Persistent run registry. Stdlib sqlite3 only.

Every `bt.py run / sweep / walkforward` logs one row per run plus its
artifacts under results/<run-id>/. `compare` reads back from here, so
assessing studies is a query, not a pile of CSVs.

Run id: <UTC-timestamp>-<study>-<short-hash of params+dataset>.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    created TEXT,
    study TEXT,
    dataset TEXT,
    tag TEXT,
    split TEXT,
    params TEXT,
    metrics TEXT,
    is_metrics TEXT,
    oos_metrics TEXT,
    artifact_dir TEXT
);
"""

ALL_COLS = ["id", "created", "study", "dataset", "tag", "split", "params",
            "metrics", "is_metrics", "oos_metrics", "artifact_dir",
            "code_sha", "data_sha256", "data_rows", "data_range", "n_trials"]

# Columns added after the first release. Fresh DBs and old DBs converge by
# migration in connect(); old rows keep NULLs, which verify treats as
# "pre-audit, hash check skipped" rather than failure.
AUDIT_COLS = (("code_sha", "TEXT"), ("data_sha256", "TEXT"),
               ("data_rows", "INTEGER"), ("data_range", "TEXT"),
               ("n_trials", "INTEGER"))


def default_db() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "results.db")


def default_results_dir() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")


def connect(db_path: str | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(db_path or default_db())
    con.execute(SCHEMA)
    have = {r[1] for r in con.execute("PRAGMA table_info(runs)")}
    for name, typ in AUDIT_COLS:
        if name not in have:
            con.execute(f"ALTER TABLE runs ADD COLUMN {name} {typ}")
    con.commit()
    return con


def make_id(study: str, dataset: str, params: dict) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    blob = json.dumps({"s": study, "d": dataset, "p": params}, sort_keys=True)
    digest = hashlib.sha1(blob.encode()).hexdigest()[:6]
    safe = "".join(c if c.isalnum() else "_" for c in study)[:24]
    return f"{stamp}-{safe}-{digest}"


def log_run(con: sqlite3.Connection, run_id: str, study: str, dataset: str,
            tag: str, split: str, params: dict, metrics: dict,
            is_metrics: dict | None = None,
            oos_metrics: dict | None = None,
            artifact_dir: str = "", code_sha: str = "",
            data_sha256: str = "", data_rows: int = 0,
            data_range: str = "", n_trials: int = 1) -> None:
    # Append-only: reruns mint new ids (timestamped), history is never
    # overwritten. Callers retry with a suffixed id on collision.
    con.execute(
        "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, datetime.now(timezone.utc).isoformat(), study, dataset,
         tag, split, json.dumps(params), json.dumps(metrics),
         json.dumps(is_metrics) if is_metrics else None,
         json.dumps(oos_metrics) if oos_metrics else None, artifact_dir,
         code_sha, data_sha256, data_rows, data_range, n_trials),
    )
    con.commit()


def list_runs(con: sqlite3.Connection, dataset: str = "",
              study: str = "", tag_like: str = "") -> list[dict]:
    q = "SELECT * FROM runs WHERE 1=1"
    args: list[str] = []
    if dataset:
        q += " AND dataset LIKE ?"
        args.append(f"%{dataset}%")
    if study:
        q += " AND study = ?"
        args.append(study)
    if tag_like:
        q += " AND tag LIKE ?"
        args.append(f"%{tag_like}%")
    q += " ORDER BY created"
    rows = []
    for r in con.execute(q, args):
        d = dict(zip(ALL_COLS, r))
        d["params"] = json.loads(d["params"])
        d["metrics"] = json.loads(d["metrics"])
        d["is_metrics"] = json.loads(d["is_metrics"]) if d["is_metrics"] else None
        d["oos_metrics"] = json.loads(d["oos_metrics"]) if d["oos_metrics"] else None
        rows.append(d)
    return rows


def get_run(con: sqlite3.Connection, run_id: str) -> dict | None:
    """One run by id, JSON columns parsed (mirrors list_runs)."""
    r = con.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if r is None:
        return None
    d = dict(zip(ALL_COLS, r))
    d["params"] = json.loads(d["params"])
    d["metrics"] = json.loads(d["metrics"])
    d["is_metrics"] = json.loads(d["is_metrics"]) if d["is_metrics"] else None
    d["oos_metrics"] = json.loads(d["oos_metrics"]) if d["oos_metrics"] else None
    return d


def code_version(anchor: str = "") -> str:
    """Short git sha of the harness plus -dirty when modified.

    Scoped to the backtest dir so unrelated study edits don't taint the
    record. Never raises: nogit/unknown when git is unavailable.
    """
    import subprocess
    d = os.path.dirname(os.path.abspath(anchor or __file__))
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             cwd=d, capture_output=True, text=True,
                             timeout=15)
        if sha.returncode:
            return "nogit"
        st = subprocess.run(["git", "status", "--porcelain", "--", "."],
                            cwd=d, capture_output=True, text=True,
                            timeout=15)
        return sha.stdout.strip() + ("-dirty" if st.stdout.strip() else "")
    except Exception:
        return "unknown"


def dataset_provenance(dataset_path: str, bars: list) -> dict:
    """sha256 of the dataset bytes plus row count and stamp range."""
    h = hashlib.sha256()
    with open(dataset_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    stamps = [b.stamp for b in bars if getattr(b, "stamp", "")]
    return {"data_sha256": h.hexdigest(), "data_rows": len(bars),
            "data_range": f"{stamps[0]}..{stamps[-1]}" if stamps else ""}
