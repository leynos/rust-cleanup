# rust-cleanup

*Reclaim stale Rust build output according to filesystem pressure.*

`rust-cleanup` scans repositories and worktrees, identifies Cargo output, and
removes eligible targets oldest first. Busy builds, uncertain activity, and
source-tree markers keep output off the deletion list.

______________________________________________________________________

## Why rust-cleanup?

Build output accumulates across worktrees. Cleaning it project by project is a
chore; deleting directories because they happen to be named `target` is a poor
substitute for a policy.

- Set scan roots once, then assess each backing filesystem separately.
- Keep a longer inactivity window during routine use and shorten it when
  capacity runs low.
- Preview eligible removals and see why other targets remain protected.

______________________________________________________________________

## Quick start

### Installation

From a checkout, with [uv](https://docs.astral.sh/uv/) installed:

```bash
uv tool install --python 3.14 .
```

The tool requires Linux and Python 3.14 or newer. It uses Python's standard
library at runtime; it does not require Cargo to run. See the
[installation guide](docs/users-guide.md#install-from-a-checkout) for a virtual
environment alternative and `PATH` setup.

### Basic usage

Preview the current directory before applying any cleanup:

```bash
rust-cleanup --dry-run --verbose .
```

Supply another directory, or several, to choose the scope. With no paths, the
built-in roots are `~/.lody/repos` and `~/Projects`, unless configuration
replaces them. Remove `--dry-run` to perform cleanup.

Dry runs preserve targets and mode state, but can create an invocation lock
file. A real run checks activity again and can stop earlier when capacity
recovers. The [users' guide](docs/users-guide.md) explains the policy and its
concurrency limits.

______________________________________________________________________

## Features

- Cargo-layout evidence and source-tree refusal, including custom output names.
- Per-filesystem normal, pressure, and critical policies with persisted
  hysteresis.
- Active-build inspection, Cargo-lock probes, and checks immediately before
  deletion.
- Configurable roots, retention windows, and capacity thresholds in
  [TOML](examples/config.toml).
- Human diagnostics and bounded structured events for scheduled operation.

______________________________________________________________________

## Learn more

- [Users' guide](docs/users-guide.md) – installation, configuration, and
  operation.
- [Technical design](docs/design-doc.md) – architecture, invariants, and known
  limits.
- [Draft roadmap](docs/roadmap.md) – planned artefact pruning in the same
  sweep as whole-target cleanup.
- [Developer guide](docs/developers-guide.md) – development and validation.
- [Documentation contents](docs/contents.md) – the complete documentation index.

______________________________________________________________________

## Licence

ISC – see [LICENSE](LICENSE).

______________________________________________________________________

## Contributing

Contributions are welcome. Read the [developer guide](docs/developers-guide.md)
and [repository instructions](AGENTS.md) before changing code or documentation.
