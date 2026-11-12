"""Bounded, secret-safe telemetry records for rust-cleanup sweeps.

Records are single-line JSON documents with a fixed vocabulary, so journal
output can be aggregated without leaking target paths, mount points, user
names, command lines, environment values, or configuration contents. A field
whose name or value falls outside that vocabulary is dropped with a warning
rather than written.
"""

import json
import logging
import re
import secrets
import sys
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from rust_cleanup.diagnostics import FilesystemDiagnostics

EVENT_PREFIX = "rust-cleanup-event "
_LIVE_LOGGER_NAME = "rust_cleanup"
_EVENT_LOGGER = logging.getLogger(f"{_LIVE_LOGGER_NAME}.events")
_DIAGNOSTICS_LOGGER = logging.getLogger(f"{_LIVE_LOGGER_NAME}.diagnostics")

OPERATIONS = frozenset({
    "invocation",
    "sweep",
    "discovery",
    "lock",
    "state_load",
    "state_save",
    "active_build_inspection",
    "filesystem_decision",
    "deletion",
    "recovery_watermark",
    "unresolved_pressure",
})
LIFECYCLES = frozenset({"start", "finish"})
MODES = frozenset({"normal", "pressure", "critical"})
OUTCOMES = frozenset({
    "completed",
    "failed",
    "acquired",
    "contended",
    "loaded",
    "absent",
    "malformed",
    "saved",
    "clean",
    "uncertain",
    "complete",
    "incomplete",
    "reached",
    "not_reached",
    "skipped",
    "unresolved",
})
_COUNTER_FIELDS = frozenset({
    "duration_ms",
    "scanned",
    "protected",
    "eligible",
    "deleted",
    "failed",
    "filesystems",
    "unknown_activity",
    "unidentified",
    "omitted",
    "roots",
    "protected_active_build",
    "protected_changed",
})
_TOKEN_FIELDS = frozenset({
    "operation",
    "lifecycle",
    "outcome",
    "correlation",
    "mode",
})
_BOOLEAN_FIELDS = frozenset({"dry_run"})
FIELDS = _COUNTER_FIELDS | _TOKEN_FIELDS | _BOOLEAN_FIELDS
_TOKEN = re.compile(r"\A[a-z][a-z0-9_]{0,31}\Z")
_MAX_COUNTER = 10**12


def new_correlation() -> str:
    """Return a per-invocation correlation token for one sweep and its spans.

    The token is random and bounded, so it can never carry a path, a mount
    point, a command line, or any other user-controlled value.

    Returns
    -------
    str
        A bounded correlation identifier shared by every record of one run.
    """
    return f"run_{secrets.token_hex(3)}"


class EventSink(typ.Protocol):
    """Destination for one bounded telemetry record."""

    def emit(self, record: cabc.Mapping[str, object]) -> None:
        """Write one already-bounded record.

        Parameters
        ----------
        record : cabc.Mapping[str, object]
            Record already validated against the telemetry vocabulary.
        """


class EventPort(typ.Protocol):
    """Domain-neutral port for emitting one bounded span record.

    The sweep engine and the values it returns depend on this port rather than
    on :class:`EventEmitter`, so the domain layer never imports the
    logging-backed implementation; the command-line entry point wires the
    concrete emitter at the composition root.
    """

    def emit(self, operation: str, lifecycle: str, /, **fields: object) -> None:
        """Emit one bounded start or finish record.

        Parameters
        ----------
        operation : str
            Logical operation the record belongs to.
        lifecycle : str
            Either ``start`` or ``finish``.
        **fields : object
            Bounded scalar values keyed by names in :data:`FIELDS`.
        """


class LoggingEventSink:
    """Emit records through the ``rust_cleanup.events`` logger."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        """Bind the sink to ``logger`` or the package event logger."""
        self._logger = logger or _EVENT_LOGGER

    def emit(self, record: cabc.Mapping[str, object]) -> None:
        """Log one bounded record as prefixed single-line JSON.

        Parameters
        ----------
        record : cabc.Mapping[str, object]
            Bounded record serialised to one JSON line at INFO level.
        """
        self._logger.info(
            "%s%s", EVENT_PREFIX, json.dumps(dict(record), sort_keys=True)
        )


def _rejected(message: str, name: str) -> tuple[bool, object]:
    """Warn about a dropped telemetry field and report it as unbounded.

    Returns
    -------
    tuple[bool, object]
        Always ``(False, None)``, marking the field as dropped.
    """
    _EVENT_LOGGER.warning(message, name)
    return False, None


def _bounded_number(name: str, value: object) -> tuple[bool, object]:
    """Return a counter field's bounded number or a rejection.

    Booleans are rejected explicitly because ``bool`` is an ``int`` subclass,
    so a flag passed to a counter field would otherwise record as ``0``/``1``.

    Returns
    -------
    tuple[bool, object]
        Whether the counter is bounded, and the bounded value to record.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return _rejected("dropping non-numeric telemetry field %s", name)
    if 0 <= value <= _MAX_COUNTER:
        return True, round(value, 3)
    return _rejected("dropping unbounded telemetry field %s", name)


def _bounded_text(name: str, value: object) -> tuple[bool, object]:
    """Return a token field's fixed-vocabulary text or a rejection.

    Returns
    -------
    tuple[bool, object]
        Whether the text is bounded, and the bounded value to record.
    """
    if not isinstance(value, str):
        return _rejected("dropping non-text telemetry field %s", name)
    if name == "outcome" and value not in OUTCOMES:
        return _rejected("dropping telemetry outcome %s outside the vocabulary", name)
    if name == "mode" and value not in MODES:
        return _rejected("dropping telemetry mode %s outside the vocabulary", name)
    if _TOKEN.fullmatch(value):
        return True, value
    return _rejected("dropping unbounded telemetry field %s", name)


def _bounded_flag(name: str, value: object) -> tuple[bool, object]:
    """Return a flag field's boolean value or a rejection.

    Returns
    -------
    tuple[bool, object]
        Whether the flag is bounded, and the bounded value to record.
    """
    if isinstance(value, bool):
        return True, value
    return _rejected("dropping non-boolean telemetry field %s", name)


def _bounded_field(name: str, value: object) -> tuple[bool, object]:
    """Return whether a named telemetry field carries a bounded value.

    Each field's scalar kind is fixed by its name, so a counter is never
    recorded from text and a token is never recorded from a number. ``None`` is
    outside every kind, so an absent value is dropped rather than emitted.

    Returns
    -------
    tuple[bool, object]
        Whether the value is bounded, and the bounded value to record.
    """
    if name not in FIELDS:
        return _rejected("dropping unknown telemetry field %s", name)
    if name in _BOOLEAN_FIELDS:
        return _bounded_flag(name, value)
    if name in _COUNTER_FIELDS:
        return _bounded_number(name, value)
    return _bounded_text(name, value)


class EventEmitter:
    """Validate and emit fixed-vocabulary, bounded telemetry records."""

    def __init__(self, sink: EventSink | None = None) -> None:
        """Emit through ``sink`` or the default logging sink."""
        self._sink = sink or LoggingEventSink()

    def emit(self, operation: str, lifecycle: str, **fields: object) -> None:
        """Emit one bounded record, dropping values outside the vocabulary.

        Parameters
        ----------
        operation : str
            One of :data:`OPERATIONS`.
        lifecycle : str
            Either ``start`` or ``finish``.
        **fields : object
            Bounded scalar values keyed by names in :data:`FIELDS`.
        """
        if operation not in OPERATIONS or lifecycle not in LIFECYCLES:
            _EVENT_LOGGER.warning("dropping telemetry record outside the vocabulary")
            return
        record: dict[str, object] = {"operation": operation, "lifecycle": lifecycle}
        for name, value in fields.items():
            allowed, bounded_value = _bounded_field(name, value)
            if allowed:
                record[name] = bounded_value
        self._sink.emit(record)


def emit_diagnostics(diagnostics: FilesystemDiagnostics, *, dry_run: bool) -> None:
    """Log per-filesystem policy selection and reclamation outcome.

    The record is the operator-facing counterpart to the bounded telemetry
    events: it names the mount and the policy in force, including how many
    targets were preserved because their activity could not be established.
    Reporting lives here rather than beside the records it renders, so the
    query path never writes a log record of its own.

    Parameters
    ----------
    diagnostics : FilesystemDiagnostics
        Completed per-filesystem record to render for the operator.
    dry_run : bool
        Whether the sweep only planned the deletions it reports, in which case
        the watermark is reported as ``skipped``: a dry run reads no recovery
        result to report.
    """
    mount = diagnostics.identity.mount_point
    previous = (
        diagnostics.previous_mode.value if diagnostics.previous_mode else "derived"
    )
    action = "would delete" if dry_run else "deleted"
    if dry_run:
        exit_status = "skipped"
    else:
        exit_status = "reached" if diagnostics.exit_reached else "not reached"
    _DIAGNOSTICS_LOGGER.info(
        "rust-cleanup filesystem=%s usage=%.2f%%->%.2f%% previous=%s mode=%s "
        "cutoff=%ss scanned=%d protected=%d unknown-activity=%d eligible=%d "
        "%s=%d failed=%d exit-watermark=%s",
        mount,
        diagnostics.usage_before,
        diagnostics.usage_after,
        previous,
        diagnostics.selected_mode.value,
        diagnostics.cutoff_seconds,
        diagnostics.scanned,
        diagnostics.protected,
        diagnostics.unknown_activity,
        diagnostics.eligible,
        action,
        diagnostics.deleted,
        diagnostics.failed,
        exit_status,
    )


def configure_logging(stream: typ.IO[str] | None = None) -> None:
    """Route ``rust_cleanup`` records to stderr at INFO for command-line runs.

    Replaces any handlers already attached to the package logger so repeated
    invocations cannot duplicate output and always target the current stream.

    Parameters
    ----------
    stream : typing.IO[str] | None
        Destination for human diagnostics and telemetry records. Defaults to
        :data:`sys.stderr`.
    """
    logger = logging.getLogger(_LIVE_LOGGER_NAME)
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.handlers[:] = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
