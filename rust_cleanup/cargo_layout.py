"""Positive evidence that a directory holds Cargo build output.

Cargo writes ``CACHEDIR.TAG`` only when it creates the target directory
itself, so a directory that already existed stays unmarked
(rust-lang/cargo#12441). A marker-only rule therefore misses real build output,
which is what left hundreds of gigabytes behind on the host this tool exists
for. A directory name is no better: Cargo accepts any ``--target-dir``, and
worktree tooling routinely names build outputs after the branch or variant it
belongs to.

This module therefore recognizes the layout Cargo itself creates. A build
directory holds ``.rustc_info.json`` at its root and a profile directory that
contains at least one of Cargo's own artefacts. Anything less is reported with
a reason instead of being offered for deletion, because the cost of deleting a
source tree is far higher than the cost of leaving build output behind.

The evidence deliberately accepts *any* single artefact. A partially cleaned
output loses ``.fingerprint`` and ``deps`` while keeping ``.cargo-lock``,
``build``, ``examples``, and ``incremental``; requiring both of the first two
would miss exactly the directories this tool exists to reclaim.
"""

import os
import stat
import typing as typ

if typ.TYPE_CHECKING:
    from pathlib import Path

CARGO_INFO = ".rustc_info.json"

# One of these inside a profile is enough to prove Cargo owns the directory.
PROFILE_ARTEFACTS = frozenset({".cargo-lock", ".fingerprint", "deps", "build"})

# A crate checkout keeps its sources beside its manifest or repository state.
SOURCE_MARKERS = frozenset({"Cargo.toml", ".git"})

# Fixed tokens, so a reason can be reported or counted without carrying a path.
OMIT_UNMARKED = "unmarked-no-evidence"
OMIT_LOOKALIKE = "lookalike"
OMIT_SYMLINK = "symlink"
OMIT_SOURCE = "source-tree"


def _names(directory: Path) -> set[str]:
    """Return the entry names directly inside ``directory``.

    Returns
    -------
    set[str]
        The names of the directory's direct children.

    Raises
    ------
    OSError
        If the directory cannot be read, which callers treat as an inspection
        failure rather than as an empty directory.
    """  # ruff: ignore[docstring-extraneous-exception] - scandir raises it.
    with os.scandir(directory) as entries:
        return {entry.name for entry in entries}


def _is_plain_directory(path: Path) -> bool:
    """Return whether ``path`` is a directory rather than a link to one.

    Returns
    -------
    bool
        Whether the entry is a real directory, so a link cannot smuggle extra
        evidence into the layout test.
    """
    try:
        return path.is_dir() and not path.is_symlink()
    except OSError:
        return False


def _holds_profile_artefact(profile: Path) -> bool:
    """Return whether ``profile`` directly contains a Cargo artefact.

    Returns
    -------
    bool
        Whether any Cargo artefact name is present.

    Raises
    ------
    OSError
        If the profile cannot be read.
    """  # ruff: ignore[docstring-extraneous-exception] - _names raises it.
    return not PROFILE_ARTEFACTS.isdisjoint(_names(profile))


def _holds_nested_profile_artefact(profile: Path) -> bool:
    """Return whether a triple directory directly below ``profile`` qualifies.

    A build that targets an explicit triple nests the profile one level deeper:
    ``target/<triple>/<profile>``. Only that shape is inspected, so an
    unrelated directory further down cannot supply the evidence.

    Returns
    -------
    bool
        Whether any plain child of ``profile`` holds a Cargo artefact.

    Raises
    ------
    OSError
        If ``profile`` or a child cannot be read.
    """  # ruff: ignore[docstring-extraneous-exception] - _names raises it.
    return any(
        _is_plain_directory(profile / nested)
        and _holds_profile_artefact(profile / nested)
        for nested in _names(profile)
    )


def has_profile_evidence(target: Path, entries: set[str]) -> bool:
    """Return whether ``target`` holds a Cargo profile directory.

    A profile sits directly below the build root, or one level further down
    when a target triple separates them. Only those two shapes are accepted, so
    an unrelated nested project cannot supply the evidence for its parent.

    Parameters
    ----------
    target : pathlib.Path
        Candidate build-output directory.
    entries : set[str]
        Names of the directory's direct children, already read by the caller.

    Returns
    -------
    bool
        Whether Cargo's profile layout is present.

    Raises
    ------
    OSError
        If a directory that would provide the evidence cannot be read.
    """  # ruff: ignore[docstring-extraneous-exception] - _names raises it.
    profiles = [target / name for name in entries]
    return any(
        _is_plain_directory(profile)
        and (
            _holds_profile_artefact(profile) or _holds_nested_profile_artefact(profile)
        )
        for profile in profiles
    )


def holds_rustc_info(directory: Path) -> bool | None:
    """Report whether a directory holds ``.rustc_info.json``.

    The explicit stat keeps a permission failure distinguishable from an
    absent file, so an uninspectable candidate makes the scan incomplete
    instead of quietly disappearing from the candidate list.

    Returns
    -------
    bool | None
        Whether the file is present, or ``None`` when that could not be
        established.
    """
    try:
        return stat.S_ISREG((directory / CARGO_INFO).stat().st_mode)
    except FileNotFoundError, NotADirectoryError:
        return False
    except OSError:
        return None


def classify(
    path: Path, entries: set[str] | None = None, *, marked: bool = False
) -> str | None:
    """Classify a candidate directory as build output or as an omission.

    A cache marker makes a directory disposable, not Cargo output. The source
    markers are therefore refused first: a checkout that happens to carry
    ``CACHEDIR.TAG`` is still a checkout, and accepting it on the marker alone
    would hand it to ``shutil.rmtree``.

    Parameters
    ----------
    path : pathlib.Path
        Directory that was named ``target`` or that holds
        ``.rustc_info.json``.
    entries : set[str] | None, default=None
        Names of the directory's direct children when the caller has already
        read them. Re-reading a large directory costs more than passing what
        the walk already returned.
    marked : bool, default=False
        Whether the caller found the cache marker directly inside ``path``.

    Returns
    -------
    str | None
        ``None`` when the directory is Cargo build output, otherwise one of the
        omission tokens.

    Raises
    ------
    OSError
        If the directory cannot be read.
    """  # ruff: ignore[docstring-extraneous-exception] - _names raises it.
    if path.is_symlink():
        return OMIT_SYMLINK
    if entries is None:
        entries = _names(path)
    if not SOURCE_MARKERS.isdisjoint(entries):
        return OMIT_SOURCE
    if marked:
        return None
    if CARGO_INFO not in entries:
        return OMIT_UNMARKED
    if has_profile_evidence(path, entries):
        return None
    return OMIT_LOOKALIKE
