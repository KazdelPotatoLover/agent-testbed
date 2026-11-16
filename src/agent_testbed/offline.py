"""No-network guard for trusted mock code; not a sandbox for arbitrary agents."""

import socket
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

from agent_testbed.io import TestbedError


@contextmanager
def offline_execution() -> Iterator[None]:
    original = socket.socket.connect
    original_ex = socket.socket.connect_ex

    def connect(sock: socket.socket, address: Any) -> None:
        if sock.family != socket.AF_UNIX:
            raise TestbedError("Network access is forbidden in mock execution")
        original(sock, address)

    def connect_ex(sock: socket.socket, address: Any) -> int:
        if sock.family != socket.AF_UNIX:
            raise TestbedError("Network access is forbidden in mock execution")
        return original_ex(sock, address)

    def deny(*args: Any, **kwargs: Any) -> Any:
        raise TestbedError("Network access is forbidden in mock execution")

    with (
        patch.object(socket.socket, "connect", connect),
        patch.object(socket.socket, "connect_ex", connect_ex),
        patch.object(socket.socket, "sendto", deny),
        patch.object(socket, "getaddrinfo", deny),
    ):
        yield
