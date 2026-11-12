"""Contracts for probing Cargo's advisory build lock.

Cargo holds an exclusive ``flock`` on ``.cargo-lock`` for the lifetime of a
build, so the probe's answer decides whether a target is treated as actively
written. These tests use a real second file descriptor rather than a mocked
``flock``: the interface under test is the kernel's advisory lock, and a mock
would prove only that our own code calls itself.
"""

import errno
import fcntl
import os
import typing as typ

from conftest import make_target

from rust_cleanup import cargo_locks
from rust_cleanup.cargo_locks import FileLockProbe

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _target_with_lock(tmp_path: Path, profile: str = "debug") -> tuple[Path, Path]:
    """Create a build output carrying one Cargo profile lock.

    Parameters
    ----------
    tmp_path : Path
        Test root the project directory is created under.
    profile : str, default="debug"
        Profile directory, relative to the build root, holding the lock.

    Returns
    -------
    tuple[Path, Path]
        The build-output directory and the lock file inside it.
    """
    target = make_target(tmp_path, "crate", 1)
    directory = target / profile
    directory.mkdir(parents=True)
    lock = directory / cargo_locks.LOCK_NAME
    lock.write_bytes(b"")
    return target, lock


def _hold(lock: Path) -> int:
    """Take Cargo's exclusive lock on ``lock`` and return its descriptor.

    Returns
    -------
    int
        The open descriptor holding the lock; closing it releases the lock.
    """
    descriptor = os.open(lock, os.O_RDWR)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return descriptor


def test_a_held_lock_is_reported_and_a_released_one_is_not(tmp_path: Path) -> None:
    """The same probe notices a lock being taken and released.

    The first reading proves the probe is not simply always true, and the last
    proves it re-reads the kernel rather than caching the earlier answer.
    """
    target, lock = _target_with_lock(tmp_path)
    probe = FileLockProbe()

    assert probe.holds_lock(target) is False, "an unheld lock was reported held"

    descriptor = _hold(lock)
    try:
        assert probe.holds_lock(target) is True, "a held lock was reported free"
    finally:
        os.close(descriptor)

    assert probe.holds_lock(target) is False, "a released lock stayed held"


def test_a_lock_under_a_target_triple_is_found(tmp_path: Path) -> None:
    """A build with an explicit triple locks two directories deeper."""
    target, lock = _target_with_lock(
        tmp_path, profile="x86_64-unknown-linux-gnu/release"
    )
    descriptor = _hold(lock)
    try:
        assert FileLockProbe().holds_lock(target) is True, (
            "a triple/profile lock was not found"
        )
    finally:
        os.close(descriptor)


def test_an_absent_lock_is_not_reported_as_held(tmp_path: Path) -> None:
    """Build output without a lock is not protected by the probe.

    Absence is not evidence of inactivity either: it only means this probe has
    nothing to add, and the activity snapshot decides staleness on its own.
    """
    target = make_target(tmp_path, "crate", 1)

    assert FileLockProbe().holds_lock(target) is False, (
        "build output without a lock was reported as held"
    )


def test_a_symlinked_lock_is_not_followed(tmp_path: Path) -> None:
    """A link cannot redirect the probe onto a file outside the build output."""
    target, lock = _target_with_lock(tmp_path)
    elsewhere = tmp_path / "elsewhere.lock"
    elsewhere.write_bytes(b"")
    lock.unlink()
    lock.symlink_to(elsewhere)

    assert FileLockProbe().holds_lock(target) is None, (
        "a symlinked lock was followed instead of reported as unknown"
    )


def test_probing_never_creates_a_lock_file(tmp_path: Path) -> None:
    """The probe only inspects existing locks; it never creates one.

    Opening with ``O_CREAT`` would leave a lock behind in every profile it
    visited, which would then look like Cargo's own file to the next sweep and
    to any other tool inspecting the output. The empty profile here has never
    held a lock, so an inspected-but-created file would be unambiguous.
    """
    target = make_target(tmp_path, "crate", 1)
    profile = target / "debug"
    profile.mkdir()
    before = sorted(path.name for path in profile.iterdir())

    assert FileLockProbe().holds_lock(target) is False, (
        "build output without a lock was reported as held"
    )

    assert sorted(path.name for path in profile.iterdir()) == before, (
        "the probe created an entry in the profile it inspected"
    )


def test_probing_does_not_leave_the_lock_held(tmp_path: Path) -> None:
    """A probe of a free lock releases it before returning.

    The probe takes an exclusive lock to answer the question. If it kept that
    lock, Cargo could not start a build afterwards, so the second descriptor
    here is the build's own attempt and must succeed immediately.
    """
    target, lock = _target_with_lock(tmp_path)

    assert FileLockProbe().holds_lock(target) is False, (
        "an unheld lock was reported held"
    )

    descriptor = _hold(lock)
    os.close(descriptor)


def test_an_unsupported_filesystem_is_reported_as_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mount without advisory locks is never treated as an active build.

    Cargo itself treats ``ENOTSUP`` and ``ENOSYS`` as "locking is unavailable
    here" and builds without a lock. A probe that reported "held" instead
    would protect every target on such a mount for ever.
    """
    target, _lock = _target_with_lock(tmp_path)

    def refuse(*_arguments: object) -> None:
        """Fail the way a filesystem without advisory locks does.

        Raises
        ------
        OSError
            When the forbidden operation is attempted.
        """
        raise OSError(errno.ENOTSUP, "locking not supported")

    monkeypatch.setattr(fcntl, "flock", refuse)

    assert FileLockProbe().holds_lock(target) is False, (
        "an unsupported filesystem was reported as holding the lock"
    )


def test_a_swapped_in_fifo_is_opened_without_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lock is opened non-blocking, so a FIFO cannot stall the sweep.

    The entry kind is checked before the open, but the two steps are not
    atomic: another process can replace the regular file with a FIFO in
    between. A blocking ``O_RDONLY`` then waits for a writer that may never
    come, while the sweep holds its advisory lock and the timer never re-arms.
    ``O_NONBLOCK`` changes nothing for a regular file or for ``flock``.

    The flags are inspected before the real open is attempted, so the failure
    is a message rather than a hung test.
    """
    target, lock = _target_with_lock(tmp_path)
    lock.unlink()
    os.mkfifo(lock)
    monkeypatch.setattr(cargo_locks, "_is_regular_entry", lambda _path: True)
    real_open = os.open

    def recording_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        """Record the open flags and then open for real.

        Returns
        -------
        int
            The file descriptor from the real open call.
        """
        assert flags & os.O_NONBLOCK, "the lock was opened blocking"
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(cargo_locks.os, "open", recording_open)

    assert FileLockProbe().holds_lock(target) is None, (
        "a swapped-in FIFO was not reported as unanswerable"
    )


def test_a_non_regular_descriptor_is_reported_as_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What was opened decides the answer, not what was there a moment before.

    ``flock`` succeeds on a FIFO, so a probe that trusted the earlier ``lstat``
    would report a non-Cargo entry as a free lock and let the target be
    deleted. The descriptor is re-checked with ``fstat`` after the open, and
    anything that is not a regular file stays unanswerable.
    """
    target, lock = _target_with_lock(tmp_path)
    lock.unlink()
    os.mkfifo(lock)
    monkeypatch.setattr(cargo_locks, "_is_regular_entry", lambda _path: True)

    assert FileLockProbe().holds_lock(target) is None, (
        "a non-regular descriptor was reported as a free lock"
    )


def test_an_exhausted_lock_table_is_reported_as_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ENOLCK`` is an unresolved answer, not a free lock.

    The kernel reports a full lock table when it cannot record the request at
    all. Cargo does not treat that as an unsupported filesystem, so a probe that
    answered "free" would let a target be deleted while a build the kernel could
    not answer for was writing to it.
    """
    target, _lock = _target_with_lock(tmp_path)

    def exhaust(*_arguments: object) -> None:
        """Fail the way a kernel with no lock records left does.

        Raises
        ------
        OSError
            Whenever a lock is requested without available lock records.
        """
        raise OSError(errno.ENOLCK, "no locks available")

    monkeypatch.setattr(fcntl, "flock", exhaust)

    assert FileLockProbe().holds_lock(target) is None, (
        "an exhausted lock table was reported as free"
    )
