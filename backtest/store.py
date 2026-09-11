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


def default_db() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "results.db")


def default_results_dir() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")


def connect(db_path: str | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(db_path or default_db())
    con.execute(SCHEMA)
    con.commit()
    return con


def make_id(study: str, dataset: str, params: dict) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    blob = json.dumps({"s": study, "d": dataset, "p": params}, sort_keys=True)
    digest = hashlib.sha1(blob.encode()).hexdigest()[:6]
    safe = "".join(c if c.isalnum() else "_" for c in study)[:24]
    return f"{stamp}-{safe}-{digest}"


def log_run(con: sqlite3.Connection, run_id: str, study: str, dataset: str,
            tag: str, split: str, params: dict, metrics: dict,
            is_metrics: dict | None = None,
            oos_metrics: dict | None = None,
            artifact_dir: str = "") -> None:
    con.execute(
        "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, datetime.now(timezone.utc).isoformat(), study, dataset,
         tag, split, json.dumps(params), json.dumps(metrics),
         json.dumps(is_metrics) if is_metrics else None,
         json.dumps(oos_metrics) if oos_metrics else None, artifact_dir),
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
    cols = ["id", "created", "study", "dataset", "tag", "split", "params",
            "metrics", "is_metrics", "oos_metrics", "artifact_dir"]
    rows = []
    for r in con.execute(q, args):
        d = dict(zip(cols, r))
        d["params"] = json.loads(d["params"])
        d["metrics"] = json.loads(d["metrics"])
        d["is_metrics"] = json.loads(d["is_metrics"]) if d["is_metrics"] else None
        d["oos_metrics"] = json.loads(d["oos_metrics"]) if d["oos_metrics"] else None
        rows.append(d)
    return rows
