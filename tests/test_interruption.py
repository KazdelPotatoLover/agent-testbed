"""Controller crash/recovery and live pause/cancel integration tests."""

import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from agent_testbed.controller import (
    cancel_experiment,
    experiment_status,
    pause_experiment,
    resume_experiment,
)
from agent_testbed.io import TestbedError, read_json, sha256_file
from agent_testbed.processes import ControllerBusy, group_members, stop_group
from agent_testbed.reports import report_experiment
from agent_testbed.state import StateStore


def eventually(predicate, *, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        found = predicate()
        if found:
            return found
        time.sleep(0.03)
    raise AssertionError("Worker/controller did not reach the expected state")


@contextmanager
def running_controller(config_path: Path, output: Path, *, hold=True):
    environment = {**os.environ}
    if hold:
        environment["TESTBED_WORKER_HOLD"] = "1"
    log = output / "controller.log"
    output.mkdir(parents=True, exist_ok=True)
    with log.open("wb") as stream:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "agent_testbed.cli",
                "run",
                str(config_path),
                "--output",
                str(output),
                "--no-score",
            ],
            stdout=stream,
            stderr=stream,
            env=environment,
        )
        root = None
        try:
            ready = eventually(
                lambda: next(output.glob("exp-*/runs/*/attempts/*/ready.json"), None)
            )
            root = ready.parents[4]
            store = StateStore(root)
            yield process, root, store
        finally:
            # Always release exactly the resources owned by this fixture.
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
            if root is not None:
                for claim in StateStore(root).active_claims():
                    identity = StateStore(root).attempt(claim)["worker_identity"]
                    if identity:
                        stop_group(json.loads(identity), grace_seconds=0.2)


def test_crash_recovery_retains_failed_attempt_without_retry(
    config_path: Path, tmp_path: Path
) -> None:
    with running_controller(config_path, tmp_path) as (process, root, store):
        claim = store.active_claims()[0]
        identity = json.loads(store.attempt(claim)["worker_identity"])
        process.kill()
        process.wait(timeout=10)
        assert stop_group(identity, grace_seconds=0.2)
        assert experiment_status(root)["state"] == "incomplete"
        assert len(read_json(root / "plan.json")["runs"]) == 4
        resume_experiment(root, config_path)
        summary = read_json(report_experiment(root) / "summary.json")
        # retries=0: a killed attempt is infra_error, not silently replaced by a success.
        assert summary["score_counts"] == {"passed": 1, "failed": 3}
        assert summary["rows"][0]["execution_status"] == "infra_error"
        assert len(list(root.glob("runs/*/attempts/*"))) == 4
        assert experiment_status(root)["unconfirmed_resources"] == []
        before = {str(p): sha256_file(p) for p in root.glob("runs/*/attempts/*/result.json")}
        score_batches = list(root.glob("scores/*/manifest.json"))
        resume_experiment(root, config_path)
        assert before == {
            str(p): sha256_file(p) for p in root.glob("runs/*/attempts/*/result.json")
        }
        assert score_batches == list(root.glob("scores/*/manifest.json"))


def test_pause_finishes_active_sample_then_resume_queued(config_path: Path, tmp_path: Path) -> None:
    with running_controller(config_path, tmp_path) as (process, root, store):
        pause_experiment(root)
        assert store.mode() == "paused"
        claim = store.active_claims()[0]
        # Release the fixture: pause must let this sample finish normally.
        (root / "runs" / claim["run_id"] / "attempts" / claim["attempt_id"] / "release").touch()
        process.wait(timeout=15)
        assert process.returncode == 0
        assert len(store.result_entries()) == 1
        assert sum(r["phase"] == "queued" for r in store.rows()) == 3
        resume_experiment(root, config_path)
        assert store.mode() == "running"
        assert experiment_status(root)["state"] == "execution_complete"
        assert len(store.result_entries()) == 4
        summary = read_json(report_experiment(root) / "summary.json")
        assert summary["score_counts"] == {"passed": 2, "failed": 2}


def test_live_cancel_and_cancel_after_controller_crash(config_path: Path, tmp_path: Path) -> None:
    for crashed in (False, True):
        with running_controller(config_path, tmp_path / str(crashed)) as (process, root, store):
            claim = store.active_claims()[0]
            identity = json.loads(store.attempt(claim)["worker_identity"])
            if crashed:
                process.kill()
                process.wait(timeout=10)
            cancel_experiment(root)
            if not crashed:
                process.wait(timeout=15)
                assert process.returncode == 0
            status = experiment_status(root)
            assert status["state"] == "cancelled"
            assert status["unconfirmed_resources"] == []
            assert group_members(identity) == []
            assert all(r["terminal_reason"] == "cancelled" for r in store.rows())
            assert len(store.result_entries()) == 1
            summary = read_json(report_experiment(root) / "summary.json")
            assert len(summary["unrecorded_runs"]) == 3
            assert all(r["terminal_reason"] == "cancelled" for r in summary["unrecorded_runs"])
            assert len(list(root.glob("runs/*/attempts/*"))) == 1
            with pytest.raises(TestbedError, match="Cancelled"):
                resume_experiment(root, config_path)


def test_paused_manifest_stays_pinned_after_resume(config_path: Path, tmp_path: Path) -> None:
    with running_controller(config_path, tmp_path) as (process, root, store):
        pause_experiment(root)
        identity = json.loads(store.attempt(store.active_claims()[0])["worker_identity"])
        stop_group(identity, grace_seconds=0.2)
        process.wait(timeout=15)
        old_report = report_experiment(root)
        pinned = read_json(old_report / "manifest.json")
        pinned_path = root / pinned["experiment_manifest"]
        old_hash = sha256_file(pinned_path)
        resume_experiment(root, config_path)
        assert old_hash == sha256_file(pinned_path) == pinned["experiment_sha256"]
        assert len(read_json(pinned_path)["results"]) == 1
        assert len(read_json(root / "experiment.json")["results"]) == 4


def test_deadline_stops_worker_and_records_timeout(config_path: Path, tmp_path: Path) -> None:
    with running_controller(config_path, tmp_path) as (process, root, store):
        pause_experiment(root)
        claim = store.active_claims()[0]
        identity = json.loads(store.attempt(claim)["worker_identity"])
        with store.connection(write=True) as db:
            db.execute(
                "UPDATE attempts SET deadline=? WHERE attempt_id=?",
                (time.time() - 1, claim["attempt_id"]),
            )
        process.wait(timeout=15)
        assert process.returncode == 0
        assert group_members(identity) == []
        entry = store.result_entries()[0]
        result = read_json(root / entry["path"])
        assert result["execution_status"] == "timed_out"
        assert result["cleanup_status"] == "confirmed"
        assert result["usage"] is None
        assert len(store.result_entries()) == 1


def test_live_controller_refuses_second_resume_without_mutation(
    config_path: Path, tmp_path: Path
) -> None:
    with running_controller(config_path, tmp_path) as (_, root, store):
        before = store.rows()
        with pytest.raises(ControllerBusy):
            resume_experiment(root, config_path)
        assert before == store.rows()


def test_dead_controller_reconnects_to_existing_live_worker(
    config_path: Path, tmp_path: Path
) -> None:
    with running_controller(config_path, tmp_path) as (process, root, store):
        claim = store.active_claims()[0]
        process.kill()
        process.wait(timeout=10)
        # Cancel can reconnect and stop the live orphan with its recorded identity.
        status = cancel_experiment(root)
        assert status["state"] == "cancelled"
        with store.connection() as db:
            assert db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 1
            assert (
                db.execute("SELECT attempt_id FROM attempts").fetchone()[0] == claim["attempt_id"]
            )
