# Assistant instructions

## Code style and structure

- **Code is for humans.** Write code with clarity and empathy—assume a
  tired teammate will need to debug it at 3 a.m.
- **Comment *why*, not *what*.** Explain assumptions, edge cases, trade-offs,
  or complexity. Don't echo the obvious.
- **Clarity over cleverness.** Be concise, but favour explicit over terse or
  obscure idioms. Prefer code that's easy to follow.
- **Use functions and composition.** Avoid repetition by extracting reusable
  logic. Prefer generators or comprehensions, and declarative code to
  imperative repetition when readable.
- **Small, meaningful functions.** Functions should have a clear purpose, single
  responsibility, and obey command/query segregation.
- **Clear commit messages.** Commit messages should be descriptive, explaining
  what was changed and why.
- **Use consistent spelling and grammar.** Comments must use en-GB-oxendict
  ("-ize" / "-yse" / "-our") spelling and grammar, with the exception of
  references to external APIs.
- **Illustrate with clear examples.** Function documentation must include clear
  examples demonstrating usage and outcome. Test documentation should omit
  examples that only restate the test logic.
- **Keep file size manageable.** No single code file should be longer than 400
  lines. Long switch statements or dispatch tables should be broken up by
  feature and constituents colocated with targets. Large blocks of test data
  should be moved to external data files.
- **Name things precisely.** Use clear, descriptive variable and function names.
  For booleans, prefer names with `is`, `has`, or `should`.
- **Structure logically.** Each file should encapsulate a coherent module. Group
  related code (for example, models + utilities + fixtures) close together.
- **Group by feature, not layer.** Colocate views, logic, fixtures, and helpers
  related to a domain concept rather than splitting by type.
- **Use clear file boundaries.** Each module, component, and package should have
  an obvious responsibility and avoid accidental coupling.

## Documentation maintenance

- **Reference:** Use the markdown files within the `docs/` directory as a
  knowledge base and source of truth for project requirements, dependency
  choices, and architectural decisions. Start with
  [documentation contents](docs/contents.md) and
  [repository layout](docs/repository-layout.md) when orienting within the
  project.
- **Update:** When new decisions are made, requirements change, libraries are
  added/removed, or architectural patterns evolve, **proactively update** the
  relevant file(s) in the `docs/` directory to reflect the latest state.
- **Design decisions:** Record substantive decisions in the relevant design
  document. For major decisions, capture an architectural decision record (ADR)
  and reference it from the design document.
- **User-facing behaviour:** Update [users' guide](docs/users-guide.md) for
  behaviour or user-interface changes that users should know about.
- **Internal interfaces:** Document internally facing interfaces in the relevant
  component architecture document. Record internally facing conventions and
  practices in [developers' guide](docs/developers-guide.md).
- **Style:** All documentation must adhere to the
  [documentation style guide](docs/documentation-style-guide.md).

## Change quality and committing

- **Atomicity:** Aim for small, focused, atomic changes. Each change (and
  subsequent commit) should represent a single logical unit of work.
- **Quality gates:** Before considering a change complete or proposing a
  commit, ensure all of the following are met:
  - New functionality or behaviour changes are fully validated by relevant unit
    and behavioural tests.
  - Bug fixes include a failing test before the fix and a passing test
    afterwards.
  - Code passes lint checks.
  - Formatting is correct and validated.
- **For Python files:**
  - **Testing:** Passes all relevant unit and behavioural tests (`make test`).
    Use `make test WITH_ACT=1` when local GitHub Actions workflow validation
    should run through `act`; this sets `RUN_ACT_VALIDATION=1` for the pytest
    invocation.
  - **Linting:** Passes lint checks (`make lint`).
  - **Formatting:** Adheres to formatting standards (`make check-fmt`; use
    `make fmt` to apply fixes).
  - **Typechecking:** Passes type checking (`make typecheck`).
  - **Auditing:** Passes dependency vulnerability checks (`make audit`).
  - The generated Makefile wiring for these targets is:
    - `make check-fmt` runs Ruff formatting checks with
      `ruff format --check $(PYTHON_TARGETS)`.
    - `make lint` runs `make lint-python`; `make lint-python` runs
      `ruff check $(PYTHON_TARGETS)` and
      `interrogate --fail-under 100 $(PYTHON_TARGETS)` for 100% docstring
      coverage. Git discovers tracked and untracked Python sources, including
      hidden action directories. The base Pylint sweep covers
      `$(PYLINT_TARGETS)` using the interpreter selected for the baseline:
      uv-managed CPython 3.14.
      The minimum Python baseline is 3.12. DF12 Python Lints is pinned to
      v0.4.0; it and Ambrleaks, Skylos, Vulture, and the duplication detector
      run as isolated tools on Python 3.14. Ambrleaks checks `tests/`, Skylos
      checks the repository, and Vulture checks test helpers. The strict
      Skylos and Vulture dead-code checks, shared Nose duplication gate, and
      action validation are also part of `make lint`.
    - `make typecheck` runs `ty check $(PYTHON_TARGETS)`.
      `ty` 0.0.56 and Ruff 0.16.8 run in isolated Python 3.14 tools.
      `make lint-actions` validates workflows and actions with actionlint,
      action-validator, yamllint 1.38.0, and shellcheck.
    - `make test` runs `pytest -v -n $(PYTEST_XDIST_WORKERS)` and honours
      `WITH_ACT=1` through `RUN_ACT_VALIDATION=1`.
    - `make audit` runs `pip-audit`.
  - Temporary lint suppressions must include a link to the planned fix or
    implementation that will remove the suppression.

  - **Markdown files (`.md` only):**
    - **Linting and spelling:** Passes Markdown linting and the shared
      en-GB-oxendict spelling gate (`make markdownlint`; spelling scope is
      `all`).
    - **Formatting:** Uses mdtablefix 0.6.1 through `make check-fmt`; use
      `make fmt` to apply fixes.
    - **Mermaid diagrams:** Passes validation using nixie (`make nixie`).
- **Committing:**
  - Only changes that meet all quality gates should be committed.
  - Write clear, descriptive commit messages that summarize the change,
    following:
    - **Imperative mood** in the subject line (for example, "Fix bug", "Add feature").
    - **Subject line length:** around 50 characters or fewer.
    - **Body:** Separate subject from body with a blank line. Explain *what* and
      *why* in wrapped lines (approximately 72 columns).
    - **Formatting:** Use Markdown for formatted text inside the message body.
  - Do not commit changes that fail any quality gate.

## Refactoring heuristics and workflow

- **Recognizing refactoring needs:** regularly assess the codebase for potential
  refactoring opportunities. Consider refactoring when you observe:
  - **Long methods/functions:** functions that are excessively long or try to do
    too many things.
  - **Duplicated code:** identical or very similar code blocks appearing in
    multiple places.
  - **Complex conditionals:** deeply nested or overly complex `if`/`else` or
    `switch` statements.
  - **Large code blocks for single values:** significant logic blocks dedicated
    to calculating or deriving one value.
  - **Primitive obsession / data clumps:** groups of simple variables that are
    frequently passed together, which may indicate a missing abstraction.
  - **Excessive parameters:** functions or methods requiring too many
    parameters.
  - **Feature envy:** methods that focus more on other data than their own.
  - **Shotgun surgery:** one change that forces many files to be edited.
- **Abstraction / port / helper policy:** before adding a new abstraction, port,
  or helper:
  - Sweep the repository to confirm there is no existing equivalent helper,
    port, or abstraction.
  - Document the new abstraction's intended scope and re-use policy.
  - Record the decision in architecture, design, or developers-guide docs using
    `docs/contents.md` as the index.
- **Post-commit review:** after functional changes or bug fixes that meet
  quality gates, review changed code and adjacent areas using these heuristics.
- **Separate atomic refactors:** if refactoring is required, implement it in a
  separate atomic commit after the functional change and ensure it passes all
  relevant gates.

## Python verification and testing

- For Python work, use `pytest` for unit tests and `pytest-bdd` for behavioural
  tests. Cover happy paths, unhappy paths, and relevant edge cases.
- Keep pytest tests in the top-level `tests/` tree. Do not place unit tests in
  package module directories or `unittests/` subdirectories, because CI
  coverage uses xdist-backed SlipCover support.
- Snapshot tests (using `syrupy`) should be provided where multivariant output
  format consistency is relevant to the requirements. Snapshot tests must
  capture meaningful, reviewer-useful contracts rather than generic dumps of
  broad objects. Keep snapshots focused on stable output boundaries, pair them
  with semantic assertions for the behaviours they represent, and avoid
  snapshot-only coverage for logic that can be asserted directly. Redact or
  normalize nondeterministic fields such as timestamps, absolute paths, random
  identifiers, ordering-dependent maps, hostnames, and environment-specific
  values before snapshotting. Do not accept brittle snapshots that churn after
  harmless formatting or dependency changes; narrow the captured output until a
  snapshot failure identifies a real contract change.
- Add end-to-end tests where a change affects externally observable workflows,
  integration contracts, persistence, command-line behaviour, network
  boundaries, user interface flows, or other system-level behaviour.
- Use property tests with `hypothesis` or `CrossHair` when a change introduces
  an invariant over a range of inputs, states, orderings, or transitions.
- Run relevant unit, behavioural, property, and end-to-end suites before and
  after each change.

## Markdown guidance

- Validate Markdown files using `make markdownlint`.
- `make markdownlint` runs markdownlint-cli2 and the shared spelling gate over
  all repository content. Spelling scope is `all`.
- Run `make fmt` after documentation changes to format Markdown and fix table
  markup with mdtablefix 0.6.1.
- Validate Mermaid diagrams in Markdown by running `make nixie`.
- Markdown paragraphs and bullet points should be wrapped at 80 columns.
- Code blocks should be wrapped at 120 columns.
- Tables and headings should not be wrapped.
- Use dashes (`-`) for list bullets.
- Use GitHub-flavoured Markdown footnotes (`[^1]`) for references and footnotes.

<!-- typos-config-builder:agents-md:start -->

## Spelling

- `make spelling` runs the pinned `typos-config-builder gate`, which
  regenerates `typos.toml` from the shared en-GB-oxendict dictionary and
  `typos.local.toml`, then checks spelling and the shared phrase corrections.
- `typos.toml` is generated: never edit it by hand. Put narrow
  repository-specific exceptions in `typos.local.toml`, as exact or full-line
  patterns rather than bare accepted words.
- When `make spelling` changes `typos.toml`, commit the regenerated file. If
  the change is unrelated to your work, commit it in a separate base pull
  request and stack your branch on it, so each review diff stays focused.

<!-- typos-config-builder:agents-md:end -->

## Project documentation

Record design decisions in the design document. Where a decision is
substantive, record it in an ADR document following the documentation style
guide, then reference that ADR from the design document.

Update `docs/users-guide.md` for any change to application behaviour or user
interface that users should know about. Document internally facing interfaces
or practices in the relevant component architecture document. Document
internally facing conventions or practices in `docs/developers-guide.md`.

## Python development guidelines

For Python development, refer to the detailed guidelines in the `.rules/`
directory:

- [Python code style guidelines](.rules/python-00.md) - Core Python 3.12+ style
  conventions.
- [Python context managers](.rules/python-context-managers.md) - Best practices
  for context managers.
- [Python exceptions and logging][python-exceptions] -
  Raising and handling exceptions and logging.
- [Python generators](.rules/python-generators.md) - Generator and iterator
  patterns.
- [Python project configuration](.rules/python-pyproject.md) -
  `pyproject.toml` and packaging.
- [Python return patterns](.rules/python-return.md) - Function return
  conventions.
- [Python typing](.rules/python-typing.md) - Type annotation best practices.

[python-exceptions]: .rules/python-exception-design-raising-handling-and-logging.md

Additional docs:

- [Scripting standards](docs/scripting-standards.md) - Guidance for writing
  robust scripts, including secure command execution via `cuprum`, catalogue
  allowlisting, and command mocking patterns with `cmd-mox`.
- Before adding or updating helper scripts, read the scripting standards guide
  and follow its `Cyclopts`, `cuprum`, `pathlib`, and `cmd-mox` conventions.

## Additional tooling

The following tooling is available in this environment:

- `mbake` — A Makefile validator. Run using `mbake validate Makefile`.
- `strace` — Traces system calls and signals made by a process; useful for
  debugging runtime behaviour and syscalls.
- `gdb` — The GNU Debugger, for inspecting and controlling programs as they
  execute (or post-mortem via core dumps).
- `ripgrep` — Fast, recursive text search tool (`grep` alternative) that
  respects `.gitignore` files.
- `ltrace` — Traces calls to dynamic library functions made by a process.
- `valgrind` — Suite for detecting memory leaks, profiling, and debugging
  low-level memory errors.
- `bpftrace` — High-level tracing tool for eBPF, using a custom scripting
  language for kernel and application tracing.
- `lsof` — Lists open files and the processes using them.
- `htop` — Interactive process viewer (visual upgrade to `top`).
- `iotop` — Displays and monitors I/O usage by processes.
- `ncdu` — NCurses-based disk usage viewer for finding large files/folders.
- `tree` — Displays directory structure as a tree.
- `bat` — `cat` clone with syntax highlighting, Git integration, and paging.
- `delta` — Syntax-highlighted pager for Git and diff output.
- `tcpdump` — Captures and analyses network traffic at the packet level.
- `nmap` — Network scanner for host discovery, port scanning, and service
  identification.
- `lldb` — LLVM debugger, alternative to `gdb`.
- `eza` — Modern `ls` replacement with more features and better defaults.
- `fzf` — Interactive fuzzy finder for selecting files and commands.
- `hyperfine` — Command-line benchmarking tool with statistical output.
- `shellcheck` — Linter for shell scripts.
- `fd` — Fast user-friendly `find` alternative with sensible defaults.
- `checkmake` — Linter for `Makefile`s, ensuring best practices.
- `srgn` — Structural grep and syntax-tree pattern editing.
- `difft` **(Difftastic)** — Semantic diff tool that compares code structure.

## Key takeaway

These practices help maintain a high-quality codebase and facilitate
collaboration.
