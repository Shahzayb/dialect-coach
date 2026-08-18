#!/usr/bin/env python3
"""One-off smoke test: confirm AZURE_SPEECH_* and GEMINI_API_KEY actually work.

Makes exactly one real Azure STT call (recognize_once on a local WAV file) and
one real Gemini call. Not part of the app — delete or ignore once the keys are
confirmed. Run inside the container, since only it has the pinned deps:

    docker compose run --rm app python scripts/smoke_test.py <path/to/audio.wav>

The WAV must be 16 kHz / 16-bit / mono PCM and must exist on the host path that
gets bind-mounted into the container (the project root, per compose.yaml).
"""

import os
import sys

from dotenv import load_dotenv

REQUIRED_VARS = ["AZURE_SPEECH_KEY", "AZURE_SPEECH_REGION", "GEMINI_API_KEY"]


def check_env() -> dict[str, str]:
    load_dotenv()
    values = {name: os.environ.get(name, "") for name in REQUIRED_VARS}
    missing = [name for name, value in values.items() if not value]
    if missing:
        print(f"[smoke] missing required env vars: {', '.join(missing)}")
        sys.exit(1)
    return values


def test_azure_stt(key: str, region: str, audio_path: str) -> bool:
    import azure.cognitiveservices.speech as speechsdk

    if not os.path.isfile(audio_path):
        print(f"[azure] audio file not found: {audio_path}")
        return False

    speech_config = speechsdk.SpeechConfig(subscription=key, region=region)
    audio_config = speechsdk.AudioConfig(filename=audio_path)
    recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config, audio_config=audio_config)

    result = recognizer.recognize_once_async().get()

    if result.reason == speechsdk.ResultReason.RecognizedSpeech:
        print(f"[azure] PASS — recognized: {result.text!r}")
        print(f"[azure] raw json:\n{result.json}")
        return True
    if result.reason == speechsdk.ResultReason.NoMatch:
        print(f"[azure] connected fine, but no speech recognized in {audio_path} — "
              f"key/region are valid, audio just didn't transcribe")
        return True
    if result.reason == speechsdk.ResultReason.Canceled:
        details = result.cancellation_details
        print(f"[azure] FAIL — canceled: {details.reason}, {details.error_details}")
        return False
    print(f"[azure] FAIL — unexpected reason: {result.reason}")
    return False


def test_gemini(api_key: str, model: str) -> bool:
    from google import genai

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model,
        contents="Reply with exactly one word: OK",
    )
    text = (response.text or "").strip()
    if text:
        print(f"[gemini] PASS — replied: {text!r}")
        print(f"[gemini] raw json:\n{response.model_dump_json(exclude_none=True, indent=2)}")
        return True
    print("[gemini] FAIL — empty response")
    return False


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <path/to/16khz-mono-wav>")
        return 1
    audio_path = sys.argv[1]

    env = check_env()
    model = os.environ.get("GEMINI_MODEL") or "gemini-3.6-flash"

    azure_ok = test_azure_stt(env["AZURE_SPEECH_KEY"], env["AZURE_SPEECH_REGION"], audio_path)
    gemini_ok = test_gemini(env["GEMINI_API_KEY"], model)

    print(f"[smoke] azure={'PASS' if azure_ok else 'FAIL'} gemini={'PASS' if gemini_ok else 'FAIL'}")
    return 0 if (azure_ok and gemini_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
