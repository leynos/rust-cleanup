"""Synthetic process tables and lock-probe doubles for build-protection tests.

Several contracts script the same filesystem: a ``/proc``-shaped directory
holding one process, and a Cargo output directory that is not called ``target``.
They live here rather than in one test module so a later split cannot quietly
fork the layout and let two contracts assert against different process entries.

Nothing here reads the real process table or takes a real lock: the detector's
inputs are ordinary files, and the doubles answer the two questions a probe can
be asked without touching the filesystem at all.
"""

import typing as typ

from rust_cleanup.cargo_locks import LOCK_NAME

if typ.TYPE_CHECKING:
    from pathlib import Path

# Every synthetic process table holds one process under this identifier, so the
# helpers below can write further facets without repeating the layout.
PROCESS_ID = "123"


class ProcessSpec(typ.NamedTuple):
    """Describe the facets of one synthetic process entry.

    The fields are named together rather than repeated as a wide argument list
    at every call site, because they are all facts about the same process.
    """

    command: str
    arguments: tuple[str, ...] = ()
    environ: dict[str, str] | None = None


def write_proc_process(proc_root: Path, cwd: Path, spec: ProcessSpec) -> None:
    r"""Create one synthetic process entry for detector contracts.

    Parameters
    ----------
    proc_root : Path
        Synthetic ``/proc`` root receiving the process entry.
    cwd : Path
        Working directory the process is reported to hold.
    spec : ProcessSpec
        Executable name, extra arguments, and environment recorded for the
        process.
    """
    process = proc_root / PROCESS_ID
    process.mkdir(parents=True)
    (process / "comm").write_text(f"{spec.command}\n", encoding="utf-8")
    (process / "cwd").symlink_to(cwd, target_is_directory=True)
    variables = spec.environ or {}
    (process / "environ").write_bytes(
        "".join(f"{name}={value}\0" for name, value in variables.items()).encode()
    )
    argv = (spec.command, *spec.arguments)
    (process / "cmdline").write_bytes(
        "".join(f"{argument}\x00" for argument in argv).encode()
    )


def custom_target(root: Path, name: str) -> Path:
    """Create Cargo build output under a custom, non-``target`` directory name.

    The directory holds a profile with a lock file but neither ``CACHEDIR.TAG``
    nor ``.rustc_info.json``, so it is a place a build can own and lock while
    discovery would never offer it on layout alone.

    Parameters
    ----------
    root : Path
        Directory the output is created directly inside.
    name : str
        Name of the build-output directory, which is never ``target``.

    Returns
    -------
    Path
        The created build-output directory.
    """
    output = root / name
    (output / "debug").mkdir(parents=True, exist_ok=True)
    (output / "debug" / LOCK_NAME).write_bytes(b"")
    return output


class UnheldLocks:
    """A lock probe that reports every candidate's Cargo lock as free."""

    def holds_lock(  # ruff: ignore[no-self-use] - implements the LockProbe protocol.
        self, candidate: Path
    ) -> bool:
        """Report no held lock for every candidate.

        Parameters
        ----------
        candidate : Path
            Build-output directory the caller asks about; ignored.

        Returns
        -------
        bool
            Always ``False``: nothing is held.
        """
        return False
