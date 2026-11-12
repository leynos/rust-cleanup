"""The XDG Base Directory rule the configuration and state files share.

:func:`config_home` and :func:`state_home` differ only in the variable they
read and the fallback they use, so the rule itself is tested once here. The
callers' own tests still cover the file each one places, and would fail if
either stopped consulting this module.
"""

import os
import pwd
import typing as typ
from pathlib import Path

import pytest

from rust_cleanup import xdg
from rust_cleanup.errors import CleanupError

if typ.TYPE_CHECKING:
    import collections.abc as cabc


def _environment(**overrides: str) -> dict[str, str]:
    """Return an environment with *overrides* applied to a fixed home.

    Returns
    -------
    dict[str, str]
        The fixed home environment updated with the supplied overrides.
    """
    environment = {"HOME": "/home/tester"}
    environment.update(overrides)
    return environment


def _account_home() -> Path:
    """Return the home this account's user-database entry records.

    Read from the same source the lookup falls back to, rather than assumed,
    so the expectation tracks whatever account the suite runs as.

    Returns
    -------
    Path
        The account home directory recorded by the user database.
    """
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        pytest.param(
            _environment(XDG_CONFIG_HOME="/xdg/config"),
            Path("/xdg/config"),
            id="absolute-override",
        ),
        pytest.param(
            _environment(XDG_CONFIG_HOME=""),
            Path("/home/tester/.config"),
            id="empty-override",
        ),
        pytest.param(
            _environment(XDG_CONFIG_HOME="relative/config"),
            Path("/home/tester/.config"),
            id="relative-override",
        ),
        pytest.param(
            {"HOME": "/home/tester"},
            Path("/home/tester/.config"),
            id="unset-override",
        ),
    ],
)
def test_config_home_applies_the_specification(
    environment: cabc.Mapping[str, str], expected: Path
) -> None:
    """Only a non-empty absolute override replaces the home-relative default."""
    assert xdg.config_home(environment) == expected, (
        "the configuration base directory no longer follows the XDG rule"
    )


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        pytest.param(
            _environment(XDG_STATE_HOME="/xdg/state"),
            Path("/xdg/state"),
            id="absolute-override",
        ),
        pytest.param(
            _environment(XDG_STATE_HOME=""),
            Path("/home/tester/.local/state"),
            id="empty-override",
        ),
        pytest.param(
            _environment(XDG_STATE_HOME="relative/state"),
            Path("/home/tester/.local/state"),
            id="relative-override",
        ),
        pytest.param(
            {"HOME": "/home/tester"},
            Path("/home/tester/.local/state"),
            id="unset-override",
        ),
    ],
)
def test_state_home_applies_the_specification(
    environment: cabc.Mapping[str, str], expected: Path
) -> None:
    """State falls back beneath ``HOME``, not beneath the working directory."""
    assert xdg.state_home(environment) == expected, (
        "the state base directory no longer follows the XDG rule"
    )


def test_neither_lookup_reads_its_sibling_variable() -> None:
    """Setting one XDG variable must not move the other directory.

    The two lookups share one private resolver, so a change that read the
    wrong variable would silently relocate one file while its own tests, which
    set only that variable, still passed.
    """
    environment = _environment(
        XDG_CONFIG_HOME="/xdg/config", XDG_STATE_HOME="/xdg/state"
    )

    assert xdg.config_home(environment) == Path("/xdg/config"), (
        "the configuration lookup read the state variable"
    )
    assert xdg.state_home(environment) == Path("/xdg/state"), (
        "the state lookup read the configuration variable"
    )


# Each lookup paired with the directory it appends to the resolved home.
_LOOKUPS = [
    pytest.param(xdg.config_home, "XDG_CONFIG_HOME", Path(".config"), id="config"),
    pytest.param(xdg.state_home, "XDG_STATE_HOME", Path(".local/state"), id="state"),
]


@pytest.mark.parametrize(("lookup", "variable", "fallback"), _LOOKUPS)
def test_an_absent_home_falls_back_to_the_account_home(
    lookup: cabc.Callable[[cabc.Mapping[str, str]], Path],
    variable: str,
    fallback: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty ``HOME`` must never resolve against the working directory.

    A sweeper started by a minimal service manager may have no ``HOME``
    exported. Reading it as the empty string and joining the fallback onto
    that would yield a *relative* path, scattering configuration, state, and
    the lock beside whatever directory the sweep happened to run in — where
    the next run, started elsewhere, would not find them.
    """
    monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("HOME", "")

    resolved = lookup(os.environ)

    assert resolved == _account_home() / fallback, (
        f"an empty HOME did not fall back to the account home: {resolved!r}"
    )


@pytest.mark.parametrize(("lookup", "variable", "fallback"), _LOOKUPS)
def test_a_relative_home_falls_back_to_the_account_home(
    lookup: cabc.Callable[[cabc.Mapping[str, str]], Path],
    variable: str,
    fallback: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A relative ``HOME`` is as unusable as an absent one.

    The process environment is driven directly rather than through a stub of
    the home lookup, and that is the whole point: ``Path.home()`` reads this
    same ``HOME`` on POSIX, so a resolver delegating to it returns the
    relative value it has just refused. Stubbing the resolver — as this test
    once did — replaces the defective link with the expected answer and can
    never observe the defect.
    """
    monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("HOME", "relative/home")

    resolved = lookup(os.environ)

    assert resolved == _account_home() / fallback, (
        f"a relative HOME was honoured instead of falling back: {resolved!r}"
    )


@pytest.mark.parametrize(
    ("lookup", "variable"),
    [(xdg.config_home, "XDG_CONFIG_HOME"), (xdg.state_home, "XDG_STATE_HOME")],
    ids=["config", "state"],
)
def test_an_unresolvable_home_is_reported_rather_than_guessed(
    lookup: cabc.Callable[[cabc.Mapping[str, str]], Path],
    variable: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A relative ``HOME`` with no account entry leaves nothing to fall back to.

    Once ``HOME`` is unusable the user database is the only remaining source,
    so a process whose uid it does not describe has no home to anchor an
    absolute path. Reporting that is the honest outcome: the alternatives are
    the working-directory dependence this module exists to forbid, or a
    ``~`` literal that resolves against it just the same.
    """
    monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("HOME", "relative/home")

    def missing(uid: int) -> typ.NoReturn:
        """Stand in for a uid the user database does not describe.

        Raises
        ------
        KeyError
            Whenever the account lookup is attempted.
        """
        raise KeyError(uid)

    monkeypatch.setattr(pwd, "getpwuid", missing)

    with pytest.raises(CleanupError, match="absolute home directory"):
        lookup(os.environ)
