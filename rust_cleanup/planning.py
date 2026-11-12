"""Candidate snapshots and per-filesystem policy for one cleanup sweep.

Planning is the query half of a sweep. It turns already-resolved target
directories into typed candidates, groups them by backing filesystem, and
decides - for each filesystem - which retention mode is in force and which of
those candidates that mode may reclaim. Nothing here deletes or persists
anything, so a plan can be produced, inspected, and reported without the
system changing underneath it.

The retention transition belongs to :mod:`rust_cleanup.pressure`, activity
inspection to an injected port, and canonicalization to the adapter that
resolves a target. This module only asks them for an answer and combines it
into a plan, so it holds no operating-system imports of its own and writes no
log records: an environmental failure travels back to its caller as a value,
or through the warning sink the caller supplied.
"""

import dataclasses
import typing as typ

from rust_cleanup.diagnostics import (
    PROTECT_ACTIVE_BUILD,
    PROTECT_CARGO_LOCK,
    PROTECT_RECENT,
    PROTECT_UNKNOWN_ACTIVITY,
    FilesystemDiagnostics,
    record_protected,
)
from rust_cleanup.plans import FilesystemPlan, unmeasurable_plan
from rust_cleanup.pressure import (
    FilesystemIdentity,
    exit_watermark,
    initial_mode,
    mode_cutoff,
    transition_mode,
)
from rust_cleanup.reclaim import TargetCandidate, candidate_order, is_eligible
from rust_cleanup.usage import measure_usage

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from rust_cleanup.active_build import ActiveBuildDetector
    from rust_cleanup.cargo_locks import LockProbe
    from rust_cleanup.config import CleanupConfig
    from rust_cleanup.filesystem import ResolvedTarget, UsageGetter
    from rust_cleanup.reclaim import ActivityGetter
    from rust_cleanup.state import StateStore


@dataclasses.dataclass(frozen=True, slots=True)
class CandidateSnapshot:
    """One query result over the targets that could be identified.

    Attributes
    ----------
    grouped : dict[FilesystemIdentity, list[TargetCandidate]]
        Candidate snapshots grouped by the filesystem that backs them.
    """

    grouped: dict[FilesystemIdentity, list[TargetCandidate]]


@dataclasses.dataclass(frozen=True, slots=True)
class Planner:
    """Decide candidate eligibility and policy for one sweep.

    Attributes
    ----------
    config : CleanupConfig
        Retention thresholds and watermarks for every mode.
    store : StateStore
        Persisted hysteresis state, supplying the previous mode.
    usage_getter : UsageGetter
        Measures the used capacity of a filesystem.
    activity_getter : ActivityGetter
        Reports the newest modification time under a candidate path.
    detector : ActiveBuildDetector
        Protects targets owned by live build work.
    lock_probe : LockProbe
        Protects targets whose Cargo lock is held or cannot be interrogated.
    now : float
        Wall-clock time the sweep started, in seconds.
    warn : collections.abc.Callable[[str], None]
        Sink for the bounded warnings a query produces.
    """

    config: CleanupConfig
    store: StateStore
    usage_getter: UsageGetter
    activity_getter: ActivityGetter
    detector: ActiveBuildDetector
    lock_probe: LockProbe
    now: float
    warn: cabc.Callable[[str], None]

    def snapshot(self, targets: list[ResolvedTarget]) -> CandidateSnapshot:
        """Group resolved targets by the filesystem that backs them.

        Parameters
        ----------
        targets : list[ResolvedTarget]
            Canonical target directories with their identified filesystem.

        Returns
        -------
        CandidateSnapshot
            Candidate snapshots grouped by their backing filesystem.
        """
        grouped: dict[FilesystemIdentity, list[TargetCandidate]] = {}
        for target in targets:
            candidate = TargetCandidate(
                target.path, target.filesystem, self.activity_getter(target.path)
            )
            grouped.setdefault(candidate.filesystem, []).append(candidate)
        return CandidateSnapshot(grouped)

    def plan(
        self, identity: FilesystemIdentity, candidates: list[TargetCandidate]
    ) -> FilesystemPlan:
        """Select mode and derive the ordered candidates eligible for it.

        Parameters
        ----------
        identity : FilesystemIdentity
            Backing filesystem the plan and its mode apply to.

        candidates : list[TargetCandidate]
            Candidate snapshots discovered on that filesystem.

        Returns
        -------
        FilesystemPlan
            The selected policy and safe oldest-first candidate plan.
        """
        previous = self.store.mode_for(identity)
        before = measure_usage(
            self.usage_getter,
            identity.mount_point,
            f"preserving {len(candidates)} candidate(s)",
            warn=self.warn,
        )
        if before is None:
            return unmeasurable_plan(identity, previous, candidates, self.config)
        selected = (
            initial_mode(before, self.config)
            if previous is None
            else transition_mode(previous, before, self.config)
        )
        cutoff = mode_cutoff(selected, self.config)
        cutoff_time = self.now - cutoff
        diagnostics = FilesystemDiagnostics(
            identity, before, before, previous, selected, cutoff, len(candidates)
        )
        eligible = self._eligible_candidates(candidates, diagnostics, cutoff_time)
        return FilesystemPlan(
            diagnostics,
            eligible,
            cutoff_time,
            exit_watermark(selected, self.config),
        )

    def _eligible_candidates(
        self,
        candidates: list[TargetCandidate],
        diagnostics: FilesystemDiagnostics,
        cutoff_time: float,
    ) -> list[TargetCandidate]:
        """Filter protected candidates and sort safe stale candidates oldest first.

        Returns
        -------
        list[TargetCandidate]
            Safe stale candidates in deterministic deletion order.
        """
        eligible = []
        for candidate in candidates:
            reason = _protection_reason(candidate, self.detector, self.lock_probe)
            if reason is not None:
                record_protected(diagnostics, candidate, reason)
            elif is_eligible(candidate, cutoff_time):
                eligible.append(candidate)
            else:
                # Protection checks passed, so the only remaining way to be
                # ineligible is recent activity. Record it: without this the
                # candidate drops through silently and a scan that kept
                # everything for recency reports no reason at all.
                record_protected(diagnostics, candidate, PROTECT_RECENT)
        eligible.sort(key=candidate_order)
        diagnostics.eligible = len(eligible)
        return eligible


def _protection_reason(
    candidate: TargetCandidate,
    detector: ActiveBuildDetector,
    lock_probe: LockProbe,
) -> str | None:
    """Return the fixed reason to preserve a candidate, or ``None``.

    Unknown activity outranks build detection because it is the broader
    uncertainty: a candidate whose contents could not be read cannot be shown
    to be inactive at all. The build probe follows, and the cargo-lock probe
    last, so the planning pass names the same cause in the same order as the
    deletion pass that repeats these checks.

    Returns
    -------
    str | None
        A fixed protection reason, or ``None`` when the candidate may proceed.
    """
    if candidate.last_activity is None:
        return PROTECT_UNKNOWN_ACTIVITY
    if detector.is_active(candidate.path):
        return PROTECT_ACTIVE_BUILD
    if lock_probe.holds_lock(candidate.path) is not False:
        return PROTECT_CARGO_LOCK
    return None
