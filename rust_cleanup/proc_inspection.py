"""Inspection of live build processes below a ``/proc``-shaped root.

The detector needs three answers about a running process: whether it is a build,
where that build is working, and which build outputs it named. This module reads
all three from the process entries alone, leaving the detector to hold the
snapshot and make the decision.

A process that exits while it is being inspected is a race rather than a fault,
so it is reported as absent. A process that still exists but cannot be read
leaves the question unanswered, and :class:`ProcInspectionError` carries that
difference to the caller, which must protect conservatively rather than guess.
The one exception is a process caught inside ``execve``, which clears the
argument and environment areas together: it has no output to name, and its
working directory still protects the build it is becoming.

Output directories are read from both of Cargo's naming mechanisms, because a
build the snapshot sees is not necessarily a build whose command line names its
output. Cargo accepts either environment spelling of its configured output, and
both ``CARGO_TARGET_DIR`` and ``CARGO_BUILD_TARGET_DIR`` are read alongside
``--target-dir``. Every value is resolved against the process's working
directory when relative, exactly as Cargo resolves them.
"""

import typing as typ
from pathlib import Path

from rust_cleanup.errors import CleanupError

# Cargo documents both spellings as the environment form of ``build.target-dir``.
# A process may set either or both, so every one that is set names an output.
TARGET_DIR_ENVS = ("CARGO_TARGET_DIR", "CARGO_BUILD_TARGET_DIR")
TARGET_DIR_OPTION = "--target-dir"
_OPTION_PREFIX = f"{TARGET_DIR_OPTION}="

# Arguments after these markers belong to a program the build runs, not to
# Cargo, so a path that looks like a Cargo option there names no output.
_ARGUMENT_SEPARATORS = frozenset({"--"})

_BUILD_COMMANDS = frozenset({
    "cargo",
    "rustc",
    "rustdoc",
    "rust-lld",
    "ld",
    "lld",
    "gold",
    "mold",
    "cc",
    "c++",
    "clang",
    "clang++",
    "gcc",
    "g++",
    "kani",
    "verus",
})
_BUILD_PREFIXES = ("cargo-", "rustc-", "rustdoc-", "kani-", "verus-", "ld.")

# ``Path.resolve`` reports a symlink loop as ``RuntimeError`` on CPython 3.12 and
# an unreadable filesystem as ``OSError``. Both leave a process uninspectable,
# and both must become conservative uncertainty rather than an escaping
# exception.
_RESOLUTION_FAILURES = (OSError, RuntimeError)


class ProcInspectionError(CleanupError):
    """Raised when a live process cannot be inspected safely."""


class ProcessLocation(typ.NamedTuple):
    """The file-system locations one build process is known to hold.

    Attributes
    ----------
    cwd : pathlib.Path
        Working directory the process holds.
    command_paths : tuple[pathlib.Path, ...]
        Absolute command-line arguments, resolved to canonical paths.
    named_outputs : tuple[pathlib.Path, ...]
        Output directories named by ``CARGO_TARGET_DIR``,
        ``CARGO_BUILD_TARGET_DIR``, or ``--target-dir``, resolved against
        ``cwd`` the way Cargo resolves them.
    """

    cwd: Path
    command_paths: tuple[Path, ...]
    named_outputs: tuple[Path, ...]

    def owns(self, resolved: Path, roots: tuple[Path, Path]) -> bool:
        """Report whether this process may be writing under ``resolved``.

        A working directory or command path protects every output in the
        worktree it belongs to, because Cargo may write any of them. A named
        output is precise evidence about one directory, so it protects only
        itself and the trees nested with it; redirecting one build must not
        shield its siblings.

        Parameters
        ----------
        resolved : pathlib.Path
            Canonical candidate path.
        roots : tuple[pathlib.Path, pathlib.Path]
            The candidate and the worktree that contains it.

        Returns
        -------
        bool
            Whether any recorded location points at the candidate.
        """
        if _is_within_any(self.cwd, roots):
            return True
        if any(_is_within_any(path, roots) for path in self.command_paths):
            return True
        return any(_overlaps(output, resolved) for output in self.named_outputs)


def _is_within_any(path: Path, roots: tuple[Path, Path]) -> bool:
    """Return whether an already-resolved path lies inside any resolved root.

    Returns
    -------
    bool
        Whether the path is one of, or nested below, any of the roots.
    """
    return any(root == path or root in path.parents for root in roots)


def _overlaps(left: Path, right: Path) -> bool:
    """Return whether either path lies inside the other.

    A named output is a precise location, unlike a working directory that may
    merely contain the build. Deleting either path would disturb a build writing
    to the other, so nesting in either direction is protection.

    Returns
    -------
    bool
        Whether the paths are equal or one is nested below the other.
    """
    return left == right or left in right.parents or right in left.parents


def is_build_process(command: str) -> bool:
    """Recognize build executables whose cache ownership must be protected.

    Parameters
    ----------
    command : str
        Executable name recorded in a process's ``comm`` entry.

    Returns
    -------
    bool
        Whether the executable is associated with a Rust build.
    """
    name = Path(command).name.casefold()
    return name in _BUILD_COMMANDS or name.startswith(_BUILD_PREFIXES)


def read_process_command(process: Path) -> str | None:
    """Read a live process's command, ignoring process-exit races.

    Parameters
    ----------
    process : pathlib.Path
        Process entry whose ``comm`` file is read.

    Returns
    -------
    str | None
        The command name, or ``None`` when the process exited during inspection.

    Raises
    ------
    ProcInspectionError
        If the process cannot be inspected safely.
    """
    try:
        return (process / "comm").read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError) as error:
        raise ProcInspectionError from error


def _cargo_arguments(arguments: list[str]) -> list[str]:
    """Return the arguments that belong to Cargo rather than a run program.

    Returns
    -------
    list[str]
        The arguments up to, but excluding, the first ``--`` marker.
    """
    for index, argument in enumerate(arguments):
        if argument in _ARGUMENT_SEPARATORS:
            return arguments[:index]
    return arguments


def _target_dir_argument(arguments: list[str]) -> list[str]:
    """Return every output directory named by ``--target-dir``.

    Both spellings Cargo accepts are read.

    Returns
    -------
    list[str]
        The raw option values, still relative to the process's working
        directory when Cargo would treat them that way.
    """
    values: list[str] = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == TARGET_DIR_OPTION:
            if index + 1 < len(arguments):
                values.append(arguments[index + 1])
                index += 1
        elif argument.startswith(_OPTION_PREFIX):
            values.append(argument.removeprefix(_OPTION_PREFIX))
        index += 1
    return values


def _environment_target_dirs(environment: bytes) -> list[str]:
    """Return every output directory Cargo's environment variables name.

    Cargo accepts both spellings, and which one wins is Cargo's business, so
    each value that is set names an output worth protecting. Each entry is
    split on the first ``=`` only, so a directory whose name contains one
    survives intact.

    Returns
    -------
    list[str]
        The raw values Cargo would read, in the order the spellings are
        declared. A spelling that is unset or repeated contributes nothing
        after its first occurrence.
    """
    entries = environment.split(b"\0")
    found: list[str] = []
    for variable in TARGET_DIR_ENVS:
        wanted = variable.encode()
        for entry in entries:
            name, separator, value = entry.partition(b"=")
            if separator and name == wanted:
                found.append(value.decode(errors="surrogateescape"))
                break
    return found


def _resolve_output(value: str, cwd: Path) -> Path:
    """Resolve an output directory the way Cargo interprets it.

    Returns
    -------
    Path
        The absolute output directory the value names.
    """
    output = Path(value)
    return output.resolve() if output.is_absolute() else (cwd / output).resolve()


def _read_environment(process: Path, command_line: bytes) -> bytes | None:
    """Read one process's environment, tolerating the ``execve`` window.

    ``execve`` clears a process's argument and environment areas in one
    operation. A process caught being replaced therefore reports an empty
    command line and an environment that cannot be read, which is a race rather
    than a process whose output cannot be established: it names no output, and
    its working directory still protects the build it is becoming. Treating
    that as uncertainty would protect every candidate on the host, and build
    processes are replaced often enough for a sweep to then clean nothing.

    An environment that cannot be read while the command line is intact is a
    different fact - the process is there and what it names is unknown - so it
    stays uncertain.

    Returns
    -------
    bytes | None
        The environment block, an empty block when the process was caught
        mid-replacement, or ``None`` when the process exited.

    Raises
    ------
    ProcInspectionError
        If the command line is intact but the environment cannot be read.
    """
    try:
        return (process / "environ").read_bytes()
    except FileNotFoundError:
        return None
    except OSError as error:
        if command_line:
            raise ProcInspectionError from error
        return b""


def _named_outputs(arguments: list[str], environment: bytes) -> list[str]:
    """Return every output directory the process names, without resolving.

    Returns
    -------
    list[str]
        Each environment value followed by each ``--target-dir`` value.
    """
    named = _target_dir_argument(_cargo_arguments(arguments))
    return [*_environment_target_dirs(environment), *named]


def _command_paths(cargo_arguments: list[str]) -> tuple[Path, ...]:
    """Resolve the absolute command-line arguments Cargo was given.

    Returns
    -------
    tuple[pathlib.Path, ...]
        Canonical paths of the absolute arguments, in command-line order.
    """
    return tuple(
        Path(argument).resolve()
        for argument in cargo_arguments
        if argument.startswith("/")
    )


def _resolved_outputs(
    arguments: list[str], environment: bytes, cwd: Path
) -> tuple[Path, ...]:
    """Resolve every output directory the process names against ``cwd``.

    Returns
    -------
    tuple[pathlib.Path, ...]
        Canonical output directories, in the order they were named.
    """
    return tuple(
        _resolve_output(value, cwd) for value in _named_outputs(arguments, environment)
    )


def read_process_location(process: Path) -> ProcessLocation | None:
    """Read one build process's cwd, command paths, and named outputs.

    An output directory named by ``CARGO_TARGET_DIR``,
    ``CARGO_BUILD_TARGET_DIR``, or ``--target-dir`` is resolved against the
    process's working directory, which is what Cargo does with a relative
    value. Any entry that has vanished is a process-exit race; any other
    unreadable entry leaves the process uninspectable, except that an
    unreadable environment beside an empty command line is the ``execve``
    window and contributes no output.

    Parameters
    ----------
    process : pathlib.Path
        Process entry whose ``cwd``, ``cmdline``, and ``environ`` are read.

    Returns
    -------
    ProcessLocation | None
        The locations the process may be writing, or ``None`` when the process
        exited during inspection.

    Raises
    ------
    ProcInspectionError
        If the process cannot be inspected safely.
    """
    try:
        cwd = (process / "cwd").resolve(strict=True)
        raw_command_line = (process / "cmdline").read_bytes()
    except FileNotFoundError:
        return None
    except _RESOLUTION_FAILURES as error:
        raise ProcInspectionError from error
    command_line = raw_command_line.split(b"\0")
    environment = _read_environment(process, raw_command_line)
    if environment is None:
        return None
    arguments = [
        argument.decode(errors="surrogateescape")
        for argument in command_line
        if argument
    ]
    try:
        command_paths = _command_paths(_cargo_arguments(arguments))
        named_outputs = _resolved_outputs(arguments, environment, cwd)
    except _RESOLUTION_FAILURES as error:
        raise ProcInspectionError from error
    return ProcessLocation(cwd, command_paths, named_outputs)
