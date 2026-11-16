import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from agent_testbed.cli import app
from agent_testbed.config import load_experiment, load_yaml
from agent_testbed.contracts import ExperimentConfig, TaskSpec
from agent_testbed.io import TestbedError, contained_path, write_new


def test_config_and_stable_matrix(config_path: Path) -> None:
    loaded = load_experiment(config_path)
    first = loaded.matrix("experiment-a")
    assert first == loaded.matrix("experiment-a")
    assert len(first) == len({run.run_id for run in first}) == 4
    assert first != loaded.matrix("experiment-b")
    assert loaded.summary()["model_token_budget"] == 0


@pytest.mark.parametrize("field,value", [("model_profile", "openai/model"), ("backend", "other")])
def test_reject_real_or_unknown_configuration(config_path: Path, field: str, value: str) -> None:
    data = load_yaml(config_path)
    data[field] = value
    with pytest.raises(ValidationError):
        ExperimentConfig.model_validate(data)


@pytest.mark.parametrize(
    "change", ["mode", "budget", "parallel", "retry", "duplicate", "unknown", "coerced_repeat"]
)
def test_fail_closed_configuration(config_path: Path, change: str) -> None:
    data = load_yaml(config_path)
    if change == "mode":
        data["execution"]["mode"] = "real"
    elif change == "budget":
        data["limits"]["real_model_requests"] = 1
    elif change == "parallel":
        data["execution"]["max_concurrent_runs"] = 2
    elif change == "retry":
        data["execution"]["infrastructure_retries"] = 1
    elif change == "duplicate":
        data["systems"].append(data["systems"][0])
    elif change == "unknown":
        data["secret_option"] = True
    else:
        data["repeats"] = "3"
    with pytest.raises(ValidationError):
        ExperimentConfig.model_validate(data)


def test_public_contract_rejects_private_answers(config_path: Path) -> None:
    task = load_experiment(config_path).tasks[0].spec
    assert TaskSpec.model_validate_json(task.model_dump_json()) == task
    data = copy.deepcopy(task.model_dump())
    data["expected_answer"] = "PRIVATE_CANARY"
    with pytest.raises(ValidationError):
        TaskSpec.model_validate(data)


def test_duplicate_yaml_keys_rejected(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.yaml"
    path.write_text("repeats: 1\nrepeats: 999\n")
    with pytest.raises(TestbedError, match="Duplicate"):
        load_yaml(path)


@pytest.mark.parametrize(
    "path", ["../outside", "/etc/passwd", "a/../b", "a\\b", "C:/secret", "a//b"]
)
def test_reject_unsafe_paths(tmp_path: Path, path: str) -> None:
    with pytest.raises(TestbedError):
        contained_path(tmp_path, path)


def test_reject_symlink_and_overwrite(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real", target_is_directory=True)
    with pytest.raises(TestbedError, match="Symlink"):
        contained_path(tmp_path, "link/a")
    path = tmp_path / "record.json"
    write_new(path, b"old")
    with pytest.raises(FileExistsError):
        write_new(path, b"new")
    assert path.read_bytes() == b"old"


def test_validate_dry_run_do_not_execute(
    config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("dry-run tried to execute")

    monkeypatch.setattr("agent_testbed.backend.execute_inspect", forbidden)
    runner = CliRunner()
    validated = runner.invoke(app, ["validate", str(config_path)])
    assert validated.exit_code == 0, validated.output
    result = runner.invoke(app, ["dry-run", str(config_path)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["planned_runs"] == 4
