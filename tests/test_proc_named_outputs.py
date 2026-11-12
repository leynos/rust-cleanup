"""Contracts for the outputs a process's environment and command line name.

A build process is recognized by its executable, but recognition alone says
only *where it works*: a build that redirects its output writes somewhere the
working directory does not reveal. These contracts pin the two mechanisms Cargo
accepts, the way a relative value is resolved, the arguments that belong to a
run program rather than to Cargo, and the failure policy for an environment
that cannot be read.

Every case runs against a synthetic ``/proc`` tree, so nothing here depends on
a build running on the host.
"""

import fcntl
import os
import typing as typ

import pytest
from proc_support import PROCESS_ID, ProcessSpec, custom_target, write_proc_process

from rust_cleanup.active_build import ProcActiveBuildDetector
from rust_cleanup.cargo_locks import LOCK_NAME

if typ.TYPE_CHECKING:
    from pathlib import Path


def _detector_for_cargo_in(
    tmp_path: Path,
    cwd: Path,
    *,
    environment: dict[str, str],
    arguments: tuple[str, ...],
) -> ProcActiveBuildDetector:
    """Build a detector whose only cargo process runs in ``cwd``.

    Parameters
    ----------
    tmp_path : Path
        Root under which the synthetic ``proc`` tree is written.
    cwd : Path
        Working directory the recorded build process holds; created when it
        does not exist, because the detector resolves it strictly.
    environment : dict[str, str]
        Environment recorded for the process.
    arguments : tuple[str, ...]
        Extra command-line arguments recorded after ``cargo``.

    Returns
    -------
    ProcActiveBuildDetector
        A detector over the synthetic process table.
    """
    cwd.mkdir(parents=True, exist_ok=True)
    proc_root = tmp_path / "proc"
    write_proc_process(proc_root, cwd, ProcessSpec("cargo", arguments, environment))
    return ProcActiveBuildDetector(proc_root)


def _quiet_detector(tmp_path: Path) -> ProcActiveBuildDetector:
    """Build a detector over an empty process table.

    Parameters
    ----------
    tmp_path : Path
        Root under which the synthetic ``proc`` tree is written.

    Returns
    -------
    ProcActiveBuildDetector
        A detector that sees no build processes at all.
    """
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    return ProcActiveBuildDetector(proc_root)


@pytest.mark.parametrize(
    ("variable", "name"),
    [
        ("CARGO_TARGET_DIR", "ptq-pr43-cargo-target"),
        ("CARGO_BUILD_TARGET_DIR", "configured-output"),
    ],
    ids=["cargo-target-dir", "cargo-build-target-dir"],
)
def test_an_environment_spelling_protects_the_output_it_names(
    tmp_path: Path, variable: str, name: str
) -> None:
    """A build that redirects its output through the environment protects it.

    Cargo reads its configured output from either spelling of the variable, and
    each row uses a directory a layout rule would never recognise: one takes a
    custom branch-derived name, the other a plain one. The process works in an
    unrelated directory, so only the variable can explain the protection, and
    the unrelated output beside it pins that the protection stays local.
    """
    output = custom_target(tmp_path / "outputs", name)
    other = custom_target(tmp_path / "outputs", "other")
    detector = _detector_for_cargo_in(
        tmp_path,
        tmp_path / "checkout",
        environment={variable: str(output)},
        arguments=(),
    )

    assert detector.is_active(output), f"{variable} was not used as evidence"
    assert not detector.is_active(other), "an unrelated output was protected"


def test_both_environment_spellings_protect_their_own_output(tmp_path: Path) -> None:
    """When both spellings are set, both named outputs are protected.

    Cargo uses one of them, and which one is Cargo's business; protecting both
    keeps a deletion from racing a build whose configuration this module cannot
    second-guess.
    """
    first = custom_target(tmp_path / "outputs", "first-output")
    second = custom_target(tmp_path / "outputs", "second-output")
    detector = _detector_for_cargo_in(
        tmp_path,
        tmp_path / "checkout",
        environment={
            "CARGO_TARGET_DIR": str(first),
            "CARGO_BUILD_TARGET_DIR": str(second),
        },
        arguments=(),
    )

    assert detector.is_active(first), "the first spelling's output was not protected"
    assert detector.is_active(second), "the second spelling's output was not protected"


def test_a_relative_cargo_target_dir_is_resolved_against_the_process_cwd(
    tmp_path: Path,
) -> None:
    """A relative output path means the directory Cargo would actually write."""
    output = custom_target(tmp_path / "outputs", "build-output")
    detector = _detector_for_cargo_in(
        tmp_path,
        tmp_path / "checkout",
        environment={"CARGO_TARGET_DIR": "../outputs/build-output"},
        arguments=(),
    )

    assert detector.is_active(output), "a relative CARGO_TARGET_DIR was ignored"


@pytest.mark.parametrize("style", ["separate", "equals"], ids=["separate", "equals"])
def test_target_dir_argument_protects_the_named_output(
    tmp_path: Path, style: str
) -> None:
    """``--target-dir`` names the output in either of its accepted spellings."""
    output = custom_target(tmp_path / "outputs", "named-on-the-command-line")
    value = "../outputs/named-on-the-command-line"
    arguments = (
        (f"--target-dir={value}",) if style == "equals" else ("--target-dir", value)
    )
    detector = _detector_for_cargo_in(
        tmp_path, tmp_path / "checkout", environment={}, arguments=arguments
    )

    assert detector.is_active(output), f"{style} --target-dir did not protect output"


def test_arguments_after_a_double_dash_do_not_name_a_target_directory(
    tmp_path: Path,
) -> None:
    """A program the build runs receives its arguments after ``--``.

    Those arguments belong to the program, not to Cargo, so a path that looks
    like ``--target-dir`` there must not be read as Cargo's output.
    """
    output = custom_target(tmp_path / "outputs", "not-cargos-output")
    detector = _detector_for_cargo_in(
        tmp_path,
        tmp_path / "checkout",
        environment={},
        arguments=("run", "--", "--target-dir", str(output)),
    )

    assert not detector.is_active(output), (
        "an argument after -- was read as Cargo's target directory"
    )


def test_an_unreadable_environment_is_treated_as_uncertain(tmp_path: Path) -> None:
    """An environment that cannot be read protects rather than hiding a build.

    The detector treats a vanished entry as a process-exit race, so an entry
    replaced by a directory is a different failure: one that leaves the
    protection question unanswered rather than answering it.
    """
    output = custom_target(tmp_path / "outputs", "crate-build")
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    proc_root = tmp_path / "proc"
    write_proc_process(proc_root, checkout, ProcessSpec("cargo"))
    environ = proc_root / "123" / "environ"
    environ.unlink()
    environ.mkdir()
    detector = ProcActiveBuildDetector(proc_root)

    assert detector.uncertain, "an unreadable environment was reported as certain"
    assert detector.is_active(output), "an unreadable environment allowed deletion"


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
def test_an_environment_in_the_exec_window_is_not_uncertainty(tmp_path: Path) -> None:
    """A process replacing its image is a race, not an uninspectable build.

    ``execve`` clears the argument and environment areas in one operation, so a
    process caught between them reports an empty ``cmdline`` and an environment
    that cannot be read. Treating that as uncertainty would protect every
    candidate on the host, and a build process is replaced often enough that a
    sweep would then refuse to clean anything. The process names no output
    either way, so its working directory remains its only evidence.
    """
    output = custom_target(tmp_path / "outputs", "crate-build")
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    proc_root = tmp_path / "proc"
    write_proc_process(proc_root, checkout, ProcessSpec("cargo"))
    process = proc_root / PROCESS_ID
    (process / "cmdline").write_bytes(b"")
    environ = process / "environ"
    environ.chmod(0o000)
    try:
        detector = ProcActiveBuildDetector(proc_root)
    finally:
        environ.chmod(0o600)

    assert not detector.uncertain, "the exec window was reported as uncertainty"
    assert not detector.is_active(output), (
        "an output the process never named was protected"
    )


def test_the_detector_does_not_consult_the_cargo_lock(tmp_path: Path) -> None:
    """Ownership is a process question; the lock is the callers' question.

    The planner and the reclaimer each probe the lock before recording a
    reason, so a detector that probed it too would answer first and make the
    ``cargo-lock`` token unreachable. Holding the lock here therefore must not
    change the detector's answer, which is the falsifiable half of that
    division: if the probe returns to the detector, this test fails.
    """
    output = custom_target(tmp_path / "outputs", "crate-build")
    lock = output / "debug" / LOCK_NAME
    detector = _quiet_detector(tmp_path)

    assert not detector.is_active(output), "an unheld lock protected the output"

    descriptor = os.open(lock, os.O_RDWR)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert not detector.is_active(output), "the detector probed the Cargo lock"
    finally:
        os.close(descriptor)
