"""Seed a throwaway database with a month of history, so the progress view can be looked at.

The progress view's exit criterion is that a seeded 30-day history renders as a trajectory.
That cannot be checked on the real database: the benchmark series starts empty on the day the
feature ships, and the real 30-day check is a calendar item, not something a merge can prove.
This script produces the picture the plumbing should draw, and nothing more.

Run it, then point the app at what it wrote:

    docker compose run --rm app python scripts/seed_progress_history.py
    DB_PATH=data/seed_demo.db make up

**No network, no key, zero spend.** Free-practice rows replay the committed Azure fixtures.
Benchmark rows carry a payload built here from the benchmark passage itself — replaying a
fixture whose words are a different text would mark two hundred words as omitted on every
benchmark read and bury the rankings under that noise.

It writes to `data/seed_demo.db` by default and refuses to touch the configured `DB_PATH`:
these rows never happened, and the usage meter derives from the same table.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
import progress_view  # noqa: E402
import utils  # noqa: E402
from utils import Mode  # noqa: E402

DEFAULT_PATH = "data/seed_demo.db"
FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

# Read every seventh day, which is the cadence the passage was written for.
BENCHMARK_EVERY_DAYS = 7

# The free-practice texts. Both are the reference the committed fixtures were captured
# against, so the payloads parse without inventing miscues.
FIXTURE_REFERENCE = (
    "The weather this month has been rather unpredictable. Thursday brought Thunder and "
    "thick clouds, while Wednesday stayed warm and clear."
)

# Which passage words the seeded speaker keeps getting wrong, and what they produce instead.
# Chosen from the sounds the passage exists to measure, so the seeded rankings show what a
# real ranking would show rather than an arbitrary scatter. SYNTHETIC — invented here for a
# demo database, never captured from Azure.
_TROUBLE: dict[str, tuple[str, str]] = {
    "three": ("θ", "t"), "things": ("θ", "t"), "third": ("θ", "t"), "thoughts": ("θ", "s"),
    "month": ("θ", "s"), "breath": ("θ", "f"), "thought": ("θ", "t"),
    "brother": ("ð", "d"), "breathe": ("ð", "d"), "whether": ("ð", "d"),
    "value": ("v", "w"), "vowel": ("v", "w"), "believe": ("v", "w"),
    "world": ("l", "ɹ"), "school": ("l", "ɹ"), "careful": ("l", "ɹ"), "full": ("l", "ɹ"),
    "asked": ("t", ""), "helped": ("t", ""), "next": ("t", ""), "first": ("t", ""),
    "sure": ("ʃ", "s"), "short": ("ʃ", "s"),
    "judge": ("dʒ", "z"), "joy": ("dʒ", "z"),
}

_TICKS_PER_SECOND = 10_000_000


def _word_payload(word: str, offset: int, duration: int, accuracy: float,
                  substitution: tuple[str, str] | None) -> dict:
    """One word in Azure's own shape, down to the phoneme.

    Only the fields this project's parser reads are filled in. Note `ErrorType` and
    `Feedback` sit *inside* `PronunciationAssessment` — the flat placement in Azure's REST
    documentation is the trap this codebase already learned about the hard way.
    """
    phonemes = []
    if substitution:
        expected, produced = substitution
        phonemes.append({
            "Phoneme": expected,
            "PronunciationAssessment": {
                "AccuracyScore": accuracy,
                # No differing alternate means "weakened or dropped", which is what final
                # cluster simplification looks like — the parser buckets it as (unclear).
                "NBestPhonemes": ([{"Phoneme": produced, "Score": 96.0},
                                   {"Phoneme": expected, "Score": accuracy}]
                                  if produced else [{"Phoneme": expected, "Score": accuracy}]),
            },
        })
    return {
        "Word": word,
        "Offset": offset,
        "Duration": duration,
        "PronunciationAssessment": {
            "AccuracyScore": accuracy,
            "ErrorType": "Mispronunciation" if accuracy < utils.WORD_RED else "None",
            "Feedback": {"Prosody": {"Break": {"ErrorTypes": ["None"], "BreakLength": 0},
                                     "Intonation": {"ErrorTypes": []}}},
        },
        "Syllables": [],
        "Phonemes": phonemes,
    }


def benchmark_payload(rng: random.Random, scores: dict[str, float], skill: float) -> dict:
    """An Azure payload for one reading of the benchmark passage.

    `skill` runs 0 (first read) to 1 (last): the troublesome words climb out of the red as it
    rises, so the seeded flagged-word ranking thins over the month the way a real one would.
    """
    words = utils.normalise_words(progress_view.BENCHMARK_PASSAGE)
    payload_words = []
    offset = 0
    for word in words:
        substitution = _TROUBLE.get(word)
        if substitution:
            accuracy = min(99.0, 52.0 + 34.0 * skill + rng.uniform(-6.0, 6.0))
            substitution = substitution if accuracy < utils.WORD_AMBER else None
        else:
            # Clean words sit above the amber cut so they are not flagged. A real reading
            # trips over the odd ordinary word too, so one in twelve dips below it — but
            # if every word dipped, "the" and "i" would head the flagged-word ranking and
            # the seeded picture would say nothing about the sounds being measured.
            accuracy = (rng.uniform(84.0, 94.0) if rng.random() < 0.08
                        else rng.uniform(96.0, 100.0))
        duration = int(_TICKS_PER_SECOND * rng.uniform(0.22, 0.42))
        payload_words.append(_word_payload(word, offset, duration, round(accuracy, 1),
                                           substitution))
        offset += duration + int(_TICKS_PER_SECOND * 0.04)

    return {
        "RecognitionStatus": "Success",
        "Offset": 0,
        "Duration": offset,
        "DisplayText": progress_view.BENCHMARK_PASSAGE,
        "NBest": [{
            "Display": progress_view.BENCHMARK_PASSAGE,
            "PronunciationAssessment": {
                "AccuracyScore": scores["accuracy"],
                "FluencyScore": scores["fluency"],
                "ProsodyScore": scores["prosody"] if scores["prosody"] is not None else 0.0,
                "CompletenessScore": scores["completeness"],
                "PronScore": scores["pron_score"],
            },
            "Words": payload_words,
        }],
    }


def _scores(rng: random.Random, skill: float, *, spread: float) -> dict[str, float | None]:
    """Four scores on a gentle upward trend with jitter. Prosody is None one time in nine.

    That None is deliberate: the chart has to render a NULL prosody as a gap and never as a
    zero, and a seeded history that never contains one cannot show whether it does.
    """
    def value(base: float, gain: float) -> float:
        return round(min(99.5, max(35.0, base + gain * skill + rng.uniform(-spread, spread))), 1)

    return {
        "pron_score": value(72.0, 16.0),
        "accuracy": value(78.0, 14.0),
        "fluency": value(70.0, 18.0),
        "completeness": 100.0,
        "prosody": None if rng.random() < 0.11 else value(66.0, 20.0),
    }


def seed(path: str, *, days: int, seed_value: int) -> int:
    """Write `days` of history and return how many attempts were written."""
    configured = str(utils.get("DB_PATH"))
    if Path(path).resolve() == Path(configured).resolve():
        raise SystemExit(
            f"Refusing to seed {path}: that is the configured DB_PATH. These attempts never "
            f"happened and the usage meter is derived from the same table. Pass --path."
        )

    rng = random.Random(seed_value)
    conn = db.connect(path)
    written = 0
    start = datetime.now(timezone.utc).replace(hour=8, minute=0, second=0, microsecond=0)

    drill_fixture = json.loads((FIXTURES / "sample_azure_response.json").read_text())
    paragraph_fixture = json.loads((FIXTURES / "sample_azure_continuous.json").read_text())

    for day in range(days):
        when = start - timedelta(days=days - 1 - day)
        skill = day / max(1, days - 1)
        created_at = when.strftime("%Y-%m-%dT%H:%M:%SZ")

        if day % BENCHMARK_EVERY_DAYS == 0:
            scores = _scores(rng, skill, spread=2.0)   # the fixed passage varies least
            db.record_attempt(
                conn, mode=Mode.PARAGRAPH, reference_text=progress_view.BENCHMARK_PASSAGE,
                recognised_text=progress_view.BENCHMARK_PASSAGE, audio_seconds=82.0,
                audio_sha256=f"benchmark-{day}", overall_scores=scores,
                azure_raw=benchmark_payload(rng, {**scores}, skill), created_at=created_at,
            )
            written += 1

        # Free practice, on most but not all days. Wider spread than the benchmark, because
        # a self-chosen text varies in difficulty — which is the entire reason the benchmark
        # exists and the reason these are drawn as an unconnected cloud.
        if rng.random() < 0.72:
            drill = rng.random() < 0.5
            db.record_attempt(
                conn, mode=Mode.DRILL if drill else Mode.PARAGRAPH,
                reference_text=FIXTURE_REFERENCE, recognised_text=FIXTURE_REFERENCE,
                audio_seconds=13.0 if drill else 34.0, audio_sha256=f"practice-{day}",
                overall_scores=_scores(rng, skill, spread=7.5),
                azure_raw=drill_fixture if drill else paragraph_fixture,
                created_at=when.replace(hour=19).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
            written += 1

    conn.close()
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", default=DEFAULT_PATH, help=f"database to write (default {DEFAULT_PATH})")
    parser.add_argument("--days", type=int, default=30, help="how many days of history")
    parser.add_argument("--seed", type=int, default=20260819,
                        help="random seed; the same seed always draws the same picture")
    args = parser.parse_args()

    written = seed(args.path, days=args.days, seed_value=args.seed)
    print(f"Wrote {written} attempts across {args.days} days to {args.path}.")
    print(f"Look at it with:  DB_PATH={args.path} make up")


if __name__ == "__main__":
    main()
