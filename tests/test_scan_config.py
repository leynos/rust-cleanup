"""Tests for the ``[scan]`` table of the XDG configuration file.

The scan table is the only file-based way to change which roots a sweep walks,
so these tests pin how it is parsed: roots are expanded onto the policy, an
absent table stays distinct from an explicitly empty list, and every malformed
shape is a configuration error rather than a silently narrowed scan.
"""

import os
import pwd
from pathlib import Path

import pytest

from rust_cleanup.config import ConfigError
from rust_cleanup.config_loading import default_config_path, default_home, load_config


def _write_config(home: Path, contents: str) -> Path:
    """Write a rust-cleanup TOML file under the isolated home.

    Returns
    -------
    Path
        The written configuration path.
    """
    path = home / "config.toml"
    path.write_text(contents, encoding="utf-8")
    return path


def test_scan_roots_are_loaded_and_expanded(home: Path) -> None:
    """A ``[scan]`` list reaches the policy as expanded absolute paths."""
    (home / ".lody/repos").mkdir(parents=True)
    (home / "Projects").mkdir()
    path = _write_config(home, '[scan]\nroots = ["~/.lody/repos", "~/Projects"]\n')

    config = load_config(path)

    assert config.scan.roots == (
        (home / ".lody/repos").resolve(),
        (home / "Projects").resolve(),
    ), "configured scan roots were not expanded onto the policy"


def test_an_absent_scan_table_means_built_in_roots(home: Path) -> None:
    """With no ``[scan]`` table the policy leaves the choice to the defaults."""
    path = _write_config(home, "[cleanup.normal]\nstale_after = '3h'\n")

    config = load_config(path)

    assert config.scan.roots is None, (
        "an absent [scan] table was mistaken for an explicit root list"
    )
    assert config.normal.stale_after == 3 * 60 * 60, "retention settings were lost"


def test_an_empty_scan_list_is_retained_as_empty(home: Path) -> None:
    """An explicit empty list stays distinct from an absent table."""
    path = _write_config(home, "[scan]\nroots = []\n")

    config = load_config(path)

    assert not config.scan.roots, (
        "an explicit empty root list fell back to the built-ins"
    )


@pytest.mark.parametrize(
    "contents",
    [
        '[scan]\nunexpected = ["~/x"]\n',
        '[scan]\nroots = "~/x"\n',
        "[scan]\nroots = [1]\n",
        '[scan]\nroots = ["~user/x"]\n',
        '[scan]\nroots = ["relative/x"]\n',
    ],
    ids=["unknown-key", "not-a-list", "not-strings", "user-tilde", "relative"],
)
def test_invalid_scan_configuration_is_rejected(home: Path, contents: str) -> None:
    """Every malformed ``[scan]`` shape is a configuration error."""
    path = _write_config(home, contents)

    with pytest.raises(ConfigError):
        load_config(path)


def test_relative_home_uses_account_home_for_configuration_and_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Configuration and scan roots use the same absolute account home."""
    monkeypatch.setenv("HOME", "relative-home")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    account_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    assert default_home() == account_home, "scan roots must use the account home"
    assert default_config_path() == account_home / ".config/rust-cleanup/config.toml", (
        "configuration must use the same account home"
    )
