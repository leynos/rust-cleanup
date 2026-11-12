"""Command-line entry point for rust-cleanup.

The parser, configuration loading, locking, and exit statuses live here so the
sweep engine stays importable without a command-line context. Rendering lives
here too: the sweep returns what it observed, and this module is the only place
that writes a human-readable line, so the library never prints.

This is also the composition root for telemetry. The run is wired with the
logging-backed emitter and one correlation token here, and both are passed into
the sweep, so the lock and invocation spans share the sweep's identifier.
"""

import argparse
import sys
import typing as typ
from pathlib import Path

from rust_cleanup.config import CleanupConfig, ConfigError
from rust_cleanup.config_loading import default_home, load_config
from rust_cleanup.diagnostics import CleanupOutcome, CleanupRun
from rust_cleanup.errors import CleanupError
from rust_cleanup.locking import advisory_lock
from rust_cleanup.observability import (
    EventEmitter,
    EventPort,
    configure_logging,
    new_correlation,
)
from rust_cleanup.roots import (
    RootSelection,
    ScanRootError,
    select_scan_roots,
    validate_explicit_roots,
)
from rust_cleanup.sweep import cleanup_scan_roots
from rust_cleanup.timing import Span

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from rust_cleanup.diagnostics import FilesystemDiagnostics

UNRESOLVED_PRESSURE_EXIT_STATUS = 3


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser including explicit policy overrides.

    Returns
    -------
    argparse.ArgumentParser
        The configured rust-cleanup command-line parser.
    """
    parser = argparse.ArgumentParser(
        description="Clean up stale Rust target directories using filesystem pressure.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        metavar="PATH",
        help="Root directories to search; omit to use the configured defaults",
    )
    parser.add_argument(
        "--dry-run", "-n", action="store_true", help="Report without deleting"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Print per-target detail"
    )
    parser.add_argument("--config", type=Path, help="TOML configuration path")
    parser.add_argument(
        "--normal-stale-after",
        help="Normal-mode inactivity cutoff, for example 2h",
    )
    parser.add_argument(
        "--pressure-enter-percent",
        type=int,
        help="Used-capacity percentage that enters pressure mode",
    )
    parser.add_argument(
        "--pressure-exit-percent",
        type=int,
        help="Used-capacity percentage that recovers from pressure mode",
    )
    parser.add_argument(
        "--pressure-stale-after",
        help="Pressure-mode inactivity cutoff, for example 10m",
    )
    parser.add_argument(
        "--critical-enter-percent",
        type=int,
        help="Used-capacity percentage that enters critical mode",
    )
    parser.add_argument(
        "--critical-exit-percent",
        type=int,
        help="Used-capacity percentage that recovers from critical mode",
    )
    parser.add_argument(
        "--critical-stale-after",
        help="Critical-mode inactivity cutoff, for example 2m",
    )
    return parser


def _cli_overrides(args: argparse.Namespace) -> dict[str, object]:
    """Return the explicit policy values parsed from the command line.

    Returns
    -------
    dict[str, object]
        The recognized command-line policy override values.
    """
    return {
        name: getattr(args, name)
        for name in (
            "normal_stale_after",
            "pressure_enter_percent",
            "pressure_exit_percent",
            "pressure_stale_after",
            "critical_enter_percent",
            "critical_exit_percent",
            "critical_stale_after",
        )
    }


def _load_cli_config(
    config_path: Path | None,
    cli_overrides: cabc.Mapping[str, object],
) -> CleanupConfig | None:
    """Load CLI configuration, reporting validation errors consistently.

    Resolving the default path can fail before any file is read: an unusable
    ``HOME`` with no account entry leaves no directory to place the file in.
    That is reported like any other configuration problem rather than escaping
    as a traceback, so the same status covers both halves of the lookup.

    Returns
    -------
    CleanupConfig | None
        The effective configuration, or ``None`` after reporting an error.
    """
    try:
        return load_config(config_path, cli_overrides)
    except ConfigError as error:
        print(f"Error: invalid rust-cleanup configuration: {error}", file=sys.stderr)
        return None
    except CleanupError as error:
        print(f"Error: {error}", file=sys.stderr)
        return None


def _summary_needed(run: CleanupRun, outcome: CleanupOutcome, *, verbose: bool) -> bool:
    """Return whether the sweep summary is worth printing.

    Returns
    -------
    bool
        Whether verbose, dry-run, or deletion output applies.
    """
    if verbose or run.dry_run:
        return True
    return outcome.deleted > 0


def _render_protected_targets(filesystem: FilesystemDiagnostics) -> None:
    """Name every preserved target, one per line."""
    for path in filesystem.protected_targets:
        print(f"  Keeping (protected): {path}")


def _render_planned_deletions(filesystem: FilesystemDiagnostics) -> None:
    """Report the selected mode, the planned count, and each planned candidate."""
    mode = filesystem.selected_mode.value
    planned = filesystem.planned_deletions
    print(f"  Would clean (mode={mode}): {len(planned)} candidate(s)")
    for path in planned:
        print(f"  Would delete (mode={mode}): {path}")


def _render_deletion_failures(filesystem: FilesystemDiagnostics) -> None:
    """Report every failed deletion on standard error."""
    for failure in filesystem.deletion_failures:
        print(f"Error deleting {failure.path}: {failure.message}", file=sys.stderr)


def _render_filesystem(
    filesystem: FilesystemDiagnostics, run: CleanupRun, *, verbose: bool
) -> None:
    """Render one filesystem's preserved and planned targets.

    Verbose mode names each protected target; dry-run mode reports the selected
    mode and every planned candidate whether or not verbose is set. Neither
    depends on the other, so the two blocks are rendered independently, and a
    failed deletion is reported whatever the mode.
    """
    if verbose:
        _render_protected_targets(filesystem)
    if run.dry_run:
        _render_planned_deletions(filesystem)
    _render_deletion_failures(filesystem)


def _render_outcome(run: CleanupRun, outcome: CleanupOutcome, *, verbose: bool) -> None:
    """Render everything the sweep observed and summarize the run.

    Successful deletions stay an aggregate count: a real run names each
    protected target under ``--verbose`` but never each deleted one. The
    omission notice is deliberately rendered outside the summary guard, because
    the case it explains - a quiet run that found nothing - is the case where
    that guard suppresses output.
    """
    if verbose:
        _render_omissions(outcome)
    for filesystem in outcome.filesystems:
        _render_filesystem(filesystem, run, verbose=verbose)
    if _summary_needed(run, outcome, verbose=verbose):
        action = "Would delete" if run.dry_run else "Deleted"
        print(f"{action} {outcome.deleted} of {outcome.scanned} target directories")
    _render_omission_summary(outcome, verbose=verbose)


def _render_omissions(outcome: CleanupOutcome) -> None:
    """Name each candidate discovery declined, with its fixed reason."""
    for omission in outcome.omissions:
        print(f"  Not a target ({omission.reason}): {omission.path}")


def _render_omission_summary(outcome: CleanupOutcome, *, verbose: bool) -> None:
    """Explain an empty scan when discovery declined what it found.

    An empty result has two very different meanings: the roots held no build
    output, or the scan refused every candidate it saw. Without this line an
    operator cannot tell a correct quiet run from a discovery rule that has
    stopped matching, which is exactly the failure that hid this issue.
    """
    if outcome.scanned or not outcome.omitted:
        return
    hint = "" if verbose else "; rerun with --verbose to list them"
    print(
        f"rust-cleanup: no target directories found in {len(outcome.roots)} "
        f"scan root(s); {outcome.omitted} candidate(s) were not treated as "
        f"build output{hint}"
    )


def _render_skipped_roots(selection: RootSelection, *, verbose: bool) -> None:
    """Report each root the selection declined, one per line, on stderr.

    A built-in default the host does not have is withheld unless ``--verbose``
    is set. The timer runs every five minutes, so reporting an absence the
    operator never chose would fill the journal with a warning about a
    directory that was simply never created. A configured root is the
    operator's own instruction, and an explicit one has already failed the
    invocation, so every other skip is reported on every run.
    """
    for skipped in selection.skipped:
        if skipped.is_absent_default and not verbose:
            continue
        print(
            f"rust-cleanup: skipping scan root {skipped.path} ({skipped.reason})",
            file=sys.stderr,
        )


def _run_cleanup(
    roots: tuple[Path, ...], run: CleanupRun, events: EventPort, *, verbose: bool
) -> int:
    """Run one locked multi-root cleanup sweep and report its outcome.

    Returns
    -------
    int
        The CLI status for lock contention, failure, or pressure outcome.
    """
    correlation = run.correlation
    try:
        with advisory_lock() as acquired:
            if not acquired:
                events.emit(
                    "lock", "finish", outcome="contended", correlation=correlation
                )
                print("rust-cleanup: another invocation already holds the lock")
                return 0
            events.emit("lock", "finish", outcome="acquired", correlation=correlation)
            outcome = cleanup_scan_roots(roots, run)
    except (CleanupError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    if outcome.discovery_failed:
        print(
            "Error: refusing to clean up; the directory scan was incomplete",
            file=sys.stderr,
        )
        return 1
    _render_outcome(run, outcome, verbose=verbose)
    return UNRESOLVED_PRESSURE_EXIT_STATUS if outcome.unresolved_pressure else 0


def _status_outcome(status: int) -> str:
    """Return the bounded telemetry outcome for one command status.

    Returns
    -------
    str
        ``unresolved`` for the pressure status, ``completed`` for success, and
        ``failed`` for every error status. Lock contention reports its own
        outcome through the ``lock`` span and completes normally.
    """
    if status == UNRESOLVED_PRESSURE_EXIT_STATUS:
        return "unresolved"
    return "completed" if status == 0 else "failed"


def _dispatch(args: argparse.Namespace, events: EventPort, correlation: str) -> int:
    """Resolve the effective roots, load configuration, and run one sweep.

    Explicit paths are validated before the lock is taken or any configuration
    is read, so an unusable argument fails the invocation without contending
    for the lock. Configuration errors keep their own status. Selection then
    applies the documented precedence: command line, then ``[scan] roots``,
    then the built-in defaults.

    Returns
    -------
    int
        The command's status for this invocation.
    """
    try:
        explicit = validate_explicit_roots(tuple(Path(path) for path in args.paths))
    except ScanRootError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    config = _load_cli_config(args.config, _cli_overrides(args))
    if config is None:
        return 2
    try:
        selection = select_scan_roots(
            explicit=explicit,
            configured=config.scan.roots,
            home=default_home,
        )
    except CleanupError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    _render_skipped_roots(selection, verbose=args.verbose)
    if selection.inspection_failed:
        # A root whose status could not be read leaves the scan incomplete;
        # treating it as merely absent would narrow the sweep without saying so.
        print(
            "Error: refusing to clean up; a scan root could not be inspected",
            file=sys.stderr,
        )
        return 1
    run = CleanupRun(
        config=config,
        dry_run=args.dry_run,
        events=events,
        correlation=correlation,
    )
    return _run_cleanup(selection.roots, run, events, verbose=args.verbose)


def main(argv: cabc.Sequence[str] | None = None) -> int:
    """Run the CLI, returning a distinct status for unresolved pressure.

    Parameters
    ----------
    argv : cabc.Sequence[str] | None, default=None
        Command-line arguments to parse. ``None`` reads :data:`sys.argv`.

    Returns
    -------
    int
        The command's success, failure, configuration, or pressure status.
    """
    args = build_parser().parse_args(argv)
    configure_logging()
    events: EventPort = EventEmitter()
    correlation = new_correlation()
    span = Span()
    events.emit("invocation", "start", correlation=correlation)
    status = _dispatch(args, events, correlation)
    events.emit(
        "invocation",
        "finish",
        outcome=_status_outcome(status),
        duration_ms=span.close(),
        correlation=correlation,
    )
    return status
