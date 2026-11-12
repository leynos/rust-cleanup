"""Contracts for what the command line renders from one sweep outcome.

The sweep returns what it observed and writes nothing itself, so message
wording, ordering, and stream selection are decided here. These tests drive the
entry point with a scripted outcome and assert the rendered lines exactly: the
selected mode and every planned candidate for a dry run, an empty plan that
reports zero candidates, protected targets only under ``--verbose``, deletion
failures on standard error, and a refusal to report success for an incomplete
scan.

Each message is pinned on its own here; the Syrupy snapshot at the end of the
module pins the report they compose, with the temporary root normalised.
Declined candidates and the empty-scan notice are rendered from the same
outcome but pinned in :mod:`test_cli_omissions`.
"""

import typing as typ

from conftest import main_with_outcome
from rendering_support import filesystem_diagnostics as _filesystem_diagnostics
from rendering_support import outcome as _outcome

from rust_cleanup import TARGET_DIR
from rust_cleanup.diagnostics import DeletionFailure
from rust_cleanup.observability import EVENT_PREFIX
from rust_cleanup.pressure import Mode

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest
    from syrupy.assertion import SnapshotAssertion

# Stands in for the cleanup root in a snapshot: pytest names the temporary
# directory differently on every run.
_SNAPSHOT_ROOT = "<root>"


def test_main_prints_the_selected_mode_and_every_planned_candidate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A dry run names the policy and every planned deletion in order.

    Parameters
    ----------
    tmp_path : Path
        Root directory passed to the command line.
    capsys : pytest.CaptureFixture[str]
        Capture fixture holding the rendered plan.
    """
    first = tmp_path / "first" / TARGET_DIR
    second = tmp_path / "second" / TARGET_DIR
    outcome = _outcome(_filesystem_diagnostics(tmp_path, planned=[first, second]))

    result = main_with_outcome(tmp_path, outcome, "--dry-run")

    captured = capsys.readouterr()
    assert result == 0, "a dry run should return success"
    assert captured.out.splitlines() == [
        "  Would clean (mode=pressure): 2 candidate(s)",
        f"  Would delete (mode=pressure): {first}",
        f"  Would delete (mode=pressure): {second}",
        "Would delete 2 of 2 target directories",
    ], f"the dry-run rendering changed: {captured.out!r}"


def test_main_prints_protected_targets_only_in_verbose_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Protected targets are named under ``--verbose`` and hidden without it.

    Parameters
    ----------
    tmp_path : Path
        Root directory passed to the command line.
    capsys : pytest.CaptureFixture[str]
        Capture fixture holding the rendered report.
    """
    active = tmp_path / "active" / TARGET_DIR
    outcome = _outcome(
        _filesystem_diagnostics(tmp_path, protected=[active]),
        scanned=1,
        deleted=0,
    )

    quiet = main_with_outcome(tmp_path, outcome)
    quiet_output = capsys.readouterr().out
    verbose = main_with_outcome(tmp_path, outcome, "--verbose")
    verbose_output = capsys.readouterr().out

    assert quiet == 0, "the sweep failed unexpectedly"
    assert verbose == 0, "the sweep failed unexpectedly"
    assert not quiet_output, f"an idle quiet run printed: {quiet_output!r}"
    assert verbose_output.splitlines() == [
        f"  Keeping (protected): {active}",
        "Deleted 0 of 1 target directories",
    ], f"the verbose rendering changed: {verbose_output!r}"


def test_main_prints_summary_for_verbose_execution(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verbose commands print a summary even when no target was deleted.

    Parameters
    ----------
    tmp_path : Path
        Root directory passed to the command line.
    capsys : pytest.CaptureFixture[str]
        Capture fixture holding the printed summary.
    """
    outcome = _outcome(_filesystem_diagnostics(tmp_path), scanned=4, deleted=0)

    result = main_with_outcome(tmp_path, outcome, "--verbose")

    captured = capsys.readouterr()
    assert result == 0, "verbose cleanup should return success"
    assert captured.out == "Deleted 0 of 4 target directories\n", (
        "verbose summary changed"
    )


def test_main_reports_deletion_failures_on_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A failed deletion is rendered on stderr without failing the run.

    Parameters
    ----------
    tmp_path : Path
        Root directory passed to the command line.
    capsys : pytest.CaptureFixture[str]
        Capture fixture holding the rendered report.
    """
    stuck = tmp_path / "stuck" / TARGET_DIR
    outcome = _outcome(
        _filesystem_diagnostics(
            tmp_path, failures=[DeletionFailure(stuck, "permission denied")]
        )
    )

    result = main_with_outcome(tmp_path, outcome)

    captured = capsys.readouterr()
    assert result == 0, "a failed deletion should not fail the invocation"
    assert f"Error deleting {stuck}: permission denied\n" in captured.err, (
        f"the deletion failure was not rendered: {captured.err!r}"
    )


def test_main_refuses_to_report_success_for_an_incomplete_scan(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An incomplete discovery is a failure, not a clean sweep.

    Parameters
    ----------
    tmp_path : Path
        Root directory passed to the command line.
    capsys : pytest.CaptureFixture[str]
        Capture fixture holding the rendered report.
    """
    outcome = _outcome(_filesystem_diagnostics(tmp_path), discovery_failed=True)

    result = main_with_outcome(tmp_path, outcome)

    captured = capsys.readouterr()
    assert result == 1, "an incomplete scan should return failure"
    assert "the directory scan was incomplete" in captured.err, (
        f"the incomplete-scan error text changed: {captured.err!r}"
    )


def test_main_reports_an_empty_normal_dry_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An empty dry run reports its selected normal-mode plan.

    Nothing eligible is not the same as nothing decided: the renderer must
    still name the mode in force and report zero candidates rather than print
    no plan at all.

    Parameters
    ----------
    tmp_path : Path
        Root directory passed to the command line.
    capsys : pytest.CaptureFixture[str]
        Capture fixture holding the rendered plan.
    """
    diagnostics = _filesystem_diagnostics(tmp_path, planned=[], protected=[])
    diagnostics.selected_mode = Mode.NORMAL
    diagnostics.deleted = 0
    diagnostics.scanned = 0
    outcome = _outcome(diagnostics, scanned=0, deleted=0)

    result = main_with_outcome(tmp_path, outcome, "--dry-run")

    captured = capsys.readouterr()
    assert result == 0, "an empty dry run should return success"
    assert captured.out.splitlines() == [
        "  Would clean (mode=normal): 0 candidate(s)",
        "Would delete 0 of 0 target directories",
    ], f"the empty dry-run rendering changed: {captured.out!r}"
    assert "Error" not in captured.err, (
        f"an empty dry run reported an error: {captured.err!r}"
    )


def _rendered_lines(text: str, root: Path) -> list[str]:
    """Return the readable lines of one stream with the temporary root replaced.

    Standard error carries the structured event stream alongside the readable
    failures. That stream has its own snapshot in :mod:`test_observability_cli`,
    where the correlation token and the elapsed time are masked; dropping it
    here keeps this snapshot to what an operator reads.

    Parameters
    ----------
    text : str
        Captured content of one stream.
    root : Path
        Cleanup root the command line was given.

    Returns
    -------
    list[str]
        The stream's readable lines, in order, with ``root`` named by
        :data:`_SNAPSHOT_ROOT`.
    """
    return [
        line.replace(str(root), _SNAPSHOT_ROOT)
        for line in text.splitlines()
        if not line.startswith(EVENT_PREFIX)
    ]


def test_main_renders_the_reviewed_transcript(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    snapshot: SnapshotAssertion,
) -> None:
    """Pin the assembled report for a sweep that takes every rendering path.

    The contracts above pin each message in isolation, which cannot catch a
    block that is dropped, duplicated, or moved between streams. This snapshot
    covers the widest the rendered surface gets: a verbose dry run reporting a
    planned candidate, a protected target, and a failed deletion at once.

    Parameters
    ----------
    tmp_path : Path
        Root directory passed to the command line.
    capsys : pytest.CaptureFixture[str]
        Capture fixture holding both rendered streams.
    snapshot : SnapshotAssertion
        Syrupy fixture holding the reviewed rendering.
    """
    planned = tmp_path / "stale" / TARGET_DIR
    active = tmp_path / "active" / TARGET_DIR
    stuck = tmp_path / "stuck" / TARGET_DIR
    outcome = _outcome(
        _filesystem_diagnostics(
            tmp_path,
            planned=[planned],
            protected=[active],
            failures=[DeletionFailure(stuck, "permission denied")],
        ),
        scanned=2,
        deleted=1,
    )

    result = main_with_outcome(tmp_path, outcome, "--dry-run", "--verbose")

    captured = capsys.readouterr()
    assert result == 0, "the scripted sweep failed unexpectedly"
    rendered = {
        "stdout": _rendered_lines(captured.out, tmp_path),
        "stderr": _rendered_lines(captured.err, tmp_path),
    }
    assert rendered == snapshot, "the rendered transcript changed"
