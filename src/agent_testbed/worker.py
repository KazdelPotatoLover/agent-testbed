"""Trusted mock worker: gated start and public request; not an OS security boundary."""

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Literal

from agent_testbed.backend import execute_inspect
from agent_testbed.contracts import Usage, WorkerOutcome, WorkerRequest
from agent_testbed.io import read_json, write_json_new
from agent_testbed.offline import offline_execution


def execute_attempt(request_path: Path) -> None:
    request = WorkerRequest.model_validate(read_json(request_path))
    attempt = request_path.parent
    state: Literal["finished", "agent_error", "infra_error", "timed_out"] = "infra_error"
    error = None
    inspect_log = None
    claimed = None
    usage = None
    if os.environ.get("TESTBED_WORKER_HOLD") == "1":
        write_json_new(attempt / "ready.json", {"pid": os.getpid()})
        deadline = time.monotonic() + 60
        while not (attempt / "release").exists() and time.monotonic() < deadline:
            time.sleep(0.03)
    try:
        if os.environ.get("TESTBED_WORKER_FAIL") == "1":
            state = "agent_error"
            raise RuntimeError("fixture agent failed")
        with offline_execution():
            log = execute_inspect(
                request.task,
                request.run.system_id,
                request.run.run_id,
                request.attempt_id,
                attempt / "work" / "inputs",
                attempt / "work" / "output",
                attempt / "inspect",
                request.wall_time_seconds,
            )
        if log.location:
            # Worker logs are relative to the experiment directory.
            inspect_log = str(Path(log.location).relative_to(attempt.parents[3]))
        samples = log.samples or []
        if len(samples) == 1:
            sample = samples[0]
            if sample.limit:
                state = "timed_out" if sample.limit.type == "time" else "agent_error"
                error = f"Inspect sample limit: {sample.limit.type}"
            elif sample.error:
                state, error = "agent_error", sample.error.message
            elif log.status == "success":
                state = "finished"
                value = json.loads(sample.output.completion).get("success")
                claimed = value if isinstance(value, bool) else None
            else:
                error = log.error.message if log.error else f"Inspect status: {log.status}"
            usage = Usage()
        else:
            error = log.error.message if log.error else f"Inspect status: {log.status}"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    if os.environ.get("TESTBED_WORKER_OMIT_OUTPUT") == "1":
        shutil.rmtree(attempt / "work" / "output", ignore_errors=True)
    outcome = WorkerOutcome(
        run_id=request.run.run_id,
        attempt_id=request.attempt_id,
        execution_status=state,
        self_reported_success=claimed,
        inspect_log=inspect_log,
        usage=usage,
        error=error,
    )
    write_json_new(attempt / "worker-result.json", outcome.model_dump())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    args = parser.parse_args()
    # Parent closes this pipe on death. No task is run until registration is committed.
    if sys.stdin.buffer.read(1) != b"G":
        return
    execute_attempt(args.request.resolve())


if __name__ == "__main__":
    main()
