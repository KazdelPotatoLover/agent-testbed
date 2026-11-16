import json
import re
from pathlib import Path

import pytest
from inspect_ai.log import read_eval_log
from typer.testing import CliRunner

from agent_testbed.cli import app
from agent_testbed.controller import run_experiment
from agent_testbed.grading import score_experiment
from agent_testbed.io import read_json, sha256_file
from agent_testbed.reports import report_experiment


def test_full_inspect_run_and_independent_scoring(
    config_path: Path, tmp_path: Path, grading_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = run_experiment(config_path, tmp_path)
    report = report_experiment(root)
    summary = read_json(report / "summary.json")
    assert summary["score_counts"] == {"passed": 2, "failed": 2}
    assert summary["systems"]["mock-pass"]["model_cost_per_success"] == 0
    assert summary["systems"]["mock-wrong-answer"]["model_cost_per_success"] is None
    assert all(row["execution_status"] == "finished" for row in summary["rows"])
    assert all(row["self_reported_success"] is True for row in summary["rows"])
    assert all(row["real_model_requests"] == 0 for row in summary["rows"])
    logs = list(root.glob("runs/*/attempts/*/inspect/*.eval"))
    assert len(logs) == 4
    before = {str(path): sha256_file(path) for path in logs}
    for path in logs:
        log = read_eval_log(path)
        assert log.status == "success"
        assert log.stats.model_usage == {}
        assert len(log.samples) == 1
        assert log.samples[0].target == ""
        assert not log.samples[0].scores
    original_scores = {str(p): sha256_file(p) for p in (root / "scores").rglob("*.json")}

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("offline rescore attempted an Agent execution")

    monkeypatch.setattr("agent_testbed.controller.execute_inspect", forbidden)
    batch = score_experiment(root, grading_path)
    second = report_experiment(root, batch)
    assert read_json(second / "summary.json")["score_counts"] == {"passed": 2, "failed": 2}
    assert before == {str(path): sha256_file(path) for path in logs}
    assert all(sha256_file(Path(p)) == h for p, h in original_scores.items())
    for link in re.findall(r"\]\(<([^>]+)>\)", (second / "summary.md").read_text()):
        assert (second / link).exists()


def test_deferred_scoring_and_cli(config_path: Path, grading_path: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    run = runner.invoke(app, ["run", str(config_path), "--output", str(tmp_path), "--no-score"])
    assert run.exit_code == 0, run.output
    root = Path(json.loads(run.output)["experiment"])
    report = Path(json.loads(run.output)["report"]).parent
    assert read_json(report / "summary.json")["score_counts"] == {"unscored": 4}
    status = runner.invoke(app, ["status", str(root)])
    assert json.loads(status.output)["state"] == "execution_complete"
    score = runner.invoke(app, ["score", str(root), "--grading", str(grading_path)])
    assert score.exit_code == 0, score.output
    scored_report = runner.invoke(app, ["report", str(root)])
    assert scored_report.exit_code == 0, scored_report.output


def test_tampered_snapshot_is_grader_error(
    config_path: Path, grading_path: Path, tmp_path: Path
) -> None:
    root = run_experiment(config_path, tmp_path, score=False)
    first = read_json(root / "experiment.json")["results"][0]
    result = read_json(root / first["path"])
    snapshot = root / result["snapshot"]
    artifact = read_json(snapshot)["artifacts"][0]["path"]
    (snapshot.parent / "artifacts" / artifact).write_text("tampered\n")
    batch = score_experiment(root, grading_path)
    report = report_experiment(root, batch)
    counts = read_json(report / "summary.json")["score_counts"]
    assert counts == {"grader_error": 1, "passed": 1, "failed": 2}


def test_missing_artifact_fails_independent_grader(
    config_path: Path, grading_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def omit_outputs(*args: object, **kwargs: object) -> str:
        return '{"success": true, "artifacts": []}'

    monkeypatch.setattr("agent_testbed.backend.execute_mock", omit_outputs)
    root = run_experiment(config_path, tmp_path)
    summary = read_json(report_experiment(root) / "summary.json")
    assert summary["score_counts"] == {"failed": 4}
