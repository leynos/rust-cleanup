"""Discovery of Cargo build-output directories and their activity.

Discovery is deliberately conservative: a directory is reported only when it is
marked as a disposable cache or when it carries the layout Cargo itself writes,
and activity that cannot be established safely is reported as unknown rather
than as stale. Every directory that looks like a candidate but is declined is
recorded with a fixed reason, so an empty result can be told apart from a scan
that refused what it found. Traversal and marker inspections that fail are
counted rather than swallowed, so a caller can refuse to act on a scan it knows
to be incomplete.
"""

import dataclasses
import os
from pathlib import Path

from rust_cleanup import cargo_layout

SKIP_DIRS = frozenset({
    ".git",
    "node_modules",
    ".svn",
    ".hg",
    "__pycache__",
    ".pytest_cache",
    # Toolchain state lives here. It contains package caches that must never be
    # deleted, and walking it is both slow and pointless.
    ".cargo",
    ".rustup",
})
CACHEDIR_TAG = "CACHEDIR.TAG"
TARGET_DIR = "target"


@dataclasses.dataclass(frozen=True, slots=True)
class Omission:
    """One candidate directory the scan declined, and why.

    Attributes
    ----------
    path : pathlib.Path
        The directory that was considered.
    reason : str
        One of ``unmarked-no-evidence``, ``lookalike``, ``symlink``, or
        ``source-tree``.
    """

    path: Path
    reason: str


@dataclasses.dataclass(frozen=True, slots=True)
class DiscoveryScan:
    """Targets found below a root, what was declined, and the failures seen.

    Attributes
    ----------
    targets : tuple[pathlib.Path, ...]
        Build-output directories discovered below the root.
    failures : int
        Number of directories or subtrees that could not be inspected. Any
        non-zero count means the scan is incomplete, so the target list cannot
        be treated as the whole truth.
    omissions : tuple[Omission, ...]
        Candidate directories that were deliberately not reported, with fixed
        reason tokens. A deliberate decision never makes a scan incomplete.
    """

    targets: tuple[Path, ...]
    failures: int = 0
    omissions: tuple[Omission, ...] = ()

    @property
    def complete(self) -> bool:
        """Whether every directory below the root could be inspected."""
        return self.failures == 0


@dataclasses.dataclass(frozen=True, slots=True)
class ActivityInspection:
    """Newest artefact mtime of a target, or why that value is unknown.

    Attributes
    ----------
    newest_mtime : float | None
        Newest non-marker mtime, negative infinity for an empty target, or
        ``None`` when inspection could not establish it.
    uncertain : bool
        Whether the mtime is unknown. Kept separate from the value so callers
        can distinguish "established as empty" from "not established".
    """

    newest_mtime: float | None
    uncertain: bool


def should_skip_dir(dirname: str) -> bool:
    """Return true when a directory should be skipped during traversal.

    Parameters
    ----------
    dirname : str
        Directory name considered for exclusion from traversal.

    Returns
    -------
    bool
        Whether ``dirname`` is one of the known dependency or metadata
        directories excluded from traversal.
    """
    return dirname in SKIP_DIRS


def is_cache_dir(directory: Path) -> bool:
    """Return whether a directory is explicitly marked as a disposable cache.

    Parameters
    ----------
    directory : Path
        Directory checked for the cache marker directly inside it.

    Returns
    -------
    bool
        Whether the directory directly contains the cache marker.
    """
    return (directory / CACHEDIR_TAG).is_file()


def _is_candidate(name: str, files: list[str]) -> bool:
    """Return whether a directory could plausibly hold Cargo build output.

    ``target`` is Cargo's conventional name, and ``.rustc_info.json`` is the
    file rustc writes at the root of any ``--target-dir``, so a directory
    carrying either is worth classifying. Everything else is left alone, which
    keeps the scan focused on Rust output rather than every marked cache on the
    machine.

    Returns
    -------
    bool
        Whether the directory is worth classifying.
    """
    return name == TARGET_DIR or cargo_layout.CARGO_INFO in files


def _decline(path: Path, files: list[str], entries: set[str]) -> str | None:
    """Return the reason to decline ``path``, or ``None`` to accept it.

    The classification is shared with :func:`decline_reason`, so the rule that
    offers a directory and the rule that re-checks it before deletion cannot
    disagree. Source markers are refused before the cache marker is honoured:
    a checkout carrying ``CACHEDIR.TAG`` is still a checkout.

    Returns
    -------
    str | None
        The omission token, or ``None`` for an accepted target.

    Raises
    ------
    OSError
        If the layout cannot be established.
    """  # ruff: ignore[docstring-extraneous-exception] - classify raises it.
    return cargo_layout.classify(path, entries, marked=CACHEDIR_TAG in files)


def decline_reason(directory: Path) -> str | None:
    """Re-apply the acceptance rule to one already-discovered target.

    Discovery decides what may be deleted, and the tree can change between that
    decision and the deletion. Asking the same rule again immediately before
    removal is what turns "the evidence was there when we planned" into "the
    evidence is there now"; a directory whose marker or Cargo layout has gone,
    or which has become a symbolic link or a source tree, is preserved rather
    than removed.

    Parameters
    ----------
    directory : pathlib.Path
        Candidate directory about to be deleted.

    Returns
    -------
    str | None
        ``None`` when the directory is still accepted as build output,
        otherwise the fixed omission token that now declines it.

    Raises
    ------
    OSError
        If the directory cannot be read, so the caller treats an uninspectable
        candidate as protected rather than as absent evidence.
    """  # ruff: ignore[docstring-extraneous-exception] - classify raises it.
    return cargo_layout.classify(directory, marked=is_cache_dir(directory))


def scan_target_dirs(root_path: Path) -> DiscoveryScan:
    """Discover Cargo build-output directories and any inspection failures.

    A directory is reported when it is marked as a disposable cache, or when it
    carries the layout Cargo writes. Every other candidate is recorded as an
    omission with a fixed reason, so a small result can be distinguished from a
    scan that declined what it found. Traversal errors are counted instead of
    ignored, so a caller that deletes can first establish that it saw the whole
    tree.

    A directory holding ``.rustc_info.json`` but no Cargo profile is still
    descended into: the evidence may belong to a parent that wraps a real
    build, and refusing to look would hide it.

    Parameters
    ----------
    root_path : Path
        Root directory searched for build-output directories.

    Returns
    -------
    DiscoveryScan
        The discovered targets, the declined candidates, and the number of
        failed inspections.
    """
    targets: list[Path] = []
    omissions: list[Omission] = []
    failures = 0

    def collect_error(_error: OSError) -> None:
        """Count one traversal failure without disclosing its path.

        Parameters
        ----------
        _error : OSError
            Traversal failure reported through :func:`os.walk`.
        """
        nonlocal failures
        failures += 1

    for root, dirs, files in os.walk(root_path, onerror=collect_error):
        here = Path(root)
        if here != root_path and _is_candidate(here.name, files):
            entries = set(dirs) | set(files)
            try:
                reason = _decline(here, files, entries)
            except OSError:
                failures += 1
            else:
                if reason is None:
                    targets.append(here)
                    dirs.clear()
                else:
                    omissions.append(Omission(here, reason))
        linked, linked_failures = _linked_candidates(here, dirs)
        omissions.extend(linked)
        failures += linked_failures
        dirs[:] = [dirname for dirname in dirs if not should_skip_dir(dirname)]
    return DiscoveryScan(tuple(targets), failures, tuple(omissions))


def _linked_candidates(here: Path, dirs: list[str]) -> tuple[list[Omission], int]:
    """Report symbolic links that name build output, without following them.

    A link is never traversed, so it can neither widen the deletion boundary
    nor hide the fact that output exists outside the root. Inspection looks at
    the destination only to decide whether the omission is worth reporting.

    Returns
    -------
    tuple[list[Omission], int]
        One ``symlink`` omission for each linked candidate, and the number of
        links whose destination could not be inspected. That count belongs in
        the scan's failure total: a link that could not be read is an open
        question about the tree, not a decision to leave it alone.
    """
    found: list[Omission] = []
    failures = 0
    for dirname in dirs:
        link = here / dirname
        if not link.is_symlink():
            continue
        if dirname == TARGET_DIR:
            found.append(Omission(link, cargo_layout.OMIT_SYMLINK))
            continue
        holds_info = cargo_layout.holds_rustc_info(link)
        if holds_info is None:
            failures += 1
        elif holds_info:
            found.append(Omission(link, cargo_layout.OMIT_SYMLINK))
    return found, failures


def find_target_dirs(root_path: Path) -> list[Path]:
    """Discover every cache-marked target directory exactly once below a root.

    Symbolic links named ``target`` are ignored, so a link cannot redirect
    reclamation to a directory outside the requested root.

    This is the convenience form of :func:`scan_target_dirs`; it drops the
    failure count. Callers that act destructively on the result should use
    :func:`scan_target_dirs` and check :attr:`DiscoveryScan.complete`.

    Parameters
    ----------
    root_path : Path
        Root directory searched for cache-marked target directories.

    Returns
    -------
    list[pathlib.Path]
        All cache-marked target directories below the root.
    """
    return list(scan_target_dirs(root_path).targets)


def inspect_activity(directory: Path) -> ActivityInspection:
    """Inspect the newest non-marker mtime of a target directory.

    Empty targets use negative infinity, making them safely eligible for every
    positive cutoff. Any unreadable or disappearing path is reported as
    uncertain, so callers preserve it rather than guessing that it is stale.

    Parameters
    ----------
    directory : Path
        Target directory whose newest artefact mtime is measured.

    Returns
    -------
    ActivityInspection
        The newest artefact mtime, or an explicitly uncertain result.
    """
    newest = float("-inf")
    errors: list[OSError] = []

    def collect_error(error: OSError) -> None:
        """Record a traversal error for conservative handling after the walk."""
        errors.append(error)

    try:
        for root, _dirs, files in os.walk(directory, onerror=collect_error):
            for filename in files:
                if filename == CACHEDIR_TAG:
                    continue
                try:
                    newest = max(newest, (Path(root) / filename).stat().st_mtime)
                except OSError:
                    return ActivityInspection(None, uncertain=True)
    except OSError:
        return ActivityInspection(None, uncertain=True)
    if errors:
        return ActivityInspection(None, uncertain=True)
    return ActivityInspection(newest, uncertain=False)


def newest_mtime(target_path: Path) -> float | None:
    """Return newest file mtime, or ``None`` when inspection is unsafe.

    Parameters
    ----------
    target_path : Path
        Target directory whose newest artefact mtime is returned.

    Returns
    -------
    float | None
        Newest file mtime, negative infinity for an empty target, or ``None``
        when inspection cannot safely establish activity.
    """
    return inspect_activity(target_path).newest_mtime


def has_recent_files(directory: Path, cutoff_time: float) -> bool:
    """Return whether a non-marker file is newer than ``cutoff_time``.

    An uncertain inspection counts as recent: this boolean form cannot express
    "unknown", so it errs towards preserving the target. Callers that need the
    distinction should use :func:`inspect_activity`.

    Parameters
    ----------
    directory : Path
        Target directory whose non-marker files are inspected.

    cutoff_time : float
        Epoch seconds at or after which a file counts as recent.

    Returns
    -------
    bool
        Whether activity is at least as recent as the cutoff, or unknown.
    """
    inspection = inspect_activity(directory)
    newest = inspection.newest_mtime
    if inspection.uncertain or newest is None:
        return True
    return newest >= cutoff_time
