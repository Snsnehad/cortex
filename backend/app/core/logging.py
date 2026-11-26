"""Structured logging for the whole process.

Every log line, whether it comes from ``structlog`` or from a standard-library logger
(uvicorn, httpx, Celery, SQLAlchemy …), goes through one pipeline:

    context vars (request_id, …) → level/logger/timestamp → redaction → renderer

and is written to stdout as one JSON object per line (``CORTEX_LOG_FORMAT=json``, the default)
or as coloured human-readable text (``console``, local development only).

Redaction is defence in depth: code should never log secrets in the first place, but if a
password, token or credential-bearing URL slips into an event, it is masked before output.
"""

import logging
import re
import sys
from collections.abc import Mapping
from typing import Any, Literal

import structlog
from structlog.types import EventDict, Processor, WrappedLogger

from app.config import Settings

REDACTED = "[REDACTED]"

# Keys are normalised (lower-case, "-" → "_") before matching. Exact names plus suffixes:
# deliberately NOT a plain substring match on "token", so usage fields such as
# ``prompt_tokens`` or ``token_count`` stay visible.
_SENSITIVE_EXACT = frozenset({"token", "jwt", "dsn", "auth", "cookie", "set_cookie"})
_SENSITIVE_SUFFIXES = (
    "password",
    "passwd",
    "secret",
    "_token",
    "api_key",
    "apikey",
    "authorization",
    "credentials",
    "private_key",
    "_cookie",
)

# Secrets embedded in free text, e.g. exception messages or URLs.
_BEARER = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]+")
# user part may be empty: redis://:password@host
_URL_CREDENTIALS = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^:/@\s]*):[^@\s]+@")

_HANDLER_MARKER = "_cortex_handler"

# Libraries that log every outgoing request at INFO; readiness probes would flood the logs.
_QUIET_LIBRARIES = ("httpx", "httpcore")


def is_sensitive_key(key: str) -> bool:
    normalised = key.lower().replace("-", "_")
    return normalised in _SENSITIVE_EXACT or normalised.endswith(_SENSITIVE_SUFFIXES)


def scrub_text(text: str) -> str:
    text = _BEARER.sub(rf"\1 {REDACTED}", text)
    return _URL_CREDENTIALS.sub(rf"\1:{REDACTED}@", text)


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, Mapping):
        return {
            key: REDACTED if isinstance(key, str) and is_sensitive_key(key) else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [_redact(item) for item in value]
    return value


def redact_sensitive_data(
    _logger: WrappedLogger, _method_name: str, event_dict: EventDict
) -> EventDict:
    """structlog processor: mask sensitive keys (recursively) and secrets inside strings."""
    for key, value in list(event_dict.items()):
        if key.startswith("_"):  # structlog/stdlib internals such as _record
            continue
        event_dict[key] = REDACTED if is_sensitive_key(key) else _redact(value)
    return event_dict


class _CurrentStdoutHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """Writes to whatever ``sys.stdout`` is at emit time, not when the handler was created.

    A handler that captures ``sys.stdout`` once keeps writing to that object even after it
    has been replaced and closed (test capture phases, embedding hosts). Looking it up per
    record is cheap and always correct.
    """

    def emit(self, record: logging.LogRecord) -> None:
        self.stream = sys.stdout
        super().emit(record)


def _drop_color_message(
    _logger: WrappedLogger, _method_name: str, event_dict: EventDict
) -> EventDict:
    """uvicorn adds a ``color_message`` copy with ANSI escape codes: noise in any output."""
    event_dict.pop("color_message", None)
    return event_dict


def _shared_processors() -> list[Processor]:
    """Applied to both structlog events and records from stdlib loggers."""
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.ExtraAdder(),
        _drop_color_message,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact_sensitive_data,
    ]


def _render_processors(log_format: Literal["json", "console"]) -> list[Processor]:
    if log_format == "json":
        # Tracebacks become structured data; redact again because exception messages are
        # only materialised at this point.
        return [
            structlog.processors.dict_tracebacks,
            redact_sensitive_data,
            structlog.processors.JSONRenderer(),
        ]
    return [
        structlog.processors.format_exc_info,
        redact_sensitive_data,
        structlog.dev.ConsoleRenderer(),
    ]


def configure_logging(settings: Settings) -> None:
    """Configure structlog and the stdlib root logger. Safe to call more than once."""
    level = logging.getLevelNamesMapping()[settings.log_level]

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *_shared_processors(),
            structlog.processors.StackInfoRenderer(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        # Not cached: allows reconfiguration (tests, app factory called twice).
        cache_logger_on_first_use=False,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_shared_processors(),
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            *_render_processors(settings.log_format),
        ],
    )
    handler = _CurrentStdoutHandler()
    handler.setFormatter(formatter)
    setattr(handler, _HANDLER_MARKER, True)

    root = logging.getLogger()
    # Replace only our own handler, so test-framework handlers (pytest caplog) survive.
    for existing in [h for h in root.handlers if getattr(h, _HANDLER_MARKER, False)]:
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    # uvicorn installs its own handlers before the app factory runs; route its records
    # through ours instead. Its access log is replaced by RequestContextMiddleware.
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True

    for name in _QUIET_LIBRARIES:
        logging.getLogger(name).setLevel(max(level, logging.WARNING))


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.stdlib.get_logger(name)
    return logger


__all__ = [
    "REDACTED",
    "configure_logging",
    "get_logger",
    "is_sensitive_key",
    "redact_sensitive_data",
    "scrub_text",
]
