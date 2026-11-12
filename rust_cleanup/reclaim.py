"""Reclamation helpers for ordered, revalidated target deletion.

These helpers keep the safety decisions local: which candidate is safe to
remove, which order is deterministic, and whether a candidate is still stale at
the moment of deletion.

Re-reading activity and removing a directory are operating-system operations,
so both arrive as injected ports rather than as imports: the sweep wires
:func:`rust_cleanup.discovery.newest_mtime` and
:func:`rust_cleanup.filesystem.delete_directory` for real runs, and tests wire
fakes without patching module attributes.
"""

import dataclasses
import typing as typ

from rust_cleanup.diagnostics import (
    PROTECT_RECENT,
    PROTECT_REVALIDATION_CHANGED,
    PROTECT_UNKNOWN_ACTIVITY,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

    from rust_cleanup.pressure import FilesystemIdentity


class ActivityGetter(typ.Protocol):
    """Re-read the newest artefact mtime of a target directory.

    Implementations return ``None`` when activity cannot be established
    safely; callers then treat the target as not stale rather than guessing.
    """

    def __call__(self, directory: Path, /) -> float | None:
        """Return the newest non-marker mtime, or ``None`` when unknown.

        Parameters
        ----------
        directory : Path
            Target directory whose activity is re-read.

        Returns
        -------
        float | None
            Newest artefact mtime, or ``None`` when inspection is unsafe.
        """


@dataclasses.dataclass(frozen=True, slots=True)
class TargetCandidate:
    """A cache-marked target and its activity snapshot.

    Attributes
    ----------
    path : Path
        Canonical path of the discovered target directory.
    filesystem : FilesystemIdentity
        Mounted filesystem that backs the candidate.
    last_activity : float | None
        Newest non-marker mtime, or ``None`` when inspection was unsafe.
    """

    path: Path
    filesystem: FilesystemIdentity
    last_activity: float | None


def candidate_order(candidate: TargetCandidate) -> tuple[float, str]:
    """Return deterministic oldest-first ordering for an eligible candidate.

    Parameters
    ----------
    candidate : TargetCandidate
        Candidate whose activity determines the sort position.

    Returns
    -------
    tuple[float, str]
        Activity time and canonical path for a deterministic sort.
    """
    activity = candidate.last_activity
    return (float("inf") if activity is None else activity), str(candidate.path)


def is_eligible(candidate: TargetCandidate, cutoff_time: float) -> bool:
    """Return whether a discovery snapshot predates the effective cutoff.

    Parameters
    ----------
    candidate : TargetCandidate
        Discovery snapshot whose activity is compared.
    cutoff_time : float
        Wall-clock time the candidate's newest activity must precede.

    Returns
    -------
    bool
        Whether discovery established inactivity before the cutoff.
    """
    return candidate.last_activity is not None and candidate.last_activity < cutoff_time


def revalidation_failure(
    candidate: TargetCandidate,
    cutoff_time: float,
    activity_getter: ActivityGetter,
) -> str | None:
    """Return why a candidate must be preserved, or ``None`` to delete it.

    The distinction matters to an operator: a build that started between
    discovery and deletion is a race, while output that merely grew old beyond
    the cutoff is routine retention. Both preserve the candidate.

    Parameters
    ----------
    candidate : TargetCandidate
        Discovery snapshot whose activity must be reconfirmed.
    cutoff_time : float
        Inactivity cutoff the candidate must still predate.
    activity_getter : ActivityGetter
        Port that re-reads the target's newest artefact mtime.

    Returns
    -------
    str | None
        A fixed protection reason, or ``None`` when deletion may proceed.
    """
    activity = activity_getter(candidate.path)
    if activity is None:
        return PROTECT_UNKNOWN_ACTIVITY
    if activity != candidate.last_activity:
        return PROTECT_REVALIDATION_CHANGED
    if activity >= cutoff_time:
        return PROTECT_RECENT
    return None
