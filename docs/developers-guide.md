# Developer guide

## Source import

The initial implementation and test suite were imported from
`dev-env-rocky/python/rust_cleanup` at revision
`8dbf0e665127b9cd8b1dda3a0f48a22e6717abdd`. Runtime modules were copied into
the existing top-level `rust_cleanup/` package. Explicit status reads preserve
inspection errors on Python 3.14, whose `Path.exists()` and `Path.is_file()`
predicates suppress those errors. The imported regression tests cover
unreadable roots and linked candidates. Explicit type narrowing records the
validated TOML table and string-list contracts for the pinned type checker. The
console entry point remains `rust-cleanup = rust_cleanup:main`. This repository
retains its generated Python 3.14 baseline and quality tooling; the original
package declared Python 3.12 or newer.

The runtime uses only the Python standard library. Hypothesis supplies the
imported property tests, Syrupy preserves the CLI snapshot contracts, and
Jinja2 renders the timer acceptance fixture. Tests remain under top-level
`tests/`. The subprocess locking test uses this repository's package root. The
original Ansible timer template is retained under `tests/fixtures/` for the
timer tests; Ansible provisioning remains in `dev-env-rocky`.

Ordinary tests do not modify systemd units. The host acceptance test requires
`RUST_CLEANUP_HOST_ACCEPTANCE=1` and an available Linux systemd user manager.
Run it only on a suitable test host. The cleaner itself relies on Linux process
inspection and advisory filesystem locks.

See [Repository layout](repository-layout.md) for the imported component
boundaries and [Users' guide](users-guide.md) for cleanup policy and usage.

This guide explains the contributor workflow for the generated project.

## Static analysis exceptions

Vulture checks the top-level test tree. Its configured name exceptions cover
pytest plugin registration and the two named autouse fixtures, Python's
`__call__` protocol, and `RunOptions` fields consumed through keyword expansion
into `CleanupRun`. These are runtime entry points and typed-dictionary keys;
removing them changes test behaviour. Other test helpers remain checked.

Subprocess tests use fixed executable arguments without a shell. Their narrow
Ruff annotations describe why a separate process is necessary to test lock
contention, or why importing `CompletedProcess` is harmless.

### Import duplication follow-up

The imported test suite contains repeated arrangements for separate safety
contracts. Its source import retains those scenarios and records reviewed
families in `tool.duplication_gate.allow`, with a reason, the imported
locations, and a link to this follow-up. The whole checkout remains in the
detector's scope with the existing mode and size threshold.

The allowlist format supports path and symbol keys, rather than content hashes.
A named entry covers that symbol; an unnamed detector location requires a path
key. Path keys can therefore cover later families wholly within the same listed
paths. Treat these entries as an import baseline, not as permission to add
further duplication. Review changes in those paths for repeated logic.

Reduce that baseline in focused refactors after the import:

- Parameterize malformed-state and account-home cases while retaining their
  distinct corrupt inputs and semantic assertions, then remove their entries.
- Consolidate repeated CLI isolation and invocation arrangements using the
  existing `conftest.py` and rendering helpers, then remove matching entries.
- Reuse telemetry sweep arrangements while keeping lifecycle, timing, and
  secrecy assertions visible in each test, then remove matching entries.
- Review filesystem, process, and scan-root families for reusable setup. Keep
  scenario-specific safety assertions separate, and remove obsolete entries.
- Retain deliberate API re-exports, explicit imports, and separate telemetry
  event schemas only where their recorded reasons still apply.

Run `make lint` and `make test` after each refactor. Do not replace these
entries with directory exclusions or a higher detector threshold.

## Local workflow

The public entrypoint for formatting, linting, typechecking, tests, and
spelling is `make all`. Narrower Make targets may be invoked when investigating
a specific failure, and changes should be reconciled with the aggregate gate
before being considered complete.

`make lint` checks tracked and untracked Python sources discovered by Git,
including sources under hidden action directories. It runs Ruff 0.16.8,
Interrogate for 100% docstring coverage, Pylint, the DF12 Python lint plugin,
Ambrleaks on `tests/`, the strict Skylos gate and Vulture on test helpers, the
shared Nose duplication gate, and workflow/action validation. Pylint's base
sweep runs on PyPy 3.12 when the selected Python baseline is exactly 3.12; for
a newer baseline it runs on CPython at that baseline. The baseline is the
selected `python_version`, with 3.12 as the minimum supported version. The DF12
Python Lints v0.4.0, Ambrleaks, Skylos, Vulture, and duplication tools run as
isolated tools on Python 3.14.

With default Git source discovery, `fmt`, `check-fmt`, `lint-python`, and
`typecheck` stop with a diagnostic before running tools if Git or repository
metadata is unavailable. GNU Make 4.2 or newer captures one Git result and
checks its `.SHELLSTATUS` immediately; the tools share that captured source
list. `help` and `build` do not depend on source discovery; initialize the
generated project with `git init` before its first quality-gate run.

Rust-enabled projects declare only the PyO3 module initializer as a runtime
entrypoint. The native import tests exercise that boundary, and unused sibling
functions remain blocking.

`make typecheck` runs `ty`, pinned at `0.0.56`: unpinned installations broke
repositories when ty 0.0.56 landed. Bump the pin deliberately — update the
version and fix any new diagnostics in the same pull request.

Ruff and `ty` run from isolated tools on Python 3.14; their versions are pinned
to Ruff 0.16.8 and `ty` 0.0.56. `make check-fmt` and `make fmt` also check or
apply Markdown table formatting with mdtablefix 0.6.1.

`make lint-actions` validates YAML under `.github/` with yamllint 1.38.0,
workflow syntax with actionlint, composite actions with action-validator, and
shell scripts with shellcheck. `make markdownlint` runs markdownlint-cli2 and
the spelling gate. Spelling uses `typos-config-builder` with scope `all`.

Run `make audit` as the dependency vulnerability gate. It runs `pip-audit` for
Python dependencies, and Rust-enabled projects also run `cargo audit` from the
`rust_extension` crate directory.

## Automation scripts

The [Scripting standards](scripting-standards.md) document provides guidance
for adding or updating helper scripts. New and updated scripts are expected to
use `Cyclopts` for command-line interfaces, `cuprum` for typed and
catalogue-bound external command execution, `pathlib` for filesystem paths, and
`cmd-mox` for tests that mock external executables.

Script changes should update the scripting guide when they introduce a new
convention, command catalogue, testing pattern, or operational expectation that
future contributors need to follow.

## GitHub Actions

The generated repository includes GitHub Actions workflows and local composite
actions under `.github/`.

- `.github/workflows/ci.yml` runs on pushes to `main` and on pull requests. It
  sets up the selected `python_version` baseline (at least 3.12), installs
  `uv`, validates the generated `Makefile` with `mbake`, installs CLI tools,
  then runs `make build`, `make check-fmt`, `make lint`, `make typecheck`,
  `make markdownlint`, and `make audit` except for Dependabot pull requests via
  `if: github.actor != 'dependabot[bot]'`. The Markdown target includes
  repository-wide spelling. CI provisions Go and Rust/Cargo toolchains for
  installing CLI tools, then delegates coverage generation to the shared
  coverage action. Rust lint and test tools are installed and
  `rust_extension/Cargo.toml` is passed to coverage only when the Rust
  extension is enabled.
- `.github/workflows/audit.yml` runs `make audit` against the default branch
  weekly as the compensating control for the Dependabot CI bypass.
- `.github/workflows/act-validation.yml` runs rendered workflow validation in a
  separate workflow. It installs `act`, checks Docker availability, and runs
  `make test WITH_ACT=1` outside the coverage path.
- `.github/workflows/release.yml` publishes wheels when a `v*.*.*` tag is
  pushed. It builds a pure Python wheel, creates a GitHub release with
  generated release notes, downloads wheel artefacts, and uploads them to the
  tag release.
- `.github/workflows/build-wheels.yml` is a reusable workflow for extension
  builds. It accepts a Python version and builds wheels across Linux, Windows,
  and macOS architectures via `.github/actions/build-wheels`.
- `.github/workflows/get-codescene-sha.yml` is manually dispatched. It fetches
  the CodeScene coverage CLI installer, computes its SHA-256 digest, and writes
  the result to the `CODESCENE_CLI_SHA256` repository variable.

- `.github/workflows/mutation-testing.yml` runs daily at 09:30 UTC and supports
  manual dispatch. It delegates to reusable workflows from
  `leynos/shared-actions`; stagger the generated cron schedule before adopting
  it alongside other repositories.

  The mutmut job is generated only when the minimum Python version is 3.13 or
  newer because the shared workflow helpers require Python 3.13 or newer.

- `.github/actions/build-wheels` wraps `cibuildwheel` with `uvx` and uploads
  architecture-specific wheel artefacts.
- `.github/actions/pure-python-wheel` builds a pure Python wheel with
  `uv build --wheel` and uploads the resulting artefact.
- `.github/dependabot.yml` enables dependency update pull requests for GitHub
  Actions and Python packages. Rust-enabled projects also receive Cargo updates.

CI provisions Go 1.27 before installing Actionlint 1.7.12, whose minimum is Go
1.25; module caching is disabled because the project has no Go module. It also
provisions stable Rust/Cargo for the `mdtablefix` `cargo-binstall` installer in
both pure-Python and Rust-enabled projects. These prerequisites do not change
the selected `python_version` application baseline. In Rust-enabled projects,
the Rust lint and test cache uses the `rust_extension -> target` workspace
while retaining the tool-cache directories; Rust-specific cache configuration
and tools remain conditional on the extension.

The `CS_ACCESS_TOKEN` secret must be configured when CodeScene coverage upload
is required. The `CODESCENE_CLI_SHA256` variable should be populated using the
refresh workflow, so CI can verify the downloaded CodeScene installer before
upload.

## Shared spelling configuration

Run `make spelling` to enforce en-GB-oxendict spelling. The shared
`typos-config-builder` gate regenerates `typos.toml` from the live estate
dictionary in `leynos/agent-helper-scripts` and the `typos.local.toml` overlay
on every run, then checks tracked Markdown. A word added to the shared
dictionary therefore needs no change here, and `typos.toml` must never be drift
checked in continuous integration. `typos.toml` is generated output, but it is
tracked: commit the regenerated file after a `make spelling` run changes it.
The local cache that keeps the gate usable when the authority is temporarily
unreachable is ignored by Git. Add only narrow project-specific terms and
exclusions to `typos.local.toml`; never edit generated `typos.toml` by hand.
