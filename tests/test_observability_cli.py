"""Contracts for the telemetry one command-line invocation emits.

The command line is the composition root: it opens the run's invocation span,
reports the lock decision as its own bounded outcome, and keeps every record
on standard error so the human-readable summary keeps standard output to
itself. These tests drive the real entry point and decode what it wrote, so
what they assert is what an operator would see.

The Syrupy snapshot at the end of the module pins the whole stream one dry run
emits, with the correlation token and the elapsed times masked.
"""

import contextlib
import typing as typ
from unittest import mock

from conftest import make_target, recorded_lines, without_correlation

from rust_cleanup import CleanupOutcome, cli, sweep
from rust_cleanup.observability import EVENT_PREFIX, OPERATIONS

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

    import pytest
    from syrupy.assertion import SnapshotAssertion

# Stands in for the elapsed milliseconds of a record when a snapshot compares a
# whole run: the real value differs every time, but its presence in a record is
# part of the shape under test.
_SNAPSHOT_DURATION = "<duration_ms>"

# Free space decides which retention mode a run selects, and the mode decides
# which records the run emits at all. A runner whose filesystem is past the
# default pressure watermark would therefore pin a different transcript to the
# one a roomier host produces, so the reading is supplied by the test instead.
_SNAPSHOT_USAGE_PERCENT = 50.0


def _without_run_variance(record: cabc.Mapping[str, object]) -> dict[str, object]:
    """Return a telemetry record with the values that differ on every run masked.

    A correlation token and an elapsed time are the only fields a sweep cannot
    reproduce between runs, so a snapshot of a whole stream has to replace
    them. Whether they are present and bounded is proved by the contracts that
    read them directly; this helper exists so the remaining fixed shape can be
    pinned in one comparison.

    Parameters
    ----------
    record : collections.abc.Mapping[str, object]
        Telemetry record to normalize.

    Returns
    -------
    dict[str, object]
        The record without its correlation token and with any duration replaced
        by :data:`_SNAPSHOT_DURATION`.
    """
    normalized = without_correlation(record)
    if "duration_ms" in normalized:
        normalized["duration_ms"] = _SNAPSHOT_DURATION
    return normalized


def test_cli_records_lock_contention_without_starting_cleanup(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A contended lock is a bounded outcome and never starts a sweep."""
    with (
        mock.patch.object(
            cli,
            "advisory_lock",
            return_value=contextlib.nullcontext(enter_result=False),
        ),
        mock.patch.object(cli, "cleanup_scan_roots") as cleanup_targets,
    ):
        status = cli.main(["--config", str(tmp_path / "missing.toml"), str(tmp_path)])
    captured = capsys.readouterr()
    locks = [
        without_correlation(record)
        for record in recorded_lines(captured.err)
        if record["operation"] == "lock"
    ]
    assert status == 0, "lock contention should succeed without cleanup"
    assert locks == [
        {"operation": "lock", "lifecycle": "finish", "outcome": "contended"}
    ], "contended lock record changed"
    cleanup_targets.assert_not_called()


def test_cli_records_lock_acquisition(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An acquired lock is reported as its own bounded outcome."""
    outcome = CleanupOutcome(
        scanned=0, deleted=0, unresolved_pressure=False, filesystems=()
    )
    with (
        mock.patch.object(
            cli, "advisory_lock", return_value=contextlib.nullcontext(enter_result=True)
        ),
        mock.patch.object(cli, "cleanup_scan_roots", return_value=outcome),
    ):
        status = cli.main(["--config", str(tmp_path / "missing.toml"), str(tmp_path)])
    captured = capsys.readouterr()
    locks = [
        without_correlation(record)
        for record in recorded_lines(captured.err)
        if record["operation"] == "lock"
    ]
    assert status == 0, "acquired lock should succeed"
    assert locks == [
        {"operation": "lock", "lifecycle": "finish", "outcome": "acquired"}
    ], "acquired lock record changed"


def test_cli_records_one_correlated_invocation_span_per_run(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run brackets itself with one invocation span that shares its token.

    The invocation span is the entry point an operator correlates a whole run
    by, so it must open before any other record, close with a bounded duration,
    and carry the same token as every record the sweep below it emitted.
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    make_target(tmp_path, "stale", 1)
    status = cli.main([
        "--dry-run",
        "--config",
        str(tmp_path / "missing.toml"),
        str(tmp_path),
    ])
    captured = capsys.readouterr()
    records = recorded_lines(captured.err)
    invocations = [record for record in records if record["operation"] == "invocation"]
    assert status == 0, "dry-run sweep should succeed"
    assert [record["lifecycle"] for record in invocations] == ["start", "finish"], (
        "the invocation span is not a single start/finish pair"
    )
    started, finished = invocations
    assert records[0] is started, "the invocation span did not open the run"
    assert started["correlation"] == finished["correlation"], (
        "the invocation span finished under a different token"
    )
    assert finished["outcome"] == "completed", "the invocation outcome is wrong"
    assert isinstance(finished["duration_ms"], int), "no bounded duration was recorded"
    assert finished["duration_ms"] >= 0, "the recorded duration is negative"
    assert all("correlation" in record for record in records), "a record has no token"
    tokens = {record["correlation"] for record in records}
    assert tokens == {started["correlation"]}, (
        f"the run's records disagree on their correlation token: {tokens!r}"
    )


def test_cli_keeps_stdout_for_the_summary_and_telemetry_on_stderr(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Telemetry never displaces the human-readable dry-run summary."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    make_target(tmp_path, "stale", 1)
    status = cli.main([
        "--dry-run",
        "--config",
        str(tmp_path / "missing.toml"),
        str(tmp_path),
    ])
    captured = capsys.readouterr()
    assert status == 0, "dry-run sweep should succeed"
    assert captured.out.splitlines()[-1] == "Would delete 1 of 1 target directories", (
        "stdout summary changed"
    )
    assert "Would delete (mode=" in captured.out, "dry-run detail missing from stdout"
    assert EVENT_PREFIX not in captured.out, "telemetry leaked into stdout"
    records = recorded_lines(captured.err)
    assert records, "no telemetry reached stderr"
    assert all(record["operation"] in OPERATIONS for record in records), (
        "telemetry outside the fixed vocabulary reached stderr"
    )


def test_cli_emits_the_reviewed_telemetry_transcript(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    snapshot: SnapshotAssertion,
) -> None:
    """Pin every record one dry run emits, in order and in full.

    The contracts above each read a single record. This pins the sequence and
    the fixed shape of all of them together, so a span that stops being
    emitted, changes operation, or gains an unbounded field shows up as one
    reviewable diff. The correlation token and the elapsed times are the only
    values that differ between runs, and both are masked by
    :func:`_without_run_variance`.

    Parameters
    ----------
    tmp_path : Path
        Root directory passed to the command line.
    capsys : pytest.CaptureFixture[str]
        Capture fixture holding the emitted stream.
    monkeypatch : pytest.MonkeyPatch
        Patch fixture used to isolate the state store and the advisory lock
        from the host's.
    snapshot : SnapshotAssertion
        Syrupy fixture holding the reviewed transcript.
    """
    # A real invocation reads the host's state file, takes the host's advisory
    # lock, and measures the host's free space. The first two decide the
    # ``state_load`` and ``lock`` outcomes this transcript records, and the
    # third decides the retention mode and with it the records emitted at all,
    # so all three are pointed at the test rather than at the machine running
    # it.
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    make_target(tmp_path, "stale", 1)

    with mock.patch.object(
        sweep, "usage_percent", return_value=_SNAPSHOT_USAGE_PERCENT
    ):
        status = cli.main([
            "--dry-run",
            "--config",
            str(tmp_path / "missing.toml"),
            str(tmp_path),
        ])

    captured = capsys.readouterr()
    assert status == 0, "dry-run sweep should succeed"
    assert [
        _without_run_variance(record) for record in recorded_lines(captured.err)
    ] == snapshot, "the telemetry transcript changed"
