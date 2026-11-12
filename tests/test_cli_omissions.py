"""Contracts for how the command line reports what discovery declined.

A sweep that found nothing and a sweep that refused everything it found produce
the same empty plan, and that ambiguity is what let the original discovery gap
pass unnoticed. The renderer must therefore name each declined candidate under
``--verbose`` and explain an empty scan that declined something, without
printing either when a quiet run genuinely found nothing.

The plan, protected-target, failure, and snapshot contracts for the same
outcome live in :mod:`test_cli_rendering`.
"""

import typing as typ

from conftest import main_with_outcome
from rendering_support import filesystem_diagnostics as _filesystem_diagnostics
from rendering_support import outcome as _outcome

from rust_cleanup import TARGET_DIR
from rust_cleanup.discovery import Omission

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_main_reports_declined_candidates_with_their_reasons(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A verbose empty run names what discovery refused, and why.

    Parameters
    ----------
    tmp_path : Path
        Root directory passed to the command line.
    capsys : pytest.CaptureFixture[str]
        Capture fixture holding the rendered report.
    """
    lookalike = tmp_path / "lookalike" / TARGET_DIR
    outcome = _outcome(
        _filesystem_diagnostics(tmp_path, planned=[], protected=[]),
        scanned=0,
        deleted=0,
        roots=(tmp_path,),
        omissions=(Omission(lookalike, "unmarked-no-evidence"),),
    )

    result = main_with_outcome(tmp_path, outcome, "--verbose")

    captured = capsys.readouterr()
    assert result == 0, "a verbose empty run should return success"
    assert f"  Not a target (unmarked-no-evidence): {lookalike}\n" in captured.out, (
        f"the omission was not named: {captured.out!r}"
    )


def test_main_explains_an_empty_scan_only_when_candidates_were_declined(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The notice distinguishes a quiet scan from one that refused everything.

    Parameters
    ----------
    tmp_path : Path
        Root directory passed to the command line.
    capsys : pytest.CaptureFixture[str]
        Capture fixture holding the rendered report.
    """
    diagnostics = _filesystem_diagnostics(tmp_path, planned=[], protected=[])
    diagnostics.scanned = 0
    diagnostics.deleted = 0
    empty = _outcome(diagnostics, scanned=0, deleted=0, roots=(tmp_path,))

    quiet_result = main_with_outcome(tmp_path, empty)
    quiet_output = capsys.readouterr().out

    declined = _outcome(
        diagnostics,
        scanned=0,
        deleted=0,
        roots=(tmp_path,),
        omissions=(Omission(tmp_path / "lookalike", "unmarked-no-evidence"),),
    )
    declined_result = main_with_outcome(tmp_path, declined)
    declined_output = capsys.readouterr().out

    assert quiet_result == 0, "the empty runs failed"
    assert declined_result == 0, "the empty runs failed"
    assert not quiet_output, (
        f"a quiet run with nothing declined printed a notice: {quiet_output!r}"
    )
    assert "no target directories found in 1 scan root(s)" in declined_output, (
        f"the empty-scan notice was not rendered: {declined_output!r}"
    )
    assert "rerun with --verbose to list them" in declined_output, (
        f"the quiet notice did not offer the verbose listing: {declined_output!r}"
    )
