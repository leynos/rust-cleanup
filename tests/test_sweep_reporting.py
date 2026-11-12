"""Contracts for what a sweep reports and what it must never report.

A dry run reports the policy it would apply, including when no candidate is
eligible, and a failed usage read leaves a bounded warning behind: neither a
candidate target path nor the raw ``OSError`` text reaches standard output or
standard error. The mount point is reported by the operator diagnostics
record, so it is the warning and the telemetry that must stay bounded.

Rendering belongs to the command line, so the sweep is asserted to return what
it observed and to write nothing itself.
"""

import typing as typ
from unittest import mock

from conftest import (
    InactiveBuilds,
    RecordingSink,
    build_run,
    build_sink_run,
    make_target,
    synthetic_identity,
)

from rust_cleanup import cleanup_scan_roots
from rust_cleanup.observability import configure_logging
from rust_cleanup.pressure import Mode
from rust_cleanup.state import StateStore

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from rust_cleanup.pressure import FilesystemIdentity


def test_post_deletion_usage_failure_stops_reclamation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unmeasurable usage re-read after a deletion stops reclamation safely.

    The warning is asserted through the command line's own logging sink, so the
    test observes what a real invocation writes to standard error: a fixed
    message that carries neither the raw ``OSError`` text nor the path of the
    filesystem whose usage could not be read.
    """
    first = make_target(tmp_path, "first", 1)
    second = make_target(tmp_path, "second", 2)

    store = StateStore(tmp_path / "state.json")
    configure_logging()
    outcome = cleanup_scan_roots(
        (tmp_path,),
        build_run(
            state_store=store,
            active_build_detector=InactiveBuilds(),
            usage_getter=mock.Mock(side_effect=[85.0, OSError("usage unavailable")]),
            identity_getter=lambda _path: synthetic_identity(tmp_path),
            now=10000,
        ),
    )
    assert outcome.deleted == 1, "the first candidate was not reclaimed"
    assert not first.exists(), "the oldest target survived"
    assert second.exists(), "reclamation continued after the usage re-read failed"
    assert outcome.unresolved_pressure, (
        "an unmeasurable re-read reported pressure as resolved"
    )
    assert not outcome.filesystems[0].exit_reached, (
        "an unmeasurable re-read reported the recovery watermark as reached"
    )
    assert store.mode_for(synthetic_identity(tmp_path)) is None, (
        "a mode was persisted despite the unmeasurable usage re-read"
    )
    captured = capsys.readouterr()
    warnings = [
        line for line in captured.err.splitlines() if line.startswith("Warning:")
    ]
    assert warnings == [
        "Warning: stopping reclamation; filesystem usage is unavailable"
    ], f"the bounded availability warning changed: {warnings!r}"
    warning_output = "\n".join(warnings)
    assert str(tmp_path) not in warning_output, (
        "the filesystem path was included in the usage warning"
    )
    assert "usage unavailable" not in warning_output, (
        "the raw error text was included in the usage warning"
    )
    # The target paths are checked against both complete streams, not just the
    # warning lines: a candidate path smuggled into a telemetry field would
    # otherwise satisfy the narrower assertion. The mount point is not checked
    # here because the per-filesystem operator record names it deliberately.
    for path in (first, second):
        assert str(path) not in captured.err, f"{path} reached stderr"
        assert str(path) not in captured.out, f"{path} reached stdout"
    assert "usage unavailable" not in captured.err, "the raw error reached stderr"
    assert "usage unavailable" not in captured.out, "the raw error reached stdout"


def test_dry_run_does_not_write_state_or_delete_targets(tmp_path: Path) -> None:
    """Dry-run reports policy decisions while leaving both durable resources alone."""
    target = make_target(tmp_path, "project", 1)
    state_path = tmp_path / "state.json"
    outcome = cleanup_scan_roots(
        (tmp_path,),
        build_run(
            dry_run=True,
            state_store=StateStore(state_path),
            active_build_detector=InactiveBuilds(),
            usage_getter=lambda _path: 85.0,
            identity_getter=lambda _path: synthetic_identity(tmp_path),
            now=10000,
        ),
    )
    assert outcome.deleted == 1, "dry-run did not report deletion"
    assert target.exists(), "dry-run deleted target"
    assert not state_path.exists(), "dry-run wrote state"


def test_dry_run_plans_every_eligible_candidate(tmp_path: Path) -> None:
    """A dry run returns the planned order for every eligible candidate."""
    first = make_target(tmp_path, "first", 1)
    second = make_target(tmp_path, "second", 2)
    outcome = cleanup_scan_roots(
        (tmp_path,),
        build_run(
            dry_run=True,
            state_store=StateStore(tmp_path / "state.json"),
            active_build_detector=InactiveBuilds(),
            usage_getter=lambda _path: 85.0,
            identity_getter=lambda _path: synthetic_identity(tmp_path),
            now=10000,
        ),
    )
    assert outcome.planned_deletions == (first.resolve(), second.resolve()), (
        "the dry-run plan did not return every candidate in oldest-first order"
    )
    assert outcome.deleted == 2, "dry-run planned deletion count is wrong"
    assert first.exists(), "dry run deleted the oldest target"
    assert second.exists(), "dry run deleted the newer target"


def test_dry_run_reports_the_selected_mode_without_candidates(tmp_path: Path) -> None:
    """An empty dry run still reports the mode it would have applied.

    A target that is not yet stale leaves the plan with no eligible candidate,
    which is exactly when an unreported mode would hide the policy in force.
    """
    target = make_target(tmp_path, "recent", 10000)
    state_path = tmp_path / "state.json"
    outcome = cleanup_scan_roots(
        (tmp_path,),
        build_run(
            dry_run=True,
            state_store=StateStore(state_path),
            active_build_detector=InactiveBuilds(),
            usage_getter=lambda _path: 10.0,
            identity_getter=lambda _path: synthetic_identity(tmp_path),
            now=10000,
        ),
    )

    assert outcome.filesystems[0].selected_mode is Mode.NORMAL, (
        "an empty dry run did not report the selected mode"
    )
    assert not outcome.planned_deletions, "an empty dry run planned a deletion"
    assert outcome.deleted == 0, "an empty dry run reported a deletion"
    assert target.exists(), "an empty dry run deleted the target"
    assert not state_path.exists(), "an empty dry run wrote state"


def test_diagnostics_report_the_policy_and_the_reclamation_counters(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The operator log names the mount, the policy, and every counter.

    This is the human-readable counterpart to the bounded telemetry events, so
    it is asserted here for both a reclaiming sweep and a dry run: the mount
    point, the before/after usage, the previous and selected modes, the cutoff,
    the per-target counters, and the recovery watermark status.
    """
    make_target(tmp_path, "stale", 1)
    mount = synthetic_identity(tmp_path).mount_point
    with caplog.at_level("INFO", logger="rust_cleanup.diagnostics"):
        cleanup_scan_roots(
            (tmp_path,),
            build_run(
                state_store=StateStore(tmp_path / "state.json"),
                active_build_detector=InactiveBuilds(),
                usage_getter=lambda _path: 1.0,
                identity_getter=lambda _path: synthetic_identity(tmp_path),
                now=10000,
            ),
        )

    reported = [
        record.getMessage()
        for record in caplog.records
        if record.name == "rust_cleanup.diagnostics"
    ]
    assert reported == [
        (
            f"rust-cleanup filesystem={mount} usage=1.00%->1.00% previous=derived "
            "mode=normal cutoff=7200s scanned=1 protected=0 unknown-activity=0 "
            "eligible=1 deleted=1 failed=0 exit-watermark=reached"
        )
    ], f"the per-filesystem diagnostics changed: {reported!r}"


def test_dry_run_diagnostics_report_a_plan_instead_of_a_deletion(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A dry run reports the planned mode and count as a plan, not a result."""
    make_target(tmp_path, "stale", 1)
    mount = synthetic_identity(tmp_path).mount_point
    with caplog.at_level("INFO", logger="rust_cleanup.diagnostics"):
        cleanup_scan_roots(
            (tmp_path,),
            build_run(
                dry_run=True,
                state_store=StateStore(tmp_path / "state.json"),
                active_build_detector=InactiveBuilds(),
                usage_getter=lambda _path: 85.0,
                identity_getter=lambda _path: synthetic_identity(tmp_path),
                now=10000,
            ),
        )

    reported = [
        record.getMessage()
        for record in caplog.records
        if record.name == "rust_cleanup.diagnostics"
    ]
    assert reported == [
        (
            f"rust-cleanup filesystem={mount} usage=85.00%->85.00% previous=derived "
            "mode=pressure cutoff=600s scanned=1 protected=0 unknown-activity=0 "
            "eligible=1 would delete=1 failed=0 exit-watermark=skipped"
        )
    ], f"the dry-run diagnostics changed: {reported!r}"


def test_reclamation_writes_nothing_to_the_console(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Reclamation returns what it observed instead of printing it.

    Message wording belongs to the command line, which is why the rendered
    report is asserted where the rendering lives. This is the other half of
    that split: a successful sweep is silent on both streams.
    """
    target = make_target(tmp_path, "project", 1)
    outcome = cleanup_scan_roots(
        (tmp_path,),
        build_run(
            state_store=StateStore(tmp_path / "state.json"),
            active_build_detector=InactiveBuilds(),
            usage_getter=lambda _path: 1.0,
            identity_getter=lambda _path: synthetic_identity(tmp_path),
            now=10000,
        ),
    )

    captured = capsys.readouterr()
    assert not captured.out, f"the sweep wrote to stdout: {captured.out!r}"
    assert not captured.err, f"the sweep wrote to stderr: {captured.err!r}"
    assert outcome.deleted == 1, "the sweep did not reclaim the stale target"
    assert not target.exists(), "the sweep reported a deletion it did not perform"


def test_sweep_counts_targets_it_could_not_identify(tmp_path: Path) -> None:
    """A target preserved for want of a filesystem identity is still reported.

    Such a target appears in no per-filesystem record, so the sweep-level count
    is the only evidence the snapshot dropped it rather than found nothing.
    """
    make_target(tmp_path, "project", 1)
    sink = RecordingSink()

    message = "no such device"

    def unidentifiable(_path: Path) -> FilesystemIdentity:
        """Report every path as unresolvable.

        Parameters
        ----------
        _path : Path
            Target path whose identity cannot be resolved; ignored.

        Raises
        ------
        OSError
            Whenever the filesystem identity is requested.
        """
        raise OSError(message)

    outcome = cleanup_scan_roots(
        (tmp_path,),
        build_sink_run(
            sink,
            state_store=StateStore(tmp_path / "state.json"),
            active_build_detector=InactiveBuilds(),
            usage_getter=lambda _path: 1.0,
            identity_getter=unidentifiable,
            now=10000,
        ),
    )

    assert outcome.scanned == 1, "the target was never scanned"
    finish = [
        record
        for record in sink.for_operation("sweep")
        if record["lifecycle"] == "finish"
    ]
    assert finish, "an unidentified target went unreported"
    assert finish[0]["unidentified"] == 1, "an unidentified target went unreported"
