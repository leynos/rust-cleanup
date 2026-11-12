"""Contracts for evidence and scope checks that guard a deletion.

Discovery accepted these candidates, and the deletion happens later. The
reclaimer therefore re-applies the acceptance rule to the directory itself and
re-derives its canonical containment before removing anything. These contracts
pin the two ways that can fail: output whose Cargo evidence has gone, and a path
that is no longer the one discovery planned - because it became a symbolic link,
or because its canonical path has left every effective root.

The checks that protect a candidate for being *busy* are contracts of their
own; :mod:`test_reclamation` holds them.
"""

import shutil
import typing as typ

from test_reclamation_support import ReclaimCase, one_reason, plan_for, reclaim

from rust_cleanup import CACHEDIR_TAG
from rust_cleanup.diagnostics import PROTECT_EVIDENCE_LOST

if typ.TYPE_CHECKING:
    from pathlib import Path


def test_output_whose_marker_was_removed_is_not_deleted(tmp_path: Path) -> None:
    """Evidence that has gone since discovery preserves the directory.

    The candidate was real, marked build output when it was planned. Deleting
    the marker afterwards leaves a directory discovery would no longer offer,
    so the re-check must refuse it rather than remove it anyway.
    """
    plan = plan_for(tmp_path, last_activity=100.0)
    (plan.candidates[0].path / CACHEDIR_TAG).unlink()

    deleter = reclaim(plan, ReclaimCase(current_activity=100.0))

    assert not deleter.deleted, "output that lost its evidence was deleted"
    assert plan.candidates[0].path.exists(), "the directory was removed"
    one_reason(plan.diagnostics, PROTECT_EVIDENCE_LOST)


def test_output_that_became_a_source_tree_is_not_deleted(tmp_path: Path) -> None:
    """A checkout written into a marked target preserves it.

    The marker that accepted the directory is unchanged; only a source marker
    arrived. The re-check has to refuse the directory on its own evidence
    rather than trusting the marker that discovery saw.
    """
    plan = plan_for(tmp_path, last_activity=100.0)
    (plan.candidates[0].path / "Cargo.toml").write_text("[package]\n", encoding="utf-8")

    deleter = reclaim(plan, ReclaimCase(current_activity=100.0))

    assert not deleter.deleted, "a candidate that became a source tree was deleted"
    one_reason(plan.diagnostics, PROTECT_EVIDENCE_LOST)


def test_output_replaced_by_a_symlink_is_not_deleted(tmp_path: Path) -> None:
    """A directory swapped for a link would delete something else entirely.

    The link points at a directory outside every effective root, so following
    it would remove a tree discovery never offered.
    """
    plan = plan_for(tmp_path, last_activity=100.0)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    kept = outside / "keep.txt"
    kept.write_text("keep", encoding="utf-8")
    target = plan.candidates[0].path
    shutil.rmtree(target)
    target.symlink_to(outside, target_is_directory=True)

    deleter = reclaim(
        plan,
        ReclaimCase(current_activity=100.0, roots=(tmp_path.resolve(),)),
    )

    assert not deleter.deleted, "a symlinked candidate was offered for deletion"
    assert kept.exists(), "the link target was followed"
    one_reason(plan.diagnostics, PROTECT_EVIDENCE_LOST)


def test_output_outside_every_effective_root_is_not_deleted(tmp_path: Path) -> None:
    """Canonical containment is rechecked, not assumed from discovery.

    The candidate is unchanged on disk; only the effective roots moved. A
    deletion pass that trusted the planned scope would remove it anyway.
    """
    plan = plan_for(tmp_path, last_activity=100.0)
    elsewhere = tmp_path / "not-a-scan-root"
    elsewhere.mkdir()

    deleter = reclaim(
        plan,
        ReclaimCase(current_activity=100.0, roots=(elsewhere.resolve(),)),
    )

    assert not deleter.deleted, "a candidate outside every root was deleted"
    assert plan.candidates[0].path.exists(), "the out-of-scope directory was removed"
    one_reason(plan.diagnostics, PROTECT_EVIDENCE_LOST)


def test_a_candidate_inside_a_root_survives_the_containment_check(
    tmp_path: Path,
) -> None:
    """The containment check permits the scope it was handed.

    Without this witness the two contracts above would pass just as well if the
    scope check protected everything unconditionally.
    """
    plan = plan_for(tmp_path, last_activity=100.0)

    deleter = reclaim(
        plan,
        ReclaimCase(current_activity=100.0, roots=(tmp_path.resolve(),)),
    )

    assert deleter.deleted == [plan.candidates[0].path], (
        "a candidate inside its root was not deleted"
    )
    assert not plan.diagnostics.protection_reasons, (
        f"an in-scope candidate recorded a reason: "
        f"{plan.diagnostics.protection_reasons!r}"
    )
