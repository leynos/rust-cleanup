"""Tests for the generated package stub."""

import rust_cleanup


def test_hello_returns_stub_greeting() -> None:
    """The generated package exposes a working greeting."""
    expected = "hello from Python"
    assert rust_cleanup.hello() == expected, (
        "The generated package must expose the expected greeting"
    )
