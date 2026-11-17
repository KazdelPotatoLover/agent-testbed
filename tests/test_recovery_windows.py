"""Deterministic crash windows around registration, snapshots and state commit."""

import subprocess
from pathlib import Path

import pytest
import yaml

from agent_testbed.config import load_experiment
from agent_testbed.controller import (
    cancel_experiment,
    experiment_status,
    resume_experiment,
    run_experiment,
)
from agent_testbed.io import TestbedError, read_json, sha256_file
from agent_testbed.reports import report_experiment
from agent_testbed.state import StateStore
from agent_testbed.storage import stage_inputs


def test_crash_after_result_publication_reuses_same_attempt(
    config_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = StateStore.finish
    called = False

    def crash_once(self, claim, entry, outcome):
        nonlocal called
        if not called:
            called = True
            raise RuntimeError("crash before transaction commit")
        return original(self, claim, entry, outcome)

    monkeypatch.setattr(StateStore, "finish", crash_once)
    with pytest.raises(RuntimeError, match="transaction commit"):
        run_experiment(config_path, tmp_path, score=False)
    root = next(tmp_path.glob("exp-*"))
    result_path = next(root.glob("runs/*/attempts/*/result.json"))
    before = sha256_file(result_path)
    resume_experiment(root, config_path)
    assert sha256_file(result_path) == before
    assert len(list(root.glob("runs/*/attempts/*"))) == 4
    summary = read_json(report_experiment(root) / "summary.json")
    assert summary["score_counts"] == {"passed": 2, "failed": 2}


def test_worker_receives_public_task_and_no_grading_or_provider_secrets(
    config_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed = []
    original = subprocess.Popen

    def inspect_launch(command, **kwargs):
        request = read_json(Path(command[-1]))
        observed.append(request)
        assert "--config" not in command
        assert "OPENAI_API_KEY" not in kwargs["env"]
        assert "grading" not in request and "expected_answer" not in request
        assert request["task"]["task_id"] in {"T001", "T003"}
        assert set(request) == {"schema_version", "run", "task", "attempt_id", "wall_time_seconds"}
        return original(command, **kwargs)

    monkeypatch.setenv("OPENAI_API_KEY", "LOCAL_TEST_CANARY_NOT_A_CREDENTIAL")
    monkeypatch.setattr("agent_testbed.controller.subprocess.Popen", inspect_launch)
    root = run_experiment(config_path, tmp_path, score=False)
    assert len(observed) == 4
    for path in root.glob("runs/*/attempts/*/request.json"):
        assert "LOCAL_TEST_CANARY" not in path.read_text()


def test_cancel_does_not_require_original_input_or_grading_files(
    config_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agent_testbed.controller as controller

    def cancel_at_dispatch(root, store, loaded):
        store.request("paused")

    monkeypatch.setattr(controller, "_drive", cancel_at_dispatch)
    root = run_experiment(config_path, tmp_path, score=False)
    status = cancel_experiment(root)
    assert status["state"] == "cancelled"
    assert status["recorded_runs"] == 0
    assert status["unconfirmed_resources"] == []
    assert all(r["terminal_reason"] == "cancelled" for r in status["runs"])
    report = read_json(report_experiment(root) / "summary.json")
    assert report["planned_runs"] == 4


def test_config_drift_refuses_resume_before_dispatch(
    config_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def pause_at_dispatch(root, store, loaded):
        store.request("paused")

    monkeypatch.setattr("agent_testbed.controller._drive", pause_at_dispatch)
    root = run_experiment(config_path, tmp_path, score=False)
    config = yaml.safe_load(config_path.read_text())
    config["suite"] = str((config_path.parent / config["suite"]).resolve())
    config["grading"] = str((config_path.parent / config["grading"]).resolve())
    config["repeats"] = 2
    changed = tmp_path / "changed.yaml"
    changed.write_text(yaml.safe_dump(config))
    with pytest.raises(TestbedError, match="differs"):
        resume_experiment(root, changed)
    assert experiment_status(root)["control_mode"] == "paused"
    assert list(root.glob("runs/*/attempts/*")) == []


def test_stage_never_copies_private_answer(config_path: Path, tmp_path: Path) -> None:
    loaded = load_experiment(config_path)
    for task in loaded.tasks:
        target = tmp_path / task.spec.task_id
        stage_inputs(task.spec, task.root, target)
        assert sorted(
            str(p.relative_to(target)) for p in target.rglob("*") if p.is_file()
        ) == sorted(a.path for a in task.spec.inputs)
        assert all("expected" not in str(p) for p in target.rglob("*"))


def test_registration_failure_closes_gate_and_reaps_worker(
    config_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def cannot_identify(pid):
        raise TestbedError("fixture identity read failure")

    monkeypatch.setattr("agent_testbed.controller.process_identity", cannot_identify)
    root = run_experiment(config_path, tmp_path, score=False)
    status = experiment_status(root)
    assert status["recorded_runs"] == 4
    assert all(r["terminal_reason"] == "infra_error" for r in status["runs"])
    assert status["unconfirmed_resources"] == []
    assert list(root.glob("runs/*/attempts/*/inspect/*.eval")) == []
    assert list(root.glob("runs/*/attempts/*/worker-result.json")) == []


def test_launch_failure_becomes_visible_infra_error(
    config_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_launch(*args, **kwargs):
        raise OSError("fixture launch failure")

    monkeypatch.setattr("agent_testbed.controller.subprocess.Popen", fail_launch)
    root = run_experiment(config_path, tmp_path, score=False)
    status = experiment_status(root)
    assert status["state"] == "execution_complete"
    assert status["recorded_runs"] == 4
    assert all(r["terminal_reason"] == "infra_error" for r in status["runs"])
    assert status["unconfirmed_resources"] == []
