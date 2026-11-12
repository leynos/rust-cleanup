# rust-cleanup users' guide

This guide is for Linux developers and operators who want to remove stale Rust
build output across repositories or worktrees. It covers installation, preview,
configuration, diagnostics, and scheduled use. Maintainers should also read the
[developer guide](developers-guide.md)
and [technical design](design-doc.md).

## Install from a checkout

The package requires Python 3.14 or newer on Linux. It reads `/proc` to inspect
build processes and uses advisory filesystem locks. Its runtime dependencies
are all in Python's standard library; Cargo is not required to run the cleaner.
The commands below install the local checkout rather than a published package.

With [uv](https://docs.astral.sh/uv/), run from the repository root:

```bash
uv tool install --python 3.14 .
rust-cleanup --help
```

If the command is not on `PATH`, run `uv tool update-shell` and follow its
instructions, or invoke the executable from the directory printed by
`uv tool dir --bin`.

Alternatively, install into a Python virtual environment:

```bash
python3.14 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/rust-cleanup --help
```

Use the executable from that environment in subsequent examples when choosing
this installation method.

## Preview a cleanup

Start with a specific root. From a project checkout, this previews the current
directory without deleting its build output:

```bash
rust-cleanup --dry-run --verbose .
```

Replace `.` with the directory containing the projects to scan. Several roots
can be supplied in one invocation:

```bash
rust-cleanup --dry-run --verbose "$HOME/Projects" "$HOME/.lody/repos"
```

Each explicitly supplied root must exist and be a readable directory. Omit a
path that is absent on the host. Relative command-line paths resolve against
the current directory.

A dry run reports every currently eligible target in oldest-first order within
each filesystem. It writes neither target deletions nor mode changes. It still
inspects the host and acquires the invocation lock, so it can create a lock
file and its parent directory. A real run performs fresh checks and can stop
when usage recovers, so it can delete fewer targets than the preview lists.

## Run a cleanup

Remove `--dry-run` to apply the policy:

```bash
rust-cleanup --verbose .
```

With no positional roots, the command uses `[scan].roots` from configuration,
or the built-in `~/.lody/repos` and `~/Projects` roots when no list is
configured. A supplied root list replaces the configured roots for that
invocation.

The cleaner permanently removes accepted directory trees. It provides no
recycle bin or undo. Run it as the account that owns the disposable output;
process inspection and deletion permissions affect which targets it preserves.

## Choose scan roots

The optional configuration file is `$XDG_CONFIG_HOME/rust-cleanup/config.toml`,
falling back to `$HOME/.config/rust-cleanup/config.toml`. Only non-empty
absolute `XDG_CONFIG_HOME` values override the fallback. If `HOME` is unusable,
the tool tries the account's home-directory entry.

```toml
[scan]
roots = ["~/.lody/repos", "~/Projects", "/srv/work"]
```

The configured list replaces the defaults, so repeat any default that should
remain covered. `roots = []` requests no discovery. Duplicate and overlapping
roots collapse to the outermost canonical directory.

Configured expressions accept absolute paths and leading `~` or `~/`. They
reject relative paths, empty expressions, and another user's home shorthand.
There is no shell evaluation: `/srv/$WORK` keeps `$WORK` literally, and `*`
does not expand into a wildcard selection.

| Root condition                              | Behaviour                                    |
| ------------------------------------------- | -------------------------------------------- |
| Explicit path missing or not a directory    | Fail with status 1.                          |
| Configured path missing or not a directory  | Report and skip it; scan the other roots.    |
| Built-in default missing                    | Skip it; report it only with `--verbose`.    |
| Root cannot be inspected                    | Refuse the sweep.                            |
| Repeated, nested, or equivalent linked root | Keep the outermost canonical selection once. |

A symbolic link supplied as a root can resolve to a directory. Links found
inside the walk are not traversed for cleanup. The root directory itself is
never a deletion candidate.

## Recognize disposable output

A directory is considered when it is named `target` or contains
`.rustc_info.json`. It is accepted as build output when either:

- It has a recognized `CACHEDIR.TAG` file.
- It contains `.rustc_info.json` and a Cargo profile layout with at least one
  of `.cargo-lock`, `.fingerprint`, `deps`, or `build`.

A custom output name therefore needs `.rustc_info.json` to be considered. A
bare directory name or a marker on an unrelated cache does not automatically
put it in scope. Direct `Cargo.toml` or `.git` entries identify a source tree
and override cache evidence. The marker check does not validate a cache-tag
signature or prove ownership.

Discovery omits symbolic links, source trees, unmarked candidates without
evidence, and lookalikes. Verbose output shows their fixed reasons: `symlink`,
`source-tree`, `unmarked-no-evidence`, and `lookalike`.

The walk skips `.git`, `.svn`, `.hg`, `node_modules`, `__pycache__`,
`.pytest_cache`, `.cargo`, and `.rustup`. It does not clean Cargo's downloaded
package cache or installed Rust toolchains.

## Configure retention and pressure

Copy [the configuration example](../examples/config.toml) to the configuration
path, or select it explicitly:

```bash
rust-cleanup --config examples/config.toml --dry-run --verbose .
```

The example contains the built-in retention settings:

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

| Mode     | Initial entry           | Recovery requires usage below | Inactive for more than |
| -------- | ----------------------- | ----------------------------- | ---------------------- |
| Normal   | Below 80%               | No recovery target            | 2 hours                |
| Pressure | At least 80%, below 95% | 75%                           | 10 minutes             |
| Critical | At least 95%            | 90%                           | 2 minutes              |

Used capacity is the filesystem's used bytes divided by total bytes. A prior
mode affects the next selection: pressure remains active until usage falls
below its exit threshold, and critical mode remains active at or above its own
exit threshold. Reaching exactly 75% or 90% is insufficient to recover under
the defaults. See the
[transition table](design-doc.md#5-pressure-policy-and-persisted-state) for the
complete default state machine.

Each filesystem has its own plan. Eligible targets are ordered by newest
observed modification time, oldest first, with canonical path breaking ties.
Pressure and critical runs remeasure usage after each successful deletion and
stop when the recovery threshold is crossed. Normal runs apply retention to all
eligible targets. Only filesystems containing discovered output are assessed.

Durations require a positive integer followed by `s`, `m`, or `h`; examples are
`90s`, `10m`, and `2h`. Percentages must be integers from 0 through 100.
Recovery thresholds must be lower than their respective entry thresholds, and
the critical entry threshold must exceed the pressure entry threshold. Activity
at exactly the cutoff remains protected.

Command-line settings override TOML settings, which override defaults:

```bash
rust-cleanup --dry-run --normal-stale-after 4h --pressure-stale-after 20m .
```

Unknown TOML keys and malformed values fail before deletion. A missing config
file uses defaults, including when its path was supplied with `--config`; check
the file exists when selecting a custom policy.

## Understand preserved targets

The cleaner inspects Linux build processes and probes existing Cargo profile
locks. Immediately before a real deletion it checks build ownership, the lock,
activity, Cargo evidence, and root containment again. An uncertain process
snapshot can preserve every target. Output named by Cargo environment variables
or `--target-dir` is included in ownership detection.

| Protection reason      | Meaning                                                                               |
| ---------------------- | ------------------------------------------------------------------------------------- |
| `active-build`         | A recognized build process may own the output, or process inspection is uncertain.    |
| `cargo-lock`           | A Cargo lock is held or cannot be safely probed.                                      |
| `unknown-activity`     | The newest modification time cannot be established.                                   |
| `revalidation-changed` | Activity changed after planning.                                                      |
| `recent-activity`      | Activity is at or newer than the selected cutoff.                                     |
| `evidence-lost`        | Cargo evidence disappeared, or the path became linked, unresolvable, or out of scope. |
| `usage-unavailable`    | Initial filesystem usage could not be measured.                                       |

A missing lock does not prove that output is idle. These checks reduce races;
they cannot prevent a build starting or a directory changing after the last
check. The tool does not guarantee safety against deliberately concurrent tree
mutation. Dry runs do not perform the final destructive-path checks.

## Read output and exit statuses

Standard output contains the cleanup summary, planned deletions, and verbose
target details. Standard error contains filesystem decisions, warnings, and
JSON events prefixed with `rust-cleanup-event` and a space. `--verbose`
controls target detail; it does not enable or disable the structured events.

Filesystem diagnostics report the mount, usage before and after, previous and
selected modes, cutoff, candidate counts, deletion failures, and recovery
status. In a dry run, deletion counts represent planned removals and recovery
is reported as skipped. When initial usage is unavailable, zero-valued usage
fields are placeholders; the warning and `usage-unavailable` reason explain
that no measurement was obtained.

The structured events contain bounded tokens and counters rather than target
paths or raw exceptions. Human diagnostics can contain paths and error text.
Filtering event records does not make the complete stderr stream path-free.

| Status | Meaning                                                                                                    |
| ------ | ---------------------------------------------------------------------------------------------------------- |
| `0`    | Invocation completed without a fatal error or reported unresolved recovery; also used for lock contention. |
| `1`    | Invalid explicit root, incomplete scan, or a fatal cleanup, lock, or state-save error.                     |
| `2`    | Argument parsing, configuration, or home/configured-root resolution error.                                 |
| `3`    | A processed pressure or critical plan could not establish recovery below its watermark.                    |

Status 0 does not guarantee a deletion occurred or that every attempted removal
succeeded. Review the diagnostic `failed` count and warnings. Initial usage
failure can preserve all candidates while returning 0. A failed usage reread
after a deletion leaves recovery unresolved and can return 3. Deletions
completed before an error are not rolled back.

## State, locking, and scheduling

Selected modes persist at `$XDG_STATE_HOME/rust-cleanup/state.json`, falling
back to `$HOME/.local/state/rust-cleanup/state.json`. Empty or relative
`XDG_STATE_HOME` values use the fallback. Missing state derives fresh modes;
malformed state warns and is ignored. State is private mode `0600` after a
successful replacement. Dry runs do not save state.

The invocation lock is `$XDG_RUNTIME_DIR/rust-cleanup.lock` when that variable
is non-empty. Otherwise it is `rust-cleanup.lock` beside the state file. Unlike
config and state overrides, the runtime-directory value is currently used as
supplied: provide an absolute, private directory. A competing command using the
same lock path reports contention and returns 0 without a sweep.

The repository does not install a daemon or production systemd units. Its
systemd timer file under `tests/fixtures/` is an acceptance-test fixture. To
use an external scheduler:

- Invoke the installed executable by absolute path as the owning account.
- Set consistent `HOME`, configuration, state, and runtime-directory values
  for manual and scheduled runs so they share a lock and policy history.
- Capture both output streams and distinguish status 3 from an ordinary
  completed run.
- Preview the selected roots and policy before enabling real deletions.

A dry-run preview is not a reservation: later scheduled runs observe the host
again and can make different decisions.

## Troubleshoot a run

| Symptom                                    | Next action                                                                                                            |
| ------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------- |
| No candidates found                        | Use `--verbose`; check effective roots and omission reasons. Bare names and unsupported layouts remain untouched.      |
| Every candidate is protected               | Inspect the reasons; restricted `/proc` access can make build ownership uncertain.                                     |
| Status 3                                   | Read the mount's diagnostics, investigate protected output, and free space outside eligible targets if necessary.      |
| Missing custom configuration seems ignored | Check the path exists; missing files deliberately use defaults.                                                        |
| Malformed-state warning                    | Inspect the state file and permissions. Moving it aside discards hysteresis history and derives fresh modes next time. |
| State-save error after cleanup             | Correct directory permissions or storage failure; already removed targets cannot be restored by retrying.              |
| Initial usage unavailable                  | Investigate the filesystem or mount; all its candidates are preserved until usage can be measured.                     |
| Deletion failure                           | Inspect the reported target and error; recursive removal can have partially removed its contents.                      |

For implementation constraints and the remaining concurrency interval, read
[Failure modes and limits](design-doc.md#8-failure-modes-and-limits).

## Command reference

```text
rust-cleanup [OPTIONS] [PATH ...]
```

| Option                             | Purpose                                                            |
| ---------------------------------- | ------------------------------------------------------------------ |
| `--help`, `-h`                     | Display the installed command's help.                              |
| `--dry-run`, `-n`                  | Report the eligible plan without deleting targets or saving modes. |
| `--verbose`, `-v`                  | Print per-target details and additional root notices.              |
| `--config PATH`                    | Select an optional TOML configuration file.                        |
| `--normal-stale-after DURATION`    | Override normal-mode inactivity.                                   |
| `--pressure-enter-percent INTEGER` | Override the pressure entry threshold.                             |
| `--pressure-exit-percent INTEGER`  | Override the pressure recovery threshold.                          |
| `--pressure-stale-after DURATION`  | Override pressure-mode inactivity.                                 |
| `--critical-enter-percent INTEGER` | Override the critical entry threshold.                             |
| `--critical-exit-percent INTEGER`  | Override the critical recovery threshold.                          |
| `--critical-stale-after DURATION`  | Override critical-mode inactivity.                                 |

Run `rust-cleanup --help` to check the exact surface of the installed version.
