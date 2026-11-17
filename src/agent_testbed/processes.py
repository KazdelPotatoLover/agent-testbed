"""Linux process supervision for trusted local workers, not a security sandbox."""

from __future__ import annotations

import fcntl
import os
import signal
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from agent_testbed.io import TestbedError


class ControllerBusy(TestbedError):
    pass


@contextmanager
def controller_lock(root: Path) -> Iterator[None]:
    """Crash-released, non-inherited lock: exactly one driver per experiment."""
    with (root / "controller.lock").open("a+b") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ControllerBusy("another controller is active for this experiment") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _stat(pid: int) -> tuple[str, int, int, int] | None:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return fields[0], int(fields[2]), int(fields[3]), int(fields[19])
    except FileNotFoundError:
        return None


def process_identity(pid: int) -> dict[str, int | str]:
    stat = _stat(pid)
    if stat is None or stat[0] == "Z" or stat[1] != pid or stat[2] != pid:
        raise TestbedError("worker must be a live process-group/session leader")
    return {
        "pid": pid,
        "start_ticks": stat[3],
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
    }


def group_members(identity: dict[str, int | str]) -> list[int]:
    """Reject PID reuse before inspecting members of the recorded session."""
    if Path("/proc/sys/kernel/random/boot_id").read_text().strip() != identity["boot_id"]:
        return []
    pid, start = int(identity["pid"]), int(identity["start_ticks"])
    leader = _stat(pid)
    if leader is not None and leader[3] != start:
        raise TestbedError("worker PID identity changed; refusing to signal")
    members = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        stat = _stat(int(entry.name))
        if stat and stat[0] != "Z" and stat[1] == pid:
            if stat[2] != pid or stat[3] < start:
                raise TestbedError("worker process-group identity cannot be confirmed")
            members.append(int(entry.name))
    return members


def stop_group(identity: dict[str, int | str], grace_seconds: float = 2.0) -> bool:
    """Terminate the proven session, including children after the leader exits."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if not group_members(identity):
            return True
        try:
            os.killpg(int(identity["pid"]), sig)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + grace_seconds
        while time.monotonic() < deadline:
            if not group_members(identity):
                return True
            time.sleep(0.02)
    return not group_members(identity)
