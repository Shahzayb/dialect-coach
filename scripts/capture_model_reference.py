#!/usr/bin/env python3
"""Buy the General American reference this project measures for itself.

The benchmark passage, synthesised in a sex-stratified set of en-US neural voices and pushed
back through pronunciation assessment, so every rendering is measurable by exactly the code
that measures the user. `scripts/build_model_reference.py` turns the captures into
`src/model_reference.py`.

## Why this is worth real allowance

The only General American reference in the project is Hillenbrand et al. (1995), and
`vowel_reference.py`'s own docstring lists what that costs:

  - It covers 12 vowels. The benchmark passage deliberately carries 22, so ten categories —
    including six of the seven r-coloured ones, on the marker the brief calls the loudest and
    most correctable available — have no target at all and render "no published GA reference".
  - Its durations are citation-form /hVd/ words, which is why absolute milliseconds cannot be
    compared against it and the duration surface had to choose between honesty and usefulness.
  - It is upper-Midwest speech from the early 1990s, patched with hand-widened tolerance bands
    for the low-back merger and GOOSE-fronting rather than with current data.

Sixteen voices costs about 15,600 TTS characters of the monthly 500,000 (3.1%) and about 992
seconds of the monthly 18,000 (5.5%). Once. `scripts/capture_baseline.py` already spends 975
characters and 62 seconds on a single voice and calls the result a fixed point worth having;
this is the same trade sixteen times over, for a reference complete across the inventory,
measured in connected speech, by one segmenter, on both sides of every comparison.

A synthesiser is not a native speaker and nothing here claims it is. What earns the set its
place is that it is General American, connected, current, and — captured across sixteen
talkers — a distribution rather than one voice's idiosyncrasy.

## Running it

Refuses to spend unless told to, because a capture run that spends on a bare invocation is
exactly the accident `progress.md`'s "spend deliberately and say so" rule exists to prevent:

    docker compose run --rm app python scripts/capture_model_reference.py            # dry run
    docker compose run --rm app python scripts/capture_model_reference.py --spend

Resumable: a voice already captured for this passage is skipped, so an interrupted run costs
only the remainder. The audio lands in the gitignored `audio/native/`; the payloads land in
the local database. Neither is committed — `src/model_reference.py` is the artefact that is.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import budget  # noqa: E402
import db  # noqa: E402
import native_model  # noqa: E402
import perception_trainer  # noqa: E402
import progress_view  # noqa: E402
import utils  # noqa: E402
from native_model import MEN, WOMEN  # noqa: E402

BASELINE_FIXTURE = ROOT / "tests" / "fixtures" / "benchmark_tts_baseline.json"
BASELINE_WAV = ROOT / "audio" / "benchmark_tts_baseline.wav"

# Curated first, alphabetical after. `perception_trainer.VOICES` was chosen for spread across
# two voice generations, which is the property a reference wants too: eight voices of one
# generation are one recording character wearing eight names, and the between-voice SD they
# produce would understate how much real speakers differ. BrianNeural is added because it is
# the default `AZURE_TTS_VOICE` — the voice the user actually imitates everywhere else in the
# app — and the reference is more useful for containing it.
PREFERRED = (*perception_trainer.VOICES, "en-US-BrianNeural")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--spend",
        action="store_true",
        help="Actually synthesise and assess. Without it this only prints the plan and cost.",
    )
    parser.add_argument(
        "--per-set",
        type=int,
        default=native_model.VOICES_PER_SET,
        help=f"Voices per reference set (default {native_model.VOICES_PER_SET}).",
    )
    args = parser.parse_args()

    utils.configure_logging(logging.INFO)

    if utils.offline_mode():
        print("[reference] OFFLINE_MODE is on — there is nothing to capture. Unset it first.")
        return 1
    missing = utils.check_required()
    if missing:
        print(f"[reference] missing required env vars: {', '.join(missing)}")
        return 1

    conn = db.connect()
    try:
        budget.require_f0_acknowledgement()
    except budget.BudgetError as exc:
        print(f"[reference] refused: {exc}")
        return 1

    text = progress_view.BENCHMARK_PASSAGE
    print(
        f"[reference] benchmark passage v{progress_view.BENCHMARK_VERSION}, "
        f"{len(text)} characters, {len(text.split())} words"
    )

    # Free: the benchmark rendering was already bought once, in v0.7.0, and committed.
    if native_model.seed_from_baseline_fixture(conn, BASELINE_FIXTURE, BASELINE_WAV):
        print("[reference] seeded the committed baseline rendering — no allowance spent")
    else:
        print("[reference] no committed baseline rendering to seed from (that is fine)")

    try:
        live = native_model.fetch_roster()
    except native_model.CaptureRefused as exc:
        print(f"[reference] {exc}")
        return 1
    chosen = native_model.select_voices(live, per_set=args.per_set, preferred=PREFERRED)
    already = native_model.captured_voices(conn, text)

    for name in (MEN, WOMEN):
        names = chosen[name]
        if len(names) < native_model.MIN_VOICES_PER_SET:
            print(
                f"[reference] only {len(names)} usable {name}'s voice(s) on the live roster, "
                f"and a reference set needs {native_model.MIN_VOICES_PER_SET}. Refusing "
                f"rather than reporting an SD computed from a handful of talkers."
            )
            return 1
        print(f"[reference] {name}: {', '.join(names)}")

    todo = [v for names in chosen.values() for v in names if v not in already]
    skipped = [v for names in chosen.values() for v in names if v in already]
    if skipped:
        print(f"[reference] already captured, skipping: {', '.join(sorted(skipped))}")

    if not todo:
        print("[reference] nothing left to capture.")
        return 0

    characters, seconds = native_model.estimate(text, todo)
    tts_meter, stt_meter = budget.tts_meter(conn), budget.stt_meter(conn)
    print(
        f"\n[reference] {len(todo)} voice(s) to capture.\n"
        f"[reference] estimated cost: {characters:,} TTS characters "
        f"({characters / max(tts_meter.free_allowance, 1) * 100:.1f}% of the monthly free "
        f"tier) and {seconds:.0f} STT seconds "
        f"({seconds / max(stt_meter.free_allowance, 1) * 100:.1f}%).\n"
        f"[reference] remaining before this run: {tts_meter.remaining:,.0f} characters, "
        f"{stt_meter.remaining:,.0f} seconds."
    )

    if not args.spend:
        print("\n[reference] dry run. Re-run with --spend to actually buy this.")
        return 0

    captured, failed = 0, []
    for index, voice in enumerate(todo, start=1):
        print(f"[reference] ({index}/{len(todo)}) {voice} …")
        try:
            rendering = native_model.capture(conn, text, voice)
        except Exception as exc:  # noqa: BLE001 — one bad voice must not lose the rest
            # Resumable by design: the voices already bought are already stored, so the next
            # run starts from here rather than paying for them again.
            print(f"[reference]   FAILED: {utils.redact(str(exc))}")
            failed.append(voice)
            continue
        captured += 1
        print(f"[reference]   {rendering.seconds:.1f}s, {rendering.characters} characters")

    after_tts, after_stt = budget.tts_meter(conn), budget.stt_meter(conn)
    print(
        f"\n[reference] captured {captured} of {len(todo)}.\n"
        f"[reference] TTS used this month: {tts_meter.used:,.0f} → {after_tts.used:,.0f} "
        f"characters. STT: {stt_meter.used:,.0f} → {after_stt.used:,.0f} seconds."
    )
    if failed:
        print(f"[reference] failed: {', '.join(failed)}. Re-run to retry only those.")
    print("[reference] next: docker compose run --rm app python scripts/build_model_reference.py")
    return 0 if captured else 1


if __name__ == "__main__":
    raise SystemExit(main())
