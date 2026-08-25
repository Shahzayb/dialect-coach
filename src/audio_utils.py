"""Audio conversion, duration validation, span slicing, temp files, and kept recordings.

Azure's `AudioConfig(filename=...)` needs a real path on disk, which is why the temp file in
`temp_wav` exists. It is deleted in a `finally` block so it goes even when the call it was
created for raises mid-flight.

**`keep` is a separate thing and a newer one.** Until v0.10.0 this module deleted every
recording and said so in a comment that called it a project constraint. That stopped being
true on 2026-08-19, when the "no stored audio" rule was lifted: recordings may be kept
locally, never committed, with the path and hash in the database. Two things need it: the
History page, which replays an old attempt's recording months later, and `slice_wav`, which
cuts one word out of that recording at Azure's own offsets so "how I said it" can sit beside
the native rendering. A stored recording is what makes both a re-read of existing bytes
rather than a request that the passage be read again.

`temp_wav` still deletes, because a temp file is still a temp file.
"""

from __future__ import annotations

import io
import logging
import os
import tempfile
import wave
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pydub import AudioSegment

import utils

logger = logging.getLogger(__name__)

# What Azure pronunciation assessment expects: 16 kHz, 16-bit, mono PCM.
TARGET_SAMPLE_RATE = 16_000
TARGET_SAMPLE_WIDTH = 2  # bytes, i.e. 16-bit
TARGET_CHANNELS = 1

SUPPORTED_UPLOAD_TYPES = ("wav", "mp3", "m4a", "webm", "ogg")


class AudioError(ValueError):
    """The supplied audio cannot be used. Message is safe to show in the UI."""


def to_pcm_wav(data: bytes) -> bytes:
    """Decode any ffmpeg-readable audio and re-encode as 16 kHz/16-bit/mono PCM WAV.

    Format is sniffed by ffmpeg rather than trusted from a file extension, so a
    mislabelled upload still works.
    """
    if not data:
        raise AudioError("The recording is empty. Try recording again.")

    try:
        segment = AudioSegment.from_file(io.BytesIO(data))
    except Exception as exc:
        # pydub surfaces ffmpeg's stderr, which is noise to a user.
        logger.debug("pydub failed to decode audio", exc_info=True)
        raise AudioError(
            "That audio could not be decoded. Supported formats: "
            + ", ".join(SUPPORTED_UPLOAD_TYPES)
            + "."
        ) from exc

    segment = (
        segment.set_frame_rate(TARGET_SAMPLE_RATE)
        .set_sample_width(TARGET_SAMPLE_WIDTH)
        .set_channels(TARGET_CHANNELS)
    )
    buffer = io.BytesIO()
    segment.export(buffer, format="wav")
    return buffer.getvalue()


def duration_seconds(wav_bytes: bytes) -> float:
    """Length of already-converted PCM WAV audio, in seconds."""
    try:
        segment = AudioSegment.from_file(io.BytesIO(wav_bytes), format="wav")
    except Exception as exc:
        raise AudioError("Could not read the converted audio.") from exc
    return len(segment) / 1000.0


def validate_duration(seconds: float) -> None:
    """Reject audio that is too short to assess, before any API call.

    **There is no maximum any more.** Per-mode ceilings were removed on 2026-08-25: a read
    should be as long as the thing being read, and the client-side half of the quota guard
    was never what stood between this project and a bill — `budget.py` and an F0 resource
    that physically cannot bill are. What survives is the floor, because Azure returns
    confusing errors on near-silent or sub-second audio and a 0.2 s recording is a misclick.
    """
    minimum = utils.get_float("MIN_DURATION_SECONDS")
    if seconds < minimum:
        raise AudioError(
            f"That recording is {seconds:.1f}s — too short to assess. Record at least {minimum:g}s."
        )


# A cut never starts or ends exactly on a phoneme boundary in practice, and a formant
# transition carries the identity of the sound before it. This much audio is kept either side
# so a sliced word does not begin mid-burst — small enough not to pull in a neighbouring
# vowel at connected-speech rates, where a short word runs about 200 ms.
PAD_S = 0.02


def _framerate(wav_bytes: bytes) -> int:
    """The recording's sample rate, read from its own header rather than assumed."""
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as source:
            return int(source.getframerate())
    except (wave.Error, EOFError) as exc:
        raise AudioError("That recording could not be read as WAV audio.") from exc


def slice_wav(wav_bytes: bytes, start_s: float, end_s: float, *, pad_s: float = PAD_S) -> bytes:
    """The audio between `start_s` and `end_s`, as its own WAV.

    This is what puts "how I said it" next to the native rendering on a flagged word: the
    span comes from Azure's own word offsets, so the clip is the recogniser's idea of that
    word rather than a guess at where it fell.

    Plain PCM frame arithmetic through the standard library, deliberately. A span for
    LISTENING does not need a signal-processing library, and Praat's `Extract part` returns
    the WHOLE sound on an empty range — a trap that spliced an entire recording into a clip
    once already in this project's history. Frame slicing cannot do that.

    Clamped to the recording rather than raising at the edges — a first-word span padded
    backwards starts before zero, and that is ordinary, not an error.
    """
    if end_s <= start_s:
        raise AudioError("That span has no duration.")
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as source:
            params = source.getparams()
            frames = source.readframes(params.nframes)
    except (wave.Error, EOFError) as exc:
        raise AudioError("That recording could not be read as WAV audio.") from exc

    rate = params.framerate
    width = params.sampwidth * params.nchannels
    total = len(frames) // width

    first = max(0, int((start_s - pad_s) * rate))
    last = min(total, int((end_s + pad_s) * rate))
    if last <= first:
        raise AudioError("That span falls outside the recording.")

    cut = frames[first * width : last * width]
    out = io.BytesIO()
    with wave.open(out, "wb") as sink:
        sink.setnchannels(params.nchannels)
        sink.setsampwidth(params.sampwidth)
        sink.setframerate(rate)
        sink.writeframes(cut)
    return out.getvalue()


@contextmanager
def temp_wav(wav_bytes: bytes) -> Iterator[str]:
    """Write `wav_bytes` to a temp file, yield its path, and always delete it.

    The `finally` is the point: a temp file is scratch space for one Azure call, and Azure
    raising mid-call must not leave a stray copy in the system temp directory. This is no
    longer about a no-stored-audio rule — that was lifted on 2026-08-19 and `keep` below is
    what acts on it — it is simply that scratch space gets cleaned up.
    """
    handle, path = tempfile.mkstemp(suffix=".wav")
    try:
        with os.fdopen(handle, "wb") as f:
            f.write(wav_bytes)
        yield path
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        except OSError:
            logger.warning("Could not remove temp audio file", exc_info=True)


def prepare(data: bytes) -> tuple[bytes, float]:
    """Convert, measure, and validate in one step. Returns (wav_bytes, seconds)."""
    wav_bytes = to_pcm_wav(data)
    seconds = duration_seconds(wav_bytes)
    validate_duration(seconds)
    return wav_bytes, seconds


def kept_path(sha256: str) -> Path:
    """Where the recording with this digest lives. Content-addressed, so repeats share a file.

    `attempts.audio_sha256` already holds the digest, so the path needs no separate identity
    and re-reading the same passage with byte-identical audio cannot write it twice.
    """
    return Path(utils.get("AUDIO_DIR") or "./audio/attempts") / f"{sha256}.wav"


def keep(wav_bytes: bytes, sha256: str) -> Path | None:
    """Write a recording to the kept-audio directory. None when keeping is switched off.

    Never raises into the caller: a full disk must fail the recording's *storage*, not the
    assessment the user just paid Azure for. A failure is logged and reported as None, and
    the attempt is still recorded — it simply cannot be re-derived later.

    The directory is gitignored (`audio/`). Nothing here ever leaves the machine.
    """
    if not utils.get_bool("KEEP_AUDIO"):
        return None
    path = kept_path(sha256)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(wav_bytes)
        return path
    except OSError:
        logger.warning("Could not keep the recording at %s", path, exc_info=True)
        return None
