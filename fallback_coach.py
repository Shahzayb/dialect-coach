"""The offline coach, and the report shape both coaches emit.

**This is the primary path, not a degraded one.** The free Gemini tier runs out, and when
it does the app still has to be worth opening. Everything here is derived from the Azure
response and the static reference table: no key, no network, no clock, no model. Same
attempt in, same bytes out.

The pydantic models live here rather than in `ai_coach` for the same reason. `ai_coach`
imports this module anyway — it falls through to it on any failure — so keeping the schema
on this side means the free path never imports the Google SDK, and the UI has exactly one
report shape to render regardless of which coach produced it.

The models are deliberately plain: no `Optional`, no defaults, no unions. That is what the
GenAI SDK converts cleanly into a Gemini response schema, and a schema that fails to
convert would only show up as a badly-shaped answer at run time.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

import phoneme_reference as pr
import speech_analyzer
import utils
from utils import Mode

logger = logging.getLogger(__name__)

# Three, because a practice session that tries to fix everything fixes nothing. The cap is
# also stated to the model, and enforced on its answer rather than trusted.
MAX_PRIORITY_FIXES = 3

# What `db.attach_coaching` stores in `coach_source`, and what tells a later re-parse which
# shape `gemini_raw_json` holds.
SOURCE_FALLBACK = "fallback"
SOURCE_GEMINI = "gemini"

# The coach's own phrasing for Azure's prosody feedback. Full sentences naming the words,
# where `app.DELIVERY_LABELS` is a noun phrase for a heading — the same three faults, said
# for a different purpose.
_DELIVERY_SENTENCES: dict[str, str] = {
    "UnexpectedBreak": "You paused in the middle of a phrase",
    "MissingBreak": "You ran two phrases together with no pause",
    "Monotone": "Your pitch stayed flat",
}

# One drill per fault, written once, formatted with the words it actually happened on.
# This is the half of the coaching layer that has to work with no key and no network, so
# the delivery advice cannot live in the prompt: a learner without a Gemini key would get
# a score for their prosody and nothing to do about it, which is the complaint that
# started this. Each one is something to *perform* — the sentence in `_DELIVERY_SENTENCES`
# above is what already happened, and describing a problem back at someone is not coaching.
_DELIVERY_DRILLS: dict[str, str] = {
    "UnexpectedBreak": (
        "Read the phrase containing {words} straight through once, without stopping "
        "anywhere inside it. Then read it again and put the only pause at the punctuation. "
        "Record both and listen for where the break actually landed."
    ),
    "MissingBreak": (
        "Mark the boundary at {words} with a pencil stroke. Read the sentence at half "
        "speed putting one clear beat there, then at normal speed keeping the same beat."
    ),
    "Monotone": (
        "Say {words} three times: once with the pitch rising on the last stressed "
        "syllable, once falling, once the way you would say it to someone in the room. "
        "Record it and listen for whether the shape changed at all between the three."
    ),
}

# For a fault Azure starts reporting that has no template yet. It still gets an entry —
# a fault named on the page with no drill under it is the exact gap this chunk closes.
_GENERIC_DELIVERY_DRILL = (
    "Read the text containing {words} at half speed and then at normal speed, recording "
    "both, and listen to the two back to back for what changes between them."
)

# A syllable below this is worth naming as a stress problem. Same cut as a phoneme: below
# it, Azure is reporting something the listener can hear.
SYLLABLE_RED = utils.PHONEME_RED


# --- The report shape -------------------------------------------------------------------


class MinimalPair(BaseModel):
    """Two real words differing only in the sound being drilled."""

    a: str = Field(description="The word containing the target sound.")
    b: str = Field(description="The word containing the sound that was produced instead.")


class PriorityFix(BaseModel):
    """One substitution worth practising, with everything needed to practise it."""

    expected_phoneme: str = Field(description="The target sound, IPA, without slashes.")
    produced_phoneme: str = Field(
        description="The sound actually produced, IPA, taken from the Azure data only."
    )
    affected_words: list[str] = Field(description="Words from this attempt where it happened.")
    why_it_matters: str = Field(description="What a listener hears instead. One sentence.")
    articulation: str = Field(
        description="Concrete tongue, lip and airflow instruction for the target sound."
    )
    minimal_pairs: list[MinimalPair] = Field(description="Real word pairs to drill the contrast.")


class DeliveryDrill(BaseModel):
    """One delivery fault, and something to perform about it."""

    fault: str = Field(description="UnexpectedBreak, MissingBreak or Monotone.")
    span: list[str] = Field(description="The words from this attempt that carry it.")
    what_happened: str = Field(
        description="One sentence naming the span. What happened, not what to do."
    )
    drill: str = Field(
        description=(
            "An exercise the learner performs, naming those words. Never a restatement "
            "of the problem."
        )
    )


class StressAndRhythm(BaseModel):
    """Delivery rather than sounds: pausing, stress placement, intonation."""

    issues: list[str] = Field(description="Specific problems observed, each one sentence.")
    drill: str = Field(description="One concrete rhythm or stress exercise.")


class CoachingReport(BaseModel):
    """What the UI renders, whichever coach wrote it."""

    overall_comment: str = Field(
        description="Two or three sentences on this attempt. No praise padding."
    )
    priority_fixes: list[PriorityFix] = Field(
        description=(
            f"At most {MAX_PRIORITY_FIXES}, ranked by how much they cost intelligibility."
        )
    )
    delivery_drills: list[DeliveryDrill] = Field(
        description=(
            "One per delivery fault reported in `delivery_faults`, and none for a fault "
            "that is not there."
        )
    )
    stress_and_rhythm: StressAndRhythm
    practice_plan: str = Field(
        description="One five-minute routine naming specific words from this attempt."
    )


# --- Compaction ---------------------------------------------------------------------------
# Shared with `ai_coach`: one grouping, so the model and the offline coach are looking at
# exactly the same facts. Raw Azure JSON for a paragraph is ~100 kB, almost all of it
# offsets and durations and scores for words that were fine — sending it costs tokens and
# measurably degrades the answer.


def _substitutions(word: dict[str, Any]) -> list[dict[str, Any]]:
    """The mispronounced phonemes of one word, as expected → produced.

    A phoneme only appears here when Azure scored it below the red cut *and* offered a
    different top alternate. Without the alternate there is no produced sound to name, and
    naming one anyway is the invention this whole layer has to avoid.

    Adjacent targets claiming the *same* produced sound are collapsed to the worst of the
    run. In the captured fixture the /z/ and the /d/ of "thursday" both come back as /tʃ/
    at 100: the aligner smeared one produced sound across two targets, and reporting it as
    two separate substitutions would spend two of the three fix slots on one event.
    """
    found = []
    pairs = speech_analyzer.phoneme_pairs(word)
    for index, (expected, produced, score) in enumerate(pairs):
        if not expected or not produced or score is None:
            continue
        if score >= utils.PHONEME_RED:
            continue
        previous = pairs[index - 1][0] if index else None
        entry = {
            "expected": expected,
            "produced": produced,
            "score": round(score, 1),
            # Cluster simplification, not substitution: the last consonant of a word that
            # ends in two of them is the one that gets swallowed.
            "final_cluster": (
                index == len(pairs) - 1
                and _is_consonant(expected)
                and _is_consonant(previous)
            ),
            "_index": index,
        }
        smeared = (
            found
            and found[-1]["produced"] == produced
            and found[-1]["_index"] == index - 1
        )
        if smeared:
            if entry["score"] < found[-1]["score"]:
                found[-1] = entry
            else:
                found[-1]["_index"] = index
                # The merged entry now stands at `index`, so it inherits that position's
                # final-cluster status. Without this the kept entry keeps the flag it had
                # one phoneme earlier, where it could not have been word-final, and the
                # swallowed-cluster note never fires.
                found[-1]["final_cluster"] = entry["final_cluster"]
            continue
        found.append(entry)

    for entry in found:
        entry.pop("_index", None)
    return found


def _is_consonant(symbol: str | None) -> bool:
    entry = pr.lookup(symbol)
    return entry is not None and entry.kind == "consonant"


def _weak_syllables(word: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"syllable": syllable.get("syllable"), "score": round(float(syllable["score"]), 1)}
        for syllable in word.get("syllables") or []
        if syllable.get("syllable") and isinstance(syllable.get("score"), (int, float))
    ]


def compact(assessment: Any, mode: Mode) -> dict[str, Any]:
    """The evidence, and only the evidence: flagged words with what went wrong in them.

    Everything scored above the flag line is dropped — it is not what the report is about,
    and it is the bulk of the payload.
    """
    words: list[dict[str, Any]] = []
    observed: list[list[str]] = []

    for word in assessment.words:
        if not speech_analyzer.is_flagged(word):
            continue
        substitutions = _substitutions(word)
        for substitution in substitutions:
            pair = [substitution["expected"], substitution["produced"]]
            if pair not in observed:
                observed.append(pair)
        accuracy = word.get("accuracy")
        words.append(
            {
                "word": str(word.get("word") or ""),
                "accuracy": round(float(accuracy), 1) if isinstance(accuracy, (int, float)) else None,
                "error_type": word.get("error_type") or "None",
                "error_source": word.get("error_source") or "azure",
                "substitutions": substitutions,
                "syllables": _weak_syllables(word),
                "delivery": list(word.get("delivery_error_types") or []),
            }
        )

    scores = {
        key: (round(float(value), 1) if isinstance(value, (int, float)) else None)
        for key, value in (assessment.overall_scores or {}).items()
    }

    return {
        "mode": mode.value,
        "overall_scores": scores,
        "flagged_words": words,
        "omitted_words": [w["word"] for w in words if w["error_type"] == "Omission"],
        "delivery": speech_analyzer.delivery_summary(assessment.words),
        # Its own section, and the reason it is separate from "delivery" above: this one
        # carries what Azure *measured* where each fault happened, which is what turns a
        # prosody score into something with a span attached. Three entries at most, so the
        # payload stays the fraction of the raw response that `compact` exists to be.
        "delivery_faults": speech_analyzer.delivery_faults(assessment.words),
        # The only pairs a report is allowed to discuss. `ai_coach` validates against this.
        "observed_pairs": observed,
    }


def reference_notes(observed_pairs: list[list[str]]) -> dict[str, dict[str, Any]]:
    """What the static table knows about each observed substitution.

    Sent to the model as grounding so it works from this project's articulation notes
    rather than from whatever it remembers about phonetics. Pairs with no entry are simply
    absent — an empty note is better than a confident wrong one.
    """
    notes: dict[str, dict[str, Any]] = {}
    for expected, produced in observed_pairs:
        entry = pr.contrast(expected, produced)
        if entry is None:
            continue
        notes[f"{expected}->{produced}"] = {
            "articulation": pr.articulation_for(expected),
            "why_it_matters": entry.why_it_matters,
            "minimal_pairs": [list(pair) for pair in entry.minimal_pairs],
        }
    return notes


# --- The deterministic report ---------------------------------------------------------------


def _groups(compacted: dict[str, Any]) -> list[dict[str, Any]]:
    """Substitutions grouped by (expected, produced), worst first.

    Ranked by how much each one costs intelligibility:

    1. the number of distinct words it damaged — the same swap in five words is a habit,
       in one word it is a slip;
    2. whether the reference table has the pair written up. A written-up contrast comes
       with articulation and minimal pairs, so it can actually be *practised*; an unwritten
       one can only be named. Azure's alternates for a badly mangled word include some that
       are alignment noise rather than a real substitution, and those are exactly the ones
       with no entry, so this term demotes both at once;
    3. how badly Azure scored it;
    4. the symbols themselves. Not decoration — without a total order the report would
       reshuffle between two runs on identical input, and nothing about this coach is
       allowed to be non-deterministic.
    """
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for word in compacted["flagged_words"]:
        for substitution in word["substitutions"]:
            key = (substitution["expected"], substitution["produced"])
            group = groups.setdefault(
                key,
                {"expected": key[0], "produced": key[1], "words": [], "scores": [],
                 "final_cluster": True},
            )
            if word["word"] and word["word"] not in group["words"]:
                group["words"].append(word["word"])
            group["scores"].append(substitution["score"])
            group["final_cluster"] &= bool(substitution.get("final_cluster"))

    return sorted(
        groups.values(),
        key=lambda group: (
            -len(group["words"]),
            0 if pr.contrast(group["expected"], group["produced"]) else 1,
            sum(group["scores"]) / len(group["scores"]),
            group["expected"],
            group["produced"],
        ),
    )


def _fix(group: dict[str, Any]) -> PriorityFix:
    expected, produced = group["expected"], group["produced"]
    articulation = pr.articulation_for(expected, produced)
    if group["final_cluster"]:
        articulation = f"{articulation} {pr.FINAL_CLUSTER_NOTE}"
    return PriorityFix(
        expected_phoneme=expected,
        produced_phoneme=produced,
        affected_words=group["words"][:8],
        why_it_matters=pr.why_it_matters(expected, produced),
        articulation=articulation,
        minimal_pairs=[MinimalPair(a=a, b=b) for a, b in pr.minimal_pairs(expected, produced)[:5]],
    )


_SCORE_LABELS = (
    ("pronunciation", "pron_score"), ("accuracy", "accuracy"), ("fluency", "fluency"),
    ("completeness", "completeness"), ("prosody", "prosody"),
)


def _overall_comment(compacted: dict[str, Any], groups: list[dict[str, Any]]) -> str:
    """Two or three sentences of fact. No praise padding, and nothing not in the data."""
    scores = compacted["overall_scores"] or {}
    stated = [
        f"{label} {scores[key]:.0f}" for label, key in _SCORE_LABELS
        if isinstance(scores.get(key), (int, float))
    ]
    sentences = []
    if stated:
        sentences.append(f"This attempt scored {', '.join(stated)}.")

    flagged = len(compacted["flagged_words"])
    if groups:
        named = ", ".join(
            f"/{group['expected']}/ heard as /{group['produced']}/"
            for group in groups[:MAX_PRIORITY_FIXES]
        )
        sentences.append(
            f"{flagged} {'word' if flagged == 1 else 'words'} were flagged, and the "
            f"substitutions behind them are {named}."
        )
    elif flagged:
        sentences.append(
            f"{flagged} {'word' if flagged == 1 else 'words'} were flagged, but no single "
            f"sound substitution sits behind them — Azure scored the phonemes themselves "
            f"above the line, so this is a word- and delivery-level problem rather than an "
            f"articulation one."
        )
    else:
        sentences.append("Nothing fell below the flag line in that attempt.")

    omitted = compacted["omitted_words"]
    if omitted:
        sentences.append(
            f"{len(omitted)} {'word' if len(omitted) == 1 else 'words'} from the script "
            f"were never spoken at all: {', '.join(omitted[:6])}."
        )
    return " ".join(sentences)


def _measurement(fault: dict[str, Any]) -> str:
    """What Azure measured on this span, in Azure's own vocabulary.

    Deliberately unglossed. `BreakLength` has no unit anywhere in SDK 1.51.1 and every
    value in the committed capture is 0, so "you paused for 480 ms" would be a fabricated
    fact of exactly the kind `ai_coach.validated` exists to stop — and one written by us,
    where there is no model to blame for it. A number under the name Azure gave it is
    less satisfying and true.
    """
    longest = fault.get("break_length_max")
    if isinstance(longest, (int, float)) and longest:
        return f" Azure measured BreakLength {longest:g} at the longest of them."
    pitch = fault.get("monotone_confidence_mean")
    if isinstance(pitch, (int, float)):
        return f" Azure's SyllablePitchDeltaConfidence across that span was {pitch:.2f}."
    return ""


def _delivery_drills(compacted: dict[str, Any]) -> list[DeliveryDrill]:
    """A drill for every delivery fault in the payload. Never fewer, never invented ones.

    This is the offline half of what makes the prosody score actionable: a fault in the
    data always produces something to perform, with no key, no network and no model. The
    ordering is `speech_analyzer.delivery_faults`' own, which is deterministic.
    """
    drills: list[DeliveryDrill] = []
    for fault in compacted.get("delivery_faults") or []:
        span = [w for w in fault["words"] if w]
        named = ", ".join(span[:6]) or "the flagged span"
        sentence = _DELIVERY_SENTENCES.get(fault["fault"], f"Azure flagged {fault['fault']}")
        template = _DELIVERY_DRILLS.get(fault["fault"], _GENERIC_DELIVERY_DRILL)
        drills.append(
            DeliveryDrill(
                fault=fault["fault"],
                span=span,
                what_happened=f"{sentence}, on: {named}.{_measurement(fault)}",
                drill=template.format(words=named),
            )
        )
    return drills


def _stress_and_rhythm(compacted: dict[str, Any]) -> StressAndRhythm:
    """Stress and rhythm *other than* the delivery faults, which have their own section.

    The delivery sentences used to be the first thing in `issues` and the break-derived
    drill was the first branch below. Both moved into `delivery_drills`, where each fault
    gets its own exercise instead of sharing one line — leaving this for what is genuinely
    its own: syllables carrying the stress in the wrong place, and the overall score. The
    two sections sit inches apart on the page, so saying it in both would read as padding.
    """
    issues: list[str] = []

    for word in compacted["flagged_words"]:
        syllables = word["syllables"]
        if len(syllables) < 2:
            continue
        weakest = min(syllables, key=lambda syllable: syllable["score"])
        if weakest["score"] < SYLLABLE_RED:
            issues.append(
                f"In \"{word['word']}\" the syllable /{weakest['syllable']}/ scored "
                f"{weakest['score']:.0f}, well below the rest of the word — the stress is "
                f"landing somewhere else."
            )
        if len(issues) >= 5:  # a wall of them is as unusable as none
            break

    prosody = (compacted["overall_scores"] or {}).get("prosody")
    if isinstance(prosody, (int, float)) and prosody < utils.WORD_RED:
        issues.append(
            f"Prosody scored {prosody:.0f} overall: the delivery is flatter or more broken "
            f"up than a native reading of the same text."
        )

    # No break branch here any more: a break fault now gets its own drill in
    # `delivery_drills`, and a second one phrased differently three inches away helped
    # nobody.
    if any(len(word["syllables"]) > 1 for word in compacted["flagged_words"]):
        multi = [word["word"] for word in compacted["flagged_words"] if len(word["syllables"]) > 1]
        drill = (
            f"Say {', '.join(multi[:3])} three times each, clapping once on the stressed "
            f"syllable. Then say each one inside its sentence, keeping the same beat."
        )
    else:
        drill = (
            "Read the text once at half speed and once at normal speed, keeping the pauses "
            "in the same places both times."
        )
    return StressAndRhythm(issues=issues, drill=drill)


def _practice_plan(compacted: dict[str, Any], fixes: list[PriorityFix]) -> str:
    if not fixes:
        words = [word["word"] for word in compacted["flagged_words"]][:5]
        target = ", ".join(words) if words else "the whole text"
        return (
            "Five minutes: two minutes reading the text at half speed, one minute on "
            f"{target} said in isolation, then two minutes reading it at normal speed "
            "while recording yourself. Compare the two recordings rather than the scores."
        )

    # Four minutes split over the fixes, biggest share first, leaving the fifth minute for
    # the read-through below. Computed rather than tabulated: a lookup keyed on the number
    # of fixes is a KeyError waiting for the day MAX_PRIORITY_FIXES changes.
    # Floored at one minute so a large MAX_PRIORITY_FIXES cannot produce a "0 minutes" step.
    base, extra = divmod(4, len(fixes))
    minutes = tuple(max(1, base + (1 if i < extra else 0)) for i in range(len(fixes)))
    steps = []
    for index, (fix, allotted) in enumerate(zip(fixes, minutes), start=1):
        pairs = ", ".join(f"{pair.a}/{pair.b}" for pair in fix.minimal_pairs[:3])
        words = ", ".join(fix.affected_words[:3])
        drill = (
            f"say the pairs {pairs} slowly, then {words} from this attempt"
            if pairs
            else f"say {words} slowly, holding the /{fix.expected_phoneme}/ each time"
        )
        steps.append(
            f"{index}. {allotted} minute{'s' if allotted > 1 else ''} on "
            f"/{fix.expected_phoneme}/ → /{fix.produced_phoneme}/: {drill}."
        )
    steps.append(
        f"{len(steps) + 1}. 1 minute reading the whole text straight through at normal "
        f"speed, recording it, and listening back for the same sounds."
    )
    return "Five minutes, in this order:\n" + "\n".join(steps)


def emergency_report(reason: str) -> CoachingReport:
    """A valid report for when building the real one raised.

    Constructed from literals only — no Azure data, no reference table, no branching — so
    this cannot itself be the thing that fails. It exists because "the coach always returns
    a report" has to hold even when the coach has a bug in it: the scores, the diff and the
    word cards below are all still correct and worth reading, and losing them to a
    traceback because one paragraph of advice could not be assembled is the worse outcome.
    """
    logger.error("Falling back to the emergency report: %s", reason)
    return CoachingReport(
        overall_comment=(
            "The coaching report could not be built for this attempt. Your scores, the "
            "script-versus-heard diff and the word-by-word breakdown below are unaffected "
            "— read those instead."
        ),
        priority_fixes=[],
        delivery_drills=[],
        stress_and_rhythm=StressAndRhythm(
            issues=[],
            drill=(
                "Read the text once at half speed and once at normal speed, keeping the "
                "pauses in the same places both times."
            ),
        ),
        practice_plan=(
            "Five minutes: two minutes reading the text at half speed, one minute on the "
            "words marked red below, then two minutes reading it at normal speed while "
            "recording yourself. Compare the two recordings rather than the scores."
        ),
    )


def build(assessment: Any, mode: Mode) -> CoachingReport:
    """The whole report, from the Azure data alone."""
    compacted = compact(assessment, mode)
    return build_from_compacted(compacted)


def build_from_compacted(compacted: dict[str, Any]) -> CoachingReport:
    """The report from an already-compacted payload — the form `ai_coach` also sends."""
    groups = _groups(compacted)
    fixes = [_fix(group) for group in groups[:MAX_PRIORITY_FIXES]]
    logger.info(
        "Offline coach: %d flagged words, %d distinct substitutions, %d fixes reported",
        len(compacted["flagged_words"]), len(groups), len(fixes),
    )
    return CoachingReport(
        overall_comment=_overall_comment(compacted, groups),
        priority_fixes=fixes,
        delivery_drills=_delivery_drills(compacted),
        stress_and_rhythm=_stress_and_rhythm(compacted),
        practice_plan=_practice_plan(compacted, fixes),
    )
