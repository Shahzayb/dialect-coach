"""The vowel geometry reaching the coach, and the bridging phrases coming back.

The rule that matters here is the same one `_checked_drills` already enforces for delivery:
a report may only discuss what was measured. A bridging phrase drilling a vowel the geometry
never flagged is a fabrication the learner cannot check — it looks exactly like a real finding
and costs them practice time.
"""

from __future__ import annotations

import ai_coach
import fallback_coach as fc
import vowel_measure
import vowel_reference


def gap(vowel: str, metric: str = vowel_measure.POSITION, magnitude: float = 0.8):
    return vowel_measure.Gap(
        vowel=vowel,
        metric=metric,
        magnitude=magnitude,
        unit="z",
        detail=f"sits {magnitude:.2f} z from the General American target",
        n=7,
        evidence={"arrow_z": magnitude, "tokens": 7},
    )


BASE = {
    "mode": "paragraph",
    "overall_scores": {},
    "flagged_words": [],
    "omitted_words": [],
    "delivery": {},
    "delivery_faults": [],
    "observed_pairs": [],
    "vowel_geometry": [],
}


# --- The payload -----------------------------------------------------------------------------


def test_the_geometry_is_a_new_section_and_never_replaces_the_phoneme_evidence() -> None:
    """ "Alongside the existing phoneme payload, not instead of it" — a section, not a prompt."""
    compacted = dict(BASE, observed_pairs=[["θ", "s"]])
    with_gaps = fc.with_geometry(compacted, [gap("u")])

    assert with_gaps["observed_pairs"] == [["θ", "s"]], "the phoneme evidence was disturbed"
    assert set(compacted) <= set(with_gaps)
    assert with_gaps["vowel_geometry"][0]["vowel"] == "u"
    # And the original is untouched, so a caller cannot accidentally mutate a shared payload.
    assert compacted["vowel_geometry"] == []


def test_a_gap_carries_the_numbers_it_was_ranked_on() -> None:
    """ "Why is this here" is answered with numbers or it is not answered."""
    entry = fc.with_geometry(dict(BASE), [gap("ɝ", magnitude=1.4)])["vowel_geometry"][0]
    assert entry["keyword"] == "NURSE"
    assert entry["magnitude"] == 1.4
    assert entry["unit"] == "z"
    assert entry["tokens"] == 7
    assert "General American" in entry["detail"]


def test_an_attempt_with_no_baseline_carries_an_empty_section() -> None:
    """Most attempts have no stored baseline, and the report must be the same shape anyway."""
    assert fc.compact.__doc__  # the payload builder still exists
    assert fc.with_geometry(dict(BASE), [])["vowel_geometry"] == []
    assert fc.bridging_phrases(dict(BASE)) == []


# --- The offline coach -------------------------------------------------------------------------


def test_the_offline_coach_answers_every_flagged_vowel_with_a_sentence() -> None:
    """Free, offline and permanent — no API key, no network, and it still answers."""
    compacted = fc.with_geometry(dict(BASE), [gap("i"), gap("ɝ"), gap("u")])
    phrases = fc.bridging_phrases(compacted)
    assert [p.vowel for p in phrases] == ["i", "ɝ", "u"]
    for phrase in phrases:
        assert phrase.phrase in vowel_reference.bridging_phrases(phrase.vowel)
        assert len(phrase.phrase.split()) >= 6, "a word list is not a bridging phrase"
        assert phrase.keyword
        assert phrase.why


def test_the_offline_coach_caps_what_it_offers() -> None:
    compacted = fc.with_geometry(dict(BASE), [gap(v) for v in ("i", "ɪ", "ɛ", "æ", "u")])
    assert len(fc.bridging_phrases(compacted)) == 3


def test_the_same_vowel_twice_does_not_present_the_same_sentence_twice() -> None:
    compacted = fc.with_geometry(dict(BASE), [gap("i"), gap("i")])
    phrases = fc.bridging_phrases(compacted)
    assert len({p.phrase for p in phrases}) == len(phrases)


def test_a_full_offline_report_carries_the_section() -> None:
    compacted = fc.with_geometry(dict(BASE), [gap("ɝ")])
    report = fc.build_from_compacted(compacted)
    assert [p.vowel for p in report.bridging_phrases] == ["ɝ"]


def test_a_report_without_geometry_is_still_valid() -> None:
    """The schema defaults the field, so every stored report from before v0.11.0 still loads."""
    report = fc.build_from_compacted(dict(BASE))
    assert report.bridging_phrases == []


# --- The model's phrases, checked ----------------------------------------------------------------


def report_with(phrases: list[fc.BridgingPhrase]) -> fc.CoachingReport:
    return fc.CoachingReport(
        overall_comment="A comment about the attempt.",
        priority_fixes=[],
        delivery_drills=[],
        stress_and_rhythm=fc.StressAndRhythm(issues=[], drill="Read it again slowly."),
        practice_plan="Five minutes.",
        bridging_phrases=phrases,
    )


def phrase(vowel: str, text: str = "Sue moved two blue spoons through the soup room."):
    return fc.BridgingPhrase(vowel=vowel, keyword="GOOSE", why="measured", phrase=text)


def test_a_phrase_for_a_vowel_that_was_never_measured_is_dropped() -> None:
    """The fabrication this check exists for: it looks exactly like a real finding."""
    compacted = fc.with_geometry(dict(BASE), [gap("i")])
    checked = ai_coach.validated(report_with([phrase("u")]), compacted)
    assert checked is not None
    assert "u" not in [p.vowel for p in checked.bridging_phrases]


def test_a_flagged_vowel_the_model_ignored_is_backfilled_from_the_written_phrases() -> None:
    """The section is complete whichever coach wrote it."""
    compacted = fc.with_geometry(dict(BASE), [gap("i"), gap("u")])
    checked = ai_coach.validated(report_with([phrase("u")]), compacted)
    assert checked is not None
    assert sorted(p.vowel for p in checked.bridging_phrases) == ["i", "u"]


def test_no_geometry_means_no_phrases_however_many_the_model_wrote() -> None:
    checked = ai_coach.validated(report_with([phrase("u"), phrase("i")]), dict(BASE))
    assert checked is not None
    assert checked.bridging_phrases == []


def test_the_prompt_asks_for_a_sentence_and_forbids_a_word_list() -> None:
    """The instruction is worth asserting: 'the co-articulation is the thing being practised'."""
    instruction = ai_coach.SYSTEM_INSTRUCTION
    assert "vowel_geometry" in instruction
    assert "bridging_phrases" in instruction
    assert "never a word list" in instruction.lower()
    assert "varied consonant contexts" in instruction.lower()


# --- The wiring, which is the half that was missing --------------------------------------------
# Every rule above was covered by tests and none of it ran: nothing in `src/` passed the gaps
# to either coach, so `compact()`'s empty `vowel_geometry` was what both of them actually saw
# and the bridging phrases, the one-click drill and the queue promotion never rendered. These
# assert the seam rather than the rule.


def assessment():
    """The smallest real `Assessment` `compact` will walk. The geometry is what is under test."""
    import speech_analyzer as sa

    return sa.Assessment(
        raw=[],
        overall_scores={"pron_score": 62.0},
        recognised_text="the north wind and the sun",
        words=[],
    )


def test_the_offline_coach_writes_phrases_for_the_gaps_it_is_handed() -> None:
    from utils import Mode

    report = fc.build(assessment(), Mode.PARAGRAPH, gaps=[gap("ɝ"), gap("u")])
    assert [phrase.vowel for phrase in report.bridging_phrases] == ["ɝ", "u"]
    assert all(phrase.phrase.strip() for phrase in report.bridging_phrases)


def test_the_offline_coach_writes_none_when_there_is_no_geometry() -> None:
    """The ordinary case: no stored baseline, so no geometry, and the report is unchanged."""
    from utils import Mode

    assert fc.build(assessment(), Mode.PARAGRAPH).bridging_phrases == []


def test_the_model_coach_is_handed_the_geometry_too() -> None:
    """Offline, so this exercises the compaction and the fallback, which is the shared path."""
    from utils import Mode

    result = ai_coach.coach(
        assessment(), "the north wind and the sun", Mode.PARAGRAPH, gaps=[gap("ɝ")]
    )
    assert [phrase.vowel for phrase in result.report.bridging_phrases] == ["ɝ"]


def test_a_whole_reading_measure_carries_no_bridging_phrase() -> None:
    """`rhythm_gap` has no vowel — it is a property of the reading, not of a token.

    It belongs in the section, because what the coach needs is one continuous picture rather
    than two. It must not produce a phrase, and it must not license one either.
    """
    pace = vowel_measure.rhythm_gap(40.0, 55.0)
    assert pace is not None and pace.vowel == ""
    compacted = fc.with_geometry(dict(BASE), [pace, gap("i")])

    assert len(compacted["vowel_geometry"]) == 2, "the rhythm measure was dropped from the payload"
    assert [phrase.vowel for phrase in fc.bridging_phrases(compacted)] == ["i"]

    invented = fc.CoachingReport(
        overall_comment="x",
        priority_fixes=[],
        delivery_drills=[],
        stress_and_rhythm=fc.StressAndRhythm(issues=["x"], drill="x"),
        practice_plan="x",
        bridging_phrases=[
            fc.BridgingPhrase(vowel="", keyword="", why="x", phrase="Say it evenly.")
        ],
    )
    kept = ai_coach._checked_bridging_phrases(invented, compacted)
    assert all(phrase.vowel for phrase in kept), "a phrase was kept for a vowel-less measure"


def test_the_app_hands_the_geometry_to_whichever_coach_runs() -> None:
    """Asserted against the source: this is a wiring bug, and wiring is not observable.

    `AppTest` cannot reach this — it needs a stored baseline, a reference set and a real
    measurement — and a unit test of `geometry_gaps` would pass just as happily while nothing
    called it. What has to be true is that both branches receive `gaps`.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "src" / "app.py").read_text("utf-8")
    body = source[source.index('    cache = _session_cache("coaching")') :]
    body = body[: body.index("    lru_put(cache")]

    assert "gaps = geometry_gaps(conn, entry)" in body
    assert "ai_coach.coach(" in body and "gaps=gaps" in body
    assert "fallback_coach.build(entry.assessment, entry.mode, gaps=gaps)" in body
    assert body.index("gaps = geometry_gaps") < body.index("if ask_model:"), (
        "the geometry is derived after the branch, so one coach would not see it"
    )
