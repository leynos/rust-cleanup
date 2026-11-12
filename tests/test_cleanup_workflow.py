"""Unit tests for rust_cleanup cleanup workflows."""

import os
import time
import typing as typ

from conftest import InactiveBuilds, make_target

from rust_cleanup import (
    CACHEDIR_TAG,
    CUTOFF_SECONDS,
    TARGET_DIR,
    CleanupRun,
    cleanup_scan_roots,
)
from rust_cleanup.state import StateStore

if typ.TYPE_CHECKING:
    from pathlib import Path


class TestCleanupTargetDirs:
    """Tests for cleanup_scan_roots."""

    def test_deletes_stale_target(self, tmp_path: Path) -> None:
        """Verify that deletes stale target."""
        target = tmp_path / TARGET_DIR
        target.mkdir()
        (target / CACHEDIR_TAG).touch()
        old_file = target / "old.o"
        old_file.write_text("object code")
        old_time = time.time() - (CUTOFF_SECONDS + 3600)
        os.utime(old_file, (old_time, old_time))

        outcome = cleanup_scan_roots(
            (tmp_path,),
            CleanupRun(
                state_store=StateStore(tmp_path / "state.json"),
                active_build_detector=InactiveBuilds(),
            ),
        )
        assert outcome.scanned == 1, "cleanup_scan_roots should scan one target"
        assert outcome.deleted == 1, "cleanup_scan_roots should delete stale target"
        assert not target.exists(), "cleanup_scan_roots should remove stale target"

    def test_keeps_recent_target(self, tmp_path: Path) -> None:
        """Verify that keeps recent target."""
        target = tmp_path / TARGET_DIR
        target.mkdir()
        (target / CACHEDIR_TAG).touch()
        recent_file = target / "recent.o"
        recent_file.write_text("object code")

        outcome = cleanup_scan_roots(
            (tmp_path,),
            CleanupRun(
                state_store=StateStore(tmp_path / "state.json"),
                active_build_detector=InactiveBuilds(),
            ),
        )
        assert outcome.scanned == 1, "cleanup_scan_roots should scan recent target"
        assert outcome.deleted == 0, "cleanup_scan_roots should keep recent target"
        assert target.exists(), "cleanup_scan_roots should preserve recent target"

    def test_dry_run_does_not_delete(self, tmp_path: Path) -> None:
        """Verify that dry run does not delete."""
        target = tmp_path / TARGET_DIR
        target.mkdir()
        (target / CACHEDIR_TAG).touch()
        old_file = target / "old.o"
        old_file.write_text("object code")
        old_time = time.time() - (CUTOFF_SECONDS + 3600)
        os.utime(old_file, (old_time, old_time))

        outcome = cleanup_scan_roots(
            (tmp_path,),
            CleanupRun(
                dry_run=True,
                state_store=StateStore(tmp_path / "state.json"),
                active_build_detector=InactiveBuilds(),
            ),
        )
        assert outcome.scanned == 1, "cleanup_scan_roots dry run should scan target"
        assert outcome.deleted == 1, "cleanup_scan_roots dry run should count deletion"
        assert target.exists(), "cleanup_scan_roots dry run should preserve target"

    def test_handles_empty_target_directory(self, tmp_path: Path) -> None:
        """Verify that handles empty target directory."""
        target = tmp_path / TARGET_DIR
        target.mkdir()
        (target / CACHEDIR_TAG).touch()

        outcome = cleanup_scan_roots(
            (tmp_path,),
            CleanupRun(
                state_store=StateStore(tmp_path / "state.json"),
                active_build_detector=InactiveBuilds(),
            ),
        )
        assert outcome.scanned == 1, "cleanup_scan_roots should scan empty target"
        assert outcome.deleted == 1, "cleanup_scan_roots should delete empty target"
        assert not target.exists(), "cleanup_scan_roots should remove empty target"


class TestIntegration:
    """Integration tests for the complete workflow."""

    def test_full_cleanup_workflow(self, tmp_path: Path) -> None:
        """Verify that full cleanup workflow."""
        old_time = time.time() - (CUTOFF_SECONDS + 3600)
        target1 = make_target(tmp_path, "project1", old_time)
        target2 = make_target(tmp_path, "project2", time.time())
        target3 = make_target(tmp_path, "project3", old_time)
        (target3 / CACHEDIR_TAG).unlink()
        target4 = make_target(tmp_path / "node_modules", "some-crate", old_time)

        outcome = cleanup_scan_roots(
            (tmp_path,),
            CleanupRun(
                state_store=StateStore(tmp_path / "state.json"),
                active_build_detector=InactiveBuilds(),
            ),
        )

        assert outcome.scanned == 2, (
            "cleanup_scan_roots integration should scan tagged targets only"
        )
        assert outcome.deleted == 1, (
            "cleanup_scan_roots integration should delete stale tagged target"
        )
        assert not target1.exists(), (
            "cleanup_scan_roots integration should delete stale target"
        )
        assert target2.exists(), (
            "cleanup_scan_roots integration should preserve recent target"
        )
        assert target3.exists(), (
            "cleanup_scan_roots integration should ignore untagged target"
        )
        assert target4.exists(), (
            "cleanup_scan_roots integration should skip node_modules target"
        )
