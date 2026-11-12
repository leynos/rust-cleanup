"""Contracts for the ``/proc`` process snapshot behind active-build protection.

The detector decides whether a target is protected by a running build, so these
tests pin the cases that decide safety: an uninspectable or undecodable process
table protects rather than aborts the sweep, recognition is limited to build
executables, and protection is scoped to the worktree a build actually holds.

What a recognized process names as its output is a contract of its own;
:mod:`test_proc_named_outputs` holds it.
"""

import typing as typ

import pytest
from conftest import make_target
from proc_support import ProcessSpec, write_proc_process

from rust_cleanup.active_build import ProcActiveBuildDetector, ProcInspectionError
from rust_cleanup.errors import CleanupError

if typ.TYPE_CHECKING:
    from pathlib import Path


def test_proc_inspection_error_is_a_cleanup_error() -> None:
    """Callers can catch inspection failures as ordinary cleanup failures."""
    assert issubclass(ProcInspectionError, CleanupError), "error hierarchy changed"


def test_unreadable_proc_root_protects_every_target(tmp_path: Path) -> None:
    """An unreadable process table is uncertain and therefore protective."""
    target = make_target(tmp_path, "stale", 1)
    detector = ProcActiveBuildDetector(tmp_path / "missing-proc")

    assert detector.uncertain, "unreadable process table was reported as certain"
    assert detector.is_active(target), "uncertain detector allowed deletion"


def test_unreadable_process_entry_is_treated_as_uncertain(tmp_path: Path) -> None:
    """A process that cannot be inspected protects rather than crashes."""
    target = make_target(tmp_path, "stale", 1)
    proc_root = tmp_path / "proc"
    (proc_root / "123" / "comm").mkdir(parents=True)
    detector = ProcActiveBuildDetector(proc_root)

    assert detector.uncertain, "uninspectable process was reported as certain"
    assert detector.is_active(target), "uncertain detector allowed deletion"


def test_undecodable_process_name_is_treated_as_uncertain(tmp_path: Path) -> None:
    """A process name that is not UTF-8 protects instead of ending the sweep.

    ``comm`` holds raw bytes, so a process whose name is not valid UTF-8 raises
    ``UnicodeDecodeError`` — a ``ValueError``, not an ``OSError``. Uncaught, it
    would escape the detector's ``ProcInspectionError`` handling and abort the
    sweep rather than leaving protection conservative.
    """
    target = make_target(tmp_path, "stale", 1)
    proc_root = tmp_path / "proc"
    process = proc_root / "123"
    process.mkdir(parents=True)
    (process / "comm").write_bytes(b"cargo-\xff\xfe\n")
    (process / "cwd").symlink_to(target.parent, target_is_directory=True)
    (process / "cmdline").write_bytes(b"cargo\x00")

    detector = ProcActiveBuildDetector(proc_root)

    assert detector.uncertain, "an undecodable process name was reported as certain"
    assert detector.is_active(target), "an undecodable process name allowed deletion"


def _assert_protection(tmp_path: Path, command: str, *, protects: bool) -> None:
    """Assert whether ``command`` protects the target it is working in.

    Parameters
    ----------
    tmp_path : Path
        Isolated temporary directory provided by pytest.
    command : str
        Executable name written into the synthetic process table.
    protects : bool
        Whether the executable must protect its target.
    """
    target = make_target(tmp_path, "crate", 1)
    write_proc_process(tmp_path / "proc", target.parent, ProcessSpec(command))
    detector = ProcActiveBuildDetector(tmp_path / "proc")

    assert detector.is_active(target) is protects, (
        f"{command} protection was not {protects}"
    )


@pytest.mark.parametrize(
    "command",
    [
        "cargo",
        "Cargo",
        "cargo-nextest",
        "rustc",
        "rustc-1.79",
        "rustdoc",
        "rust-lld",
        "ld.lld",
        "cc",
        "g++",
        "kani-0.1",
        "verus",
    ],
)
def test_proc_detector_recognizes_build_processes(tmp_path: Path, command: str) -> None:
    """Every build executable shape protects a target it is working in."""
    _assert_protection(tmp_path, command, protects=True)


@pytest.mark.parametrize(
    "command", ["python3", "node", "ls", "ldconfig", "rust-analyzer"]
)
def test_proc_detector_ignores_unrelated_processes(
    tmp_path: Path, command: str
) -> None:
    """An unrelated process working in a target does not protect it forever."""
    _assert_protection(tmp_path, command, protects=False)


def test_proc_detector_only_protects_the_process_worktree(tmp_path: Path) -> None:
    """A build in one worktree never protects a sibling worktree's target."""
    worktrees = tmp_path / "repos" / "example" / "worktrees"
    busy = make_target(worktrees / "busy", "crate", 1)
    idle = make_target(worktrees / "idle", "crate", 1)
    write_proc_process(tmp_path / "proc", busy.parent, ProcessSpec("cargo"))
    detector = ProcActiveBuildDetector(tmp_path / "proc")

    assert detector.is_active(busy), "busy worktree target was unprotected"
    assert not detector.is_active(idle), "sibling worktree target was protected"


@pytest.mark.parametrize(
    "protects", [True, False], ids=["in-worktree", "outside-worktree"]
)
def test_proc_detector_scopes_an_absolute_command_path(
    tmp_path: Path, *, protects: bool
) -> None:
    """A build whose command path lies outside a worktree protects it no longer."""
    worktree = tmp_path / "repos" / "example" / "worktrees" / "busy"
    target = make_target(worktree, "crate", 1)
    unrelated = make_target(tmp_path / "unrelated", "crate", 1)
    recorded = target if protects else unrelated
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    write_proc_process(
        tmp_path / "proc",
        elsewhere,
        ProcessSpec("cargo", arguments=(str(recorded / "build.o"),)),
    )
    detector = ProcActiveBuildDetector(tmp_path / "proc")

    assert detector.is_active(target) is protects, (
        f"an absolute command path under {recorded} gave the wrong protection"
    )
