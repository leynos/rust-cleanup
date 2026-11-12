"""Plans and recovery decisions for one filesystem of a cleanup sweep.

A plan is the value that crosses from planning into reclamation: the
per-filesystem record the reclaimer fills while it works, the candidates it may
delete in order, the cutoff instant they must precede, and the watermark that
stops it. Keeping the plan beside the record it carries, rather than inside the
module that sequences a sweep, lets the planner build it, the reclaimer execute
it, and the command line inspect it without any of them importing another.

Nothing here reads a filesystem or writes a log record: an unmeasurable
filesystem arrives as a value, and the plan that results from it deletes
nothing.
"""

import dataclasses
import typing as typ

from rust_cleanup.diagnostics import (
    PROTECT_USAGE_UNAVAILABLE,
    FilesystemDiagnostics,
    record_protected,
)
from rust_cleanup.pressure import FilesystemIdentity, Mode, mode_cutoff

if typ.TYPE_CHECKING:
    from rust_cleanup.config import CleanupConfig
    from rust_cleanup.reclaim import TargetCandidate


@dataclasses.dataclass(slots=True)
class FilesystemPlan:
    """Per-filesystem candidates and policy selected for one sweep.

    Attributes
    ----------
    diagnostics : FilesystemDiagnostics
        Per-filesystem record the reclaimer fills while it works.
    candidates : list[TargetCandidate]
        Eligible candidates in deterministic oldest-first deletion order.
    cutoff_time : float
        Cutoff instant a candidate's newest activity must precede to be eligible.
    watermark : int | None
        Recovery threshold that stops reclamation, or ``None`` in normal mode.
    usage_failed : bool
        Whether usage could not be measured, stopping deletion and mode writes.
    """

    diagnostics: FilesystemDiagnostics
    candidates: list[TargetCandidate]
    cutoff_time: float
    watermark: int | None
    usage_failed: bool = False


def filesystem_sort_key(identity: FilesystemIdentity) -> tuple[str, int]:
    """Return deterministic ordering for independently processed filesystems.

    Parameters
    ----------
    identity : FilesystemIdentity
        Filesystem whose canonical sort key is derived.

    Returns
    -------
    tuple[str, int]
        Canonical mount point and device identity for sorting.
    """
    return str(identity.mount_point), identity.device


def unmeasurable_plan(
    identity: FilesystemIdentity,
    previous: Mode | None,
    candidates: list[TargetCandidate],
    config: CleanupConfig,
) -> FilesystemPlan:
    """Preserve every candidate when filesystem usage cannot be measured.

    Each candidate is recorded with the reason it was kept, so an unreadable
    filesystem is distinguishable from one whose candidates were all protected
    for reasons of their own.

    Parameters
    ----------
    identity : FilesystemIdentity
        Filesystem whose usage could not be measured.
    previous : Mode | None
        Persisted mode to keep, or ``None`` when none was recorded.
    candidates : list[TargetCandidate]
        Candidate snapshots discovered on the filesystem.
    config : CleanupConfig
        Retention policies supplying the mode's inactivity cutoff.

    Returns
    -------
    FilesystemPlan
        A plan that deletes nothing and keeps any previously persisted mode.
    """
    selected = previous or Mode.NORMAL
    diagnostics = FilesystemDiagnostics(
        identity,
        0.0,
        0.0,
        previous,
        selected,
        mode_cutoff(selected, config),
        len(candidates),
    )
    for candidate in candidates:
        record_protected(diagnostics, candidate, PROTECT_USAGE_UNAVAILABLE)
    return FilesystemPlan(diagnostics, [], 0.0, None, usage_failed=True)


def is_recovered(plan: FilesystemPlan) -> bool:
    """Return whether a pressure recovery watermark has already been met.

    An unmeasurable filesystem never counts as recovered, so a failed
    post-deletion reading cannot report pressure as resolved.

    Returns
    -------
    bool
        Whether a pressure mode is safely below its recovery watermark.
    """
    if plan.usage_failed:
        return False
    return plan.watermark is not None and plan.diagnostics.usage_after < plan.watermark
