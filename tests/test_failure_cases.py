from pathlib import Path

import pytest
import yaml

from agent_testbed.controller import run_experiment
from agent_testbed.grading import score_experiment
from agent_testbed.io import read_json, sha256_file
from agent_testbed.reports import report_experiment


def test_agent_exception_is_not_reported_as_finished(
    config_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object, **kwargs: object) -> str:
        raise RuntimeError("fixture agent failed")

    monkeypatch.setattr("agent_testbed.backend.execute_mock", fail)
    root = run_experiment(config_path, tmp_path)
    summary = read_json(report_experiment(root) / "summary.json")
    assert all(row["execution_status"] == "agent_error" for row in summary["rows"])
    assert summary["score_counts"] == {"failed": 4}
    assert len(list(root.glob("runs/*/attempts/*"))) == 4


def test_invalid_private_fixture_is_grader_error(
    config_path: Path, grading_path: Path, tmp_path: Path
) -> None:
    root = run_experiment(config_path, tmp_path, score=False)
    grading = yaml.safe_load(grading_path.read_text())
    copied = tmp_path / "bad-grader"
    copied.mkdir()
    for rule in grading["rules"]:
        source = grading_path.parent / rule["expected"]["path"]
        destination = copied / rule["expected"]["path"]
        destination.write_bytes(source.read_bytes())
        if rule["task_id"] == "T001":
            destination.write_text('{"wrong_shape": true}\n')
            rule["expected"]["sha256"] = sha256_file(destination)
    grading["revision"] = "invalid-schema-fixture"
    path = copied / "grading.yaml"
    path.write_text(yaml.safe_dump(grading))
    batch = score_experiment(root, path)
    summary = read_json(report_experiment(root, batch) / "summary.json")
    assert summary["score_counts"] == {"grader_error": 2, "passed": 1, "failed": 1}


def test_schema_cannot_fetch_external_references(config_path: Path, tmp_path: Path) -> None:
    from agent_testbed.config import _local_schema
    from agent_testbed.io import TestbedError

    with pytest.raises(TestbedError, match="local references"):
        _local_schema({"$ref": "https://example.com/schema.json"})
