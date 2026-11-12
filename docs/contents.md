# Documentation contents

[Documentation contents](contents.md) indexes the rust-cleanup guides and
engineering references.

## Project guides

- [Technical design](design-doc.md) explains the implemented architecture,
  deletion guards, verification targets, and the proposed combined sweep.

- [Repository layout](repository-layout.md) describes the cleanup modules and
  test assets.
- [User guide](users-guide.md) covers installation, scan roots, retention,
  diagnostics, and scheduled operation.
- [Example configuration](../examples/config.toml) provides the default roots
  and retention policy in a reusable TOML file.
- [systemd service](../examples/systemd/rust-cleanup.service) and
  [timer](../examples/systemd/rust-cleanup.timer) provide deployment examples
  explained in the user guide.
- [Developer guide](developers-guide.md) explains the contributor workflow and
  points maintainers to script automation standards.
- [Documentation style guide](documentation-style-guide.md) defines the
  spelling, structure, Markdown, Architecture Decision Record (ADR), Request
  for Comments (RFC), and roadmap conventions used by this documentation set.

## Decisions and implementation planning

- [Draft roadmap](roadmap.md) sequences combined target and artefact cleanup
  through compatibility, preview, mutation, pressure, and deployment gates.
- [Proposed ADR 001](adr-001-combined-target-and-artefact-cleanup.md) compares
  integration choices and records the proposed shared-inventory design.
- [Proposed artefact policy](design/artefact-policy-proposal.toml) shows TTL
  inheritance and an explicit day-based override; it is not a current config.
- [Combined-sweep contract cases](design/combined-sweep-cases.json) specify
  TTL, timestamp, name, planning, and recovery witnesses for implementation.

## Engineering practice

- [Complexity antipatterns and refactoring strategies](complexity-antipatterns-and-refactoring-strategies.md)
  explains cognitive complexity, the bumpy-road antipattern, and refactoring
  approaches for maintainable code.
- [Local validation of GitHub Actions with act and pytest](local-validation-of-github-actions-with-act-and-pytest.md)
  explains how to validate workflow behaviour locally before relying on remote
  Continuous Integration (CI) runs.
- [Scripting standards](scripting-standards.md) explains the preferred Python
  scripting stack, command execution patterns, and test expectations for helper
  scripts.
