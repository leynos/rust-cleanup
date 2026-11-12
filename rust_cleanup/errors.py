"""Shared exception types for :mod:`rust_cleanup`.

Keeping the cleanup error in its own module lets the discovery, reclamation,
locking, sweep, and command-line modules raise and catch one type without
importing each other.
"""


class CleanupError(Exception):
    """Raised when a cleanup operation cannot proceed safely."""
