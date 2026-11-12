"""Builders shared by the scan-root contracts.

The scan-root suites have to create directories, summarize skipped roots, and
own ``HOME`` so expansion is deterministic. Those three helpers are identical
in every module that touches root selection, so they live here rather than in
one test module: a later split cannot quietly fork them and let two suites
assert different behaviour for the same input.
"""

import typing as typ

import pytest

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

    from rust_cleanup.roots import SkippedRoot


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create an isolated home directory and point ``HOME`` at it.

    Expanded scan roots are resolved against ``HOME``, so any suite that reads
    a configuration file has to own that value instead of inheriting the
    developer's. A per-test temporary directory keeps the suites independent.

    Returns
    -------
    Path
        The home directory the selection functions expand against.
    """
    directory = tmp_path / "home"
    directory.mkdir()
    monkeypatch.setenv("HOME", str(directory))
    return directory


def make_dirs(*paths: Path) -> None:
    """Create every supplied directory, including its parents."""
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)


def reasons(skipped: cabc.Iterable[SkippedRoot]) -> dict[Path, str]:
    """Map each skipped root to its reason for order-insensitive assertions.

    Returns
    -------
    dict[Path, str]
        Skipped paths paired with their reasons.
    """
    return {item.path: item.reason for item in skipped}
