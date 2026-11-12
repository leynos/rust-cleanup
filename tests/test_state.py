"""Tests for persisted per-filesystem hysteresis state."""

import json
import tempfile
from pathlib import Path
from unittest import mock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from rust_cleanup.pressure import FilesystemIdentity, Mode
from rust_cleanup.state import StateLoadResult, StateStore, default_state_path

# A generated mount point has to survive ``Path`` normalization unchanged. Two
# distinct strings that normalize to one path would give two dictionary keys a
# single ``FilesystemIdentity``, and the mode stored for the first would be read
# back for the second. Restricting every segment to letters and digits keeps
# separators, ``.`` segments, and trailing slashes out of the input.
_MOUNT_SEGMENT = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=8
)
_MOUNT_POINTS = st.lists(_MOUNT_SEGMENT, min_size=1, max_size=3).map("/".join)


def test_state_absence_starts_empty_without_a_warning(tmp_path: Path) -> None:
    """A first sweep derives its mode from current entry watermarks."""
    warnings: list[str] = []
    store = StateStore(tmp_path / "state.json")
    store.load(warnings.append)
    assert not warnings, "missing state must not warn"
    assert store.mode_for(FilesystemIdentity(tmp_path, 1)) is None, (
        "missing state has mode"
    )


def test_corrupt_state_warns_and_is_ignored(tmp_path: Path) -> None:
    """Malformed state never supplies a mode for a destructive decision."""
    path = tmp_path / "state.json"
    path.write_text("{broken", encoding="utf-8")
    warnings: list[str] = []
    store = StateStore(path)
    store.load(warnings.append)
    assert warnings, "corruption must warn"
    assert "ignoring malformed" in warnings[0], "corruption must warn"
    assert store.mode_for(FilesystemIdentity(tmp_path, 1)) is None, (
        "corruption supplied mode"
    )


def test_undecodable_state_is_malformed_rather_than_fatal(tmp_path: Path) -> None:
    """Bytes that are not UTF-8 start an empty state instead of raising."""
    path = tmp_path / "state.json"
    path.write_bytes(b'{"version": 1, "filesystems": {"\xff\xfe": "pressure"}}')
    warnings: list[str] = []
    store = StateStore(path)

    result = store.load(warnings.append)

    assert result is StateLoadResult.MALFORMED, (
        "an undecodable state document was not reported as malformed"
    )
    assert warnings, "an undecodable state document warned about nothing"
    assert "ignoring malformed" in warnings[0], (
        "an undecodable state document warned about nothing"
    )


def test_state_entry_of_the_wrong_type_rejects_the_whole_document(
    tmp_path: Path,
) -> None:
    """One misshapen entry discards the document instead of its own mode.

    Keeping the entries that happen to decode would report a partly valid file
    as loaded, silently dropping the modes it could not read. A filesystem that
    then falls back to its current watermark can drop out of pressure early.
    """
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({
            "version": 1,
            "filesystems": {"/mnt/one\x001": "pressure", "/mnt/two\x002": 7},
        }),
        encoding="utf-8",
    )
    warnings: list[str] = []
    store = StateStore(path)

    result = store.load(warnings.append)

    assert result is StateLoadResult.MALFORMED, (
        "a partially decodable state document was reported as loaded"
    )
    assert warnings, "the rejected document warned about nothing"
    assert store.mode_for(FilesystemIdentity(Path("/mnt/one"), 1)) is None, (
        "a valid entry survived the rejection of its document"
    )


def test_state_without_a_version_is_malformed(tmp_path: Path) -> None:
    """A document that never carried the version is ignored, not assumed.

    Reading a versionless document would mean interpreting whatever shape
    happened to be present under the current schema, so a truncated write is
    reported as malformed instead.
    """
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"filesystems": {"/mnt/one\x001": "pressure"}}),
        encoding="utf-8",
    )
    warnings: list[str] = []
    store = StateStore(path)

    result = store.load(warnings.append)

    assert result is StateLoadResult.MALFORMED, (
        "a versionless state document was reported as loaded"
    )
    assert warnings, "a versionless state document warned about nothing"
    assert "ignoring malformed" in warnings[0], (
        "a versionless state document warned about nothing"
    )
    assert store.mode_for(FilesystemIdentity(Path("/mnt/one"), 1)) is None, (
        "a versionless state document supplied a mode"
    )


def test_state_from_an_unsupported_version_is_malformed(tmp_path: Path) -> None:
    """A document written by another schema version is ignored rather than read."""
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({
            "version": 2,
            "filesystems": {"/mnt/one\x001": "pressure"},
        }),
        encoding="utf-8",
    )
    warnings: list[str] = []
    store = StateStore(path)

    result = store.load(warnings.append)

    assert result is StateLoadResult.MALFORMED, (
        "a state document from another schema version was reported as loaded"
    )
    assert warnings, "a state document from another schema version warned about nothing"
    assert "ignoring malformed" in warnings[0], (
        "a state document from another schema version warned about nothing"
    )
    assert store.mode_for(FilesystemIdentity(Path("/mnt/one"), 1)) is None, (
        "a state document from another schema version supplied a mode"
    )


@pytest.mark.parametrize("version", [True, 1.0], ids=["boolean", "float"])
def test_non_integer_state_version_is_malformed(
    tmp_path: Path, version: object
) -> None:
    """A version that is not an integer is not the schema this module writes.

    ``True`` equals ``1`` and ``1.0`` equals ``1`` under Python equality, so a
    version match that compares by value alone would read a document this
    module never wrote and take its filesystem modes as current state.
    """
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({
            "version": version,
            "filesystems": {"/mnt/one\x001": "pressure"},
        }),
        encoding="utf-8",
    )
    warnings: list[str] = []
    store = StateStore(path)

    result = store.load(warnings.append)

    assert result is StateLoadResult.MALFORMED, (
        f"a state version of {version!r} was reported as loaded"
    )
    assert warnings, f"a state version of {version!r} warned about nothing"
    assert "ignoring malformed" in warnings[0], (
        f"a state version of {version!r} warned about nothing"
    )
    assert store.mode_for(FilesystemIdentity(Path("/mnt/one"), 1)) is None, (
        f"a state version of {version!r} supplied a mode"
    )


def test_state_round_trip_is_private_and_keyed_by_filesystem(tmp_path: Path) -> None:
    """Atomic state persists one mode for the exact mount and device identity."""
    path = tmp_path / "state.json"
    identity = FilesystemIdentity(tmp_path, 99)
    store = StateStore(path)
    store.set_mode(identity, Mode.CRITICAL)
    store.save()
    assert path.stat().st_mode & 0o777 == 0o600, "state must be private"
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1, (
        "state version lost"
    )
    restored = StateStore(path)
    restored.load(lambda _message: None)
    assert restored.mode_for(identity) is Mode.CRITICAL, "stored mode not restored"
    assert restored.mode_for(FilesystemIdentity(tmp_path, 100)) is None, (
        "device identity lost"
    )


def test_default_state_path_prefers_the_configured_state_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit ``XDG_STATE_HOME`` supplies the state document location."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))

    assert default_state_path() == tmp_path / "xdg" / "rust-cleanup" / "state.json", (
        "the configured state home was ignored"
    )


def test_default_state_path_falls_back_to_the_home_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without ``XDG_STATE_HOME`` the state lives under the home directory."""
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    assert (
        default_state_path()
        == tmp_path / "home" / ".local" / "state" / "rust-cleanup" / "state.json"
    ), "the home-directory fallback changed"


@pytest.mark.parametrize("override", ["", "relative/state"])
def test_default_state_path_ignores_a_relative_or_empty_state_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, override: str
) -> None:
    """A relative ``XDG_STATE_HOME`` never places state beside the cwd.

    The XDG specification requires an absolute path, so a relative override is
    discarded rather than resolved against wherever the sweep was started.
    """
    monkeypatch.setenv("XDG_STATE_HOME", override)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    assert (
        default_state_path()
        == tmp_path / "home" / ".local" / "state" / "rust-cleanup" / "state.json"
    ), "the home-directory fallback changed"


def test_failed_replacement_keeps_the_previous_state_and_leaves_no_debris(
    tmp_path: Path,
) -> None:
    """A save that cannot replace the document preserves the last good one.

    The state file is the only record of hysteresis, so a failed atomic
    replacement must neither truncate it nor leave a temporary sibling behind
    for the next sweep to trip over.
    """
    path = tmp_path / "state.json"
    identity = FilesystemIdentity(tmp_path, 1)
    store = StateStore(path)
    store.set_mode(identity, Mode.PRESSURE)
    store.save()
    store.set_mode(identity, Mode.CRITICAL)

    with (
        mock.patch.object(Path, "replace", side_effect=OSError("no space left")),
        pytest.raises(OSError, match="no space left"),
    ):
        store.save()

    restored = StateStore(path)
    restored.load(lambda _message: None)
    assert restored.mode_for(identity) is Mode.PRESSURE, (
        "the failed save lost the previous state"
    )
    debris = sorted(path.parent.glob(f".{path.name}.*"))
    assert debris == [], f"a failed save left temporary files behind: {debris!r}"


@given(
    st.dictionaries(
        keys=st.tuples(_MOUNT_POINTS, st.integers(min_value=0, max_value=2**31)),
        values=st.sampled_from(list(Mode)),
        min_size=1,
        max_size=8,
    )
)
@settings(deadline=None)
def test_state_round_trip_preserves_every_generated_identity(
    modes: dict[tuple[str, int], Mode],
) -> None:
    """Every generated filesystem identity survives a save and load intact.

    The mapping mixes several distinct mount points, devices, and modes at
    once, which is where a key encoding that loses the device or the mode would
    show up as one identity answering for another.
    """
    with tempfile.TemporaryDirectory() as temporary_directory:
        path = Path(temporary_directory) / "state.json"
        identities = {
            (mount, device): FilesystemIdentity(Path(mount), device)
            for mount, device in modes
        }
        store = StateStore(path)
        for (mount, device), mode in modes.items():
            store.set_mode(identities[mount, device], mode)
        store.save()

        restored = StateStore(path)
        restored.load(lambda _message: None)
        for key, identity in identities.items():
            assert restored.mode_for(identity) is modes[key], (
                f"identity {key!r} lost its mode"
            )
        ungenerated = FilesystemIdentity(Path("/ungenerated"), 2**40)
        assert restored.mode_for(ungenerated) is None, (
            "an identity that was never stored returned a mode"
        )
