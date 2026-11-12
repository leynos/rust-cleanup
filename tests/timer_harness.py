"""Support isolated, opt-in rust-cleanup timer acceptance tests.

The helpers keep ordinary pytest runs host-free unless
``RUST_CLEANUP_HOST_ACCEPTANCE=1`` and a running user manager are both
available. ``TimerHarness`` renders the shipped timer template for a harmless,
uniquely named service, installs test-owned units, and provides bounded
commands and teardown against that manager.
"""

import os
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import] - runs fixed systemctl argv without a shell.
import sys
import time
import typing as typ
import uuid
from pathlib import Path

import pytest
from jinja2 import Environment, StrictUndefined, select_autoescape

if typ.TYPE_CHECKING:
    import collections.abc as cabc


SYSTEMCTL_TIMEOUT_SECONDS = 15
RUNTIME_DIRECTORY = Path(f"/run/user/{os.getuid()}")
USER_UNIT_DIRECTORY = RUNTIME_DIRECTORY / "systemd" / "user"
TIMER_TEMPLATE = Path(__file__).parent / "fixtures" / "rust-cleanup.timer.j2"


def _manager_environment() -> dict[str, str]:
    """Build manager routing for this command without changing the parent.

    Returns
    -------
    dict[str, str]
        A copied environment with explicit user-bus routing.
    """
    environment = os.environ.copy()
    environment["XDG_RUNTIME_DIR"] = str(RUNTIME_DIRECTORY)
    environment["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={RUNTIME_DIRECTORY}/bus"
    return environment


def user_manager_available() -> bool:
    """Report whether explicitly opted-in user-manager acceptance can run.

    Returns
    -------
    bool
        Whether the host acceptance gate is enabled and the user manager is
        running or degraded.
    """
    if os.environ.get("RUST_CLEANUP_HOST_ACCEPTANCE") != "1":
        return False
    systemctl_path = shutil.which("systemctl")
    if systemctl_path is None:
        return False
    try:
        result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed systemctl query, no shell.
            [systemctl_path, "--user", "is-system-running"],
            capture_output=True,
            check=False,
            env=_manager_environment(),
            text=True,
            timeout=SYSTEMCTL_TIMEOUT_SECONDS,
        )
    except OSError, subprocess.TimeoutExpired:
        return False
    return result.stdout.strip() in {"running", "degraded"}


def run_systemctl(*arguments: str) -> subprocess.CompletedProcess[str]:
    """Run one bounded command against the current user's systemd manager.

    Parameters
    ----------
    *arguments : str
        Arguments following ``systemctl --user``.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The captured command result. Callers decide whether a non-zero status
        is expected for their assertion.

    Raises
    ------
    FileNotFoundError
        If ``systemctl`` is not available on ``PATH``.
    """
    systemctl_path = shutil.which("systemctl")
    if systemctl_path is None:
        raise FileNotFoundError
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - arguments are fixed test actions and generated fixture unit names.
        [systemctl_path, "--user", *arguments],
        capture_output=True,
        check=False,
        env=_manager_environment(),
        text=True,
        timeout=SYSTEMCTL_TIMEOUT_SECONDS,
    )


def wait_for(condition: cabc.Callable[[], bool], timeout: float = 15.0) -> bool:
    """Poll a condition to a monotonic deadline, then make one final check.

    Parameters
    ----------
    condition : Callable[[], bool]
        The condition to check. Any systemctl call it makes must set a timeout.
    timeout : float, default 15.0
        Maximum time to wait, in seconds.

    Returns
    -------
    bool
        Whether the condition held by the deadline or on the final check.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return condition()


class TimerHarness:
    """Own a harmless service and a rendered timer under the user runtime.

    Parameters
    ----------
    work_directory : Path
        Pytest-owned temporary directory for the service script and run log.
    """

    def __init__(self, work_directory: Path) -> None:
        """Assign unique unit names and paths without creating host files.

        Parameters
        ----------
        work_directory : Path
            Pytest-owned temporary directory for the service script and run log.
        """
        identifier = uuid.uuid4().hex[:12]
        stem = f"rust-cleanup-timer-test-{identifier}"
        self.service_name = f"{stem}.service"
        self.timer_name = f"{stem}.timer"
        self.work_directory = work_directory
        self.run_log = work_directory / "runs.log"
        self.service_script = work_directory / "append_monotonic_time.py"
        self.timer_path = USER_UNIT_DIRECTORY / self.timer_name
        self.service_path = USER_UNIT_DIRECTORY / self.service_name
        self.dropin_directory = USER_UNIT_DIRECTORY / f"{self.timer_name}.d"
        self.dropin_path = self.dropin_directory / "accuracy.conf"
        self.owned_files: list[Path] = []
        self.owns_dropin_directory = False

    def install(self) -> None:
        """Write uniquely named service, timer and accuracy drop-in files."""
        self.work_directory.mkdir(parents=True, exist_ok=True)
        USER_UNIT_DIRECTORY.mkdir(parents=True, exist_ok=True)
        self.service_script.write_text(
            "from time import monotonic_ns\n"
            f"with open({str(self.run_log)!r}, 'a', encoding='ascii') as runs:\n"
            "    runs.write(f'{monotonic_ns()}\\n')\n",
            encoding="utf-8",
        )

        exec_start = (
            f"{_quote_systemd_argument(sys.executable)} "
            f"{_quote_systemd_argument(str(self.service_script))}"
        )
        service = "\n".join((
            "[Unit]",
            "Description=Harmless rust-cleanup timer acceptance fixture",
            "",
            "[Service]",
            "Type=oneshot",
            f"ExecStart={exec_start}",
            "",
        ))
        self._write_exclusive(self.service_path, service)

        source = TIMER_TEMPLATE.read_text(encoding="utf-8")
        rendered = (
            Environment(
                autoescape=select_autoescape(default_for_string=False),
                undefined=StrictUndefined,
            )
            .from_string(source)
            .render(rust_cleanup_timer_interval="2s")
        )
        if "{{" in rendered or "{%" in rendered:
            pytest.fail("timer template contains unresolved Jinja syntax")
        lines = rendered.splitlines()
        unit_directives = [
            line for line in lines if line == "Unit=rust-cleanup.service"
        ]
        if len(unit_directives) != 1:
            pytest.fail(
                "rendered timer must contain exactly one rust-cleanup service target"
            )
        rendered = rendered.replace(
            "Unit=rust-cleanup.service", f"Unit={self.service_name}"
        )
        self._write_exclusive(self.timer_path, rendered)

        self.dropin_directory.mkdir(mode=0o700)
        self.owns_dropin_directory = True
        self._write_exclusive(self.dropin_path, "[Timer]\nAccuracySec=100ms\n")

    def run_count(self) -> int:
        """Return the number of monotonic timestamps written by the fixture.

        Returns
        -------
        int
            Number of complete timestamp lines in the test-owned work file.
        """
        try:
            return len(self.run_log.read_text(encoding="ascii").splitlines())
        except FileNotFoundError:
            return 0

    def teardown(self) -> None:
        """Attempt every unit and file cleanup action, then report failures."""
        failures: list[str] = []
        self._stop_and_reset_units(failures)
        self._remove_owned_files(failures)
        self._attempt_systemctl(failures, "reload user manager", "daemon-reload")
        self._raise_teardown_failures(failures)

    def _stop_and_reset_units(self, failures: list[str]) -> None:
        """Stop and reset both test-owned units, collecting every failure."""
        actions = (
            ("stop timer", ("stop", self.timer_name)),
            ("stop service", ("stop", self.service_name)),
            ("reset timer failure", ("reset-failed", self.timer_name)),
            ("reset service failure", ("reset-failed", self.service_name)),
        )
        for description, arguments in actions:
            self._attempt_systemctl(failures, description, *arguments)

    def _remove_owned_files(self, failures: list[str]) -> None:
        """Remove only files and directories created by this harness."""
        for path in reversed(self.owned_files):
            self._remove_file(failures, path)
        if self.owns_dropin_directory:
            self._remove_dropin_directory(failures)

    @staticmethod
    def _remove_file(failures: list[str], path: Path) -> None:
        """Remove one owned file while keeping later cleanup actions runnable."""
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            failures.append(f"remove {path}: {error}")

    def _remove_dropin_directory(self, failures: list[str]) -> None:
        """Remove the harness-owned accuracy drop-in directory."""
        try:
            self.dropin_directory.rmdir()
        except OSError as error:
            failures.append(f"remove {self.dropin_directory}: {error}")

    @staticmethod
    def _raise_teardown_failures(failures: list[str]) -> None:
        """Report collected cleanup failures after every action was attempted.

        Raises
        ------
        RuntimeError
            If at least one cleanup action failed.
        """
        if failures:
            raise RuntimeError("timer fixture teardown failed: " + "; ".join(failures))

    def _write_exclusive(self, path: Path, content: str) -> None:
        """Create and track one fixture file without replacing existing content."""
        with path.open("x", encoding="utf-8") as stream:
            self.owned_files.append(path)
            stream.write(content)

    @staticmethod
    def _attempt_systemctl(
        failures: list[str], description: str, *arguments: str
    ) -> None:
        """Run one bounded teardown command and collect rather than stop on errors."""
        try:
            result = run_systemctl(*arguments)
        except (OSError, subprocess.SubprocessError) as error:
            failures.append(f"{description}: {error}")
            return
        if result.returncode != 0:
            detail = result.stderr.strip() or f"exit status {result.returncode}"
            if arguments[0] in {"stop", "reset-failed"} and detail.endswith(
                f"Unit {arguments[-1]} not loaded."
            ):
                # Partial setup can leave a fixture unit unknown to the manager.
                return
            failures.append(f"{description}: {detail}")


def _quote_systemd_argument(argument: str) -> str:
    """Quote one ExecStart token using systemd's command-line syntax.

    Returns
    -------
    str
        A systemd-escaped token surrounded by double quotes.
    """
    escaped = argument.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
