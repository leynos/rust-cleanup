"""Compatibility facade for the decomposed rust-cleanup implementation.

The implementation now lives in focused modules: :mod:`rust_cleanup.discovery`,
:mod:`rust_cleanup.reclaim`, :mod:`rust_cleanup.locking`,
:mod:`rust_cleanup.sweep`, and :mod:`rust_cleanup.cli`. This module preserves
the historical ``rust_cleanup.cleanup`` import path.
"""

from rust_cleanup.cli import (
    UNRESOLVED_PRESSURE_EXIT_STATUS,
    build_parser,
    main,
)
from rust_cleanup.diagnostics import (
    CleanupOutcome,
    CleanupRun,
    FilesystemDiagnostics,
)
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
from rust_cleanup.reclaim import TargetCandidate
from rust_cleanup.sweep import cleanup_scan_roots

__all__ = [
    "CACHEDIR_TAG",
    "SKIP_DIRS",
    "TARGET_DIR",
    "UNRESOLVED_PRESSURE_EXIT_STATUS",
    "CleanupError",
    "CleanupOutcome",
    "CleanupRun",
    "FilesystemDiagnostics",
    "TargetCandidate",
    "advisory_lock",
    "build_parser",
    "cleanup_scan_roots",
    "delete_directory",
    "find_target_dirs",
    "has_recent_files",
    "is_cache_dir",
    "main",
    "newest_mtime",
    "should_skip_dir",
]
