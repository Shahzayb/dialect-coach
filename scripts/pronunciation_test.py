#!/usr/bin/env python3
"""One-off: exercise Azure's Pronunciation Assessment API (not plain STT) against a
reference phrase, and print the raw JSON with accuracy/fluency/prosody scores. Unlike
scripts/smoke_test.py, this is the call that actually scores pronunciation/accent. Not
part of the app. Run inside the container, since only it has the pinned deps:

    docker compose run --rm app python scripts/pronunciation_test.py <audio.wav> <reference.txt>

The WAV must be 16 kHz / 16-bit / mono PCM and must exist on the host path that gets
bind-mounted into the container (the project root, per compose.yaml).
"""

import json
import os
import sys

from dotenv import load_dotenv


def main() -> int:
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <path/to/16khz-mono-wav> <path/to/reference.txt>")
        return 1
    audio_path, reference_path = sys.argv[1], sys.argv[2]

    load_dotenv()
    key = os.environ.get("AZURE_SPEECH_KEY", "")
    region = os.environ.get("AZURE_SPEECH_REGION", "")
    if not key or not region:
        print("[pronunciation] missing AZURE_SPEECH_KEY / AZURE_SPEECH_REGION")
        return 1

    if not os.path.isfile(audio_path):
        print(f"[pronunciation] audio file not found: {audio_path}")
        return 1

    with open(reference_path, encoding="utf-8") as f:
        reference_text = f.read().strip()

    import azure.cognitiveservices.speech as speechsdk

    speech_config = speechsdk.SpeechConfig(subscription=key, region=region)
    speech_config.speech_recognition_language = "en-US"
    audio_config = speechsdk.AudioConfig(filename=audio_path)

    pron_config = speechsdk.PronunciationAssessmentConfig(
        reference_text=reference_text,
        grading_system=speechsdk.PronunciationAssessmentGradingSystem.HundredMark,
        granularity=speechsdk.PronunciationAssessmentGranularity.Phoneme,
        enable_miscue=True,
    )
    pron_config.enable_prosody_assessment()

    recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config, audio_config=audio_config)
    pron_config.apply_to(recognizer)

    result = recognizer.recognize_once_async().get()

    if result.reason != speechsdk.ResultReason.RecognizedSpeech:
        print(f"[pronunciation] FAIL — reason: {result.reason}")
        if result.reason == speechsdk.ResultReason.Canceled:
            details = result.cancellation_details
            print(f"[pronunciation] canceled: {details.reason}, {details.error_details}")
        return 1

    print(f"[pronunciation] recognized: {result.text!r}")
    raw = json.loads(result.json)
    print(json.dumps(raw, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
