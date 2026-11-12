"""Validation of the optional ``[scan]`` configuration table.

The scan table is the only place an operator can widen or narrow the roots a
sweep walks, so its schema is checked here rather than inside the loader that
also merges retention settings. Keeping it separate leaves
:mod:`rust_cleanup.config_loading` responsible for precedence and this module
responsible for one table's shape.
"""

import collections.abc as cabc
import typing as typ

from rust_cleanup.config import ConfigError, ScanPolicy
from rust_cleanup.scan_root_expressions import expand_root_expression

if typ.TYPE_CHECKING:
    from pathlib import Path


def _roots_value(scan_data: cabc.Mapping[str, object]) -> list[str] | None:
    """Return the validated ``roots`` list, or ``None`` when it is absent.

    Returns
    -------
    list[str] | None
        The configured expressions, or ``None`` when the table names no roots.

    Raises
    ------
    ConfigError
        If ``roots`` is present but is not a list of strings.
    """
    if "roots" not in scan_data:
        return None
    roots = scan_data["roots"]
    if not isinstance(roots, list) or not all(isinstance(item, str) for item in roots):
        msg = "[scan] roots must be a list of strings"
        raise ConfigError(msg)
    # Every element passed the string check above.
    return typ.cast("list[str]", roots)


def _scan_section(
    config_data: cabc.Mapping[str, object],
) -> cabc.Mapping[str, object] | None:
    """Return the validated ``[scan]`` table, or ``None`` when it is absent.

    A table is validated for unknown keys here, so the policy builder only has
    to read the one setting the schema defines.

    Returns
    -------
    collections.abc.Mapping[str, object] | None
        The scan table, or ``None`` when the file has no ``[scan]`` table.

    Raises
    ------
    ConfigError
        If the table is not a table or carries an unknown setting.
    """
    if "scan" not in config_data:
        return None
    scan_data = _scan_table(config_data["scan"])
    unknown_keys = set(scan_data) - {"roots"}
    if unknown_keys:
        msg = f"unknown setting(s) in [scan]: {', '.join(sorted(unknown_keys))}"
        raise ConfigError(msg)
    return scan_data


def scan_policy(
    config_data: cabc.Mapping[str, object], home: Path | cabc.Callable[[], Path]
) -> ScanPolicy:
    """Validate and expand the optional ``[scan]`` table.

    An absent table means the built-in roots apply. A present table keeps its
    ``roots`` list exactly, so an explicit empty list scans nothing rather
    than silently falling back to the defaults.

    Parameters
    ----------
    config_data : collections.abc.Mapping[str, object]
        The parsed TOML root table.
    home : pathlib.Path or Callable[[], pathlib.Path]
        Home directory or lazy resolver for leading tilde expressions.

    Returns
    -------
    ScanPolicy
        The expanded scan-root policy.

    Raises
    ------
    ConfigError
        If the table, its keys, or any expression are invalid.

    Examples
    --------
    >>> scan_policy({}, home=Path("/home/dev")).roots is None
    True
    """  # ruff: ignore[docstring-extraneous-exception] - the helpers raise it.
    scan_data = _scan_section(config_data)
    roots = None if scan_data is None else _roots_value(scan_data)
    if roots is None:
        return ScanPolicy()
    return ScanPolicy(
        roots=tuple(expand_root_expression(item, home=home) for item in roots)
    )


def _scan_table(value: object) -> cabc.Mapping[str, object]:
    """Return the ``[scan]`` table or raise a clear configuration error.

    Returns
    -------
    collections.abc.Mapping[str, object]
        The validated scan table.

    Raises
    ------
    ConfigError
        If the value is not a table.
    """
    if not isinstance(value, cabc.Mapping):
        msg = "[scan] must be a TOML table"
        raise ConfigError(msg)
    # TOML tables have string keys, including this optional scan table.
    return typ.cast("cabc.Mapping[str, object]", value)
