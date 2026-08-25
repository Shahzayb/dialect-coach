#!/usr/bin/env python3
"""One-off diagnostic: exercise the live Gemini annotation path for the price of one call.

Since 2026-08-25 Gemini's only job is the prosody annotation — the same words marked up with
stress, pauses and linking. The coaching is `fallback_coach`'s and needs no network, so this
script prints it for comparison but does not test a model path for it.

Deliberately **spends no Azure quota**. The assessment is replayed from the committed
fixture — the same zero-cost path the app uses under OFFLINE_MODE — and only the annotation
call is real. That makes this cheap enough to re-run whenever the model ID, the SDK or the
schema changes, which is exactly when the model path breaks.

Run it from the container, which is the only place the pinned SDKs live:

    docker compose run --rm app python scripts/coach_test.py

`OFFLINE_FIXTURE` picks which committed payload is replayed, so the delivery half of the
report can be exercised too — neither capture carries a break or an intonation fault:

    docker compose run --rm -e OFFLINE_FIXTURE=synthetic_delivery_faults.json app \
        python scripts/coach_test.py

Not collected by pytest: `pytest.ini` scopes collection to `tests/`, and this file makes a
real, billable-in-principle API call.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

REFERENCE = (
    "The weather this month has been rather unpredictable. Thursday brought thunder "
    "and thick clouds, while Wednesday stayed warm and clear."
)


def main() -> int:
    # Replay the fixture first, with the offline contract still in force: no Azure call.
    os.environ["OFFLINE_MODE"] = "true"

    import utils

    utils.configure_logging()

    import ai_coach
    import fallback_coach
    import speech_analyzer
    from utils import Mode

    assessment = speech_analyzer.analyse("", REFERENCE, Mode.PARAGRAPH)
    compacted = fallback_coach.compact(assessment, Mode.PARAGRAPH)
    print(
        f"fixture replayed: {len(assessment.words)} words, "
        f"{len(compacted['flagged_words'])} flagged, "
        f"{len(compacted['observed_pairs'])} substitutions observed"
    )
    print(
        f"payload sent: {len(json.dumps(compacted))} characters "
        f"(raw response is {len(json.dumps(assessment.raw))})"
    )

    # The coach runs offline and always has an answer. Printed first, so a run that cannot
    # reach Gemini still shows what the page would render without it.
    report = fallback_coach.build(assessment, Mode.PARAGRAPH)
    print(f"\ncoach (offline, always available)\n{report.overall_comment}\n")
    for rank, fix in enumerate(report.priority_fixes, start=1):
        pairs = ", ".join(f"{pair.a}/{pair.b}" for pair in fix.minimal_pairs)
        print(f"{rank}. /{fix.expected_phoneme}/ -> /{fix.produced_phoneme}/  {fix.affected_words}")
        print(f"   why: {fix.why_it_matters}")
        print(f"   how: {fix.articulation}")
        print(f"   pairs: {pairs}")
    for drill in report.delivery_drills:
        print(f"\n[{drill.fault}] {drill.span}")
        print(f"   what: {drill.what_happened}")
        print(f"   drill: {drill.drill}")
    print(f"\nstress/rhythm: {report.stress_and_rhythm.issues}")
    print(f"drill: {report.stress_and_rhythm.drill}")
    print(f"\n{report.practice_plan}")

    # Only now, and only for the annotation call.
    os.environ["OFFLINE_MODE"] = "false"
    usable, reason = ai_coach.available()
    if not usable:
        print(f"\ncannot reach the model: {reason}")
        return 2

    print(f"\ncalling {ai_coach.model_name()} — one free-tier call")
    outcome = ai_coach.annotate(assessment, REFERENCE, Mode.PARAGRAPH)
    if outcome.annotation is None:
        print(f"no annotation: {outcome.reason}")
        return 1

    usage = (outcome.raw or {}).get("usage_metadata") or {}
    print(
        f"tokens: {usage.get('prompt_token_count')} in, "
        f"{usage.get('candidates_token_count')} out, "
        f"{usage.get('total_token_count')} total"
    )

    marks = {"none": "", "minor": " |", "major": " ‖"}
    rendered = " ".join(
        (word.word.upper() if word.stress else word.word)
        + ("⁀" if word.linked else "")
        + marks.get(word.break_after, "")
        for word in outcome.annotation.words
    )
    print(f"\n{rendered}\n")
    print(outcome.annotation.summary)

    # The check that matters: the model returned the passage it was given, not a rewrite.
    given = ai_coach.words_of(REFERENCE)
    returned = [word.word for word in outcome.annotation.words]
    print(f"\nwords given: {len(given)}; returned: {len(returned)}; identical: {given == returned}")
    stressed = sum(1 for word in outcome.annotation.words if word.stress)
    breaks = sum(1 for word in outcome.annotation.words if word.break_after != "none")
    print(f"stressed: {stressed}; breaks marked: {breaks}")
    print(
        f"re-parsed from the stored payload: "
        f"{ai_coach.annotation_from_raw(outcome.raw) is not None}"
    )
    return 0 if given == returned else 1


if __name__ == "__main__":
    raise SystemExit(main())
