"""Regression contracts for safety defects found during review.

Each test here pins one behaviour that was previously wrong: lock-file and
target symbolic-link handling, descriptor release, the XDG fallback, stale
state after a malformed reload, unmeasurable usage, the single clock snapshot
per sweep, and the command-line help text. The active-build detector's
conservative cases live in ``test_proc_active_build.py``.
"""

from pathlib import Path
from unittest import mock

import pytest
from conftest import InactiveBuilds, build_run, make_target, synthetic_identity

from rust_cleanup import (
    TARGET_DIR,
    advisory_lock,
    cleanup_scan_roots,
    cli,
    find_target_dirs,
    is_cache_dir,
)
from rust_cleanup.errors import CleanupError
from rust_cleanup.locking import lock_path
from rust_cleanup.pressure import FilesystemIdentity, Mode
from rust_cleanup.state import StateLoadResult, StateStore

_LOCK_NAME = "rust-cleanup.lock"


def _open_descriptor_count() -> int:
    """Return the number of descriptors this process currently holds.

    Returns
    -------
    int
        The count of open descriptors visible under ``/proc/self/fd``.
    """
    return sum(1 for _entry in Path("/proc/self/fd").iterdir())


_HELP_OPTIONS = (
    "--dry-run",
    "--verbose",
    "--config",
    "--normal-stale-after",
    "--pressure-enter-percent",
    "--pressure-exit-percent",
    "--pressure-stale-after",
    "--critical-enter-percent",
    "--critical-exit-percent",
    "--critical-stale-after",
)


def test_advisory_lock_refuses_a_symlinked_lock_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A symbolic link at the lock path cannot redirect the open elsewhere."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    victim = tmp_path / "victim"
    victim.write_text("do not truncate", encoding="utf-8")
    (tmp_path / _LOCK_NAME).symlink_to(victim)

    with (
        pytest.raises(CleanupError, match="failed to open cleanup lock"),
        advisory_lock(),
    ):
        pytest.fail("symlinked lock file was opened")

    assert victim.read_text(encoding="utf-8") == "do not truncate", (
        "symlinked lock file was truncated"
    )


def test_advisory_lock_releases_its_descriptor_on_every_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acquired, contended, and released locks never leak a descriptor."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    before = _open_descriptor_count()
    for _ in range(3):
        with advisory_lock() as acquired:
            assert acquired, "lock acquisition failed"
    with advisory_lock() as held:
        assert held, "lock acquisition failed"
        with advisory_lock() as contended:
            assert not contended, "second acquisition succeeded"

    assert _open_descriptor_count() == before, "lock descriptor leaked"


def test_lock_path_falls_back_to_the_private_state_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a runtime directory the lock still lives in private state."""
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    assert lock_path() == tmp_path / "state" / "rust-cleanup" / _LOCK_NAME, (
        "lock fallback path changed"
    )
    with advisory_lock() as acquired:
        assert acquired, "fallback lock acquisition failed"
    lock_directory = tmp_path / "state" / "rust-cleanup"
    assert lock_directory.stat().st_mode & 0o777 == 0o700, "lock directory is public"


def test_symlinked_target_cannot_redirect_reclamation(tmp_path: Path) -> None:
    """A target symbolic link is ignored, so reclamation stays inside the root."""
    external = make_target(tmp_path / "external", "crate", 1)
    root = tmp_path / "root"
    root.mkdir()
    (root / TARGET_DIR).symlink_to(external, target_is_directory=True)
    assert is_cache_dir(external), "the symlink destination was not cache-marked"
    assert not find_target_dirs(root), "a symlinked target was discovered"

    outcome = cleanup_scan_roots(
        (root,),
        build_run(
            state_store=StateStore(tmp_path / "state.json"),
            active_build_detector=InactiveBuilds(),
            usage_getter=lambda _path: 1.0,
            identity_getter=lambda _path: synthetic_identity(root),
            now=10000,
        ),
    )

    assert external.exists(), "reclamation followed a target symlink out of the root"
    assert outcome.deleted == 0, "a symlinked target was counted as reclaimed"


def test_reloading_malformed_state_clears_earlier_modes(tmp_path: Path) -> None:
    """A malformed reload cannot leave a stale destructive mode behind."""
    identity = FilesystemIdentity(tmp_path, 7)
    path = tmp_path / "state.json"
    store = StateStore(path)
    store.set_mode(identity, Mode.CRITICAL)
    store.save()
    assert store.load(lambda _message: None) is StateLoadResult.LOADED, (
        "valid state did not load"
    )
    path.write_text("{broken", encoding="utf-8")
    warnings: list[str] = []

    assert store.load(warnings.append) is StateLoadResult.MALFORMED, (
        "malformed state was not reported"
    )
    assert store.mode_for(identity) is None, "malformed reload kept a stale mode"
    assert warnings, "malformed reload did not warn"


def test_unmeasurable_usage_preserves_every_candidate(tmp_path: Path) -> None:
    """A failing usage reading deletes nothing and reports no false success."""

    def failing_usage(_path: Path) -> float:
        """Simulate a filesystem whose usage cannot be measured.

        Parameters
        ----------
        _path : Path
            Filesystem whose usage is requested; this getter ignores it.

        Raises
        ------
        OSError
            Always, so the sweep must handle an unreadable usage value.
        """
        message = "usage unavailable"
        raise OSError(message)

    target = make_target(tmp_path, "stale", 1)
    outcome = cleanup_scan_roots(
        (tmp_path,),
        build_run(
            state_store=StateStore(tmp_path / "state.json"),
            active_build_detector=InactiveBuilds(),
            usage_getter=failing_usage,
            identity_getter=lambda _path: synthetic_identity(tmp_path),
            now=10000,
        ),
    )

    assert outcome.deleted == 0, "unmeasurable filesystem deleted a candidate"
    assert target.exists(), "unmeasurable filesystem deleted the target"
    assert not outcome.unresolved_pressure, "unmeasurable usage reported pressure"
    assert outcome.filesystems[0].selected_mode is Mode.NORMAL, "mode was not derived"
    assert outcome.filesystems[0].protected == 1, (
        "unmeasurable filesystem did not account for its preserved candidate"
    )


def test_sweep_takes_one_clock_reading_for_the_whole_run(tmp_path: Path) -> None:
    """A single clock snapshot bounds every candidate in one sweep."""
    make_target(tmp_path, "first", 1)
    make_target(tmp_path, "second", 2)
    with mock.patch(
        "rust_cleanup.sweep.wall_clock_seconds", return_value=10000.0
    ) as clock:
        cleanup_scan_roots(
            (tmp_path,),
            build_run(
                state_store=StateStore(tmp_path / "state.json"),
                active_build_detector=InactiveBuilds(),
                usage_getter=lambda _path: 1.0,
                identity_getter=lambda _path: synthetic_identity(tmp_path),
            ),
        )

    assert clock.call_count == 1, "the sweep read the clock more than once"


def test_main_help_documents_every_policy_override(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The command-line help stays complete for operators."""
    with pytest.raises(SystemExit) as exit_status:
        cli.main(["--help"])

    assert exit_status.value.code == 0, "help did not exit successfully"
    help_text = capsys.readouterr().out
    missing = [option for option in _HELP_OPTIONS if option not in help_text]
    assert not missing, f"help text is missing: {missing}"
