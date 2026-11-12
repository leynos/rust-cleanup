"""The clock ports and the span durations measured through them.

This is the only module in the package that reads a clock. The monotonic port
is injected into every component that opens a telemetry span, and the wall
clock reading the inactivity cutoff is derived from has its default here too,
so the modules that consume a reading stay free of the ``time`` module and a
test can replace either clock without reaching into the domain. Nothing here
reads the environment, the filesystem, or the logging system, which is what
lets the domain modules that report durations import it without taking an
infrastructure dependency.
"""

import collections.abc as cabc
import time
import typing as typ

# Durations are measured from the monotonic clock so that they report elapsed
# time rather than movement of the calendar. The wall clock is a separate port:
# it supplies the one reading a sweep derives its inactivity cutoff from, and a
# sweep takes that reading once so that every candidate is judged against the
# same instant.
type Monotonic = cabc.Callable[[], float]
DEFAULT_MONOTONIC: typ.Final[Monotonic] = time.monotonic


def wall_clock_seconds() -> float:
    """Return the current epoch second from the system wall clock.

    The sweep calls this once when a run injects no reading of its own, which
    keeps every clock read in the package behind one module while leaving the
    reading itself injectable through the run.

    Returns
    -------
    float
        Seconds since the Unix epoch, as the system wall clock reports them.
    """
    return time.time()


def elapsed_ms(started: float, monotonic: Monotonic = DEFAULT_MONOTONIC) -> int:
    """Return whole milliseconds elapsed since a monotonic ``started`` reading.

    Parameters
    ----------
    started : float
        A monotonic reading taken before the span began.
    monotonic : Monotonic, default=time.monotonic
        Clock the closing reading is taken from, so a caller that injects a
        clock measures the span with the same clock that opened it.

    Returns
    -------
    int
        Duration in whole milliseconds, which the telemetry vocabulary bounds
        to a non-negative counter like every other numeric field.
    """
    return round((monotonic() - started) * 1000)


class Span:
    """A monotonic stopwatch that reports whole milliseconds when closed.

    A span reads its clock once when it opens and once when it closes, so a
    component that opens several spans never has to pair a reading with the
    clock that produced it.
    """

    __slots__ = ("_monotonic", "_started")

    def __init__(self, monotonic: Monotonic = DEFAULT_MONOTONIC) -> None:
        """Open a span, reading the clock once.

        Parameters
        ----------
        monotonic : Monotonic, default=time.monotonic
            Clock both readings are taken from.
        """
        self._monotonic = monotonic
        self._started = monotonic()

    def close(self) -> int:
        """Return whole milliseconds elapsed since the span opened.

        Returns
        -------
        int
            Duration in whole milliseconds, bounded like every other numeric
            telemetry field.
        """
        return elapsed_ms(self._started, self._monotonic)
