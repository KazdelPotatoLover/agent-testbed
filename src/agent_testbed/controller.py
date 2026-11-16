"""M1 sequential orchestration. Durable recovery and remote lifecycle are M2+."""

import importlib.metadata
import json
import platform
import time
from pathlib import Path
from typing import Any, Literal

from agent_testbed.backend import execute_inspect
from agent_testbed.config import LoadedExperiment, load_experiment
from agent_testbed.contracts import RunResult, Usage
from agent_testbed.grading import score_experiment
from agent_testbed.io import identifier, now, sha256_file, write_json_new
from agent_testbed.offline import offline_execution
from agent_testbed.storage import freeze_outputs, stage_inputs


def run_experiment(config_path: Path, output_root: Path, *, score: bool = True) -> Path:
    loaded = load_experiment(config_path)
    experiment_id = identifier("exp")
    root = output_root.resolve() / experiment_id
    root.mkdir(parents=True, exist_ok=False)
    matrix = loaded.matrix(experiment_id)
    write_json_new(
        root / "plan.json",
        {
            "schema_version": "1",
            "experiment_id": experiment_id,
            "created_at": now(),
            "config_hash": loaded.fingerprint,
            "runs": [run.model_dump() for run in matrix],
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
            "network_guard": "python_socket_guard_not_security_sandbox",
        },
    )
    entries = _execute_runs(loaded, root, experiment_id)
    write_json_new(
        root / "experiment.json",
        {
            "schema_version": "1",
            "experiment_id": experiment_id,
            "completed_at": now(),
            "config_hash": loaded.fingerprint,
            "suite": loaded.suite.model_dump(),
            "systems": loaded.config.systems,
            "planned_runs": len(matrix),
            "tasks": [task.spec.model_dump() for task in loaded.tasks],
            "results": entries,
        },
    )
    if score:
        grading_path = (config_path.resolve().parent / loaded.config.grading).resolve()
        score_experiment(root, grading_path)
    return root


def _execute_runs(loaded: LoadedExperiment, root: Path, experiment_id: str) -> list[dict[str, str]]:
    tasks = {task.spec.task_id: task for task in loaded.tasks}
    entries = []
    for run in loaded.matrix(experiment_id):
        task = tasks[run.task_id]
        attempt_id = identifier("attempt")
        attempt = root / "runs" / run.run_id / "attempts" / attempt_id
        inputs = attempt / "work" / "inputs"
        outputs = attempt / "work" / "output"
        started_at = now()
        start = time.monotonic()
        state: Literal["finished", "agent_error", "infra_error", "timed_out"] = "infra_error"
        error = None
        inspect_log = None
        claimed = None
        usage = None
        try:
            stage_inputs(task.spec, task.root, inputs)
            with offline_execution():
                log = execute_inspect(
                    task.spec,
                    run.system_id,
                    run.run_id,
                    attempt_id,
                    inputs,
                    outputs,
                    attempt / "inspect",
                    loaded.config.execution.wall_time_seconds,
                )
            if log.location:
                inspect_log = str(Path(log.location).relative_to(root))
            samples = log.samples or []
            if len(samples) == 1:
                sample = samples[0]
                if sample.limit and sample.limit.type == "time":
                    state = "timed_out"
                    error = f"Inspect sample limit: {sample.limit.type}"
                elif sample.limit:
                    state, error = "agent_error", f"Unexpected sample limit: {sample.limit.type}"
                elif sample.error:
                    state, error = "agent_error", sample.error.message
                elif log.status == "success":
                    state = "finished"
                    claim = json.loads(sample.output.completion).get("success")
                    claimed = claim if isinstance(claim, bool) else None
                else:
                    error = log.error.message if log.error else f"Inspect status: {log.status}"
                usage = Usage()
            else:
                error = log.error.message if log.error else f"Inspect status: {log.status}"
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        snapshot_path = attempt / "snapshot"
        freeze_outputs(task.spec, outputs, snapshot_path, run.run_id, attempt_id)
        result = RunResult(
            run=run,
            attempt_id=attempt_id,
            execution_status=state,
            self_reported_success=claimed,
            started_at=started_at,
            finished_at=now(),
            elapsed_seconds=time.monotonic() - start,
            inspect_log=inspect_log,
            snapshot=str((snapshot_path / "manifest.json").relative_to(root)),
            usage=usage,
            error=error,
        )
        result_path = attempt / "result.json"
        write_json_new(result_path, result.model_dump())
        entries.append(
            {
                "path": str(result_path.relative_to(root)),
                "sha256": sha256_file(result_path),
                "snapshot_sha256": sha256_file(snapshot_path / "manifest.json"),
            }
        )
    return entries


def experiment_status(root: Path) -> dict[str, Any]:
    from agent_testbed.io import read_json

    plan = read_json(root / "plan.json")
    result_path = root / "experiment.json"
    if not result_path.exists():
        return {
            "experiment_id": plan["experiment_id"],
            "state": "incomplete",
            "planned_runs": len(plan["runs"]),
            "note": "M1 does not resume interrupted experiments; artifacts are retained.",
        }
    manifest = read_json(result_path)
    return {
        "experiment_id": manifest["experiment_id"],
        "state": "execution_complete",
        "planned_runs": manifest["planned_runs"],
        "recorded_runs": len(manifest["results"]),
    }
