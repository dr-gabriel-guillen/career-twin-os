"""Session-wide isolation: every test runs with the network blocked."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from ctint import netguard


@pytest.fixture(scope="session", autouse=True)
def _network_blocked() -> Iterator[None]:
    restore = netguard.install()
    yield
    restore()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    skip = pytest.mark.skip(
        reason="NETWORK: never run in isolated mode (and no such test may call a paid API)"
    )
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip)
