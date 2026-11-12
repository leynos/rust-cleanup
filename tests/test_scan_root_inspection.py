"""Tests for scan roots whose status cannot be read.

Resolving a root and reading its status are the two places where the operating
system can refuse to answer instead of answering "no". CPython raises
``RuntimeError`` from :meth:`pathlib.Path.resolve` for a symbolic link loop, and
re-raises ``PermissionError`` from :meth:`pathlib.Path.exists` for ``EACCES``.
Neither may escape a sweep as a traceback: a configured root is reported and
skipped so the other roots still run, and an explicit root fails the command
line with its usual status.

These contracts are separate from the configured precedence and de-duplication
rules that :mod:`test_scan_roots` pins, so they have their own fixtures. They
live apart mainly to keep each module a readable length.
"""

import os
import typing as typ

import pytest
from scan_root_support import make_dirs as _make_dirs
from scan_root_support import reasons as _reasons

from rust_cleanup.roots import ScanRootError, select_scan_roots

if typ.TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses directory permission bits")
def test_unreadable_root_marks_inspection_failed(home: Path) -> None:
    """A root whose status cannot be read aborts rather than silently skips."""
    blocked = home / "blocked"
    child = blocked / "child"
    _make_dirs(child)
    blocked.chmod(0o000)
    try:
        selection = select_scan_roots(explicit=(), configured=(child,), home=home)
    finally:
        blocked.chmod(0o700)

    assert not selection.roots, "an uninspectable root was accepted"
    assert selection.inspection_failed is True, (
        "an uninspectable root was treated as merely missing"
    )
    assert _reasons(selection.skipped) == {child.resolve(): "unreadable"}, (
        "an uninspectable root was not reported"
    )


def test_a_configured_symlink_loop_is_reported_unreadable(home: Path) -> None:
    """A looping root is skipped like any other uninspectable root.

    :meth:`pathlib.Path.resolve` raises ``RuntimeError`` for a symlink loop
    rather than returning a path, so a configured root that loops would
    otherwise escape as an unhandled exception and stop the whole sweep.
    """
    loop = home / "loop"
    loop.symlink_to(loop)
    usable = home / "projects"
    usable.mkdir()

    selection = select_scan_roots(explicit=(), configured=(loop, usable), home=home)

    assert selection.roots == (usable.resolve(),), (
        "a looping root suppressed the usable one"
    )
    assert selection.inspection_failed is True, (
        "a looping root was not treated as an inspection failure"
    )
    assert _reasons(selection.skipped) == {loop: "unreadable"}, (
        "the looping root was not reported as unreadable"
    )


def test_an_explicit_symlink_loop_stops_the_run(home: Path) -> None:
    """A looping explicit root is an argument error, not a traceback."""
    loop = home / "loop"
    loop.symlink_to(loop)

    with pytest.raises(ScanRootError, match="Path cannot be inspected"):
        select_scan_roots(explicit=(loop,), configured=None, home=home)


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses directory permission bits")
def test_an_explicit_root_below_an_unreadable_directory_stops_the_run(
    home: Path,
) -> None:
    """An explicit root whose parents cannot be read is an argument error.

    ``Path.exists()`` re-raises ``PermissionError`` for ``EACCES`` instead of
    returning ``False``, so this path would otherwise escape the command
    line's validation as a traceback.
    """
    blocked = home / "blocked"
    child = blocked / "child"
    _make_dirs(child)
    blocked.chmod(0o000)
    try:
        with pytest.raises(ScanRootError, match="Path cannot be inspected"):
            select_scan_roots(explicit=(child,), configured=None, home=home)
    finally:
        blocked.chmod(0o700)


def test_os_stat_failure_does_not_leak_oserror(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unexpected stat failures are reported through the selection result."""
    root = home / "root"
    root.mkdir()
    real_stat = os.stat

    def _stat(
        path: str | os.PathLike[str],
        *,
        dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> os.stat_result:
        """Fail for the root under test and delegate every other lookup.

        Returns
        -------
        os.stat_result
            The real filesystem status for paths outside the injected failure.

        Raises
        ------
        OSError
            When the requested path matches the injected failure root.
        """
        if os.fspath(path) == str(root):
            msg = "device not ready"
            raise OSError(msg)
        return real_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(os, "stat", _stat)

    selection = select_scan_roots(explicit=(), configured=(root,), home=home)

    assert selection.inspection_failed is True, "a stat failure was swallowed"
    assert _reasons(selection.skipped) == {root: "unreadable"}, (
        "a stat failure was misreported"
    )
