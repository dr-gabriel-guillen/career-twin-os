"""Runtime network block for the test process.

Installed for the whole pytest session by ``tests/conftest.py``. Any attempt to
resolve or connect to a non-loopback address raises ``NetworkBlocked``, so an
"isolated" test that secretly needs the network (or a paid API) fails loudly
instead of passing on a developer laptop and hanging in CI.

Scope: this patches ``socket`` inside the current Python process. Subprocesses
are handled separately by ``runner.offline_env`` (scrubbed credentials + dead proxy).
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from typing import Any

LOOPBACK_NAMES = ("localhost", "localhost.localdomain", "ip6-localhost")


class NetworkBlocked(RuntimeError):
    """Raised when isolated code tries to reach the network."""


def _is_loopback(host: object) -> bool:
    if isinstance(host, bytes):
        host = host.decode("ascii", errors="replace")
    if not isinstance(host, str):
        return False
    if host.lower().rstrip(".") in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def _check(address: object) -> None:
    host = address[0] if isinstance(address, tuple) and address else address
    if isinstance(address, tuple) and not _is_loopback(host):
        raise NetworkBlocked(f"network access blocked in isolated tests: {host!r}")


def install() -> Callable[[], None]:
    """Patch ``socket``; returns a function that restores the originals."""
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_getaddrinfo = socket.getaddrinfo
    original_create = socket.create_connection

    def connect(self: socket.socket, address: Any) -> None:
        if self.family in (socket.AF_INET, socket.AF_INET6):
            _check(address)
        original_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> int:
        if self.family in (socket.AF_INET, socket.AF_INET6):
            _check(address)
        return original_connect_ex(self, address)

    def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        if host is not None and not _is_loopback(host):
            raise NetworkBlocked(f"DNS resolution blocked in isolated tests: {host!r}")
        return original_getaddrinfo(host, *args, **kwargs)

    def create_connection(address: Any, *args: Any, **kwargs: Any) -> socket.socket:
        _check(address)
        return original_create(address, *args, **kwargs)

    socket.socket.connect = connect  # type: ignore[method-assign, assignment]
    socket.socket.connect_ex = connect_ex  # type: ignore[method-assign, assignment]
    socket.getaddrinfo = getaddrinfo
    socket.create_connection = create_connection

    def restore() -> None:
        socket.socket.connect = original_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = original_connect_ex  # type: ignore[method-assign]
        socket.getaddrinfo = original_getaddrinfo
        socket.create_connection = original_create

    return restore
