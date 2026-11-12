"""Probe Cargo's advisory build lock without disturbing the build.

Cargo holds an exclusive ``flock`` on ``.cargo-lock`` inside a profile directory
for the whole lifetime of a build. Taking a non-blocking lock on the same file
therefore answers "is a build writing here right now?" without signalling a
process or modifying anything: the probe closes its descriptor immediately,
which releases the probe's own lock.

The probe is pessimistic about *inspection failure* but optimistic about
*unavailability*. A filesystem without advisory locks cannot host a held Cargo
lock, so it reports "free" rather than "held": a mount that answers
``ENOTSUP`` must never protect every target on it for ever. Cargo's own
``util::flock`` takes the same view, skipping locks on NFS and treating
unsupported locking as success.
"""

import contextlib
import errno
import fcntl
import os
import stat
import typing as typ
from pathlib import Path

from rust_cleanup.cargo_layout import PROFILE_ARTEFACTS

LOCK_NAME = ".cargo-lock"

# ``flock`` reports a lock held by another open file description as
# ``EWOULDBLOCK``, which is ``EAGAIN`` on Linux.
_HELD_ERRNOS = frozenset({errno.EWOULDBLOCK, errno.EAGAIN})

# Errors meaning "this mount cannot answer the question". Cargo treats them as
# no lock rather than as a held one, and so must the probe. ``ENOLCK`` is
# deliberately absent: it reports that the kernel has run out of lock records,
# which says nothing about whether a build holds this one. Cargo does not
# classify it as unsupported either, so treating it as "free" could report an
# unresolved lock as available.
_UNAVAILABLE_ERRNOS = frozenset({
    errno.ENOTSUP,
    errno.EOPNOTSUPP,
    errno.ENOSYS,
})

# Children of a profile that hold artefacts rather than further profiles. They
# can contain tens of thousands of entries, so the probe never descends into
# them while looking for a lock.
_ARTEFACT_DIRECTORIES = PROFILE_ARTEFACTS | frozenset({"incremental", "doc"})


class LockProbe(typ.Protocol):
    """Answer whether a build currently holds the lock on one target."""

    def holds_lock(self, candidate: Path) -> bool | None:
        """Report whether ``candidate`` is held by a running build.

        Parameters
        ----------
        candidate : Path
            Build-output directory to inspect.

        Returns
        -------
        bool | None
            ``True`` when a lock is held, ``False`` when no lock is held or the
            mount cannot hold one, and ``None`` when the answer could not be
            established.
        """


def _plain_directories(directory: Path) -> list[Path]:
    """Return the real directory children of ``directory``.

    Symbolic links are excluded so a link cannot point the probe at a lock
    outside the candidate's own tree.

    Returns
    -------
    list[Path]
        Plain directories directly inside ``directory``.

    Raises
    ------
    OSError
        If the directory cannot be listed.
    """  # ruff: ignore[docstring-extraneous-exception] - os.scandir raises it.
    with os.scandir(directory) as entries:
        return [
            Path(entry.path) for entry in entries if entry.is_dir(follow_symlinks=False)
        ]


def _profile_directories(candidate: Path) -> list[Path]:
    """Return the directories that can hold a Cargo profile lock.

    A profile sits directly below the build output, or one level further down
    when a target triple or a workspace member separates them. Artefact
    directories cannot contain a profile and are not descended into, which keeps
    the probe's cost proportional to the number of profiles rather than to the
    number of compiled artefacts.

    Returns
    -------
    list[Path]
        Candidate and nested directories that may contain a lock.

    Raises
    ------
    OSError
        If a directory that would be inspected cannot be listed.
    """  # ruff: ignore[docstring-extraneous-exception] - _plain_directories raises it.
    profiles: list[Path] = []
    for child in _plain_directories(candidate):
        profiles.append(child)
        if child.name not in _ARTEFACT_DIRECTORIES:
            profiles.extend(_plain_directories(child))
    return profiles


def _is_regular_entry(lock: Path) -> bool | None:
    """Report whether ``lock`` exists and is a regular file, without following it.

    Only a regular file can be Cargo's lock. Opening anything else is at best
    pointless and at worst blocking, because reading a FIFO waits for a writer
    that may never come, so the entry kind is established before it is opened.

    Returns
    -------
    bool | None
        ``True`` for an existing regular file, ``False`` when nothing is there,
        and ``None`` when the entry exists as something else - a link included -
        or cannot be interrogated safely.
    """
    try:
        status = lock.stat(follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError:
        return None
    return True if stat.S_ISREG(status.st_mode) else None


def _flock_state(descriptor: int) -> bool | None:
    """Report the lock state of one open file description, releasing its lock.

    Returns
    -------
    bool | None
        ``True`` when another description holds the lock, ``False`` when the
        lock was free or the mount cannot provide one, and ``None`` when the
        error does not settle the question.
    """
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        if error.errno in _HELD_ERRNOS:
            return True
        if error.errno in _UNAVAILABLE_ERRNOS:
            return False
        return None
    with contextlib.suppress(OSError):
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    return False


def _lock_state(lock: Path) -> bool | None:
    """Report one lock file's state without altering it.

    Returns
    -------
    bool | None
        ``True`` when held, ``False`` when absent, free, or unsupported, and
        ``None`` when the file exists but cannot be interrogated.
    """
    regular = _is_regular_entry(lock)
    if regular is not True:
        # Absence is not evidence of inactivity; it is only the absence of
        # evidence, which the caller weighs with its own activity snapshot. An
        # entry that exists as something else is equally unanswerable.
        return regular
    try:
        # ``O_NONBLOCK`` has no effect on a regular file or on ``flock``, but it
        # stops a FIFO swapped in after ``_is_regular_entry`` from waiting for a
        # writer that may never come while the sweep holds its advisory lock.
        descriptor = os.open(lock, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return False
    except OSError:
        return None
    try:
        try:
            is_regular = stat.S_ISREG(os.fstat(descriptor).st_mode)
        except OSError:
            is_regular = False
        if not is_regular:
            # ``flock`` succeeds on a FIFO, so trusting the earlier ``lstat``
            # would report a non-Cargo entry as a free lock.
            return None
        return _flock_state(descriptor)
    finally:
        # Closing the descriptor releases the probe's lock even if the unlock
        # above failed, so the probe never leaves a lock behind.
        os.close(descriptor)


class FileLockProbe:
    """Inspect Cargo's lock files through the kernel's advisory lock."""

    def holds_lock(  # ruff: ignore[no-self-use] - implements the LockProbe protocol.
        self, candidate: Path
    ) -> bool | None:
        """Report whether ``candidate`` is held by a running build.

        Parameters
        ----------
        candidate : Path
            Build-output directory to inspect.

        Returns
        -------
        bool | None
            ``True`` when a lock is held, ``False`` when no lock is held or the
            mount cannot hold one, and ``None`` when the tree could not be
            inspected.
        """
        try:
            profiles = _profile_directories(candidate)
        except OSError:
            return None
        uncertain = False
        for profile in profiles:
            state = _lock_state(profile / LOCK_NAME)
            if state is True:
                return True
            if state is None:
                uncertain = True
        return None if uncertain else False
