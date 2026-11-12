"""Command-line failures when scan roots require an unavailable home."""

import typing as typ
from unittest import mock

from rust_cleanup import cli

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_default_roots_report_an_unavailable_account_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Built-in roots fail clearly when no absolute home can be resolved."""
    config = tmp_path / "config.toml"
    config.write_text("", encoding="utf-8")
    monkeypatch.setenv("HOME", "relative-home")
    with mock.patch("rust_cleanup.xdg.pwd.getpwuid", side_effect=KeyError):
        status = cli.main(["--config", str(config)])
    assert status == 2, "missing home must produce a configuration failure"
    assert "cannot determine an absolute home directory" in capsys.readouterr().err, (
        "home failure should be explained"
    )
