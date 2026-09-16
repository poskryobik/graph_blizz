"""Acceptance coverage for safe structured operational logging."""

import asyncio
import json
import logging
from io import StringIO

import pytest

from backend.app import create_app
from backend.config import ApplicationSettings, LoggingSettings
from backend.observability import (
    JsonLogFormatter,
    OperationalEvent,
    bind_logging_context,
    clear_logging_context,
    configure_logging,
    current_logging_context,
    get_logger,
    service_operation,
)

pytestmark = pytest.mark.unit


def _records(stream: StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_json_formatter_emits_stable_operational_fields() -> None:
    stream = StringIO()
    configure_logging(
        LoggingSettings(service_name="indexer", level="DEBUG"), stream=stream
    )

    with bind_logging_context(request_id="req-1", workspace_id="workspace-1"):
        get_logger("test").info(
            "indexed",
            extra={
                "document_id": "document-1",
                "operation": "index",
                "duration_ms": 12.5,
                "result": "success",
            },
        )

    [record] = _records(stream)
    assert record == {
        "message": "indexed",
        "timestamp": record["timestamp"],
        "level": "INFO",
        "service": "indexer",
        "request_id": "req-1",
        "workspace_id": "workspace-1",
        "document_id": "document-1",
        "operation": "index",
        "duration_ms": 12.5,
        "result": "success",
    }


def test_operation_logs_duration_and_preserves_exception() -> None:
    stream = StringIO()
    configure_logging(LoggingSettings(service_name="app"), stream=stream)
    error = RuntimeError("Authorization: Bearer do-not-log")

    with (
        pytest.raises(RuntimeError) as captured,
        service_operation("documents", "index", request_id="req-2"),
    ):
        raise error

    assert captured.value is error
    [record] = _records(stream)
    assert record["service"] == "documents"
    assert record["operation"] == "index"
    assert record["result"] == "failure"
    assert isinstance(record["duration_ms"], (int, float))
    assert record["duration_ms"] >= 0
    assert "do-not-log" not in stream.getvalue()


def test_operation_inherits_bound_identifiers() -> None:
    stream = StringIO()
    configure_logging(LoggingSettings(service_name="app"), stream=stream)

    with (
        bind_logging_context(request_id="outer", workspace_id="workspace"),
        service_operation("documents", "read"),
    ):
        pass

    [record] = _records(stream)
    assert record["request_id"] == "outer"
    assert record["workspace_id"] == "workspace"


def test_context_is_nested_task_local_and_clearable() -> None:
    clear_logging_context()

    async def child(value: str) -> str:
        with bind_logging_context(request_id=value):
            await asyncio.sleep(0)
            return str(current_logging_context()["request_id"])

    async def run() -> list[str]:
        with bind_logging_context(request_id="parent"):
            values = await asyncio.gather(child("first"), child("second"))
            assert current_logging_context()["request_id"] == "parent"
            return values

    assert asyncio.run(run()) == ["first", "second"]
    assert current_logging_context() == {}
    with bind_logging_context(request_id="temporary"):
        clear_logging_context()
        assert current_logging_context() == {}
    assert current_logging_context() == {}


@pytest.mark.parametrize(
    ("message", "sentinel"),
    (
        ("Authorization: Basic YmFzaWMtc2VudGluZWw=", "YmFzaWMtc2VudGluZWw="),
        ("prompt=prompt-sentinel", "prompt-sentinel"),
        ("response: response-sentinel", "response-sentinel"),
        ({"document_text": "document-sentinel"}, "document-sentinel"),
        ({"auth": "auth-sentinel"}, "auth-sentinel"),
        ({"headers": (("Authorization", "tuple-sentinel"),)}, "tuple-sentinel"),
        ({"mapping-key-sentinel": "ordinary"}, "mapping-key-sentinel"),
        ("arbitrary private document sentinel", "private document sentinel"),
    ),
)
def test_untyped_records_cannot_cross_json_boundary(
    message: object, sentinel: str
) -> None:
    formatter = JsonLogFormatter("test")
    rendered = formatter.format(
        logging.LogRecord("test", logging.INFO, __file__, 1, message, (), None)
    )

    assert sentinel not in rendered
    assert "message" not in json.loads(rendered)


def test_only_schema_fields_and_typed_safe_extras_are_emitted() -> None:
    record = logging.LogRecord(
        "test",
        logging.INFO,
        __file__,
        1,
        {
            "event": OperationalEvent.REQUEST_COMPLETED,
            "response_time_ms": 42.5,
            "content_type": "application/json; charset=utf-8",
            "durations_ms": tuple(range(12)),
            "document_text": "document-sentinel",
            "untrusted_key": "untrusted-sentinel",
        },
        (),
        None,
    )

    decoded = json.loads(JsonLogFormatter("test").format(record))

    assert decoded["message"] == "request completed"
    assert decoded["response_time_ms"] == 42.5
    assert decoded["content_type"] == "application/json; charset=utf-8"
    assert decoded["durations_ms"] == list(range(12))
    assert "document_text" not in decoded
    assert "untrusted_key" not in decoded
    assert "sentinel" not in json.dumps(decoded)


def test_exception_used_as_format_argument_does_not_expose_opaque_message() -> None:
    formatter = JsonLogFormatter("test")
    error = RuntimeError("opaque-exception-secret")
    record = logging.LogRecord(
        "test", logging.ERROR, __file__, 1, "failed: %s", (error,), None
    )

    rendered = formatter.format(record)

    assert "opaque-exception-secret" not in rendered
    assert "RuntimeError" in rendered


def test_text_format_does_not_emit_arbitrary_message_content() -> None:
    stream = StringIO()
    logger = configure_logging(LoggingSettings(format="text"), stream=stream)

    logger.info("private document sentinel")

    assert stream.getvalue() == "INFO operational event\n"
    assert "private document sentinel" not in stream.getvalue()


def test_repeated_configuration_and_app_creation_do_not_leak_handlers() -> None:
    logger = logging.getLogger("graph_blizz")
    baseline_external = [
        handler
        for handler in logger.handlers
        if not getattr(handler, "_graph_blizz_handler", False)
    ]
    settings = ApplicationSettings(_env_file=None)

    create_app(settings)
    create_app(settings)
    owned = [
        handler
        for handler in logger.handlers
        if getattr(handler, "_graph_blizz_handler", False)
    ]

    assert len(owned) == 1
    assert [
        handler
        for handler in logger.handlers
        if not getattr(handler, "_graph_blizz_handler", False)
    ] == baseline_external
