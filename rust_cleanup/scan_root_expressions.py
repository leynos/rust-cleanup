"""Expansion of the root expressions a configuration file may contain.

The configuration file names scan roots as text, so this module owns the small
expression language that turns those strings into paths. Expansion is
deliberately tiny: a leading ``~`` or ``~/`` is replaced with the home
directory, and every other character is kept as written. Shell syntax,
environment references, and other users' home directories are not evaluated.

Selection, canonicalization, and reporting live in :mod:`rust_cleanup.roots`;
keeping the expansion here means the rule that nothing is ever run through a
shell can be read in one place.
"""

import typing as typ
from pathlib import Path

from rust_cleanup.config import ConfigError

if typ.TYPE_CHECKING:
    import collections.abc as cabc

DEFAULT_SCAN_ROOT_EXPRESSIONS: tuple[str, str] = ("~/.lody/repos", "~/Projects")


def _reject_foreign_home(expression: str) -> None:
    """Reject a tilde form that names something other than one home directory.

    Raises
    ------
    ConfigError
        If the expression starts with a tilde that is not ``~`` or ``~/``.
    """
    if expression.startswith("~") and expression[:2] not in {"~", "~/"}:
        msg = (
            f"scan root {expression!r} must use '~' or '~/'; "
            "other users' homes are not expanded"
        )
        raise ConfigError(msg)


def _expand_home(expression: str, home: Path | cabc.Callable[[], Path]) -> Path:
    """Replace the leading ``~`` or ``~/`` with ``home``.

    Returns
    -------
    Path
        The expression with its home prefix expanded; every other character is
        kept as written, so nothing is evaluated as shell syntax.

    Notes
    -----
    A repeated separator keeps the expansion under ``home``: ``~//Projects``
    becomes ``home/Projects`` and ``~//`` becomes ``home``. Without this the
    suffix would stay absolute and ``home / suffix`` would discard ``home``,
    silently moving the scan and deletion boundary outside the user's home.
    """
    if expression == "~" or expression.startswith("~/"):
        resolved = home if isinstance(home, Path) else home()
        if expression == "~":
            return resolved
        return resolved.joinpath(expression[2:].lstrip("/"))
    return Path(expression)


def expand_root_expression(
    expression: str, *, home: Path | cabc.Callable[[], Path]
) -> Path:
    """Expand one configured root expression without invoking a shell.

    Only a leading ``~`` or ``~/`` is expanded. An internal tilde, an
    environment reference, and a home directory belonging to another user are
    all handled as written or rejected, so a typo cannot quietly resolve
    against the working directory or the wrong account.

    Parameters
    ----------
    expression : str
        Root expression as written in the configuration file.
    home : pathlib.Path or Callable[[], pathlib.Path]
        Home directory or lazy resolver used only for a leading tilde.

    Returns
    -------
    pathlib.Path
        The absolute path the expression denotes.

    Raises
    ------
    ConfigError
        If the expression is empty, relative, names another user's home, or is
        not absolute after expansion.

    Examples
    --------
    >>> expand_root_expression("~/Projects", home=Path("/home/dev"))
    PosixPath('/home/dev/Projects')
    >>> expand_root_expression("/data/$VAR", home=Path("/home/dev"))
    PosixPath('/data/$VAR')
    """
    if not expression:
        msg = "scan root must not be empty"
        raise ConfigError(msg)
    _reject_foreign_home(expression)
    expanded = _expand_home(expression, home)
    if not expanded.is_absolute():
        msg = f"scan root {expression!r} must be absolute or start with '~/'"
        raise ConfigError(msg)
    return expanded


def builtin_scan_roots(home: Path) -> tuple[Path, ...]:
    """Return the documented default scan roots for one home directory.

    Parameters
    ----------
    home : pathlib.Path
        Home directory the built-in expressions expand against.

    Returns
    -------
    tuple[pathlib.Path, ...]
        The Lody repositories and projects directories, in that order.
    """
    return tuple(
        expand_root_expression(item, home=home)
        for item in DEFAULT_SCAN_ROOT_EXPRESSIONS
    )
