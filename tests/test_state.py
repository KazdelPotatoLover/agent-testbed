"""Transactional state tests; no worker or process is signalled by these tests."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agent_testbed.io import TestbedError
from agent_testbed.state import StateStore


def make_store(tmp_path: Path) -> StateStore:
    return StateStore.create(
        tmp_path / "experiment",
        {
            "experiment_id": "exp-state",
            "config_hash": "config-hash",
            "config_path": str(tmp_path / "config.yaml"),
            "protocol": "gated-worker-v2",
            "suite": {"suite_id": "suite", "revision": "1", "tasks": []},
            "systems": ["mock-pass"],
            "tasks": [],
            "runs": [
                {
                    "schema_version": "1",
                    "run_id": f"run-{repeat}",
                    "task_id": "T001",
                    "task_revision": "1",
                    "system_id": "mock-pass",
                    "repeat_index": repeat,
                }
                for repeat in (1, 2)
            ],
        },
    )


def test_durable_control_and_transaction_events(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    claim = store.claim_next("owner-a")
    assert claim and claim["run_id"] == "run-1"
    store.request("paused")
    assert store.claim_next("owner-b") is None
    reopened = StateStore(store.root)
    assert reopened.mode() == "paused"
    assert reopened.rows()[0]["phase"] == "preparing"
    with reopened.connection() as db:
        kinds = [row[0] for row in db.execute("SELECT kind FROM events ORDER BY seq")]
    assert kinds == ["experiment_created", "run_claimed", "control_requested"]
    # Queued tasks have no resources; active unregistered attempts remain explicit in runs.
    assert reopened.status()["unconfirmed_resources"] == []


def test_owner_fencing_and_terminal_records_reject_stale_updates(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    claim = store.claim_next("owner-a")
    wrong = {**claim, "owner": "wrong"}
    with pytest.raises(TestbedError, match="ownership"):
        store.finish(wrong, {}, "finished")
    with pytest.raises(TestbedError, match="unresolved"):
        store.claim_next("owner-b")
    store.finish(claim, {"path": "result.json"}, "infra_error")
    with pytest.raises(TestbedError, match="ownership"):
        store.finish(claim, {}, "finished")
    second = store.claim_next("owner-b")
    assert second["run_id"] == "run-2"
    assert second["attempt_id"] != claim["attempt_id"]


def test_cancel_queued_without_resource_claims_and_never_uncancel(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    claim = store.claim_next("owner-a")
    store.request("cancelled")
    assert store.status()["state"] == "cancel_requested"
    assert store.rows()[1]["cleanup_status"] == "not_applicable"
    assert store.attempt(claim)["stop_reason"] == "cancelled"
    store.finish(claim, {"path": "result.json"}, "cancelled")
    assert store.status()["state"] == "cancelled"
    with pytest.raises(TestbedError):
        store.request("running")
    assert store.claim_next("owner-b") is None


def test_concurrent_claims_cannot_start_two_samples(tmp_path: Path) -> None:
    store = make_store(tmp_path)

    def claim(owner: str) -> str:
        try:
            claimed = store.claim_next(owner)
            return claimed["run_id"] if claimed else "none"
        except TestbedError:
            return "blocked"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, ("a", "b")))
    assert sorted(results) == ["blocked", "run-1"]
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 1


def test_unknown_cleanup_blocks_status_and_foreign_owner(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    claim = store.claim_next("owner")
    # Simulated identity is never used to signal a process in state-only tests.
    store.register_worker(claim, {"pid": 42, "start_ticks": 1, "boot_id": "test"}, 1.0)
    with pytest.raises(TestbedError):
        store.unknown_cleanup({**claim, "owner": "foreign"}, "no")
    store.unknown_cleanup(claim, "termination not confirmed")
    status = store.status()
    assert status["state"] == "recovery_required"
    assert status["unconfirmed_resources"][0]["status"] == "unknown"
    with pytest.raises(TestbedError):
        store.claim_next("other")


def test_schema_v1_migration_keeps_history_and_refuses_unknown_version(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    with store.connection(write=True) as db:
        for column in ("worker_identity", "deadline", "stop_reason"):
            db.execute(f"ALTER TABLE attempts DROP COLUMN {column}")
        db.execute("PRAGMA user_version=1")
    migrated = StateStore(store.root)
    with migrated.connection() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 2
        assert (
            db.execute("SELECT kind FROM events ORDER BY seq DESC LIMIT 1").fetchone()[0]
            == "schema_migrated"
        )
    with migrated.connection(write=True) as db:
        db.execute("PRAGMA user_version=99")
    with pytest.raises(TestbedError, match="Unsupported"):
        StateStore(store.root)
