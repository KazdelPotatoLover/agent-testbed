"""Inspect execution boundary. The only supported provider is a local mock."""

from pathlib import Path
from typing import Any

from inspect_ai import Task, eval
from inspect_ai.dataset import Sample
from inspect_ai.log import EvalLog
from inspect_ai.model import ModelOutput, get_model
from inspect_ai.solver import Generate, Solver, TaskState, solver

from agent_testbed.adapters import execute_mock
from agent_testbed.contracts import TaskSpec
from agent_testbed.io import TestbedError


@solver
def fixture_solver(task_data: dict[str, Any], system_id: str, inputs: str, output: str) -> Solver:
    task = TaskSpec.model_validate(task_data)

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        # Deliberately never calls generate() or any model API.
        answer = execute_mock(task, system_id, Path(inputs), Path(output))
        state.output = ModelOutput.from_content("mockllm/model", answer)
        state.completed = True
        return state

    return solve


def execute_inspect(
    task: TaskSpec,
    system_id: str,
    run_id: str,
    attempt_id: str,
    inputs: Path,
    output: Path,
    logs: Path,
    timeout: int,
) -> EvalLog:
    model = get_model("mockllm/model", memoize=False)
    task_definition = Task(
        name=f"testbed_{task.task_id}",
        version=task.revision,
        dataset=[Sample(id=run_id, input=task.instruction, metadata={"attempt_id": attempt_id})],
        solver=fixture_solver(task.model_dump(), system_id, str(inputs), str(output)),
        metadata={"run_id": run_id, "attempt_id": attempt_id, "system_id": system_id},
        time_limit=timeout,
    )
    results = eval(
        task_definition,
        model=model,
        score=False,
        log_dir=str(logs),
        display="none",
        log_format="eval",
        log_realtime=False,
        notification=False,
        ctl_server=False,
        acp_server=False,
        sentinel_proxy="off",
        retry_on_error=0,
        epochs=1,
        max_samples=1,
        max_tasks=1,
        fail_on_error=False,
        log_model_api=False,
    )
    if len(results) != 1:
        raise TestbedError("Inspect did not return exactly one attempt log")
    log = results[0]
    if log.stats.model_usage:
        raise TestbedError("Unexpected model usage in the no-model fixture")
    return log
