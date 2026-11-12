"""Unit and property tests for configuration and hysteretic pressure policy."""

import re
import typing as typ

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rust_cleanup.config import DEFAULT_CONFIG, ConfigError, parse_duration
from rust_cleanup.config_loading import default_config_path, load_config
from rust_cleanup.pressure import Mode, initial_mode, mode_cutoff, transition_mode

if typ.TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(("text", "seconds"), [("1s", 1), ("10m", 600), ("2h", 7200)])
def test_parse_duration_accepts_supported_units(text: str, seconds: int) -> None:
    """Supported duration suffixes convert to their exact number of seconds."""
    assert parse_duration(text) == seconds, "supported unit must retain its duration"


@pytest.mark.parametrize("text", ["0s", "-1m", "10d", "ten minutes"])
def test_parse_duration_rejects_unsafe_values(text: str) -> None:
    """Non-positive and unsupported durations fail before cleanup can run."""
    with pytest.raises(ConfigError, match="duration"):
        parse_duration(text)


def test_default_cutoffs_match_the_documented_retention_windows() -> None:
    """Defaults keep their documented normal, pressure, and critical cutoffs."""
    assert DEFAULT_CONFIG.normal.stale_after == 7200, (
        "the default normal cutoff is not two hours"
    )
    assert DEFAULT_CONFIG.pressure.stale_after == 600, (
        "the default pressure cutoff is not ten minutes"
    )
    assert DEFAULT_CONFIG.critical.stale_after == 120, (
        "the default critical cutoff is not two minutes"
    )


@pytest.mark.parametrize("override", ["", "relative/config"])
def test_config_path_ignores_an_unusable_xdg_override(
    override: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty or relative ``XDG_CONFIG_HOME`` falls back to the home directory.

    A relative override is ignored rather than resolved against the working
    directory, so the configuration a sweep reads does not depend on where it
    was started.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", override)

    path = default_config_path()

    assert path == tmp_path / ".config/rust-cleanup/config.toml", (
        f"XDG override {override!r} changed the default configuration path"
    )


def test_config_path_honours_an_absolute_xdg_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An absolute ``XDG_CONFIG_HOME`` replaces the home-relative default."""
    override = tmp_path / "xdg"
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(override))

    path = default_config_path()

    assert path == override / "rust-cleanup/config.toml", (
        "an absolute XDG override was not honoured"
    )


def test_config_file_and_cli_overrides_follow_documented_precedence(
    tmp_path: Path,
) -> None:
    """CLI values override TOML values, which override built-in defaults."""
    path = tmp_path / "config.toml"
    path.write_text(
        "[cleanup.normal]\nstale_after = '3h'\n"
        "[cleanup.pressure]\nenter_percent = 81\nexit_percent = 76\n"
        "stale_after = '11m'\n[cleanup.critical]\nenter_percent = 96\n"
        "exit_percent = 91\nstale_after = '3m'\n",
        encoding="utf-8",
    )
    config = load_config(path, {"pressure_stale_after": "12m"})
    assert config.normal.stale_after == 3 * 60 * 60, "TOML normal cutoff lost"
    assert config.pressure.enter_percent == 81, "TOML pressure entry lost"
    assert config.pressure.stale_after == 12 * 60, "CLI must override TOML"
    assert config.critical.exit_percent == 91, "TOML critical exit lost"


def test_known_toml_setting_updates_its_matching_policy_field(tmp_path: Path) -> None:
    """Known TOML settings retain their direct mapping to policy fields."""
    path = tmp_path / "config.toml"
    path.write_text("[cleanup.pressure]\nexit_percent = 74\n", encoding="utf-8")

    config = load_config(path)

    assert config.pressure.exit_percent == 74, "known TOML setting was not applied"


def test_unknown_root_section_is_rejected_before_settings_are_merged(
    tmp_path: Path,
) -> None:
    """Only the cleanup root section is accepted by the configuration schema."""
    path = tmp_path / "config.toml"
    path.write_text("[unexpected]\nvalue = 1\n", encoding="utf-8")

    with pytest.raises(
        ConfigError,
        match=re.escape("unknown configuration section(s): unexpected"),
    ):
        load_config(path)


def test_unknown_and_none_cli_overrides_are_ignored(tmp_path: Path) -> None:
    """Only recognized CLI values intentionally override the loaded settings."""
    config = load_config(
        tmp_path / "missing.toml",
        {"unknown_setting": "ignored", "normal_stale_after": None},
    )

    assert config.normal.stale_after == 2 * 60 * 60, "ignored CLI value changed config"


@pytest.mark.parametrize("section", ["normal", "pressure", "critical"])
def test_unknown_toml_section_keys_are_rejected(tmp_path: Path, section: str) -> None:
    """Every supported TOML subsection rejects keys outside its schema."""
    path = tmp_path / "config.toml"
    path.write_text(f"[cleanup.{section}]\nunexpected = 1\n", encoding="utf-8")
    expected = f"unknown setting(s) in [cleanup.{section}]: unexpected"

    with pytest.raises(ConfigError, match=re.escape(expected)):
        load_config(path)


@pytest.mark.parametrize(
    "contents",
    [
        "[cleanup.pressure]\nenter_percent = 75\nexit_percent = 75\n",
        "[cleanup.critical]\nenter_percent = 80\nexit_percent = 70\n",
        "[cleanup.normal]\nstale_after = '0m'\n",
    ],
)
def test_invalid_config_fails_safely(tmp_path: Path, contents: str) -> None:
    """Unsafe watermarks and cutoffs fail while configuration is still loading."""
    path = tmp_path / "config.toml"
    path.write_text(contents, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        (79.999, Mode.NORMAL),
        (80, Mode.PRESSURE),
        (94.999, Mode.PRESSURE),
        (95, Mode.CRITICAL),
    ],
)
def test_initial_mode_includes_entry_threshold_boundaries(
    usage: float, expected: Mode
) -> None:
    """Entry percentages are inclusive, matching the operator-facing policy."""
    assert initial_mode(usage, DEFAULT_CONFIG) is expected, (
        "entry boundary chose wrong mode"
    )


@pytest.mark.parametrize(
    ("previous", "usage", "expected"),
    [
        (Mode.NORMAL, 80, Mode.PRESSURE),
        (Mode.PRESSURE, 74.999, Mode.NORMAL),
        (Mode.PRESSURE, 75, Mode.PRESSURE),
        (Mode.PRESSURE, 95, Mode.CRITICAL),
        (Mode.CRITICAL, 89.999, Mode.PRESSURE),
        (Mode.CRITICAL, 90, Mode.CRITICAL),
        (Mode.CRITICAL, 74.999, Mode.NORMAL),
    ],
)
def test_transition_mode_obeys_all_hysteresis_boundaries(
    previous: Mode, usage: float, expected: Mode
) -> None:
    """Every transition follows the persisted mode rather than a raw snapshot."""
    assert transition_mode(previous, usage, DEFAULT_CONFIG) is expected, (
        "transition broke hysteresis"
    )


def test_critical_recovery_watermark_survives_a_lower_pressure_watermark(
    tmp_path: Path,
) -> None:
    """A persisted critical filesystem recovers on its own watermark."""
    path = tmp_path / "config.toml"
    path.write_text(
        "[cleanup.pressure]\n"
        "enter_percent = 75\n"
        "exit_percent = 74\n"
        "[cleanup.critical]\n"
        "enter_percent = 90\n"
        "exit_percent = 70\n",
        encoding="utf-8",
    )
    config = load_config(path)

    assert transition_mode(Mode.CRITICAL, 73, config) is Mode.CRITICAL, (
        "an observation above critical.exit_percent dropped a critical filesystem"
    )
    assert transition_mode(Mode.CRITICAL, 69.999, config) is Mode.NORMAL, (
        "a filesystem below both recovery watermarks did not return to normal"
    )


@given(st.lists(st.floats(min_value=0, max_value=100, allow_nan=False), min_size=1))
def test_transition_mode_always_produces_a_valid_mode(
    observations: list[float],
) -> None:
    """Arbitrary pressure observations can be fed through the state machine."""
    mode = Mode.NORMAL
    for usage in observations:
        mode = transition_mode(mode, usage, DEFAULT_CONFIG)
        assert mode in Mode, "transition returned an unknown mode"
        assert mode_cutoff(mode, DEFAULT_CONFIG) > 0, "selected mode needs a cutoff"
