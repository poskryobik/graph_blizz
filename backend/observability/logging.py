"""Structured operational logging with a strict, typed output boundary."""

from __future__ import annotations

import json
import logging
import math
import re
import sys
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from enum import StrEnum
from typing import IO, Protocol
from uuid import UUID

LOGGER_NAME = "graph_blizz"
REDACTED = "[REDACTED]"
CONTEXT_FIELDS = (
    "request_id",
    "workspace_id",
    "document_id",
    "operation",
    "duration_ms",
    "result",
)
REQUIRED_OPERATION_FIELDS = ("service", *CONTEXT_FIELDS)


class OperationalEvent(StrEnum):
    """Messages approved for the operational log schema."""

    INDEXED = "indexed"
    OPERATION_COMPLETED = "operation completed"
    REQUEST_STARTED = "request started"
    REQUEST_COMPLETED = "request completed"
    SERVICE_STARTED = "service started"
    SERVICE_STOPPED = "service stopped"
    HEALTH_CHECK_COMPLETED = "health check completed"
    RETRY_SCHEDULED = "retry scheduled"


SAFE_EXTRA_FIELDS = (
    "response_time_ms",
    "status_code",
    "content_type",
    "item_count",
    "batch_size",
    "attempt",
    "retry_count",
    "durations_ms",
)

_context: ContextVar[dict[str, object] | None] = ContextVar(
    "operational_logging_context", default=None
)
_owned_handler_marker = "_graph_blizz_handler"
_identifier = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
_content_type = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,63}/"
    r"[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,127}"
    r"(?:;\s*[A-Za-z0-9._-]+=[A-Za-z0-9._-]+)?\Z"
)
_safe_exceptions = {
    "AssertionError",
    "ConnectionError",
    "KeyError",
    "LookupError",
    "OSError",
    "RuntimeError",
    "TimeoutError",
    "TypeError",
    "ValueError",
}


class _LoggingSettings(Protocol):
    @property
    def level(self) -> str: ...

    @property
    def format(self) -> str: ...

    @property
    def service_name(self) -> str: ...


class _SafeStreamHandler(logging.StreamHandler[IO[str]]):
    """Handler that suppresses diagnostics which could echo an unsafe record."""

    def handleError(self, record: logging.LogRecord) -> None:
        """Contain sink and formatting failures without exposing the record."""
        del record


class JsonLogFormatter(logging.Formatter):
    """Render only fields explicitly admitted by the operational log schema."""

    def __init__(self, service: str = "graph-blizz") -> None:
        super().__init__()
        self._service = _safe_identifier(service)

    def format(self, record: logging.LogRecord) -> str:
        """Serialize a record without arbitrary keys, values, or exception text."""
        try:
            context = current_logging_context()
            payload: dict[str, object] = {
                "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
                "level": record.levelname,
                "service": self._service,
                **{
                    name: _safe_operational_field(name, context.get(name))
                    for name in CONTEXT_FIELDS
                },
            }

            message = _safe_event_message(record)
            if message is not None:
                payload["message"] = message

            if isinstance(record.msg, Mapping):
                for name in ("service", *CONTEXT_FIELDS):
                    if name in record.msg:
                        payload[name] = _safe_operational_field(name, record.msg[name])
                for name in SAFE_EXTRA_FIELDS:
                    if name in record.msg:
                        value = _safe_extra_field(name, record.msg[name])
                        if value is not None:
                            payload[name] = value

            for name in ("service", *CONTEXT_FIELDS):
                if hasattr(record, name):
                    payload[name] = _safe_operational_field(name, getattr(record, name))
            for name in SAFE_EXTRA_FIELDS:
                if hasattr(record, name):
                    value = _safe_extra_field(name, getattr(record, name))
                    if value is not None:
                        payload[name] = value

            if record.exc_info is not None:
                payload["exception"] = _safe_exception_name(record.exc_info[0])
            elif isinstance(record.args, tuple):
                exception = next(
                    (item for item in record.args if isinstance(item, BaseException)),
                    None,
                )
                if exception is not None:
                    payload["exception"] = _safe_exception_name(type(exception))
            return json.dumps(
                payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            )
        except Exception:  # noqa: BLE001 - logging must not affect application flow
            return json.dumps(
                {
                    "timestamp": datetime.now(UTC).isoformat(),
                    "level": "ERROR",
                    "service": "logging",
                    **dict.fromkeys(CONTEXT_FIELDS),
                    "message": "log record unavailable",
                },
                separators=(",", ":"),
            )


class _SafeTextFormatter(logging.Formatter):
    """Render only approved event names and exception classes as text."""

    def format(self, record: logging.LogRecord) -> str:
        """Format an untrusted record without arbitrary record content."""
        message = _safe_event_message(record) or "operational event"
        exception = ""
        if record.exc_info is not None:
            exception = f" {_safe_exception_name(record.exc_info[0])}"
        return f"{record.levelname} {message}{exception}"


def configure_logging(
    settings: _LoggingSettings, *, stream: IO[str] | None = None
) -> logging.Logger:
    """Configure and return the application logger without external I/O.

    Repeated calls replace only the handler owned by this module. This keeps app
    factory calls idempotent and preserves handlers installed by test or host code.
    """
    logger = logging.getLogger(LOGGER_NAME)
    handler = _SafeStreamHandler(stream or sys.stdout)
    setattr(handler, _owned_handler_marker, True)
    if settings.format == "json":
        handler.setFormatter(JsonLogFormatter(settings.service_name))
    else:
        handler.setFormatter(_SafeTextFormatter())

    for existing in tuple(logger.handlers):
        if getattr(existing, _owned_handler_marker, False):
            logger.removeHandler(existing)
            existing.close()
    logger.addHandler(handler)
    logger.setLevel(settings.level)
    logger.propagate = False
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """Return the application logger or one of its named descendants."""
    return logging.getLogger(LOGGER_NAME if name is None else f"{LOGGER_NAME}.{name}")


def current_logging_context() -> dict[str, object]:
    """Return an isolated copy of the current task's logging context."""
    return dict(_context.get() or {})


def clear_logging_context() -> None:
    """Clear correlation fields in the current execution context only."""
    _context.set({})


@contextmanager
def bind_logging_context(**values: object) -> Iterator[None]:
    """Temporarily bind supported operational fields for this task or request."""
    unknown = values.keys() - set(CONTEXT_FIELDS)
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ValueError(f"unknown logging context fields: {names}")
    merged = current_logging_context()
    merged.update(values)
    token = _context.set(merged)
    try:
        yield
    finally:
        _context.reset(token)


@contextmanager
def service_operation(
    service: str,
    operation: str,
    *,
    request_id: UUID | str | None = None,
    workspace_id: UUID | str | None = None,
    document_id: UUID | str | None = None,
) -> Iterator[None]:
    """Measure an operation and emit one success or failure record on exit."""
    inherited = current_logging_context()
    fields: dict[str, object] = {
        "service": service,
        "operation": operation,
        "request_id": request_id or inherited.get("request_id"),
        "workspace_id": workspace_id or inherited.get("workspace_id"),
        "document_id": document_id or inherited.get("document_id"),
    }
    started = time.perf_counter_ns()
    context_fields = {key: value for key, value in fields.items() if key != "service"}
    with bind_logging_context(**context_fields):
        try:
            yield
        except BaseException:
            _log_operation(fields, started, "failure")
            raise
        else:
            _log_operation(fields, started, "success")


def _log_operation(fields: Mapping[str, object], started: int, result: str) -> None:
    try:
        get_logger("operations").info(
            OperationalEvent.OPERATION_COMPLETED,
            extra={
                **fields,
                "duration_ms": max(0.0, (time.perf_counter_ns() - started) / 1_000_000),
                "result": result,
            },
        )
    except Exception:  # noqa: BLE001 - observability must not break business flow
        return


def _safe_event_message(record: logging.LogRecord) -> str | None:
    if record.args:
        return None
    candidate = (
        record.msg.get("event") if isinstance(record.msg, Mapping) else record.msg
    )
    if isinstance(candidate, OperationalEvent):
        return candidate.value
    if isinstance(candidate, str) and candidate in OperationalEvent:
        return candidate
    return None


def _safe_operational_field(name: str, value: object) -> object:
    if value is None:
        return None
    if name == "duration_ms":
        return _safe_nonnegative_number(value)
    if name == "result":
        return value if value in {"success", "failure"} else REDACTED
    if name in {"service", "request_id", "workspace_id", "document_id", "operation"}:
        return _safe_identifier(value)
    return REDACTED


def _safe_extra_field(name: str, value: object) -> object | None:
    if name == "response_time_ms":
        return _safe_nonnegative_number(value)
    if name == "status_code":
        return value if isinstance(value, int) and 100 <= value <= 599 else None
    if name == "content_type":
        return (
            value if isinstance(value, str) and _content_type.fullmatch(value) else None
        )
    if name in {"item_count", "batch_size", "attempt", "retry_count"}:
        return (
            value
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0
            else None
        )
    if (
        name == "durations_ms"
        and isinstance(value, Sequence)
        and not isinstance(value, (str, bytes, bytearray))
    ):
        numbers = [_safe_nonnegative_number(item) for item in value]
        return (
            numbers
            if len(numbers) <= 1_024 and all(item is not None for item in numbers)
            else None
        )
    return None


def _safe_nonnegative_number(value: object) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if value >= 0 and math.isfinite(float(value)) else None


def _safe_identifier(value: object) -> str:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, str) and _identifier.fullmatch(value):
        return value
    return REDACTED


def _safe_exception_name(exception_type: object) -> str:
    if isinstance(exception_type, type) and exception_type.__name__ in _safe_exceptions:
        return exception_type.__name__
    return "Exception"
