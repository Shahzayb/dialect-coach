"""Assembling a practice unit: which units exist, which voice plays them, what judges them."""

from __future__ import annotations

import io
import itertools
import wave

import ladder
import ladder_practice
import ladder_reference
import progress_view
import shadowing

SCRIPT = "The cat sat. It slept soundly."


def _word(text: str, start: float | None, end: float | None) -> dict[str, object]:
    return {"word": text, "start_s": start, "end_s": end, "phonemes": []}


def _reading(extra: str | None = None) -> list[dict[str, object]]:
    words = [
        _word("The", 0.0, 0.1),
        _word("cat", 0.1, 0.3),
        _word("sat.", 0.3, 0.6),
        _word("It", 0.9, 1.0),
        _word("slept", 1.0, 1.4),
        _word("soundly.", 1.4, 1.9),
    ]
    if extra:
        words.insert(1, _word(extra, 0.05, 0.09))
    return words


def _wav(seconds: float) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as sink:
        sink.setnchannels(1)
        sink.setsampwidth(2)
        sink.setframerate(16_000)
        sink.writeframes(b"\x00\x00" * int(seconds * 16_000))
    return out.getvalue()


# --- Choosing the voice that plays the native leg -------------------------------------------------


def test_the_native_voice_is_the_one_nearest_the_speakers_own_pitch() -> None:
    low = ladder_practice.nearest_voice(105.0)
    high = ladder_practice.nearest_voice(220.0)
    assert low is not None and high is not None
    assert ladder_reference.MEDIAN_F0_HZ[low] < 130.0
    assert ladder_reference.MEDIAN_F0_HZ[high] > 200.0


def test_matching_on_pitch_sex_matches_without_needing_a_voice_roster() -> None:
    """The stored medians are cleanly bimodal, so this falls out rather than being asserted."""
    values = sorted(ladder_reference.MEDIAN_F0_HZ.values())
    gaps = [(b - a, a) for a, b in itertools.pairwise(values)]
    widest, below = max(gaps)
    assert widest > 20.0, "the two registers are not separable, so this claim does not hold"
    assert below < 160.0


def test_no_pitch_to_match_on_yields_no_voice_rather_than_an_arbitrary_one() -> None:
    assert ladder_practice.nearest_voice(None) is None


# --- Which units are practisable ------------------------------------------------------------------


def test_units_carry_their_position_in_the_script_not_in_the_word_list() -> None:
    """Bands are keyed on the script, which is what survives a stumble."""
    stumbled = ladder_practice.units(_reading("um"), SCRIPT, ladder.Rung.WORD)
    slept = next(u for u in stumbled if u.span.label == "slept")
    clean = ladder_practice.units(_reading(), SCRIPT, ladder.Rung.WORD)
    assert slept.script_index == next(u for u in clean if u.span.label == "slept").script_index


def test_an_inserted_word_has_no_place_in_the_script() -> None:
    stumbled = ladder_practice.units(_reading("um"), SCRIPT, ladder.Rung.WORD)
    assert next(u for u in stumbled if u.span.label == "um").script_index is None


def test_a_unit_with_no_band_is_not_judgeable() -> None:
    """The made-up script has no reference behind it, so nothing on it can resolve."""
    for unit in ladder_practice.units(_reading(), SCRIPT, ladder.Rung.SENTENCE):
        assert not unit.judgeable


def test_the_benchmark_passage_has_judgeable_units_at_every_measured_rung() -> None:
    text = progress_view.BENCHMARK_PASSAGE
    words = [_word(w, i * 0.3, i * 0.3 + 0.25) for i, w in enumerate(text.split())]
    for rung in (ladder.Rung.WORD, ladder.Rung.SENTENCE, ladder.Rung.PARAGRAPH):
        found = ladder_practice.units(words, text, rung)
        assert any(u.judgeable for u in found), rung


def test_rhythm_is_banded_where_it_is_measurable_and_absent_where_it_is_not() -> None:
    """nPVI needs rhythm.MIN_PAIRS vocalic pairs, so it is a passage-scale measure.

    Short sentences carry no rhythm band at all, and that is the instrument refusing rather
    than a gap: they are still judged on pitch range and terminal fall, and `Verdict.resolved`
    only requires the metrics that ARE judgeable to clear.
    """
    banded = [i for i, m in ladder_reference.SENTENCE.items() if "npvi" in m]
    assert banded, "no sentence carries a rhythm band at all"
    assert len(banded) < len(ladder_reference.SENTENCE), "every sentence has one — check MIN_PAIRS"
    assert "npvi" in ladder_reference.PARAGRAPH
    phrases = shadowing.phrases(progress_view.BENCHMARK_PASSAGE)
    longest = max(range(len(phrases)), key=lambda i: len(phrases[i].split()))
    assert longest in banded


# --- The native leg -------------------------------------------------------------------------------


def test_the_native_leg_is_cut_from_the_matching_unit() -> None:
    mine_words, their_words = _reading(), _reading()
    mine = ladder.align(mine_words, SCRIPT)
    theirs = ladder.align(their_words, SCRIPT)
    span = ladder.sentence_spans(mine_words, SCRIPT)[1]
    leg = ladder_practice.native_leg(span, mine, theirs, their_words, _wav(3.0), "en-US-XNeural")
    assert leg is not None
    assert leg.voice == "en-US-XNeural"
    assert leg.span.label == "It slept soundly."
    assert 0 < len(leg.audio) < len(_wav(3.0))


def test_a_unit_with_no_counterpart_plays_nothing_rather_than_a_neighbour() -> None:
    mine_words, their_words = _reading("um"), _reading()
    mine = ladder.align(mine_words, SCRIPT)
    theirs = ladder.align(their_words, SCRIPT)
    stumble = next(s for s in ladder.word_spans(mine_words) if s.label == "um")
    assert (
        ladder_practice.native_leg(stumble, mine, theirs, their_words, _wav(3.0), "en-US-XNeural")
        is None
    )


def test_the_sound_rung_has_no_native_leg_of_its_own() -> None:
    """It is heard inside its word, so the surface walks it up before asking for audio."""
    mine_words, their_words = _reading(), _reading()
    mine = ladder.align(mine_words, SCRIPT)
    theirs = ladder.align(their_words, SCRIPT)
    sound = ladder.Span(ladder.Rung.SOUND, "k", 0.1, 0.2, (1,), phoneme_index=0)
    assert (
        ladder_practice.native_leg(sound, mine, theirs, their_words, _wav(3.0), "en-US-XNeural")
        is None
    )


# --- The bands describe one passage, and refuse for any other -------------------------------------
# Caught by a test that expected an unbanded unit and got the benchmark's numbers instead. The
# bands are keyed by POSITION, so an index lookup alone hands sentence 0 of any text the numbers
# measured from sentence 0 of the benchmark. That is a confident wrong answer, not a thin one.


def test_the_bands_cover_the_benchmark_passage() -> None:
    assert ladder_practice.covers(progress_view.BENCHMARK_PASSAGE)


def test_the_bands_refuse_a_passage_they_were_not_measured_on() -> None:
    assert not ladder_practice.covers(SCRIPT)
    assert not ladder_practice.covers(None)
    assert not ladder_practice.covers("")


def test_a_passage_with_one_word_changed_is_not_covered() -> None:
    """Editing the benchmark invalidates these bands exactly as it invalidates the series."""
    edited = progress_view.BENCHMARK_PASSAGE.replace("morning", "evening", 1)
    assert edited != progress_view.BENCHMARK_PASSAGE
    assert not ladder_practice.covers(edited)


def test_another_passage_gets_no_bands_at_any_rung() -> None:
    for rung in (ladder.Rung.WORD, ladder.Rung.SENTENCE, ladder.Rung.PARAGRAPH):
        for unit in ladder_practice.units(_reading(), SCRIPT, rung):
            assert unit.bands == {}, rung


def test_the_paragraph_band_is_not_handed_out_to_an_unrelated_recording() -> None:
    """The paragraph lookup takes no index, so it is the easiest one to leak."""
    assert ladder_practice.bands_for(ladder.Rung.PARAGRAPH, None, SCRIPT) == {}
    assert ladder_practice.bands_for(ladder.Rung.PARAGRAPH, None, None) == {}
    assert ladder_practice.bands_for(ladder.Rung.PARAGRAPH, None, progress_view.BENCHMARK_PASSAGE)
