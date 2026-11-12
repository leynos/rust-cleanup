"""Recorded steps of one cleanup sweep.

Each step of a sweep that an operator can observe - inspecting build ownership,
loading and saving persisted modes, scanning the roots, and abandoning an
incomplete scan - emits one start and one finish event and nothing else. Keeping
those recordings together, over one :class:`SweepTrace`, leaves
:mod:`rust_cleanup.sweep` to sequence discovery, planning, and reclamation
without also carrying the shape of every telemetry span.

Nothing here deletes, plans, or decides: an abandoned sweep is a value this
module returns, and the caller stops.
"""

import dataclasses
import logging
import typing as typ

from rust_cleanup import discovery, sweep_discovery
from rust_cleanup.active_build import ActiveBuildDetector, ProcActiveBuildDetector
from rust_cleanup.diagnostics import CleanupOutcome, CleanupRun
from rust_cleanup.timing import Monotonic, Span

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

    from rust_cleanup.observability import EventPort
    from rust_cleanup.state import StateStore

_LOGGER = logging.getLogger(__name__)


def log_warning(message: str) -> None:
    """Write one bounded warning a query or usage re-read produced.

    Parameters
    ----------
    message : str
        Already-bounded warning text, which is the only argument logged.
    """
    _LOGGER.warning("%s", message)


@dataclasses.dataclass(frozen=True, slots=True)
class SweepTrace:
    """Telemetry ports and fixed fields shared by every recorded step.

    Attributes
    ----------
    events : EventPort
        Sink every step records its start and finish through.
    correlation : str
        Per-run token attached to each recorded event.
    monotonic : Monotonic
        Clock the recorded durations are measured with.
    dry_run : bool
        Whether the sweep plans rather than deletes, as reported to telemetry.
    """

    events: EventPort
    correlation: str
    monotonic: Monotonic
    dry_run: bool

    def span(self) -> Span:
        """Open a duration measurement over this trace's clock.

        Returns
        -------
        Span
            An open span closed by the step that opened it.
        """
        return Span(self.monotonic)


def active_build_factory(run: CleanupRun) -> cabc.Callable[[], ActiveBuildDetector]:
    """Return the factory a sweep consults before each build check.

    A factory is consulted per check, which is what lets the deletion pass take
    a fresh reading rather than trusting the sweep's opening snapshot. An
    injected detector is returned as itself, so a scripted answer stays
    scripted instead of being replaced by a fresh inspection of the host; only
    the default path builds a new :class:`ProcActiveBuildDetector` each time.

    Parameters
    ----------
    run : CleanupRun
        Sweep supplying the injected detector or its factory.

    Returns
    -------
    collections.abc.Callable[[], ActiveBuildDetector]
        A factory returning the detector for one check.
    """
    if run.detector_factory is not None:
        return run.detector_factory
    fixed = run.active_build_detector
    if fixed is not None:
        return lambda: fixed
    return ProcActiveBuildDetector


def inspect_active_builds(run: CleanupRun, trace: SweepTrace) -> ActiveBuildDetector:
    """Snapshot build ownership, recording bounded uncertainty.

    Parameters
    ----------
    run : CleanupRun
        Sweep whose injected detector is preferred over the default one.
    trace : SweepTrace
        Telemetry ports the inspection is recorded through.

    Returns
    -------
    ActiveBuildDetector
        The detector used for every candidate decision in this sweep.
    """
    span = trace.span()
    trace.events.emit("active_build_inspection", "start", correlation=trace.correlation)
    detector = active_build_factory(run)()
    uncertain = isinstance(detector, ProcActiveBuildDetector) and detector.uncertain
    trace.events.emit(
        "active_build_inspection",
        "finish",
        outcome="uncertain" if uncertain else "clean",
        duration_ms=span.close(),
        correlation=trace.correlation,
    )
    return detector


def load_state(store: StateStore, trace: SweepTrace) -> None:
    """Load persisted modes, recording their outcome.

    Parameters
    ----------
    store : StateStore
        Persisted-mode store whose load is recorded.
    trace : SweepTrace
        Telemetry ports the load is recorded through.
    """
    span = trace.span()
    trace.events.emit("state_load", "start", correlation=trace.correlation)
    warnings: list[str] = []
    result = store.load(warnings.append)
    for message in warnings:
        _LOGGER.warning("%s", message)
    trace.events.emit(
        "state_load",
        "finish",
        outcome=result.value,
        duration_ms=span.close(),
        correlation=trace.correlation,
    )


def save_state(store: StateStore, trace: SweepTrace) -> None:
    """Persist selected modes, recording the bounded outcome.

    Parameters
    ----------
    store : StateStore
        Persisted-mode store whose save is recorded.
    trace : SweepTrace
        Telemetry ports the save is recorded through.

    Raises
    ------
    OSError
        If the persisted modes cannot be written.
    """
    span = trace.span()
    trace.events.emit("state_save", "start", correlation=trace.correlation)
    outcome = "saved"
    try:
        store.save()
    except OSError:
        outcome = "failed"
        raise
    finally:
        trace.events.emit(
            "state_save",
            "finish",
            outcome=outcome,
            duration_ms=span.close(),
            correlation=trace.correlation,
        )


def scan_every_root(
    roots: cabc.Sequence[Path], trace: SweepTrace
) -> discovery.DiscoveryScan:
    """Scan every effective root, recording how complete the result is.

    Parameters
    ----------
    roots : collections.abc.Sequence[pathlib.Path]
        Effective scan roots to walk.
    trace : SweepTrace
        Telemetry ports the scan is recorded through.

    Returns
    -------
    discovery.DiscoveryScan
        The discovered targets and how many inspections failed.
    """
    span = trace.span()
    combined = sweep_discovery.scan_roots(roots)
    trace.events.emit(
        "discovery",
        "finish",
        outcome="complete" if combined.complete else "incomplete",
        scanned=len(combined.targets),
        failed=combined.failures,
        omitted=len(combined.omissions),
        roots=len(roots),
        duration_ms=span.close(),
        correlation=trace.correlation,
    )
    return combined


def abandon_sweep(
    roots: cabc.Sequence[Path],
    scan: discovery.DiscoveryScan,
    trace: SweepTrace,
    span: Span,
) -> CleanupOutcome:
    """Return an unresolved outcome for a scan that could not be completed.

    Nothing is deleted and no state is persisted, because reclamation decisions
    taken on a partial scan could preserve or remove the wrong targets.

    Parameters
    ----------
    roots : collections.abc.Sequence[pathlib.Path]
        Effective roots the abandoned sweep was asked to walk.
    scan : discovery.DiscoveryScan
        Incomplete scan whose discovered targets are reported.
    trace : SweepTrace
        Telemetry ports the abandonment is recorded through.
    span : Span
        Open sweep span this outcome closes with its duration.

    Returns
    -------
    CleanupOutcome
        An unresolved outcome that reports the incomplete discovery.
    """
    outcome = CleanupOutcome(
        scanned=len(scan.targets),
        deleted=0,
        unresolved_pressure=True,
        filesystems=(),
        discovery_failed=True,
        roots=tuple(roots),
        omissions=scan.omissions,
    )
    trace.events.emit(
        "sweep",
        "finish",
        outcome="failed",
        dry_run=trace.dry_run,
        duration_ms=span.close(),
        scanned=outcome.scanned,
        deleted=0,
        filesystems=0,
        omitted=outcome.omitted,
        roots=len(roots),
        correlation=trace.correlation,
    )
    return outcome
