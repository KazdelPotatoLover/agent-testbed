"""Content-hashed snapshots. Immutability is detected, not enforced by OS permissions."""

from pathlib import Path

from agent_testbed.contracts import Artifact, Snapshot, TaskSpec
from agent_testbed.io import (
    TestbedError,
    contained_path,
    identifier,
    read_bytes,
    read_json,
    sha256_file,
    write_json_new,
    write_new,
)


def stage_inputs(task: TaskSpec, source: Path, destination: Path) -> None:
    for asset in task.inputs:
        path = contained_path(source, asset.path)
        if sha256_file(path) != asset.sha256:
            raise TestbedError(f"Input changed since validation: {asset.path}")
        write_new(contained_path(destination, asset.path), read_bytes(path))


def freeze_outputs(
    task: TaskSpec, output: Path, target: Path, run_id: str, attempt_id: str
) -> Snapshot:
    artifacts = []
    for spec in task.outputs:
        source = contained_path(output, spec.path)
        if not source.exists():
            continue
        data = read_bytes(source)
        destination = contained_path(target / "artifacts", spec.path)
        write_new(destination, data)
        artifacts.append(Artifact(path=spec.path, sha256=sha256_file(destination), size=len(data)))
    snapshot = Snapshot(
        snapshot_id=identifier("snapshot"),
        run_id=run_id,
        attempt_id=attempt_id,
        artifacts=artifacts,
    )
    write_json_new(target / "manifest.json", snapshot.model_dump())
    return snapshot


def verify_snapshot(path: Path, expected_hash: str) -> Snapshot:
    if sha256_file(path) != expected_hash:
        raise TestbedError("Snapshot manifest has changed")
    snapshot = Snapshot.model_validate(read_json(path))
    if len({a.path for a in snapshot.artifacts}) != len(snapshot.artifacts):
        raise TestbedError("Snapshot contains duplicate artifact paths")
    for artifact in snapshot.artifacts:
        target = contained_path(path.parent / "artifacts", artifact.path)
        if sha256_file(target) != artifact.sha256 or target.stat().st_size != artifact.size:
            raise TestbedError(f"Snapshot artifact has changed: {artifact.path}")
    return snapshot
