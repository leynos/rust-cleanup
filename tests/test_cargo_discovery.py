"""Tests for positive-evidence discovery of Cargo build output.

Discovery decides what the sweep is allowed to delete, so these tests pin the
evidence rule in both directions: real Cargo output is found whether or not it
carries a marker or uses the conventional name, and a directory that merely
looks like build output is not. They also pin the omission reasons, because an
operator has to be able to tell an empty scan from one that declined a
candidate.
"""

import os
import typing as typ

import pytest

from rust_cleanup import CACHEDIR_TAG, TARGET_DIR, find_target_dirs
from rust_cleanup.discovery import DiscoveryScan, scan_target_dirs

if typ.TYPE_CHECKING:
    from pathlib import Path

# The marker files and directories Cargo itself writes into a profile. The
# tests build these by hand so they never need a real toolchain.
_CARGO_LOCK = ".cargo-lock"


def _profile(target: Path, name: str, *, artefacts: tuple[str, ...] = ()) -> Path:
    """Create one Cargo profile directory with the named artefacts.

    Parameters
    ----------
    target : Path
        Directory the profile is created inside.
    name : str
        Profile directory name, such as ``debug``.
    artefacts : tuple[str, ...], default=()
        Cargo artefacts to create inside the profile.

    Returns
    -------
    Path
        The created profile directory.
    """
    profile = target / name
    profile.mkdir(parents=True, exist_ok=True)
    for artefact in artefacts:
        if artefact.startswith(".") or "." in artefact:
            (profile / artefact).touch()
        else:
            (profile / artefact).mkdir()
    return profile


def _cargo_target(
    root: Path,
    name: str,
    *,
    profile: str = "debug",
    artefacts: tuple[str, ...] = (_CARGO_LOCK, "deps"),
) -> Path:
    """Create an unmarked Cargo build-output directory under ``root``.

    Parameters
    ----------
    root : Path
        Directory the build output is created inside.
    name : str
        Directory name of the build output.
    profile : str, default="debug"
        Name of the profile directory created inside the build output.
    artefacts : tuple[str, ...], default=(".cargo-lock", "deps")
        Cargo artefacts created inside that profile.

    Returns
    -------
    Path
        The created build-output directory.
    """
    target = root / name
    target.mkdir(parents=True)
    (target / ".rustc_info.json").touch()
    _profile(target, profile, artefacts=artefacts)
    return target


def _reasons(scan: DiscoveryScan) -> dict[Path, str]:
    """Return the omission reasons keyed by the path they describe.

    Returns
    -------
    dict[Path, str]
        One fixed reason token per omitted path.
    """
    return {omission.path: omission.reason for omission in scan.omissions}


def test_an_unmarked_conventional_target_is_discovered(tmp_path: Path) -> None:
    """A ``target`` with rustc info and a Cargo profile is discovered unmarked."""
    target = _cargo_target(
        tmp_path, TARGET_DIR, profile="debug", artefacts=(_CARGO_LOCK, "deps")
    )

    assert not (target / CACHEDIR_TAG).exists(), "the fixture must stay unmarked"
    assert find_target_dirs(tmp_path) == [target], (
        "an unmarked Cargo target with positive evidence was not discovered"
    )


def test_a_custom_named_cargo_output_is_discovered(tmp_path: Path) -> None:
    """Build output named for a variant is discovered, not only ``target``."""
    target = _cargo_target(tmp_path, "ptq-pr43-cargo-target")

    assert find_target_dirs(tmp_path) == [target], (
        "a custom-named Cargo target was not discovered"
    )


def test_a_profile_two_levels_deep_under_a_triple_is_discovered(
    tmp_path: Path,
) -> None:
    """A profile below a target triple is accepted as Cargo's own layout."""
    target = tmp_path / TARGET_DIR
    target.mkdir()
    (target / ".rustc_info.json").touch()
    _profile(target / "x86_64-unknown-linux-gnu", "debug", artefacts=(_CARGO_LOCK,))

    assert find_target_dirs(tmp_path) == [target], (
        "a triple-nested Cargo profile was not accepted as evidence"
    )


def test_a_report_child_is_not_nested_profile_evidence(tmp_path: Path) -> None:
    """A report-only nested directory is not mistaken for a profile.

    ``cargo build --timings`` writes ``cargo-timings/cargo-timing-<stamp>.html``
    inside a target. The nested level of the layout rule exists for
    ``target/<triple>/<profile>``, so a directory whose children are reports
    must not be read as the triple half of that pair. The enclosing profile is
    left otherwise empty so the nested branch is the only thing that could
    supply the evidence.
    """
    target = tmp_path / TARGET_DIR
    target.mkdir()
    (target / ".rustc_info.json").touch()
    timings = _profile(target, "debug") / "cargo-timings"
    timings.mkdir()
    (timings / "cargo-timing-20261024T000000Z.html").touch()

    scan = scan_target_dirs(tmp_path)

    assert not scan.targets, "a report-only child was accepted as profile evidence"
    assert _reasons(scan) == {target: "lookalike"}, (
        "the report-only directory was not reported as a lookalike"
    )


def test_a_marked_target_is_discovered_without_cargo_evidence(tmp_path: Path) -> None:
    """The cache marker stays sufficient evidence on its own."""
    target = tmp_path / TARGET_DIR
    target.mkdir()
    (target / CACHEDIR_TAG).touch()

    assert find_target_dirs(tmp_path) == [target], (
        "a cache-marked target was not discovered"
    )


def test_a_bare_target_name_is_not_enough(tmp_path: Path) -> None:
    """A directory that is only named ``target`` is reported, never deleted."""
    lookalike = tmp_path / TARGET_DIR
    lookalike.mkdir()
    (lookalike / "notes.txt").write_text("source, not output", encoding="utf-8")

    scan = scan_target_dirs(tmp_path)

    assert not scan.targets, "a bare target name was accepted as build output"
    assert _reasons(scan) == {lookalike: "unmarked-no-evidence"}, (
        "the unproven directory was not reported as omitted"
    )


def test_rustc_info_without_profile_evidence_is_a_lookalike(tmp_path: Path) -> None:
    """Rustc info alone is not enough: the profile must carry Cargo artefacts."""
    lookalike = tmp_path / "cargo-target"
    lookalike.mkdir()
    (lookalike / ".rustc_info.json").touch()
    (lookalike / "debug").mkdir()

    scan = scan_target_dirs(tmp_path)

    assert not scan.targets, "an empty profile was accepted as Cargo output"
    assert _reasons(scan) == {lookalike: "lookalike"}, (
        "the lookalike was not reported with its reason"
    )


def test_a_directory_holding_a_manifest_is_a_source_tree(tmp_path: Path) -> None:
    """A checked-out crate is never treated as build output."""
    crate = _cargo_target(tmp_path, "crate")
    (crate / "Cargo.toml").write_text("[package]\n", encoding="utf-8")

    scan = scan_target_dirs(tmp_path)

    assert not scan.targets, "a source tree was accepted as build output"
    assert _reasons(scan) == {crate: "source-tree"}, (
        "the source tree was not reported with its reason"
    )


def test_a_directory_holding_a_repository_is_a_source_tree(tmp_path: Path) -> None:
    """A repository checkout is protected even when it looks like output."""
    checkout = _cargo_target(tmp_path, "checkout")
    (checkout / ".git").mkdir()

    scan = scan_target_dirs(tmp_path)

    assert not scan.targets, "a repository checkout was accepted as output"
    assert _reasons(scan) == {checkout: "source-tree"}, (
        "the checkout was not reported with its reason"
    )


@pytest.mark.parametrize("marker", ["Cargo.toml", ".git"])
def test_a_marked_source_tree_is_still_refused(tmp_path: Path, marker: str) -> None:
    """The cache marker cannot overrule a source marker.

    A directory can be both: a checkout may carry ``CACHEDIR.TAG`` because an
    unrelated tool wrote it. Accepting the marker first would hand the checkout
    to ``shutil.rmtree``, so the source markers are refused before the marker
    is honoured.

    Parameters
    ----------
    tmp_path : Path
        Root directory searched for build output.
    marker : str
        Source marker placed beside the cache marker.
    """
    checkout = _cargo_target(tmp_path, "checkout")
    (checkout / CACHEDIR_TAG).touch()
    if marker == ".git":
        (checkout / marker).mkdir()
    else:
        (checkout / marker).write_text("[package]\n", encoding="utf-8")

    scan = scan_target_dirs(tmp_path)

    assert not scan.targets, "a marked source tree was accepted as build output"
    assert _reasons(scan) == {checkout: "source-tree"}, (
        "the marked source tree was not reported with its reason"
    )


def test_a_symlinked_cargo_output_is_reported_rather_than_followed(
    tmp_path: Path,
) -> None:
    """A symlink is omitted, so a link can never widen the deletion boundary."""
    external = _cargo_target(tmp_path / "external", "build-output")
    root = tmp_path / "root"
    root.mkdir()
    (root / "linked-target").symlink_to(external, target_is_directory=True)

    scan = scan_target_dirs(root)

    assert not scan.targets, "a symlinked build output was accepted"
    assert _reasons(scan) == {root / "linked-target": "symlink"}, (
        "the symlinked output was not reported as omitted"
    )


def test_an_accepted_target_is_not_traversed_again(tmp_path: Path) -> None:
    """An accepted candidate ends the search below it."""
    target = _cargo_target(tmp_path, TARGET_DIR)
    nested = _cargo_target(target, "nested-target")

    scan = scan_target_dirs(tmp_path)

    assert scan.targets == (target,), "the outer target was not the only discovery"
    assert not any(omission.path == nested for omission in scan.omissions), (
        "a directory inside an accepted target was inspected as a candidate"
    )


@pytest.mark.parametrize("dirname", [".cargo", ".rustup"])
def test_toolchain_state_directories_are_not_traversed(
    tmp_path: Path, dirname: str
) -> None:
    """Cargo and rustup state is never scanned or deleted."""
    state = tmp_path / dirname
    state.mkdir()
    (state / "registry").mkdir()
    (state / "registry" / TARGET_DIR).mkdir()
    (state / "registry" / TARGET_DIR / CACHEDIR_TAG).touch()

    assert not find_target_dirs(tmp_path), f"{dirname} was traversed for targets"


def test_the_scan_root_itself_is_never_a_candidate(tmp_path: Path) -> None:
    """A root that looks like Cargo output is not offered for deletion."""
    (tmp_path / ".rustc_info.json").touch()
    _profile(tmp_path, "debug", artefacts=(_CARGO_LOCK,))

    assert not find_target_dirs(tmp_path), "the scan root was classified as a target"


def test_omissions_are_reported_without_making_the_scan_incomplete(
    tmp_path: Path,
) -> None:
    """Declining a candidate is a decision, not a failed inspection."""
    (tmp_path / TARGET_DIR).mkdir()

    scan = scan_target_dirs(tmp_path)

    assert scan.complete, "a deliberate omission was reported as a failed inspection"
    assert len(scan.omissions) == 1, "the omission was not recorded"


def test_an_unreadable_candidate_still_makes_the_scan_incomplete(
    tmp_path: Path,
) -> None:
    """A candidate that cannot be inspected is still a hard failure."""
    candidate = tmp_path / TARGET_DIR
    candidate.mkdir()
    (candidate / "data").write_text("x", encoding="utf-8")
    candidate.chmod(0o000)
    try:
        scan = scan_target_dirs(tmp_path)
    finally:
        candidate.chmod(0o700)

    if os.geteuid() == 0:
        pytest.skip("root bypasses directory permission bits")
    assert not scan.complete, "an uninspectable candidate did not fail the scan"


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses directory permission bits")
def test_an_uninspectable_linked_candidate_makes_the_scan_incomplete(
    tmp_path: Path,
) -> None:
    """A link whose destination cannot be read leaves the candidate question open.

    A link is omitted only when its destination can be shown to hold Cargo
    output. When that cannot be established the link is neither followed nor
    declared irrelevant: what it points at was never seen, so the scan must not
    be treated as complete.
    """
    external = tmp_path / "external"
    (external / "debug").mkdir(parents=True)
    external.chmod(0o400)
    root = tmp_path / "root"
    root.mkdir()
    (root / "linked-build-output").symlink_to(external, target_is_directory=True)
    try:
        scan = scan_target_dirs(root)
    finally:
        external.chmod(0o700)

    assert not scan.complete, "an uninspectable linked candidate did not fail the scan"


def test_an_unmarked_target_inside_a_hidden_directory_is_discovered(
    tmp_path: Path,
) -> None:
    """Hidden directories are still searched; only known state is skipped."""
    hidden = tmp_path / ".worktrees"
    _cargo_target(hidden, TARGET_DIR)

    assert len(find_target_dirs(tmp_path)) == 1, (
        "build output inside a hidden directory was not discovered"
    )


@pytest.mark.parametrize("artefact", [_CARGO_LOCK, "deps", ".fingerprint", "build"])
def test_each_cargo_profile_artefact_is_sufficient_evidence(
    tmp_path: Path, artefact: str
) -> None:
    """Any artefact Cargo writes into a profile proves the layout.

    Parameters
    ----------
    tmp_path : Path
        Root directory searched for build output.
    artefact : str
        Single Cargo profile artefact used as the only positive evidence.
    """
    target = _cargo_target(tmp_path, TARGET_DIR, artefacts=(artefact,))

    assert find_target_dirs(tmp_path) == [target], (
        f"{artefact} was not accepted as Cargo profile evidence"
    )
