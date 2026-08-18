"""Conversion, duration guards, and — the important one — temp-file cleanup on failure."""

from __future__ import annotations

import io
import math
import os
import struct
import wave

import pytest

import audio_utils
from audio_utils import AudioError
from utils import Mode


def make_wav(seconds: float, sample_rate: int = 44_100, channels: int = 2) -> bytes:
    """A real WAV of a quiet sine, so pydub/ffmpeg has something genuine to decode."""
    frames = int(seconds * sample_rate)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        samples = bytearray()
        for i in range(frames):
            value = int(8000 * math.sin(2 * math.pi * 220 * i / sample_rate))
            samples += struct.pack("<h", value) * channels
        w.writeframes(bytes(samples))
    return buffer.getvalue()


def test_to_pcm_wav_downmixes_to_16k_mono_16bit() -> None:
    converted = audio_utils.to_pcm_wav(make_wav(1.0, sample_rate=44_100, channels=2))
    with wave.open(io.BytesIO(converted), "rb") as w:
        assert w.getframerate() == audio_utils.TARGET_SAMPLE_RATE
        assert w.getnchannels() == audio_utils.TARGET_CHANNELS
        assert w.getsampwidth() == audio_utils.TARGET_SAMPLE_WIDTH


def test_to_pcm_wav_rejects_empty_input() -> None:
    with pytest.raises(AudioError):
        audio_utils.to_pcm_wav(b"")


def test_to_pcm_wav_rejects_undecodable_input() -> None:
    with pytest.raises(AudioError):
        audio_utils.to_pcm_wav(b"this is definitely not audio")


def test_duration_seconds_round_trips() -> None:
    wav = audio_utils.to_pcm_wav(make_wav(2.0))
    assert audio_utils.duration_seconds(wav) == pytest.approx(2.0, abs=0.05)


def test_validate_duration_rejects_too_short() -> None:
    with pytest.raises(AudioError, match="too short"):
        audio_utils.validate_duration(0.4, Mode.DRILL)


def test_validate_duration_rejects_over_mode_maximum() -> None:
    with pytest.raises(AudioError, match="drill"):
        audio_utils.validate_duration(45.0, Mode.DRILL)


def test_validate_duration_accepts_the_same_length_in_a_longer_mode() -> None:
    audio_utils.validate_duration(45.0, Mode.PARAGRAPH)  # 120 s ceiling — must not raise


def test_prepare_returns_converted_audio_and_its_length() -> None:
    wav, seconds = audio_utils.prepare(make_wav(2.0), Mode.DRILL)
    assert seconds == pytest.approx(2.0, abs=0.05)
    with wave.open(io.BytesIO(wav), "rb") as w:
        assert w.getframerate() == audio_utils.TARGET_SAMPLE_RATE


def test_temp_wav_writes_then_removes_the_file() -> None:
    with audio_utils.temp_wav(b"RIFFfake") as path:
        assert os.path.exists(path)
        captured = path
    assert not os.path.exists(captured)


def test_temp_wav_removes_the_file_when_the_body_raises() -> None:
    """Acceptance criterion 8: Azure raising mid-call must not leave audio on disk."""
    captured: str | None = None
    with pytest.raises(RuntimeError):
        with audio_utils.temp_wav(b"RIFFfake") as path:
            captured = path
            raise RuntimeError("Azure blew up mid-call")
    assert captured is not None
    assert not os.path.exists(captured)
