# Repository layout

`rust_cleanup/` contains the standard-library-only cleanup tool. The public
exports in `__init__.py` preserve the imported API, and `cli.py` implements the
installed `rust-cleanup` command.

- `scan_config.py`, `scan_root_expressions.py`, `roots.py`, and `xdg.py` resolve
  configuration and scan roots.
- `discovery.py`, `cargo_layout.py`, and `sweep_discovery.py` identify
  disposable Cargo output and refuse source trees.
- `pressure.py`, `planning.py`, `plans.py`, and `state.py` select and persist
  filesystem pressure policy.
- `active_build.py`, `proc_inspection.py`, and `cargo_locks.py` protect live
  build output.
- `sweep.py`, `reclaim.py`, `reclamation.py`, and `filesystem.py` coordinate and
  revalidate deletion through injectable filesystem interfaces.
- `observability.py`, `sweep_telemetry.py`, and `timing.py` report bounded
  lifecycle telemetry.

`tests/` holds imported unit, property, snapshot, and workflow tests alongside
existing repository automation tests. `tests/fixtures/rust-cleanup.timer.j2` is
a copy of the upstream Ansible timer template for opt-in host acceptance tests.
`examples/systemd/` contains copyable user service and timer units based on the
dev-env-rocky deployment; installation and operation are documented in the
[users' guide](users-guide.md#install-a-systemd-user-timer). `scripts/` and
`.github/` retain the repository's generated automation.

Import provenance and validation conventions are recorded in the
[Developers' guide](developers-guide.md#source-import).
