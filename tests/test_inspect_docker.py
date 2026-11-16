"""Opt-in SP02: export a sandbox artifact before Inspect removes its container."""

import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from inspect_ai import Task, eval
from inspect_ai.dataset import Sample
from inspect_ai.model import ModelOutput, get_model
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util import sandbox


@solver
def sandbox_fixture() -> Solver:
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        await sandbox().write_file("/tmp/artifact.txt", "snapshot-before-cleanup\n")
        result = await sandbox().exec(["sh", "-c", "test -f /tmp/artifact.txt"])
        assert result.success
        state.output = ModelOutput.from_content("mockllm/model", "created")
        state.completed = True
        return state

    return solve


@pytest.mark.requires_docker
@pytest.mark.skipif(os.environ.get("TESTBED_DOCKER_PROBE") != "1", reason="Docker probe is opt-in")
def test_export_before_sandbox_cleanup(tmp_path: Path) -> None:
    image = os.environ.get("TESTBED_DOCKER_IMAGE", "python:3.12-slim-bookworm")
    info = subprocess.run(
        ["docker", "image", "inspect", image], check=True, text=True, capture_output=True
    )
    image_id = json.loads(info.stdout)[0]["Id"]
    owner = "testbed-sp02-" + uuid4().hex
    compose = tmp_path / "compose.yaml"
    compose.write_text(
        yaml.safe_dump(
            {
                "services": {
                    "default": {
                        "image": image_id,
                        "x-local": True,
                        "pull_policy": "never",
                        "init": True,
                        "command": ["sleep", "infinity"],
                        "network_mode": "none",
                        "cpus": 1.0,
                        "mem_limit": "256m",
                        "labels": {"agent-testbed.probe-owner": owner},
                    }
                }
            }
        )
    )
    exported = tmp_path / "exported.txt"

    async def collect_before_cleanup(state: TaskState) -> None:
        exported.write_text(await sandbox().read_file("/tmp/artifact.txt"))

    logs = eval(
        Task(
            dataset=[Sample(input="create artifact")],
            solver=sandbox_fixture(),
            cleanup=collect_before_cleanup,
            sandbox=("docker", str(compose)),
            time_limit=30,
        ),
        model=get_model("mockllm/model"),
        score=False,
        sandbox_prebuilt=True,
        log_dir=str(tmp_path / "logs"),
        display="none",
        ctl_server=False,
        acp_server=False,
        notification=False,
        sentinel_proxy="off",
        log_realtime=False,
    )
    remaining = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"label=agent-testbed.probe-owner={owner}"],
        check=True,
        text=True,
        capture_output=True,
    )
    assert logs[0].status == "success", logs[0].error
    assert exported.read_text() == "snapshot-before-cleanup\n"
    assert not logs[0].stats.model_usage
    assert not remaining.stdout.strip(), "Probe-owned container was not removed"
