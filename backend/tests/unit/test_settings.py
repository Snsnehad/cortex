"""Tests for app.config.settings (TASK-01.03)."""

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.config.settings import (
    DEV_DATABASE_PASSWORD,
    DEV_JWT_SECRET,
    REPO_ROOT,
    Environment,
    Settings,
    get_settings,
    iter_env_var_names,
)

ENV_EXAMPLE = REPO_ROOT / ".env.example"
EXAMPLE_VAR_PATTERN = re.compile(r"^#?\s*(CORTEX_[A-Z0-9_]+)=", re.MULTILINE)


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Tests must not see the developer's real CORTEX_* variables."""
    import os

    for key in list(os.environ):
        if key.upper().startswith("CORTEX_"):
            monkeypatch.delenv(key)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def make_settings(**overrides: Any) -> Settings:
    """Settings from defaults + overrides only (no .env file)."""
    return Settings(_env_file=None, **overrides)


def safe_production_overrides(**changes: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "environment": "production",
        "debug": False,
        "cors_origins": ["https://app.example.com"],
        "database": {"password": "a-strong-production-db-password"},
        "storage": {"backend": "s3", "s3_bucket": "cortex-prod-documents"},
        "auth": {"jwt_secret": "p" * 48, "refresh_cookie_secure": True},
    }
    config.update(changes)
    return config


# --- development defaults ----------------------------------------------------------------


def test_development_defaults_load_without_any_configuration() -> None:
    settings = make_settings()

    assert settings.environment is Environment.DEVELOPMENT
    assert settings.debug is False
    assert settings.storage.backend == "local"
    assert settings.database.url == "postgresql+psycopg://cortex:cortex@localhost:15432/cortex"


def test_environment_variables_configure_nested_groups(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORTEX_DATABASE__HOST", "db.internal")
    monkeypatch.setenv("CORTEX_RAG__FINAL_K", "4")
    monkeypatch.setenv("CORTEX_CORS_ORIGINS", '["http://localhost:3000"]')

    settings = make_settings()

    assert settings.database.host == "db.internal"
    assert settings.rag.final_k == 4
    assert settings.cors_origins == ["http://localhost:3000"]


def test_real_environment_variables_override_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("CORTEX_DATABASE__HOST=from-file\nCORTEX_LOG_LEVEL=DEBUG\n")
    monkeypatch.setenv("CORTEX_DATABASE__HOST", "from-environment")

    settings = Settings(_env_file=env_file)

    assert settings.database.host == "from-environment"
    assert settings.log_level == "DEBUG"


# --- typos fail loudly --------------------------------------------------------------------


def test_unknown_environment_variable_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORTEX_DATABSE__PASSWORD", "typo")

    with pytest.raises(ValidationError, match="CORTEX_DATABSE__PASSWORD"):
        make_settings()


def test_unknown_key_in_env_file_is_rejected(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("CORTEX_LOG_LEVL=DEBUG\n")

    with pytest.raises(ValidationError, match="log_levl"):
        Settings(_env_file=env_file)


# --- deployed environments ----------------------------------------------------------------


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_deployed_environment_accepts_safe_configuration(environment: str) -> None:
    settings = make_settings(**safe_production_overrides(environment=environment))

    assert settings.environment.is_deployed


@pytest.mark.parametrize(
    ("changes", "expected_problem"),
    [
        pytest.param(
            {"auth": {"jwt_secret": DEV_JWT_SECRET, "refresh_cookie_secure": True}},
            "auth.jwt_secret",
            id="default-jwt-secret",
        ),
        pytest.param(
            {"auth": {"jwt_secret": "too-short", "refresh_cookie_secure": True}},
            "auth.jwt_secret",
            id="short-jwt-secret",
        ),
        pytest.param(
            {"database": {"password": DEV_DATABASE_PASSWORD}},
            "database.password",
            id="default-db-password",
        ),
        pytest.param({"debug": True}, "debug must be false", id="debug-on"),
        pytest.param({"cors_origins": ["*"]}, "cors_origins", id="wildcard-cors"),
        pytest.param({"cors_origins": ["http://app.example.com"]}, "cors_origins", id="http-cors"),
        pytest.param({"cors_origins": []}, "cors_origins", id="no-cors-origins"),
        pytest.param({"storage": {"backend": "local"}}, "storage.backend", id="local-storage"),
        pytest.param(
            {"auth": {"jwt_secret": "p" * 48, "refresh_cookie_secure": False}},
            "refresh_cookie_secure",
            id="insecure-cookie",
        ),
    ],
)
def test_production_refuses_unsafe_configuration(
    changes: dict[str, Any], expected_problem: str
) -> None:
    with pytest.raises(ValidationError, match=re.escape(expected_problem)):
        make_settings(**safe_production_overrides(**changes))


def test_production_with_only_defaults_reports_every_problem_at_once() -> None:
    with pytest.raises(ValidationError) as exc_info:
        make_settings(environment="production")

    message = str(exc_info.value)
    for problem in ("auth.jwt_secret", "database.password", "storage.backend", "cookie"):
        assert problem in message


@pytest.mark.parametrize("environment", ["development", "test"])
def test_non_deployed_environments_allow_development_defaults(environment: str) -> None:
    settings = make_settings(environment=environment, debug=True)

    assert not settings.environment.is_deployed


# --- secrets ------------------------------------------------------------------------------


def test_secrets_are_masked_in_repr_and_serialisation() -> None:
    settings = make_settings(
        database={"password": "db-pass-visible?"},
        llm={"api_key": "sk-visible-key"},
    )

    rendered = repr(settings) + str(settings) + settings.model_dump_json()

    for secret in ("db-pass-visible?", "sk-visible-key", DEV_JWT_SECRET):
        assert secret not in rendered


def test_database_url_escapes_special_characters_in_credentials() -> None:
    settings = make_settings(database={"user": "app@cortex", "password": "p@ss/w:rd#1"})

    assert settings.database.url.startswith(
        "postgresql+psycopg://app%40cortex:p%40ss%2Fw%3Ard%231@"
    )


# --- group-level validation ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("rag", "expected_problem"),
    [
        ({"chunk_size_tokens": 256, "chunk_overlap_tokens": 256}, "chunk_overlap_tokens"),
        ({"dense_weight": 0, "sparse_weight": 0}, "cannot both be 0"),
        ({"final_k": 40, "fused_k": 30}, "final_k cannot exceed"),
    ],
)
def test_inconsistent_rag_settings_are_rejected(rag: dict[str, Any], expected_problem: str) -> None:
    with pytest.raises(ValidationError, match=expected_problem):
        make_settings(rag=rag)


def test_s3_storage_requires_a_bucket() -> None:
    with pytest.raises(ValidationError, match="s3_bucket is required"):
        make_settings(storage={"backend": "s3"})


# --- .env.example stays in sync -----------------------------------------------------------


def test_env_example_documents_every_setting_and_nothing_else() -> None:
    documented = set(EXAMPLE_VAR_PATTERN.findall(ENV_EXAMPLE.read_text(encoding="utf-8")))
    known = set(iter_env_var_names())

    assert known - documented == set(), "settings missing from .env.example"
    assert documented - known == set(), ".env.example lists variables Settings does not know"


def test_env_example_is_a_valid_development_configuration() -> None:
    from_example = Settings(_env_file=ENV_EXAMPLE)

    assert from_example.model_dump() == make_settings().model_dump()


# --- accessor -----------------------------------------------------------------------------


def test_get_settings_is_cached_per_process() -> None:
    assert get_settings() is get_settings()
