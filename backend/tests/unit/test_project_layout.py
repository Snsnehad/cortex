"""Guards the backend's structural conventions (TASK-01.02).

These tests keep the agreed layering from ARCHITECTURE.md from eroding silently:
the interpreter version is the pinned one, every architectural package exists and
documents its responsibility, and no directory under ``app/`` is an accidental
namespace package (a missing ``__init__.py`` makes mypy and pytest resolve modules
inconsistently).
"""

import importlib
import sys
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[2] / "app"

EXPECTED_PACKAGES = [
    "app",
    "app.api",
    "app.api.v1",
    "app.core",
    "app.config",
    "app.database",
    "app.models",
    "app.schemas",
    "app.repositories",
    "app.services",
    "app.middleware",
    "app.dependencies",
    "app.workers",
    "app.storage",
    "app.rag",
    "app.rag.ingestion",
    "app.rag.chunking",
    "app.rag.embeddings",
    "app.rag.retrieval",
    "app.rag.reranking",
    "app.rag.generation",
    "app.rag.evaluation",
    "app.utils",
]


def test_runs_on_pinned_python_version() -> None:
    assert sys.version_info[:2] == (3, 12)


@pytest.mark.parametrize("name", EXPECTED_PACKAGES)
def test_architectural_package_exists_and_is_documented(name: str) -> None:
    module = importlib.import_module(name)

    assert module.__file__ is not None, f"{name} is a namespace package (no __init__.py)"
    assert module.__doc__, f"{name} must state its responsibility in a docstring"


def test_every_app_directory_is_a_regular_package() -> None:
    missing = [
        str(directory.relative_to(APP_DIR.parent))
        for directory in [APP_DIR, *APP_DIR.rglob("*")]
        if directory.is_dir()
        and directory.name != "__pycache__"
        and not (directory / "__init__.py").exists()
    ]

    assert missing == [], f"directories without __init__.py: {missing}"
