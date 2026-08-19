"""Azure neural text-to-speech for target playback.

The point of this module, from the brief: *"I can't hear the difference between my
pronunciation and a native speaker's."* Scores do not fix that; audio does. Azure Speech F0
includes 0.5 M characters of neural TTS a month, which at a few words per click is
effectively unlimited — the meter exists to catch misconfiguration, not to ration.

One trap dominates this file. `SpeechSynthesizer`'s `audio_config` parameter does **not**
default to `None` — it defaults to an `AudioOutputConfig` bound to the default speaker. Omit
it and the SDK synthesises to a sound device the container does not have: no exception, no
`audio_data`, and a call that consumed allowance for nothing. `audio_config=None` is what
asks for the bytes back, and it is as easy to leave out as `apply_to` is in
`speech_analyzer`.

Like that module, this one makes no spend decisions of its own. `app.py` brackets the call
with `budget.preflight_tts` and `db.record_tts_usage`, so the meter and the guard stay in
one place rather than being split across every caller.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from xml.sax.saxutils import escape as xml_escape, quoteattr

import utils
from speech_analyzer import classify_cancellation

logger = logging.getLogger(__name__)

LOCALE = "en-US"



# --- The disk cache ---------------------------------------------------------------------
# THIS CACHES SYNTHESISED AUDIO ONLY, AND NEVER A BYTE OF RECORDED INPUT. The project's
# no-stored-audio rule exists for privacy and covers the *user's* recordings; a neural voice
# reading "think" carries no personal data, and re-buying it every restart is a charge for
# nothing. The perception trainer plays the same few dozen words across four voices over and
# over, so without this the same corpus is paid for on every run.
#
# Keyed by (voice, text, rate) — the three things that change the bytes. `rate` is
# NORMAL_RATE for a plain-text call and the SSML rate string otherwise, so a slow rendering
# is a separate entry rather than a collision with the normal one.

NORMAL_RATE = "normal"

CACHE_DIR_VAR = "TTS_CACHE_DIR"
DEFAULT_CACHE_DIR = "./data/tts_cache"


def cache_dir() -> Path:
    """Where synthesised clips live. Under `data/`, which is gitignored in full."""
    return Path(utils.get(CACHE_DIR_VAR) or DEFAULT_CACHE_DIR)


def cache_key(voice: str, text: str, rate: str = NORMAL_RATE) -> str:
    """A stable filename for one clip.

    Hashed rather than slugified because `text` is arbitrary — a phrase with a slash or a
    hundred characters of punctuation is not a filename, and truncating one silently
    collides two different clips onto the same bytes.
    """
    digest = hashlib.sha256()
    digest.update(voice.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(text.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(rate.encode("utf-8"))
    return digest.hexdigest()


def cache_path(voice: str, text: str, rate: str = NORMAL_RATE) -> Path:
    return cache_dir() / f"{cache_key(voice, text, rate)}.wav"


def cached_audio(voice: str, text: str, rate: str = NORMAL_RATE) -> bytes | None:
    """The stored clip, or None. An unreadable file is a miss, never an error."""
    path = cache_path(voice, text, rate)
    try:
        return path.read_bytes() if path.is_file() else None
    except OSError:
        logger.warning("Could not read the cached clip at %s; re-synthesising", path)
        return None


def store_audio(voice: str, text: str, audio: bytes, rate: str = NORMAL_RATE) -> None:
    """Write a clip to the cache. A failure here costs quota next time, not correctness."""
    path = cache_path(voice, text, rate)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write beside the target and rename, so an interrupted write cannot leave a
        # truncated WAV that every later run reads as a valid cache hit.
        temporary = path.with_suffix(".part")
        temporary.write_bytes(audio)
        temporary.replace(path)
    except OSError:
        logger.warning("Could not cache the clip at %s; it will be synthesised again", path)


# Slow enough that an unfamiliar phoneme separates out from its neighbours, not so slow the
# word stops sounding like speech. Hearing a contrast you cannot catch at conversational
# speed is the whole reason this option exists.
SLOW_RATE = "-35%"

BAD_REQUEST_HINT = "Check the voice name in AZURE_TTS_VOICE, and the text being synthesised."


class SynthesisError(RuntimeError):
    """Synthesis failed in a way worth showing the user. Never carries a key."""


@dataclass
class Synthesis:
    """One synthesised phrase: the audio, and what it should be billed as."""

    audio: bytes
    characters: int
    voice: str
    # How many times the text was actually sent. A retry re-sends it and can consume
    # allowance even when it ultimately fails, so the meter multiplies by this — the same
    # reasoning as `Assessment.attempts`.
    attempts: int = 1


def voice_name() -> str:
    """The configured neural voice. `AZURE_TTS_VOICE` has a default, so this cannot fail."""
    return utils.get("AZURE_TTS_VOICE") or "en-US-BrianNeural"


def slow_ssml(text: str, voice: str, rate: str = SLOW_RATE) -> str:
    """Wrap `text` in SSML that slows delivery without changing pitch.

    Pure, so the escaping and the structure can be asserted without an SDK or a network.
    `xml_escape` is not decoration: the text comes from the reference textarea, and a single
    unescaped `&` or `<` makes Azure reject the whole request as malformed rather than
    mispronouncing one word.
    """
    return (
        f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
        f'xml:lang="{LOCALE}">'
        f"<voice name={quoteattr(voice)}>"
        f"<prosody rate={quoteattr(rate)}>{xml_escape(text)}</prosody>"
        f"</voice></speak>"
    )


def payload_for(text: str, *, slow: bool, voice: str | None = None) -> str:
    """Exactly the string that will be sent to Azure — which is exactly what is billed.

    Exposed so the pre-flight guard can price a call before making it without rebuilding
    the SSML itself. One construction, so the estimate and the charge cannot disagree.
    """
    return slow_ssml(text, voice or voice_name()) if slow else text


def _speak(payload: str, voice: str, *, is_ssml: bool) -> bytes:
    """Send one synthesis request and return the audio bytes.

    The single seam that touches the SDK, so everything around it — retry accounting,
    character counting, error mapping — is testable without a network or a key.
    """
    import azure.cognitiveservices.speech as speechsdk

    config = speechsdk.SpeechConfig(
        subscription=utils.require("AZURE_SPEECH_KEY"),
        region=utils.require("AZURE_SPEECH_REGION"),
    )
    config.speech_synthesis_voice_name = voice
    # RIFF PCM out, so `st.audio(..., format="audio/wav")` plays it directly and nothing has
    # to round-trip through ffmpeg to be playable.
    config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Riff24Khz16BitMonoPcm
    )

    # audio_config=None is mandatory — see the module docstring. The default is the speaker.
    synthesizer = speechsdk.SpeechSynthesizer(speech_config=config, audio_config=None)

    future = (
        synthesizer.speak_ssml_async(payload) if is_ssml
        else synthesizer.speak_text_async(payload)
    )
    result = future.get()

    if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
        audio = bytes(result.audio_data or b"")
        if not audio:
            raise SynthesisError("Azure reported success but returned no audio.")
        return audio
    if result.reason == speechsdk.ResultReason.Canceled:
        raise classify_cancellation(
            result.cancellation_details, bad_request_hint=BAD_REQUEST_HINT
        )
    raise SynthesisError(f"Unexpected synthesis result: {result.reason}")


def synthesise(text: str, *, voice: str | None = None, slow: bool = False,
               on_attempt: Callable[[int], None] | None = None) -> Synthesis:
    """Synthesise `text` with the configured neural voice. Returns audio and billing info.

    `characters` counts the **full payload sent to Azure**, SSML markup included. If Azure
    excludes markup from billing this over-counts; over-counting is the correct direction
    for a spend guard, and erring toward "less remaining than you think" is the rule the
    rest of `budget.py` already follows.

    `on_attempt` is called with the attempt number before each try. A caller that pays per
    call needs it on the *failing* path too: when every attempt fails, the exception carries
    no attempt count, but the text still reached Azure each time and may have been charged.
    """
    text = (text or "").strip()
    if not text:
        raise SynthesisError("There is nothing to say — the text is empty.")

    if utils.offline_mode():
        # The UI disables the buttons; this is the backstop that makes OFFLINE_MODE's
        # "no network call, ever" contract true no matter which path reaches here.
        raise SynthesisError(
            "OFFLINE_MODE is on, so nothing can be synthesised — there is no fixture to "
            "replay for audio the way there is for an assessment. Unset it to hear this."
        )

    chosen = voice or voice_name()
    payload = payload_for(text, slow=slow, voice=chosen)

    made = 0

    def count(attempt: int) -> None:
        nonlocal made
        made = attempt
        if on_attempt is not None:
            on_attempt(attempt)

    audio = utils.retry_transient(
        lambda: _speak(payload, chosen, is_ssml=slow), on_attempt=count
    )
    if made > 1:
        logger.warning("Synthesis took %d attempts; all of them may have cost quota.", made)

    return Synthesis(
        audio=audio, characters=len(payload), voice=chosen, attempts=max(made, 1)
    )
