"""Ordered reclamation of one planned filesystem sweep.

Planning decides what may be reclaimed; this module carries that decision out.
It deletes in the planned order, re-checks every candidate immediately before
removal, stops as soon as the recovery watermark is met, and persists the
selected mode only for a sweep that completed with measurable usage.
Everything it does is reported through the injected telemetry port, and it
renders nothing itself: what it observed travels back in the plan it was
handed.

The immediate re-check is ordered, and the order is the contract. A fresh build
detector answers first, because a build that started after discovery is the
race the check exists to close. The Cargo lock probe follows, since a held lock
is the same fact observed a different way. Only then is the activity snapshot
reconfirmed, then the evidence discovery accepted, and finally the scope: a
candidate that has become a symbolic link, or whose canonical path has left
every effective root, is not the directory that was planned. Each failed check
records its own fixed reason and nothing is deleted.

A failed usage re-read leaves the plan unmeasurable, which stops the deletion
loop, keeps the recovery watermark unreached, and suppresses the mode write,
so an unreadable filesystem is never reported as recovered.
"""

import dataclasses
import typing as typ

from rust_cleanup import discovery, sweep_discovery
from rust_cleanup.diagnostics import (
    PROTECT_ACTIVE_BUILD,
    PROTECT_CARGO_LOCK,
    PROTECT_EVIDENCE_LOST,
    DeletionFailure,
    record_protected,
)
from rust_cleanup.errors import CleanupError
from rust_cleanup.plans import FilesystemPlan, is_recovered
from rust_cleanup.reclaim import (
    ActivityGetter,
    TargetCandidate,
    revalidation_failure,
)
from rust_cleanup.timing import Monotonic, Span
from rust_cleanup.usage import refresh_usage

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

    from rust_cleanup.active_build import ActiveBuildDetector
    from rust_cleanup.cargo_locks import LockProbe
    from rust_cleanup.filesystem import DirectoryDeleter, UsageGetter
    from rust_cleanup.observability import EventPort
    from rust_cleanup.state import StateStore

# Resolving a path can fail on a symlink loop (``RuntimeError``) or on an
# unreadable filesystem (``OSError``). Both leave the question unanswered, which
# protects the candidate rather than letting the failure escape the sweep.
_RESOLUTION_FAILURES = (OSError, RuntimeError)


@dataclasses.dataclass(frozen=True, slots=True)
class Reclaimer:
    """Carry out one filesystem plan through the resolved adapters.

    Attributes
    ----------
    dry_run : bool
        Whether deletion is planned and reported instead of performed.
    store : StateStore
        Persisted hysteresis state that records the selected mode.
    usage_getter : UsageGetter
        Measures the used capacity of a filesystem.
    activity_getter : ActivityGetter
        Re-reads a candidate's newest artefact mtime before it is deleted.
    delete_directory : DirectoryDeleter
        Removes one directory tree.
    detector_factory : collections.abc.Callable[[], ActiveBuildDetector]
        Builds the detector consulted immediately before each deletion, so the
        answer is fresh rather than the sweep's opening snapshot.
    lock_probe : LockProbe
        Reports whether a build currently holds a candidate's Cargo lock.
    roots : tuple[pathlib.Path, ...]
        Canonical effective scan roots the candidate must still lie inside.
    events : EventPort
        Telemetry port every boundary is reported through.
    correlation : str
        Token tying this sweep's records together.
    monotonic : Monotonic
        Clock the deletion span's duration is measured with.
    warn : collections.abc.Callable[[str], None]
        Sink for the bounded warnings this sweep produces: a usage re-read that
        failed, and a filesystem left above its recovery watermark.
    """

    dry_run: bool
    store: StateStore
    usage_getter: UsageGetter
    activity_getter: ActivityGetter
    delete_directory: DirectoryDeleter
    detector_factory: cabc.Callable[[], ActiveBuildDetector]
    lock_probe: LockProbe
    roots: tuple[Path, ...]
    events: EventPort
    correlation: str
    monotonic: Monotonic
    warn: cabc.Callable[[str], None]

    def reclaim(self, plan: FilesystemPlan) -> None:
        """Record the dry-run plan, or reclaim until recovery is reached.

        Parameters
        ----------
        plan : FilesystemPlan
            Planned filesystem sweep to record or carry out.
        """
        if self.dry_run:
            self._record_dry_run_plan(plan)
            return
        self._reclaim_until_recovered(plan)

    @staticmethod
    def _record_dry_run_plan(plan: FilesystemPlan) -> None:
        """Record the planned order without touching the filesystem.

        Every planned candidate is reported through the returned outcome, and
        the planned count fills the deletion counter, so an empty dry run still
        reports the policy it would have applied.
        """
        plan.diagnostics.planned_deletions.extend(
            candidate.path for candidate in plan.candidates
        )
        plan.diagnostics.deleted = len(plan.candidates)

    def _reclaim_until_recovered(self, plan: FilesystemPlan) -> None:
        """Delete candidates in order until the recovery watermark is met."""
        span = Span(self.monotonic)
        self.events.emit(
            "deletion",
            "start",
            mode=plan.diagnostics.selected_mode.value,
            correlation=self.correlation,
        )
        for candidate in plan.candidates:
            if plan.usage_failed or is_recovered(plan):
                break
            self._delete_candidate(plan, candidate)
        self.events.emit(
            "deletion",
            "finish",
            outcome="failed" if plan.usage_failed else "completed",
            deleted=plan.diagnostics.deleted,
            failed=plan.diagnostics.failed,
            duration_ms=span.close(),
            correlation=self.correlation,
        )

    def _delete_candidate(
        self, plan: FilesystemPlan, candidate: TargetCandidate
    ) -> None:
        """Revalidate and delete one candidate, retaining race-safe skips."""
        reason = self._protection_reason(plan, candidate)
        if reason is not None:
            record_protected(plan.diagnostics, candidate, reason)
            return
        try:
            self.delete_directory(candidate.path)
        except CleanupError as error:
            plan.diagnostics.failed += 1
            plan.diagnostics.deletion_failures.append(
                DeletionFailure(candidate.path, str(error))
            )
            return
        plan.diagnostics.deleted += 1
        refresh_usage(plan, self.usage_getter, warn=self.warn)

    def _protection_reason(
        self, plan: FilesystemPlan, candidate: TargetCandidate
    ) -> str | None:
        """Return the first failed check's fixed reason, or ``None``.

        The checks run in a fixed order, and each one names a distinct fact:
        a live build, a held Cargo lock, changed or recent activity, evidence
        that has gone, and a path that has left the planned scope. Iterating
        over the checks rather than nesting them keeps that order in one place
        and lets every check be read on its own.

        Returns
        -------
        str | None
            The preserve reason from the first failed check, or ``None`` when
            the candidate may be deleted.
        """
        for check in (
            self._build_reason,
            self._lock_reason,
            self._activity_reason,
            self._evidence_reason,
            self._scope_reason,
        ):
            reason = check(plan, candidate)
            if reason is not None:
                return reason
        return None

    def _build_reason(
        self, _plan: FilesystemPlan, candidate: TargetCandidate
    ) -> str | None:
        """Name a build the fresh detector sees, which discovery may have missed.

        Returns
        -------
        str | None
            ``PROTECT_ACTIVE_BUILD`` when a build owns the candidate, otherwise
            ``None``.
        """
        if self.detector_factory().is_active(candidate.path):
            return PROTECT_ACTIVE_BUILD
        return None

    def _lock_reason(
        self, _plan: FilesystemPlan, candidate: TargetCandidate
    ) -> str | None:
        """Name a held or unreadable Cargo lock, which only a "free" clears.

        Lock absence is not evidence of inactivity, so only a definite answer
        lets the candidate proceed.

        Returns
        -------
        str | None
            ``PROTECT_CARGO_LOCK`` unless the probe reported the lock free.
        """
        if self.lock_probe.holds_lock(candidate.path) is not False:
            return PROTECT_CARGO_LOCK
        return None

    def _activity_reason(
        self, plan: FilesystemPlan, candidate: TargetCandidate
    ) -> str | None:
        """Reconfirm the activity snapshot the candidate was planned against.

        Returns
        -------
        str | None
            The reason activity revalidation failed, or ``None`` when the
            candidate is still stale.
        """
        return revalidation_failure(candidate, plan.cutoff_time, self.activity_getter)

    @staticmethod
    def _evidence_reason(
        _plan: FilesystemPlan, candidate: TargetCandidate
    ) -> str | None:
        """Name output whose Cargo evidence has gone since discovery.

        Returns
        -------
        str | None
            ``PROTECT_EVIDENCE_LOST`` when discovery would no longer accept the
            directory, otherwise ``None``.
        """
        try:
            declined = discovery.decline_reason(candidate.path)
        except OSError:
            return PROTECT_EVIDENCE_LOST
        return None if declined is None else PROTECT_EVIDENCE_LOST

    def _scope_reason(
        self, _plan: FilesystemPlan, candidate: TargetCandidate
    ) -> str | None:
        """Name a candidate that has become a link or left every scan root.

        The path was canonical and in scope when it was planned. A directory
        replaced by a symbolic link, or one whose canonical path no longer lies
        inside an effective root, would delete something other than what
        discovery offered, so it is preserved instead.

        Returns
        -------
        str | None
            ``PROTECT_EVIDENCE_LOST`` when the candidate is a link, cannot be
            resolved, or lies outside every effective root, otherwise ``None``.
        """
        try:
            if candidate.path.is_symlink():
                return PROTECT_EVIDENCE_LOST
            resolved = candidate.path.resolve(strict=True)
        except _RESOLUTION_FAILURES:
            return PROTECT_EVIDENCE_LOST
        if not sweep_discovery.within_roots(resolved, self.roots):
            return PROTECT_EVIDENCE_LOST
        return None

    def complete(self, plan: FilesystemPlan) -> None:
        """Record the selected mode and report the recovery outcome.

        Parameters
        ----------
        plan : FilesystemPlan
            Completed plan whose mode is stored and whose recovery outcome is
            reported. Rendering the diagnostics is the caller's step, so this
            module reports only through the injected ports.
        """
        diagnostics = plan.diagnostics
        if plan.watermark is not None:
            self._record_recovery(plan)
        if not self.dry_run and not plan.usage_failed:
            self.store.set_mode(diagnostics.identity, diagnostics.selected_mode)

    def _record_recovery(self, plan: FilesystemPlan) -> None:
        """Emit the bounded recovery-watermark and unresolved-pressure outcome."""
        diagnostics = plan.diagnostics
        mode = diagnostics.selected_mode.value
        if self.dry_run:
            self.events.emit(
                "recovery_watermark",
                "finish",
                outcome="skipped",
                mode=mode,
                correlation=self.correlation,
            )
            return
        diagnostics.exit_reached = is_recovered(plan)
        outcome = "reached" if diagnostics.exit_reached else "not_reached"
        self.events.emit(
            "recovery_watermark",
            "finish",
            outcome=outcome,
            mode=mode,
            correlation=self.correlation,
        )
        if diagnostics.exit_reached:
            return
        if not plan.usage_failed:
            # An unmeasurable filesystem was reported as unreadable already: no
            # reading exists to say where it sits against the watermark.
            self.warn("Warning: filesystem remains above its recovery watermark")
        self.events.emit(
            "unresolved_pressure",
            "finish",
            outcome="unresolved",
            mode=mode,
            correlation=self.correlation,
        )
