"""Offline configuration resolution and content-addressed experiment planning."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from agent_testbed.contracts import (
    ExperimentConfig,
    GradingSpec,
    RunSpec,
    SuiteSpec,
    TaskSpec,
)
from agent_testbed.io import TestbedError, contained_path, digest, read_bytes, sha256_file


class UniqueKeyLoader(yaml.SafeLoader):
    """Reject duplicate keys instead of silently weakening a limit."""


def _mapping(loader: UniqueKeyLoader, node: yaml.MappingNode) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in result:
            raise TestbedError(f"Duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def load_yaml(path: Path) -> Any:
    try:
        return yaml.load(read_bytes(path), Loader=UniqueKeyLoader)
    except yaml.YAMLError as exc:
        raise TestbedError(f"Invalid YAML: {path}: {exc}") from exc


def _local_schema(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"$ref", "$dynamicRef"} and (
                not isinstance(child, str) or not child.startswith("#")
            ):
                raise TestbedError("Output schemas may only use document-local references")
            _local_schema(child)
    elif isinstance(value, list):
        for child in value:
            _local_schema(child)


@dataclass(frozen=True)
class LoadedTask:
    spec: TaskSpec
    root: Path


@dataclass(frozen=True)
class LoadedExperiment:
    config: ExperimentConfig
    suite: SuiteSpec
    tasks: tuple[LoadedTask, ...]
    grading: GradingSpec
    grading_root: Path
    fingerprint: str

    def matrix(self, experiment_id: str) -> list[RunSpec]:
        return [
            RunSpec(
                run_id="run-" + digest([experiment_id, task.spec.task_id, system, repeat])[:24],
                task_id=task.spec.task_id,
                task_revision=task.spec.revision,
                system_id=system,
                repeat_index=repeat,
            )
            for task in self.tasks
            for system in self.config.systems
            for repeat in range(1, self.config.repeats + 1)
        ]

    def summary(self) -> dict[str, Any]:
        return {
            "name": self.config.name,
            "config_hash": self.fingerprint,
            "suite": self.suite.suite_id,
            "tasks": [task.spec.task_id for task in self.tasks],
            "systems": self.config.systems,
            "repeats": self.config.repeats,
            "planned_runs": len(self.matrix("dry-run")),
            "real_model_requests": 0,
            "model_token_budget": 0,
            "mode": "mock-only",
        }


def load_experiment(path: Path) -> LoadedExperiment:
    path = path.resolve()
    config = ExperimentConfig.model_validate(load_yaml(path))
    suite_path = (path.parent / config.suite).resolve()
    suite = SuiteSpec.model_validate(load_yaml(suite_path))
    tasks = []
    for relative in suite.tasks:
        task_path = contained_path(suite_path.parent, relative)
        task = TaskSpec.model_validate(load_yaml(task_path))
        for asset in task.inputs:
            asset_path = contained_path(task_path.parent, asset.path)
            if sha256_file(asset_path) != asset.sha256:
                raise TestbedError(f"Input hash mismatch: {task.task_id}/{asset.path}")
        for output in task.outputs:
            contained_path(task_path.parent, output.path)
            if output.json_schema is not None:
                _local_schema(output.json_schema)
                Draft202012Validator.check_schema(output.json_schema)
        tasks.append(LoadedTask(task, task_path.parent))
    if len({t.spec.task_id for t in tasks}) != len(tasks):
        raise TestbedError("Task IDs must be unique within a suite")
    grading_path = (path.parent / config.grading).resolve()
    grading = load_grading(grading_path)
    expected_outputs = {(t.spec.task_id, o.path) for t in tasks for o in t.spec.outputs}
    actual_outputs = {(rule.task_id, rule.output_path) for rule in grading.rules}
    if expected_outputs != actual_outputs or len(actual_outputs) != len(grading.rules):
        raise TestbedError("Grading rules must cover every declared output exactly once")
    by_id = {task.spec.task_id: task.spec for task in tasks}
    for rule in grading.rules:
        task = by_id[rule.task_id]
        if rule.task_revision != task.revision:
            raise TestbedError(f"Grader task revision mismatch: {rule.task_id}")
        output = next(output for output in task.outputs if output.path == rule.output_path)
        required_kind = "exact_json" if output.kind == "json" else "csv_rows"
        if rule.kind != required_kind:
            raise TestbedError(f"Grader/output kind mismatch: {rule.task_id}")
    content = {
        "config": config.model_dump(),
        "suite": suite.model_dump(),
        "tasks": [task.spec.model_dump() for task in tasks],
        "grading": grading.model_dump(),
    }
    return LoadedExperiment(
        config, suite, tuple(tasks), grading, grading_path.parent, digest(content)
    )


def load_grading(path: Path) -> GradingSpec:
    grading = GradingSpec.model_validate(load_yaml(path))
    for rule in grading.rules:
        expected = contained_path(path.parent, rule.expected.path)
        if sha256_file(expected) != rule.expected.sha256:
            raise TestbedError(f"Grading asset hash mismatch: {rule.expected.path}")
        if rule.kind == "exact_json":
            json.loads(read_bytes(expected))
    return grading
