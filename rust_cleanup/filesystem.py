"""Filesystem adapters for capacity measurement, mount identity, and deletion.

These adapters are the only place where cleanup policy meets the operating
system's view of storage: how full a filesystem is, which mounted filesystem
backs a path, and removing a target tree. Keeping them out of
:mod:`rust_cleanup.pressure` leaves that module a pure policy table, and lets
the sweep inject fakes in tests without touching a real mount.

The measurement adapters report an unmeasurable or unidentifiable path by
raising :class:`OSError`. Callers treat that as "unknown", which preserves
candidates instead of deleting them under a filesystem the sweep cannot see.
:func:`resolve_target` performs that translation for the discovery half of a
sweep, so canonicalizing a target and identifying its mount stay one adapter
step rather than leaking into the planner.
"""

import dataclasses
import shutil
import typing as typ

from rust_cleanup.errors import CleanupError
from rust_cleanup.pressure import FilesystemIdentity

if typ.TYPE_CHECKING:
    from pathlib import Path


@dataclasses.dataclass(frozen=True, slots=True)
class ResolvedTarget:
    """One discovered target with its canonical path and backing filesystem.

    Attributes
    ----------
    path : pathlib.Path
        Canonical path of the discovered target directory.
    filesystem : FilesystemIdentity
        Mounted filesystem that backs ``path``.
    """

    path: Path
    filesystem: FilesystemIdentity


class DirectoryDeleter(typ.Protocol):
    """Remove one target directory tree.

    Implementations raise :class:`~rust_cleanup.errors.CleanupError` when the
    directory cannot be removed, which the sweep counts as a failed deletion
    and leaves to the next run.
    """

    def __call__(self, directory: Path, /) -> bool:
        """Delete ``directory`` and report that it was removed.

        Parameters
        ----------
        directory : Path
            Target directory to remove.

        Returns
        -------
        bool
            ``True`` when the directory was removed.

        Raises
        ------
        CleanupError
            If the directory cannot be removed.
        """


class UsageGetter(typ.Protocol):
    """Query the used capacity of the filesystem that backs a path.

    Implementations raise ``OSError`` when the filesystem cannot be measured,
    including when it reports a zero total capacity. The sweep treats that as
    an unmeasurable filesystem: it preserves every candidate on it and stops
    further reclamation.
    """

    def __call__(self, path: Path, /) -> float:
        """Return used capacity as a percentage for ``path``'s filesystem.

        Parameters
        ----------
        path : Path
            Path on the filesystem whose used capacity is measured.

        Returns
        -------
        float
            The filesystem's used capacity percentage.

        Raises
        ------
        OSError
            If the underlying usage query cannot measure the filesystem, or
            the filesystem reports a zero total capacity.
        """


class IdentityGetter(typ.Protocol):
    """Discover the mounted filesystem that backs a path.

    Implementations raise ``OSError`` when the path cannot be resolved or
    stat-ed. The sweep then preserves the candidate rather than guessing which
    filesystem it belongs to.
    """

    def __call__(self, path: Path, /) -> FilesystemIdentity:
        """Return canonical mount point and device identity for ``path``.

        Parameters
        ----------
        path : Path
            Path whose backing mount is identified.

        Returns
        -------
        FilesystemIdentity
            The filesystem's canonical mount point and device identity.

        Raises
        ------
        OSError
            If the path cannot be resolved or stat-ed.
        """


def usage_percent(path: Path) -> float:
    """Return used capacity as a percentage for ``path``'s filesystem.

    An unmeasurable filesystem raises ``OSError``, either from the underlying
    usage query or because the filesystem reports a zero total. Some
    pseudo-filesystems and container mounts report a zero total, and a
    percentage of that capacity would otherwise divide by zero rather than
    take the conservative unmeasured path.

    Parameters
    ----------
    path : Path
        Path on the filesystem whose used capacity is measured.

    Returns
    -------
    float
        The filesystem's used capacity percentage.

    Raises
    ------
    OSError
        If the underlying usage query cannot measure the filesystem, or the
        filesystem reports a zero total capacity.
    """
    usage = shutil.disk_usage(path)
    if usage.total == 0:
        msg = "filesystem reports zero total capacity"
        raise OSError(msg)
    return usage.used * 100 / usage.total


def filesystem_identity(path: Path) -> FilesystemIdentity:
    """Return canonical mount point and device identity for ``path``.

    An unidentifiable path propagates the ``OSError`` raised while resolving
    or stat-ing it.

    Parameters
    ----------
    path : Path
        Path whose backing mount is identified.

    Returns
    -------
    FilesystemIdentity
        The filesystem's canonical mount point and device identity.
    """
    current = path.resolve()
    device = current.stat().st_dev
    while current.parent != current and current.parent.stat().st_dev == device:
        current = current.parent
    return FilesystemIdentity(mount_point=current, device=device)


def resolve_target(
    target: Path, identity_getter: IdentityGetter
) -> ResolvedTarget | None:
    """Return the canonical path and backing filesystem for ``target``.

    Canonicalization and mount identification are one adapter step, so the
    planner never resolves a path or interprets an adapter's ``OSError``
    itself. The canonical path is resolved once and identified as-is, so the
    path that is stored cannot diverge from the path that was identified. An
    unidentifiable target is reported as ``None``, which preserves it rather
    than guessing which filesystem it belongs to.

    Parameters
    ----------
    target : Path
        Discovered target directory to identify.
    identity_getter : IdentityGetter
        Adapter that identifies the mounted filesystem backing a path.

    Returns
    -------
    ResolvedTarget | None
        The canonical path with the filesystem that backs it, or ``None``
        when the target cannot be resolved or identified.
    """
    try:
        canonical = target.resolve()
        return ResolvedTarget(canonical, identity_getter(canonical))
    except OSError:
        return None


def delete_directory(directory: Path) -> bool:
    """Delete a directory tree or raise :class:`CleanupError` on failure.

    Parameters
    ----------
    directory : Path
        Target directory to remove.

    Returns
    -------
    bool
        ``True`` when the directory was removed.

    Raises
    ------
    CleanupError
        If the directory cannot be removed.
    """
    try:
        shutil.rmtree(directory)
    except (OSError, shutil.Error) as error:
        msg = f"failed to delete {directory}: {error}"
        raise CleanupError(msg) from error
    return True
