"""Advisory-lock contracts for overlapping cleanup invocations.

The lock is the sweep's cross-process guard: a second invocation, whether in
this process or a separate one, must observe contention and leave reclamation
alone.
"""

import os
import subprocess  # ruff: ignore[suspicious-subprocess-import] - fixed-argv child verifies cross-process exclusion.
import sys
import typing as typ
from pathlib import Path

from rust_cleanup import advisory_lock

if typ.TYPE_CHECKING:
    import pytest


def test_advisory_lock_rejects_an_overlapping_local_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second lock acquisition observes contention without blocking."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    with advisory_lock() as first_acquired, advisory_lock() as second_acquired:
        assert first_acquired, "first lock acquisition failed"
        assert not second_acquired, "second lock acquisition succeeded"


def test_advisory_lock_rejects_an_overlapping_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A separate process cannot race the lock held by the current sweep."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    source_root = Path(__file__).resolve().parents[1]
    environment = {**os.environ, "PYTHONPATH": str(source_root)}
    command = (
        "from rust_cleanup.cleanup import advisory_lock\n"
        "with advisory_lock() as acquired:\n"
        "    print(acquired)\n"
    )
    with advisory_lock() as acquired:
        assert acquired, "parent lock acquisition failed"
        result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed Python code and interpreter, no shell.
            [sys.executable, "-c", command],
            check=True,
            capture_output=True,
            env=environment,
            text=True,
        )
    assert result.stdout.strip() == "False", "subprocess acquired held lock"
