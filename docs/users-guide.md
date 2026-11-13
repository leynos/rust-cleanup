# rust-cleanup Users' Guide

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
