#!/usr/bin/env python3
"""Capture a real Azure pronunciation-assessment payload and save it as a test fixture.

Run once per mode. The committed fixture is what lets the entire parsing, colouring, and
coaching layer be built and tested without spending any of the 5-hour monthly allowance,
so this is the only script in the project that deliberately costs quota — a few seconds of
it. Run inside the container, since only it has the pinned deps:

    docker compose run --rm app python scripts/capture_fixture.py \
        recording.wav reference.txt --mode drill

Writes verbatim JSON: a single object for drill, an array of per-utterance objects for
paragraph. The audio is read, never copied or stored.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import audio_utils  # noqa: E402
import budget  # noqa: E402
import db  # noqa: E402
import speech_analyzer  # noqa: E402
import utils  # noqa: E402
from utils import Mode  # noqa: E402

DEFAULT_OUT = {
    Mode.DRILL: ROOT / "tests" / "fixtures" / "sample_azure_response.json",
    Mode.PARAGRAPH: ROOT / "tests" / "fixtures" / "sample_azure_continuous.json",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", help="Recording in any ffmpeg-readable format")
    parser.add_argument("reference", help="Text file holding what was meant to be said")
    parser.add_argument("--mode", choices=["drill", "paragraph"], default="drill")
    parser.add_argument("--out", default=None, help="Defaults to the fixture path for the mode")
    args = parser.parse_args()

    utils.configure_logging(logging.INFO)
    mode = Mode(args.mode)
    out_path = Path(args.out) if args.out else DEFAULT_OUT[mode]

    if utils.offline_mode():
        print("[capture] OFFLINE_MODE is on — there is nothing to capture. Unset it first.")
        return 1

    missing = utils.check_required()
    if missing:
        print(f"[capture] missing required env vars: {', '.join(missing)}")
        return 1

    audio_path = Path(args.audio)
    if not audio_path.is_file():
        print(f"[capture] audio file not found: {audio_path}")
        return 1

    reference_text = Path(args.reference).read_text(encoding="utf-8").strip()
    if not reference_text:
        print("[capture] the reference text file is empty")
        return 1

    wav_bytes, seconds = audio_utils.prepare(audio_path.read_bytes(), mode)
    print(f"[capture] {seconds:.1f}s of audio, mode={mode.value}")
    print(f"[capture] reference: {reference_text[:120]}{'…' if len(reference_text) > 120 else ''}")

    # This script spends real quota, so it goes through the same guards as the app. Without
    # them a resource that was never confirmed as F0 — the case the app refuses to start on
    # — could still be billed from here.
    conn = db.connect()
    try:
        budget.preflight_stt(conn, seconds, mode)
    except budget.BudgetError as exc:
        print(f"[capture] refused: {exc}")
        return 1

    with audio_utils.temp_wav(wav_bytes) as wav_path:
        payloads, _, attempts = speech_analyzer.recognise(wav_path, reference_text, mode)

    # Charge the meter for what this actually consumed, so the app's remaining-allowance
    # figure stays honest rather than silently ignoring fixture captures.
    db.record_attempt(
        conn, mode=mode, reference_text=reference_text,
        recognised_text=speech_analyzer._display_text(payloads[0]),
        audio_seconds=seconds * max(attempts, 1),
        audio_sha256=utils.sha256_bytes(wav_bytes),
        overall_scores={}, azure_raw=payloads if len(payloads) > 1 else payloads[0],
        offline=False,
    )

    # Drill is a single utterance; keeping it an object rather than a one-element array
    # matches what a single-shot call actually returns.
    document = payloads[0] if mode is Mode.DRILL and len(payloads) == 1 else payloads

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")

    # `relative_to` raises on a path outside the repo, and a `--out` typed relative to the
    # working directory is one of those — which used to crash *after* the quota was spent
    # and the file written, reporting a traceback for a capture that had actually worked.
    shown = out_path.resolve()
    shown = shown.relative_to(ROOT) if shown.is_relative_to(ROOT) else shown
    print(f"[capture] wrote {shown} ({len(payloads)} utterance(s))")
    print("[capture] read it before committing — it should contain no key and no audio.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
