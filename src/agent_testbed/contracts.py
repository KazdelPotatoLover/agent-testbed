"""Versioned, strict data contracts. Public tasks never contain grading assets."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    schema_version: Literal["1"] = "1"


class Asset(Contract):
    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class OutputSpec(Contract):
    path: str
    kind: Literal["json", "csv"]
    json_schema: dict[str, Any] | None = None
    columns: list[str] | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "OutputSpec":
        if self.kind == "json" and (self.json_schema is None or self.columns is not None):
            raise ValueError("JSON outputs require json_schema, not columns")
        if self.kind == "csv" and (not self.columns or self.json_schema is not None):
            raise ValueError("CSV outputs require columns, not json_schema")
        if self.columns and len(set(self.columns)) != len(self.columns):
            raise ValueError("CSV column names must be unique")
        return self


class TaskSpec(Contract):
    task_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    revision: str = Field(min_length=1)
    instruction: str = Field(min_length=1)
    inputs: list[Asset] = Field(min_length=1)
    outputs: list[OutputSpec] = Field(min_length=1)
    fixture_driver: Literal["extract_contract", "reconcile_orders"]

    @model_validator(mode="after")
    def unique_paths(self) -> "TaskSpec":
        for paths in ([a.path for a in self.inputs], [o.path for o in self.outputs]):
            if len(paths) != len(set(paths)):
                raise ValueError("Input and output paths must be unique")
        return self


class SuiteSpec(Contract):
    suite_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    revision: str = Field(min_length=1)
    tasks: list[str] = Field(min_length=1)


class ExecutionConfig(Contract):
    mode: Literal["mock"] = "mock"
    max_concurrent_runs: Literal[1] = 1
    infrastructure_retries: Literal[0] = 0
    wall_time_seconds: int = Field(default=30, ge=1, le=3600)


class Limits(Contract):
    real_model_requests: Literal[0] = 0
    experiment_total_tokens: Literal[0] = 0


class ExperimentConfig(Contract):
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    suite: str
    grading: str
    systems: list[Literal["mock-pass", "mock-wrong-answer"]] = Field(min_length=1)
    repeats: int = Field(default=1, ge=1, le=100)
    backend: Literal["inspect"] = "inspect"
    model_profile: None = None
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    limits: Limits = Field(default_factory=Limits)

    @field_validator("systems")
    @classmethod
    def unique_systems(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("System IDs must be unique")
        return values


class GradingRule(Contract):
    task_id: str
    task_revision: str
    output_path: str
    kind: Literal["exact_json", "csv_rows"]
    expected: Asset


class GradingSpec(Contract):
    grader_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    revision: str = Field(min_length=1)
    rules: list[GradingRule] = Field(min_length=1)


class RunSpec(Contract):
    run_id: str
    task_id: str
    task_revision: str
    system_id: Literal["mock-pass", "mock-wrong-answer"]
    repeat_index: int = Field(ge=1)


class Usage(Contract):
    source: Literal["no-model-mock"] = "no-model-mock"
    complete: Literal[True] = True
    real_model_requests: Literal[0] = 0
    input_tokens: Literal[0] = 0
    output_tokens: Literal[0] = 0
    model_cost: Literal[0] = 0
    # Zero model cost is established by no model calls, not by a price estimate.


class Artifact(Contract):
    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size: int = Field(ge=0)


class Snapshot(Contract):
    snapshot_id: str
    run_id: str
    attempt_id: str
    artifacts: list[Artifact]


class RunResult(Contract):
    run: RunSpec
    attempt_id: str
    execution_status: Literal["finished", "agent_error", "infra_error", "timed_out"]
    self_reported_success: bool | None
    started_at: str
    finished_at: str
    elapsed_seconds: float = Field(ge=0)
    inspect_log: str | None
    snapshot: str
    usage: Usage | None
    cleanup_status: Literal["not_applicable"] = "not_applicable"
    error: str | None = None


class RuleResult(Contract):
    rule_id: str
    output_path: str
    passed: bool
    explanation: str


class ScoreResult(Contract):
    score_id: str
    run_id: str
    snapshot_id: str
    grader_id: str
    grader_revision: str
    grading_hash: str
    created_at: str
    status: Literal["passed", "failed", "grader_error"]
    rules: list[RuleResult]
    error: str | None = None
