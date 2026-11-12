"""Contracts for the telemetry boundaries emitted by one cleanup sweep.

Every long-running phase a sweep runs opens a bounded span and closes it with
an outcome a consumer can act on. These tests assert that boundary set, the
duration and correlation discipline that makes a span usable, and what the
sweep does *not* do - delete, re-read recovery, or persist state - when
discovery reports itself incomplete or the run is a dry run.
"""

import typing as typ
from unittest import mock

from conftest import (
    InactiveBuilds,
    RecordingSink,
    build_sink_run,
    make_target,
    synthetic_identity,
)

from rust_cleanup import cleanup_scan_roots
from rust_cleanup.discovery import DiscoveryScan
from rust_cleanup.state import StateStore

if typ.TYPE_CHECKING:
    from pathlib import Path


def test_sweep_emits_start_and_finish_boundaries(tmp_path: Path) -> None:
    """Every long-running phase reports a bounded start and finish boundary."""
    make_target(tmp_path, "stale", 1)
    sink = RecordingSink()
    cleanup_scan_roots(
        (tmp_path,),
        build_sink_run(
            sink,
            state_store=StateStore(tmp_path / "state.json"),
            active_build_detector=InactiveBuilds(),
            usage_getter=lambda _path: 1.0,
            identity_getter=lambda _path: synthetic_identity(tmp_path),
            now=10000,
        ),
    )
    expected = {
        ("sweep", "start"),
        ("sweep", "finish"),
        ("active_build_inspection", "start"),
        ("active_build_inspection", "finish"),
        ("filesystem_decision", "start"),
        ("filesystem_decision", "finish"),
        ("deletion", "start"),
        ("deletion", "finish"),
        ("state_load", "finish"),
        ("state_save", "finish"),
    }
    missing = expected - sink.boundaries()
    assert not missing, f"missing boundaries: {sorted(missing)}"


def _lifecycles_by_operation(
    records: list[dict[str, object]],
) -> dict[str, list[str]]:
    """Group each record's lifecycle under the operation that emitted it.

    Returns
    -------
    dict[str, list[str]]
        Lifecycle stages grouped by operation in emission order.
    """
    lifecycles: dict[str, list[str]] = {}
    for record in records:
        operation = record["operation"]
        lifecycle = record["lifecycle"]
        assert isinstance(operation, str), "a record carried no operation name"
        assert isinstance(lifecycle, str), "a record carried no lifecycle name"
        lifecycles.setdefault(operation, []).append(lifecycle)
    return lifecycles


def _opened_operations(lifecycles: dict[str, list[str]]) -> set[str]:
    """Return the operations that opened a span.

    Returns
    -------
    set[str]
        Operations with a captured start event.
    """
    return {operation for operation, spans in lifecycles.items() if "start" in spans}


def _assert_spans_pair_up(lifecycles: dict[str, list[str]], opened: set[str]) -> None:
    """Assert each operation emitted exactly the records its span accounts for."""
    for operation, spans in lifecycles.items():
        expected = ["start", "finish"] if operation in opened else ["finish"]
        assert spans == expected, f"{operation} did not pair one span: {spans!r}"


def _closing_records(
    records: list[dict[str, object]], opened: set[str]
) -> list[dict[str, object]]:
    """Return the records that close a span which actually opened.

    Returns
    -------
    list[dict[str, object]]
        Captured finish events in emission order.
    """
    return [
        record
        for record in records
        if record["operation"] in opened and record["lifecycle"] == "finish"
    ]


def _assert_bounded_durations(records: list[dict[str, object]]) -> None:
    """Assert every closing record carries a bounded, non-negative duration."""
    for record in records:
        duration = record.get("duration_ms")
        assert isinstance(duration, int | float), (
            f"{record['operation']} finished without a bounded duration: {record!r}"
        )
        assert not isinstance(duration, bool), (
            f"{record['operation']} finished without a bounded duration: {record!r}"
        )
        assert duration >= 0, "the recorded duration is negative"


def _correlation_tokens(records: list[dict[str, object]]) -> set[object]:
    """Return the correlation tokens every record was emitted under.

    Returns
    -------
    set[object]
        Distinct correlation tokens attached to captured events.
    """
    assert all("correlation" in record for record in records), "a record has no token"
    return {record["correlation"] for record in records}


def test_every_paired_span_closes_with_a_bounded_duration(tmp_path: Path) -> None:
    """Each opened span finishes once, after its start, with a bounded duration.

    Operations that only ever emit a ``finish`` record are instantaneous
    decisions rather than spans, so the pairing contract is asserted over the
    operations that actually opened a boundary: a span that opens must close
    exactly once and after its own start, or the records are not a timeline a
    consumer can read.
    """
    make_target(tmp_path, "stale", 1)
    sink = RecordingSink()
    cleanup_scan_roots(
        (tmp_path,),
        build_sink_run(
            sink,
            state_store=StateStore(tmp_path / "state.json"),
            active_build_detector=InactiveBuilds(),
            usage_getter=lambda _path: 1.0,
            identity_getter=lambda _path: synthetic_identity(tmp_path),
            now=10000,
        ),
    )

    lifecycles = _lifecycles_by_operation(sink.records)
    opened = _opened_operations(lifecycles)
    assert opened, "no span was opened"
    _assert_spans_pair_up(lifecycles, opened)
    _assert_bounded_durations(_closing_records(sink.records, opened))
    tokens = _correlation_tokens(sink.records)
    assert len(tokens) == 1, (
        f"one sweep emitted records under several tokens: {tokens!r}"
    )


def test_an_incomplete_scan_stops_the_sweep_before_it_deletes(tmp_path: Path) -> None:
    """An incomplete scan is reported, and never drives deletion or state."""
    target = make_target(tmp_path, "stale", 1)
    state_path = tmp_path / "state.json"
    sink = RecordingSink()
    with mock.patch(
        "rust_cleanup.discovery.scan_target_dirs",
        return_value=DiscoveryScan(targets=(), failures=1),
    ):
        outcome = cleanup_scan_roots(
            (tmp_path,),
            build_sink_run(
                sink,
                state_store=StateStore(state_path),
                active_build_detector=InactiveBuilds(),
                usage_getter=lambda _path: 1.0,
                identity_getter=lambda _path: synthetic_identity(tmp_path),
                now=10000,
            ),
        )

    assert outcome.discovery_failed, "the incomplete scan was not reported"
    assert outcome.unresolved_pressure, (
        "an incomplete scan reported pressure as resolved"
    )
    assert not state_path.exists(), "an incomplete scan persisted state"
    assert target.exists(), "an incomplete scan deleted a target"
    boundaries = sink.boundaries()
    assert ("deletion", "start") not in boundaries, "an incomplete scan deleted"
    assert ("state_save", "finish") not in boundaries, "an incomplete scan saved state"
    discovery = sink.for_operation("discovery")[-1]
    assert discovery["outcome"] == "incomplete", (
        f"the incomplete scan was recorded as complete: {discovery!r}"
    )
    assert discovery["failed"] == 1, "the failed inspection was not counted"
    assert discovery["scanned"] == 0, "a failed scan reported targets"
    assert sink.for_operation("sweep")[-1]["outcome"] == "failed", (
        "the sweep reported an incomplete run as completed"
    )


def test_records_are_aggregate_counters_not_per_candidate_records(
    tmp_path: Path,
) -> None:
    """Candidate volume changes counters, never the number of records."""
    make_target(tmp_path, "only", 1)
    single = RecordingSink()
    cleanup_scan_roots(
        (tmp_path,),
        build_sink_run(
            single,
            state_store=StateStore(tmp_path / "state.json"),
            active_build_detector=InactiveBuilds(),
            usage_getter=lambda _path: 1.0,
            identity_getter=lambda _path: synthetic_identity(tmp_path),
            now=10000,
        ),
    )
    many_root = tmp_path / "many"
    many_root.mkdir()
    for index in range(4):
        make_target(many_root, f"crate{index}", 1)
    many = RecordingSink()
    cleanup_scan_roots(
        (many_root,),
        build_sink_run(
            many,
            state_store=StateStore(many_root / "state.json"),
            active_build_detector=InactiveBuilds(),
            usage_getter=lambda _path: 1.0,
            identity_getter=lambda _path: synthetic_identity(many_root),
            now=10000,
        ),
    )
    assert len(many.records) == len(single.records), (
        "record count grew with candidate count"
    )
    assert many.for_operation("deletion")[-1]["deleted"] == 4, (
        "aggregate deletion counter is wrong"
    )


def test_dry_run_records_skipped_recovery_and_no_deletion(tmp_path: Path) -> None:
    """A dry run reports policy without deletion, recovery, or state writes."""
    target = make_target(tmp_path, "stale", 1)
    state_path = tmp_path / "state.json"
    sink = RecordingSink()
    outcome = cleanup_scan_roots(
        (tmp_path,),
        build_sink_run(
            sink,
            dry_run=True,
            state_store=StateStore(state_path),
            active_build_detector=InactiveBuilds(),
            usage_getter=lambda _path: 85.0,
            identity_getter=lambda _path: synthetic_identity(tmp_path),
            now=10000,
        ),
    )
    assert outcome.deleted == 1, "dry run did not report the planned deletion"
    assert target.exists(), "dry run deleted the target"
    assert not state_path.exists(), "dry run wrote state"
    assert ("deletion", "start") not in sink.boundaries(), "dry run started deletion"
    assert ("deletion", "finish") not in sink.boundaries(), "dry run finished deletion"
    assert ("state_save", "finish") not in sink.boundaries(), "dry run saved state"
    assert sink.for_operation("recovery_watermark")[-1]["outcome"] == "skipped", (
        "dry-run recovery was not skipped"
    )


def test_reclamation_emits_one_deletion_boundary_pair(tmp_path: Path) -> None:
    """A real sweep opens and closes exactly one deletion boundary."""
    make_target(tmp_path, "stale", 1)
    sink = RecordingSink()
    cleanup_scan_roots(
        (tmp_path,),
        build_sink_run(
            sink,
            state_store=StateStore(tmp_path / "state.json"),
            active_build_detector=InactiveBuilds(),
            usage_getter=lambda _path: 1.0,
            identity_getter=lambda _path: synthetic_identity(tmp_path),
            now=10000,
        ),
    )
    lifecycles = [record["lifecycle"] for record in sink.for_operation("deletion")]
    assert lifecycles == ["start", "finish"], "deletion boundary pair is not unique"


def test_deletion_span_reports_an_unmeasurable_usage_reread(tmp_path: Path) -> None:
    """A re-read failure closes the deletion span as failed, not completed.

    Reclamation stops when the filesystem can no longer be measured, so the
    span that covered the deletions has to carry that outcome: closing it as
    ``completed`` would claim a recovery decision the sweep never took.
    """
    make_target(tmp_path, "first", 1)
    make_target(tmp_path, "second", 2)
    sink = RecordingSink()

    cleanup_scan_roots(
        (tmp_path,),
        build_sink_run(
            sink,
            state_store=StateStore(tmp_path / "state.json"),
            active_build_detector=InactiveBuilds(),
            usage_getter=mock.Mock(side_effect=[85.0, OSError("usage unavailable")]),
            identity_getter=lambda _path: synthetic_identity(tmp_path),
            now=10000,
        ),
    )

    finish = sink.for_operation("deletion")[-1]
    assert finish["lifecycle"] == "finish", "the deletion span never closed"
    assert finish["outcome"] == "failed", (
        f"an unmeasurable re-read closed the deletion span as {finish['outcome']!r}"
    )
    assert finish["deleted"] == 1, "the deletion counter lost the reclaimed target"


def test_state_records_report_load_and_save_outcomes(tmp_path: Path) -> None:
    """Persistence boundaries carry the bounded load and save outcome."""
    make_target(tmp_path, "stale", 1)
    state_path = tmp_path / "state.json"
    first = RecordingSink()
    cleanup_scan_roots(
        (tmp_path,),
        build_sink_run(
            first,
            state_store=StateStore(state_path),
            active_build_detector=InactiveBuilds(),
            usage_getter=lambda _path: 1.0,
            identity_getter=lambda _path: synthetic_identity(tmp_path),
            now=10000,
        ),
    )
    assert first.for_operation("state_load")[-1]["outcome"] == "absent", (
        "absent state was not reported"
    )
    assert first.for_operation("state_save")[-1]["outcome"] == "saved", (
        "saved state was not reported"
    )
    second = RecordingSink()
    cleanup_scan_roots(
        (tmp_path,),
        build_sink_run(
            second,
            state_store=StateStore(state_path),
            active_build_detector=InactiveBuilds(),
            usage_getter=lambda _path: 1.0,
            identity_getter=lambda _path: synthetic_identity(tmp_path),
            now=10000,
        ),
    )
    assert second.for_operation("state_load")[-1]["outcome"] == "loaded", (
        "persisted state was not reloaded"
    )
