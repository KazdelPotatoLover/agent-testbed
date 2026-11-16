"""Offline-first CLI. Real providers and remote systems are intentionally unsupported."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import ValidationError

from agent_testbed.config import load_experiment
from agent_testbed.io import TestbedError

app = typer.Typer(no_args_is_help=True, help="Independent Agent evaluations; M1 is mock-only.")
ConfigPath = Annotated[Path, typer.Argument(exists=True, file_okay=True, dir_okay=False)]
ExperimentPath = Annotated[Path, typer.Argument(exists=True, file_okay=False, dir_okay=True)]


def _perform(action: Callable[[], Any]) -> None:
    try:
        result = action()
    except (TestbedError, ValidationError, OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@app.command()
def validate(config: ConfigPath) -> None:
    """Validate local configuration, hashes, schemas and grading coverage; no model calls."""
    _perform(lambda: {"valid": True, **load_experiment(config).summary()})


@app.command("dry-run")
def dry_run(config: ConfigPath) -> None:
    """Expand the matrix without starting Inspect or creating experiment output."""

    def action() -> dict[str, Any]:
        loaded = load_experiment(config)
        return {
            **loaded.summary(),
            "runs": [run.model_dump() for run in loaded.matrix("dry-run")],
        }

    _perform(action)


@app.command()
def run(
    config: ConfigPath,
    output: Annotated[Path, typer.Option(help="Root directory for new experiment IDs")] = Path(
        "output"
    ),
    no_score: Annotated[bool, typer.Option(help="Defer independent grading")] = False,
) -> None:
    """Run deterministic mocks through Inspect, then write scores and a report."""
    from agent_testbed.controller import run_experiment
    from agent_testbed.reports import report_experiment

    def action() -> dict[str, str]:
        root = run_experiment(config, output, score=not no_score)
        report = report_experiment(root)
        return {"experiment": str(root), "report": str(report / "summary.md")}

    _perform(action)


@app.command()
def status(experiment: ExperimentPath) -> None:
    """Inspect recorded execution completion; M1 has no resume support."""
    from agent_testbed.controller import experiment_status

    _perform(lambda: experiment_status(experiment))


@app.command()
def score(
    experiment: ExperimentPath,
    grading: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
) -> None:
    """Append a new deterministic scoring batch without running the Agent."""
    from agent_testbed.grading import score_experiment

    _perform(
        lambda: {"score_batch": str(score_experiment(experiment.resolve(), grading.resolve()))}
    )


@app.command()
def report(
    experiment: ExperimentPath,
    score_batch: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
) -> None:
    """Create a new report that pins a scoring batch (latest batch by default)."""
    from agent_testbed.reports import report_experiment

    _perform(lambda: {"report": str(report_experiment(experiment, score_batch) / "summary.md")})


if __name__ == "__main__":
    app()
