"""SP06: a killed worker leaves a detectable incomplete experiment, never auto-restarted."""

import selectors
import subprocess
import sys
from pathlib import Path

from agent_testbed.controller import experiment_status
from agent_testbed.io import read_json


def test_worker_termination_preserves_plan_without_restarting(
    config_path: Path, tmp_path: Path
) -> None:
    script = """
import sys, time
from pathlib import Path
import agent_testbed.backend as backend
from agent_testbed.controller import run_experiment

def interrupted_fixture(*args, **kwargs):
    print('TESTBED_READY', flush=True)
    time.sleep(60)

backend.execute_mock = interrupted_fixture
run_experiment(Path(sys.argv[1]), Path(sys.argv[2]))
"""
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", script, str(config_path), str(tmp_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=15), "Worker did not reach the Inspect solver"
            assert process.stdout.readline().strip() == "TESTBED_READY"
        process.terminate()
        process.communicate(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)
    assert process.returncode != 0
    roots = list(tmp_path.glob("exp-*"))
    assert len(roots) == 1
    root = roots[0]
    assert len(read_json(root / "plan.json")["runs"]) == 4
    before = {str(p) for p in root.rglob("*")}
    status = experiment_status(root)
    assert status["state"] == "incomplete"
    assert before == {str(p) for p in root.rglob("*")}
    assert not (root / "experiment.json").exists()
    assert len(list(root.glob("runs/*/attempts/*"))) == 1
