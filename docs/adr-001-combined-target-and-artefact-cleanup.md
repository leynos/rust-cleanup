# ADR 001: Combine target and artefact reclamation

## Status

Proposed. The requested product direction is combined cleanup with an inherited
retention TTL; the implementation and compatibility gates remain outstanding.

## Date

2026-11-12.

## Context and problem statement

Parallel coding agents keep target directories active while accumulating old
build outputs. Whole-target retention cannot reclaim old outputs from a target
whose aggregate modification time remains recent. In a space-constrained host,
waiting for that entire target to become cold leaves useful reclamation work
unavailable.

The current Python sweep already owns root discovery, build protection,
per-filesystem pressure, recovery, and mode persistence. Running another
cleaner after it would introduce a second discovery/planning path and decisions
outside the common pressure budget. Cargo-sweep provides a reference for
fingerprint-age selection, but it is a binary rather than an embedded Python
planner and its current README marks it unmaintained.[^upstream]

## Decision drivers

- Reclaim old artefact groups from retained targets during the same invocation.
- Inherit the effective whole-target TTL, including pressure-driven reductions,
  instead of introducing a longer default retention window.
- Preserve active agents' output and coordinate mutation with Cargo.
- Avoid duplicate root walks, descendant deletion plans, and inflated counts.
- Keep unsupported layouts and uncertain ownership conservative.

## Options considered

| Option                                                       | Consequence                                                                                                            |
| ------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------- |
| Run `cargo sweep` after target cleanup.                      | Separate traversal and recovery accounting; subprocess output does not provide a shared typed deletion plan.           |
| Remove arbitrary files by modification time.                 | Does not implement fingerprint/hash-group semantics and can leave inconsistent Cargo metadata.                         |
| Add native fingerprint-group planning to the existing sweep. | Reuses the invocation and filesystem decisions, but requires an explicit Cargo-layout and lock compatibility boundary. |

*Table 1: Integration options and their consequences.*

## Proposed direction

Extend the existing sweep with a shared per-target planning inventory and two
operation kinds: whole-target deletion and fingerprint-group pruning. Select
whole targets first and suppress every descendant group operation. For retained
targets, plan old fingerprint groups independently of aggregate target mtime.

The completed feature enables both strategies by default, with an explicit
artefact opt-out. Artefact TTL inherits the selected normal, pressure, or
critical `stale_after` value unless explicitly overridden. An added `d`
duration unit permits an N-day override without rounding inherited sub-day TTLs.

Implement the time-selection semantics in the standard-library runtime, using
pinned cargo-sweep source as a compatibility reference rather than a runtime
dependency. Retain the current invocation lock and whole-target guard path.
Require an acquired, supported profile mutation guard for partial cleanup;
`LockProbe.holds_lock() is False` is insufficient because it also represents
some unsupported lock cases. Certify the guard protocol before destructive
partial cleanup ships.

The detailed contracts, exceptions, and verification obligations are in
[design §11](design-doc.md#11-proposed-combined-target-and-artefact-cleanup).
The [roadmap](roadmap.md) makes ratification and compatibility evidence
explicit prerequisites for implementation.

## Outstanding decisions

Before acceptance, record the supported Cargo command/version and lock-set
matrix, supported profile layouts, and how atime capability is established on
those filesystems. The existing one-version lock experiment does not close
these questions. The requested default TTL inheritance is fixed by the brief,
not an open question for an implementation task to reinterpret.

## Consequences

The deployment keeps one command and timer. A shared filesystem budget stops
both strategies when recovery is measured. Target counters retain their
existing meaning; new group counters describe partial cleanup separately.

Access times remain a recency heuristic, especially under `relatime`. Inherited
short TTLs must not be silently widened to compensate. Known disabled or
unavailable access-time evidence instead preserves partial candidates.

Cargo internal layouts and locks can change. Unknown hashes, unsupported
profiles, and uncertain guards may leave reclaimable space untouched. Phase 1
must settle the supported compatibility matrix; it cannot infer correctness
from one Cargo version or one successful lock experiment. The current
whole-target concurrency limits remain documented rather than being hidden by
the stronger partial-cleanup protocol.

[^upstream]: [cargo-sweep source at revision 94a7cf0](https://github.com/holmgr/cargo-sweep/tree/94a7cf012d40d314896c0fa6986132b0a1e931ba),
             version 0.8.0; [README maintenance notice](https://github.com/holmgr/cargo-sweep/blob/94a7cf012d40d314896c0fa6986132b0a1e931ba/README.md).
