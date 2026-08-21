#!/usr/bin/env python3
"""Capture the rhythm baseline: the benchmark passage as Azure TTS reads it.

Run once, ever. This is the fixed point every nPVI figure in the project is compared
against, and the reason it exists is that no other comparison is available:

    Published General American nPVI bands come from hand-segmented corpora reading
    different material, and nPVI is sensitive both to the segmentation method and to the
    text. Scoring Azure-derived durations against a published band compares three things
    at once. This synthesises the SAME text and pushes it through the SAME pipeline —
    same segmenter, same code, one variable.

It is a reference point, not ground truth for "native": it is a synthesiser, and a
synthesiser's rhythm is its own. What earns it the place is that it does not move.

Costs 975 TTS characters and about 62 seconds of STT, once, against free tiers of 500,000
characters and 18,000 seconds a month. (62, not the ~85 a human takes over the same passage:
the neural voice reads it faster than the estimate assumed.) Both are metered like any other
call so the app's remaining-allowance figure stays honest. Run inside the container, since only
it has the pinned deps:

    docker compose run --rm app python scripts/capture_baseline.py

Writes tests/fixtures/benchmark_tts_baseline.json, which IS committed — it is JSON, carries
no key and no audio, and committing it means the baseline survives a fresh clone without
re-spending quota. The WAV is written to audio/, which is NOT committed; it is kept only so
the passage can be re-assessed later without paying for synthesis again.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import audio_utils  # noqa: E402
import budget  # noqa: E402
import db  # noqa: E402
import progress_view  # noqa: E402
import rhythm  # noqa: E402
import speech_analyzer  # noqa: E402
import tts  # noqa: E402
import utils  # noqa: E402
from utils import Mode  # noqa: E402

DEFAULT_OUT = ROOT / "tests" / "fixtures" / "benchmark_tts_baseline.json"
DEFAULT_WAV = ROOT / "audio" / "benchmark_tts_baseline.wav"

# The passage is always read in paragraph mode — it is ~196 words, far past what single-shot
# handles — so the baseline must be captured the same way. A baseline captured through a
# different mode would go through a different merge and stop being one variable.
MODE = Mode.PARAGRAPH


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=None, help=f"Defaults to {DEFAULT_OUT.name}")
    parser.add_argument("--wav", default=None, help=f"Defaults to {DEFAULT_WAV}")
    parser.add_argument(
        "--force", action="store_true", help="Overwrite an existing baseline instead of refusing"
    )
    args = parser.parse_args()

    utils.configure_logging(logging.INFO)
    out_path = Path(args.out) if args.out else DEFAULT_OUT
    wav_path = Path(args.wav) if args.wav else DEFAULT_WAV

    if utils.offline_mode():
        print("[baseline] OFFLINE_MODE is on — there is nothing to capture. Unset it first.")
        return 1

    missing = utils.check_required()
    if missing:
        print(f"[baseline] missing required env vars: {', '.join(missing)}")
        return 1

    # Refusing by default rather than overwriting. Re-capturing silently would spend quota
    # and, worse, move the fixed point every stored reading is plotted against — the one
    # thing about the baseline that must not change without someone meaning it.
    if out_path.exists() and not args.force:
        print(
            f"[baseline] {out_path.name} already exists. The baseline is meant to be "
            f"captured once and left alone — re-capturing moves the line every past "
            f"reading is measured against. Pass --force if that is really what you want."
        )
        return 1

    text = progress_view.BENCHMARK_PASSAGE
    voice = tts.voice_name()

    # The plain text, never `slow_ssml`. Slowed synthesis would stretch exactly the durations
    # this exists to measure, and the resulting nPVI would describe the SSML, not the voice.
    payload = tts.payload_for(text, slow=False, voice=voice)
    print(
        f"[baseline] voice={voice}, benchmark v{progress_view.BENCHMARK_VERSION}, "
        f"{len(payload)} characters"
    )

    conn = db.connect()
    try:
        budget.preflight_tts(conn, len(payload))
    except budget.BudgetError as exc:
        print(f"[baseline] refused: {exc}")
        return 1

    synthesis = tts.synthesise(text, voice=voice, slow=False)
    db.record_tts_usage(
        conn,
        characters=synthesis.characters * max(synthesis.attempts, 1),
        voice=synthesis.voice,
    )
    print(f"[baseline] synthesised {len(synthesis.audio)} bytes")

    wav_bytes, seconds = audio_utils.prepare(synthesis.audio, MODE)
    print(f"[baseline] {seconds:.1f}s of audio")

    try:
        budget.preflight_stt(conn, seconds, MODE)
    except budget.BudgetError as exc:
        # The TTS half is already spent and already metered. Keeping the WAV means a retry
        # costs only the STT half rather than starting over.
        wav_path.parent.mkdir(parents=True, exist_ok=True)
        wav_path.write_bytes(wav_bytes)
        print(f"[baseline] refused before assessment: {exc}")
        print(
            f"[baseline] the audio is kept at {wav_path} — re-run to assess it without "
            f"paying for synthesis again."
        )
        return 1

    with audio_utils.temp_wav(wav_bytes) as temp_path:
        recognition = speech_analyzer.recognise(temp_path, text, MODE)
    payloads, attempts = recognition.payloads, recognition.attempts

    # Marked, so the Progress tab can never plot the synthesiser's reading as the user's own.
    # Recorded rather than skipped because this really was billable seconds and the meter
    # derives from this table — the row must be honest about the money and about the voice.
    db.record_attempt(
        conn,
        mode=MODE,
        reference_text=f"{rhythm.BASELINE_CAPTURE_MARKER} {voice}",
        recognised_text=speech_analyzer._display_text(payloads[0]),
        audio_seconds=seconds * max(attempts, 1),
        audio_sha256=utils.sha256_bytes(wav_bytes),
        overall_scores={},
        azure_raw=payloads if len(payloads) > 1 else payloads[0],
        offline=False,
    )

    wav_path.parent.mkdir(parents=True, exist_ok=True)
    wav_path.write_bytes(wav_bytes)

    # The voice and the benchmark version are stored beside the payloads, not inferred later.
    # A different AZURE_TTS_VOICE is a different baseline, and an edited passage is a
    # different series — a fixed point whose provenance is unrecorded cannot be told apart
    # from the reading it is supposed to anchor.
    document = {
        "voice": synthesis.voice,
        "captured_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "benchmark_version": progress_view.BENCHMARK_VERSION,
        "mode": MODE.value,
        "reference_text": text,
        "payloads": payloads,
    }
    # Compact, unlike the other capture script's indent=2. This payload is 196 words with
    # five phoneme alternates each: indented it is 814 kB, compact 281 kB — in line with the
    # largest fixture already committed. Nothing reads a dump this size by eye anyway.
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(document, ensure_ascii=False) + "\n", encoding="utf-8")

    rhythm.reset_baseline_cache()
    captured = rhythm.baseline(out_path)
    if captured is None or not captured.rhythm.measured:
        print(
            "[baseline] WARNING: the payload was written but produced no nPVI. Check it "
            "before committing — a baseline that cannot be measured is not a baseline."
        )
        return 1

    shown = out_path.resolve()
    shown = shown.relative_to(ROOT) if shown.is_relative_to(ROOT) else shown
    print(f"[baseline] wrote {shown} ({len(payloads)} utterance(s))")
    print(
        f"[baseline] nPVI {captured.rhythm.npvi:.2f} over {captured.rhythm.pairs} pairs "
        f"in {captured.rhythm.runs} runs"
    )
    print(f"[baseline] audio kept at {wav_path} (not committed)")
    print("[baseline] read the JSON before committing — it should contain no key.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
