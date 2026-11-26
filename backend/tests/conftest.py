"""Shared pytest configuration (test harness overview: docs/testing/README.md).

* Async tests use AnyIO's pytest plugin (shipped with anyio, a FastAPI dependency): mark them
  ``@pytest.mark.anyio``.
* Every test under ``tests/integration/`` is marked ``integration`` automatically, so
  ``pytest -m "not integration"`` reliably runs without Docker.
* pytest-randomly shuffles test order on every run (seed printed in the header; reproduce with
  ``-p randomly -p "randomly_seed=<seed>"`` or ``-p no:randomly`` to disable).
"""

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest

INTEGRATION_DIR = Path(__file__).parent / "integration"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        if INTEGRATION_DIR in Path(str(item.path)).parents:
            item.add_marker(pytest.mark.integration)


@pytest.fixture
def anyio_backend() -> tuple[str, dict[str, Any]]:
    # psycopg's async mode cannot run on Windows' default ProactorEventLoop; Linux (Docker,
    # CI) is unaffected. Using the selector loop on Windows keeps local runs identical.
    # (os.name rather than sys.platform: mypy evaluates sys.platform statically and would
    # flag the other branch as unreachable.)
    if os.name == "nt":
        return "asyncio", {"loop_factory": asyncio.SelectorEventLoop}
    return "asyncio", {}
