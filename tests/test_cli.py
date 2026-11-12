"""Unit tests for rust_cleanup command-line handling."""

import contextlib
import os
import time
import typing as typ
from unittest import mock

import pytest
from conftest import main_with_outcome

from rust_cleanup import (
    CACHEDIR_TAG,
    CUTOFF_SECONDS,
    TARGET_DIR,
    UNRESOLVED_PRESSURE_EXIT_STATUS,
    CleanupError,
    CleanupOutcome,
    cli,
    main,
)
from rust_cleanup.config import (
    CleanupConfig,
    ConfigError,
    PressurePolicy,
    RetentionPolicy,
)
from rust_cleanup.state import StateStore

if typ.TYPE_CHECKING:
    from pathlib import Path

# Every policy flag the command line offers, each with a value that differs
# from the shipped default and from every other mode's value. Distinct values
# are what let one sweep prove each flag reached its own field: a swap between
# two modes, or an override silently dropped in favour of a default, changes
# the observed configuration.
_POLICY_FLAGS = (
    "--normal-stale-after",
    "3h",
    "--pressure-enter-percent",
    "70",
    "--pressure-exit-percent",
    "65",
    "--pressure-stale-after",
    "7m",
    "--critical-enter-percent",
    "90",
    "--critical-exit-percent",
    "85",
    "--critical-stale-after",
    "95s",
)


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


def _main_with_failing_cleanup(root: Path, error: Exception) -> int:
    """Run the command line with a failing sweep and return its exit status.

    Parameters
    ----------
    root : Path
        Directory passed to the command line as its cleanup root.
    error : Exception
        Failure raised by the sweep in place of a real cleanup.

    Returns
    -------
    int
        The exit status returned for the injected sweep failure.
    """
    with (
        mock.patch.object(
            cli, "advisory_lock", return_value=contextlib.nullcontext(enter_result=True)
        ),
        mock.patch.object(cli, "cleanup_scan_roots", side_effect=error),
    ):
        return cli.main([str(root)])


class TestMain:
    """Tests for main."""

    def test_main_with_valid_path(self, tmp_path: Path) -> None:
        """Verify that main with valid path."""
        with mock.patch("sys.argv", ["rust_cleanup.py", str(tmp_path)]):
            result = main()
        assert result == 0, "main should return success for valid path"

    def test_main_with_nonexistent_path(self, tmp_path: Path) -> None:
        """Verify that main with nonexistent path."""
        nonexistent = tmp_path / "does_not_exist"
        with mock.patch("sys.argv", ["rust_cleanup.py", str(nonexistent)]):
            result = main()
        assert result == 1, "main should return failure for nonexistent path"

    def test_main_with_file_instead_of_directory(self, tmp_path: Path) -> None:
        """Verify that main with file instead of directory."""
        test_file = tmp_path / "file.txt"
        test_file.write_text("content")
        with mock.patch("sys.argv", ["rust_cleanup.py", str(test_file)]):
            result = main()
        assert result == 1, "main should return failure for file path"

    def test_main_with_dry_run(self, tmp_path: Path) -> None:
        """Verify that main with dry run."""
        target = tmp_path / TARGET_DIR
        target.mkdir()
        (target / CACHEDIR_TAG).touch()
        old_file = target / "old.o"
        old_file.write_text("content")
        old_time = time.time() - (CUTOFF_SECONDS + 3600)
        os.utime(old_file, (old_time, old_time))

        with mock.patch("sys.argv", ["rust_cleanup.py", "--dry-run", str(tmp_path)]):
            result = main()
        assert result == 0, "main should return success for dry run"
        assert target.exists(), "main dry run should preserve target"


class TestMainFailures:
    """Tests for the command line's documented error statuses."""

    def test_main_reports_invalid_configuration(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Configuration errors use the documented exit status and stderr text.

        Parameters
        ----------
        tmp_path : Path
            Root directory passed to the command line.
        capsys : pytest.CaptureFixture[str]
            Capture fixture holding the reported stderr.
        """
        with mock.patch.object(
            cli, "load_config", side_effect=ConfigError("bad policy")
        ):
            result = cli.main([str(tmp_path)])

        captured = capsys.readouterr()
        assert result == 2, "configuration error should return status two"
        # Telemetry records share stderr, so the human error is asserted as one
        # line among them rather than as the entire stream.
        assert (
            "Error: invalid rust-cleanup configuration: bad policy\n" in captured.err
        ), f"configuration error text changed: {captured.err!r}"

    def test_main_skips_cleanup_when_another_invocation_holds_the_lock(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Lock contention is a successful no-op and never starts cleanup.

        Parameters
        ----------
        tmp_path : Path
            Root directory passed to the command line.
        capsys : pytest.CaptureFixture[str]
            Capture fixture holding the reported stdout.
        """
        with (
            mock.patch.object(
                cli,
                "advisory_lock",
                return_value=contextlib.nullcontext(enter_result=False),
            ),
            mock.patch.object(cli, "cleanup_scan_roots") as cleanup_targets,
        ):
            result = cli.main([str(tmp_path)])

        captured = capsys.readouterr()
        assert result == 0, "lock contention should succeed without cleanup"
        assert (
            captured.out == "rust-cleanup: another invocation already holds the lock\n"
        ), "lock contention message changed"
        cleanup_targets.assert_not_called()

    def test_main_reports_cleanup_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Cleanup failures retain their error exit status and stderr text.

        Parameters
        ----------
        tmp_path : Path
            Root directory passed to the command line.
        capsys : pytest.CaptureFixture[str]
            Capture fixture holding the reported stderr.
        """
        result = _main_with_failing_cleanup(tmp_path, CleanupError("cannot clean"))

        captured = capsys.readouterr()
        assert result == 1, "cleanup error should return status one"
        # Telemetry records share stderr, so the human error is asserted as one
        # line among them rather than as the entire stream.
        assert "Error: cannot clean\n" in captured.err, (
            f"cleanup error text changed: {captured.err!r}"
        )

    def test_main_reports_filesystem_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A propagated filesystem error is reported instead of a traceback.

        Parameters
        ----------
        tmp_path : Path
            Root directory passed to the command line.
        capsys : pytest.CaptureFixture[str]
            Capture fixture holding the reported stderr.
        """
        result = _main_with_failing_cleanup(tmp_path, OSError("no space left"))

        captured = capsys.readouterr()
        assert result == 1, "filesystem error should return status one"
        assert "Error: no space left\n" in captured.err, (
            f"filesystem error text changed: {captured.err!r}"
        )

    def test_main_reports_state_save_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A failed state save is reported instead of escaping as a traceback.

        Parameters
        ----------
        tmp_path : Path
            Root directory passed to the command line.
        capsys : pytest.CaptureFixture[str]
            Capture fixture holding the reported stderr.
        """
        with (
            mock.patch.object(
                cli,
                "advisory_lock",
                return_value=contextlib.nullcontext(enter_result=True),
            ),
            mock.patch.object(StateStore, "save", side_effect=OSError("no space left")),
        ):
            result = cli.main([str(tmp_path)])

        captured = capsys.readouterr()
        assert result == 1, "state save failure should return status one"
        assert "Error: no space left\n" in captured.err, (
            f"state save error text changed: {captured.err!r}"
        )

    def test_main_rejects_a_malformed_configuration_file(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A real malformed TOML file stops the sweep before it deletes anything.

        Parameters
        ----------
        tmp_path : Path
            Root directory passed to the command line.
        capsys : pytest.CaptureFixture[str]
            Capture fixture holding the reported stderr.
        """
        target = tmp_path / TARGET_DIR
        target.mkdir()
        (target / CACHEDIR_TAG).touch()
        stale_file = target / "old.o"
        stale_file.write_text("content")
        old_time = time.time() - (CUTOFF_SECONDS + 3600)
        os.utime(stale_file, (old_time, old_time))
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            "[cleanup.pressure\nenter_percent = 80\n", encoding="utf-8"
        )

        result = cli.main(["--config", str(config_path), str(tmp_path)])

        captured = capsys.readouterr()
        assert result == 2, "malformed configuration should return status two"
        assert target.exists(), "a malformed configuration still deleted a target"
        assert "Error: invalid rust-cleanup configuration" in captured.err, (
            f"malformed configuration error text changed: {captured.err!r}"
        )

    def test_main_returns_pressure_exit_status(self, tmp_path: Path) -> None:
        """Unresolved pressure remains visible to the calling service manager.

        Parameters
        ----------
        tmp_path : Path
            Root directory passed to the command line.
        """
        outcome = CleanupOutcome(
            scanned=0,
            deleted=0,
            unresolved_pressure=True,
            filesystems=(),
        )

        result = main_with_outcome(tmp_path, outcome)

        assert result == UNRESOLVED_PRESSURE_EXIT_STATUS, (
            "unresolved pressure exit status changed"
        )

    def test_main_maps_every_policy_flag_onto_its_config_field(
        self, tmp_path: Path
    ) -> None:
        """Each policy flag reaches the sweep as its own configuration field.

        The command line is the only place these overrides are parsed, so an
        override wired to the wrong field - or dropped in favour of a default -
        is invisible to every test that builds its configuration directly.
        Distinct values per flag make that swap a configuration mismatch here.

        Parameters
        ----------
        tmp_path : Path
            Root directory passed to the command line.
        """
        outcome = CleanupOutcome(
            scanned=0,
            deleted=0,
            unresolved_pressure=False,
            filesystems=(),
        )

        with (
            mock.patch.object(
                cli,
                "advisory_lock",
                return_value=contextlib.nullcontext(enter_result=True),
            ),
            mock.patch.object(cli, "cleanup_scan_roots", return_value=outcome) as sweep,
        ):
            result = cli.main([*_POLICY_FLAGS, str(tmp_path)])

        assert result == 0, "a valid policy override failed the run"
        run = sweep.call_args.args[1]
        assert run.config == CleanupConfig(
            normal=RetentionPolicy(stale_after=3 * 60 * 60),
            pressure=PressurePolicy(
                stale_after=7 * 60, enter_percent=70, exit_percent=65
            ),
            critical=PressurePolicy(stale_after=95, enter_percent=90, exit_percent=85),
        ), f"a policy flag reached the wrong field: {run.config!r}"

    def test_main_rejects_an_invalid_policy_flag(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An unparsable policy override stops the run before any sweep starts.

        Parameters
        ----------
        tmp_path : Path
            Root directory passed to the command line.
        capsys : pytest.CaptureFixture[str]
            Capture fixture holding the reported stderr.
        """
        with mock.patch.object(cli, "cleanup_scan_roots") as sweep:
            result = cli.main(["--pressure-stale-after", "7x", str(tmp_path)])

        captured = capsys.readouterr()
        assert result == 2, "an invalid policy flag should return status two"
        assert "Error: invalid rust-cleanup configuration" in captured.err, (
            f"invalid override error text changed: {captured.err!r}"
        )
        sweep.assert_not_called()
