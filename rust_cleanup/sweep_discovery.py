"""Discovery and identification for a multi-root sweep.

A sweep may scan several roots, and the same build output can be reachable
through more than one of them. These helpers keep that aggregation and its
de-duplication out of :mod:`rust_cleanup.sweep`, which stays responsible for
sequencing decisions and reclamation rather than for walking directories or
identifying mounts.
"""

import typing as typ

from rust_cleanup import discovery
from rust_cleanup.filesystem import ResolvedTarget, resolve_target

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

    from rust_cleanup.filesystem import IdentityGetter

# A discovered path whose canonical form leaves every effective root. Reported
# rather than silently dropped, so a root replaced or redirected mid-scan is
# visible in the same list as any other declined candidate.
OMIT_OUTSIDE_ROOT = "outside-root"


def scan_roots(roots: cabc.Sequence[Path]) -> discovery.DiscoveryScan:
    """Scan every effective root, aggregating targets, omissions, and failures.

    A missing root is a selection concern rather than a traversal failure, so
    it contributes no failure here; an unreadable directory inside a root does,
    which is what makes the combined scan incomplete.

    Parameters
    ----------
    roots : collections.abc.Sequence[pathlib.Path]
        Effective scan roots to walk.

    Returns
    -------
    discovery.DiscoveryScan
        The combined targets, declined candidates, and failed inspections.

    Examples
    --------
    >>> scan_roots(()).targets
    ()
    """
    targets: list[Path] = []
    omissions: list[discovery.Omission] = []
    failures = 0
    for root in roots:
        scan = discovery.scan_target_dirs(root)
        targets.extend(scan.targets)
        omissions.extend(scan.omissions)
        failures += scan.failures
    return discovery.DiscoveryScan(tuple(targets), failures, tuple(omissions))


def within_roots(path: Path, roots: cabc.Sequence[Path]) -> bool:
    """Return whether a canonical path still lies inside a canonical root.

    Discovery never follows a symbolic link, but a directory can be replaced
    between the walk and the deletion, and a scan root may itself be a link.
    Re-checking containment against the canonical roots keeps the outermost
    selection authoritative.

    Parameters
    ----------
    path : pathlib.Path
        Canonical candidate path.
    roots : collections.abc.Sequence[pathlib.Path]
        Canonical effective roots.

    Returns
    -------
    bool
        Whether ``path`` is one of the roots or lies below one.

    Examples
    --------
    >>> within_roots(Path("/a/b"), (Path("/a"),))
    True
    >>> within_roots(Path("/c"), (Path("/a"),))
    False
    """
    return any(path == root or path.is_relative_to(root) for root in roots)


def confine_targets(
    targets: cabc.Iterable[Path], roots: cabc.Sequence[Path]
) -> tuple[list[Path], list[discovery.Omission]]:
    """Split discovered paths into canonical in-root targets and omissions.

    Discovery never follows a symbolic link, but a directory can be replaced
    between the walk and the deletion, and a scan root may itself be a link.
    Re-checking containment against the canonical roots keeps the outermost
    selection authoritative. A path that escapes is reported with a fixed
    reason rather than dropped, because a silently narrowed sweep looks
    exactly like one that found nothing.

    Parameters
    ----------
    targets : collections.abc.Iterable[pathlib.Path]
        Discovered target paths, in scan order.
    roots : collections.abc.Sequence[pathlib.Path]
        Effective roots, canonicalized here.

    Returns
    -------
    tuple[list[pathlib.Path], list[discovery.Omission]]
        The canonical in-root targets, and one omission per escaped path.

    Examples
    --------
    >>> targets, omissions = confine_targets((Path("/a/b"), Path("/c")), (Path("/a"),))
    >>> [str(path) for path in targets], [str(item.path) for item in omissions]
    (['/a/b'], ['/c'])
    """
    canonical_roots = tuple(root.resolve(strict=False) for root in roots)
    kept: list[Path] = []
    escaped: list[discovery.Omission] = []
    for target in targets:
        canonical = target.resolve(strict=False)
        if within_roots(canonical, canonical_roots):
            kept.append(canonical)
        else:
            escaped.append(discovery.Omission(target, OMIT_OUTSIDE_ROOT))
    return kept, escaped


def resolve_targets(
    targets: cabc.Iterable[Path],
    identity_getter: IdentityGetter,
) -> tuple[list[ResolvedTarget], int]:
    """Identify each discovered target, preserving the ones that fail.

    Canonicalization and mount identification belong to the injected identity
    adapter, so a target the sweep cannot identify is counted and preserved
    rather than reaching the planner. Overlapping roots can report the same
    build output more than once, so a target already resolved under an earlier
    root is dropped here; de-duplication happens after canonicalization, which
    is what makes two names for one directory collapse to one candidate.

    Parameters
    ----------
    targets : collections.abc.Iterable[pathlib.Path]
        Discovered target directories, in scan order.
    identity_getter : rust_cleanup.filesystem.IdentityGetter
        Adapter that identifies the mounted filesystem backing a path.

    Returns
    -------
    tuple[list[ResolvedTarget], int]
        The identified targets, and how many were preserved for want of an
        identity.

    Examples
    --------
    >>> targets, unidentified = resolve_targets((), lambda path: None)
    >>> targets, unidentified
    ([], 0)
    """
    resolved: list[ResolvedTarget] = []
    seen: set[Path] = set()
    unidentified = 0
    for target_path in targets:
        found = resolve_target(target_path, identity_getter)
        if found is None:
            unidentified += 1
            continue
        if found.path in seen:
            continue
        seen.add(found.path)
        resolved.append(found)
    return resolved, unidentified
