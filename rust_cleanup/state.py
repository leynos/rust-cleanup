"""Atomic XDG state persistence for filesystem retention modes.

The sweep consults persisted modes so pressure hysteresis survives across
runs: a filesystem that is already in a pressure mode must not drop straight
back to normal merely because one timer activation measured a lower usage
figure. This module keeps that state in one JSON document under
``XDG_STATE_HOME`` and replaces it atomically, so a crashed sweep can never
leave a half-written state file behind.

Typical use::

    store = StateStore()
    store.load(warn)
    store.set_mode(identity, Mode.PRESSURE)
    store.save()
"""

import contextlib
import enum
import json
import os
import tempfile
import typing as typ
from pathlib import Path

from rust_cleanup import xdg
from rust_cleanup.pressure import FilesystemIdentity, Mode

if typ.TYPE_CHECKING:
    import collections.abc as cabc

# One fixed message for both malformed paths: the warning names neither the
# state file nor the failing exception, so it can never carry a filesystem path
# or any other value read from the document.
_MALFORMED_STATE_WARNING = "Warning: ignoring malformed rust-cleanup state"


class StateLoadResult(enum.StrEnum):
    """Bounded outcome of loading persisted retention modes.

    Attributes
    ----------
    LOADED : str
        Valid persisted modes replaced the in-memory mapping.
    ABSENT : str
        No state file exists yet; the mapping stays empty.
    MALFORMED : str
        Unreadable or invalid state was ignored; the mapping stays empty.
    """

    LOADED = "loaded"
    ABSENT = "absent"
    MALFORMED = "malformed"


def default_state_path(environment: cabc.Mapping[str, str] | None = None) -> Path:
    """Return the XDG state file location for rust-cleanup.

    Parameters
    ----------
    environment : Mapping[str, str] or None
        Environment to resolve the location from. ``None`` reads the process
        environment; tests pass a mapping rather than mutating it.

    Returns
    -------
    Path
        The configured state file location.
    """
    resolved = os.environ if environment is None else environment
    return xdg.state_home(resolved) / "rust-cleanup" / "state.json"


def _state_key(identity: FilesystemIdentity) -> str:
    """Return the JSON-safe state key for a mounted filesystem identity.

    The encoding belongs to this persistence adapter rather than to
    :class:`~rust_cleanup.pressure.FilesystemIdentity`: the domain value names
    a mount, and only the state document needs a flat string key. A NUL
    separator cannot occur in a mount point, so distinct identities cannot
    collide.

    Returns
    -------
    str
        The persisted mapping key for ``identity``.
    """
    return f"{identity.mount_point}\x00{identity.device}"


class StateStore:
    """A small persisted mapping from filesystem identities to modes.

    Attributes
    ----------
    path : Path
        Location of the JSON state document read by :meth:`load` and written
        by :meth:`save`.
    """

    def __init__(self, path: Path | None = None) -> None:
        """Initialize an empty state store at ``path`` or the XDG default.

        Parameters
        ----------
        path : Path | None, default=None
            State document to use. ``None`` selects
            :func:`default_state_path`.
        """
        self.path = path or default_state_path()
        self._modes: dict[str, Mode] = {}

    def load(self, warn: cabc.Callable[[str], None]) -> StateLoadResult:
        """Load valid state or safely start from an empty state mapping.

        Loading always replaces any previously loaded modes, so a malformed
        file cannot leave a stale mode behind from an earlier load.

        Parameters
        ----------
        warn : cabc.Callable[[str], None]
            Sink for human-readable warnings about malformed state.

        Returns
        -------
        StateLoadResult
            Whether persisted modes were loaded, absent, or malformed.
        """
        self._modes = {}
        try:
            with self.path.open(encoding="utf-8") as state_file:
                raw_state = json.load(state_file)
        except FileNotFoundError:
            return StateLoadResult.ABSENT
        except OSError, json.JSONDecodeError, UnicodeDecodeError:
            warn(_MALFORMED_STATE_WARNING)
            return StateLoadResult.MALFORMED
        modes = _modes_from_state(raw_state)
        if modes is None:
            warn(_MALFORMED_STATE_WARNING)
            return StateLoadResult.MALFORMED
        self._modes = modes
        return StateLoadResult.LOADED

    def mode_for(self, identity: FilesystemIdentity) -> Mode | None:
        """Return the prior mode for an exact mounted filesystem identity.

        Parameters
        ----------
        identity : FilesystemIdentity
            Mounted filesystem whose persisted mode is queried.

        Returns
        -------
        Mode | None
            The stored mode, or ``None`` when the identity has no state.
        """
        return self._modes.get(_state_key(identity))

    def set_mode(self, identity: FilesystemIdentity, mode: Mode) -> None:
        """Record the selected mode for a mounted filesystem identity.

        Parameters
        ----------
        identity : FilesystemIdentity
            Mounted filesystem the mode applies to.
        mode : Mode
            Mode selected for this sweep, replacing any earlier value.
        """
        self._modes[_state_key(identity)] = mode

    def save(self) -> None:
        """Atomically persist state using a private sibling temporary file.

        The document is written to a mode ``0o600`` temporary file in the
        state directory, flushed to disk, then renamed over the destination,
        so readers observe either the previous or the new document. A failing
        write leaves the previous document untouched and propagates the
        underlying ``OSError``.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", dir=self.path.parent
        )
        temporary_path = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as state_file:
                descriptor = -1
                json.dump(
                    {"version": 1, "filesystems": self._modes},
                    state_file,
                    sort_keys=True,
                )
                state_file.write("\n")
                state_file.flush()
                os.fsync(state_file.fileno())
            temporary_path.replace(self.path)
            directory_descriptor = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        finally:
            if descriptor >= 0:
                with contextlib.suppress(OSError):
                    os.close(descriptor)
            with contextlib.suppress(FileNotFoundError):
                temporary_path.unlink()


def _modes_from_state(raw_state: object) -> dict[str, Mode] | None:
    """Extract a valid filesystem-to-mode mapping from decoded JSON.

    An entry outside the persisted shape rejects the document whole rather
    than being skipped, so a partly valid file cannot be reported as loaded
    with the remainder of its modes silently dropped. The document must also
    carry the integer version this module writes, so a document from another
    schema — or one truncated before its version — is ignored rather than
    misread.

    Returns
    -------
    dict[str, Mode] | None
        Valid decoded modes, or ``None`` when the state shape is malformed.
    """
    match raw_state:
        case {"version": 1 as version, "filesystems": dict() as filesystems} if (
            isinstance(version, int) and not isinstance(version, bool)
        ):
            pass
        case _:
            return None
    modes: dict[str, Mode] = {}
    for key, value in filesystems.items():
        match key, value:
            case (str(), str()):
                pass
            case _:
                return None
        try:
            modes[key] = Mode(value)
        except ValueError:
            return None
    return modes
