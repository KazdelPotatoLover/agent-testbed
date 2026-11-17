"""Versioned, evidence-linked JSON/Markdown reports with explicit missing values."""

import os
from collections import Counter
from pathlib import Path
from typing import Any

from agent_testbed.contracts import RunResult, ScoreResult
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
    write_new,
)


def _md(value: str) -> str:
    return value.replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;").replace("\n", " ")


def report_experiment(root: Path, score_batch: Path | None = None) -> Path:
    root = root.resolve()
    manifest = read_json(root / "experiment.json")
    if score_batch is None:
        batches = list((root / "scores").glob("*/manifest.json"))
        if batches:
            score_batch = max(batches, key=lambda p: read_json(p)["created_at"])
    scores = {}
    if score_batch is not None:
        score_batch = score_batch.resolve()
        if not score_batch.is_relative_to(root):
            raise TestbedError("Score batch must belong to this experiment")
        batch = read_json(score_batch)
        if batch["experiment_id"] != manifest["experiment_id"]:
            raise TestbedError("Score batch identity mismatch")
        for entry in batch["scores"]:
            path = contained_path(root, entry["path"])
            if sha256_file(path) != entry["sha256"]:
                raise TestbedError("Score record has changed")
            loaded_score = ScoreResult.model_validate(read_json(path))
            if loaded_score.run_id in scores:
                raise TestbedError("Score batch contains duplicate run IDs")
            scores[loaded_score.run_id] = (loaded_score, path)
    rows = []
    for entry in manifest["results"]:
        path = contained_path(root, entry["path"])
        if sha256_file(path) != entry["sha256"]:
            raise TestbedError("Run result has changed")
        result = RunResult.model_validate(read_json(path))
        score_and_path = scores.get(result.run.run_id)
        score = score_and_path[0] if score_and_path else None
        rows.append(
            {
                "run_id": result.run.run_id,
                "task_id": result.run.task_id,
                "system_id": result.run.system_id,
                "execution_status": result.execution_status,
                "cleanup_status": result.cleanup_status,
                "self_reported_success": result.self_reported_success,
                "score_status": score.status if score else "unscored",
                "score_id": score.score_id if score else None,
                "elapsed_seconds": result.elapsed_seconds,
                "model_tokens": 0 if result.usage else None,
                "real_model_requests": 0 if result.usage else None,
                "model_cost": 0 if result.usage else None,
                "usage_missing_reason": None
                if result.usage
                else "attempt did not finish observably",
                "result": entry["path"],
                "snapshot": result.snapshot,
                "inspect_log": result.inspect_log,
                "score": str(score_and_path[1].relative_to(root)) if score_and_path else None,
            }
        )
    recorded_ids = {row["run_id"] for row in rows}
    unrecorded = [
        state for state in manifest.get("run_states", []) if state["run_id"] not in recorded_ids
    ]
    summary: dict[str, Any] = {
        "schema_version": "1",
        "experiment_id": manifest["experiment_id"],
        "created_at": now(),
        "mode": "deterministic_mock_only_not_agent_capability_evidence",
        "planned_runs": manifest["planned_runs"],
        "recorded_runs": len(rows),
        "unrecorded_runs": unrecorded,
        "run_states": manifest.get("run_states", []),
        "score_counts": dict(Counter(row["score_status"] for row in rows)),
        "systems": {},
        "rows": rows,
    }
    for system in manifest["systems"]:
        group = [row for row in rows if row["system_id"] == system]
        passed = sum(row["score_status"] == "passed" for row in group)
        usage_complete = all(row["model_cost"] is not None for row in group)
        summary["systems"][system] = {
            "recorded_runs": len(group),
            "passed": passed,
            "failed": sum(row["score_status"] == "failed" for row in group),
            "unscored": sum(row["score_status"] == "unscored" for row in group),
            "grader_error": sum(row["score_status"] == "grader_error" for row in group),
            "model_tokens": 0 if usage_complete else None,
            "model_cost": 0 if usage_complete else None,
            "model_cost_per_success": 0 if passed and usage_complete else None,
        }
    report = root / "reports" / identifier("report")
    # Pin the exact execution index: subsequent resume must not alter old report evidence.
    evidence_manifest = root / "manifests" / f"{digest(manifest)}.json"
    ensure_json(evidence_manifest, manifest)
    write_json_new(report / "summary.json", summary)
    write_json_new(
        report / "manifest.json",
        {
            "experiment_sha256": sha256_file(evidence_manifest),
            "experiment_manifest": str(evidence_manifest.relative_to(root)),
            "score_batch": str(score_batch.relative_to(root)) if score_batch else None,
            "score_batch_sha256": sha256_file(score_batch) if score_batch else None,
            "score_ids": [row["score_id"] for row in rows],
        },
    )
    lines = [
        "# Agent Testbed — Mock evaluation report",
        "",
        "> Deterministic engineering fixtures, not a comparison of real Agent capability.",
        "> Model cost only; host compute and developer time are not priced.",
        "",
        f"Experiment: `{manifest['experiment_id']}`",
        f"Planned: {manifest['planned_runs']} · Recorded: {len(rows)}",
        "",
        "| System | Passed | Failed | Unscored | Grader errors | Model tokens |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for system, stats in summary["systems"].items():
        lines.append(
            f"| {_md(system)} | {stats['passed']} | {stats['failed']} | {stats['unscored']} "
            f"| {stats['grader_error']} | {stats['model_tokens']} |"
        )
    lines.extend(
        [
            "",
            "## Per-run evidence",
            "",
            "| Task | System | Execution | Self-report | External score | Evidence |",
            "|---|---|---|---|---|---|",
        ]
    )
    for row in rows:
        links = []
        for key in ("result", "snapshot", "inspect_log", "score"):
            if row[key]:
                relative = os.path.relpath(root / row[key], report)
                links.append(f"[{key}](<{relative}>)")
        lines.append(
            f"| {_md(row['task_id'])} | {_md(row['system_id'])} "
            f"| {row['execution_status']} | {row['self_reported_success']} "
            f"| {row['score_status']} | {' · '.join(links)} |"
        )
    if unrecorded:
        lines.extend(
            [
                "",
                "## Scheduled runs without execution artifacts",
                "",
                "These remain unscored; cancellation before dispatch "
                "is not an Agent capability failure.",
                "",
                "| Run | Phase | Stop reason | Cleanup |",
                "|---|---|---|---|",
            ]
        )
        for state in unrecorded:
            lines.append(
                f"| {_md(state['run_id'])} | {_md(state['phase'])} "
                f"| {_md(str(state['terminal_reason']))} | {_md(state['cleanup_status'])} |"
            )
    lines.extend(["", "Zero successes → cost per success is null, not zero.", ""])
    write_new(report / "summary.md", "\n".join(lines).encode())
    return report
