"""Shared pytest configuration.

Async tests use AnyIO's pytest plugin (shipped with anyio, a FastAPI dependency), so no extra
async test plugin is needed. Mark async tests with ``@pytest.mark.anyio``.
"""

import asyncio
import os
from typing import Any

import pytest


@pytest.fixture
def anyio_backend() -> tuple[str, dict[str, Any]]:
    # psycopg's async mode cannot run on Windows' default ProactorEventLoop; Linux (Docker,
    # CI) is unaffected. Using the selector loop on Windows keeps local runs identical.
    # (os.name rather than sys.platform: mypy evaluates sys.platform statically and would
    # flag the other branch as unreachable.)
    if os.name == "nt":
        return "asyncio", {"loop_factory": asyncio.SelectorEventLoop}
    return "asyncio", {}
