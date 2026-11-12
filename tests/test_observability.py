"""Contracts for the bounded, secret-safe telemetry emitter."""

import io
import json
import logging
import typing as typ
from pathlib import Path

import pytest
from conftest import RecordingSink

from rust_cleanup.observability import (
    EVENT_PREFIX,
    FIELDS,
    MODES,
    EventEmitter,
    LoggingEventSink,
    configure_logging,
)
from rust_cleanup.pressure import Mode

if typ.TYPE_CHECKING:
    import collections.abc as cabc

_SINK_LOGGER_NAME = "rust_cleanup.tests.sink"


@pytest.fixture
def wired_sink_logger() -> cabc.Iterator[tuple[logging.Logger, io.StringIO]]:
    """Wire a private logger to a fresh stream and restore it afterwards.

    The logger is a process-wide singleton, so a test that rewires it owns
    putting it back: the handlers, the level, and propagation are all restored
    and the installed handler is closed. The autouse ``_restore_package_logging``
    fixture in ``conftest`` covers only the ``rust_cleanup`` logger, so nothing
    else would undo this wiring for ``_SINK_LOGGER_NAME``.

    Yields
    ------
    tuple[logging.Logger, io.StringIO]
        The wired logger and the stream its records are written to.
    """
    logger = logging.getLogger(_SINK_LOGGER_NAME)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handlers = logger.handlers[:]
    level = logger.level
    propagate = logger.propagate
    logger.handlers[:] = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        yield logger, stream
    finally:
        logger.handlers[:] = handlers
        logger.setLevel(level)
        logger.propagate = propagate
        handler.close()


def test_emitter_drops_fields_outside_the_fixed_vocabulary() -> None:
    """A field name outside the fixed vocabulary is never written."""
    sink = RecordingSink()
    EventEmitter(sink=sink).emit(
        "sweep", "finish", outcome="completed", target_path="/home/user/secret"
    )
    (record,) = sink.records
    assert "target_path" not in record, "unbounded field name was written"
    assert set(record) <= FIELDS, "record escaped the fixed field set"


def test_emitter_drops_values_that_are_not_bounded_tokens() -> None:
    """Path-like and secret-like values are dropped rather than echoed."""
    sink = RecordingSink()
    EventEmitter(sink=sink).emit(
        "sweep",
        "finish",
        mode="/home/user/.ssh/id_rsa",
        outcome="sk-live-51H8xQ",
        deleted="not a number",
    )
    (record,) = sink.records
    assert record == {"operation": "sweep", "lifecycle": "finish"}, (
        "unbounded value was written"
    )


def test_emitter_drops_values_outside_the_mode_vocabulary() -> None:
    """A secret-like token in a fixed-vocabulary field is still dropped."""
    sink = RecordingSink()
    EventEmitter(sink=sink).emit("sweep", "finish", mode="sk_live_51h8xq")
    (record,) = sink.records
    assert "mode" not in record, "mode outside the vocabulary was written"


def test_mode_vocabulary_matches_the_pressure_modes() -> None:
    """The telemetry mode vocabulary stays aligned with the policy modes."""
    assert {mode.value for mode in Mode} == MODES, "mode vocabulary drifted"


def test_emitter_rejects_unknown_operations_and_lifecycles() -> None:
    """Records outside the fixed vocabulary are dropped whole."""
    sink = RecordingSink()
    emitter = EventEmitter(sink=sink)
    emitter.emit("secret_operation", "start")
    emitter.emit("sweep", "middle")
    assert not sink.records, "record outside the vocabulary was emitted"


def test_emitter_bounds_numeric_counters() -> None:
    """Counters stay finite, non-negative, and rounded for aggregation."""
    sink = RecordingSink()
    EventEmitter(sink=sink).emit(
        "deletion",
        "finish",
        outcome="completed",
        deleted=-1,
        failed=10**15,
        duration_ms=12.34567,
    )
    (record,) = sink.records
    assert "deleted" not in record, "negative counter was written"
    assert "failed" not in record, "oversized counter was written"
    assert record["duration_ms"] == pytest.approx(12.346), "duration was not rounded"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("outcome", True),
        ("scanned", "normal"),
        ("dry_run", 1),
        ("correlation", 7),
        ("deleted", None),
    ],
)
def test_emitter_drops_values_whose_kind_the_field_does_not_accept(
    field: str, value: object
) -> None:
    """A field's scalar kind is fixed by its name, so a mismatched value is dropped."""
    sink = RecordingSink()

    EventEmitter(sink=sink).emit("sweep", "finish", **{field: value})

    (record,) = sink.records
    assert record == {"operation": "sweep", "lifecycle": "finish"}, (
        f"{field} accepted a value of the wrong kind: {value!r}"
    )


def test_emitter_drops_structured_values() -> None:
    """Structured values cannot smuggle paths or configuration into records."""
    sink = RecordingSink()
    EventEmitter(sink=sink).emit(
        "state_save",
        "finish",
        outcome="saved",
        scanned=[Path("relative/secret")],
        deleted={"filesystem": 1},
    )
    (record,) = sink.records
    assert set(record) == {"operation", "lifecycle", "outcome"}, (
        "structured value was written"
    )


def test_logging_sink_writes_one_prefixed_json_line_per_record(
    wired_sink_logger: tuple[logging.Logger, io.StringIO],
) -> None:
    """The default sink writes machine-readable single-line JSON."""
    logger, stream = wired_sink_logger
    LoggingEventSink(logger).emit({"operation": "sweep", "lifecycle": "start"})
    (line,) = stream.getvalue().splitlines()
    assert line.startswith(EVENT_PREFIX), "event prefix missing"
    assert json.loads(line.removeprefix(EVENT_PREFIX)) == {
        "operation": "sweep",
        "lifecycle": "start",
    }, "record payload changed"


def test_configure_logging_routes_records_to_the_given_stream() -> None:
    """Telemetry reaches the configured stream through the default emitter."""
    stream = io.StringIO()
    configure_logging(stream)
    EventEmitter().emit("sweep", "start", dry_run=True)
    assert [
        json.loads(line.removeprefix(EVENT_PREFIX))
        for line in stream.getvalue().splitlines()
    ] == [{"operation": "sweep", "lifecycle": "start", "dry_run": True}], (
        "configured stream did not receive the record"
    )
