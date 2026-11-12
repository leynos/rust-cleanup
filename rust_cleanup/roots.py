"""Selection of the scan roots a sweep walks.

The sweep deletes only inside the directories chosen here, so this module
makes the whole boundary explicit. It applies the documented precedence,
canonicalizes every root, and reports the roots it could not use instead of
silently narrowing the scan. The expression language the configuration file
uses lives in :mod:`rust_cleanup.scan_root_expressions`.
"""

import dataclasses
import stat
import typing as typ
from pathlib import Path

from rust_cleanup.scan_root_expressions import builtin_scan_roots

if typ.TYPE_CHECKING:
    import collections.abc as cabc

# Reasons are fixed tokens so they can be reported to an operator and counted
# without ever carrying a path of their own.
_ROOT_MISSING = "missing"
_ROOT_NOT_DIRECTORY = "not-directory"
_ROOT_DUPLICATE = "duplicate"
_ROOT_NESTED = "nested"
_ROOT_UNREADABLE = "unreadable"


class ScanRootError(ValueError):
    """Raised when an explicitly requested scan root cannot be used."""


@dataclasses.dataclass(frozen=True, slots=True)
class SkippedRoot:
    """One configured root that will not be scanned, and why.

    Attributes
    ----------
    path : pathlib.Path
        The root as it was resolved when it was rejected.
    reason : str
        One of ``missing``, ``not-directory``, ``duplicate``, ``nested``, or
        ``unreadable``.
    builtin : bool
        Whether the root came from the built-in defaults rather than from the
        operator. A missing built-in is an absence the host simply has, so a
        timed run may withhold it; every other skip is news on every run.
    """

    path: Path
    reason: str
    builtin: bool = False

    @property
    def is_absent_default(self) -> bool:
        """Report whether this skip is a default directory the host lacks.

        Returns
        -------
        bool
            Whether the root is a built-in default that does not exist here.
        """
        return self.builtin and self.reason == _ROOT_MISSING


@dataclasses.dataclass(frozen=True, slots=True)
class RootSelection:
    """The roots one sweep will scan and the ones it declined.

    Attributes
    ----------
    roots : tuple[pathlib.Path, ...]
        Ordered, canonical, duplicate-free roots with no root nested in
        another.
    skipped : tuple[SkippedRoot, ...]
        Configured or built-in roots that were not scanned, with fixed reason
        tokens.
    inspection_failed : bool
        Whether any root's status could not be read. A caller must treat that
        as an incomplete scan rather than as a narrower one.
    """

    roots: tuple[Path, ...]
    skipped: tuple[SkippedRoot, ...]
    inspection_failed: bool = False


def _resolve(path: Path) -> Path:
    """Return the canonical form of one root without requiring it to exist.

    Returns
    -------
    pathlib.Path
        The canonical root path.

    Raises
    ------
    RuntimeError
        If the path contains a symbolic link loop. CPython raises this instead
        of returning a path, so callers treat it as an uninspectable root.
    """  # ruff: ignore[docstring-extraneous-exception] - resolve raises it.
    return path.resolve(strict=False)


def _unusable_reason(path: Path) -> str | None:
    """Return the fixed reason a root cannot be scanned, or ``None``.

    A directory reached through a symbolic link is usable, matching the
    command line's historic behaviour. A path below a regular file is treated as
    absent, since it cannot exist as a directory. A status read that fails for
    any other reason is left to the caller, which reports it as an inspection
    failure rather than as a narrower scan.

    Returns
    -------
    str | None
        A fixed reason token, or ``None`` for a usable directory.
    """
    try:
        status = path.stat()
    except FileNotFoundError, NotADirectoryError:
        # A root below a regular file is as absent as a root that was never
        # created, and skipping it is what keeps the other roots scanable.
        return _ROOT_MISSING
    return None if stat.S_ISDIR(status.st_mode) else _ROOT_NOT_DIRECTORY


def _classify(
    candidates: cabc.Iterable[Path], *, builtin: bool = False
) -> tuple[list[Path], list[SkippedRoot], bool]:
    """Resolve roots in order, reporting unusable or repeated ones.

    Parameters
    ----------
    candidates : collections.abc.Iterable[pathlib.Path]
        Root paths to resolve, in declaration order.
    builtin : bool, default=False
        Whether these are the built-in defaults, recorded on each skip so the
        command line can tell a host's absent default from an operator's.

    Returns
    -------
    tuple[list[Path], list[SkippedRoot], bool]
        Usable roots in declaration order, skipped roots with reasons, and
        whether any status read failed.
    """
    usable: list[Path] = []
    skipped: list[SkippedRoot] = []
    inspection_failed = False

    def _skip(path: Path, reason: str) -> None:
        """Record one declined root with its provenance."""
        skipped.append(SkippedRoot(path, reason, builtin=builtin))

    for candidate in candidates:
        try:
            resolved = _resolve(candidate)
        except RuntimeError:
            # A symlink loop has no canonical form, so the candidate is
            # reported as written rather than as a path that cannot exist.
            inspection_failed = True
            _skip(candidate, _ROOT_UNREADABLE)
            continue
        if resolved in usable:
            _skip(resolved, _ROOT_DUPLICATE)
            continue
        try:
            reason = _unusable_reason(resolved)
        except OSError:
            inspection_failed = True
            _skip(resolved, _ROOT_UNREADABLE)
            continue
        if reason is not None:
            _skip(resolved, reason)
            continue
        usable.append(resolved)
    return usable, skipped, inspection_failed


def _explicit_root(candidate: Path) -> Path:
    """Return one explicit root, or raise the command line's path error.

    Returns
    -------
    pathlib.Path
        The canonical root the candidate names.

    Raises
    ------
    ScanRootError
        If the candidate cannot be inspected, does not exist, or is not a
        directory.
    """
    try:
        path = _resolve(candidate)
        # Path predicates suppress inspection errors on Python 3.14.
        reason = _unusable_reason(path)
    except (OSError, RuntimeError) as error:
        msg = f"Path cannot be inspected: {candidate}"
        raise ScanRootError(msg) from error
    if reason == _ROOT_MISSING:
        msg = f"Path does not exist: {path}"
        raise ScanRootError(msg)
    if reason == _ROOT_NOT_DIRECTORY:
        msg = f"Path is not a directory: {path}"
        raise ScanRootError(msg)
    return path


def _validate_explicit(explicit: cabc.Iterable[Path]) -> list[Path]:
    """Resolve explicit roots, raising the command line's path errors.

    Returns
    -------
    list[Path]
        The usable explicit roots in declaration order.

    Raises
    ------
    ScanRootError
        If an explicit root cannot be inspected, does not exist, or is not a
        directory. Inspection fails for a symlink loop and for a path below a
        directory the process may not read, both of which raise rather than
        report a missing path.
    """  # ruff: ignore[docstring-extraneous-exception] - _explicit_root raises it.
    return [_explicit_root(candidate) for candidate in explicit]


def validate_explicit_roots(explicit: cabc.Sequence[Path]) -> tuple[Path, ...]:
    """Validate command-line roots, raising the historic path errors.

    The command line validates its positional roots before it reads
    configuration or takes the advisory lock, so an unusable argument fails
    the invocation without contending for the lock.

    Parameters
    ----------
    explicit : collections.abc.Sequence[pathlib.Path]
        Roots named on the command line.

    Returns
    -------
    tuple[pathlib.Path, ...]
        The canonical explicit roots, in declaration order.

    Raises
    ------
    ScanRootError
        If an explicit root does not exist or is not a directory.

    Examples
    --------
    >>> validate_explicit_roots(())
    ()
    """  # ruff: ignore[docstring-extraneous-exception] - _validate_explicit raises it.
    return tuple(_validate_explicit(explicit))


def _outermost(roots: cabc.Sequence[Path]) -> list[Path]:
    """Drop repeated roots and every root that sits inside another.

    Repetition is removed before containment is tested, because the
    ``other != root`` guard cannot see two equal paths: an explicit root named
    twice, or a symlink named beside its target, would otherwise survive and
    be traversed twice. Comparison uses canonical paths, so a descendant
    declared before its ancestor is still covered by the ancestor.

    Returns
    -------
    list[Path]
        The outermost roots, in the order they were first declared.
    """
    unique = list(dict.fromkeys(roots))
    return [
        root
        for root in unique
        if not any(other != root and root.is_relative_to(other) for other in unique)
    ]


def _candidate_roots(
    explicit: cabc.Sequence[Path],
    configured: cabc.Sequence[Path] | None,
    home: Path | cabc.Callable[[], Path],
) -> tuple[list[Path], list[SkippedRoot], bool, bool]:
    """Apply the precedence rule and report the roots and their provenance.

    Returns
    -------
    tuple[list[Path], list[SkippedRoot], bool, bool]
        Usable roots, skipped roots, whether a status read failed, and whether
        the roots came from the built-in defaults.
    """
    if explicit:
        return _validate_explicit(explicit), [], False, False
    if configured is not None:
        return (*_classify(configured), False)
    resolved = home if isinstance(home, Path) else home()
    return (*_classify(builtin_scan_roots(resolved), builtin=True), True)


def select_scan_roots(
    *,
    explicit: cabc.Sequence[Path],
    configured: cabc.Sequence[Path] | None,
    home: Path | cabc.Callable[[], Path],
) -> RootSelection:
    """Choose the roots one sweep scans, applying documented precedence.

    Explicit roots replace everything. A configured sequence, including an
    empty one, replaces the built-ins. When neither is present the built-in
    Lody and Projects roots are used. Configured and built-in roots that are
    missing or not directories are reported and skipped, so one unavailable
    root never suppresses the rest. Explicit roots instead raise, because the
    caller named them directly.

    Parameters
    ----------
    explicit : collections.abc.Sequence[pathlib.Path]
        Roots named on the command line.
    configured : collections.abc.Sequence[pathlib.Path] | None
        Roots from the configuration file, or ``None`` when the file named
        none.
    home : pathlib.Path or Callable[[], pathlib.Path]
        Home directory or lazy resolver for the built-in expressions.

    Returns
    -------
    RootSelection
        The accepted roots and the reported exclusions.

    Raises
    ------
    ScanRootError
        If an explicit root does not exist or is not a directory.

    Examples
    --------
    >>> selection = select_scan_roots(
    ...     explicit=(), configured=(), home=Path("/home/dev")
    ... )
    >>> selection.roots, selection.skipped
    ((), ())
    """  # ruff: ignore[docstring-extraneous-exception] - _validate_explicit raises it.
    usable, skipped, inspection_failed, builtin = _candidate_roots(
        explicit, configured, home
    )
    kept = _outermost(usable)
    kept_set = set(kept)
    skipped.extend(
        SkippedRoot(root, _ROOT_NESTED, builtin=builtin)
        for root in usable
        if root not in kept_set
    )
    return RootSelection(
        roots=tuple(kept),
        skipped=tuple(skipped),
        inspection_failed=inspection_failed,
    )
