"""Observable records for one pressure-aware cleanup sweep.

The sweep engine describes what it observed with these bounded value types, and
:mod:`rust_cleanup.plans` builds the plan that carries a record from planning
into reclamation. Keeping the two apart lets the planner build a plan, the
reclaimer execute it, and the command line inspect the record without any of
them importing the module that sequences a sweep.

Presentation stays out of this layer: the sweep returns the protected targets,
planned deletions, and deletion failures it observed, the command line renders
them, and :func:`~rust_cleanup.observability.emit_diagnostics` renders the
per-filesystem record. Usage is read through :mod:`rust_cleanup.usage`, which
reports an unmeasurable filesystem through the warning sink its caller
supplied, so nothing here writes a log record of its own. Every annotation that
names a port is resolved statically, so this module imports no infrastructure
at run time.
"""

import collections
import collections.abc as cabc
import dataclasses
import typing as typ

from rust_cleanup.config import DEFAULT_CONFIG, CleanupConfig

if typ.TYPE_CHECKING:
    from pathlib import Path

    from rust_cleanup.active_build import ActiveBuildDetector
    from rust_cleanup.discovery import Omission
    from rust_cleanup.filesystem import DirectoryDeleter, IdentityGetter, UsageGetter
    from rust_cleanup.observability import EventPort
    from rust_cleanup.pressure import FilesystemIdentity, Mode
    from rust_cleanup.reclaim import ActivityGetter, TargetCandidate
    from rust_cleanup.state import StateStore
    from rust_cleanup.timing import Monotonic


@dataclasses.dataclass(frozen=True, slots=True)
class DeletionFailure:
    """One candidate that could not be deleted, with its bounded reason.

    Attributes
    ----------
    path : pathlib.Path
        Target directory that could not be removed.
    message : str
        The :class:`~rust_cleanup.errors.CleanupError` text describing why.
    """

    path: Path
    message: str


@dataclasses.dataclass(frozen=True, slots=True)
class ProtectedTarget:
    """One preserved candidate and the fixed reason it was kept.

    Attributes
    ----------
    path : pathlib.Path
        Candidate directory that was preserved.
    reason : str
        One of ``active-build``, ``cargo-lock``, ``unknown-activity``,
        ``revalidation-changed``, ``recent-activity``, ``evidence-lost``, or
        ``usage-unavailable``.
    """

    path: Path
    reason: str


@dataclasses.dataclass(slots=True)
class FilesystemDiagnostics:
    """Observable outcome of processing candidates on one filesystem.

    The counter fields stay the aggregate view; the record fields preserve
    enough per-target detail for the command line to render what happened
    without the sweep printing anything itself.

    Attributes
    ----------
    identity : FilesystemIdentity
        Mounted filesystem this record describes.
    usage_before : float
        Used-capacity percentage before reclamation, or zero when unmeasurable.
    usage_after : float
        Used-capacity percentage after the most recent successful deletion.
    previous_mode : Mode | None
        Persisted mode at sweep start, or ``None`` when none was stored.
    selected_mode : Mode
        Retention mode chosen for this sweep.
    cutoff_seconds : int
        Inactivity cutoff in seconds for the selected mode.
    scanned : int
        Number of candidate directories discovered on the filesystem.
    protected : int
        Number of candidates preserved rather than deleted.
    eligible : int
        Number of stale, unprotected candidates planned for deletion.
    deleted : int
        Number of candidates deleted, or planned in a dry run.
    failed : int
        Number of deletions that failed with a CleanupError.
    exit_reached : bool
        Whether reclamation met the recovery watermark, resolving pressure.
    unknown_activity : int
        Preserved candidates whose activity could not be established.
    protected_targets : list[pathlib.Path]
        Paths of every preserved candidate, in preservation order.
    planned_deletions : list[pathlib.Path]
        Candidate paths a dry run would have deleted.
    deletion_failures : list[DeletionFailure]
        Deletion failures with their bounded reasons.
    protection_reasons : collections.Counter[str]
        How many candidates were preserved for each fixed reason token.
    protected_details : list[ProtectedTarget]
        Every preserved candidate with the reason it was kept.
    """

    identity: FilesystemIdentity
    usage_before: float
    usage_after: float
    previous_mode: Mode | None
    selected_mode: Mode
    cutoff_seconds: int
    scanned: int
    protected: int = 0
    eligible: int = 0
    deleted: int = 0
    failed: int = 0
    exit_reached: bool = True
    unknown_activity: int = 0
    protected_targets: list[Path] = dataclasses.field(default_factory=list)
    planned_deletions: list[Path] = dataclasses.field(default_factory=list)
    deletion_failures: list[DeletionFailure] = dataclasses.field(default_factory=list)
    protection_reasons: collections.Counter[str] = dataclasses.field(
        default_factory=collections.Counter
    )
    protected_details: list[ProtectedTarget] = dataclasses.field(default_factory=list)


@dataclasses.dataclass(frozen=True, slots=True)
class CleanupOutcome:
    """Aggregate result returned by one complete cleanup sweep.

    Attributes
    ----------
    scanned : int
        Number of target directories discovered below the root.
    deleted : int
        Number of targets deleted, or planned in a dry run.
    unresolved_pressure : bool
        Whether pressure was left unresolved, including a failed scan.
    filesystems : tuple[FilesystemDiagnostics, ...]
        One record per processed filesystem, in deterministic order.
    discovery_failed : bool
        Whether an incomplete directory scan ended the sweep before it acted.
    roots : tuple[pathlib.Path, ...]
        Effective scan roots this sweep walked.
    omitted : int
        Candidates discovery declined, with reasons in ``omissions``.
    omissions : tuple[Omission, ...]
        Each declined candidate and its fixed reason token.
    """

    scanned: int
    deleted: int
    unresolved_pressure: bool
    filesystems: tuple[FilesystemDiagnostics, ...]
    discovery_failed: bool = False
    roots: tuple[Path, ...] = ()
    omissions: tuple[Omission, ...] = ()

    @property
    def omitted(self) -> int:
        """How many candidates discovery declined to offer for deletion."""
        return len(self.omissions)

    @property
    def protection_reasons(self) -> collections.Counter[str]:
        """How many candidates were preserved for each fixed reason token."""
        total: collections.Counter[str] = collections.Counter()
        for filesystem in self.filesystems:
            total.update(filesystem.protection_reasons)
        return total

    def __iter__(self) -> cabc.Iterator[int]:
        """Provide compatibility with historical ``scanned, deleted`` unpacking.

        Yields
        ------
        int
            The scanned count followed by the deleted count.
        """
        yield self.scanned
        yield self.deleted

    @property
    def protected_targets(self) -> tuple[Path, ...]:
        """Every preserved target across all filesystems, in order."""
        return tuple(
            path
            for filesystem in self.filesystems
            for path in filesystem.protected_targets
        )

    @property
    def planned_deletions(self) -> tuple[Path, ...]:
        """Every dry-run planned deletion across all filesystems."""
        return tuple(
            path
            for filesystem in self.filesystems
            for path in filesystem.planned_deletions
        )

    @property
    def deletion_failures(self) -> tuple[DeletionFailure, ...]:
        """Every failed deletion across all filesystems, in order."""
        return tuple(
            failure
            for filesystem in self.filesystems
            for failure in filesystem.deletion_failures
        )


@dataclasses.dataclass(frozen=True, slots=True)
class CleanupRun:
    """Dependencies and options for one injected cleanup sweep.

    Every adapter and port defaults to ``None`` so this value type stays free of
    infrastructure imports; the sweep wires the concrete adapters when a caller
    does not inject its own, and the command line wires telemetry and the
    per-run correlation token at the composition root.

    Attributes
    ----------
    config : CleanupConfig
        Retention policy for the sweep, defaulting to ``DEFAULT_CONFIG``.
    dry_run : bool
        Whether deletion is planned and reported instead of performed.
    state_store : StateStore | None
        Persisted-mode store, or ``None`` to use a default store.
    active_build_detector : ActiveBuildDetector | None
        Live-build protection port, or ``None`` to use the default detector.
    detector_factory : collections.abc.Callable[[], ActiveBuildDetector] | None
        Builds the detector consulted immediately before each deletion. ``None``
        builds a fresh default detector, except that an injected
        ``active_build_detector`` is returned as itself so a scripted answer is
        not replaced by a fresh inspection of the host.
    usage_getter : UsageGetter | None
        Filesystem usage port, or ``None`` to measure through the default
        adapter.
    identity_getter : IdentityGetter | None
        Filesystem identity port, or ``None`` to identify through the default
        adapter.
    activity_getter : ActivityGetter | None
        Target activity port, or ``None`` to read through the default adapter.
    delete_directory : DirectoryDeleter | None
        Reclamation port, or ``None`` to delete through the default adapter.
    now : float | None
        Epoch second used to derive the inactivity cutoff, or ``None`` to read
        the wall clock once at sweep start.
    monotonic : Monotonic | None
        Clock the telemetry durations are measured with, or ``None`` to measure
        them with :func:`time.monotonic`. Keyword-only, so a positional caller
        cannot land another port in this slot.
    events : EventPort | None
        Telemetry sink, or ``None`` to emit through the default emitter.
    correlation : str | None
        Per-run token attached to telemetry, or ``None`` to mint one for the
        sweep.
    """

    config: CleanupConfig = DEFAULT_CONFIG
    dry_run: bool = False
    state_store: StateStore | None = None
    active_build_detector: ActiveBuildDetector | None = None
    detector_factory: cabc.Callable[[], ActiveBuildDetector] | None = None
    usage_getter: UsageGetter | None = None
    identity_getter: IdentityGetter | None = None
    activity_getter: ActivityGetter | None = None
    delete_directory: DirectoryDeleter | None = None
    now: float | None = None
    # Keyword-only, so a positional caller cannot land an event port here.
    monotonic: Monotonic | None = dataclasses.field(default=None, kw_only=True)
    events: EventPort | None = None
    correlation: str | None = None


# Fixed reasons a candidate is preserved. Protection is the safety-critical
# path, so the reasons stay a closed vocabulary that can be counted and logged
# without ever carrying a path.
PROTECT_ACTIVE_BUILD = "active-build"
PROTECT_CARGO_LOCK = "cargo-lock"
PROTECT_UNKNOWN_ACTIVITY = "unknown-activity"
PROTECT_REVALIDATION_CHANGED = "revalidation-changed"
PROTECT_RECENT = "recent-activity"
PROTECT_EVIDENCE_LOST = "evidence-lost"
PROTECT_USAGE_UNAVAILABLE = "usage-unavailable"


def record_protected(
    diagnostics: FilesystemDiagnostics,
    candidate: TargetCandidate,
    reason: str = PROTECT_UNKNOWN_ACTIVITY,
) -> None:
    """Record one preserved target with the reason it was kept.

    A candidate whose activity could not be established is counted twice: once
    among the preserved targets, and once among those preserved because what
    they contain could not be established at all.

    Parameters
    ----------
    diagnostics : FilesystemDiagnostics
        Per-filesystem record the preservation is appended to.
    candidate : TargetCandidate
        The candidate that was preserved rather than deleted.
    reason : str, default=PROTECT_UNKNOWN_ACTIVITY
        Fixed reason token for the preservation.
    """
    diagnostics.protected += 1
    diagnostics.protected_targets.append(candidate.path)
    diagnostics.protected_details.append(ProtectedTarget(candidate.path, reason))
    diagnostics.protection_reasons[reason] += 1
    if candidate.last_activity is None:
        diagnostics.unknown_activity += 1
