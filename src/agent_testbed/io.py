"""Small, bounded filesystem primitives; paths from task data are never shell code."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

MAX_FILE_BYTES = 20 * 1024 * 1024


class TestbedError(Exception):
    """An actionable configuration, integrity, or runtime error."""

    __test__ = False


def now() -> str:
    return datetime.now(UTC).isoformat()


def identifier(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def read_bytes(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise TestbedError(f"Not a regular non-symlink file: {path}")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise TestbedError(f"File exceeds {MAX_FILE_BYTES} bytes: {path}")
    data = path.read_bytes()
    if len(data) > MAX_FILE_BYTES:
        raise TestbedError(f"File grew beyond size limit: {path}")
    return data


def read_json(path: Path) -> Any:
    return json.loads(read_bytes(path))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(read_bytes(path)).hexdigest()


def contained_path(root: Path, relative: str) -> Path:
    """Accept portable, strictly relative paths without symlink components."""
    path = PurePosixPath(relative)
    if (
        not relative
        or "\\" in relative
        or ":" in relative
        or path.is_absolute()
        or any(p in {"", ".", ".."} for p in relative.split("/"))
    ):
        raise TestbedError(f"Unsafe relative path: {relative!r}")
    root = root.resolve()
    candidate = root
    for part in path.parts:
        candidate /= part
        if candidate.is_symlink():
            raise TestbedError(f"Symlink is not allowed: {candidate}")
    if not candidate.resolve().is_relative_to(root):
        raise TestbedError(f"Path escapes root: {relative}")
    return candidate


def write_new(path: Path, data: bytes) -> None:
    """Never replace an existing artifact or audit record."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)


def write_json_new(path: Path, data: Any) -> None:
    write_new(path, canonical(data) + b"\n")
