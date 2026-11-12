"""Contracts for multi-root coverage and for what a sweep reports keeping.

A sweep that quietly scans less than it was asked to is indistinguishable from
one that found nothing, and that ambiguity is what let the original incident
pass unnoticed. These tests pin the observable difference: the effective roots
travel with the outcome, every declined candidate carries a fixed reason, a
target reached through two roots is offered once, and each protected candidate
records why it survived.
"""

import fcntl
import os
import typing as typ

from conftest import (
    InactiveBuilds,
    RunOptions,
    build_run,
    make_target,
    synthetic_identity,
)

from rust_cleanup import TARGET_DIR, CleanupOutcome, cleanup_scan_roots, sweep_discovery
from rust_cleanup.cargo_locks import LOCK_NAME
from rust_cleanup.state import StateStore
from rust_cleanup.sweep_discovery import confine_targets

if typ.TYPE_CHECKING:
    from pathlib import Path


def _sweep(
    tmp_path: Path, roots: tuple[Path, ...], **overrides: typ.Unpack[RunOptions]
) -> CleanupOutcome:
    """Build an inert injected run over ``tmp_path`` with eligible stale targets.

    Parameters
    ----------
    tmp_path : Path
        Test root holding the sweep's state document.
    roots : tuple[Path, ...]
        Effective scan roots to sweep.
    **overrides : Unpack[RunOptions]
        CleanupRun overrides applied on top of the inert defaults.

    Returns
    -------
    CleanupOutcome
        The completed sweep outcome.
    """
    options: RunOptions = {
        "state_store": StateStore(tmp_path / "state.json"),
        "active_build_detector": InactiveBuilds(),
        "usage_getter": lambda _path: 1.0,
        "identity_getter": lambda _path: synthetic_identity(tmp_path),
        "now": 10000,
        **overrides,
    }
    return cleanup_scan_roots(roots, build_run(**options))


def test_the_outcome_names_every_effective_root(tmp_path: Path) -> None:
    """Both roots are reported, so a caller can see what was scanned."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    make_target(first, "one", 1)
    make_target(second, "two", 1)

    outcome = _sweep(tmp_path, (first, second), dry_run=True)

    assert outcome.roots == (first, second), (
        f"the effective roots were not reported: {outcome.roots!r}"
    )
    assert outcome.scanned == 2, "a target below the second root was not scanned"


def test_a_target_reached_through_two_roots_is_offered_once(tmp_path: Path) -> None:
    """Overlapping roots must not produce two deletion plans for one target."""
    outer = tmp_path / "projects"
    inner = outer / "nested"
    target = make_target(inner, "project", 1)

    outcome = _sweep(tmp_path, (outer, inner), dry_run=True)

    assert outcome.planned_deletions == (target.resolve(),), (
        f"overlapping roots duplicated the target: {outcome.planned_deletions!r}"
    )


def test_a_target_outside_every_root_is_reported_not_dropped(tmp_path: Path) -> None:
    """The roots confine deletion even when discovery reports a path elsewhere.

    A root can be replaced between the walk and the deletion, so containment is
    re-checked against the canonical roots. The escaped path is reported as a
    fixed omission rather than silently dropped, because a narrowed sweep
    otherwise looks exactly like a quiet one.
    """
    root = tmp_path / "root"
    root.mkdir()
    inside = root / "target"
    inside.mkdir()
    outside = tmp_path / "elsewhere" / "target"
    outside.mkdir(parents=True)

    kept, escaped = confine_targets((inside, outside), (root,))

    assert kept == [inside.resolve()], f"an in-root target was dropped: {kept!r}"
    assert [(item.path, item.reason) for item in escaped] == [
        (outside, sweep_discovery.OMIT_OUTSIDE_ROOT)
    ], f"the escaped target was not reported: {escaped!r}"


def test_declined_candidates_are_reported_with_their_reason(tmp_path: Path) -> None:
    """An empty sweep shows what it refused, not merely that it found nothing."""
    lookalike = tmp_path / "project" / TARGET_DIR
    lookalike.mkdir(parents=True)
    (lookalike / "notes.txt").write_text("source, not output", encoding="utf-8")

    outcome = _sweep(tmp_path, (tmp_path,), dry_run=True)

    assert outcome.scanned == 0, "an unproven directory was scanned as a target"
    assert outcome.omitted == 1, "the declined candidate was not reported"
    assert outcome.omissions[0].reason == "unmarked-no-evidence", (
        f"the omission reason changed: {outcome.omissions[0].reason!r}"
    )


def test_a_marked_target_is_not_reported_as_an_omission(tmp_path: Path) -> None:
    """Accepting a candidate is not a decision worth reporting as declined."""
    target = make_target(tmp_path, "project", 1)

    outcome = _sweep(tmp_path, (tmp_path,), dry_run=True)

    assert outcome.omitted == 0, (
        f"an accepted target was reported as omitted: {outcome.omissions!r}"
    )
    assert outcome.planned_deletions == (target.resolve(),), (
        "the marked target was not planned for deletion"
    )


def test_an_active_build_is_recorded_with_its_reason(tmp_path: Path) -> None:
    """A candidate kept for a live build carries the fixed ``active-build`` token."""
    make_target(tmp_path, "project", 1)

    outcome = _sweep(
        tmp_path,
        (tmp_path,),
        active_build_detector=_ProtectEverything(),
        delete_directory=_RefuseToDelete(),
    )

    assert outcome.protection_reasons["active-build"] == 1, (
        f"the active-build reason was not recorded: {outcome.protection_reasons!r}"
    )
    assert outcome.deleted == 0, "a protected candidate was counted as deleted"


def test_a_held_cargo_lock_is_recorded_with_its_own_reason(tmp_path: Path) -> None:
    """A lock with no owning process is named ``cargo-lock``, not ``active-build``.

    The reason vocabulary exists so an operator can tell a live build from an
    unreadable or held lock. The detector here reports no build at all, so only
    the lock probe can preserve the target; if the detector probed the lock
    itself, the reason recorded would be the less specific ``active-build``.
    """
    target = make_target(tmp_path, "project", 1)
    profile = target / "debug"
    profile.mkdir()
    lock = profile / LOCK_NAME
    lock.write_bytes(b"")

    descriptor = os.open(lock, os.O_RDWR)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        outcome = _sweep(tmp_path, (tmp_path,))
    finally:
        os.close(descriptor)

    assert outcome.protected_targets == (target.resolve(),), (
        f"a held Cargo lock did not protect its target: {outcome.protected_targets!r}"
    )
    assert outcome.protection_reasons["cargo-lock"] == 1, (
        f"the cargo-lock reason was not recorded: {outcome.protection_reasons!r}"
    )
    assert target.exists(), "the target of a held lock was deleted"


def test_unknown_activity_is_recorded_with_its_reason(tmp_path: Path) -> None:
    """Unreadable activity is named, so a stalled sweep can be diagnosed."""
    make_target(tmp_path, "project", 1)

    outcome = _sweep(tmp_path, (tmp_path,), activity_getter=lambda _path: None)

    assert outcome.protection_reasons["unknown-activity"] == 1, (
        f"the unknown-activity reason was not recorded: {outcome.protection_reasons!r}"
    )


def test_recent_activity_is_recorded_with_its_reason(tmp_path: Path) -> None:
    """A target kept only because it is recent must say so.

    Protection checks pass for this candidate, so recency is the only thing
    that keeps it. Without an explicit branch the candidate would drop through
    planning silently and the sweep would report keeping nothing, leaving an
    operator unable to tell a recent-only scan from a discovery rule that had
    stopped matching.
    """
    make_target(tmp_path, "project", 1)

    outcome = _sweep(tmp_path, (tmp_path,), activity_getter=lambda _path: 9999.0)

    assert outcome.protection_reasons["recent-activity"] == 1, (
        f"the recent-activity reason was not recorded: {outcome.protection_reasons!r}"
    )


def test_a_build_that_starts_after_planning_is_protected_by_the_fresh_detector(
    tmp_path: Path,
) -> None:
    """The planning snapshot and the deletion check are separate readings.

    Planning asks the detector once, while the reclaimer asks its factory again
    immediately before deleting. The factory here answers inactive for the
    planning snapshot and active for the deletion check, which is precisely the
    race the fresh check exists to close: a sweep that reused the opening
    snapshot would delete a target a build had just started writing.
    """
    target = make_target(tmp_path, "project", 1)
    answers = iter((False, True))

    def factory() -> _BuildStartedLater:
        """Build a fresh detector that answers the next scripted value.

        Returns
        -------
        _BuildStartedLater
            A detector carrying the next scripted build state.
        """
        return _BuildStartedLater(active=next(answers))

    outcome = _sweep(tmp_path, (tmp_path,), detector_factory=factory)

    assert outcome.deleted == 0, "a build started after planning was ignored"
    assert outcome.protected_targets == (target.resolve(),), (
        f"the late build did not protect its target: {outcome.protected_targets!r}"
    )
    assert outcome.protection_reasons["active-build"] == 1, (
        f"the active-build reason was not recorded: {outcome.protection_reasons!r}"
    )
    assert target.exists(), "the target of a late build was deleted"


class _ProtectEverything:
    """A detector that reports every candidate as owned by a live build."""

    def is_active(self, target_path: Path) -> bool:
        """Return true for every path.

        Parameters
        ----------
        target_path : Path
            Target path the caller asks about; this detector ignores it.

        Returns
        -------
        bool
            Always ``True``.
        """
        return True


class _BuildStartedLater:
    """A detector that reports a build registered against a named project.

    A real detector resolves a target to the project that owns it and asks
    whether any process is building there, so this one does the same: the
    target's parent names the project, and the registry holds the projects
    with a build in flight. Each instance carries its own registry, which is
    what lets a scripted factory answer "no build" at planning and "build
    started" at the pre-deletion check.
    """

    def __init__(self, *, active: bool) -> None:
        """Register a build for the case's project when ``active``.

        Parameters
        ----------
        active : bool
            Whether this detector's registry holds a build in flight.
        """
        self._building = {"project"} if active else set()

    def is_active(self, target_path: Path) -> bool:
        """Report whether the target's owning project has a build in flight.

        Parameters
        ----------
        target_path : Path
            Target path the caller asks about.

        Returns
        -------
        bool
            Whether this detector's registry names the owning project.
        """
        return target_path.parent.name in self._building


class _RefuseToDelete:
    """A deleter that fails the test if it is ever reached."""

    def __call__(self, directory: Path, /) -> bool:
        """Fail loudly: a protected candidate must never reach deletion.

        Parameters
        ----------
        directory : Path
            Directory whose deletion was requested.

        Raises
        ------
        AssertionError
            For every directory, because none should be offered.
        """
        message = f"a protected candidate reached deletion: {directory}"
        raise AssertionError(message)
