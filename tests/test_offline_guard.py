"""The guarantee the whole suite rests on: a test run cannot spend money.

Everything else here is tested against fixtures precisely so that no test needs the network.
These tests check the guards themselves, because a guard nobody checks is a guard that can
quietly become a no-op — `conftest.no_network` is a monkeypatch, and a monkeypatch that stops
being applied leaves no trace at all in a passing run.
"""

from __future__ import annotations

import socket

import pytest

import utils

# TEST-NET-1 (RFC 5737), reserved for documentation and guaranteed never to be routed. Using
# a literal address rather than a hostname keeps DNS out of it: the point is to prove the
# connect is refused, not to discover how a name fails to resolve.
UNROUTABLE = ("192.0.2.1", 443)


def test_a_connection_that_leaves_this_machine_is_refused() -> None:
    """The layer below OFFLINE_MODE. If a future path forgets the check, this still holds."""
    with (
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock,
        pytest.raises(RuntimeError, match="offline"),
    ):
        sock.connect(UNROUTABLE)


def test_the_high_level_helpers_are_refused_too() -> None:
    """`create_connection` goes through `socket.connect`, so the guard covers it as well.

    Worth asserting separately: most SDKs reach the network through a helper like this or
    through a library built on it, never by calling `connect` themselves.
    """
    with pytest.raises(RuntimeError, match="offline"):
        socket.create_connection(UNROUTABLE, timeout=1)


def test_loopback_is_still_allowed() -> None:
    """The guard refuses what leaves the machine, not everything.

    Blocking loopback would make the guard fail for reasons that have nothing to do with
    spending money — a local test server, a future fixture — and a guard that cries wolf gets
    switched off.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            client.connect(server.getsockname())
            assert client.getpeername()[0] == "127.0.0.1"


def test_offline_mode_is_on_and_the_credentials_are_gone() -> None:
    """The other half of the guarantee, asserted rather than assumed.

    `conftest.offline_env` sets this for every test. If it stopped being applied, every test
    that replays a fixture would still pass — and the one that reached Azure would spend.
    """
    assert utils.offline_mode() is True
    for name in ("AZURE_SPEECH_KEY", "AZURE_SPEECH_REGION", "GEMINI_API_KEY"):
        assert utils.get(name) is None, f"{name} must not be readable from a test"
