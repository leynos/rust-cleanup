# rust-cleanup implementation roadmap

This draft delivers the
[combined cleanup proposal](design-doc.md#11-proposed-combined-target-and-artefact-cleanup)
on top of the existing whole-target implementation. It targets
space-constrained hosts with many parallel Rust coding agents. The current
command does not yet prune individual artefact groups.
[ADR 001](adr-001-combined-target-and-artefact-cleanup.md) is proposed;
ratification and compatibility evidence are delivery gates.

The roadmap follows Goals, Ideas, Steps, and Tasks (GIST). Each phase states a
falsifiable idea, each step answers a delivery question, and each checkbox is a
review-sized execution unit. It makes no date commitments. Existing design
sections 1–10 remain the baseline; this roadmap implements section 11 rather
than rebuilding already imported functionality. Step 1.3 separately strengthens
verification of the existing implementation before refactoring it. Normal
implementation tasks include relevant unit and behavioural verification;
combination and end-to-end suites are separate delivery tasks where they span
several boundaries.

The completed feature runs both strategies in one pass by default. Artefact TTL
inherits the effective normal, pressure, or critical cleanup TTL unless
explicitly overridden. Development previews may remain opt-in until the release
gate establishes safe combined operation.

| Requirement                                   | Principal delivery tasks |
| --------------------------------------------- | ------------------------ |
| R1–R2: shared invocation and inventory        | 2.1.1, 2.2.2, 4.2.2      |
| R3: inherited TTL                             | 2.2.1, 4.1.2             |
| R4: disjoint operations                       | 2.2.2, 3.2.1, 4.2.1      |
| R5: retained-target reclamation               | 2.1.2, 3.2.1             |
| R6: acquired build guard                      | 1.2.1, 3.1.1, 4.2.2      |
| R7: shared recovery                           | 4.1.1, 4.1.2             |
| R8: compatible reporting                      | 3.2.2                    |
| R9: truthful preview                          | 2.2.2, 4.2.1             |
| R10: default combined deployment and rollback | 5.1.1, 5.1.2             |

## 1. Establish a compatibility boundary that can authorize deletion

Idea: if pinned fingerprint semantics and acquired Cargo guards can be verified
against real layouts before destructive work, native partial cleanup can reuse
the existing sweep without guessing which files or concurrent builds it owns.
Failure to establish those contracts blocks mutation rather than weakening the
guards.

### 1.1. Settle the contracts that would otherwise change during implementation

This step answers which semantics are deliberate, which upstream differences
are conservative, and what the public compatibility promise includes. Its
outcome constrains every implementation slice. See design-doc.md §§11.1–11.4
and ADR 001.

- [ ] 1.1.1. Ratify ADR 001 and the initial compatibility scope.
  - See [ADR 001](adr-001-combined-target-and-artefact-cleanup.md) and
    [design §11.2](design-doc.md#112-cargo-sweep-reference-and-compatibility-boundary).
  - Success: an accepted decision identifies supported profile shapes,
    orphan/unknown preservation, atime limitations, default combined operation,
    and the explicit deferred scope.
- [ ] 1.1.2. Finalize inventory, group, operation, guard-result, and outcome
  contracts, extending the proposed JSON and TOML artefacts.
  - Requires 1.1.1.
  - See
    [design §§11.3–11.4](design-doc.md#113-retention-policy-and-proposed-configuration)
    and [contract cases](design/combined-sweep-cases.json).
  - Success: inherited and explicit TTLs, age equality, invalidated-parent
    suppression, legacy activity adapters, and separate counters have one
    unambiguous representation.

### 1.2. Prove the Cargo and filesystem evidence is usable

This step answers whether the proposed guard and age evidence work on the
supported environment. It informs which layouts can enter the first preview and
which must be preserved. See design-doc.md §§11.2, 11.6–11.7.

- [ ] 1.2.1. Build a real-Cargo compatibility corpus and certify the profile
  mutation-guard protocol for the supported command/version matrix.
  - Requires 1.1.2.
  - See
    [design §11.6](design-doc.md#116-concurrent-build-guards-and-mutation-protocol).
  - Success: offline build, check, test, and clean witnesses document the
    relevant lock sets and demonstrate blocking while held and progress after
    release. Include legacy locks and the additional locks observed with Cargo
    1.99.1; unknown sets remain unsupported.
- [ ] 1.2.2. Establish a pinned upstream comparison corpus and workload
      baseline.
  - Requires 1.1.2.
  - See
    [design §§11.2 and 11.7](design-doc.md#112-cargo-sweep-reference-and-compatibility-boundary).
  - Success: recorded fingerprint timestamps and hash names produce known
    upstream eligible sets; intentional orphan/empty/unknown differences are
    labelled. Capture planning walks, process snapshots, peak memory, and
    reclamation latency on a synthetic many-agent layout for later comparison.

### 1.3. Establish a stronger verification baseline for existing cleanup code

This step answers which current invariants are already generated, which gaps
benefit from Hypothesis, and which small pure predicates support useful bounded
CrossHair searches. It validates the existing whole-target implementation
before combined-cleanup refactors. See design-doc.md §9.1. These explicit
baseline assurance tasks span several components; they do not replace the unit
and behavioural checks required inside later implementation tasks.

- [ ] 1.3.1. Inventory existing property coverage and qualify a reproducible,
  bounded verification runner on the project's Python baseline.

  - See [design §9.1](design-doc.md#91-planned-validation-of-existing-code).
  - Success: map each existing `@given` property to its actual assertion and
    uncovered domain. Qualify and pin compatible CrossHair tooling for Python
    3.14, choose supported contract syntax and real function targets, and record
    per-path/per-condition budgets and unsupported cases. Keep cheap Hypothesis
    properties in ordinary pytest and make symbolic runs separately reproducible;
    neither a timeout nor an unanalysed target counts as verification.
- [ ] 1.3.2. Add and search contracts on qualified existing pure cleanup
  predicates, retaining counterexamples as named regression tests.

  - Requires 1.3.1.
  - See
    [design §§5, 6, and 9.1](design-doc.md#91-planned-validation-of-existing-code).
  - Success: bounded searches exercise critical-mode retention, entry/recovery
    boundaries, mode cutoffs, unknown/equal-age eligibility, and usage-failure
    recovery predicates in the actual production functions. Document validated
    input domains and special timestamps; use `diffbehavior` for subsequent
    qualified pure refactors. Record unsupported or inconclusive searches as
    gaps with an explicit generated-test fallback, never as proofs.
- [ ] 1.3.3. Strengthen existing Hypothesis selection and pressure properties
  with semantic invariants and the domains they currently omit.

  - Requires 1.3.1.
  - See
    [design §§5 and 9.1](design-doc.md#91-planned-validation-of-existing-code).
  - Success: construct valid custom threshold configurations and observation
    sequences that check non-premature critical recovery and strict boundary
    semantics, beyond mode membership. Extend ordering coverage with tied ages,
    unknown activity, input permutations, and multiple filesystems; assert
    deterministic ties, eligibility, and isolation. Extend root properties
    with deeper canonical trees, idempotence, and first-declared survivor order
    instead of duplicating the existing outermost-set property.
- [ ] 1.3.4. Add lightweight generated properties for existing configuration,
  state-decoding, and telemetry validation boundaries lacking broad coverage.

  - Requires 1.3.1.
  - See
    [design §§7 and 9.1](design-doc.md#91-planned-validation-of-existing-code).
  - Success: current `s`/`m`/`h` duration scaling and configuration precedence
    hold across constructed valid inputs; malformed state entries invalidate a
    whole document without duplicating the existing valid round trip. Generated
    field/value combinations exercise telemetry vocabulary, numeric bounds,
    booleans, non-finite values, and structured-payload rejection. Use independent
    or metamorphic assertions and preserve named examples and real I/O checks.
- [ ] 1.3.5. Generate bounded histories against the existing whole-target
  planner and reclaimer through their injected adapters.

  - Requires 1.3.3 and 1.3.4.
  - See
    [design §§6, 8, and 9.1](design-doc.md#91-planned-validation-of-existing-code).
  - Success: generated changes in build/lock/activity evidence, usage, and
    dry-run
    selection preserve protected targets, stop after observed recovery, and keep
    filesystem outcomes isolated. Use simple generated sequences first and a
    rule-based state machine only for history-dependent gaps; run real production
    planner/reclaimer methods with isolated recording ports. Retain shrunk
    regressions and distinguish modelled interleavings from actual OS races.

## 2. Preview both cleanup strategies through one command

Idea: if a combined preview can find old groups inside recent targets while
preserving legacy whole-target decisions and avoiding a second planning walk,
the design supplies useful reclamation visibility before partial mutation is
enabled.

### 2.1. Discover reclaimable groups without repeating target planning

This step answers whether one inventory can serve both age models without
changing existing whole-target eligibility. It unlocks the combined operation
planner. See design-doc.md §11.4.

- [ ] 2.1.1. Add the reusable target inventory and adapt whole-target planning
      to
  its legacy-compatible activity projection.
  - Requires 1.1.2, 1.2.2, 1.3.2, and 1.3.3.
  - See
    [design §11.4](design-doc.md#114-shared-inventory-and-component-responsibilities).
  - Success: marker exclusion, empty targets, errors, and injected legacy
    adapters retain their documented behaviour; instrumentation shows one
    ordinary planning traversal per accepted target.
- [ ] 2.1.2. Extract profile-local fingerprint groups and conservative age
  evidence from that inventory.
  - Requires 2.1.1.
  - See
    [design §§11.2–11.4](design-doc.md#112-cargo-sweep-reference-and-compatibility-boundary).
  - Success: supported direct, custom, and target-triple profiles match the
    pinned time-selection corpus. Orphans, links, cross-device members, empty
    fingerprints, and unsupported atime/layout evidence remain unselected.

### 2.2. Make inherited retention and disjoint plans inspectable

This step answers whether operators can understand the eventual mutation scope
from one CLI invocation. It informs the destructive slice while keeping its
implementation gate explicit. See design-doc.md §§11.3, 11.5, 11.8.

- [ ] 2.2.1. Implement artefact configuration and CLI overrides with late TTL
  inheritance and additive day-duration parsing.
  - Requires 1.1.2, 1.3.4, and 2.1.1.
  - See
    [design §11.3](design-doc.md#113-retention-policy-and-proposed-configuration)
    and [proposed policy](design/artefact-policy-proposal.toml).
  - Success: missing artefact TTL follows customized normal, pressure, and
    critical settings, including CLI changes; explicit `7d` means 604,800
    seconds. Invalid input fails before mutation, and rollback switches have
    deterministic precedence.
- [ ] 2.2.2. Deliver a combined CLI dry run with disjoint whole-target and group
  plans and separately labelled planned counts.
  - Requires 2.1.2 and 2.2.1.
  - See
    [design §§11.5 and 11.8](design-doc.md#115-disjoint-planning-and-common-recovery).
  - Success: a recent target can show old groups; a whole-target plan contains
    no descendants. Preview writes no payload or mode state, passes stable
    rendering snapshots, and cannot silently request an unavailable mutating
    strategy during the preview-only development stage.

## 3. Reclaim old groups from retained targets without disrupting builders

Idea: if a retained target survives with its fresh and unknown outputs intact
and Cargo rebuilds correctly after guarded group pruning, partial cleanup can
reclaim useful space without a full target eviction.

### 3.1. Make a group mutation require a live safety capability

This step answers whether mutation remains confined and coordinated while
agents start work and filesystem entries change. It unlocks real combined
cleanup. See design-doc.md §11.6.

- [ ] 3.1.1. Implement non-blocking profile mutation leases for the certified
  lock protocols, reusing existing enumeration and safety helpers.
  - Requires 1.2.1 and 2.1.2.
  - See
    [design §11.6](design-doc.md#116-concurrent-build-guards-and-mutation-protocol).
  - Success: only acquired leases authorize mutation. Busy, unsupported, and
    unknown results preserve groups; failed multi-lock acquisition releases
    earlier locks, and cancellation leaves no lock held or replacement created.
- [ ] 3.1.2. Implement group revalidation and confined member removal with
  fingerprint metadata removed last.
  - Requires 1.3.5, 3.1.1, and 2.2.2.
  - See
    [design §§11.4 and 11.6](design-doc.md#114-shared-inventory-and-component-responsibilities).
  - Success: generated member/path changes fail closed before mutation;
    injected partial removal failures preserve remaining fingerprint evidence.
    Targets, containers, lock files, unknown members, and external paths survive.

### 3.2. Deliver retained-target pruning with compatible outcomes

This step answers whether the complete command can combine both operation kinds
in normal mode and still produce valid Cargo output and truthful counts. Its
outcome establishes the mutation mechanism that pressure recovery will use. See
design-doc.md §§11.5–11.9.

- [ ] 3.2.1. Integrate whole-target-first and retained-target group mutation
      into
  the existing normal-mode sweep.
  - Requires 3.1.2.
  - See [design §11.5](design-doc.md#115-disjoint-planning-and-common-recovery).
  - Success: a mixed-age fixture loses a cold whole target and only the old
    group inside a recent retained target. A busy target is preserved, and an
    invalidated whole-target operation never gains a fallback descendant plan.
- [ ] 3.2.2. Extend human summaries, bounded events, and result projections with
  separate group and member outcomes.
  - Requires 3.2.1.
  - See
    [design §11.8](design-doc.md#118-reporting-compatibility-and-deployment).
  - Success: target counts and two-value unpacking retain their meaning;
    planned, completed, failed, and preserved groups are distinguishable.
    Partial failures never count as completed groups or measured freed bytes.
- [ ] 3.2.3. Deliver packaged-command and real-Cargo rebuild acceptance for
  supported retained-target layouts.
  - Requires 3.2.2 and 1.2.2.
  - See [design §11.9](design-doc.md#119-verification-and-acceptance-gates).
  - Success: permitted pruning matches the supported upstream corpus, then
    build/check/test and a subsequent no-change build succeed. Explicitly
    account for conservative differences and unchanged final outputs.

## 4. Recover space under pressure while parallel agents keep working

Idea: if measured recovery stops both strategies together, including after
partial failures and concurrent writes, the combined pass improves capacity
reclamation without bypassing build protection in critical mode.

### 4.1. Give both strategies one filesystem recovery budget

This step answers whether partial cleanup participates in the existing
hysteretic policy rather than running after recovery as an unrelated cleaner.
It unlocks the completed feature's pressure behaviour. See design-doc.md §11.5.

- [ ] 4.1.1. Integrate group mutation into common pressure and critical recovery
  control, including failed attempts that may already have changed files.
  - Requires 3.2.2.
  - See [design §11.5](design-doc.md#115-disjoint-planning-and-common-recovery)
    and [recovery cases](design/combined-sweep-cases.json).
  - Success: whole-target recovery suppresses groups, group recovery suppresses
    later groups, equality does not recover, and an unreadable reread stops the
    filesystem unresolved. A partially failed attempt still triggers a reread.
- [ ] 4.1.2. Preserve filesystem isolation, mode persistence, and TTL
      inheritance
  throughout mixed-operation recovery.
  - Requires 4.1.1 and 2.2.1.
  - See
    [design §§5 and 11.3–11.5](design-doc.md#5-pressure-policy-and-persisted-state).
  - Success: one filesystem's recovery or failure does not alter another's
    budget; selected-mode state retains its existing format. Custom and default
    pressure TTLs apply equally to inherited artefact retention.

### 4.2. Demonstrate concurrency, interaction coverage, and resource cost

This step answers whether the design remains useful when agents share a host,
not just when one process cleans an idle fixture. Results inform release and
any performance work needed before default activation. See design-doc.md
§§11.7–11.9.

- [ ] 4.2.1. Deliver a combinatorial acceptance harness for the combined policy
  and its highest-risk failure sequences.
  - Requires 4.1.2 and 3.2.3.
  - See [design §11.9](design-doc.md#119-verification-and-acceptance-gates).
  - Success: pairwise mode/state, TTL, ownership, layout, atime, lease, dry-run,
    and failure coverage is recorded. Directed critical/new-build,
    partial-failure/recovery, and invalidated-parent cases pass; property
    generators verify disjointness and scope without claiming race freedom.
- [ ] 4.2.2. Deliver many-agent stress and concurrent-Cargo acceptance with
  isolated and shared target directories.
  - Requires 4.2.1.
  - See
    [design §§11.6–11.9](design-doc.md#116-concurrent-build-guards-and-mutation-protocol).
  - Success: active or queued builders remain protected, independent cold
    targets still reclaim space, cleaner invocations serialize, and cancellation
    releases leases. Compare structural traversal counts, process snapshots,
    memory, and time to safe reclamation with the phase 1 workload baseline;
    investigate any extra ordinary root or target planning traversal.

## 5. Activate the combined pass through the existing deployment

Idea: if ordinary commands and the existing systemd timer can enable combined
cleanup with inherited TTLs and a verified rollback, operators can adopt it
without introducing a second scheduled cleaner or hiding unresolved pressure.

### 5.1. Establish the default-on release and rollback contract

This step answers whether the completed feature is both deployable and
reversible at the policy boundary. It closes the core combined-cleanup scope.
See design-doc.md §§11.3 and 11.8.

- [ ] 5.1.1. Enable combined cleanup by default after the compatibility and
  concurrency gates, and publish the migration and rollback guidance.
  - Requires phase 4.
  - See
    [design §11.8](design-doc.md#118-reporting-compatibility-and-deployment).
  - Success: existing configurations and the ordinary command select both
    strategies with the same effective TTL. `enabled = false` and
    `--no-artefacts` restore whole-target-only behaviour; working examples and
    the users' guide describe implemented flags rather than proposed ones.
- [ ] 5.1.2. Extend opt-in systemd acceptance to exercise one combined scheduled
  invocation, interruption, diagnostics, and rollback.
  - Requires 5.1.1.
  - See [design §11.8](design-doc.md#118-reporting-compatibility-and-deployment)
    and [systemd operation](users-guide.md#install-a-systemd-user-timer).
  - Success: isolated test-owned units prune an eligible group and retain its
    target, preserve status 3 as a visible unresolved-pressure failure, and
    prove no second cleaner or timer is required. Host-free validation remains
    the default; actual user-manager acceptance stays explicitly opt-in.

## 6. Evaluate extensions after the combined policy is established

Idea: if the inherited-TTL combined pass is already safe to operate, additional
eviction policies can be judged independently instead of delaying or silently
broadening the requested cleanup semantics. These tasks are deferred decision
gates, not part of the core release acceptance.

### 6.1. Evaluate additional cargo-sweep selection criteria

This step answers whether toolchain, stamp, and quota policies add enough value
to justify new contracts. Its result informs a separate design revision. See
design-doc.md §11.10.

- [ ] 6.1.1. Evaluate toolchain filtering, stamp/file workflows, size quotas,
      and
  zero-age `--all` as separate proposals.
  - Requires phase 5.
  - See [design §11.10](design-doc.md#1110-deferred-scope).
  - Success: each accepted addition has explicit ownership, policy precedence,
    pressure interaction, and verification requirements before implementation.

### 6.2. Evaluate broader Cargo-layout and ownership coverage

This step answers whether unsupported output can be classified without
weakening the current deletion boundary. Its result constrains later scope. See
design-doc.md §11.10.

- [ ] 6.2.1. Evaluate split build directories, orphan recovery, incremental and
  example outputs, unhashed products, and finer profile ownership separately.
  - Requires phase 5.
  - See [design §11.10](design-doc.md#1110-deferred-scope).
  - Success: additions require new compatibility evidence and an accepted
    confinement/guard decision; critical mode never becomes a protection bypass.
