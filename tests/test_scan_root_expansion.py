"""Tests for the scan-root expression language.

A configured root is expanded without a shell, so the language has to be tiny
and entirely predictable: a leading ``~`` or ``~/`` means the home directory,
and everything else is taken as written. These tests pin both the accepted
forms and the rejected ones, because a root that quietly resolved against the
working directory would put the deletion boundary in the wrong place.
"""

from pathlib import Path

import pytest

from rust_cleanup.config import ConfigError
from rust_cleanup.scan_root_expressions import (
    DEFAULT_SCAN_ROOT_EXPRESSIONS,
    builtin_scan_roots,
    expand_root_expression,
)


def test_builtin_roots_are_the_documented_defaults(tmp_path: Path) -> None:
    """The shipped expressions cover the Lody tree and the projects tree."""
    assert DEFAULT_SCAN_ROOT_EXPRESSIONS == ("~/.lody/repos", "~/Projects"), (
        "the documented default root expressions changed"
    )
    assert builtin_scan_roots(tmp_path / "home") == (
        tmp_path / "home" / ".lody/repos",
        tmp_path / "home" / "Projects",
    ), "built-in roots did not expand against the supplied home"


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("~", Path("/home/example")),
        ("~/", Path("/home/example")),
        ("~/.lody/repos", Path("/home/example/.lody/repos")),
        ("/abs/$VAR", Path("/abs/$VAR")),
    ],
)
def test_expansion_accepts_only_leading_home_expressions(
    expression: str, expected: Path
) -> None:
    """A lone home marker and a leading ``~/`` expand; literals stay literal.

    Parameters
    ----------
    expression : str
        Configured root expression under test.
    expected : Path
        The fully expanded path the expression must produce.
    """
    expanded = expand_root_expression(expression, home=Path("/home/example"))

    assert expanded == expected, f"{expression!r} expanded to {expanded!r}"


@pytest.mark.parametrize(
    "expression",
    ["", "   ", "~user/projects", "relative/path", "$HOME/projects"],
)
def test_expansion_rejects_expressions_it_cannot_resolve_safely(
    expression: str,
) -> None:
    """Unsupported forms fail loudly instead of resolving against the cwd."""
    with pytest.raises(ConfigError, match="scan root"):
        expand_root_expression(expression, home=Path("/home/example"))


def test_expansion_keeps_environment_text_literal(tmp_path: Path) -> None:
    """``$VAR`` text is never evaluated, even inside an absolute path."""
    home = tmp_path / "home"

    assert expand_root_expression("/data/$VAR", home=home) == Path("/data/$VAR"), (
        "an environment reference was expanded"
    )
    assert expand_root_expression("~/x", home=home) == home / "x", (
        "a leading home expression did not expand"
    )
    assert expand_root_expression("~", home=home) == home, "a lone tilde did not expand"


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("~//Projects", Path("/home/example/Projects")),
        ("~///Projects", Path("/home/example/Projects")),
        ("~//", Path("/home/example")),
    ],
)
def test_repeated_separators_stay_inside_the_home_directory(
    expression: str, expected: Path
) -> None:
    """A doubled slash must not shift the boundary out of the user's home.

    ``home / "/Projects"`` discards ``home`` and yields ``/Projects``, so an
    untrimmed suffix would quietly widen the scan and deletion scope to the
    whole filesystem.

    Parameters
    ----------
    expression : str
        Configured root expression under test.
    expected : Path
        The path the expression must resolve to, still beneath the home.
    """
    home = Path("/home/example")

    expanded = expand_root_expression(expression, home=home)

    assert expanded == expected, f"{expression!r} expanded to {expanded!r}"
    assert expanded.is_relative_to(home), f"{expression!r} escaped the home directory"
