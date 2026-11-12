"""Contracts for the injected clocks and the spans measured through them.

Telemetry durations come from the monotonic port, and the instant a sweep
derives its cutoff from is a value the caller supplies, so neither is read
from the process. These tests drive both from a scripted clock: a duration is
then a fact about the clock the component was given rather than about the host
it happened to run on.
"""

import time
import typing as typ

from conftest import (
    InactiveBuilds,
    RecordingSink,
    build_sink_run,
    make_target,
    synthetic_identity,
)
from proc_support import UnheldLocks

from rust_cleanup import cleanup_scan_roots
from rust_cleanup.diagnostics import FilesystemDiagnostics
from rust_cleanup.observability import EventEmitter
from rust_cleanup.plans import FilesystemPlan
from rust_cleanup.pressure import Mode
from rust_cleanup.reclamation import Reclaimer
from rust_cleanup.state import StateStore
from rust_cleanup.timing import DEFAULT_MONOTONIC, Span, wall_clock_seconds

if typ.TYPE_CHECKING:
    from pathlib import Path

# Every reading a scripted clock returns is a quarter of a second after the
# last, so a duration measured with one is an exact multiple of this step and
# can never be confused with a reading the host clock produced.
_STEP_MS = 250
_STEP_SECONDS = _STEP_MS / 1000
_CORRELATION = "run_timing_test"


class ScriptedClock:
    """A monotonic clock that advances a fixed amount on every reading.

    Attributes
    ----------
    reads : int
        Number of readings taken so far.
    """

    __slots__ = ("_next", "_step", "reads")

    def __init__(self, step: float) -> None:
        """Start at zero, advancing ``step`` seconds per reading.

        Parameters
        ----------
        step : float
            Seconds every reading follows the previous one by.
        """
        self._step = step
        self._next = 0.0
        self.reads = 0

    def __call__(self) -> float:
        """Return the current reading and advance the clock.

        Returns
        -------
        float
            The reading for this call.
        """
        self.reads += 1
        reading = self._next
        self._next += self._step
        return reading


def _sweep_durations(root: Path) -> list[float]:
    """Return every duration one scripted sweep reported, in record order.

    Parameters
    ----------
    root : Path
        Directory created to hold the sweep's candidate and state.

    Returns
    -------
    list[float]
        Whole-millisecond durations, as the telemetry records carried them.
    """
    root.mkdir()
    make_target(root, "stale", 1)
    sink = RecordingSink()
    cleanup_scan_roots(
        (root,),
        build_sink_run(
            sink,
            state_store=StateStore(root / "state.json"),
            active_build_detector=InactiveBuilds(),
            usage_getter=lambda _path: 1.0,
            identity_getter=lambda _path: synthetic_identity(root),
            now=10000,
            monotonic=ScriptedClock(_STEP_SECONDS),
        ),
    )
    durations: list[float] = []
    for record in sink.records:
        duration = record.get("duration_ms")
        if duration is None:
            continue
        assert isinstance(duration, int | float), (
            f"a telemetry record carried an unbounded duration: {record!r}"
        )
        assert not isinstance(duration, bool), (
            f"a telemetry record carried an unbounded duration: {record!r}"
        )
        durations.append(duration)
    return durations


def test_span_dates_itself_from_the_injected_clock() -> None:
    """A span reports exactly the delta the clock it was given dictates."""
    clock = ScriptedClock(_STEP_SECONDS)

    span = Span(clock)

    assert clock.reads == 1, "opening a span did not take one reading"
    assert span.close() == _STEP_MS, "the span reported a duration its clock never gave"
    assert clock.reads == 2, "closing a span did not take one more reading"


def test_span_rounds_a_partial_millisecond_to_the_nearest_whole_one() -> None:
    """A sub-millisecond delta rounds, so a short span is never lost to zero."""
    readings = iter((10.0, 10.0006))

    span = Span(lambda: next(readings))

    assert span.close() == 1, "a partial millisecond was truncated instead of rounded"


def test_the_default_monotonic_port_is_the_process_clock() -> None:
    """The default port is the monotonic clock, which cannot run backwards."""
    assert DEFAULT_MONOTONIC is time.monotonic, (
        "durations would be measured from a clock that can move backwards"
    )


def test_wall_clock_seconds_reads_an_epoch_second() -> None:
    """The wall clock default reports the epoch second a cutoff is derived from."""
    before = time.time()

    reading = wall_clock_seconds()

    assert before <= reading <= time.time(), (
        "the wall clock reading is not an epoch second between two known readings"
    )


def test_a_scripted_sweep_dates_every_span_from_that_clock(tmp_path: Path) -> None:
    """Two identically scripted sweeps report identical durations.

    A duration taken from the host clock differs between two runs; a duration
    taken from the injected clock is fixed by it. Comparing the two runs
    therefore pins every duration to the scripted clock, and the step check
    keeps a host reading from passing as one of them.
    """
    first = _sweep_durations(tmp_path / "first")
    second = _sweep_durations(tmp_path / "second")

    assert first, "the sweep reported no durations at all"
    assert first == second, "the same scripted clock produced different durations"
    off_grid = [duration for duration in first if duration % _STEP_MS]
    assert not off_grid, f"durations came from outside the injected clock: {off_grid!r}"
    assert all(duration > 0 for duration in first), (
        "a span that opened and closed reported no elapsed time"
    )


def test_reclaimer_measures_its_deletion_span_with_the_injected_clock(
    tmp_path: Path,
) -> None:
    """A reclaimer built from its required ports alone dates its own span.

    The reclaimer is constructed with the collaborators it needs and nothing
    else - no sweep, no run options, no command line - so the duration it
    reports is evidence that the timing port reaches the component that emits
    the record rather than being applied to the record afterwards.
    """
    sink = RecordingSink()
    reclaimer = Reclaimer(
        dry_run=False,
        store=StateStore(tmp_path / "state.json"),
        usage_getter=lambda _path: 1.0,
        activity_getter=lambda _path: None,
        delete_directory=lambda _path: True,
        detector_factory=InactiveBuilds,
        lock_probe=UnheldLocks(),
        roots=(tmp_path.resolve(),),
        events=EventEmitter(sink=sink),
        correlation=_CORRELATION,
        monotonic=ScriptedClock(_STEP_SECONDS),
        warn=lambda _message: None,
    )
    plan = FilesystemPlan(
        diagnostics=FilesystemDiagnostics(
            identity=synthetic_identity(tmp_path),
            usage_before=85.0,
            usage_after=85.0,
            previous_mode=None,
            selected_mode=Mode.PRESSURE,
            cutoff_seconds=0,
            scanned=0,
        ),
        candidates=[],
        cutoff_time=0.0,
        watermark=None,
    )

    reclaimer.reclaim(plan)

    finish = sink.for_operation("deletion")[-1]
    assert finish["duration_ms"] == _STEP_MS, (
        f"the deletion span was not measured with the injected clock: {finish!r}"
    )
