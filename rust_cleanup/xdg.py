"""XDG Base Directory locations for the files rust-cleanup owns.

Three files live outside the repository: the retention configuration, the
persisted retention state, and the advisory lock. Each is placed by an XDG
environment variable with a defined home-relative fallback, and the rule for
reading those variables is the specification's, not ours:

- the variable is honoured only when it is non-empty and absolute;
- a relative override is ignored rather than resolved against the working
  directory, so the file a sweep reads never depends on where it was started;
- an unset or unusable override falls back beneath the user's home.

That last rule is weaker than it reads. ``HOME`` may itself be unusable, and
the obvious resolver cannot repair it: ``Path.home`` and
``os.path.expanduser("~")`` both read ``HOME`` first on POSIX, so each returns
the relative value it was called to replace. The user database is the source
that answers independently of the environment, and it is consulted directly.
When it cannot supply an absolute directory either, the lookup reports that
rather than falling back to the working directory, which the contract forbids.

Configuring and persisting each applied that rule separately, which is one
rule stated twice. Keeping it here leaves both modules to say *which* file
they own rather than restating how the base directory is chosen.
"""

import os
import pwd
import typing as typ
from pathlib import Path

from rust_cleanup.errors import CleanupError

if typ.TYPE_CHECKING:
    import collections.abc as cabc

__all__ = ["config_home", "state_home", "user_home"]


def state_home(environment: cabc.Mapping[str, str]) -> Path:
    """Return the base directory for persisted state.

    Parameters
    ----------
    environment : Mapping[str, str]
        Environment the lookup reads, with ``HOME`` for the fallback.

    Returns
    -------
    pathlib.Path
        An absolute ``XDG_STATE_HOME`` override, or ``HOME``'s
        ``.local/state`` directory when the override is unset, empty, or
        relative.

    Raises
    ------
    CleanupError
        If the fallback is needed and no absolute home directory can be found.
    """  # ruff: ignore[docstring-extraneous-exception] - raised by _account_home.
    return _xdg_home(environment, "XDG_STATE_HOME", Path(".local") / "state")


def config_home(environment: cabc.Mapping[str, str]) -> Path:
    """Return the base directory for configuration.

    Parameters
    ----------
    environment : Mapping[str, str]
        Environment the lookup reads, with ``HOME`` for the fallback.

    Returns
    -------
    pathlib.Path
        An absolute ``XDG_CONFIG_HOME`` override, or ``HOME``'s ``.config``
        directory when the override is unset, empty, or relative.

    Raises
    ------
    CleanupError
        If the fallback is needed and no absolute home directory can be found.
    """  # ruff: ignore[docstring-extraneous-exception] - raised by _account_home.
    return _xdg_home(environment, "XDG_CONFIG_HOME", Path(".config"))


def _xdg_home(
    environment: cabc.Mapping[str, str],
    variable: str,
    fallback: Path,
) -> Path:
    """Return one XDG base directory: its override, or a home-relative fallback.

    Returns
    -------
    Path
        The override when it is absolute, otherwise the fallback under home.
    """
    override = environment.get(variable, "")
    if override and Path(override).is_absolute():
        return Path(override)
    return user_home(environment) / fallback


def user_home(environment: cabc.Mapping[str, str]) -> Path:
    """Resolve an absolute home for XDG fallbacks and scan-root expansion.

    Parameters
    ----------
    environment : Mapping[str, str]
        Environment providing an optional absolute ``HOME``.

    Returns
    -------
    pathlib.Path
        The absolute environment home or the current account's home.

    Raises
    ------
    CleanupError
        If neither source provides an absolute home.
    """  # ruff: ignore[docstring-extraneous-exception] - raised by _account_home.
    home = Path(environment.get("HOME", ""))
    return home if home.is_absolute() else _account_home()


def _account_home() -> Path:
    """Return this account's home directory from the user database.

    ``HOME`` cannot supply the fallback for an unusable ``HOME``. On POSIX
    ``Path.home`` and ``os.path.expanduser("~")`` both read that same variable
    before consulting the user database, so a resolver delegating to either
    returns the relative value it has just refused, and the module's
    absolute-path contract breaks in exactly the case the fallback exists for.
    ``pwd`` reads the account entry and nothing else.

    Returns
    -------
    Path
        The home directory recorded for this process's real user, when the
        account entry supplies an absolute one.

    Raises
    ------
    CleanupError
        If the user database has no entry for this uid, or records a relative
        home that cannot anchor an absolute path.
    """
    try:
        recorded = pwd.getpwuid(os.getuid()).pw_dir
    except KeyError as error:
        msg = (
            f"cannot determine an absolute home directory for uid "
            f"{os.getuid()}: the user database has no entry for it"
        )
        raise CleanupError(msg) from error
    # The entry is trusted to exist, not to be absolute: a hand-edited
    # passwd file can hold a relative home, which would reintroduce the
    # working-directory dependence this function exists to avoid.
    home = Path(recorded)
    if not home.is_absolute():
        msg = (
            f"cannot determine an absolute home directory for uid "
            f"{os.getuid()}: the user database records {recorded!r}"
        )
        raise CleanupError(msg)
    return home
