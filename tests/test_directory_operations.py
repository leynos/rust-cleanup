"""Unit tests for rust_cleanup directory operations."""

import os
import time
import typing as typ
from unittest import mock

import pytest
from conftest import InactiveBuilds, synthetic_identity

from rust_cleanup import (
    CACHEDIR_TAG,
    CUTOFF_SECONDS,
    SKIP_DIRS,
    TARGET_DIR,
    CleanupError,
    CleanupRun,
    cleanup_scan_roots,
    delete_directory,
    find_target_dirs,
    has_recent_files,
    is_cache_dir,
    should_skip_dir,
)
from rust_cleanup.state import StateStore

if typ.TYPE_CHECKING:
    from pathlib import Path


class TestShouldSkipDir:
    """Tests for should_skip_dir."""

    @pytest.mark.parametrize("dirname", sorted(SKIP_DIRS))
    def test_should_skip_dir_accepts_skip_dirs(self, dirname: str) -> None:
        """Verify that every SKIP_DIRS member is classified as skippable."""
        assert should_skip_dir(dirname) is True, (
            f"should_skip_dir should classify {dirname}"
        )

    @pytest.mark.parametrize("dirname", ["target", "src"])
    def test_should_skip_dir_rejects_non_skip_dirs(self, dirname: str) -> None:
        """Verify that directories outside SKIP_DIRS are not skipped."""
        assert should_skip_dir(dirname) is False, (
            f"should_skip_dir should classify {dirname}"
        )


class TestIsCacheDir:
    """Tests for is_cache_dir."""

    def test_returns_true_when_cachedir_tag_exists(self, tmp_path: Path) -> None:
        """Verify that returns true when cachedir tag exists."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        (cache_dir / CACHEDIR_TAG).touch()
        assert is_cache_dir(cache_dir) is True, (
            "is_cache_dir should accept CACHEDIR.TAG file"
        )

    def test_returns_false_when_cachedir_tag_missing(self, tmp_path: Path) -> None:
        """Verify that returns false when cachedir tag missing."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        assert is_cache_dir(cache_dir) is False, (
            "is_cache_dir should reject missing tag"
        )

    def test_returns_false_when_cachedir_tag_is_directory(self, tmp_path: Path) -> None:
        """Verify that returns false when cachedir tag is directory."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        (cache_dir / CACHEDIR_TAG).mkdir()
        assert is_cache_dir(cache_dir) is False, (
            "is_cache_dir should reject tag directory"
        )


class TestHasRecentFiles:
    """Tests for has_recent_files."""

    def test_returns_true_for_recent_file(self, tmp_path: Path) -> None:
        """Verify that returns true for recent file."""
        test_file = tmp_path / "recent.txt"
        test_file.write_text("content")
        current_time = time.time()
        cutoff_time = current_time - CUTOFF_SECONDS
        assert has_recent_files(tmp_path, cutoff_time) is True, (
            "has_recent_files should detect recent file"
        )

    def test_returns_false_for_old_file(self, tmp_path: Path) -> None:
        """Verify that returns false for old file."""
        test_file = tmp_path / "old.txt"
        test_file.write_text("content")
        old_time = time.time() - (CUTOFF_SECONDS + 3600)
        os.utime(test_file, (old_time, old_time))
        cutoff_time = time.time() - CUTOFF_SECONDS
        assert has_recent_files(tmp_path, cutoff_time) is False, (
            "has_recent_files should ignore old file"
        )

    def test_returns_false_for_empty_directory(self, tmp_path: Path) -> None:
        """Verify that returns false for empty directory."""
        cutoff_time = time.time() - CUTOFF_SECONDS
        assert has_recent_files(tmp_path, cutoff_time) is False, (
            "has_recent_files should reject empty directory"
        )

    def test_checks_nested_files(self, tmp_path: Path) -> None:
        """Verify that checks nested files."""
        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)
        test_file = nested / "deep.txt"
        test_file.write_text("content")
        cutoff_time = time.time() - CUTOFF_SECONDS
        assert has_recent_files(tmp_path, cutoff_time) is True, (
            "has_recent_files should check nested files"
        )

    def test_checks_files_in_nested_directories(self, tmp_path: Path) -> None:
        """Verify activity calculation includes every nested target file.

        Parameters
        ----------
        tmp_path : Path
            Root holding the nested ``.git`` directory under test.
        """
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        test_file = git_dir / "recent.txt"
        test_file.write_text("content")
        cutoff_time = time.time() - CUTOFF_SECONDS
        assert has_recent_files(tmp_path, cutoff_time) is True, (
            "has_recent_files should inspect every target file"
        )

    def test_handles_missing_file_gracefully(self, tmp_path: Path) -> None:
        """Verify that handles missing file gracefully."""
        test_file = tmp_path / "temp.txt"
        test_file.write_text("content")
        cutoff_time = time.time() - CUTOFF_SECONDS
        test_file.unlink()
        assert has_recent_files(tmp_path, cutoff_time) is False, (
            "has_recent_files should tolerate missing files"
        )


class TestDeleteDirectory:
    """Tests for delete_directory."""

    def test_deletes_directory_and_contents(self, tmp_path: Path) -> None:
        """Verify that deletes directory and contents."""
        target = tmp_path / "to_delete"
        target.mkdir()
        (target / "file.txt").write_text("content")
        (target / "subdir").mkdir()
        (target / "subdir" / "another.txt").write_text("more content")

        assert delete_directory(target) is True, (
            "delete_directory should delete existing directory"
        )
        assert not target.exists(), "delete_directory should remove directory contents"

    def test_raises_for_nonexistent_directory(self, tmp_path: Path) -> None:
        """Verify that raises for nonexistent directory."""
        target = tmp_path / "does_not_exist"
        with pytest.raises(CleanupError, match="failed to delete"):
            delete_directory(target)

    def test_raises_without_logging_on_rmtree_error(
        self, tmp_path: Path, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """Verify that raises without logging on rmtree error."""
        target = tmp_path / "to_delete"
        target.mkdir()
        error_message = "permission denied"

        with (
            mock.patch(
                "rust_cleanup.filesystem.shutil.rmtree",
                side_effect=OSError(error_message),
            ),
            pytest.raises(CleanupError, match="failed to delete"),
        ):
            delete_directory(target)

        captured = capfd.readouterr()
        assert not captured.err, (
            "delete_directory should leave stderr logging to callers"
        )

    def test_cleanup_returns_delete_error_through_the_outcome(
        self, tmp_path: Path, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """Verify that a failed deletion is returned rather than printed."""
        target = tmp_path / TARGET_DIR
        target.mkdir()
        (target / CACHEDIR_TAG).touch()
        error_message = "permission denied"

        with mock.patch(
            "rust_cleanup.filesystem.shutil.rmtree", side_effect=OSError(error_message)
        ):
            outcome = cleanup_scan_roots(
                (tmp_path,),
                CleanupRun(
                    state_store=StateStore(tmp_path / "state.json"),
                    active_build_detector=InactiveBuilds(),
                    usage_getter=lambda _path: 1.0,
                    identity_getter=lambda _path: synthetic_identity(tmp_path),
                    now=10000,
                ),
            )

        captured = capfd.readouterr()
        assert outcome.scanned == 1, "cleanup_scan_roots should scan target with error"
        assert outcome.deleted == 0, "cleanup_scan_roots should not count failure"
        assert len(outcome.deletion_failures) == 1, (
            "the failed deletion was not returned to the caller"
        )
        failure = outcome.deletion_failures[0]
        assert failure.path == target.resolve(), "the failure named the wrong target"
        assert error_message in failure.message, "the failure dropped its reason"
        assert not captured.out, "the sweep should leave rendering to its caller"
        assert not captured.err, "the sweep should leave rendering to its caller"


class TestFindTargetDirs:
    """Tests for find_target_dirs."""

    def test_finds_target_with_cachedir_tag(self, tmp_path: Path) -> None:
        """Verify that finds target with cachedir tag."""
        target = tmp_path / TARGET_DIR
        target.mkdir()
        (target / CACHEDIR_TAG).touch()

        results = find_target_dirs(tmp_path)
        assert len(results) == 1, "find_target_dirs should find one tagged target"
        assert results[0] == target, "find_target_dirs should return tagged target path"

    def test_ignores_target_without_cachedir_tag(self, tmp_path: Path) -> None:
        """Verify that ignores target without cachedir tag."""
        target = tmp_path / TARGET_DIR
        target.mkdir()

        results = find_target_dirs(tmp_path)
        assert len(results) == 0, "find_target_dirs should ignore untagged target"

    def test_finds_multiple_targets(self, tmp_path: Path) -> None:
        """Verify that finds multiple targets."""
        for i in range(3):
            project = tmp_path / f"project{i}"
            project.mkdir()
            target = project / TARGET_DIR
            target.mkdir()
            (target / CACHEDIR_TAG).touch()

        results = find_target_dirs(tmp_path)
        assert len(results) == 3, "find_target_dirs should find all tagged targets"

    def test_skips_node_modules(self, tmp_path: Path) -> None:
        """Verify that skips node modules."""
        node_modules = tmp_path / "node_modules"
        node_modules.mkdir()
        target = node_modules / TARGET_DIR
        target.mkdir()
        (target / CACHEDIR_TAG).touch()

        results = find_target_dirs(tmp_path)
        assert len(results) == 0, "find_target_dirs should skip node_modules"

    def test_skips_git_directory(self, tmp_path: Path) -> None:
        """Verify that skips git directory."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        target = git_dir / TARGET_DIR
        target.mkdir()
        (target / CACHEDIR_TAG).touch()

        results = find_target_dirs(tmp_path)
        assert len(results) == 0, "find_target_dirs should skip .git directory"

    def test_does_not_traverse_into_target(self, tmp_path: Path) -> None:
        """Verify that does not traverse into target."""
        outer_target = tmp_path / TARGET_DIR
        outer_target.mkdir()
        (outer_target / CACHEDIR_TAG).touch()
        inner_target = outer_target / TARGET_DIR
        inner_target.mkdir()
        (inner_target / CACHEDIR_TAG).touch()

        results = find_target_dirs(tmp_path)
        assert len(results) == 1, (
            "find_target_dirs should not traverse nested target dirs"
        )
        assert results[0] == outer_target, "find_target_dirs should return outer target"
