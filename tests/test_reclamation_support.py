"""Scaffolding shared by the reclamation protection contracts.

Two modules script the same deletion pass: one pins the order of the
pre-deletion checks and the reason each failed check records, the other pins
the evidence and scope failures. They share one candidate builder and one
reclaimer harness here so a later split cannot quietly fork the plan layout and
let the two modules assert against different deletion passes.

This follows the suite's established naming for helper modules that are
collected but contribute no contracts of their own, as
``test_yaml_task_utils.py`` does for the role tests.
"""

import dataclasses
import enum
import typing as typ

from conftest import (
    InactiveBuilds,
    RecordingDeleter,
    RecordingSink,
    make_target,
    synthetic_identity,
)
from proc_support import UnheldLocks

from rust_cleanup.diagnostics import FilesystemDiagnostics
from rust_cleanup.observability import EventEmitter
from rust_cleanup.plans import FilesystemPlan
from rust_cleanup.pressure import Mode
from rust_cleanup.reclaim import ActivityGetter, TargetCandidate
from rust_cleanup.reclamation import Reclaimer
from rust_cleanup.state import StateStore

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

    from rust_cleanup.active_build import ActiveBuildDetector
    from rust_cleanup.cargo_locks import LockProbe

# Every case rests on this cutoff: a candidate whose newest activity is 100
# seconds old is stale against it, and one at 50 seconds is not.
CUTOFF = 1000.0
_CORRELATION = "run_reclamation_test"

# Named so the default reads as a value rather than constructing a probe at
# import time, which ruff's B008 rejects.
_FREE_LOCKS = UnheldLocks()


class ReclaimCase(typ.NamedTuple):
    """Script the collaborators one reclamation case observes.

    The fields are grouped rather than repeated as a wide argument list at each
    call site, because they all describe the same deletion decision.

    Attributes
    ----------
    current_activity : float | None
        Activity the immediate re-check observes, or ``None`` when unreadable.
    detector_factory : collections.abc.Callable[[], ActiveBuildDetector]
        Builds the detector consulted before the deletion.
    lock_probe : LockProbe
        Probe reporting the candidate's Cargo lock state.
    roots : tuple[pathlib.Path, ...] | None
        Effective roots, or ``None`` for the candidate's filesystem root.
    activity_getter : collections.abc.Callable[[pathlib.Path], float | None] | None
        Overrides ``current_activity`` for a case that refuses to be read.
    """

    current_activity: float | None
    detector_factory: cabc.Callable[[], ActiveBuildDetector] = InactiveBuilds
    lock_probe: LockProbe = _FREE_LOCKS
    roots: tuple[Path, ...] | None = None
    activity_getter: cabc.Callable[[Path], float | None] | None = None


def diagnostic_record(root: Path) -> FilesystemDiagnostics:
    """Return a per-filesystem record for one planned candidate.

    Parameters
    ----------
    root : Path
        Directory whose backing filesystem the record describes.

    Returns
    -------
    FilesystemDiagnostics
        A record for a single scanned candidate under pressure mode.
    """
    return FilesystemDiagnostics(
        identity=synthetic_identity(root),
        usage_before=85.0,
        usage_after=85.0,
        previous_mode=None,
        selected_mode=Mode.PRESSURE,
        cutoff_seconds=int(CUTOFF),
        scanned=1,
    )


def plan_for(
    root: Path, *, last_activity: float, cutoff_time: float = CUTOFF
) -> FilesystemPlan:
    """Build a one-candidate plan examined against ``cutoff_time``.

    The candidate is created as real, cache-marked build output: the reclaimer
    re-applies discovery's acceptance rule immediately before deleting, so a
    path that never existed could not reach the deletion under test.

    Parameters
    ----------
    root : Path
        Directory whose backing filesystem the plan describes.
    last_activity : float
        Activity snapshot discovery recorded for the candidate.
    cutoff_time : float, default=CUTOFF
        Instant the candidate's activity must precede to remain eligible.

    Returns
    -------
    FilesystemPlan
        A plan whose only candidate has the requested discovery snapshot.
    """
    target = make_target(root, "crate", 1)
    candidate = TargetCandidate(target, synthetic_identity(root), last_activity)
    return FilesystemPlan(diagnostic_record(root), [candidate], cutoff_time, None)


def reclaim(plan: FilesystemPlan, case: ReclaimCase) -> RecordingDeleter:
    """Run reclamation over ``plan`` with the collaborators ``case`` scripts.

    Parameters
    ----------
    plan : FilesystemPlan
        Plan the reclaimer works through.
    case : ReclaimCase
        Activity, detector, lock probe, and scope the reclaimer observes.

    Returns
    -------
    RecordingDeleter
        The deletion port, recording every candidate it was asked to remove.
    """
    deleter = RecordingDeleter()
    mount_point = plan.diagnostics.identity.mount_point
    activity_getter = case.activity_getter or constant_activity(case.current_activity)
    Reclaimer(
        dry_run=False,
        store=StateStore(mount_point / "state.json"),
        usage_getter=lambda _path: 1.0,
        activity_getter=activity_getter,
        delete_directory=deleter,
        detector_factory=case.detector_factory,
        lock_probe=case.lock_probe,
        roots=(mount_point,) if case.roots is None else case.roots,
        events=EventEmitter(sink=RecordingSink()),
        correlation=_CORRELATION,
        monotonic=lambda: 0.0,
        warn=lambda _message: None,
    ).reclaim(plan)
    return deleter


def constant_activity(activity: float | None) -> ActivityGetter:
    """Return a re-read port that always reports ``activity``.

    Parameters
    ----------
    activity : float | None
        Activity the port reports, or ``None`` for an unreadable candidate.

    Returns
    -------
    ActivityGetter
        A port reporting the scripted activity for every candidate.
    """

    def read(_directory: Path) -> float | None:
        """Report the scripted activity for any candidate.

        Returns
        -------
        float | None
            The scripted modification time, or None for unknown activity.
        """
        return activity

    return read


def one_reason(diagnostics: FilesystemDiagnostics, expected: str) -> None:
    """Assert ``diagnostics`` recorded exactly one protection, with ``expected``.

    Parameters
    ----------
    diagnostics : FilesystemDiagnostics
        Record the reclamation pass wrote its protections into.
    expected : str
        The single fixed reason the pass must have recorded.
    """
    reasons = diagnostics.protection_reasons
    assert reasons[expected] == 1, (
        f"the reason {expected!r} was not recorded exactly once: {reasons!r}"
    )
    assert diagnostics.protected_details[0].reason == expected, (
        "the protected detail carried a different reason"
    )


class ProtectEverything:
    """A detector that reports every candidate as owned by a live build."""

    def is_active(self, target_path: Path) -> bool:
        """Return true for every path.

        Parameters
        ----------
        target_path : Path
            Target path the caller asks about; this detector ignores it.

        Returns
        -------
        bool
            Always ``True``.
        """
        return True


class ScriptedLock(enum.Enum):
    """The lock states a case can script without a real lock file.

    ``HELD`` is direct evidence that a build owns the candidate. ``UNKNOWN`` is
    a probe that could not establish the state, which the reclaimer must treat
    as active rather than as free.
    """

    HELD = enum.auto()
    UNKNOWN = enum.auto()


# The answer each scripted state reports. A mapping keeps the probe free of a
# method that only forwards its own field, which DF12's
# ``trivial-attribute-wrapper`` rejects.
_LOCK_ANSWERS: dict[ScriptedLock, bool | None] = {
    ScriptedLock.HELD: True,
    ScriptedLock.UNKNOWN: None,
}


@dataclasses.dataclass(frozen=True, slots=True)
class FixedLock:
    """A lock probe that reports the same scripted state for every candidate.

    Attributes
    ----------
    state : ScriptedLock
        The state every candidate reports.
    """

    state: ScriptedLock

    def holds_lock(self, candidate: Path) -> bool | None:
        """Report the scripted lock state for ``candidate``.

        Parameters
        ----------
        candidate : Path
            Build-output directory the caller asks about; ignored, because the
            case has already fixed the state it wants observed.

        Returns
        -------
        bool | None
            ``True`` for a held lock, or ``None`` for an unestablished one.
        """
        return _LOCK_ANSWERS[self.state]


class RefusingLocks:
    """A lock probe that fails the test if it is consulted at all."""

    def holds_lock(self, candidate: Path) -> bool:
        """Raise instead of answering, naming the check that should have won.

        Parameters
        ----------
        candidate : Path
            Build-output directory the caller asks about; ignored.

        Raises
        ------
        AssertionError
            Always, because a preceding check already settled the candidate.
        """
        message = "the lock probe ran after an earlier check already protected it"
        raise AssertionError(message)
