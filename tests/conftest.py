"""Shared helpers and isolation fixtures for the rust_cleanup test suite.

Keeping the telemetry recorder and the injected-run builders in one module
avoids copy-pasting the same test scaffolding into every test module in this
package.
"""

import contextlib
import json
import logging
import os
import typing as typ
from unittest import mock

import pytest

from rust_cleanup import CACHEDIR_TAG, CleanupError, CleanupOutcome, CleanupRun, cli
from rust_cleanup.config import DEFAULT_CONFIG
from rust_cleanup.observability import EVENT_PREFIX, EventEmitter
from rust_cleanup.pressure import FilesystemIdentity

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

    from rust_cleanup.active_build import ActiveBuildDetector
    from rust_cleanup.config import CleanupConfig
    from rust_cleanup.filesystem import DirectoryDeleter
    from rust_cleanup.reclaim import ActivityGetter
    from rust_cleanup.state import StateStore
    from rust_cleanup.timing import Monotonic

_LOGGER_NAME = "rust_cleanup"

# The scan-root suites share one ``home`` fixture and two builders. They live in
# a support module so no suite can fork them, and are registered here because
# pytest only discovers fixtures declared in ``conftest.py`` or a plugin.
pytest_plugins = ["scan_root_support"]

# What a scripted advisory lock yields: this run holds the lock, so the sweep
# below it proceeds. Named rather than passed as a bare literal so the intent
# is visible at the call site.
_UNCONTENDED_LOCK = True


@pytest.fixture(autouse=True)
def _restore_package_logging() -> cabc.Iterator[None]:
    """Restore rust_cleanup logging state so command-line tests stay isolated.

    ``configure_logging`` intentionally rewires the package logger for a real
    invocation. Tests must not inherit a handler bound to a capture stream that
    the harness has already closed.

    Yields
    ------
    None
        Control to the test; handlers, level, and propagation are restored
        afterwards.
    """
    logger = logging.getLogger(_LOGGER_NAME)
    handlers = logger.handlers[:]
    level = logger.level
    propagate = logger.propagate
    yield
    logger.handlers[:] = handlers
    logger.setLevel(level)
    logger.propagate = propagate


class RecordingSink:
    """Capture emitted telemetry records for boundary assertions.

    Attributes
    ----------
    records : list[dict[str, object]]
        Every captured telemetry mapping, in emission order.
    """

    def __init__(self) -> None:
        """Start with no captured records."""
        self.records: list[dict[str, object]] = []

    def emit(self, record: cabc.Mapping[str, object]) -> None:
        """Capture one bounded telemetry mapping.

        Parameters
        ----------
        record : collections.abc.Mapping[str, object]
            Telemetry mapping copied into ``records``.
        """
        self.records.append(dict(record))

    def boundaries(self) -> set[tuple[str, str]]:
        """Return the observed ``(operation, lifecycle)`` pairs.

        Returns
        -------
        set[tuple[str, str]]
            Every captured operation paired with its lifecycle stage.
        """
        return {
            (str(record["operation"]), str(record["lifecycle"]))
            for record in self.records
        }

    def for_operation(self, operation: str) -> list[dict[str, object]]:
        """Return every captured record for one operation.

        Parameters
        ----------
        operation : str
            Operation name whose captured records are returned.

        Returns
        -------
        list[dict[str, object]]
            Captured records for ``operation`` in emission order.
        """
        return [record for record in self.records if record["operation"] == operation]

    def serialized(self) -> str:
        """Return the serialized records used to prove values never leak.

        Returns
        -------
        str
            The JSON form of every captured record.
        """
        return json.dumps(self.records)


class InactiveBuilds:
    """A deterministic detector that protects nothing."""

    def is_active(  # ruff: ignore[no-self-use] - implements the ActiveBuildDetector protocol.
        self, target_path: Path
    ) -> bool:
        """Return false for every target path.

        Parameters
        ----------
        target_path : Path
            Target path the caller asks about; this detector ignores it.

        Returns
        -------
        bool
            Always ``False``.
        """
        return False


class RecordingDeleter:
    """Record deletion attempts while leaving every target on disk.

    Attributes
    ----------
    deleted : list[Path]
        Directories passed to the deleter, in call order.
    """

    def __init__(self) -> None:
        """Start with no recorded deletions."""
        self.deleted: list[Path] = []

    def __call__(self, directory: Path, /) -> bool:
        """Record ``directory`` and report it as removed.

        Parameters
        ----------
        directory : Path
            Target directory appended to ``deleted``.

        Returns
        -------
        bool
            Always ``True``.
        """
        self.deleted.append(directory)
        return True


class FailingDeleter:
    """Fail every deletion the way an undeletable mount does."""

    def __init__(self, message: str = "disk on fire") -> None:
        """Fail with ``message`` as the raised error text.

        Parameters
        ----------
        message : str, default="disk on fire"
            Error text carried by every raised ``CleanupError``.
        """
        self.message = message

    def __call__(self, directory: Path, /) -> bool:
        """Raise the configured failure for ``directory``.

        Parameters
        ----------
        directory : Path
            Target directory whose deletion was requested; unused.

        Raises
        ------
        CleanupError
            For every directory, carrying the configured message.
        """
        raise CleanupError(self.message)


class RunOptions(typ.TypedDict, total=False):
    """Typed optional arguments supplied to :class:`CleanupRun` in tests.

    Attributes
    ----------
    config : CleanupConfig
        Retention policies the sweep applies.
    dry_run : bool
        Whether the run reports its plan without deleting or writing state.
    state_store : StateStore
        Store holding persisted hysteresis modes between sweeps.
    active_build_detector : ActiveBuildDetector
        Detector protecting targets owned by running builds.
    detector_factory : collections.abc.Callable[[], ActiveBuildDetector]
        Factory consulted afresh before each deletion.
    usage_getter : collections.abc.Callable[[Path], float]
        Reads a filesystem's used-capacity percentage.
    identity_getter : collections.abc.Callable[[Path], FilesystemIdentity]
        Resolves the mounted filesystem that backs a path.
    activity_getter : ActivityGetter
        Re-reads a target's newest artefact mtime.
    delete_directory : DirectoryDeleter
        Removes one planned target directory.
    now : float
        Clock reading shared by every decision in one sweep.
    monotonic : Monotonic
        Clock the telemetry durations are measured with.
    events : EventEmitter
        Telemetry emitter receiving the run's boundary records.
    correlation : str
        Token stamped on every telemetry record of one run.
    """

    config: CleanupConfig
    dry_run: bool
    state_store: StateStore
    active_build_detector: ActiveBuildDetector
    detector_factory: cabc.Callable[[], ActiveBuildDetector]
    usage_getter: cabc.Callable[[Path], float]
    identity_getter: cabc.Callable[[Path], FilesystemIdentity]
    activity_getter: ActivityGetter
    delete_directory: DirectoryDeleter
    now: float
    monotonic: Monotonic
    events: EventEmitter
    correlation: str


def without_correlation(record: cabc.Mapping[str, object]) -> dict[str, object]:
    """Return a telemetry record without its per-run correlation token.

    Tests that assert the fixed shape of a record drop the token here and
    prove separately that every record of one run shares it.

    Parameters
    ----------
    record : collections.abc.Mapping[str, object]
        Telemetry record whose ``correlation`` field is dropped.

    Returns
    -------
    dict[str, object]
        The record's remaining fields.
    """
    return {name: value for name, value in record.items() if name != "correlation"}


def build_run(**kwargs: typ.Unpack[RunOptions]) -> CleanupRun:
    """Construct one injected cleanup run for a focused contract test.

    Returns
    -------
    CleanupRun
        The injected run with only the supplied overrides applied.
    """
    return CleanupRun(**kwargs)


def build_sink_run(sink: RecordingSink, **kwargs: typ.Unpack[RunOptions]) -> CleanupRun:
    """Construct a run whose telemetry reaches ``sink``.

    Parameters
    ----------
    sink : RecordingSink
        Recording sink that receives every emitted event.

    Returns
    -------
    CleanupRun
        The injected run wired to the recording telemetry sink.
    """
    options: RunOptions = {**kwargs, "events": EventEmitter(sink=sink)}
    return build_run(**options)


def main_with_outcome(root: Path, outcome: CleanupOutcome, *flags: str) -> int:
    """Run the command line against a scripted sweep outcome.

    The configuration, the advisory lock, and the sweep itself are all
    replaced, so the invocation exercises exactly the command-line behaviour
    under test and nothing below it.

    Parameters
    ----------
    root : Path
        Directory passed to the command line as its cleanup root.
    outcome : CleanupOutcome
        Scripted sweep result returned in place of a real cleanup.

    Returns
    -------
    int
        The exit status the command line reported.
    """
    with (
        mock.patch.object(cli, "load_config", return_value=DEFAULT_CONFIG),
        mock.patch.object(
            cli,
            "advisory_lock",
            return_value=contextlib.nullcontext(_UNCONTENDED_LOCK),
        ),
        mock.patch.object(cli, "cleanup_scan_roots", return_value=outcome),
    ):
        return cli.main([*flags, str(root)])


def make_target(root: Path, name: str, activity: float) -> Path:
    """Create one cache-marked target with a controlled artefact mtime.

    Parameters
    ----------
    root : Path
        Directory the project directory is created under.
    name : str
        Name of the project directory; also written as the artefact text.
    activity : float
        Modification time applied to the target's artefact.

    Returns
    -------
    Path
        The created target directory.
    """
    target = root / name / "target"
    target.mkdir(parents=True)
    (target / CACHEDIR_TAG).touch()
    artefact = target / "build.o"
    artefact.write_text(name, encoding="utf-8")
    os.utime(artefact, (activity, activity))
    return target


def synthetic_identity(root: Path, device: int = 1) -> FilesystemIdentity:
    """Return a stable synthetic filesystem identity for a test root.

    Parameters
    ----------
    root : Path
        Directory whose backing filesystem is identified.
    device : int
        Device number pairing the identity, distinct for separate mounts.

    Returns
    -------
    FilesystemIdentity
        The canonical root paired with the requested device number.
    """
    return FilesystemIdentity(root.resolve(), device)


def recorded_lines(text: str) -> list[dict[str, object]]:
    """Decode every prefixed telemetry line in ``text``.

    Parameters
    ----------
    text : str
        Captured output text holding ``EVENT_PREFIX`` telemetry lines.

    Returns
    -------
    list[dict[str, object]]
        Decoded records in the order they appeared.
    """
    return [
        json.loads(line.removeprefix(EVENT_PREFIX))
        for line in text.splitlines()
        if line.startswith(EVENT_PREFIX)
    ]
