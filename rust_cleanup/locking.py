"""Advisory locking that serialises overlapping rust-cleanup invocations."""

import contextlib
import fcntl
import os
import typing as typ
from pathlib import Path

from rust_cleanup.errors import CleanupError
from rust_cleanup.state import default_state_path

if typ.TYPE_CHECKING:
    import collections.abc as cabc

LOCK_NAME = "rust-cleanup.lock"


def lock_path() -> Path:
    """Return the private lock path, preferring the XDG runtime directory.

    ``XDG_RUNTIME_DIR`` provides a per-user, session-scoped location. When it is
    absent, as in cron, CI, or a minimal service manager, the lock falls back to
    the private per-user state directory so overlapping runs still serialise.

    Returns
    -------
    pathlib.Path
        The per-user advisory lock file path.
    """
    runtime_directory = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_directory:
        return Path(runtime_directory) / LOCK_NAME
    return default_state_path().parent / LOCK_NAME


def _open_lock(path: Path) -> int:
    """Open a private lock descriptor, refusing to follow symbolic links.

    Returns
    -------
    int
        A descriptor for the private lock file.

    Raises
    ------
    OSError
        If the lock file cannot be created, opened, or secured.
    """
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
    except OSError:
        os.close(descriptor)
        raise
    return descriptor


@contextlib.contextmanager
def advisory_lock() -> cabc.Iterator[bool]:
    """Acquire a non-blocking private advisory lock, yielding false if busy.

    Yields
    ------
    bool
        Whether this invocation acquired the lock.

    Raises
    ------
    CleanupError
        If the lock file cannot be created or opened safely.
    """
    path = lock_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = _open_lock(path)
    except OSError as error:
        msg = f"failed to open cleanup lock {path}: {error}"
        raise CleanupError(msg) from error
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
