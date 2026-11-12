"""Cover the opt-in gate, teardown edge cases and live timer behaviour.

The opt-in contract proves collection stays host-free without
``RUST_CLEANUP_HOST_ACCEPTANCE=1``. The ``systemd_user`` acceptance case installs
uniquely named, harmless units and checks repeated scheduling and rearming.
"""

import subprocess  # ruff: ignore[suspicious-subprocess-import] - only constructs CompletedProcess test results.
import typing as typ
import unittest.mock as mock  # ruff: ignore[manual-from-import] - module alias required by .rules/python-typing.md.

import pytest
import timer_harness
from timer_harness import (
    TimerHarness,
    run_systemctl,
    user_manager_available,
    wait_for,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

SYSTEMD_TIMEOUT_REASON = "needs an opted-in running systemd user manager"


def test_manager_probe_requires_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return unavailable without spawning systemctl when the gate is unset."""
    monkeypatch.delenv("RUST_CLEANUP_HOST_ACCEPTANCE", raising=False)
    spawn = mock.Mock(side_effect=AssertionError("must not contact host manager"))
    monkeypatch.setattr(timer_harness.subprocess, "run", spawn)
    assert not user_manager_available(), "manager readiness requires explicit opt-in"
    spawn.assert_not_called()


@pytest.fixture
def timer_fixture(
    tmp_path: Path,
) -> cabc.Iterator[TimerHarness]:
    """Install harmless units and guarantee teardown if setup or assertions fail.

    Yields
    ------
    TimerHarness
        Installed test-owned units with guaranteed teardown.
    """
    fixture = TimerHarness(tmp_path)
    try:
        fixture.install()
        result = run_systemctl("daemon-reload")
        _assert_systemctl_success(result, "daemon-reload")
        yield fixture
    finally:
        fixture.teardown()


@pytest.mark.systemd_user
@pytest.mark.skipif(not user_manager_available(), reason=SYSTEMD_TIMEOUT_REASON)
def test_timer_activation_and_restart_keep_repeated_deadlines(
    timer_fixture: TimerHarness,
) -> None:
    """Rearm the rendered timer after manager startup and timer restart."""
    fixture = timer_fixture
    service_timestamp = run_systemctl(
        "show",
        "--property=ActiveEnterTimestampMonotonic",
        "--value",
        fixture.service_name,
    )
    _assert_systemctl_success(service_timestamp, "read service activation time")
    assert service_timestamp.stdout.strip() == "0", (
        "the fixture service must have no activation before the timer starts"
    )

    started = run_systemctl("start", fixture.timer_name)
    _assert_systemctl_success(started, "start timer")
    first_state = _timer_state(fixture.timer_name)
    _assert_timer_waiting(first_state)
    assert "OnActiveUSec=2s" in first_state["TimersMonotonic"], (
        "the first deadline must use the rendered activation-relative interval"
    )

    assert wait_for(lambda: fixture.run_count() >= 2, timeout=12), (
        "timer did not invoke the harmless service twice"
    )
    assert wait_for(
        lambda: _timer_state(fixture.timer_name)["SubState"] == "waiting",
        timeout=5,
    ), "timer did not return to waiting after the service runs"
    repeated_state = _timer_state(fixture.timer_name)
    _assert_timer_waiting(repeated_state)
    assert "OnActiveUSec=2s" in repeated_state["TimersMonotonic"], (
        "the timer activation deadline must use the rendered 2s interval"
    )
    assert "OnUnitActiveUSec=2s" in repeated_state["TimersMonotonic"], (
        "the recurring deadline must use the rendered 2s interval"
    )
    runs_before_restart = fixture.run_count()

    restarted = run_systemctl("restart", fixture.timer_name)
    _assert_systemctl_success(restarted, "restart timer")
    restarted_state = _timer_state(fixture.timer_name)
    _assert_timer_waiting(restarted_state)

    assert wait_for(
        lambda: fixture.run_count() >= runs_before_restart + 2, timeout=12
    ), "timer did not invoke the harmless service twice after restart"


def _timer_state(unit: str) -> dict[str, str]:
    """Read the timer properties that describe its monotonic schedule.

    Returns
    -------
    dict[str, str]
        The timer schedule and state properties reported by systemd.
    """
    result = run_systemctl(
        "show",
        "--property=NextElapseUSecMonotonic",
        "--property=TimersMonotonic",
        "--property=SubState",
        unit,
    )
    _assert_systemctl_success(result, "read timer state")
    properties = dict(
        line.split("=", maxsplit=1)
        for line in result.stdout.splitlines()
        if "=" in line
    )
    properties["TimersMonotonic"] = "\n".join(
        line.partition("=")[2]
        for line in result.stdout.splitlines()
        if line.startswith("TimersMonotonic=")
    )
    return properties


def _assert_timer_waiting(properties: dict[str, str]) -> None:
    """Require a finite monotonic trigger and the waiting timer state."""
    deadline = properties.get("NextElapseUSecMonotonic", "").strip()
    assert deadline, "the timer must report its next monotonic deadline"
    assert deadline.lower() not in {"0", "infinity"}, (
        "the timer must have a finite non-zero next deadline"
    )
    assert properties["SubState"] == "waiting", (
        "the timer must remain waiting for its next deadline"
    )


def _assert_systemctl_success(
    result: subprocess.CompletedProcess[str], action: str
) -> None:
    """Raise a useful assertion when a fixture manager command fails."""
    assert result.returncode == 0, (
        f"systemctl --user {action} failed: {result.stderr.strip()}"
    )


@pytest.mark.parametrize("action", ["stop", "reset-failed"])
def test_teardown_accepts_unloaded_fixture_unit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    action: str,
) -> None:
    """Partially installed fixtures may leave units unknown to the manager."""
    result = subprocess.CompletedProcess(
        args=["systemctl", "--user", action, "fixture.timer"],
        returncode=1,
        stdout="",
        stderr=f"Failed to {action} fixture.timer: Unit fixture.timer not loaded.",
    )
    monkeypatch.setattr(timer_harness, "run_systemctl", lambda *_args: result)
    failures: list[str] = []

    TimerHarness(tmp_path)._attempt_systemctl(
        failures, f"{action} timer", action, "fixture.timer"
    )

    assert not failures, "an unloaded fixture unit needs no further cleanup"
