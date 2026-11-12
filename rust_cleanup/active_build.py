"""Linux active-build protection for cache reclamation.

The detector snapshots process ownership once per sweep. Anything it cannot
inspect safely is reported as uncertain, which protects the candidate rather
than risking a deletion under a live build.

Process ownership alone is not enough to see every build. A build may name its
output through ``CARGO_TARGET_DIR``, ``CARGO_BUILD_TARGET_DIR``, or
``--target-dir``, and it may already have been running when the snapshot was
taken. The detector therefore also reads both Cargo mechanisms that name an
output, so a build that redirects its output is matched against the location it
actually writes.

The detector answers one question: does a process in this snapshot own the
target? Cargo's advisory lock answers a different one, and is probed separately
by the planner and the reclaimer, which ask the detector first and then the
lock, recording a protection reason for whichever answered.

Reading the process table itself lives in :mod:`rust_cleanup.proc_inspection`;
this module holds the snapshot and turns it into a protection decision.
"""

import typing as typ
from pathlib import Path

from rust_cleanup.proc_inspection import (
    ProcessLocation,
    ProcInspectionError,
    is_build_process,
    read_process_command,
    read_process_location,
)

__all__ = [
    "ActiveBuildDetector",
    "ProcActiveBuildDetector",
    "ProcInspectionError",
]

# ``Path.resolve`` reports a symlink loop as ``RuntimeError`` on CPython 3.12 and
# an unreadable filesystem as ``OSError``. Both leave a target uninspectable,
# and both must become conservative uncertainty rather than an escaping
# exception.
_RESOLUTION_FAILURES = (OSError, RuntimeError)


class ActiveBuildDetector(typ.Protocol):
    """Answers whether deleting a target could disrupt a live build."""

    def is_active(self, target_path: Path) -> bool:
        """Return whether ``target_path`` belongs to an active or uncertain build.

        Parameters
        ----------
        target_path : Path
            Target directory checked for active or uncertain build ownership.

        Returns
        -------
        bool
            Whether an active or uncertain build may own the target.
        """


def _worktree_for(target_path: Path) -> Path:
    """Return the nearest Lody worktree root, or the target's parent.

    Returns
    -------
    Path
        The worktree containing the target when its path has the Lody shape.
    """
    for ancestor in target_path.resolve().parents:
        if ancestor.parent.name == "worktrees":
            return ancestor
    return target_path.parent.resolve()


class ProcActiveBuildDetector:
    """Snapshot Linux build process ownership once for an entire sweep.

    The snapshot reads ``comm``, ``cwd``, ``cmdline``, and ``environ`` entries
    below ``proc_root`` to locate live build processes and the output
    directories they name. A process that cannot be inspected safely leaves the
    snapshot uncertain rather than raising, so protection stays conservative.

    The snapshot covers only what a process names. A target with no owning
    process is not thereby free to delete: the callers probe Cargo's advisory
    lock as well, and only a definite "free" from that probe lets a candidate
    proceed.
    """

    def __init__(self, proc_root: Path = Path("/proc")) -> None:
        """Inspect ``proc_root`` once, retaining conservative uncertainty.

        Parameters
        ----------
        proc_root : Path
            Process table root read for the entries of live build processes,
            defaulting to ``/proc``.
        """
        self._uncertain = False
        self._locations: list[ProcessLocation] = []
        self._scan(proc_root)

    @property
    def uncertain(self) -> bool:
        """Whether inspection was incomplete and protection is conservative."""
        return self._uncertain

    def _scan(self, proc_root: Path) -> None:
        """Collect work directories and absolute command paths for build processes."""
        try:
            processes = tuple(proc_root.iterdir())
        except OSError:
            self._uncertain = True
            return
        for process in processes:
            self._record_process(process)

    def _record_process(self, process: Path) -> None:
        """Append a build process's locations or record uncertain ownership."""
        if not process.name.isdecimal():
            return
        try:
            command = read_process_command(process)
            if command is None or not is_build_process(command):
                return
            location = read_process_location(process)
        except ProcInspectionError:
            self._uncertain = True
            return
        if location is not None:
            self._locations.append(location)

    def is_active(self, target_path: Path) -> bool:
        """Conservatively report whether a snapshot process owns this target.

        An uninspectable snapshot or an unresolvable target protects, because
        neither can rule a build out.

        Parameters
        ----------
        target_path : Path
            Target directory matched against the snapshot's build locations.

        Returns
        -------
        bool
            Whether an active or uncertain build may own the target.
        """
        if self._uncertain:
            return True
        try:
            resolved = target_path.resolve()
            roots = (resolved, _worktree_for(target_path))
        except _RESOLUTION_FAILURES:
            return True
        return any(location.owns(resolved, roots) for location in self._locations)
