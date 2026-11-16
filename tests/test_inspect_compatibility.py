"""Compatibility probes for Inspect 0.3.290, using no real model provider."""

import asyncio
from pathlib import Path

import pytest
from inspect_ai import Task, eval, score
from inspect_ai.dataset import Sample
from inspect_ai.log import read_eval_log, write_eval_log
from inspect_ai.model import ModelOutput, get_model
from inspect_ai.scorer import CORRECT, Score, Scorer, Target, accuracy, scorer
from inspect_ai.solver import Generate, Solver, TaskState, solver

from agent_testbed.io import sha256_file
from agent_testbed.offline import offline_execution


@solver
def local_answer() -> Solver:
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        state.output = ModelOutput.from_content("mockllm/model", "local-only")
        state.completed = True
        return state

    return solve


@scorer(metrics=[accuracy()])
def exact_local_answer() -> Scorer:
    async def grade(state: TaskState, target: Target) -> Score:
        return Score(value=CORRECT if state.output.completion == "local-only" else "I")

    return grade


@solver
def wait_forever() -> Solver:
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        await asyncio.sleep(10)
        return state

    return solve


def test_native_offline_rescore_preserves_original(tmp_path: Path) -> None:
    with offline_execution():
        logs = eval(
            Task(dataset=[Sample(input="fixture")], solver=local_answer()),
            model=get_model("mockllm/model"),
            score=False,
            log_dir=str(tmp_path),
            display="none",
            ctl_server=False,
            acp_server=False,
            notification=False,
            sentinel_proxy="off",
            log_realtime=False,
        )
        original = Path(logs[0].location)
        original_hash = sha256_file(original)
        rescored = score(
            read_eval_log(original),
            exact_local_answer(),
            model=get_model("mockllm/model"),
            display="none",
        )
        destination = tmp_path / "rescored.eval"
        write_eval_log(rescored, destination)
    assert sha256_file(original) == original_hash
    saved = read_eval_log(destination)
    assert list(saved.samples[0].scores.values())[0].value == CORRECT
    assert saved.stats.model_usage == {}


def test_timeout_still_runs_cleanup(tmp_path: Path) -> None:
    cleaned = []

    async def cleanup(state: TaskState) -> None:
        cleaned.append(state.sample_id)

    with offline_execution():
        logs = eval(
            Task(
                dataset=[Sample(id="timeout", input="wait")],
                solver=wait_forever(),
                cleanup=cleanup,
                time_limit=1,
            ),
            model=get_model("mockllm/model"),
            score=False,
            log_dir=str(tmp_path),
            display="none",
            ctl_server=False,
            acp_server=False,
            notification=False,
            sentinel_proxy="off",
            log_realtime=False,
        )
    assert cleaned == ["timeout"]
    assert logs[0].samples[0].limit.type == "time"
    assert not logs[0].stats.model_usage


def test_mock_guard_rejects_network() -> None:
    import socket

    from agent_testbed.io import TestbedError

    with offline_execution(), pytest.raises(TestbedError, match="forbidden"):
        socket.getaddrinfo("example.com", 443)
