"""Pure filesystem-pressure decisions.

The module holds the retention mode, the hysteresis transition table, and the
cut-off and watermark lookups those modes select. Measuring usage and
discovering the mounted filesystem that backs a path are operating-system
operations, so they live in :mod:`rust_cleanup.filesystem` instead.
"""

import dataclasses
import enum
import typing as typ

if typ.TYPE_CHECKING:
    from pathlib import Path

    from rust_cleanup.config import CleanupConfig


class Mode(enum.StrEnum):
    """Retention modes selected from filesystem pressure."""

    NORMAL = "normal"
    PRESSURE = "pressure"
    CRITICAL = "critical"


@dataclasses.dataclass(frozen=True, slots=True)
class FilesystemIdentity:
    """Stable identity for a mounted filesystem.

    Attributes
    ----------
    mount_point : Path
        Canonical mount point of the filesystem.
    device : int
        Numeric device identifier (``st_dev``) of the mount.
    """

    mount_point: Path
    device: int


def initial_mode(usage: float, config: CleanupConfig) -> Mode:
    """Derive a mode from entry watermarks when no prior state exists.

    Parameters
    ----------
    usage : float
        Used-capacity percentage measured for the filesystem.
    config : CleanupConfig
        Retention policies holding the entry watermarks.

    Returns
    -------
    Mode
        The retention mode selected from the entry watermarks.
    """
    if usage >= config.critical.enter_percent:
        return Mode.CRITICAL
    if usage >= config.pressure.enter_percent:
        return Mode.PRESSURE
    return Mode.NORMAL


def transition_mode(previous: Mode, usage: float, config: CleanupConfig) -> Mode:
    """Apply the persisted hysteresis transition table to one observation.

    The critical recovery watermark is evaluated before the pressure one, so a
    filesystem still above its critical recovery threshold is never downgraded
    to normal by a single observation.

    Parameters
    ----------
    previous : Mode
        Persisted mode from the last sweep.
    usage : float
        Used-capacity percentage measured for the filesystem.
    config : CleanupConfig
        Retention policies holding the watermarks.

    Returns
    -------
    Mode
        The next persisted retention mode.
    """
    if previous is Mode.NORMAL:
        return initial_mode(usage, config)
    if usage >= config.critical.enter_percent:
        return Mode.CRITICAL
    if previous is Mode.CRITICAL and usage >= config.critical.exit_percent:
        return Mode.CRITICAL
    if usage < config.pressure.exit_percent:
        return Mode.NORMAL
    if previous is Mode.CRITICAL:
        return Mode.PRESSURE
    return previous


def mode_cutoff(mode: Mode, config: CleanupConfig) -> int:
    """Return the inactivity cutoff in seconds for a selected mode.

    Parameters
    ----------
    mode : Mode
        Selected retention mode.
    config : CleanupConfig
        Retention policies holding each mode's cutoff.

    Returns
    -------
    int
        The configured inactivity cutoff in seconds.
    """
    match mode:
        case Mode.NORMAL:
            return config.normal.stale_after
        case Mode.PRESSURE:
            return config.pressure.stale_after
        case Mode.CRITICAL:
            return config.critical.stale_after


def exit_watermark(mode: Mode, config: CleanupConfig) -> int | None:
    """Return the recovery watermark for a pressure mode, if applicable.

    Parameters
    ----------
    mode : Mode
        Selected retention mode.
    config : CleanupConfig
        Retention policies holding each mode's exit watermark.

    Returns
    -------
    int | None
        The recovery watermark, or ``None`` for normal mode.
    """
    match mode:
        case Mode.PRESSURE:
            return config.pressure.exit_percent
        case Mode.CRITICAL:
            return config.critical.exit_percent
        case Mode.NORMAL:
            return None
