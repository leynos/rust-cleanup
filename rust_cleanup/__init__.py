"""Clean stale Rust target directories."""

from rust_cleanup.cli import UNRESOLVED_PRESSURE_EXIT_STATUS, main
from rust_cleanup.config import CUTOFF_SECONDS
from rust_cleanup.diagnostics import CleanupOutcome, CleanupRun
from rust_cleanup.discovery import (
    CACHEDIR_TAG,
    SKIP_DIRS,
    TARGET_DIR,
    find_target_dirs,
    has_recent_files,
    is_cache_dir,
    newest_mtime,
    should_skip_dir,
)
from rust_cleanup.errors import CleanupError
from rust_cleanup.filesystem import delete_directory
from rust_cleanup.locking import advisory_lock
from rust_cleanup.sweep import cleanup_scan_roots

__all__ = [
    "CACHEDIR_TAG",
    "CUTOFF_SECONDS",
    "SKIP_DIRS",
    "TARGET_DIR",
    "UNRESOLVED_PRESSURE_EXIT_STATUS",
    "CleanupError",
    "CleanupOutcome",
    "CleanupRun",
    "advisory_lock",
    "cleanup_scan_roots",
    "delete_directory",
    "find_target_dirs",
    "has_recent_files",
    "is_cache_dir",
    "main",
    "newest_mtime",
    "should_skip_dir",
]
