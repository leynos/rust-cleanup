"""Contracts for the ordered checks that guard a deletion.

Planning decides what may be reclaimed, but deletion happens later, and the
filesystem can change in between. The reclaimer therefore re-checks every
candidate immediately before removing it, and these tests pin both the order of
those checks and the fixed reason each failed check records.

The order is part of the contract. A build that started after planning is the
race the re-check exists to close, so it is named before the activity checks
that would otherwise report the same candidate as merely changed.

Evidence that has gone, and a candidate that has left the planned scope, are
contracts of their own; :mod:`test_reclamation_evidence` holds them.
"""

import typing as typ

import pytest
from test_reclamation_support import (
    CUTOFF,
    FixedLock,
    ProtectEverything,
    ReclaimCase,
    RefusingLocks,
    ScriptedLock,
    one_reason,
    plan_for,
    reclaim,
)

from rust_cleanup.diagnostics import (
    PROTECT_ACTIVE_BUILD,
    PROTECT_CARGO_LOCK,
    PROTECT_RECENT,
    PROTECT_REVALIDATION_CHANGED,
    PROTECT_UNKNOWN_ACTIVITY,
)

if typ.TYPE_CHECKING:
    from pathlib import Path


class CheckCase(typ.NamedTuple):
    """Script the activity readings one pre-deletion check case observes.

    The fields describe one decision, so they travel together: the snapshot
    discovery recorded, the reading the immediate re-check takes, the cutoff the
    plan was built against, and the reason the pass must record.
    """

    last_activity: float
    current_activity: float | None
    cutoff_time: float
    expected_reason: str | None


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(CheckCase(100.0, 100.0, CUTOFF, None), id="unchanged-stale"),
        pytest.param(
            CheckCase(100.0, 200.0, CUTOFF, PROTECT_REVALIDATION_CHANGED),
            id="changed",
        ),
        pytest.param(
            CheckCase(100.0, None, CUTOFF, PROTECT_UNKNOWN_ACTIVITY), id="unknown"
        ),
        pytest.param(CheckCase(100.0, 100.0, 50.0, PROTECT_RECENT), id="recent"),
    ],
)
def test_every_pre_deletion_check_records_its_fixed_reason(
    tmp_path: Path, case: CheckCase
) -> None:
    """Each transition between discovery and deletion keeps its own reason.

    The unchanged-stale witness proves the checks are not protecting
    everything, and each protected witness proves one branch is live.
    """
    plan = plan_for(
        tmp_path, last_activity=case.last_activity, cutoff_time=case.cutoff_time
    )

    deleter = reclaim(plan, ReclaimCase(case.current_activity))

    if case.expected_reason is None:
        assert deleter.deleted == [plan.candidates[0].path], (
            "an unchanged stale candidate was not deleted"
        )
        assert not plan.diagnostics.protection_reasons, (
            "an eligible candidate recorded a reason: "
            f"{plan.diagnostics.protection_reasons!r}"
        )
        return
    assert not deleter.deleted, (
        f"a protected candidate was offered for deletion: {deleter.deleted!r}"
    )
    one_reason(plan.diagnostics, case.expected_reason)


def test_a_build_started_after_planning_outranks_changed_activity(
    tmp_path: Path,
) -> None:
    """Build detection runs first, so the race is named rather than the symptom.

    The candidate's activity also changed after discovery, so activity alone
    would explain the preservation. Reading activity before the build check
    would therefore record ``revalidation-changed`` and hide the race, which is
    why the activity port below refuses to be consulted at all.
    """
    plan = plan_for(tmp_path, last_activity=100.0)

    def refuse(_directory: Path) -> float | None:
        """Fail if activity is read before the build check runs.

        Raises
        ------
        AssertionError
            When the forbidden operation is attempted.
        """
        message = "activity was read before the build check ran"
        raise AssertionError(message)

    deleter = reclaim(
        plan,
        ReclaimCase(
            current_activity=200.0,
            detector_factory=ProtectEverything,
            activity_getter=refuse,
        ),
    )

    assert not deleter.deleted, "a candidate owned by a live build was deleted"
    one_reason(plan.diagnostics, PROTECT_ACTIVE_BUILD)


def test_a_held_cargo_lock_protects_the_candidate_from_deletion(tmp_path: Path) -> None:
    """A held lock is direct evidence, so the candidate is not deleted.

    The lock probe runs before the activity re-check, so a held lock alone is
    enough: the activity reading is never consulted.
    """
    plan = plan_for(tmp_path, last_activity=100.0)

    def refuse(_directory: Path) -> float | None:
        """Fail if activity is read before the lock probe runs.

        Raises
        ------
        AssertionError
            When the forbidden operation is attempted.
        """
        message = "activity was read before the lock probe ran"
        raise AssertionError(message)

    deleter = reclaim(
        plan,
        ReclaimCase(
            current_activity=100.0,
            lock_probe=FixedLock(ScriptedLock.HELD),
            activity_getter=refuse,
        ),
    )

    assert not deleter.deleted, "a held Cargo lock did not protect the candidate"
    one_reason(plan.diagnostics, PROTECT_CARGO_LOCK)


def test_an_unprobeable_lock_is_treated_as_active(tmp_path: Path) -> None:
    """An unreadable lock state protects rather than counting as free."""
    plan = plan_for(tmp_path, last_activity=100.0)

    deleter = reclaim(
        plan,
        ReclaimCase(current_activity=100.0, lock_probe=FixedLock(ScriptedLock.UNKNOWN)),
    )

    assert not deleter.deleted, "an unknown lock state allowed a deletion"
    one_reason(plan.diagnostics, PROTECT_CARGO_LOCK)


def test_a_protected_build_is_named_before_the_lock_is_consulted(
    tmp_path: Path,
) -> None:
    """The build check outranks the lock probe, so a live build is named.

    Both would preserve the candidate, but only the build check names the race
    an operator can act on, so the lock probe must not run first.
    """
    plan = plan_for(tmp_path, last_activity=100.0)

    deleter = reclaim(
        plan,
        ReclaimCase(
            current_activity=100.0,
            detector_factory=ProtectEverything,
            lock_probe=RefusingLocks(),
        ),
    )

    assert not deleter.deleted, "a candidate owned by a live build was deleted"
    one_reason(plan.diagnostics, PROTECT_ACTIVE_BUILD)
