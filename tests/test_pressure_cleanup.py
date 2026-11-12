"""Integration-style contracts for pressure-aware candidate reclamation."""

import itertools
import tempfile
from pathlib import Path

from conftest import (
    InactiveBuilds,
    RecordingDeleter,
    build_run,
    make_target,
    synthetic_identity,
)
from hypothesis import given, settings
from hypothesis import strategies as st

from rust_cleanup import CUTOFF_SECONDS, cleanup_scan_roots
from rust_cleanup.active_build import ProcActiveBuildDetector
from rust_cleanup.config import DEFAULT_CONFIG
from rust_cleanup.pressure import Mode
from rust_cleanup.state import StateStore


class ActiveBuilds:
    """A deterministic detector that protects every candidate."""

    def is_active(self, target_path: Path) -> bool:
        """Return true for every target path.

        Parameters
        ----------
        target_path : Path
            Target path the caller asks about; this detector ignores it.

        Returns
        -------
        bool
            Whether the target is protected by the scripted build state.
        """
        return True


def test_pressure_stops_after_reaching_its_recovery_watermark(tmp_path: Path) -> None:
    """Pressure mode preserves newer eligible caches once enough space returns."""
    first = make_target(tmp_path, "first", 1)
    second = make_target(tmp_path, "second", 2)
    # Report critical pressure once, then recovery for every later reading.
    usages = itertools.chain((85.0,), itertools.repeat(74.0))
    outcome = cleanup_scan_roots(
        (tmp_path,),
        build_run(
            config=DEFAULT_CONFIG,
            state_store=StateStore(tmp_path / "state.json"),
            active_build_detector=InactiveBuilds(),
            usage_getter=lambda _path: next(usages),
            identity_getter=lambda _path: synthetic_identity(tmp_path),
            now=1000,
        ),
    )
    assert outcome.filesystems[0].selected_mode is Mode.PRESSURE, (
        "pressure not selected"
    )
    assert outcome.filesystems[0].exit_reached, "recovery watermark not reported"
    assert not first.exists(), "oldest target was not reclaimed"
    assert second.exists(), "cleanup did not stop at recovery"


def test_critical_mode_reclaims_with_its_two_minute_cutoff(tmp_path: Path) -> None:
    """Critical mode reclaims with its own cutoff and stops at recovery."""
    stale = make_target(tmp_path, "stale", 10000 - 300)
    recent = make_target(tmp_path, "recent", 10000 - 60)
    usages = itertools.chain((96.0,), itertools.repeat(85.0))
    outcome = cleanup_scan_roots(
        (tmp_path,),
        build_run(
            state_store=StateStore(tmp_path / "state.json"),
            active_build_detector=InactiveBuilds(),
            usage_getter=lambda _path: next(usages),
            identity_getter=lambda _path: synthetic_identity(tmp_path),
            now=10000,
        ),
    )

    diagnostics = outcome.filesystems[0]
    assert diagnostics.selected_mode is Mode.CRITICAL, "critical mode was not selected"
    assert diagnostics.cutoff_seconds == 2 * 60, "critical cutoff is not two minutes"
    assert not stale.exists(), "critical mode kept a five-minute-old target"
    assert recent.exists(), "critical mode reclaimed inside its two-minute cutoff"
    assert diagnostics.exit_reached, "critical recovery was not reported"
    assert not outcome.unresolved_pressure, "recovered pressure reported as unresolved"


def test_critical_mode_stops_deleting_below_its_recovery_watermark(
    tmp_path: Path,
) -> None:
    """Critical mode deletes only until usage drops below its exit watermark."""
    first = make_target(tmp_path, "first", 1)
    second = make_target(tmp_path, "second", 2)
    third = make_target(tmp_path, "third", 3)
    # Enter critical at 96%, stay critical at 92%, then recover at 89%. The
    # recovered reading repeats so an unexpected extra read reports a recovery
    # rather than raising out of the sweep as an opaque error.
    usages = itertools.chain((96.0, 92.0), itertools.repeat(89.0))
    outcome = cleanup_scan_roots(
        (tmp_path,),
        build_run(
            state_store=StateStore(tmp_path / "state.json"),
            active_build_detector=InactiveBuilds(),
            usage_getter=lambda _path: next(usages),
            identity_getter=lambda _path: synthetic_identity(tmp_path),
            now=10000,
        ),
    )

    diagnostics = outcome.filesystems[0]
    assert diagnostics.selected_mode is Mode.CRITICAL, "critical mode was not selected"
    assert not first.exists(), "the oldest target was not reclaimed"
    assert not second.exists(), "reclamation stopped above the critical watermark"
    assert third.exists(), "reclamation continued below the critical watermark"
    assert diagnostics.deleted == 2, "the deletion count is wrong"
    assert diagnostics.exit_reached, "critical recovery was not reported"
    assert not outcome.unresolved_pressure, "recovered pressure reported as unresolved"


def test_persisted_critical_mode_drives_the_next_sweep(tmp_path: Path) -> None:
    """A stored critical mode is reloaded and keeps its two-minute cutoff.

    The usage reading sits below critical entry but above its recovery
    watermark, so only the persisted mode can select critical retention: a
    sweep that always derived its mode from current usage would fall back to
    the pressure cutoff and spare the five-minute-old target.
    """
    stale = make_target(tmp_path, "stale", 10000 - 300)
    identity = synthetic_identity(tmp_path)
    state_path = tmp_path / "state.json"
    seeded = StateStore(state_path)
    seeded.set_mode(identity, Mode.CRITICAL)
    seeded.save()

    outcome = cleanup_scan_roots(
        (tmp_path,),
        build_run(
            state_store=StateStore(state_path),
            active_build_detector=InactiveBuilds(),
            usage_getter=lambda _path: 92.0,
            identity_getter=lambda _path: identity,
            now=10000,
        ),
    )

    diagnostics = outcome.filesystems[0]
    assert diagnostics.previous_mode is Mode.CRITICAL, "the stored mode was not loaded"
    assert diagnostics.selected_mode is Mode.CRITICAL, (
        "a stored critical mode was discarded between sweeps"
    )
    assert diagnostics.cutoff_seconds == 2 * 60, (
        "the persisted critical cutoff was not applied"
    )
    assert not stale.exists(), "a five-minute-old target survived critical retention"


def test_active_build_detector_excludes_candidate_from_deletion(tmp_path: Path) -> None:
    """An injected active-build detector is a hard safety boundary."""
    target = make_target(tmp_path, "project", 1)
    outcome = cleanup_scan_roots(
        (tmp_path,),
        build_run(
            state_store=StateStore(tmp_path / "state.json"),
            active_build_detector=ActiveBuilds(),
            usage_getter=lambda _path: 1.0,
            identity_getter=lambda _path: synthetic_identity(tmp_path),
            now=10000,
        ),
    )
    assert outcome.deleted == 0, "protected target was counted as deleted"
    assert outcome.filesystems[0].protected == 1, "protection was not counted"
    assert target.exists(), "active-build target was deleted"


def test_proc_detector_protects_a_target_owned_by_cargo(tmp_path: Path) -> None:
    """The Linux adapter recognizes a Cargo working directory in a worktree."""
    worktree = tmp_path / "repos" / "example" / "worktrees" / "branch"
    target = make_target(worktree, "crate", 1)
    process = tmp_path / "proc" / "123"
    process.mkdir(parents=True)
    (process / "comm").write_text("cargo\n", encoding="utf-8")
    (process / "cwd").symlink_to(worktree, target_is_directory=True)
    (process / "environ").write_bytes(b"")
    (process / "cmdline").write_bytes(b"cargo\x00check\x00")
    assert ProcActiveBuildDetector(tmp_path / "proc").is_active(target), (
        "cargo target unprotected"
    )


def test_pre_delete_activity_change_skips_the_target(tmp_path: Path) -> None:
    """The immediate revalidation protects a target changed after discovery."""
    target = make_target(tmp_path, "project", 1)
    # Both the planner and the reclaimer read activity through the same
    # injected port, so the first reading is what discovery snapshots and the
    # second is the activity that arrived afterwards. That later reading
    # repeats, so an extra read keeps the target protected and the assertions
    # below report the failure instead of a ``StopIteration`` escaping the sweep.
    readings = itertools.chain((1.0, 9999.0), itertools.repeat(9999.0))
    outcome = cleanup_scan_roots(
        (tmp_path,),
        build_run(
            state_store=StateStore(tmp_path / "state.json"),
            active_build_detector=InactiveBuilds(),
            usage_getter=lambda _path: 1.0,
            identity_getter=lambda _path: synthetic_identity(tmp_path),
            activity_getter=lambda _path: next(readings),
            now=10000,
        ),
    )
    assert outcome.deleted == 0, "changed target was counted as deleted"
    assert outcome.protected_targets == (target.resolve(),), (
        "the changed target was not recorded as protected"
    )
    assert target.exists(), "changed target was deleted"


def test_planner_snapshots_activity_through_the_injected_port(tmp_path: Path) -> None:
    """Planning reads newest activity from the injected port, not the filesystem.

    The target's artefact is left recent on purpose. A planner that reached for
    its own activity source would see that timestamp and preserve the target, so
    deleting it here proves the composition root's port governs the snapshot.
    """
    target = make_target(tmp_path, "project", 9999.0)
    outcome = cleanup_scan_roots(
        (tmp_path,),
        build_run(
            state_store=StateStore(tmp_path / "state.json"),
            active_build_detector=InactiveBuilds(),
            usage_getter=lambda _path: 1.0,
            identity_getter=lambda _path: synthetic_identity(tmp_path),
            activity_getter=lambda _path: 0.0,
            now=10000,
        ),
    )
    assert outcome.deleted == 1, "the injected activity port did not drive planning"
    assert not target.exists(), "the injected activity port did not drive planning"


def test_oldest_first_order_uses_canonical_path_to_break_ties(tmp_path: Path) -> None:
    """Equal activity timestamps produce a deterministic canonical deletion order."""
    alpha = make_target(tmp_path, "alpha", 1)
    beta = make_target(tmp_path, "beta", 1)
    deleter = RecordingDeleter()
    cleanup_scan_roots(
        (tmp_path,),
        build_run(
            state_store=StateStore(tmp_path / "state.json"),
            active_build_detector=InactiveBuilds(),
            usage_getter=lambda _path: 1.0,
            identity_getter=lambda _path: synthetic_identity(tmp_path),
            delete_directory=deleter,
            now=10000,
        ),
    )
    assert deleter.deleted == [alpha.resolve(), beta.resolve()], (
        "path tie-breaker drifted"
    )


def test_candidates_are_grouped_by_their_backing_filesystem(tmp_path: Path) -> None:
    """One sweep makes separate decisions for distinct filesystem identities."""
    first = make_target(tmp_path, "first", 1)
    second = make_target(tmp_path, "second", 1)
    identities = {
        first.resolve(): synthetic_identity(tmp_path / "one", 1),
        second.resolve(): synthetic_identity(tmp_path / "two", 2),
    }
    outcome = cleanup_scan_roots(
        (tmp_path,),
        build_run(
            state_store=StateStore(tmp_path / "state.json"),
            active_build_detector=InactiveBuilds(),
            usage_getter=lambda _path: 1.0,
            identity_getter=lambda path: identities[path.resolve()],
            delete_directory=RecordingDeleter(),
            now=10000,
        ),
    )
    assert len(outcome.filesystems) == 2, "filesystem candidates were merged"


@given(
    st.lists(
        st.integers(min_value=1, max_value=999), min_size=2, max_size=8, unique=True
    )
)
@settings(deadline=None)
def test_oldest_first_order_is_monotonic_for_many_synthetic_targets(
    activities: list[int],
) -> None:
    """Property coverage preserves globally sorted activity order in one filesystem."""
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        targets = [
            make_target(root, str(index), activity)
            for index, activity in enumerate(activities)
        ]
        # Place every synthetic activity strictly inside the normal-mode
        # cutoff so the property exercises ordering rather than eligibility.
        now = max(activities) + CUTOFF_SECONDS + 1
        deleter = RecordingDeleter()
        cleanup_scan_roots(
            (root,),
            build_run(
                state_store=StateStore(root / "state.json"),
                active_build_detector=InactiveBuilds(),
                usage_getter=lambda _path: 1.0,
                identity_getter=lambda _path: synthetic_identity(root),
                delete_directory=deleter,
                now=now,
            ),
        )
        by_path = {
            target.resolve(): activity
            for target, activity in zip(targets, activities, strict=True)
        }
        assert [by_path[path] for path in deleter.deleted] == sorted(activities), (
            "order is not oldest first"
        )
