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
    monkeypatch.setenv("MAX_DURATION_SECONDS_DRILL", "30")
    monkeypatch.setenv("MAX_DURATION_SECONDS_PARAGRAPH", "120")
    monkeypatch.setenv("MAX_DURATION_SECONDS_UNSCRIPTED", "300")


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


# --- Synthetic speech ------------------------------------------------------------------------
# The suite runs offline and no recording is committed, so the audio the accent measurement
# needs has to be generated. That is not a compromise — it is the only honest way to test a
# formant tracker: a synthesised vowel has a KNOWN F1/F2/F3, so "did the analyser recover it"
# is a real assertion rather than a comparison against another estimate of the same unknown.
#
# The source-filter model here is the standard one: an impulse train at f0, shaped by a
# two-pole lowpass at 100 Hz to give the roughly -12 dB/octave tilt of a real glottal source,
# then passed through one two-pole resonator per formant.
#
# **Five formants, not three, and that detail cost real debugging time.** Praat's Burg fits
# five poles below a 5 kHz ceiling because real speech has about that many. A synthetic vowel
# carrying only three under-determines the model, so the analyser spends a spare pole on a
# spurious wide-bandwidth resonance between F1 and F2 — measured at B=1692 Hz on a three-formant
# /i/ — and every formant above it shifts down a slot. The test signal has to look like speech
# or it tests the wrong thing.

_GLOTTAL_POLE_HZ = 100.0
_GLOTTAL_BANDWIDTH_HZ = 100.0

# The upper formants every vowel gets, so the signal has as many resonances as the model fits.
UPPER_FORMANTS = (3600.0, 4500.0)

SAMPLE_RATE = 16_000


def _resonate(signal: Any, frequency: float, bandwidth: float, rate: int) -> Any:
    """One two-pole resonator, applied in place of a real vocal tract section."""
    import math

    import numpy as np

    radius = math.exp(-math.pi * bandwidth / rate)
    first = 2 * radius * math.cos(2 * math.pi * frequency / rate)
    second = -radius * radius
    out = np.zeros_like(signal)
    for index in range(len(signal)):
        previous = out[index - 1] if index >= 1 else 0.0
        before = out[index - 2] if index >= 2 else 0.0
        out[index] = signal[index] + first * previous + second * before
    return out


def synth_vowel(
    formants: tuple[float, ...],
    seconds: float,
    *,
    f0: float = 120.0,
    rate: int = SAMPLE_RATE,
    amplitude: float = 0.5,
) -> Any:
    """A voiced vowel with exactly these formants. Returns float samples in [-1, 1]."""
    import numpy as np

    count = int(seconds * rate)
    source = np.zeros(count)
    source[:: int(rate / f0)] = 1.0
    signal = _resonate(source, _GLOTTAL_POLE_HZ, _GLOTTAL_BANDWIDTH_HZ, rate)
    for position, frequency in enumerate(tuple(formants) + UPPER_FORMANTS):
        signal = _resonate(signal, frequency, 60.0 + 30.0 * position, rate)
    peak = float(np.max(np.abs(signal)))
    return signal / peak * amplitude if peak else signal


def synth_noise(seconds: float, *, rate: int = SAMPLE_RATE, amplitude: float = 0.01) -> Any:
    """Low-level noise, standing in for a consonant or for room tone between words."""
    import numpy as np

    generator = np.random.default_rng(0)
    return generator.normal(0.0, amplitude, int(seconds * rate))


def to_wav_bytes(signal: Any, *, rate: int = SAMPLE_RATE) -> bytes:
    """Float samples as the 16 kHz/16-bit/mono PCM WAV the pipeline expects."""
    import io
    import wave

    import numpy as np

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes((np.clip(signal, -1.0, 1.0) * 32767).astype("<i2").tobytes())
    return buffer.getvalue()
