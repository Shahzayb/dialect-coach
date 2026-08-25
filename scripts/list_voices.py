#!/usr/bin/env python3
"""Print the live en-US neural voice roster, by introspection rather than from memory.

Run this BEFORE changing `AZURE_TTS_VOICE`. The roster changes without notice — voices are
added, renamed and retired — and a configured name that no longer exists fails as a
`BadRequest` at synthesis time, after the pre-flight has already approved the spend.

This lists voices. It synthesises nothing, so it should charge no characters, and the script
proves that rather than asserting it: the TTS meter is read before and after and the two are
compared. Run inside the container, since only it has the pinned SDK:

    docker compose run --rm app python scripts/list_voices.py

The configured voice is what every "hear the native version" on the Analyze page is
rendered with, so it is the reference this whole application compares against. Choose a
General American neural voice and then leave it alone: changing it changes what every
stored `native_renderings` cache key means.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import db  # noqa: E402
import tts  # noqa: E402
import utils  # noqa: E402

LOCALE = "en-US"


def main() -> int:
    utils.configure_logging()

    if utils.offline_mode():
        print("[voices] OFFLINE_MODE is on, and this needs a real call. Unset it first.")
        return 1

    missing = utils.check_required()
    if missing:
        print(f"[voices] Missing required settings: {', '.join(missing)}")
        return 1

    conn = db.connect()
    before = db.monthly_tts_characters(conn)

    import azure.cognitiveservices.speech as speechsdk

    config = speechsdk.SpeechConfig(
        subscription=utils.require("AZURE_SPEECH_KEY"),
        region=utils.require("AZURE_SPEECH_REGION"),
    )
    # audio_config=None for the same reason tts.py needs it: the default binds a speaker the
    # container does not have. Nothing is synthesised here, but the object is still built.
    synthesizer = speechsdk.SpeechSynthesizer(speech_config=config, audio_config=None)

    result = synthesizer.get_voices_async(LOCALE).get()
    if result.reason != speechsdk.ResultReason.VoicesListRetrieved:
        print(
            f"[voices] Could not list voices: {result.reason} — "
            f"{utils.redact(str(getattr(result, 'error_details', '')))}"
        )
        return 1

    voices = sorted(result.voices, key=lambda v: v.short_name)
    print(f"[voices] {len(voices)} voices for {LOCALE}:\n")
    for voice in voices:
        gender = getattr(voice.gender, "name", str(voice.gender))
        kind = getattr(voice.voice_type, "name", str(voice.voice_type))
        styles = ", ".join(voice.style_list or []) or "-"
        print(f"  {voice.short_name:<34} {gender:<8} {kind:<22} styles: {styles}")

    available = {voice.short_name for voice in voices}
    configured = tts.voice_name()
    present = configured in available
    print(f"\n[voices] AZURE_TTS_VOICE is {configured}: {'ok' if present else 'GONE'}")
    absent = [] if present else [configured]
    if absent:
        print(
            "\n[voices] The configured voice no longer exists. Every playback on the Analyze "
            "page would fail at synthesis time, after the spend guard has already approved "
            "it. Set AZURE_TTS_VOICE to a name from the list above."
        )

    after = db.monthly_tts_characters(conn)
    print(
        f"\n[voices] TTS meter before {before}, after {after} — "
        f"{'unchanged, as expected' if before == after else 'CHANGED, which it should not'}"
    )
    return 0 if before == after and not absent else (0 if before == after else 1)


if __name__ == "__main__":
    raise SystemExit(main())
