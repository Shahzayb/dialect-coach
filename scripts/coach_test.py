#!/usr/bin/env python3
"""One-off diagnostic: exercise the live Gemini coaching path for the price of one call.

Deliberately **spends no Azure quota**. The assessment is replayed from the committed
fixture — the same zero-cost path the app uses under OFFLINE_MODE — and only the coaching
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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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

    assessment = speech_analyzer.analyse("", REFERENCE, Mode.DRILL)
    compacted = fallback_coach.compact(assessment, Mode.DRILL)
    print(f"fixture replayed: {len(assessment.words)} words, "
          f"{len(compacted['flagged_words'])} flagged, "
          f"{len(compacted['observed_pairs'])} substitutions observed")
    print(f"payload sent: {len(json.dumps(compacted))} characters "
          f"(raw response is {len(json.dumps(assessment.raw))})")

    # Only now, and only for the coaching call.
    os.environ["OFFLINE_MODE"] = "false"
    usable, reason = ai_coach.available()
    if not usable:
        print(f"cannot reach the model: {reason}")
        return 2

    print(f"calling {ai_coach.model_name()} — one free-tier call")
    result = ai_coach.coach(assessment, REFERENCE, Mode.DRILL)

    print(f"\ncoach_source: {result.source}")
    if result.source != fallback_coach.SOURCE_GEMINI:
        print("the model path did not produce the report; see the log above")
        return 1

    usage = (result.raw or {}).get("usage_metadata") or {}
    print(f"tokens: {usage.get('prompt_token_count')} in, "
          f"{usage.get('candidates_token_count')} out, "
          f"{usage.get('total_token_count')} total")

    report = result.report
    print(f"\n{report.overall_comment}\n")
    for rank, fix in enumerate(report.priority_fixes, start=1):
        pairs = ", ".join(f"{pair.a}/{pair.b}" for pair in fix.minimal_pairs)
        print(f"{rank}. /{fix.expected_phoneme}/ -> /{fix.produced_phoneme}/  "
              f"{fix.affected_words}")
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

    # The check that matters: nothing named that Azure did not report.
    observed = {(e, p) for e, p in compacted["observed_pairs"]}
    invented = [
        (fix.expected_phoneme, fix.produced_phoneme) for fix in report.priority_fixes
        if (fix.expected_phoneme, fix.produced_phoneme) not in observed
    ]
    words = len(" ".join([report.overall_comment, report.practice_plan]).split())
    reported = {fault["fault"] for fault in compacted["delivery_faults"]}
    answered = {drill.fault for drill in report.delivery_drills}
    print(f"\ninvented pairs surviving validation: {invented or 'none'}")
    print(f"delivery faults reported: {sorted(reported) or 'none'}; "
          f"drilled: {sorted(answered) or 'none'}; "
          f"unmatched: {sorted(answered ^ reported) or 'none'}")
    print(f"re-parsed from the stored payload: "
          f"{ai_coach.report_from_raw(result.raw, result.source) is not None}")
    print(f"prose length (comment + plan): {words} words")
    # Every reported fault must come back with a drill — the model's, or a backfilled
    # template — and no drill for a fault Azure never reported.
    return 1 if invented or (answered ^ reported) else 0


if __name__ == "__main__":
    raise SystemExit(main())
