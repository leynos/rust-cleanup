"""Regression coverage for home-resolution failures at the CLI boundary."""

import typing as typ
from unittest import mock

from rust_cleanup import CleanupError, cli

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_main_reports_an_unresolvable_home_as_configuration_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Home-resolution errors are reported instead of escaping as tracebacks.

    The lookup can fail before any file is read, when an unusable ``HOME``
    leaves no directory to place the configuration in. That is the same class
    of problem as an unreadable file, so the command must report it and return
    the configuration status rather than escaping with a traceback.

    Parameters
    ----------
    tmp_path : Path
        Root directory passed to the command line.
    capsys : pytest.CaptureFixture[str]
        Capture fixture holding the reported stderr.
    """
    with mock.patch.object(
        cli,
        "load_config",
        side_effect=CleanupError("cannot determine an absolute home directory"),
    ):
        result = cli.main([str(tmp_path)])

    captured = capsys.readouterr()
    assert result == 2, "an unresolvable home should return status two"
    assert "Error: cannot determine an absolute home directory\n" in captured.err, (
        f"the home-resolution error was not reported: {captured.err!r}"
    )
