"""Review queue and audit log in SQLite.

Every alert above the threshold becomes a case. An analyst closes it with a disposition.
The audit log is append-only from the application's point of view: rows are inserted,
never updated or deleted, and each row carries a hash of the previous row so tampering
with history is detectable.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time

DISPOSITIONS = {"true_match", "false_positive", "escalated"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created REAL NOT NULL,
  subject_ref TEXT NOT NULL,
  subject_name TEXT NOT NULL,
  entry_id TEXT NOT NULL,
  list_type TEXT NOT NULL,
  score REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  disposition TEXT,
  analyst TEXT,
  note TEXT,
  closed REAL,
  UNIQUE(subject_ref, entry_id)
);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  detail TEXT NOT NULL,
  prev_hash TEXT NOT NULL,
  hash TEXT NOT NULL
);
"""


class ReviewStore:
    def __init__(self, path: str = ":memory:") -> None:
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()

    # -- audit
    def audit(self, actor: str, action: str, **detail) -> None:
        with self.lock:
            row = self.db.execute("SELECT hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
            prev = row["hash"] if row else "genesis"
            ts = time.time()
            body = json.dumps(detail, sort_keys=True)
            h = hashlib.sha256(f"{prev}|{ts}|{actor}|{action}|{body}".encode()).hexdigest()
            self.db.execute("INSERT INTO audit(ts, actor, action, detail, prev_hash, hash) VALUES (?,?,?,?,?,?)",
                            (ts, actor, action, body, prev, h))
            self.db.commit()

    def audit_log(self, limit: int = 100) -> list[dict]:
        rows = self.db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(r), "detail": json.loads(r["detail"])} for r in rows]

    def verify_chain(self) -> bool:
        prev = "genesis"
        for r in self.db.execute("SELECT * FROM audit ORDER BY id"):
            h = hashlib.sha256(f"{prev}|{r['ts']}|{r['actor']}|{r['action']}|{r['detail']}".encode()).hexdigest()
            if r["prev_hash"] != prev or r["hash"] != h:
                return False
            prev = h
        return True

    # -- cases
    def open_case(self, subject_ref: str, subject_name: str, entry_id: str, list_type: str, score: float) -> int:
        """Open a case unless one already exists for this subject and entry. Returns the case id."""
        with self.lock:
            row = self.db.execute("SELECT id FROM cases WHERE subject_ref=? AND entry_id=?", (subject_ref, entry_id)).fetchone()
            if row:
                return row["id"]
            cur = self.db.execute(
                "INSERT INTO cases(created, subject_ref, subject_name, entry_id, list_type, score) VALUES (?,?,?,?,?,?)",
                (time.time(), subject_ref, subject_name, entry_id, list_type, score))
            self.db.commit()
            cid = cur.lastrowid
        self.audit("system", "case_opened", case_id=cid, subject_ref=subject_ref, entry_id=entry_id, score=score)
        return cid

    def get(self, case_id: int) -> dict | None:
        r = self.db.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
        return dict(r) if r else None

    def queue(self, status: str | None = "open", limit: int = 200) -> list[dict]:
        if status:
            rows = self.db.execute("SELECT * FROM cases WHERE status=? ORDER BY score DESC, id LIMIT ?", (status, limit))
        else:
            rows = self.db.execute("SELECT * FROM cases ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def dispose(self, case_id: int, disposition: str, analyst: str, note: str = "") -> dict:
        if disposition not in DISPOSITIONS:
            raise ValueError(f"disposition must be one of {sorted(DISPOSITIONS)}")
        case = self.get(case_id)
        if case is None:
            raise KeyError(case_id)
        if case["status"] == "closed":
            raise ValueError("case is already closed")
        status = "escalated" if disposition == "escalated" else "closed"
        with self.lock:
            self.db.execute("UPDATE cases SET status=?, disposition=?, analyst=?, note=?, closed=? WHERE id=?",
                            (status, disposition, analyst, note, time.time() if status == "closed" else None, case_id))
            self.db.commit()
        self.audit(analyst, "case_disposition", case_id=case_id, disposition=disposition, note=note)
        return self.get(case_id)

    def counts(self) -> dict[str, int]:
        return {r["status"]: r["n"] for r in self.db.execute("SELECT status, COUNT(*) n FROM cases GROUP BY status")}
