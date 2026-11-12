# rust-cleanup Users' Guide

`rust-cleanup` removes stale Rust build output across every configured scan
root. It scans the roots once, makes decisions per backing filesystem, and only
removes a directory it can positively identify as Cargo output.

## Scan roots

The default roots are `~/.lody/repos` and `~/Projects`. They can be replaced in
the configuration file:

```toml
[scan]
roots = ["~/.lody/repos", "~/Projects", "/srv/work"]
```

A configured list replaces the built-in defaults rather than extending them, so
repeat any default that should stay covered. A leading `~` or `~/` expands
against the home directory; everything else, including a `$VAR` reference, is
kept literal with no shell evaluation. Command-line roots replace both the file
and the defaults. A configured root that is missing or is not a directory is
reported on standard error and skipped, so one unavailable root never
suppresses the rest. A missing *built-in* default is the one exception: the
host simply may not have that directory, so it is reported only under
`--verbose` rather than on every timed run. Repeated and overlapping roots are
collapsed to the outermost, so no tree is walked twice, and an unreadable root
stops the run rather than narrowing it silently. A symbolic link loop, or a
root below a directory that cannot be read, counts as unreadable; an explicit
root in either state is reported as a path that cannot be inspected.

## What counts as build output

Discovery considers a directory named `target`, or any directory holding
`.rustc_info.json`; every other directory is left alone. A considered directory
is accepted when it carries a `CACHEDIR.TAG` marker, or when it shows the
layout Cargo leaves behind: `.rustc_info.json` plus a profile directory holding
at least one of `.cargo-lock`, `.fingerprint`, `deps`, or `build`. The layout
rule is what recognizes unmarked targets and custom names such as
`ptq-pr43-cargo-target`. Accepting any single artefact is deliberate:
`cargo clean` removes `.fingerprint` and `deps` while leaving the others.

Being considered is not acceptance: a bare name, including `target` or `debug`,
never qualifies on its own. A directory showing `Cargo.toml` or `.git` is
treated as a source tree and refused, as are lookalikes and symbolic links;
links are never followed out of scope. The source-tree refusal is checked
before the cache marker, so a checkout carrying `CACHEDIR.TAG` is refused as
well. Every refusal carries a fixed reason token, and an empty sweep reports
how many candidates it declined, so a quiet filesystem is distinguishable from
a discovery rule that has stopped matching.

## Pressure policy

The default policy follows the backing filesystem's used capacity. The
persisted mode is hysteretic, so recovery thresholds are lower than entry
thresholds and a one-off deletion cannot immediately downgrade critical mode.

| Mode     | Enter        | Recover below | Inactive for more than |
| -------- | ------------ | ------------- | ---------------------- |
| Normal   | below 80%    | n/a           | 2 hours                |
| Pressure | at least 80% | 75%           | 10 minutes             |
| Critical | at least 95% | 90%           | 2 minutes              |

Targets are ordered oldest first within each filesystem, with canonical path as
a deterministic tie-breaker. Pressure and critical mode re-read utilization
after each deletion and stop as soon as their recovery watermark is reached.

## Protection

The cleaner snapshots Linux build processes once per sweep and protects targets
owned by live Cargo, Rust compiler, linker, Kani, or Verus work, including
output redirected through `CARGO_TARGET_DIR`, `CARGO_BUILD_TARGET_DIR`, or
`--target-dir`. Immediately before each deletion it takes a fresh process-table
reading, probes the Cargo build lock, revalidates the target's newest
modification time, re-checks the layout evidence, and confirms the path is
still a real directory inside a scan root. Each failure preserves the target
and is recorded under a fixed reason:

| Reason                 | Meaning                                               |
| ---------------------- | ----------------------------------------------------- |
| `active-build`         | A build process owns or targets this output.          |
| `cargo-lock`           | A Cargo build lock is held or could not be read.      |
| `unknown-activity`     | The newest modification time could not be read.       |
| `revalidation-changed` | The target changed after it was planned for deletion. |
| `recent-activity`      | The target is newer than the active cutoff.           |
| `evidence-lost`        | The layout evidence is gone, or the path left scope.  |
| `usage-unavailable`    | Filesystem usage could not be measured.               |

The lock probe opens only an existing regular `.cargo-lock` directly under a
profile directory, read-only, without creating anything; `CARGO_HOME`
package-cache locks are never inspected. Lock absence is not treated as
evidence of inactivity.

## Configuration and state

The optional TOML configuration is loaded from
`${XDG_CONFIG_HOME:-$HOME/.config}/rust-cleanup/config.toml`; an
`XDG_CONFIG_HOME` override is honoured only when it is a non-empty absolute
path, and an empty or relative value falls back to `$HOME/.config`. Missing
files use the defaults. Command-line options override TOML, and TOML overrides
built-in defaults. The `[scan]` table is described under
[Scan roots](#scan-roots):

```toml
[cleanup.normal]
stale_after = "2h"

[cleanup.pressure]
enter_percent = 80
exit_percent = 75
stale_after = "10m"

[cleanup.critical]
enter_percent = 95
exit_percent = 90
stale_after = "2m"
```

Durations accept positive integer `s`, `m`, and `h` suffixes. Unsafe or
malformed configuration, including recovery thresholds that are not lower than
their entry thresholds, fails before any deletion. Use `--config PATH` to test
or explicitly select a file; the equivalent `--*-stale-after` and `--*-percent`
options take the highest precedence.

The selected mode is written atomically per canonical mount point and device
identity to `${XDG_STATE_HOME:-$HOME/.local/state}/rust-cleanup/state.json`.
Malformed state only emits a warning and causes a freshly derived mode to be
used. A non-blocking advisory lock at `$XDG_RUNTIME_DIR/rust-cleanup.lock`
prevents manual commands racing the systemd timer; when `XDG_RUNTIME_DIR` is
unset, as in cron or a minimal service manager, the lock falls back to the
private state directory. Contention is a successful no-op: the command reports
it and exits with status 0 without starting a sweep.

Install it with pip from this directory:

```bash
python -m pip install .
```

Run it against the configured roots, or against specific ones:

```bash
rust-cleanup
rust-cleanup /path/to/worktree /path/to/other
```

Use `--dry-run` to report the selected mode and exact oldest-first deletion
order without changing targets or state.

When pressure or critical mode cannot reach its recovery watermark because no
safe target exists, `rust-cleanup` emits a prominent warning and exits with
status 3. This is an emergency capacity signal for the systemd journal, not a
substitute for placing disposable build output on a separate filesystem.

## Quality Gates

Before the first quality-gate run, initialize Git at the generated project root
with `git init`. Python source discovery and Markdown table formatting use Git
repository metadata.

Generated projects use `make all` as the standard local quality gate. It runs
these targets in order:

- `build`: create the local virtual environment and install development
  dependencies with `uv sync --group dev`.
- `check-fmt`: check Ruff formatting for Python sources and, when Rust is
  enabled, `cargo fmt` for the Rust extension.
- `lint`: run `lint-python` and, when Rust is enabled, `lint-rust`.
- `typecheck`: run `ty check`.
- `test`: run pytest and, when Rust is enabled, Rust tests.
- `spelling`: regenerate `typos.toml` and check repository-wide spelling.

Run `make markdownlint` separately to lint Markdown and run the same spelling
gate. Run `make audit` separately to check dependencies with `pip-audit` and,
when Rust is enabled, `cargo audit`.

The Python baseline is `3.14` (minimum: 3.12).

Git discovers Python sources from tracked and untracked files, including hidden
action directories. `lint-python` runs Ruff 0.16.8 and Interrogate for 100%
docstring coverage, followed by the pinned base Pylint (`PYLINT_VERSION`). It
uses the interpreter selected for the baseline:

uv-managed CPython 3.14 (`PYLINT_PYTHON`)

The base sweep keeps `syntax-error` enabled, so a module that the interpreter
cannot parse fails linting rather than being skipped.

The DF12 Python Lints v0.4.0 Pylint plugin adds its configured project rules.
DF12 Pylint, Ambrleaks, Skylos, Vulture, and the duplication detector run as
isolated tools on Python 3.14. Ambrleaks checks `tests/`; Skylos checks the
repository, and Vulture checks test helpers. The duplication gate installs its
pinned Nose detector with the shared verified installer before checking
duplication families. `make lint-actions` validates GitHub Actions sources with
actionlint, action-validator, yamllint, and shellcheck.

Ruff and `ty` run as isolated tools on Python 3.14; `ty` is pinned to 0.0.56.
The Makefile pins mdtablefix to 0.6.1 for Markdown table formatting. CI
installs these CLI tools and runs `make markdownlint`.

The spelling target regenerates `typos.toml` from the live shared dictionary
and the `typos.local.toml` overlay on every run, so `typos.toml` is never drift
checked in continuous integration. Run `make spelling` directly when updating
documentation; commit the regenerated `typos.toml`. An ignored local cache
remains usable when the shared source is temporarily offline.

Pytest discovery is limited to the top-level `tests/` tree. Keep generated
project unit tests there rather than in package module directories or
`unittests/` subdirectories, because CI coverage runs through xdist-backed
SlipCover support.

When the Rust extension is enabled, `lint-rust` runs:

- `cargo doc` with warnings denied;
- `cargo clippy` with the generated Clippy configuration; and
- Whitaker with `whitaker --all`.

The generated Makefile never installs Whitaker; it fails with a clear error
when the wrapper is missing. Install it yourself with `whitaker-installer` (see
<https://github.com/leynos/whitaker>) before running local Rust linting.

## Dependency Auditing

Run `make audit` to check generated project dependencies for known
vulnerabilities. All generated projects run `pip-audit` against the Python
environment created by `uv sync --group dev`. CI skips `make audit` for
Dependabot pull requests; a weekly scheduled audit on the default branch is the
compensating control. Rust-enabled projects also run `cargo audit` from the
`rust_extension` crate directory.

## Scheduled Mutation Testing

The `.github/workflows/mutation-testing.yml` workflow runs mutation testing
daily at 09:30 UTC and can also be started manually from GitHub Actions. Adjust
the generated cron schedule to stagger it against other repositories.

For projects whose minimum Python version is 3.13 or newer, the workflow runs
mutmut against `rust_cleanup/`. The Python mutation job is omitted for older
baselines because the shared workflow helpers require Python 3.13 or newer.

## Rust Test Behaviour

Rust-enabled projects use `cargo nextest run` when `cargo-nextest` is
available. If `cargo-nextest` is not installed, the generated `test` target
falls back to `cargo test`. Rust documentation tests still run through
`cargo test --doc`.

If cargo is missing from the local environment, generated Rust test targets
fail early with a clear error instead of falling through to an unusable `cargo`
invocation.

## Local GitHub Actions Validation

The generated Makefile supports optional local workflow validation using
[`act`](https://github.com/nektos/act). When `act` is installed and Docker is
available, pass `WITH_ACT=1` to the `test` target:

```bash
make test WITH_ACT=1
```

This sets `RUN_ACT_VALIDATION=1` for the pytest invocation, enabling the
act-based integration tests that run the generated CI workflow locally. Omitting
`WITH_ACT` (or setting it to `0`) skips act validation; the rest of the test
suite runs unchanged.

## Cleaning Local State

Run `make clean` to remove local build and cache outputs, including `.venv`,
`.uv-cache`, `.uv-tools`, Python cache directories, coverage outputs, and Rust
`target` output when the Rust extension is enabled.
