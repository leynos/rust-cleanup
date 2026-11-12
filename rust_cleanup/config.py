"""Retention policy configuration for :mod:`rust_cleanup`.

The policy is deliberately data-only so that command-line parsing, TOML
loading, and environment lookup remain adapters around a small, directly
testable configuration model. Loading, merging, and XDG path resolution are
implemented in :mod:`rust_cleanup.config_loading`.
"""

import dataclasses
import re
import typing as typ

if typ.TYPE_CHECKING:
    from pathlib import Path


class ConfigError(ValueError):
    """Raised when a cleanup configuration is malformed or unsafe."""


@dataclasses.dataclass(frozen=True, slots=True)
class RetentionPolicy:
    """The inactivity period before a target directory may be reclaimed.

    Attributes
    ----------
    stale_after : int
        Minimum inactivity in seconds before a target may be reclaimed.
    """

    stale_after: int


@dataclasses.dataclass(frozen=True, slots=True)
class PressurePolicy(RetentionPolicy):
    """Retention policy with separate entry and recovery watermarks.

    Attributes
    ----------
    stale_after : int
        Inherited minimum inactivity in seconds before reclamation.
    enter_percent : int
        Used-capacity percentage at which this mode is entered.
    exit_percent : int
        Used-capacity percentage below which this mode recovers.
    """

    enter_percent: int
    exit_percent: int


@dataclasses.dataclass(frozen=True, slots=True)
class ScanPolicy:
    """Where a sweep looks for Rust build output.

    Attributes
    ----------
    roots : tuple[pathlib.Path, ...] | None
        Expanded scan roots from the configuration file. ``None`` means the
        file did not name any roots, so the built-in defaults apply. An empty
        tuple is an explicit request to scan nothing, which is distinct from
        an absent setting even though both look falsy.
    """

    roots: tuple[Path, ...] | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class CleanupConfig:
    """All retention policies used by one cleanup sweep.

    Attributes
    ----------
    normal : RetentionPolicy
        Policy applied below the pressure entry watermark.
    pressure : PressurePolicy
        Policy applied while pressure mode is selected.
    critical : PressurePolicy
        Policy applied while critical mode is selected.
    scan : ScanPolicy
        Scan-root policy from the configuration file.
    """

    normal: RetentionPolicy
    pressure: PressurePolicy
    critical: PressurePolicy
    scan: ScanPolicy = ScanPolicy()


DEFAULT_CONFIG = CleanupConfig(
    normal=RetentionPolicy(stale_after=2 * 60 * 60),
    pressure=PressurePolicy(stale_after=10 * 60, enter_percent=80, exit_percent=75),
    critical=PressurePolicy(stale_after=2 * 60, enter_percent=95, exit_percent=90),
)
CUTOFF_SECONDS = DEFAULT_CONFIG.normal.stale_after
_DURATION_PATTERN = re.compile(r"^(?P<amount>[0-9]+)(?P<unit>[smh])$")


def parse_duration(text: str) -> int:
    """Parse a positive duration expressed in seconds, minutes, or hours.

    Parameters
    ----------
    text : str
        A positive integer followed by ``s``, ``m``, or ``h``.

    Returns
    -------
    int
        Duration in seconds.

    Raises
    ------
    ConfigError
        If the duration is not a positive, supported duration.
    """
    match = _DURATION_PATTERN.fullmatch(text)
    if match is None:
        msg = f"invalid duration {text!r}; use a positive integer with s, m, or h"
        raise ConfigError(msg)
    amount = int(match["amount"])
    if amount <= 0:
        msg = f"invalid duration {text!r}; duration must be positive"
        raise ConfigError(msg)
    return amount * {"s": 1, "m": 60, "h": 60 * 60}[match["unit"]]
