"""Load, merge, and validate rust-cleanup retention configuration.

Configuration precedence is command-line overrides, then TOML file settings,
then the built-in defaults. Keeping that machinery, and the XDG environment
lookup that locates the default file, here leaves :mod:`rust_cleanup.config` a
small, data-only model that the sweep consumes directly.
"""

import collections.abc as cabc
import os
import tomllib
import typing as typ

from rust_cleanup import xdg
from rust_cleanup.config import (
    DEFAULT_CONFIG,
    CleanupConfig,
    ConfigError,
    PressurePolicy,
    RetentionPolicy,
    parse_duration,
)
from rust_cleanup.scan_config import scan_policy

if typ.TYPE_CHECKING:
    from pathlib import Path

_MAX_PERCENT = 100


def default_config_path(environment: cabc.Mapping[str, str] | None = None) -> Path:
    """Return the XDG configuration location for rust-cleanup.

    Parameters
    ----------
    environment : Mapping[str, str] or None
        Environment to resolve the location from. ``None`` reads the process
        environment; tests pass a mapping rather than mutating it.

    Returns
    -------
    Path
        The configured TOML file location.
    """
    resolved = os.environ if environment is None else environment
    return xdg.config_home(resolved) / "rust-cleanup" / "config.toml"


def default_home() -> Path:
    """Return the home directory configuration expressions expand against.

    An absolute ``HOME`` is preferred; otherwise use the account database,
    matching the XDG fallback used for configuration and state.

    Returns
    -------
    pathlib.Path
        The effective home directory.

    Raises
    ------
    CleanupError
        If neither environment nor account database provides an absolute home.
    """  # ruff: ignore[docstring-extraneous-exception] - raised by user_home.
    return xdg.user_home(os.environ)


def _require_mapping(value: object, context: str) -> cabc.Mapping[str, object]:
    """Return a TOML mapping or raise a clear configuration error.

    Returns
    -------
    collections.abc.Mapping[str, object]
        The validated TOML table.

    Raises
    ------
    ConfigError
        If the value is not a TOML table.
    """
    if not isinstance(value, cabc.Mapping):
        msg = f"{context} must be a TOML table"
        raise ConfigError(msg)
    # TOML parsing supplies string keys; the guard establishes table shape.
    return typ.cast("cabc.Mapping[str, object]", value)


def _duration_value(value: object, name: str) -> int:
    """Validate and parse a TOML duration setting.

    Returns
    -------
    int
        The duration in seconds.

    Raises
    ------
    ConfigError
        If the setting is not a valid duration string.
    """
    if not isinstance(value, str):
        msg = f"{name} must be a duration string"
        raise ConfigError(msg)
    return parse_duration(value)


def _percent_value(value: object, name: str) -> int:
    """Validate a percentage setting.

    Returns
    -------
    int
        The validated percentage.

    Raises
    ------
    ConfigError
        If the setting is not an integer percentage.
    """
    if not _is_percentage(value):
        msg = f"{name} must be an integer percentage from 0 through 100"
        raise ConfigError(msg)
    return value


def _is_percentage(value: object) -> typ.TypeGuard[int]:
    """Return whether a value is an ordinary integer percentage.

    Returns
    -------
    typing.TypeGuard[int]
        Whether the value is an integer from zero through 100.
    """
    return (
        not isinstance(value, bool)
        and isinstance(value, int)
        and 0 <= value <= _MAX_PERCENT
    )


def _load_toml(path: Path) -> cabc.Mapping[str, object]:
    """Load one optional TOML file, returning an empty mapping when absent.

    Returns
    -------
    collections.abc.Mapping[str, object]
        Parsed TOML data, or an empty mapping for a missing file.

    Raises
    ------
    ConfigError
        If the file cannot be read or parsed safely.
    """
    try:
        with path.open("rb") as config_file:
            contents: object = tomllib.load(config_file)
    except FileNotFoundError:
        return {}
    except (OSError, tomllib.TOMLDecodeError) as error:
        msg = f"failed to read configuration {path}: {error}"
        raise ConfigError(msg) from error
    return _require_mapping(contents, "configuration root")


def _merged_settings(
    config_data: cabc.Mapping[str, object],
    cli_overrides: cabc.Mapping[str, object] | None,
) -> dict[str, object]:
    """Merge defaults, TOML data, and explicit CLI values by precedence.

    Returns
    -------
    dict[str, object]
        Unvalidated raw settings with CLI values taking precedence.

    Raises
    ------
    ConfigError
        If the TOML structure has an unknown or invalid section.
    """
    cleanup_data = _cleanup_data(config_data)
    settings: dict[str, object] = {
        "normal_stale_after": DEFAULT_CONFIG.normal.stale_after,
        "pressure_enter_percent": DEFAULT_CONFIG.pressure.enter_percent,
        "pressure_exit_percent": DEFAULT_CONFIG.pressure.exit_percent,
        "pressure_stale_after": DEFAULT_CONFIG.pressure.stale_after,
        "critical_enter_percent": DEFAULT_CONFIG.critical.enter_percent,
        "critical_exit_percent": DEFAULT_CONFIG.critical.exit_percent,
        "critical_stale_after": DEFAULT_CONFIG.critical.stale_after,
    }
    sections = {
        "normal": ("normal_stale_after",),
        "pressure": (
            "pressure_enter_percent",
            "pressure_exit_percent",
            "pressure_stale_after",
        ),
        "critical": (
            "critical_enter_percent",
            "critical_exit_percent",
            "critical_stale_after",
        ),
    }
    keys = {
        "normal_stale_after": "stale_after",
        "pressure_enter_percent": "enter_percent",
        "pressure_exit_percent": "exit_percent",
        "pressure_stale_after": "stale_after",
        "critical_enter_percent": "enter_percent",
        "critical_exit_percent": "exit_percent",
        "critical_stale_after": "stale_after",
    }
    _apply_toml_sections(settings, cleanup_data, sections, keys)
    unknown_sections = set(cleanup_data) - set(sections)
    if unknown_sections:
        msg = f"unknown [cleanup] section(s): {', '.join(sorted(unknown_sections))}"
        raise ConfigError(msg)
    _apply_cli_overrides(settings, cli_overrides)
    return settings


def _cleanup_data(
    config_data: cabc.Mapping[str, object],
) -> cabc.Mapping[str, object]:
    """Validate the root table and return the cleanup section.

    Returns
    -------
    collections.abc.Mapping[str, object]
        The validated ``[cleanup]`` table.

    Raises
    ------
    ConfigError
        If the root has unknown sections or cleanup is not a table.
    """
    unknown_root_keys = set(config_data) - {"cleanup", "scan"}
    if unknown_root_keys:
        msg = (
            f"unknown configuration section(s): {', '.join(sorted(unknown_root_keys))}"
        )
        raise ConfigError(msg)
    return _require_mapping(config_data.get("cleanup", {}), "[cleanup]")


def _apply_cli_overrides(
    settings: dict[str, object],
    cli_overrides: cabc.Mapping[str, object] | None,
) -> None:
    """Overlay recognized explicit command-line values onto settings."""
    if cli_overrides is None:
        return
    for key, value in cli_overrides.items():
        if key in settings and value is not None:
            settings[key] = value


def _apply_toml_sections(
    settings: dict[str, object],
    cleanup_data: cabc.Mapping[str, object],
    sections: cabc.Mapping[str, tuple[str, ...]],
    keys: cabc.Mapping[str, str],
) -> None:
    """Overlay validated per-mode TOML tables onto default settings."""
    for section, setting_names in sections.items():
        section_data = _require_mapping(
            cleanup_data.get(section, {}), f"[cleanup.{section}]"
        )
        _validate_toml_section_keys(section, section_data, setting_names, keys)
        _overlay_toml_section_settings(settings, section_data, setting_names, keys)


def _validate_toml_section_keys(
    section: str,
    section_data: cabc.Mapping[str, object],
    setting_names: tuple[str, ...],
    keys: cabc.Mapping[str, str],
) -> None:
    """Reject keys outside the selected TOML section's setting set.

    Raises
    ------
    ConfigError
        If a section contains an unknown setting.
    """
    allowed_keys = {keys[name] for name in setting_names}
    unknown_keys = set(section_data) - allowed_keys
    if unknown_keys:
        names = ", ".join(sorted(unknown_keys))
        msg = f"unknown setting(s) in [cleanup.{section}]: {names}"
        raise ConfigError(msg)


def _overlay_toml_section_settings(
    settings: dict[str, object],
    section_data: cabc.Mapping[str, object],
    setting_names: tuple[str, ...],
    keys: cabc.Mapping[str, str],
) -> None:
    """Copy present TOML values into their matching internal settings."""
    for setting_name in setting_names:
        toml_key = keys[setting_name]
        if toml_key in section_data:
            settings[setting_name] = section_data[toml_key]


def _override_duration(value: object, name: str) -> int:
    """Parse a duration supplied by either TOML or the CLI.

    Returns
    -------
    int
        The validated duration in seconds.

    Raises
    ------
    ConfigError
        If the value is not a positive supported duration.
    """
    if isinstance(value, int) and not isinstance(value, bool):
        if value <= 0:
            msg = f"{name} must be positive"
            raise ConfigError(msg)
        return value
    return _duration_value(value, name)


def load_config(
    config_path: Path | None = None,
    cli_overrides: cabc.Mapping[str, object] | None = None,
) -> CleanupConfig:
    """Load validated configuration using CLI, TOML, then default precedence.

    Parameters
    ----------
    config_path : Path | None, default=None
        TOML file to read. ``None`` selects :func:`default_config_path`.

    cli_overrides : cabc.Mapping[str, object] | None, default=None
        Recognised command-line values, which outrank the file and defaults.

    Returns
    -------
    CleanupConfig
        The validated effective retention policy.

    Raises
    ------
    ConfigError
        If configuration parsing or validation fails.
    """
    path = config_path or default_config_path()
    config_data = _load_toml(path)
    settings = _merged_settings(config_data, cli_overrides)
    config = CleanupConfig(
        normal=RetentionPolicy(
            stale_after=_override_duration(
                settings["normal_stale_after"], "normal.stale_after"
            )
        ),
        pressure=PressurePolicy(
            stale_after=_override_duration(
                settings["pressure_stale_after"], "pressure.stale_after"
            ),
            enter_percent=_percent_value(
                settings["pressure_enter_percent"], "pressure.enter_percent"
            ),
            exit_percent=_percent_value(
                settings["pressure_exit_percent"], "pressure.exit_percent"
            ),
        ),
        critical=PressurePolicy(
            stale_after=_override_duration(
                settings["critical_stale_after"], "critical.stale_after"
            ),
            enter_percent=_percent_value(
                settings["critical_enter_percent"], "critical.enter_percent"
            ),
            exit_percent=_percent_value(
                settings["critical_exit_percent"], "critical.exit_percent"
            ),
        ),
        scan=scan_policy(config_data, default_home),
    )
    if config.pressure.exit_percent >= config.pressure.enter_percent:
        msg = "pressure.exit_percent must be lower than pressure.enter_percent"
        raise ConfigError(msg)
    if config.critical.exit_percent >= config.critical.enter_percent:
        msg = "critical.exit_percent must be lower than critical.enter_percent"
        raise ConfigError(msg)
    if config.critical.enter_percent <= config.pressure.enter_percent:
        msg = "critical.enter_percent must be greater than pressure.enter_percent"
        raise ConfigError(msg)
    return config
