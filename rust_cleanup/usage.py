"""Measure filesystem usage through the injected adapter port.

The sweep reads a filesystem's used capacity once before it plans and again
after each deletion. Both readings live here because they share one contract:
an unmeasurable filesystem is reported as ``None``, the bounded warning travels
to the sink the caller supplied rather than to a logger, and nothing here
decides that a warning is warranted. :mod:`rust_cleanup.diagnostics` still owns
what an unmeasurable reading *means* for a plan. Every annotation that names a
port is resolved statically, so this module imports no infrastructure at run
time.
"""

import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

    from rust_cleanup.filesystem import UsageGetter
    from rust_cleanup.plans import FilesystemPlan


def measure_usage(
    getter: UsageGetter,
    mount_point: Path,
    context: str,
    *,
    warn: cabc.Callable[[str], None],
) -> float | None:
    """Measure used capacity, reporting an unmeasurable filesystem as ``None``.

    The usage adapter's contract reports an unmeasurable filesystem by raising
    ``OSError``. Every caller treats ``None`` as unknown: it preserves
    candidates, stops reclamation, and leaves the recovery watermark unreached.

    The failure travels to the caller's ``warn`` sink rather than the logger,
    so this query neither writes a record nor decides that one is warranted.
    The warning stays bounded: it names only what the caller was doing, never
    the filesystem path or the exception text, so an unmeasurable mount cannot
    disclose either through the standard-error logging sink.

    Parameters
    ----------
    getter : UsageGetter
        Adapter that measures the used capacity of a filesystem.
    mount_point : Path
        Filesystem whose used capacity is measured.
    context : str
        Bounded description of what the caller does with the value.
    warn : collections.abc.Callable[[str], None]
        Sink for the bounded unmeasurable-filesystem warning.

    Returns
    -------
    float | None
        The used-capacity percentage, or ``None`` when unmeasurable.
    """
    try:
        return getter(mount_point)
    except OSError:
        warn(f"Warning: {context}; filesystem usage is unavailable")
        return None


def refresh_usage(
    plan: FilesystemPlan,
    getter: UsageGetter,
    *,
    warn: cabc.Callable[[str], None],
) -> None:
    """Re-read usage after a deletion, marking it unmeasurable on failure."""
    if plan.watermark is None:
        return
    usage = measure_usage(
        getter,
        plan.diagnostics.identity.mount_point,
        "stopping reclamation",
        warn=warn,
    )
    if usage is None:
        plan.usage_failed = True
        return
    plan.diagnostics.usage_after = usage
