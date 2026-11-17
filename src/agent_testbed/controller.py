"""Single-host controller: durable intent, gated workers and recoverable collection."""

import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from agent_testbed.config import LoadedExperiment, load_experiment
from agent_testbed.contracts import RunResult, RunSpec, TaskSpec, WorkerOutcome, WorkerRequest
from agent_testbed.grading import score_experiment
from agent_testbed.io import (
    TestbedError,
    contained_path,
    digest,
    ensure_json,
    identifier,
    now,
    read_json,
    sha256_file,
    write_json_new,
    write_json_replace,
)
from agent_testbed.processes import (
    ControllerBusy,
    controller_lock,
    group_members,
    process_identity,
    stop_group,
)
from agent_testbed.state import StateStore
from agent_testbed.storage import freeze_outputs, stage_inputs, verify_snapshot

POLL_SECONDS = 0.05


def run_experiment(config_path: Path, output_root: Path, *, score: bool = True) -> Path:
    loaded = load_experiment(config_path)
    root = output_root.resolve() / identifier("exp")
    root.mkdir(parents=True, exist_ok=False)
    with controller_lock(root):
        runs = [r.model_dump() for r in loaded.matrix(root.name)]
        bundle = {
            "experiment_id": root.name,
            "config_hash": loaded.fingerprint,
            "config_path": str(config_path.resolve()),
            "protocol": "gated-worker-v2",
            "created_at": now(),
            "suite": loaded.suite.model_dump(),
            "systems": loaded.config.systems,
            "tasks": [t.spec.model_dump() for t in loaded.tasks],
            "runs": runs,
        }
        write_json_new(
            root / "plan.json",
            {
                "schema_version": "1",
                "experiment_id": root.name,
                "config_hash": loaded.fingerprint,
                "runs": runs,
            },
        )
        write_json_new(root / "config.resolved.json", loaded.config.model_dump())
        write_json_new(
            root / "environment.json",
            {
                "python": platform.python_version(),
                "inspect_ai": importlib.metadata.version("inspect-ai"),
                "agent_testbed": importlib.metadata.version("agent-testbed"),
                "mode": "trusted_mock_only",
                "protocol": bundle["protocol"],
                "network_guard": "python_socket_guard_not_security_sandbox",
            },
        )
        store = StateStore.create(root, bundle)
        _drive(root, store, loaded)
        if score:
            _score_if_possible(root, config_path, loaded)
    return root


def resume_experiment(root: Path, config_path: Path | None = None, *, score: bool = True) -> Path:
    root = root.resolve()
    with controller_lock(root):
        store = StateStore(root)
        bundle = store.bundle()
        selected = config_path.resolve() if config_path else Path(bundle["config_path"])
        loaded = load_experiment(selected)
        if loaded.fingerprint != bundle["config_hash"]:
            raise TestbedError("Resume configuration differs from the original experiment")
        if store.mode() == "cancelled":
            raise TestbedError("Cancelled experiments cannot be resumed")
        _reconcile(root, store)
        store.request("running")
        _drive(root, store, loaded)
        if score:
            _score_if_possible(root, selected, loaded)
    return root


def pause_experiment(root: Path) -> dict[str, Any]:
    store = StateStore(root.resolve())
    store.request("paused")
    return store.status()


def cancel_experiment(root: Path) -> dict[str, Any]:
    root = root.resolve()
    store = StateStore(root)
    store.request("cancelled")
    try:
        with controller_lock(root):
            # The controller may have crashed. Reclaim only proven worker resources.
            _reconcile(root, store)
            _write_experiment_manifest(root, store)
    except ControllerBusy:
        pass  # Live controller observes the persisted request, including after a crash.
    return store.status()


def _drive(root: Path, store: StateStore, loaded: LoadedExperiment) -> None:
    tasks = {t.spec.task_id: t for t in loaded.tasks}
    while claim := store.claim_next(identifier("owner")):
        attempt = _attempt_path(root, claim)
        task = tasks[claim["spec"]["task_id"]]
        request = WorkerRequest(
            run=RunSpec.model_validate(claim["spec"]),
            task=task.spec,
            attempt_id=claim["attempt_id"],
            wall_time_seconds=loaded.config.execution.wall_time_seconds,
        )
        write_json_new(attempt / "request.json", request.model_dump())
        try:
            stage_inputs(task.spec, task.root, attempt / "work" / "inputs")
        except (OSError, TestbedError) as exc:
            store.stop(claim, "infra_error")
            _collect(root, store, claim, error=str(exc))
            continue
        _run_worker(root, store, claim, request)
        _write_experiment_manifest(root, store)
        if store.mode() != "running":
            break
    _write_experiment_manifest(root, store)


def _attempt_path(root: Path, claim: dict[str, Any]) -> Path:
    return contained_path(root, f"runs/{claim['run_id']}/attempts/{claim['attempt_id']}")


def _run_worker(
    root: Path, store: StateStore, claim: dict[str, Any], request: WorkerRequest
) -> None:
    attempt = _attempt_path(root, claim)
    home = attempt / "work" / "home"
    home.mkdir(parents=True, exist_ok=True)
    environment = {"PATH": os.defpath, "HOME": str(home), "LANG": "C.UTF-8"}
    # Explicit, trusted local fault probes; no model endpoints or secrets are inherited.
    environment.update(
        {
            key: os.environ[key]
            for key in ("TESTBED_WORKER_HOLD", "TESTBED_WORKER_FAIL", "TESTBED_WORKER_OMIT_OUTPUT")
            if key in os.environ
        }
    )
    command = [
        sys.executable,
        "-m",
        "agent_testbed.worker",
        "--request",
        str(attempt / "request.json"),
    ]
    try:
        with (attempt / "worker.log").open("xb") as log:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=log,
                stderr=log,
                cwd=attempt / "work",
                env=environment,
                start_new_session=True,
            )
    except OSError as exc:
        store.stop(claim, "infra_error")
        _collect(root, store, claim, error=f"Worker launch failed: {exc}")
        return
    try:
        identity = process_identity(process.pid)
    except (OSError, TestbedError) as exc:
        # Before the gate opens, this worker cannot have started a task or its children.
        if process.stdin:
            process.stdin.close()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        store.stop(claim, "infra_error")
        _collect(root, store, claim, error=f"Worker registration failed: {exc}")
        return
    try:
        store.register_worker(claim, identity, time.time() + request.wall_time_seconds)
        assert process.stdin is not None
        if store.mode() != "cancelled":
            process.stdin.write(b"G")
            process.stdin.flush()
        process.stdin.close()
        _monitor(store, claim, process)
        _collect(root, store, claim)
    except BaseException:
        if process.stdin and not process.stdin.closed:
            process.stdin.close()
        if not stop_group(identity):
            store.unknown_cleanup(claim, "Worker group did not stop after controller failure")
        raise
    finally:
        if process.poll() is not None:
            process.wait()


def _monitor(
    store: StateStore, claim: dict[str, Any], process: subprocess.Popen[bytes] | None = None
) -> None:
    row = store.attempt(claim)
    if not row["worker_identity"]:
        if row["worker_pid"] or store.bundle().get("protocol") != "gated-worker-v2":
            store.unknown_cleanup(claim, "Worker identity is unavailable")
            raise TestbedError("Cannot verify worker identity; automatic recovery is blocked")
        return  # No registration and the gate pipe closes on parent death: no work started.
    identity = json.loads(row["worker_identity"])
    try:
        while True:
            if process:
                process.poll()  # Reap our own child; orphan zombies are excluded by group_members.
            members = group_members(identity)
            if not members:
                return
            row = store.attempt(claim)
            if store.mode() == "cancelled":
                store.stop(claim, "cancelled")
            elif row["deadline"] is not None and time.time() >= row["deadline"]:
                store.stop(claim, "timed_out")
            row = store.attempt(claim)
            leader_gone = int(identity["pid"]) not in members
            if row["stop_reason"] or leader_gone:
                if not stop_group(identity):
                    raise TestbedError("Worker process group did not stop")
                if process:
                    process.wait(timeout=2)
                return
            time.sleep(POLL_SECONDS)
    except (OSError, TestbedError) as exc:
        store.unknown_cleanup(claim, str(exc))
        raise


def _reconcile(root: Path, store: StateStore) -> None:
    for claim in store.active_claims():
        _monitor(store, claim)
        _collect(root, store, claim, error="Interrupted worker has no committed outcome")
    _write_experiment_manifest(root, store)


def _collect(
    root: Path, store: StateStore, claim: dict[str, Any], *, error: str | None = None
) -> None:
    store.collecting(claim)
    row = store.attempt(claim)
    attempt = _attempt_path(root, claim)
    result_path = attempt / "result.json"
    if result_path.exists():
        # Crash after publication but before SQLite commit: reuse, never execute again.
        result = RunResult.model_validate(read_json(result_path))
    else:
        spec = RunSpec.model_validate(claim["spec"])
        task = next(
            TaskSpec.model_validate(t)
            for t in store.bundle()["tasks"]
            if t["task_id"] == spec.task_id
        )
        outcome_path = attempt / "worker-result.json"
        outcome = None
        if outcome_path.exists():
            outcome = WorkerOutcome.model_validate(read_json(outcome_path))
            if outcome.run_id != spec.run_id or outcome.attempt_id != claim["attempt_id"]:
                raise TestbedError("Worker outcome identity does not match the attempt")
        snapshot_path = attempt / "snapshot"
        freeze_outputs(
            task, attempt / "work" / "output", snapshot_path, spec.run_id, claim["attempt_id"]
        )
        reason = row["stop_reason"] or (outcome.execution_status if outcome else "infra_error")
        result = RunResult(
            run=spec,
            attempt_id=claim["attempt_id"],
            execution_status=reason,
            self_reported_success=outcome.self_reported_success if outcome else None,
            started_at=row["started_at"],
            finished_at=now(),
            elapsed_seconds=max(
                0.0, time.time() - datetime.fromisoformat(row["started_at"]).timestamp()
            ),
            inspect_log=outcome.inspect_log if outcome else None,
            snapshot=str((snapshot_path / "manifest.json").relative_to(root)),
            usage=outcome.usage if outcome else None,
            cleanup_status="confirmed",
            error=outcome.error if outcome else error or f"Worker stopped: {reason}",
        )
        ensure_json(result_path, result.model_dump())
    if (
        result.run != RunSpec.model_validate(claim["spec"])
        or result.attempt_id != claim["attempt_id"]
    ):
        raise TestbedError("Published result identity mismatch")
    manifest_path = contained_path(root, result.snapshot)
    snapshot = verify_snapshot(manifest_path, sha256_file(manifest_path))
    if snapshot.run_id != result.run.run_id or snapshot.attempt_id != result.attempt_id:
        raise TestbedError("Snapshot identity mismatch")
    if result.inspect_log:
        contained_path(root, result.inspect_log)
    store.finish(
        claim,
        {
            "path": str(result_path.relative_to(root)),
            "sha256": sha256_file(result_path),
            "snapshot_sha256": sha256_file(manifest_path),
        },
        result.execution_status,
    )


def _write_experiment_manifest(root: Path, store: StateStore) -> None:
    bundle = store.bundle()
    manifest = {
        "schema_version": "1",
        "experiment_id": bundle["experiment_id"],
        "config_hash": bundle["config_hash"],
        "suite": bundle["suite"],
        "systems": bundle["systems"],
        "planned_runs": len(bundle["runs"]),
        "tasks": bundle["tasks"],
        "results": store.result_entries(),
        "run_states": store.status()["runs"],
    }
    ensure_json(root / "manifests" / f"{digest(manifest)}.json", manifest)
    path = root / "experiment.json"
    if not path.exists() or read_json(path) != manifest:
        write_json_replace(path, manifest)


def _score_if_possible(root: Path, config_path: Path, loaded: LoadedExperiment) -> None:
    manifest = read_json(root / "experiment.json")
    if not manifest["results"]:
        return
    execution_hash = digest(manifest["results"])
    grading_hash = digest(loaded.grading.model_dump())
    for path in (root / "scores").glob("*/manifest.json"):
        batch = read_json(path)
        if (batch.get("execution_hash"), batch["grading_hash"]) == (execution_hash, grading_hash):
            return
    grading_path = (config_path.resolve().parent / loaded.config.grading).resolve()
    score_experiment(root, grading_path)


def experiment_status(root: Path) -> dict[str, Any]:
    root = root.resolve()
    if (root / "state.sqlite").is_file():
        return StateStore(root).status()
    plan = read_json(root / "plan.json")
    if not (root / "experiment.json").is_file():
        return {
            "experiment_id": plan["experiment_id"],
            "state": "incomplete",
            "planned_runs": len(plan["runs"]),
            "note": "Legacy M1 has no state database",
        }
    manifest = read_json(root / "experiment.json")
    return {
        "experiment_id": manifest["experiment_id"],
        "state": "execution_complete",
        "planned_runs": manifest["planned_runs"],
        "recorded_runs": len(manifest["results"]),
    }
