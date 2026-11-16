"""Independent deterministic scoring over stable snapshots, without an Agent rerun."""

import csv
import io
import json
from pathlib import Path
from typing import Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from jsonschema.exceptions import ValidationError as SchemaValidationError

from agent_testbed.config import load_grading
from agent_testbed.contracts import GradingSpec, RuleResult, RunResult, ScoreResult, TaskSpec
from agent_testbed.io import (
    TestbedError,
    canonical,
    contained_path,
    digest,
    identifier,
    now,
    read_bytes,
    read_json,
    sha256_file,
    write_json_new,
)
from agent_testbed.storage import verify_snapshot


def _csv_rows(data: bytes, columns: list[str]) -> list[tuple[str, ...]]:
    reader = csv.DictReader(io.StringIO(data.decode("utf-8")))
    if reader.fieldnames != columns:
        raise ValueError("CSV columns do not match the declared order")
    rows = []
    for row in reader:
        if None in row or any(value is None for value in row.values()):
            raise ValueError("CSV row has the wrong number of fields")
        rows.append(tuple(row[col] for col in columns))
    return sorted(rows)


def _score_one(
    root: Path,
    result: RunResult,
    task: TaskSpec,
    snapshot_hash: str,
    grading: GradingSpec,
    grading_root: Path,
) -> ScoreResult:
    score_id = identifier("score")
    rule_results: list[RuleResult] = []
    snapshot_id = "unverified"
    error = None
    status: Literal["passed", "failed", "grader_error"] = "grader_error"
    try:
        manifest_path = contained_path(root, result.snapshot)
        snapshot = verify_snapshot(manifest_path, snapshot_hash)
        snapshot_id = snapshot.snapshot_id
        if snapshot.run_id != result.run.run_id or snapshot.attempt_id != result.attempt_id:
            raise TestbedError("Snapshot identity does not match the run")
        applicable = [rule for rule in grading.rules if rule.task_id == task.task_id]
        if {r.output_path for r in applicable} != {o.path for o in task.outputs}:
            raise TestbedError("Grading rules do not match the task outputs")
        if len(applicable) != len(task.outputs):
            raise TestbedError("Duplicate grading rules")
        available = {artifact.path for artifact in snapshot.artifacts}
        for rule in applicable:
            if rule.task_revision != task.revision:
                raise TestbedError("Grading revision does not match the frozen task")
            spec = next(o for o in task.outputs if o.path == rule.output_path)
            expected_path = contained_path(grading_root, rule.expected.path)
            if sha256_file(expected_path) != rule.expected.sha256:
                raise TestbedError("Grading asset changed")
            if (rule.kind == "exact_json") != (spec.kind == "json"):
                raise TestbedError("Grading rule kind does not match the output type")
            expected = read_bytes(expected_path)
            try:
                if rule.kind == "exact_json":
                    expected_answer = json.loads(expected)
                    validator = Draft202012Validator(spec.json_schema or {})
                    validator.validate(expected_answer)
                else:
                    columns = spec.columns or []
                    expected_rows = _csv_rows(expected, columns)
            except (ValueError, UnicodeError, csv.Error, SchemaValidationError, SchemaError) as exc:
                raise TestbedError(f"Invalid grading fixture: {rule.output_path}") from exc
            passed = False
            explanation = "Required output is missing"
            if rule.output_path in available:
                actual = read_bytes(contained_path(manifest_path.parent / "artifacts", spec.path))
                try:
                    if rule.kind == "exact_json":
                        answer = json.loads(actual)
                        errors = list(validator.iter_errors(answer))
                        passed = not errors and canonical(answer) == canonical(expected_answer)
                        explanation = "JSON schema and expected fields checked"
                    else:
                        passed = _csv_rows(actual, columns) == expected_rows
                        explanation = "CSV headers and row multiset checked"
                except (ValueError, UnicodeError, csv.Error):
                    passed = False
                    explanation = "Output is not valid in the required format"
            rule_results.append(
                RuleResult(
                    rule_id=f"{task.task_id}:{rule.output_path}",
                    output_path=rule.output_path,
                    passed=passed,
                    explanation=explanation,
                )
            )
        status = "passed" if all(rule.passed for rule in rule_results) else "failed"
    except (TestbedError, OSError, ValueError) as exc:
        error = str(exc)
    return ScoreResult(
        score_id=score_id,
        run_id=result.run.run_id,
        snapshot_id=snapshot_id,
        grader_id=grading.grader_id,
        grader_revision=grading.revision,
        grading_hash=digest(grading.model_dump()),
        created_at=now(),
        status=status,
        rules=rule_results,
        error=error,
    )


def score_experiment(root: Path, grading_path: Path) -> Path:
    manifest = read_json(root / "experiment.json")
    grading = load_grading(grading_path)
    tasks = {t["task_id"]: TaskSpec.model_validate(t) for t in manifest["tasks"]}
    batch_id = identifier("scorebatch")
    batch = root / "scores" / batch_id
    entries = []
    for entry in manifest["results"]:
        result_path = contained_path(root, entry["path"])
        if sha256_file(result_path) != entry["sha256"]:
            raise TestbedError("Run result has changed")
        result = RunResult.model_validate(read_json(result_path))
        score = _score_one(
            root,
            result,
            tasks[result.run.task_id],
            entry["snapshot_sha256"],
            grading,
            grading_path.parent,
        )
        score_path = batch / f"{score.score_id}.json"
        write_json_new(score_path, score.model_dump())
        entries.append(
            {"path": str(score_path.relative_to(root)), "sha256": sha256_file(score_path)}
        )
    write_json_new(
        batch / "manifest.json",
        {
            "schema_version": "1",
            "batch_id": batch_id,
            "experiment_id": manifest["experiment_id"],
            "created_at": now(),
            "grader_id": grading.grader_id,
            "grader_revision": grading.revision,
            "grading_hash": digest(grading.model_dump()),
            "scores": entries,
        },
    )
    return batch / "manifest.json"
