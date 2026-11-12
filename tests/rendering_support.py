"""Scripted sweep records shared by the command-line rendering contracts.

The rendering tests drive the entry point with a hand-built outcome instead of a
real sweep, so the same two builders appear in more than one module. They live
here rather than in one test module so a later split cannot quietly fork the
scripted diagnostics and let two rendering contracts assert different records.
"""

import typing as typ

from rust_cleanup import CleanupOutcome
from rust_cleanup.diagnostics import DeletionFailure, FilesystemDiagnostics
from rust_cleanup.pressure import FilesystemIdentity, Mode

if typ.TYPE_CHECKING:
    from pathlib import Path

    from rust_cleanup.discovery import Omission


class OutcomeOptions(typ.TypedDict, total=False):
    """Sweep-level fields a rendering contract may override.

    Attributes
    ----------
    scanned : int
        Sweep-level count of scanned targets.
    deleted : int
        Sweep-level count of deleted targets.
    discovery_failed : bool
        Whether the outcome reports an incomplete directory scan.
    roots : tuple[Path, ...]
        Effective scan roots the sweep walked.
    omissions : tuple[Omission, ...]
        Candidates discovery declined, with their fixed reasons.
    """

    scanned: int
    deleted: int
    discovery_failed: bool
    roots: tuple[Path, ...]
    omissions: tuple[Omission, ...]


def filesystem_diagnostics(
    root: Path,
    *,
    planned: list[Path] | None = None,
    protected: list[Path] | None = None,
    failures: list[DeletionFailure] | None = None,
) -> FilesystemDiagnostics:
    """Build one filesystem record holding the supplied per-target detail.

    Parameters
    ----------
    root : Path
        Root directory whose synthetic identity the record describes.
    planned : list[Path] | None, default=None
        Targets counted as eligible and as planned deletions.
    protected : list[Path] | None, default=None
        Targets recorded as preserved.
    failures : list[DeletionFailure] | None, default=None
        Failed deletions recorded against the record.

    Returns
    -------
    FilesystemDiagnostics
        A pressure-mode record carrying the supplied detail.
    """
    return FilesystemDiagnostics(
        identity=FilesystemIdentity(root, 1),
        usage_before=81.0,
        usage_after=81.0,
        previous_mode=None,
        selected_mode=Mode.PRESSURE,
        cutoff_seconds=600,
        scanned=2,
        eligible=len(planned or []),
        deleted=len(planned or []),
        protected=len(protected or []),
        protected_targets=list(protected or []),
        planned_deletions=list(planned or []),
        deletion_failures=list(failures or []),
    )


def outcome(
    diagnostics: FilesystemDiagnostics,
    **overrides: typ.Unpack[OutcomeOptions],
) -> CleanupOutcome:
    """Wrap one filesystem record in the sweep's aggregate outcome.

    Parameters
    ----------
    diagnostics : FilesystemDiagnostics
        Per-filesystem record the outcome reports.
    **overrides : Unpack[OutcomeOptions]
        Sweep-level fields applied over the defaults of two scanned targets,
        two deletions, a complete scan, no roots, and no omissions.

    Returns
    -------
    CleanupOutcome
        The scripted aggregate outcome.
    """
    defaults: OutcomeOptions = {
        "scanned": 2,
        "deleted": 2,
        "discovery_failed": False,
        "roots": (),
        "omissions": (),
    }
    merged = {**defaults, **overrides}
    return CleanupOutcome(
        scanned=merged["scanned"],
        deleted=merged["deleted"],
        unresolved_pressure=False,
        filesystems=(diagnostics,),
        discovery_failed=merged["discovery_failed"],
        roots=merged["roots"],
        omissions=merged["omissions"],
    )
