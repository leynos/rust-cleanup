"""Tests for scan-root configuration, expansion, and selection.

The scan roots are the outer boundary of every deletion the sweep may make, so
these tests pin the whole precedence chain: explicit command-line roots, then
the ``[scan] roots`` list from the XDG TOML file, then the built-in defaults.
They also pin the two transformations that decide which roots actually run:
home expansion and de-duplication of overlapping roots.
"""

import typing as typ

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from scan_root_support import make_dirs as _make_dirs
from scan_root_support import reasons as _reasons

from rust_cleanup.roots import ScanRootError, select_scan_roots
from rust_cleanup.scan_root_expressions import DEFAULT_SCAN_ROOT_EXPRESSIONS

if typ.TYPE_CHECKING:
    from pathlib import Path


def test_defaults_cover_lody_and_projects(home: Path) -> None:
    """The shipped defaults scan both the Lody tree and the projects tree."""
    _make_dirs(home / ".lody/repos", home / "Projects")

    selection = select_scan_roots(explicit=(), configured=None, home=home)

    assert selection.roots == (
        (home / ".lody/repos").resolve(),
        (home / "Projects").resolve(),
    ), "the built-in roots changed or lost their documented order"
    assert not selection.skipped, "available default roots were reported as skipped"
    assert selection.inspection_failed is False, "available defaults failed inspection"
    assert DEFAULT_SCAN_ROOT_EXPRESSIONS == ("~/.lody/repos", "~/Projects"), (
        "the documented default root expressions changed"
    )


def test_configured_roots_replace_the_defaults(home: Path) -> None:
    """A configured list is authoritative and does not extend the defaults."""
    _make_dirs(home / ".lody/repos", home / "Projects", home / "elsewhere")

    selection = select_scan_roots(
        explicit=(), configured=(home / "elsewhere",), home=home
    )

    assert selection.roots == ((home / "elsewhere").resolve(),), (
        "configured roots did not replace the built-ins"
    )


def test_explicit_roots_override_configured_roots(home: Path) -> None:
    """Command-line roots outrank the configured list entirely."""
    _make_dirs(home / "configured", home / "explicit")

    selection = select_scan_roots(
        explicit=(home / "explicit",),
        configured=(home / "configured",),
        home=home,
    )

    assert selection.roots == ((home / "explicit").resolve(),), (
        "an explicit root did not replace the configured roots"
    )


def test_an_empty_configured_list_scans_nothing(home: Path) -> None:
    """An explicitly empty list replaces the defaults with no roots at all."""
    _make_dirs(home / ".lody/repos", home / "Projects")

    selection = select_scan_roots(explicit=(), configured=(), home=home)

    assert not selection.roots, "an empty configured list fell back to the defaults"
    assert not selection.skipped, "an empty list should not report skipped roots"


def test_a_skipped_root_records_whether_it_was_a_built_in(home: Path) -> None:
    """Provenance travels with the skip so the command line can stay quiet.

    A missing built-in default is an absence the host simply has, while a
    missing configured root is an instruction that went unanswered. Only the
    first may be withheld from the timed run's standard error, so the two have
    to be distinguishable after selection.
    """
    _make_dirs(home / "Projects")

    from_defaults = select_scan_roots(explicit=(), configured=None, home=home)
    from_configuration = select_scan_roots(
        explicit=(), configured=(home / "elsewhere",), home=home
    )

    assert [item.builtin for item in from_defaults.skipped] == [True], (
        "a skipped built-in root was not marked as built in"
    )
    assert [item.builtin for item in from_configuration.skipped] == [False], (
        "a skipped configured root was mistaken for a built-in"
    )


def test_a_missing_configured_root_does_not_suppress_the_other(home: Path) -> None:
    """One unavailable configured root leaves the remaining roots scanning."""
    present = home / "present"
    missing = home / "missing"
    _make_dirs(present)

    selection = select_scan_roots(explicit=(), configured=(missing, present), home=home)

    assert selection.roots == (present.resolve(),), (
        "a missing configured root suppressed a present one"
    )
    assert _reasons(selection.skipped) == {missing.resolve(): "missing"}, (
        "the missing configured root was not reported"
    )


def test_a_configured_file_is_skipped_as_not_a_directory(home: Path) -> None:
    """A configured path that is a file is reported, not traversed."""
    file_path = home / "not-a-directory"
    file_path.write_text("data", encoding="utf-8")

    selection = select_scan_roots(explicit=(), configured=(file_path,), home=home)

    assert not selection.roots, "a file was accepted as a scan root"
    assert _reasons(selection.skipped) == {file_path.resolve(): "not-directory"}, (
        "a configured file was not reported as a non-directory"
    )


def test_a_root_below_a_file_is_skipped_without_stopping_the_run(home: Path) -> None:
    """``ENOTDIR`` means the root cannot exist, not that inspection failed.

    ``stat`` reports a regular file in place of a directory component as
    ``NotADirectoryError``. Treating that as an inspection failure would stop
    the whole sweep because of one unusable configured path, which is exactly
    the suppression the other roots must not suffer.
    """
    file_path = home / "not-a-directory"
    file_path.write_text("data", encoding="utf-8")
    below_file = file_path / "child"
    present = home / "present"
    _make_dirs(present)

    selection = select_scan_roots(
        explicit=(), configured=(below_file, present), home=home
    )

    assert selection.roots == (present.resolve(),), (
        "a root below a file suppressed the usable root"
    )
    assert selection.inspection_failed is False, (
        "a root below a file was treated as uninspectable"
    )
    assert _reasons(selection.skipped) == {below_file.resolve(): "missing"}, (
        "a root below a file was not reported as missing"
    )


def test_a_missing_explicit_root_stops_the_run(home: Path) -> None:
    """An unavailable explicit root is an argument error, not a skip."""
    missing = home / "missing"

    with pytest.raises(ScanRootError, match=f"Path does not exist: {missing}"):
        select_scan_roots(explicit=(missing,), configured=None, home=home)


def test_an_explicit_file_stops_the_run(home: Path) -> None:
    """An explicit path that is not a directory is an argument error."""
    file_path = home / "file"
    file_path.write_text("data", encoding="utf-8")

    with pytest.raises(ScanRootError, match=f"Path is not a directory: {file_path}"):
        select_scan_roots(explicit=(file_path,), configured=None, home=home)


def test_duplicate_and_nested_roots_collapse_to_the_ancestor(home: Path) -> None:
    """Repeated and nested roots scan once, under the outermost directory."""
    outer = home / "projects"
    inner = outer / "nested"
    _make_dirs(inner)

    selection = select_scan_roots(
        explicit=(), configured=(outer, outer, inner), home=home
    )

    assert selection.roots == (outer.resolve(),), (
        "repeated or nested roots were not collapsed"
    )
    assert _reasons(selection.skipped) == {
        outer.resolve(): "duplicate",
        inner.resolve(): "nested",
    }, "the collapsed roots were not reported"


def test_duplicate_explicit_roots_are_walked_once(home: Path) -> None:
    """A command line that names one directory twice still selects it once."""
    projects = home / "projects"
    _make_dirs(projects)

    selection = select_scan_roots(
        explicit=(projects, projects), configured=None, home=home
    )

    assert selection.roots == (projects.resolve(),), (
        "a repeated explicit root was selected more than once"
    )


def test_a_symlinked_explicit_root_and_its_target_are_one_root(home: Path) -> None:
    """Naming a directory and a symlink to it selects the canonical root once."""
    projects = home / "projects"
    _make_dirs(projects)
    link = home / "projects-link"
    link.symlink_to(projects, target_is_directory=True)

    selection = select_scan_roots(explicit=(projects, link), configured=None, home=home)

    assert selection.roots == (projects.resolve(),), (
        "a symlink and its target survived as two explicit roots"
    )


def test_an_ancestor_declared_last_still_covers_its_descendant(home: Path) -> None:
    """Order does not let a descendant survive beside its own ancestor."""
    outer = home / "projects"
    inner = outer / "nested"
    _make_dirs(inner)

    selection = select_scan_roots(explicit=(), configured=(inner, outer), home=home)

    assert selection.roots == (outer.resolve(),), (
        "a descendant declared first survived beside its ancestor"
    )
    assert _reasons(selection.skipped) == {inner.resolve(): "nested"}, (
        "the covered descendant was not reported"
    )


def test_a_symlinked_root_dedupes_against_its_target(home: Path) -> None:
    """Two names for one directory resolve to a single scan root."""
    target = home / "real"
    target.mkdir()
    link = home / "link"
    link.symlink_to(target)

    selection = select_scan_roots(explicit=(), configured=(link, target), home=home)

    assert selection.roots == (target.resolve(),), (
        "a symlinked root was scanned beside its target"
    )
    assert selection.skipped[0].reason == "duplicate", (
        "the resolved duplicate was not reported"
    )


@given(
    names=st.lists(
        st.sampled_from(("projects", "projects/nested", "other")), max_size=6
    )
)
@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_selection_is_ordered_deduplicated_and_containment_free(
    names: list[str], tmp_path: Path, home: Path
) -> None:
    """Every selection is ordered, duplicate-free, and free of nested roots.

    The generated lists mix disjoint, repeated, and ancestor/descendant roots,
    which is the whole space the containment rule has to handle.
    """
    _make_dirs(home / "projects/nested", home / "other")
    configured = tuple(home / name for name in names)

    selection = select_scan_roots(explicit=(), configured=configured, home=home)

    assert len(set(selection.roots)) == len(selection.roots), (
        f"selection retained duplicates: {selection.roots!r}"
    )
    for index, root in enumerate(selection.roots):
        for other in selection.roots[:index] + selection.roots[index + 1 :]:
            assert not root.is_relative_to(other), (
                f"selection kept {root} nested inside {other}"
            )
    chosen = set(configured)
    expected = {
        root
        for root in chosen
        if not any(root != other and root.is_relative_to(other) for other in chosen)
    }
    assert set(selection.roots) == {root.resolve() for root in expected}, (
        "selection did not cover exactly the outermost chosen roots"
    )


def test_a_symlinked_scan_root_is_not_reported_missing(home: Path) -> None:
    """A configured symlink to a directory is a usable root."""
    target = home / "real"
    target.mkdir()
    link = home / "link"
    link.symlink_to(target)

    selection = select_scan_roots(explicit=(), configured=(link,), home=home)

    assert selection.roots == (target.resolve(),), (
        "a symlink to a directory was not accepted as a root"
    )
    assert not selection.skipped, "an accepted symlink was reported as skipped"
