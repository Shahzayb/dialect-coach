#!/usr/bin/env python3
"""Ask Azure, once, whether the retired content-assessment fields still do anything.

**Background, because the answer is only interesting against it.** Content assessment was a
preview feature of the Speech SDK that returned vocabulary, grammar and topic scores for
unscripted speech. Microsoft retired it at SDK 1.46.0; this project pins 1.51.1. Verified by
introspecting the installed package rather than recalled:

    >>> [n for n in dir(speechsdk.PronunciationAssessmentConfig) if not n.startswith("_")]
    ['apply_to', 'enable_prosody_assessment', 'nbest_phoneme_count', 'phoneme_alphabet',
     'reference_text', 'to_json']

No content method, no content field on `PronunciationAssessmentResult`, no content `PropertyId`,
and no `contentAssessment` string anywhere in the native libraries.

What survives is the JSON route: `PronunciationAssessmentConfig(json_string=...)` passes unknown
keys through untouched — they come back out of `to_json()` unchanged — so the CLIENT can still
send `enableContentAssessment` and `contentTopic`. Whether the SERVICE still answers them cannot
be settled by introspection, only by one call.

This script is that call, made deliberately rather than folded into the app's normal path:

    docker compose run --rm app python scripts/content_probe.py clip.wav --topic "my hobby"

Use a SHORT clip — 20 seconds is plenty. Azure wants 15 s minimum for an unscripted assessment
to mean anything, and this is a yes/no question about a field, not a measurement of anybody's
speech. It runs ONE unscripted pass, not the two-pass flow, because the two-pass flow's second
pass is scripted and would not carry the fields at all.

It prints the verdict and, either way, the exact top-level keys the response carried — so the
answer can be recorded in `memory-bank/progress.md` as a fact rather than an impression.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import audio_utils  # noqa: E402
import budget  # noqa: E402
import db  # noqa: E402
import speech_analyzer  # noqa: E402
import utils  # noqa: E402
from utils import Mode  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", help="A short recording of free speech, any ffmpeg format")
    parser.add_argument("--topic", required=True, help="What the speaker was talking about")
    parser.add_argument(
        "--out",
        default=None,
        help="Where to write the verbatim payload. Defaults to not writing one.",
    )
    args = parser.parse_args()

    utils.configure_logging(logging.INFO)

    if utils.offline_mode():
        print("[probe] OFFLINE_MODE is on, so nothing would be sent. Unset it to spend the call.")
        return 1
    if not utils.get_bool("UNSCRIPTED_CONTENT_PROBE"):
        print(
            "[probe] UNSCRIPTED_CONTENT_PROBE is false, so the retired fields would not be sent "
            "and this call would answer nothing. Set it to true to run the probe."
        )
        return 1
    # One pass only. The two-pass flow's second pass is a SCRIPTED assessment, and a scripted
    # config never carries the content fields — so probing through it would return no content
    # scores for a reason that has nothing to do with whether the service still supports them.
    if utils.get_bool("UNSCRIPTED_TWO_PASS"):
        print(
            "[probe] Set UNSCRIPTED_TWO_PASS=false for this run. The two-pass flow's assessed "
            "pass is scripted, so it would not carry the content fields at all."
        )
        return 1

    config = speech_analyzer.assessment_config_json("", Mode.UNSCRIPTED, topic=args.topic)
    print(f"[probe] sending: {config}")

    conn = db.connect()
    try:
        wav_bytes, seconds = audio_utils.prepare(Path(args.audio).read_bytes(), Mode.UNSCRIPTED)
    except audio_utils.AudioError as exc:
        print(f"[probe] refused: {exc}")
        return 1

    try:
        budget.preflight_stt(conn, seconds, Mode.UNSCRIPTED)
    except budget.BudgetError as exc:
        print(f"[probe] refused: {exc}")
        return 1

    print(f"[probe] {seconds:.1f}s of audio, one unscripted pass. Calling Azure…")
    with audio_utils.temp_wav(wav_bytes) as wav_path:
        result = speech_analyzer.recognise(wav_path, "", Mode.UNSCRIPTED, topic=args.topic)

    # Charged, because it really was billable seconds and the meter derives from this table.
    db.record_attempt(
        conn,
        mode=Mode.UNSCRIPTED,
        reference_text=args.topic,
        recognised_text=result.scored_against,
        audio_seconds=seconds * max(result.attempts, 1),
        audio_sha256=utils.sha256_bytes(wav_bytes),
        overall_scores={},
        azure_raw=result.payloads if len(result.payloads) > 1 else result.payloads[0],
    )

    content = speech_analyzer.azure_content_scores(result.payloads)
    best = speech_analyzer._best(result.payloads[-1])
    assessment_keys = sorted(speech_analyzer._scores(best))

    print()
    print(f"[probe] transcript: {result.scored_against[:160]}")
    print(f"[probe] top-level payload keys: {sorted(result.payloads[-1])}")
    print(f"[probe] PronunciationAssessment keys: {assessment_keys}")
    print()
    if content:
        print(f"[probe] ANSWERED. Azure returned content scores: {content}")
        print("[probe] Record this in memory-bank/progress.md under 'What works'.")
    else:
        print("[probe] NO CONTENT SCORES. The retired fields were sent and nothing came back.")
        print("[probe] Record this in memory-bank/progress.md under 'Dead ends'.")

    if args.out:
        Path(args.out).write_text(
            json.dumps(result.payloads, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(f"[probe] verbatim payload written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
