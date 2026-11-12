"""Pressure-aware orchestration of one cleanup sweep.

The sweep discovers candidates once, decides per backing filesystem, and only
then reclaims. Policy transitions stay pure in :mod:`rust_cleanup.pressure`;
the query half of a decision lives in :mod:`rust_cleanup.planning` and the
ordered deletion pass in :mod:`rust_cleanup.reclamation`, so what remains here
is sequencing and composition.

This module is the composition point for the library entry point: it wires the
concrete adapters, the telemetry emitter, and the per-run correlation token
whenever a run injects only some of them, and it renders the operator-facing
diagnostics line for each filesystem once reclamation is done. Nothing is
written to the console here, so what it observed travels back in
:class:`~rust_cleanup.diagnostics.CleanupOutcome` for the command line to
render.
"""

import typing as typ

from rust_cleanup import discovery, sweep_discovery
from rust_cleanup.cargo_locks import FileLockProbe
from rust_cleanup.diagnostics import (
    PROTECT_ACTIVE_BUILD,
    PROTECT_REVALIDATION_CHANGED,
    CleanupOutcome,
    CleanupRun,
    FilesystemDiagnostics,
)
from rust_cleanup.filesystem import (
    DirectoryDeleter,
    ResolvedTarget,
    delete_directory,
    filesystem_identity,
    usage_percent,
)
from rust_cleanup.observability import (
    EventEmitter,
    emit_diagnostics,
    new_correlation,
)
from rust_cleanup.planning import Planner
from rust_cleanup.plans import filesystem_sort_key
from rust_cleanup.reclamation import Reclaimer
from rust_cleanup.state import StateStore
from rust_cleanup.sweep_telemetry import (
    SweepTrace,
    abandon_sweep,
    active_build_factory,
    inspect_active_builds,
    load_state,
    log_warning,
    save_state,
    scan_every_root,
)
from rust_cleanup.timing import (
    DEFAULT_MONOTONIC,
    Monotonic,
    Span,
    wall_clock_seconds,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

    from rust_cleanup.observability import EventPort
    from rust_cleanup.pressure import FilesystemIdentity
    from rust_cleanup.reclaim import ActivityGetter, TargetCandidate


def cleanup_scan_roots(
    roots: cabc.Sequence[Path], run: CleanupRun | None = None
) -> CleanupOutcome:
    """Apply one pressure-aware sweep across every selected scan root.

    The roots are scanned together and their candidates aggregated, so one
    invocation takes one lock and performs one planning and reclamation pass.
    A scan root that is missing is handled by selection rather than here; an
    empty sequence is a valid request to scan nothing.

    Parameters
    ----------
    roots : collections.abc.Sequence[pathlib.Path]
        Effective scan roots, already validated and free of nesting.

    run : CleanupRun | None, default=None
        Sweep options and injected adapters. ``None`` uses a default run.

    Returns
    -------
    CleanupOutcome
        Aggregate counts, mode decisions, per-target records, and
        unresolved-pressure status.
    """
    sweep = _CleanupSweep(roots, run or CleanupRun())
    return sweep.execute()


class _CleanupSweep:
    """Orchestrate one discovery, decision, and reclamation pass."""

    def __init__(self, roots: cabc.Sequence[Path], run: CleanupRun) -> None:
        """Bind the effective roots and adopt the ports of one sweep."""
        self.roots = tuple(roots)
        self.run = run
        self._adopt_adapters(run)
        self._adopt_run_ports(run)
        self.trace = SweepTrace(
            self.events, self.correlation, self.monotonic, run.dry_run
        )
        self.detector = inspect_active_builds(run, self.trace)
        load_state(self.store, self.trace)
        self._build_ports(run)

    def _adopt_adapters(self, run: CleanupRun) -> None:
        """Adopt the filesystem adapters ``run`` injects, defaulting the rest."""
        self.usage_getter = run.usage_getter or usage_percent
        self.identity_getter = run.identity_getter or filesystem_identity
        self.activity_getter: ActivityGetter = (
            run.activity_getter or discovery.newest_mtime
        )
        self.delete_directory: DirectoryDeleter = (
            run.delete_directory or delete_directory
        )

    def _adopt_run_ports(self, run: CleanupRun) -> None:
        """Adopt the clock, telemetry, and state ports ``run`` injects."""
        self.now = wall_clock_seconds() if run.now is None else run.now
        self.monotonic: Monotonic = run.monotonic or DEFAULT_MONOTONIC
        self.events: EventPort = run.events or EventEmitter()
        self.correlation = run.correlation or new_correlation()
        self.store = run.state_store or StateStore()

    def _build_ports(self, run: CleanupRun) -> None:
        """Build the planner and reclaimer over the ports this sweep adopted.

        Planning asks the opening build snapshot and a Cargo lock probe, while
        reclamation builds its detector afresh before each deletion. The two
        passes therefore agree on what they consult and on the order they
        consult it, but only reclamation can see a build that started after the
        sweep began.
        """
        self.lock_probe = FileLockProbe()
        self.planner = Planner(
            config=run.config,
            store=self.store,
            usage_getter=self.usage_getter,
            activity_getter=self.activity_getter,
            detector=self.detector,
            lock_probe=self.lock_probe,
            now=self.now,
            warn=log_warning,
        )
        self.reclaimer = Reclaimer(
            dry_run=run.dry_run,
            store=self.store,
            usage_getter=self.usage_getter,
            activity_getter=self.activity_getter,
            delete_directory=self.delete_directory,
            detector_factory=active_build_factory(run),
            lock_probe=self.lock_probe,
            roots=tuple(root.resolve(strict=False) for root in self.roots),
            events=self.events,
            correlation=self.correlation,
            monotonic=self.monotonic,
            warn=log_warning,
        )

    def execute(self) -> CleanupOutcome:
        """Discover all candidates and process each backing filesystem.

        An incomplete scan ends the sweep before anything is deleted or
        persisted: a target list the sweep cannot trust must not drive
        destructive or durable steps.

        Returns
        -------
        CleanupOutcome
            Aggregate counts, diagnostics, and unresolved-pressure status.
        """
        span = Span(self.monotonic)
        self.events.emit(
            "sweep", "start", dry_run=self.run.dry_run, correlation=self.correlation
        )
        scan = scan_every_root(self.roots, self.trace)
        if not scan.complete:
            return abandon_sweep(self.roots, scan, self.trace, span)
        targets, unidentified, escaped = self._resolve_targets(scan.targets)
        grouped = self.planner.snapshot(targets).grouped
        diagnostics = [
            self._process_filesystem(identity, grouped[identity])
            for identity in sorted(grouped, key=filesystem_sort_key)
        ]
        if not self.run.dry_run:
            save_state(self.store, self.trace)
        outcome = CleanupOutcome(
            scanned=len(scan.targets),
            deleted=sum(item.deleted for item in diagnostics),
            unresolved_pressure=any(not item.exit_reached for item in diagnostics),
            filesystems=tuple(diagnostics),
            roots=self.roots,
            omissions=(*scan.omissions, *escaped),
        )
        self.events.emit(
            "sweep",
            "finish",
            outcome="unresolved" if outcome.unresolved_pressure else "completed",
            dry_run=self.run.dry_run,
            duration_ms=span.close(),
            scanned=outcome.scanned,
            deleted=outcome.deleted,
            filesystems=len(diagnostics),
            unidentified=unidentified,
            omitted=outcome.omitted,
            roots=len(self.roots),
            correlation=self.correlation,
        )
        return outcome

    def _resolve_targets(
        self, targets: cabc.Iterable[Path]
    ) -> tuple[list[ResolvedTarget], int, list[discovery.Omission]]:
        """Identify each in-root target, preserving the ones that fail.

        A target that resolves outside every effective root is returned as an
        omission instead of an error: the roots, not the walk, define what may
        be deleted, and an operator needs to see that something was excluded.

        Returns
        -------
        tuple[list[ResolvedTarget], int, list[discovery.Omission]]
            The identified targets, how many were preserved for want of an
            identity, and any target that escaped the effective roots.
        """
        kept, escaped = sweep_discovery.confine_targets(targets, self.roots)
        resolved, unidentified = sweep_discovery.resolve_targets(
            kept, self.identity_getter
        )
        if unidentified:
            log_warning("Warning: preserving a target; cannot identify its filesystem")
        return resolved, unidentified, escaped

    def _process_filesystem(
        self, identity: FilesystemIdentity, candidates: list[TargetCandidate]
    ) -> FilesystemDiagnostics:
        """Select policy, reclaim candidates, then persist the chosen mode.

        Returns
        -------
        FilesystemDiagnostics
            The completed per-filesystem sweep diagnostics.
        """
        span = Span(self.monotonic)
        plan = self.planner.plan(identity, candidates)
        mode = plan.diagnostics.selected_mode.value
        self.events.emit(
            "filesystem_decision",
            "start",
            mode=mode,
            dry_run=self.run.dry_run,
            correlation=self.correlation,
        )
        self.reclaimer.reclaim(plan)
        self.reclaimer.complete(plan)
        emit_diagnostics(plan.diagnostics, dry_run=self.run.dry_run)
        diagnostics = plan.diagnostics
        self.events.emit(
            "filesystem_decision",
            "finish",
            outcome="failed" if plan.usage_failed else "completed",
            mode=mode,
            scanned=diagnostics.scanned,
            protected=diagnostics.protected,
            eligible=diagnostics.eligible,
            deleted=diagnostics.deleted,
            failed=diagnostics.failed,
            unknown_activity=diagnostics.unknown_activity,
            protected_active_build=diagnostics.protection_reasons.get(
                PROTECT_ACTIVE_BUILD, 0
            ),
            protected_changed=diagnostics.protection_reasons.get(
                PROTECT_REVALIDATION_CHANGED, 0
            ),
            duration_ms=span.close(),
            correlation=self.correlation,
        )
        return diagnostics
