"""Prove bounded process-group cleanup and PID-reuse refusal without touching foreign work."""

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent_testbed.io import TestbedError
from agent_testbed.processes import group_members, process_identity, stop_group


def test_stops_children_after_worker_leader_exits(tmp_path: Path) -> None:
    child_file = tmp_path / "child.json"
    script = """
import json, pathlib, subprocess, sys, time
p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
pathlib.Path(sys.argv[1]).write_text(json.dumps({'child': p.pid}))
time.sleep(60)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(child_file)],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    identity = process_identity(process.pid)
    try:
        deadline = time.monotonic() + 10
        while not child_file.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        child = json.loads(child_file.read_text())["child"]
        assert child in group_members(identity)
        process.terminate()
        process.wait(timeout=10)
        assert child in group_members(identity)
        assert stop_group(identity, grace_seconds=0.3)
        assert group_members(identity) == []
    finally:
        stop_group(identity, grace_seconds=0.3)
        process.wait(timeout=10)


def test_closed_launch_gate_never_reads_or_runs_task(tmp_path: Path) -> None:
    missing = tmp_path / "never-created-request.json"
    process = subprocess.run(
        [sys.executable, "-m", "agent_testbed.worker", "--request", str(missing)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=15,
    )
    assert process.returncode == 0, process.stderr.decode()
    assert list(tmp_path.iterdir()) == []


def test_pid_reuse_identity_refuses_to_signal(tmp_path: Path) -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
    )
    identity = process_identity(process.pid)
    try:
        with pytest.raises(TestbedError, match="identity changed"):
            stop_group({**identity, "start_ticks": int(identity["start_ticks"]) - 1})
        assert process.poll() is None
    finally:
        stop_group(identity, grace_seconds=0.2)
        process.wait(timeout=10)
