# Documentation contents

[Documentation contents](contents.md) indexes the rust-cleanup guides and
engineering references.

## Project guides

- [Technical design](design-doc.md) explains the implemented architecture,
  deletion guards, verification targets, and limits.

- [Repository layout](repository-layout.md) describes the cleanup modules and
  test assets.
- [User guide](users-guide.md) covers installation, scan roots, retention,
  diagnostics, and scheduled operation.
- [Example configuration](../examples/config.toml) provides the default roots
  and retention policy in a reusable TOML file.
- [Developer guide](developers-guide.md) explains the contributor workflow and
  points maintainers to script automation standards.
- [Documentation style guide](documentation-style-guide.md) defines the
  spelling, structure, Markdown, Architecture Decision Record (ADR), Request
  for Comments (RFC), and roadmap conventions used by this documentation set.

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
