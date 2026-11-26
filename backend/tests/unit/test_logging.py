"""Structured logging and redaction (TASK-01.07)."""

import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest
import structlog

from app.config import Settings
from app.core.logging import (
    REDACTED,
    configure_logging,
    get_logger,
    is_sensitive_key,
    scrub_text,
)


@pytest.fixture(autouse=True)
def _clean_context() -> Iterator[None]:
    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()


def configure(**overrides: Any) -> None:
    configure_logging(Settings(_env_file=None, **overrides))


def json_lines(output: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in output.splitlines() if line.strip()]


# --- key and text redaction rules ---------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "password",
        "DB_PASSWORD",
        "new_password",
        "client_secret",
        "token",
        "access_token",
        "refresh_token",
        "Authorization",
        "api_key",
        "X-Api-Key",
        "cookie",
        "Set-Cookie",
        "jwt",
        "credentials",
        "dsn",
    ],
)
def test_sensitive_keys_are_detected(key: str) -> None:
    assert is_sensitive_key(key)


@pytest.mark.parametrize(
    "key",
    [
        "prompt_tokens",
        "completion_tokens",
        "max_output_tokens",
        "token_count",
        "tokens",
        "cache_key",
        "sort_key",
        "author",
        "document_id",
        "status",
    ],
)
def test_usage_and_ordinary_keys_are_not_redacted(key: str) -> None:
    assert not is_sensitive_key(key)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Authorization: Bearer abc.def.ghi", f"Authorization: Bearer {REDACTED}"),
        (
            "connect postgresql+psycopg://cortex:s3cret@db:5432/cortex failed",
            f"connect postgresql+psycopg://cortex:{REDACTED}@db:5432/cortex failed",
        ),
        ("redis://:p4ss@redis:6379/0", f"redis://:{REDACTED}@redis:6379/0"),
        ("https://example.com:8080/path", "https://example.com:8080/path"),
        ("nothing secret here", "nothing secret here"),
    ],
)
def test_secrets_inside_text_are_scrubbed(raw: str, expected: str) -> None:
    assert scrub_text(raw) == expected


# --- JSON pipeline end to end ---------------------------------------------------------------


def test_structlog_event_is_json_with_metadata_and_redaction(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure()

    get_logger("cortex.test").info(
        "user.login",
        password="hunter2",
        headers={"Authorization": "Bearer abc", "Accept": "application/json"},
        prompt_tokens=12,
        note="via postgres://cortex:pw@db/cortex",
    )

    [line] = json_lines(capsys.readouterr().out)
    assert line["event"] == "user.login"
    assert line["level"] == "info"
    assert line["logger"] == "cortex.test"
    assert line["timestamp"].endswith("Z")
    assert line["password"] == REDACTED
    assert line["headers"] == {"Authorization": REDACTED, "Accept": "application/json"}
    assert line["prompt_tokens"] == 12
    assert line["note"] == f"via postgres://cortex:{REDACTED}@db/cortex"
    assert "hunter2" not in json.dumps(line)


def test_stdlib_loggers_use_the_same_pipeline(capsys: pytest.CaptureFixture[str]) -> None:
    configure()

    logging.getLogger("uvicorn.error").warning("client sent Bearer %s", "leaky-token")

    [line] = json_lines(capsys.readouterr().out)
    assert line["logger"] == "uvicorn.error"
    assert line["level"] == "warning"
    assert line["event"] == f"client sent Bearer {REDACTED}"


def test_uvicorn_ansi_color_message_is_dropped(capsys: pytest.CaptureFixture[str]) -> None:
    configure()

    logging.getLogger("uvicorn.error").info(
        "Started server process [%d]", 1, extra={"color_message": "Started [\x1b[36m%d\x1b[0m]"}
    )

    [line] = json_lines(capsys.readouterr().out)
    assert line["event"] == "Started server process [1]"
    assert "color_message" not in line


def test_exception_messages_are_redacted_in_structured_tracebacks(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure()

    try:
        raise ConnectionError("cannot reach redis://:p4ss@redis:6379/0")
    except ConnectionError:
        get_logger("cortex.test").exception("dependency.failed")

    output = capsys.readouterr().out
    [line] = json_lines(output)
    assert line["exception"][0]["exc_type"] == "ConnectionError"
    assert "p4ss" not in output


def test_context_variables_are_attached_to_every_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure()

    structlog.contextvars.bind_contextvars(request_id="req-123")
    get_logger("a").info("first")
    logging.getLogger("b").info("second")

    lines = json_lines(capsys.readouterr().out)
    assert [line["request_id"] for line in lines] == ["req-123", "req-123"]


def test_log_level_filters_events(capsys: pytest.CaptureFixture[str]) -> None:
    configure(log_level="WARNING")

    get_logger("t").info("dropped")
    get_logger("t").warning("kept")

    assert [line["event"] for line in json_lines(capsys.readouterr().out)] == ["kept"]


def test_console_format_is_human_readable_and_still_redacted(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure(log_format="console")

    get_logger("t").info("user.login", password="hunter2")

    output = capsys.readouterr().out
    assert "user.login" in output
    assert "hunter2" not in output
    assert not output.lstrip().startswith("{")


def test_reconfiguring_does_not_duplicate_output(capsys: pytest.CaptureFixture[str]) -> None:
    configure()
    configure()

    get_logger("t").info("once")

    assert len(json_lines(capsys.readouterr().out)) == 1


def test_noisy_loggers_are_tamed() -> None:
    configure()

    assert logging.getLogger("uvicorn.access").disabled
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
