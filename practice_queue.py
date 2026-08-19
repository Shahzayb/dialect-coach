"""The practice queue: what to work on today, why, and what removes it from the list.

Why this exists. Today the app has no memory between sessions: every session opens on an
empty textarea and ends with a routine that is discarded. Thirty days of that is thirty
separate first sessions, which is the difference between a tool that diagnoses and a tool
that trains.

Four rules shape everything below, and each of them is a decision rather than a detail:

- **At most three active items.** A target set you cannot hold in your head while speaking is
  not a target set.
- **The queue orders and schedules what the analysis found. It never invents a target.**
  Every candidate here comes out of `progress_view`'s aggregates over the user's own stored
  attempts — the same numbers the Progress tab draws — so the queue and the chart cannot
  disagree about what recurs. With no history there are no candidates, and the honest answer
  is an empty list rather than a guess. No first language is consulted, per `projectbrief.md`.
- **Spaced review, not disappearance.** A graduated item comes back at widening intervals. A
  graduated contrast that is never re-tested is an unverified claim.
- **The rules are visible.** Every decision carries a `reason` string built from the real
  numbers, and `app.py` renders it verbatim. "Why is this here" and "what takes it off"
  must be answerable on screen, not only in this file.

Pure: no Streamlit, no database, no network. `now` is passed in rather than read, so a
schedule is asserted in a test instead of slept through. `db.py` holds the SQL; this holds
the policy.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

import perception_trainer
import utils

logger = logging.getLogger(__name__)

ACTIVE = "active"
GRADUATED = "graduated"

CONTRAST = perception_trainer.CONTRAST
VOWEL = perception_trainer.VOWEL
STRESS = perception_trainer.STRESS

# The order the queue fills its three slots in. One of each kind before a second of any, so
# three consonant contrasts cannot crowd out a vowel gap that the recordings flagged just as
# often. Sounds and rhythm are different problems and drilling only one leaves the other.
KIND_ORDER = (CONTRAST, VOWEL, STRESS)

KIND_LABELS = {
    CONTRAST: "Consonant contrast",
    VOWEL: "Vowel gap",
    STRESS: "Stress pattern",
}


def _parse(when: str | None) -> datetime | None:
    if not when:
        return None
    try:
        return datetime.strptime(str(when), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except ValueError:
        return None


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- Candidates ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    """One thing the recordings say is worth practising, with the numbers that say so."""

    item: str
    kind: str
    attempts: int
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def why(self) -> str:
        """The sentence the UI shows under "why is this here". Numbers, not a claim."""
        counts = (
            f"flagged in {self.attempts} separate attempt"
            f"{'' if self.attempts == 1 else 's'}"
        )
        tokens = self.evidence.get("tokens")
        if isinstance(tokens, int) and tokens > self.attempts:
            counts += f" ({tokens} times in total)"
        benchmark = self.evidence.get("benchmark_attempts")
        if isinstance(benchmark, int) and benchmark:
            counts += f", {benchmark} of them on the benchmark passage"
        if self.kind == STRESS:
            syllable = self.evidence.get("syllable")
            where = f", weakest on /{syllable}/" if syllable else ""
            return f"\"{self.item}\" was {counts}{where}."
        return f"{self.item} was {counts}."


def candidates(
    phonemes: Iterable[Mapping[str, Any]],
    syllables: Iterable[Mapping[str, Any]] = (),
    *,
    recur: int | None = None,
) -> list[Candidate]:
    """Rank what the stored attempts say is worth practising.

    Takes the row dicts from `progress_view.flagged_phonemes` and
    `progress_view.weak_syllables` (`frame.to_dict("records")`), so the aggregation itself has
    exactly one definition and lives next to the chart that draws it.

    Three filters, all of them refusals to offer something that cannot be trained:
    a substitution Azure could not name (`progress_view.UNCLEAR`) is dropped, because "your
    /θ/ was unclear" has no second word to put it against; a substitution with fewer than
    `utils.MIN_PAIRS_FOR_BLOCK` minimal pairs written up is dropped, because there is nothing
    to build a block from; and anything appearing in fewer than `RECUR_ATTEMPTS` attempts is
    dropped, because one bad reading is not a pattern.
    """
    threshold = utils.RECUR_ATTEMPTS if recur is None else recur
    found: list[Candidate] = []

    for row in phonemes:
        attempts = int(row.get("attempts") or 0)
        if attempts < threshold:
            continue
        expected = str(row.get("expected") or "")
        produced = str(row.get("produced") or "")
        if not expected or not produced:
            continue
        if not perception_trainer.trainable(expected, produced):
            # Covers the "(unclear)" marker too: it is not a phoneme, so it has no pairs.
            continue
        found.append(Candidate(
            item=str(row.get("label") or f"/{expected}/ → /{produced}/"),
            kind=perception_trainer.kind_for(expected),
            attempts=attempts,
            evidence={
                "source": "flagged_phonemes",
                "expected": expected,
                "produced": produced,
                "attempts": attempts,
                "benchmark_attempts": int(row.get("benchmark_attempts") or 0),
                "tokens": int(row.get("tokens") or 0),
            },
        ))

    for row in syllables:
        attempts = int(row.get("attempts") or 0)
        if attempts < threshold:
            continue
        word = str(row.get("word") or "")
        if not word:
            continue
        found.append(Candidate(
            item=word,
            kind=STRESS,
            attempts=attempts,
            evidence={
                "source": "weak_syllables",
                "word": word,
                "syllable": str(row.get("syllable") or ""),
                "attempts": attempts,
                "benchmark_attempts": int(row.get("benchmark_attempts") or 0),
                "tokens": int(row.get("tokens") or 0),
            },
        ))

    found.sort(key=lambda c: (-c.attempts, -int(c.evidence.get("tokens") or 0), c.item))
    return found


# --- Promotion -----------------------------------------------------------------------------------


def promote(
    existing: Sequence[Mapping[str, Any]],
    found: Sequence[Candidate],
    *,
    limit: int | None = None,
) -> list[Candidate]:
    """Which candidates should become targets, given what is already on the list.

    Fills up to `MAX_ACTIVE_TARGETS`, taking the best remaining candidate of a kind that is
    not represented yet before taking a second of any kind. A graduated item still occupies
    its `(item, kind)` identity and is not re-promoted — it is on the review schedule, which
    is a different thing from being dropped.
    """
    cap = utils.MAX_ACTIVE_TARGETS if limit is None else limit
    known = {(str(row["item"]), str(row["kind"])) for row in existing}
    active_kinds = [str(row["kind"]) for row in existing if str(row["state"]) == ACTIVE]
    slots = cap - len(active_kinds)
    if slots <= 0:
        return []

    available = [c for c in found if (c.item, c.kind) not in known]
    picked: list[Candidate] = []
    represented = set(active_kinds)

    for kind in KIND_ORDER:
        if len(picked) >= slots:
            break
        if kind in represented:
            continue
        for candidate in available:
            if candidate.kind == kind and candidate not in picked:
                picked.append(candidate)
                represented.add(kind)
                break

    # Only once every kind with a candidate has one slot does a second of any kind get in.
    for candidate in available:
        if len(picked) >= slots:
            break
        if candidate not in picked:
            picked.append(candidate)

    return picked[:slots]


# --- Grading ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Decision:
    """What to do with a target, and the sentence that explains it on screen."""

    state: str
    reason: str
    reviews_passed: int = 0
    regressed: bool = False


@dataclass(frozen=True)
class BlockSummary:
    """One completed block, as the grader sees it."""

    block_id: str
    created_at: str
    correct: int
    total: int
    planned: int
    novel: int
    alternatives: int
    review: bool

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    @property
    def complete(self) -> bool:
        return self.total >= self.planned and self.planned > 0

    @property
    def chance(self) -> float:
        return perception_trainer.chance_floor(self.alternatives)


def summarise_blocks(trials: Iterable[Mapping[str, Any]], *, planned: int | None = None
                     ) -> list[BlockSummary]:
    """Group stored trial rows into blocks, oldest first.

    `planned` is how many trials a full block holds. It is passed rather than stored per
    block because it is a convention (`utils.PERCEPTION_BLOCK_TRIALS`) that may be retuned,
    and a retune should not retroactively mark old blocks incomplete — so a block that
    already holds at least as many trials as the *smaller* of the two counts as complete.
    """
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    order: list[str] = []
    for row in trials:
        block_id = str(row["block_id"])
        if block_id not in grouped:
            grouped[block_id] = []
            order.append(block_id)
        grouped[block_id].append(row)

    summaries: list[BlockSummary] = []
    for block_id in order:
        rows = grouped[block_id]
        review = bool(rows[0].get("review"))
        target = planned if planned is not None else (
            utils.PERCEPTION_REVIEW_TRIALS if review else utils.PERCEPTION_BLOCK_TRIALS
        )
        summaries.append(BlockSummary(
            block_id=block_id,
            created_at=str(rows[0].get("created_at") or ""),
            correct=sum(1 for r in rows if r.get("correct")),
            total=len(rows),
            planned=target,
            novel=sum(1 for r in rows if r.get("novel")),
            alternatives=int(rows[0].get("alternatives") or 2),
            review=review,
        ))
    summaries.sort(key=lambda s: s.created_at)
    return summaries


def graduation_rule(kind: str) -> str:
    """What takes this item off the list, in words, with the real numbers in it.

    Rendered verbatim next to every active target. The brief asks for promotion and
    graduation to be visible rather than implicit, and a rule the user cannot read is
    implicit however carefully it is implemented.
    """
    if kind == STRESS:
        return (
            f"Comes off the list when the word stops being flagged — it has to be absent "
            f"from your last {utils.RECUR_ATTEMPTS} assessed attempts. There is no scored "
            f"check for stress: Azure returns no stress marks, so this graduates on the "
            f"evidence drying up rather than on a quiz you mark yourself."
        )
    floor = perception_trainer.chance_floor(2) * 100.0
    return (
        f"Comes off the list at {utils.PERCEPTION_GRADUATE_ACCURACY:.0%} or better across "
        f"{utils.PERCEPTION_GRADUATE_BLOCKS} completed blocks, counting only blocks made "
        f"mostly of stimuli you had not heard before. Read that against {floor:.0f}%, which "
        f"is what guessing scores. It returns to the list if a spaced review drops below "
        f"{utils.PERCEPTION_REGRESS_ACCURACY:.0%}."
    )


def grade(
    target: Mapping[str, Any],
    blocks: Sequence[BlockSummary] = (),
    *,
    still_flagged: bool | None = None,
) -> Decision:
    """Decide whether a target stays, graduates, or comes back — and say why.

    Two different rules for two genuinely different kinds of item, which is the honest
    outcome rather than a compromise:

    - A **contrast or vowel gap** is checked by the block itself, so it graduates on
      sustained accuracy across two completed blocks and regresses when a spaced review
      falls below the floor.
    - A **stress pattern** has no scored check that costs no speech recognition — Azure
      returns per-syllable accuracy but no stress marks, verified against the committed
      fixtures — so it graduates when the evidence dries up: the word stops appearing in the
      flagged aggregate. `still_flagged` carries that, freshly computed, from the caller.
    """
    kind = str(target.get("kind") or CONTRAST)
    state = str(target.get("state") or ACTIVE)
    passed = int(target.get("reviews_passed") or 0)

    if kind == STRESS:
        if still_flagged is None:
            return Decision(state, "Not re-checked yet — no new attempt since it was added.")
        if still_flagged:
            return Decision(
                ACTIVE,
                f"Still on the list: the word is still being flagged in your recent "
                f"attempts. {graduation_rule(STRESS)}",
            )
        return Decision(
            GRADUATED,
            f"Graduated: the word has stopped being flagged in your recent attempts. It "
            f"comes back for a check if it reappears.",
            reviews_passed=passed,
        )

    completed = [b for b in blocks if b.complete]
    if not completed:
        return Decision(
            state,
            f"No completed block yet. {graduation_rule(kind)}",
            reviews_passed=passed,
        )

    latest = completed[-1]
    if state == GRADUATED or latest.review:
        if latest.accuracy < utils.PERCEPTION_REGRESS_ACCURACY:
            return Decision(
                ACTIVE,
                f"Back on the list: the review block scored {latest.accuracy:.0%} "
                f"({latest.correct}/{latest.total}), below the "
                f"{utils.PERCEPTION_REGRESS_ACCURACY:.0%} the schedule holds it to, against "
                f"a {latest.chance:.0%} chance floor.",
                reviews_passed=0,
                regressed=True,
            )
        return Decision(
            GRADUATED,
            f"Review passed at {latest.accuracy:.0%} ({latest.correct}/{latest.total}) "
            f"against a {latest.chance:.0%} chance floor.",
            reviews_passed=passed + 1,
        )

    recent = completed[-utils.PERCEPTION_GRADUATE_BLOCKS:]
    if (len(recent) >= utils.PERCEPTION_GRADUATE_BLOCKS
            and all(b.accuracy >= utils.PERCEPTION_GRADUATE_ACCURACY for b in recent)):
        scores = ", ".join(f"{b.accuracy:.0%}" for b in recent)
        return Decision(
            GRADUATED,
            f"Graduated: {scores} across the last {len(recent)} completed blocks, at or "
            f"above {utils.PERCEPTION_GRADUATE_ACCURACY:.0%}, against a "
            f"{recent[-1].chance:.0%} chance floor. It comes back on the review schedule "
            f"rather than disappearing.",
            reviews_passed=0,
        )

    shortfall = utils.PERCEPTION_GRADUATE_BLOCKS - len(
        [b for b in recent if b.accuracy >= utils.PERCEPTION_GRADUATE_ACCURACY]
    )
    return Decision(
        ACTIVE,
        f"Last block {latest.accuracy:.0%} ({latest.correct}/{latest.total}) against a "
        f"{latest.chance:.0%} chance floor. {shortfall} more block"
        f"{'' if shortfall == 1 else 's'} at "
        f"{utils.PERCEPTION_GRADUATE_ACCURACY:.0%} or better to graduate.",
        reviews_passed=passed,
    )


# --- Scheduling ----------------------------------------------------------------------------------


def next_due(decision: Decision, *, now: datetime, kind: str = CONTRAST) -> str:
    """When this item should be looked at next.

    An active item is due immediately — the whole point of the queue is that opening the app
    answers "what am I doing today?", and an active target that is not due today is a target
    nobody is working on. A graduated one goes out to the next interval in
    `utils.REVIEW_INTERVAL_DAYS`; past the last one it stops being re-checked, which is
    recorded honestly rather than hidden as an infinite schedule.
    """
    if decision.state == ACTIVE:
        return _iso(now)
    intervals = utils.REVIEW_INTERVAL_DAYS
    index = min(decision.reviews_passed, len(intervals) - 1)
    return _iso(now + timedelta(days=intervals[index]))


def review_horizon(reviews_passed: int) -> str:
    """The sentence describing where an item sits on the review schedule."""
    intervals = utils.REVIEW_INTERVAL_DAYS
    if reviews_passed >= len(intervals):
        return (
            f"Past the last scheduled review ({intervals[-1]} days). It stays on the list as "
            f"passed and is not re-checked again unless it starts being flagged again."
        )
    return (
        f"Review {reviews_passed + 1} of {len(intervals)} — the gaps widen "
        f"{', '.join(str(d) for d in intervals)} days, so a pass is re-tested rather than "
        f"taken on trust."
    )


def due(targets: Iterable[Mapping[str, Any]], *, now: datetime) -> list[Mapping[str, Any]]:
    """What is due, soonest first. Active items sort ahead of graduated reviews."""
    ready = []
    for row in targets:
        moment = _parse(row.get("next_due"))
        if moment is None or moment <= now:
            ready.append(row)
    ready.sort(key=lambda row: (
        0 if str(row.get("state")) == ACTIVE else 1,
        str(row.get("next_due") or ""),
    ))
    return ready


def evidence_of(target: Mapping[str, Any]) -> dict[str, Any]:
    """The stored evidence for a target, or an empty dict if it cannot be read.

    A row whose evidence will not parse is a row that can still be practised; the UI just has
    less to say about why it is there. It must never blank the page.
    """
    try:
        loaded = json.loads(str(target.get("evidence") or "{}"))
    except (TypeError, ValueError):
        logger.warning("Target %s has unreadable evidence", target.get("item"))
        return {}
    return loaded if isinstance(loaded, dict) else {}
