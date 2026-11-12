# Developer guide

This guide explains the contributor workflow for the generated project.

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
