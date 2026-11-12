"""Contracts for the filesystem adapters the sweep depends on.

Both adapters exist so policy can stay pure: they turn an operating system
query into a plain number or identity and report an answer they cannot obtain
by raising ``OSError``. These tests drive each one with controlled inputs
rather than a real mount, so the reported figure and the failure path are
asserted exactly.
"""

import types
import typing as typ
from pathlib import Path
from unittest import mock

import pytest

from rust_cleanup.filesystem import filesystem_identity, resolve_target, usage_percent
from rust_cleanup.pressure import FilesystemIdentity

if typ.TYPE_CHECKING:
    import collections.abc as cabc


def _device_lookup(devices: dict[Path, int]) -> cabc.Callable[[Path], object]:
    """Return a stand-in ``Path.stat`` reporting a controlled device number.

    Parameters
    ----------
    devices : dict[Path, int]
        Device number keyed by the exact path passed to the replacement.

    Returns
    -------
    collections.abc.Callable[[Path], object]
        A stat replacement that resolves each known path to its device.
    """

    def stat(path: Path) -> object:
        """Return a stat result carrying the device mapped to ``path``.

        Parameters
        ----------
        path : Path
            Exact path whose device number is looked up in ``devices``.

        Returns
        -------
        object
            The scripted filesystem status for the requested path.
        """
        return types.SimpleNamespace(st_dev=devices[path])

    return stat


class TestUsagePercent:
    """Tests for usage_percent."""

    def test_reports_the_used_share_of_a_controlled_filesystem(self) -> None:
        """Used capacity is derived from the measured total and used bytes."""
        measured = mock.Mock(total=200, used=50, free=150)
        with mock.patch(
            "rust_cleanup.filesystem.shutil.disk_usage", return_value=measured
        ):
            assert usage_percent(Path("/controlled")) == pytest.approx(25.0), (
                "the used-capacity percentage changed"
            )

    def test_propagates_the_measurement_failure(self) -> None:
        """An unmeasurable filesystem raises instead of guessing a figure."""
        with (
            mock.patch(
                "rust_cleanup.filesystem.shutil.disk_usage",
                side_effect=OSError("no such device"),
            ),
            pytest.raises(OSError, match="no such device"),
        ):
            usage_percent(Path("/missing"))

    def test_reports_a_zero_total_as_the_measurement_failure(self) -> None:
        """A zero-total filesystem raises rather than dividing by zero."""
        measured = mock.Mock(total=0, used=0, free=0)
        with (
            mock.patch(
                "rust_cleanup.filesystem.shutil.disk_usage", return_value=measured
            ),
            pytest.raises(OSError, match="zero total capacity"),
        ):
            usage_percent(Path("/pseudo"))


class TestFilesystemIdentity:
    """Tests for filesystem_identity."""

    def test_walks_up_to_the_mount_owning_the_device(self, tmp_path: Path) -> None:
        """The identity names the highest ancestor on the same device."""
        mount = tmp_path / "mnt"
        nested = mount / "a" / "b"
        nested.mkdir(parents=True)
        devices = {nested: 7, nested.parent: 7, mount: 7, tmp_path: 1}

        with (
            mock.patch.object(Path, "resolve", return_value=nested),
            mock.patch.object(Path, "stat", _device_lookup(devices)),
        ):
            identity = filesystem_identity(nested)

        assert identity == FilesystemIdentity(mount_point=mount, device=7), (
            "the mount point or device identity changed"
        )

    def test_propagates_an_unidentifiable_path(self, tmp_path: Path) -> None:
        """A path whose device cannot be read raises instead of defaulting."""
        with (
            mock.patch.object(Path, "stat", side_effect=OSError("device is gone")),
            pytest.raises(OSError, match="device is gone"),
        ):
            filesystem_identity(tmp_path)


class TestResolveTarget:
    """Tests for resolve_target."""

    def test_pairs_the_canonical_path_with_its_filesystem(self, tmp_path: Path) -> None:
        """A resolvable target reports both the path and the mount it lives on.

        The identity is read for the canonical path, not for the discovered
        one: identifying the link while storing its target would attribute a
        target to whichever filesystem the link itself happened to sit on.
        """
        canonical = tmp_path / "canonical"
        identity = FilesystemIdentity(canonical, 3)
        identified: list[Path] = []

        def recording_identity(path: Path) -> FilesystemIdentity:
            """Record the path the adapter was asked to identify.

            Parameters
            ----------
            path : Path
                Path whose identity is requested.

            Returns
            -------
            FilesystemIdentity
                The identity of the canonical path.
            """
            identified.append(path)
            return identity

        with mock.patch.object(Path, "resolve", return_value=canonical):
            found = resolve_target(tmp_path / "link", recording_identity)

        assert found is not None, "a resolvable target was dropped"
        assert found.path == canonical, "the canonical path was not kept"
        assert found.filesystem is identity, "the filesystem identity was not kept"
        assert identified == [canonical], (
            f"the identity was read for a path other than the canonical one: "
            f"{identified!r}"
        )

    def test_preserves_a_target_whose_identity_cannot_be_read(
        self, tmp_path: Path
    ) -> None:
        """An unidentifiable target is preserved rather than guessed at."""

        def unidentifiable(_path: Path) -> FilesystemIdentity:
            """Report every path as unresolvable.

            Parameters
            ----------
            _path : Path
                Target path whose identity cannot be resolved; ignored.

            Raises
            ------
            OSError
                Whenever the filesystem identity is requested.
            """
            message = "device is gone"
            raise OSError(message)

        assert resolve_target(tmp_path, unidentifiable) is None, (
            "an unidentifiable target was not preserved"
        )

    def test_preserves_a_target_whose_path_cannot_be_resolved(
        self, tmp_path: Path
    ) -> None:
        """A path that cannot be canonicalized is preserved the same way."""
        identity = FilesystemIdentity(tmp_path, 1)
        with mock.patch.object(Path, "resolve", side_effect=OSError("path is gone")):
            found = resolve_target(tmp_path, lambda _path: identity)

        assert found is None, "a target that could not be resolved was not preserved"
