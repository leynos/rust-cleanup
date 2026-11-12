"""Command-line contracts for scan-root precedence and selection.

The command line is where the three root sources meet: positional arguments,
the ``[scan]`` table, and the built-in defaults. These tests pin which source
wins, what happens to a root that cannot be scanned, and that every selected
root reaches exactly one locked sweep.
"""

import contextlib
import typing as typ
from unittest import mock

import pytest

from rust_cleanup import CleanupOutcome, cli
from rust_cleanup.roots import RootSelection

if typ.TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point every XDG lookup at ``tmp_path`` for the duration of one test.

    The command line resolves its configuration, its hysteresis state, and its
    advisory lock through the XDG directories. Without this the tests below
    read the host configuration, overwrite the host state document, and
    contend for the host runtime lock.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Fixture that sets the three XDG environment variables.
    tmp_path : Path
        Test root under which the config, state, and runtime directories are
        created.
    """
    for variable, name in (
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_STATE_HOME", "state"),
        ("XDG_RUNTIME_DIR", "runtime"),
    ):
        directory = tmp_path / name
        directory.mkdir()
        monkeypatch.setenv(variable, str(directory))


def _fabricate_home(root: Path, *children: str) -> Path:
    """Create a home directory holding the named child directories.

    Returns
    -------
    Path
        The created home directory.
    """
    home = root / "home"
    for child in children:
        (home / child).mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    return home


def _empty_outcome() -> CleanupOutcome:
    """Return a sweep outcome with nothing found and nothing deleted.

    Returns
    -------
    CleanupOutcome
        The trivial completed outcome.
    """
    return CleanupOutcome(
        scanned=0, deleted=0, unresolved_pressure=False, filesystems=()
    )


def _write_scan_config(root: Path, *expressions: str) -> Path:
    """Write a TOML file whose ``[scan]`` table names ``expressions``.

    Returns
    -------
    Path
        The written configuration path.
    """
    quoted = ", ".join(f'"{expression}"' for expression in expressions)
    path = root / "config.toml"
    path.write_text(f"[scan]\nroots = [{quoted}]\n", encoding="utf-8")
    return path


def _run_with_recorded_roots(
    argv: list[str],
) -> tuple[int, tuple[Path, ...], mock.Mock]:
    """Run the command line and return its status, roots, and lock mock.

    Returns
    -------
    tuple[int, tuple[Path, ...], mock.Mock]
        The command status, the roots the sweep received, and the mock that
        records the advisory-lock context manager.
    """
    lock = mock.MagicMock(return_value=contextlib.nullcontext(enter_result=True))
    with (
        mock.patch.object(cli, "advisory_lock", lock),
        mock.patch.object(
            cli, "cleanup_scan_roots", return_value=_empty_outcome()
        ) as sweep,
    ):
        status = cli.main(argv)
    roots = sweep.call_args.args[0] if sweep.called else ()
    return status, roots, lock


class TestScanRootSelection:
    """Tests for root precedence, skipping, and single-lock behaviour."""

    def test_main_default_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With no positional root the built-in Lody and Projects roots run.

        The command line no longer treats the working directory as a default
        root: an invocation with no arguments scans the documented defaults,
        so the sweep must receive both of them and nothing else.
        """
        home = _fabricate_home(tmp_path, ".lody/repos", "Projects")
        monkeypatch.setenv("HOME", str(home))

        status, roots, _lock = _run_with_recorded_roots([])

        assert status == 0, "main should return success for default path"
        assert roots == (
            (home / ".lody/repos").resolve(),
            (home / "Projects").resolve(),
        ), f"the default roots changed: {roots!r}"

    def test_a_missing_default_root_does_not_suppress_the_present_one(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """One absent built-in root stays quiet while the other still scans.

        The timer runs every five minutes. A host that simply does not keep one
        of the default directories must not journal a warning on every run, so
        a missing built-in root is not reported here; the scan still reaches
        the default that exists.
        """
        home = _fabricate_home(tmp_path, "Projects")
        monkeypatch.setenv("HOME", str(home))

        status, roots, _lock = _run_with_recorded_roots([])

        captured = capsys.readouterr()
        assert status == 0, "a missing default root failed the run"
        assert roots == ((home / "Projects").resolve(),), (
            f"the missing default suppressed the present one: {roots!r}"
        )
        assert "skipping scan root" not in captured.err, (
            f"a missing built-in root was reported on a timed run: {captured.err!r}"
        )

    def test_verbose_reports_a_missing_default_root(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """``--verbose`` explains a built-in default the host does not have."""
        home = _fabricate_home(tmp_path, "Projects")
        monkeypatch.setenv("HOME", str(home))
        absent = (home / ".lody/repos").resolve()

        status, roots, _lock = _run_with_recorded_roots(["--verbose"])

        captured = capsys.readouterr()
        assert status == 0, "a missing default root failed the run"
        assert roots == ((home / "Projects").resolve(),), (
            f"the missing default suppressed the present one: {roots!r}"
        )
        assert f"skipping scan root {absent} (missing)" in captured.err, (
            f"the skipped built-in root was not explained: {captured.err!r}"
        )

    def test_a_missing_configured_root_is_reported_without_verbose(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Only defaults are quiet; an operator's missing root stays loud.

        The configured list is the operator's own instruction, so a root in it
        that cannot be scanned is reported on every run rather than waiting
        for ``--verbose``.
        """
        home = _fabricate_home(tmp_path, ".lody/repos", "library")
        monkeypatch.setenv("HOME", str(home))
        config_path = _write_scan_config(tmp_path, "~/library", "~/gone")
        absent = (home / "gone").resolve()

        status, roots, _lock = _run_with_recorded_roots(["--config", str(config_path)])

        captured = capsys.readouterr()
        assert status == 0, "a missing configured root failed the run"
        assert roots == ((home / "library").resolve(),), (
            f"the missing configured root suppressed the present one: {roots!r}"
        )
        assert f"skipping scan root {absent} (missing)" in captured.err, (
            f"a missing configured root was silenced: {captured.err!r}"
        )

    def test_configured_roots_replace_the_defaults(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A ``[scan]`` table outranks the built-in roots but not the CLI."""
        home = _fabricate_home(tmp_path, ".lody/repos", "Projects", "library")
        monkeypatch.setenv("HOME", str(home))
        config_path = _write_scan_config(tmp_path, "~/library")

        status, roots, _lock = _run_with_recorded_roots(["--config", str(config_path)])

        assert status == 0, "a configured root list failed the run"
        assert roots == ((home / "library").resolve(),), (
            f"configured roots did not replace the defaults: {roots!r}"
        )

    def test_explicit_roots_override_the_configuration(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Positional roots outrank both the configuration file and defaults."""
        home = _fabricate_home(tmp_path, ".lody/repos", "Projects", "library")
        monkeypatch.setenv("HOME", str(home))
        config_path = _write_scan_config(tmp_path, "~/library")
        explicit = home / "explicit"
        explicit.mkdir()

        status, roots, _lock = _run_with_recorded_roots([
            "--config",
            str(config_path),
            str(explicit),
        ])

        assert status == 0, "an explicit root failed the run"
        assert roots == (explicit.resolve(),), (
            f"explicit roots did not outrank the configuration: {roots!r}"
        )

    def test_three_explicit_roots_share_one_lock_and_one_sweep(
        self, tmp_path: Path
    ) -> None:
        """Every root reaches one sweep, and the lock is entered exactly once."""
        roots = []
        for name in ("first", "second", "third"):
            directory = tmp_path / name
            directory.mkdir()
            roots.append(directory)

        status, swept_roots, lock = _run_with_recorded_roots([
            str(directory) for directory in roots
        ])

        assert status == 0, "a multi-root run failed"
        assert swept_roots == tuple(directory.resolve() for directory in roots), (
            f"the sweep did not receive all three roots: {swept_roots!r}"
        )
        lock.assert_called_once_with()

    def test_a_nested_explicit_root_is_collapsed_before_the_sweep(
        self, tmp_path: Path
    ) -> None:
        """Overlapping explicit roots scan once, under the outermost root."""
        outer = tmp_path / "projects"
        inner = outer / "nested"
        inner.mkdir(parents=True)

        status, roots, _lock = _run_with_recorded_roots([str(inner), str(outer)])

        assert status == 0, "overlapping explicit roots failed the run"
        assert roots == (outer.resolve(),), (
            f"a nested explicit root survived beside its ancestor: {roots!r}"
        )

    def test_an_unusable_explicit_root_fails_before_locking(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An explicit root that is not a directory exits before the lock."""
        present = tmp_path / "present"
        present.mkdir()
        missing = tmp_path / "missing"
        lock = mock.MagicMock(return_value=contextlib.nullcontext(enter_result=True))

        with (
            mock.patch.object(cli, "advisory_lock", lock),
            mock.patch.object(cli, "cleanup_scan_roots") as sweep,
        ):
            status = cli.main([str(present), str(missing)])

        captured = capsys.readouterr()
        assert status == 1, "an unusable explicit root should return status one"
        assert f"Error: Path does not exist: {missing.resolve()}" in captured.err, (
            f"the explicit-root error text changed: {captured.err!r}"
        )
        lock.assert_not_called()
        sweep.assert_not_called()

    def test_an_invalid_configured_root_is_a_configuration_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An unusable expression in ``[scan]`` stops the run before locking."""
        config_path = _write_scan_config(tmp_path, "~someone/projects")
        lock = mock.MagicMock(return_value=contextlib.nullcontext(enter_result=True))

        with (
            mock.patch.object(cli, "advisory_lock", lock),
            mock.patch.object(cli, "cleanup_scan_roots") as sweep,
        ):
            status = cli.main(["--config", str(config_path), str(tmp_path)])

        captured = capsys.readouterr()
        assert status == 2, "an invalid configured root should return status two"
        assert "Error: invalid rust-cleanup configuration" in captured.err, (
            f"the configuration error text changed: {captured.err!r}"
        )
        lock.assert_not_called()
        sweep.assert_not_called()

    def test_an_empty_configured_root_list_scans_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An explicit empty list replaces the defaults rather than extending."""
        home = _fabricate_home(tmp_path, ".lody/repos", "Projects")
        monkeypatch.setenv("HOME", str(home))
        config_path = tmp_path / "empty.toml"
        config_path.write_text("[scan]\nroots = []\n", encoding="utf-8")

        status, roots, lock = _run_with_recorded_roots(["--config", str(config_path)])

        assert status == 0, "an empty configured list failed the run"
        assert not roots, f"an empty configured list scanned roots: {roots!r}"
        lock.assert_called_once_with()

    def test_an_uninspectable_root_refuses_to_clean_up(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A root whose status cannot be read aborts instead of narrowing.

        Treating an uninspectable root as merely absent would silently reduce
        the scan and let the sweep delete from the roots it could see, so the
        command must refuse before locking rather than proceed.
        """
        selection = RootSelection(
            roots=(tmp_path.resolve(),),
            skipped=(),
            inspection_failed=True,
        )
        lock = mock.MagicMock(return_value=contextlib.nullcontext(enter_result=True))

        with (
            mock.patch.object(cli, "select_scan_roots", return_value=selection),
            mock.patch.object(cli, "advisory_lock", lock),
            mock.patch.object(cli, "cleanup_scan_roots") as sweep,
        ):
            status = cli.main([])

        captured = capsys.readouterr()
        assert status == 1, "an uninspectable root should return status one"
        assert "could not be inspected" in captured.err, (
            f"the inspection failure was not reported: {captured.err!r}"
        )
        lock.assert_not_called()
        sweep.assert_not_called()


@pytest.mark.parametrize("explicit", [False, True], ids=["configured", "explicit"])
def test_absolute_roots_do_not_require_an_account_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, explicit: bool
) -> None:
    """Absolute inputs remain usable when no account home can be resolved."""
    root = tmp_path / "root"
    root.mkdir()
    config = _write_scan_config(tmp_path, str(root))
    monkeypatch.setenv("HOME", "relative-home")
    with mock.patch("rust_cleanup.xdg.pwd.getpwuid", side_effect=KeyError):
        args = ["--config", str(config)]
        if explicit:
            args.append(str(root))
        status, roots, _ = _run_with_recorded_roots(args)
    assert status == 0, "absolute inputs should not need an account home"
    assert roots == (root,), "the absolute root should reach the sweep"
