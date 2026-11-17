"""Durable single-host state. Worker execution and audit writes have one owner."""

import json
import sqlite3
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from agent_testbed.io import TestbedError, canonical, identifier, now

DDL = """
CREATE TABLE experiment (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 experiment_id TEXT NOT NULL, config_hash TEXT NOT NULL,
 mode TEXT NOT NULL CHECK(mode IN ('running','paused','cancelled')),
 bundle TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE runs (
 ordinal INTEGER PRIMARY KEY, run_id TEXT NOT NULL UNIQUE, spec TEXT NOT NULL,
 phase TEXT NOT NULL, terminal_reason TEXT, cleanup_status TEXT NOT NULL DEFAULT 'pending',
 current_attempt_id TEXT UNIQUE, result_entry TEXT, outcome TEXT, owner TEXT
);
CREATE TABLE attempts (
 attempt_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
 attempt_no INTEGER NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
 owner TEXT NOT NULL, phase TEXT NOT NULL, status TEXT NOT NULL,
 worker_pid INTEGER, started_at TEXT NOT NULL, finished_at TEXT,
 result_entry TEXT, cleanup_status TEXT NOT NULL DEFAULT 'pending',
 worker_identity TEXT, deadline REAL, stop_reason TEXT,
 UNIQUE(run_id,attempt_no)
);
CREATE TABLE resources (
 resource_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
 attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id), owner TEXT NOT NULL,
 kind TEXT NOT NULL, external_id TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('active','confirmed','unknown'))
);
CREATE TABLE events (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, run_id TEXT,
 kind TEXT NOT NULL, payload TEXT NOT NULL
);
PRAGMA user_version=2;
"""
ACTIVE = ("preparing", "running", "collecting")


class StateStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.path = self.root / "state.sqlite"
        if self.path.is_symlink() or not self.path.is_file():
            raise TestbedError("No M2 state database; legacy M1 runs are read-only")
        with self.connection(write=True) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version == 1:
                for column in ("worker_identity TEXT", "deadline REAL", "stop_reason TEXT"):
                    db.execute(f"ALTER TABLE attempts ADD COLUMN {column}")
                db.execute("PRAGMA user_version=2")
                _event(db, "schema_migrated", {"from": 1, "to": 2})
            elif version != 2:
                raise TestbedError("Unsupported state database schema version")

    @classmethod
    def create(cls, root: Path, bundle: dict[str, Any]) -> "StateStore":
        root = root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        path = root / "state.sqlite"
        with path.open("xb"):
            pass
        db = sqlite3.connect(path)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA foreign_keys=ON")
            db.executescript("BEGIN IMMEDIATE;\n" + DDL)
            db.execute(
                "INSERT INTO experiment VALUES(1,?,?,'running',?,?)",
                (bundle["experiment_id"], bundle["config_hash"], canonical(bundle).decode(), now()),
            )
            for ordinal, run in enumerate(bundle["runs"]):
                db.execute(
                    "INSERT INTO runs(ordinal,run_id,spec,phase) VALUES(?,?,?,'queued')",
                    (ordinal, run["run_id"], canonical(run).decode()),
                )
            _event(db, "experiment_created", {"planned_runs": len(bundle["runs"])})
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
        return cls(root)

    @contextmanager
    def connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        try:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def bundle(self) -> dict[str, Any]:
        with self.connection() as db:
            value: dict[str, Any] = json.loads(
                db.execute("SELECT bundle FROM experiment").fetchone()[0]
            )
            return value

    def mode(self) -> str:
        with self.connection() as db:
            return str(db.execute("SELECT mode FROM experiment").fetchone()[0])

    def request(self, mode: str) -> None:
        if mode not in {"running", "paused", "cancelled"}:
            raise TestbedError("Invalid control request")
        with self.connection(write=True) as db:
            current = db.execute("SELECT mode FROM experiment").fetchone()[0]
            if current == "cancelled" and mode != "cancelled":
                raise TestbedError("Cancelled experiments cannot be resumed")
            if current != mode:
                db.execute("UPDATE experiment SET mode=?", (mode,))
                _event(db, "control_requested", {"from": current, "mode": mode})
            if mode == "cancelled":
                db.execute(
                    "UPDATE runs SET phase='terminal',terminal_reason='cancelled',"
                    "cleanup_status='not_applicable' WHERE phase='queued'"
                )
                db.execute(
                    "UPDATE attempts SET stop_reason=COALESCE(stop_reason,'cancelled') "
                    "WHERE status='active'"
                )

    def claim_next(self, owner: str) -> dict[str, Any] | None:
        with self.connection(write=True) as db:
            if db.execute("SELECT mode FROM experiment").fetchone()[0] != "running":
                return None
            if db.execute("SELECT 1 FROM attempts WHERE status='active'").fetchone():
                raise TestbedError("An unresolved attempt blocks new dispatch")
            row = db.execute(
                "SELECT * FROM runs WHERE phase='queued' ORDER BY ordinal LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            attempt_id = identifier("attempt")
            number = db.execute(
                "SELECT COALESCE(MAX(attempt_no),0)+1 FROM attempts WHERE run_id=?",
                (row["run_id"],),
            ).fetchone()[0]
            key = f"{row['run_id']}:attempt:{number}"
            db.execute(
                "UPDATE runs SET phase='preparing',current_attempt_id=?,owner=? WHERE run_id=?",
                (attempt_id, owner, row["run_id"]),
            )
            db.execute(
                "INSERT INTO attempts(attempt_id,run_id,attempt_no,idempotency_key,owner,"
                "phase,status,started_at) VALUES(?,?,?,?,?,'preparing','active',?)",
                (attempt_id, row["run_id"], number, key, owner, now()),
            )
            _event(db, "run_claimed", {"attempt_id": attempt_id, "key": key}, row["run_id"])
            return {
                "run_id": row["run_id"],
                "attempt_id": attempt_id,
                "spec": json.loads(row["spec"]),
                "owner": owner,
            }

    def register_worker(
        self, claim: dict[str, Any], identity: dict[str, int | str], deadline: float
    ) -> None:
        with self.connection(write=True) as db:
            row = _owned(db, claim)
            if row["phase"] != "preparing" or row["worker_pid"] is not None:
                raise TestbedError("Attempt already has a registered worker")
            db.execute(
                "UPDATE attempts SET phase='running',worker_pid=?,worker_identity=?,deadline=? "
                "WHERE attempt_id=?",
                (identity["pid"], canonical(identity).decode(), deadline, claim["attempt_id"]),
            )
            db.execute("UPDATE runs SET phase='running' WHERE run_id=?", (claim["run_id"],))
            db.execute(
                "INSERT INTO resources VALUES(?,?,?,?,?,?, 'active')",
                (
                    identifier("resource"),
                    claim["run_id"],
                    claim["attempt_id"],
                    claim["owner"],
                    "local-process-group",
                    canonical(identity).decode(),
                ),
            )
            _event(db, "worker_registered", identity, claim["run_id"])

    def attempt(self, claim: dict[str, Any]) -> dict[str, Any]:
        with self.connection() as db:
            return dict(_owned(db, claim))

    def active_claims(self) -> list[dict[str, Any]]:
        with self.connection() as db:
            return [
                {
                    "run_id": r["run_id"],
                    "attempt_id": r["attempt_id"],
                    "owner": r["owner"],
                    "spec": json.loads(r["spec"]),
                }
                for r in db.execute(
                    "SELECT a.*,r.spec FROM attempts a JOIN runs r "
                    "ON r.current_attempt_id=a.attempt_id WHERE a.status='active'"
                )
            ]

    def stop(self, claim: dict[str, Any], reason: str) -> str:
        if reason not in {"cancelled", "timed_out", "infra_error"}:
            raise TestbedError("Invalid stop reason")
        with self.connection(write=True) as db:
            row = _owned(db, claim)
            chosen = row["stop_reason"] or reason
            db.execute(
                "UPDATE attempts SET stop_reason=? WHERE attempt_id=?",
                (chosen, claim["attempt_id"]),
            )
            _event(db, "stop_requested", {"reason": chosen}, claim["run_id"])
            return str(chosen)

    def unknown_cleanup(self, claim: dict[str, Any], error: str) -> None:
        with self.connection(write=True) as db:
            _owned(db, claim)
            db.execute(
                "UPDATE runs SET cleanup_status='unknown' WHERE run_id=?", (claim["run_id"],)
            )
            db.execute(
                "UPDATE attempts SET cleanup_status='unknown' WHERE attempt_id=?",
                (claim["attempt_id"],),
            )
            db.execute(
                "UPDATE resources SET status='unknown' WHERE attempt_id=? AND owner=?",
                (claim["attempt_id"], claim["owner"]),
            )
            _event(db, "cleanup_unknown", {"error": error}, claim["run_id"])

    def collecting(self, claim: dict[str, Any]) -> None:
        with self.connection(write=True) as db:
            row = _owned(db, claim)
            if row["phase"] == "collecting":
                return
            db.execute("UPDATE runs SET phase='collecting' WHERE run_id=?", (claim["run_id"],))
            db.execute(
                "UPDATE attempts SET phase='collecting' WHERE attempt_id=?", (claim["attempt_id"],)
            )
            _event(db, "collecting_started", {"attempt_id": claim["attempt_id"]}, claim["run_id"])

    def finish(self, claim: dict[str, Any], entry: dict[str, str], outcome: str) -> None:
        with self.connection(write=True) as db:
            _owned(db, claim)
            db.execute(
                "UPDATE runs SET phase='terminal',terminal_reason=?,cleanup_status='confirmed',"
                "result_entry=?,outcome=? WHERE run_id=?",
                (outcome, canonical(entry).decode(), outcome, claim["run_id"]),
            )
            db.execute(
                "UPDATE attempts SET phase='terminal',status=?,finished_at=?,result_entry=?,"
                "cleanup_status='confirmed' WHERE attempt_id=?",
                (
                    "cancelled"
                    if outcome == "cancelled"
                    else "finished"
                    if outcome == "finished"
                    else "failed",
                    now(),
                    canonical(entry).decode(),
                    claim["attempt_id"],
                ),
            )
            db.execute(
                "UPDATE resources SET status='confirmed' WHERE attempt_id=? AND owner=?",
                (claim["attempt_id"], claim["owner"]),
            )
            _event(db, "run_finished", {"outcome": outcome}, claim["run_id"])

    def rows(self) -> list[dict[str, Any]]:
        with self.connection() as db:
            return [dict(r) for r in db.execute("SELECT * FROM runs ORDER BY ordinal")]

    def status(self) -> dict[str, Any]:
        with self.connection() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM runs ORDER BY ordinal")]
            experiment = db.execute("SELECT * FROM experiment").fetchone()
            unresolved = [
                dict(r) for r in db.execute("SELECT * FROM resources WHERE status!='confirmed'")
            ]
        mode = experiment["mode"]
        counts = Counter(r["phase"] for r in rows)
        complete = counts["terminal"] == len(rows)
        state = (
            "recovery_required"
            if any(r["status"] == "unknown" for r in unresolved)
            or any(r["cleanup_status"] == "unknown" for r in rows)
            else "cancel_requested"
            if mode == "cancelled" and not complete
            else "cancelled"
            if mode == "cancelled"
            else "execution_complete"
            if complete
            else "paused"
            if mode == "paused"
            else "incomplete"
        )
        return {
            "experiment_id": experiment["experiment_id"],
            "state": state,
            "control_mode": mode,
            "planned_runs": len(rows),
            "recorded_runs": sum(r["result_entry"] is not None for r in rows),
            "phases": dict(counts),
            "unconfirmed_resources": unresolved,
            "runs": [
                {
                    key: r[key]
                    for key in (
                        "run_id",
                        "current_attempt_id",
                        "phase",
                        "terminal_reason",
                        "cleanup_status",
                    )
                }
                for r in rows
            ],
        }

    def result_entries(self) -> list[dict[str, str]]:
        return [json.loads(r["result_entry"]) for r in self.rows() if r["result_entry"]]


def _owned(db: sqlite3.Connection, claim: dict[str, Any]) -> sqlite3.Row:
    row = db.execute(
        "SELECT a.* FROM attempts a JOIN runs r ON r.current_attempt_id=a.attempt_id "
        "WHERE a.attempt_id=? AND a.run_id=? AND a.owner=? AND r.owner=? AND a.status='active'",
        (claim["attempt_id"], claim["run_id"], claim["owner"], claim["owner"]),
    ).fetchone()
    if row is None:
        raise TestbedError("Attempt ownership was lost or the attempt is terminal")
    return cast(sqlite3.Row, row)


def _event(db: sqlite3.Connection, kind: str, payload: Any, run_id: str | None = None) -> None:
    db.execute(
        "INSERT INTO events(at,run_id,kind,payload) VALUES(?,?,?,?)",
        (now(), run_id, kind, canonical(payload).decode()),
    )
