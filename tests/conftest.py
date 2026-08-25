"""Test-suite setup.

Two jobs, both about the same guarantee: a test run must never be able to spend quota.
`offline_env` forces `OFFLINE_MODE=true` and clears the credentials for every test, and
`no_network` refuses the connection itself. The first says do not; the second makes it so.

Import resolution is `pythonpath = src` in pytest.ini — the source modules live under
`src/` and are imported flat, and pytest is the only thing that needs to be told where.
"""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

import pytest

import utils

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"


# Loopback only. Nothing in this suite has any business opening a socket at all, but
# 127.0.0.1 is left open because a test runner or a future local fixture legitimately might,
# and refusing it would be a guard that fails for reasons unrelated to spending money.
_ALLOWED_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", ""})


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse any connection that leaves this machine, for every test, without opting in.

    `offline_env` already removes the keys and forces `OFFLINE_MODE`, and every module that
    can reach the network refuses under it before a client is built. This is the layer below
    all of that: if some future path forgets the check, or a dependency phones home on
    import, the socket itself says no and the test fails loudly instead of quietly costing
    Azure minutes or Gemini free-tier calls.

    Several tests deliberately switch `OFFLINE_MODE` back off — `test_tts`, `test_budget`,
    `test_ai_coach`, `test_parsing` — to exercise the refusal paths that live *above* the
    network. They pass unchanged under this guard, which is the point: they were already
    proving that nothing reaches a socket.
    """
    real_connect = socket.socket.connect

    def guarded(self: socket.socket, address: Any) -> Any:
        host = address[0] if isinstance(address, tuple) else address
        if isinstance(host, bytes):
            host = host.decode("utf-8", "replace")
        if isinstance(host, str) and host in _ALLOWED_HOSTS:
            return real_connect(self, address)
        raise RuntimeError(
            f"A test tried to open a network connection to {address!r}. The suite runs "
            f"offline: nothing here may reach the network, because a real call spends "
            f"Azure minutes or Gemini free-tier quota that cannot be refunded."
        )

    monkeypatch.setattr(socket.socket, "connect", guarded)


@pytest.fixture(autouse=True)
def offline_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No network, no keys, deterministic guards — for every test, without opting in.

    `.env` is bind-mounted into the container, so a real one would otherwise be loaded by
    `utils.get` and quietly re-supply the keys this fixture just cleared. Marking dotenv
    as already-loaded keeps the suite reading from the environment set here and nowhere
    else — the tests must behave the same on a machine with credentials and without.
    """
    monkeypatch.setattr(utils, "_dotenv_loaded", True)
    monkeypatch.setenv("OFFLINE_MODE", "true")
    monkeypatch.delenv("AZURE_SPEECH_KEY", raising=False)
    monkeypatch.delenv("AZURE_SPEECH_REGION", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("MIN_DURATION_SECONDS", "1.5")


@pytest.fixture(autouse=True)
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A throwaway database per test, and no Streamlit cache carried over from the last one.

    **Both caches, not just one.** `get_connection` is `@st.cache_resource`, so without that
    clear every test after the first keeps writing to the first test's database. `@st.cache_data`
    is cleared for the same reason and is easier to miss: a cached read keyed on something as
    coarse as a row count collides across two different databases that happen to hold the same
    number of rows, and the second test silently reads the first's results. That is not
    hypothetical — it surfaced once as thirteen unrelated failures with no hint of a caching
    cause, and clearing both is what stops it recurring.
    """
    monkeypatch.setenv("DB_PATH", str(tmp_path / "coach.db"))
    # And the kept audio, for the same reason plus one of its own: `audio_utils.keep` writes
    # real WAV bytes, and its default lands in the working tree. An offline suite that leaves
    # recordings in the repository is a suite that can leak one — `audio/` is gitignored, but
    # "gitignored" is not the same promise as "never written".
    monkeypatch.setenv("AUDIO_DIR", str(tmp_path / "audio"))
    import streamlit as st

    st.cache_resource.clear()
    st.cache_data.clear()


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES
