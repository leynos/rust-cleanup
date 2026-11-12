"""rust-cleanup package."""

import importlib
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc

PACKAGE_NAME = "rust_cleanup"

try:  # pragma: no cover - Rust optional
    rust = importlib.import_module(f"._{PACKAGE_NAME}_rs", package=__name__)
    hello = typ.cast("cabc.Callable[[], str]", rust.hello)
except ModuleNotFoundError:  # pragma: no cover - Python fallback
    from .pure import hello

__all__ = ["hello"]
