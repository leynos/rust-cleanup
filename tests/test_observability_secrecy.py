"""Contracts for the values sweep telemetry must never carry.

A record is bounded by construction: it names an operation, a lifecycle, an
outcome from a fixed vocabulary, and counters. These tests drive the paths
where a value could plausibly escape - a deletion that fails, state that is
malformed, state that cannot be saved, an unreadable mount - and assert that
no target path, mount point, persisted content, or raw error text reaches the
telemetry stream.
"""

import os
import typing as typ
from unittest import mock

import pytest
from conftest import (
    FailingDeleter,
    InactiveBuilds,
    RecordingSink,
    build_sink_run,
    make_target,
    synthetic_identity,
)

from rust_cleanup import CACHEDIR_TAG, cleanup_scan_roots
from rust_cleanup.observability import FIELDS, LIFECYCLES, OPERATIONS, OUTCOMES
from rust_cleanup.state import StateStore

if typ.TYPE_CHECKING:
    from pathlib import Path


def test_records_never_contain_paths_or_secret_like_values(tmp_path: Path) -> None:
    """No record carries a target path, mount point, or secret-like value."""
    secret = tmp_path / "sk_live_51H8xQ" / "target"
    secret.mkdir(parents=True)
    (secret / CACHEDIR_TAG).touch()
    artefact = secret / "build.o"
    artefact.write_text("stale", encoding="utf-8")
    os.utime(artefact, (1, 1))
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
    assert sink.records, "no telemetry was recorded"
    serialized = sink.serialized()
    for leaked in (str(tmp_path), str(secret), "sk_live_51H8xQ", "build.o"):
        assert leaked not in serialized, f"record leaked {leaked!r}"
    for record in sink.records:
        assert set(record) <= FIELDS, f"record escaped the fixed fields: {record}"
        assert record["operation"] in OPERATIONS, "unknown operation recorded"
        assert record["lifecycle"] in LIFECYCLES, "unknown lifecycle recorded"
        if "outcome" in record:
            assert record["outcome"] in OUTCOMES, "outcome outside the vocabulary"


def test_deletion_failure_records_bounded_counters_without_error_text(
    tmp_path: Path,
) -> None:
    """A failing deletion is counted, and the raw error never reaches telemetry."""
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
            delete_directory=FailingDeleter("disk on fire"),
            now=10000,
        ),
    )
    finish = sink.for_operation("deletion")[-1]
    assert finish["failed"] == 1, "deletion failure was not counted"
    assert finish["deleted"] == 0, "failed deletion counted as deleted"
    assert "disk on fire" not in sink.serialized(), "raw error text leaked"


def test_unresolved_pressure_is_recorded_as_a_bounded_outcome(tmp_path: Path) -> None:
    """Pressure that cannot be relieved is reported without a mount point."""
    # Activity inside the critical cutoff keeps the only candidate protected.
    make_target(tmp_path, "recent", 9999)
    sink = RecordingSink()
    outcome = cleanup_scan_roots(
        (tmp_path,),
        build_sink_run(
            sink,
            state_store=StateStore(tmp_path / "state.json"),
            active_build_detector=InactiveBuilds(),
            usage_getter=lambda _path: 97.0,
            identity_getter=lambda _path: synthetic_identity(tmp_path),
            now=10000,
        ),
    )
    assert outcome.unresolved_pressure, "unresolved pressure not reported"
    assert sink.for_operation("unresolved_pressure")[-1]["outcome"] == "unresolved", (
        "unresolved pressure outcome changed"
    )
    assert sink.for_operation("sweep")[-1]["outcome"] == "unresolved", (
        "sweep outcome did not report unresolved pressure"
    )
    assert str(tmp_path) not in sink.serialized(), "mount point leaked"


def test_malformed_state_is_recorded_without_its_contents(tmp_path: Path) -> None:
    """Malformed state is reported as a token, never echoed into telemetry."""
    state_path = tmp_path / "state.json"
    state_path.write_text('{"filesystems": {"secret": "not-a-mode"}}', encoding="utf-8")
    sink = RecordingSink()
    cleanup_scan_roots(
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
    assert sink.for_operation("state_load")[-1]["outcome"] == "malformed", (
        "malformed state was not reported"
    )
    assert "secret" not in sink.serialized(), "state contents leaked"


def test_state_save_failure_is_recorded_before_the_error_propagates(
    tmp_path: Path,
) -> None:
    """A failed save is recorded as a bounded outcome, not as raw text."""
    make_target(tmp_path, "stale", 1)
    sink = RecordingSink()
    with (
        mock.patch.object(StateStore, "save", side_effect=OSError("no space left")),
        pytest.raises(OSError, match="no space left"),
    ):
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
    assert sink.for_operation("state_save")[-1]["outcome"] == "failed", (
        "failed save was not reported"
    )
    assert "no space left" not in sink.serialized(), "raw error text leaked"
