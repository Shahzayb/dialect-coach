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
        audio_utils.validate_duration(0.4)


def test_a_long_recording_is_accepted_at_any_length() -> None:
    """Per-mode ceilings were removed on 2026-08-25. There is no upper bound left to hit."""
    audio_utils.validate_duration(45.0)
    audio_utils.validate_duration(4_500.0)


def test_prepare_returns_converted_audio_and_its_length() -> None:
    wav, seconds = audio_utils.prepare(make_wav(2.0))
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
    with pytest.raises(RuntimeError), audio_utils.temp_wav(b"RIFFfake") as path:
        captured = path
        raise RuntimeError("Azure blew up mid-call")
    assert captured is not None
    assert not os.path.exists(captured)


# --- Span slicing -----------------------------------------------------------------------------
# What puts "how I said it" next to the native rendering on a flagged word. Plain PCM frame
# arithmetic: a span for listening needs no signal-processing library, and Praat's `Extract
# part` returns the WHOLE sound on an empty range — a trap this cannot fall into.


def test_a_slice_returns_only_the_span_asked_for() -> None:
    """The "how I said it" path: one word cut out of a recording at Azure's own offsets."""
    clip = audio_utils.slice_wav(make_wav(3.0), 1.0, 2.0, pad_s=0.0)
    assert audio_utils.duration_seconds(clip) == pytest.approx(1.0, abs=0.02)


def test_a_slice_keeps_the_recordings_own_format() -> None:
    """Whatever it is handed, deliberately. In the app it is always a `prepare`d recording."""
    prepared, _ = audio_utils.prepare(make_wav(2.0))
    clip = audio_utils.slice_wav(prepared, 0.5, 1.0)
    with wave.open(io.BytesIO(clip), "rb") as handle:
        assert handle.getframerate() == audio_utils.TARGET_SAMPLE_RATE
        assert handle.getnchannels() == audio_utils.TARGET_CHANNELS
        assert handle.getsampwidth() == audio_utils.TARGET_SAMPLE_WIDTH


def test_a_slice_is_padded_on_both_sides() -> None:
    """A cut never lands on a phoneme boundary, so a word must not begin mid-burst."""
    clip = audio_utils.slice_wav(make_wav(3.0), 1.0, 2.0, pad_s=0.05)
    assert audio_utils.duration_seconds(clip) == pytest.approx(1.1, abs=0.02)


def test_a_slice_at_the_very_start_clamps_rather_than_raising() -> None:
    """The first word padded backwards starts before zero. That is ordinary, not an error."""
    clip = audio_utils.slice_wav(make_wav(2.0), 0.0, 0.5, pad_s=0.05)
    assert audio_utils.duration_seconds(clip) == pytest.approx(0.55, abs=0.02)


def test_a_slice_at_the_very_end_clamps_to_the_recording() -> None:
    clip = audio_utils.slice_wav(make_wav(2.0), 1.5, 2.0, pad_s=0.05)
    assert audio_utils.duration_seconds(clip) == pytest.approx(0.55, abs=0.02)


def test_an_empty_span_is_refused() -> None:
    with pytest.raises(AudioError, match="no duration"):
        audio_utils.slice_wav(make_wav(2.0), 1.0, 1.0)


def test_a_span_past_the_end_of_the_recording_is_refused() -> None:
    with pytest.raises(AudioError, match="outside the recording"):
        audio_utils.slice_wav(make_wav(1.0), 5.0, 6.0)


def test_slicing_something_that_is_not_wav_is_refused() -> None:
    with pytest.raises(AudioError, match="could not be read"):
        audio_utils.slice_wav(b"not audio at all", 0.0, 1.0)
