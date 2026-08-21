"""The four rungs: span building, containment, and the cut that must not run long.

Two properties here are the ones that would fail silently rather than loudly, and both get
named tests below:

- **The clip edges.** A span at the very start or very end of a recording is where Praat's
  `Extract part` returns the whole sound instead of nothing. `ladder` avoids Praat entirely for
  playback, and these assert the cut really is bounded — a first-word cut that came back the
  length of the recording would still sound plausible in a browser.
- **The sentence mapping.** A reference text whose tokens do not line up with the words would
  put sentence boundaries mid-phrase. That has to refuse, not approximate.
"""

from __future__ import annotations

import io
import wave

import pytest

import ladder

RATE = 16_000


def _wav(seconds: float, *, rate: int = RATE) -> bytes:
    """A silent mono 16-bit WAV. Only its length matters to the cut."""
    out = io.BytesIO()
    with wave.open(out, "wb") as sink:
        sink.setnchannels(1)
        sink.setsampwidth(2)
        sink.setframerate(rate)
        sink.writeframes(b"\x00\x00" * int(seconds * rate))
    return out.getvalue()


def _seconds(wav_bytes: bytes) -> float:
    with wave.open(io.BytesIO(wav_bytes), "rb") as source:
        return source.getnframes() / source.getframerate()


def _word(
    text: str,
    start: float | None,
    end: float | None,
    phonemes: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "word": text,
        "start_s": start,
        "end_s": end,
        "phonemes": phonemes or [],
    }


# Three words across one sentence, timed end to end.
WORDS: list[dict[str, object]] = [
    _word(
        "The",
        0.00,
        0.12,
        [
            {"phoneme": "ð", "start_s": 0.00, "end_s": 0.06},
            {"phoneme": "ə", "start_s": 0.06, "end_s": 0.12},
        ],
    ),
    _word(
        "cat",
        0.12,
        0.40,
        [
            {"phoneme": "k", "start_s": 0.12, "end_s": 0.20},
            {"phoneme": "æ", "start_s": 0.20, "end_s": 0.34},
            {"phoneme": "t", "start_s": 0.34, "end_s": 0.40},
        ],
    ),
    _word(
        "sat.",
        0.40,
        0.75,
        [
            {"phoneme": "s", "start_s": 0.40, "end_s": 0.50},
            {"phoneme": "æ", "start_s": 0.50, "end_s": 0.68},
            {"phoneme": "t", "start_s": 0.68, "end_s": 0.75},
        ],
    ),
]

TEXT = "The cat sat."


# --- The ladder itself ----------------------------------------------------------------------


def test_the_rung_above_each_one_is_the_next_size_up() -> None:
    assert ladder.above(ladder.Rung.SOUND) is ladder.Rung.WORD
    assert ladder.above(ladder.Rung.WORD) is ladder.Rung.SENTENCE
    assert ladder.above(ladder.Rung.SENTENCE) is ladder.Rung.PARAGRAPH


def test_the_paragraph_has_nothing_above_it() -> None:
    """None means 'checked against itself', which the surface states rather than skipping."""
    assert ladder.above(ladder.Rung.PARAGRAPH) is None


def test_the_sound_rung_is_not_audible_on_its_own() -> None:
    assert ladder.Rung.SOUND not in ladder.AUDIBLE
    assert {ladder.Rung.WORD, ladder.Rung.SENTENCE, ladder.Rung.PARAGRAPH} == ladder.AUDIBLE


# --- Spans ------------------------------------------------------------------------------------


def test_word_spans_cover_every_spoken_word() -> None:
    found = ladder.word_spans(WORDS)
    assert [span.label for span in found] == ["The", "cat", "sat."]
    assert found[0].start_s == pytest.approx(0.0)
    assert found[-1].end_s == pytest.approx(0.75)


def test_a_word_that_was_never_spoken_yields_no_span() -> None:
    """An omission carries the timing keys as None, and has nowhere to point."""
    words = [*WORDS, _word("quietly", None, None)]
    assert len(ladder.word_spans(words)) == 3


def test_sound_spans_include_consonants_not_only_vowels() -> None:
    """The sound rung inherits CONTRAST targets, which are routinely consonant pairs."""
    labels = [span.label for span in ladder.sound_spans(WORDS)]
    assert "k" in labels
    assert "s" in labels
    assert labels.count("æ") == 2


def test_the_same_vowel_twice_stays_two_distinct_sounds() -> None:
    """Positional, not by symbol — the two /æ/ tokens are different practice units."""
    ash = [span for span in ladder.sound_spans(WORDS) if span.label == "æ"]
    assert ash[0].word_indices == (1,)
    assert ash[1].word_indices == (2,)


def test_a_sentence_span_covers_its_words_and_spans_their_time() -> None:
    found = ladder.sentence_spans(WORDS, TEXT)
    assert len(found) == 1
    assert found[0].word_indices == (0, 1, 2)
    assert found[0].start_s == pytest.approx(0.0)
    assert found[0].end_s == pytest.approx(0.75)


def test_two_sentences_map_to_two_spans() -> None:
    words = [
        _word("The", 0.0, 0.1),
        _word("cat", 0.1, 0.4),
        _word("sat.", 0.4, 0.7),
        _word("It", 0.9, 1.0),
        _word("slept", 1.0, 1.4),
        _word("soundly.", 1.4, 1.9),
    ]
    found = ladder.sentence_spans(words, "The cat sat. It slept soundly.")
    assert [span.word_indices for span in found] == [(0, 1, 2), (3, 4, 5)]


def test_the_sentence_mapping_refuses_when_the_text_does_not_match_the_words() -> None:
    """A wrong mapping would put a boundary mid-phrase. Refuse, do not approximate."""
    assert ladder.sentence_spans(WORDS, "Something else entirely was said.") == []


def test_there_are_no_sentences_without_a_reference_text() -> None:
    """Mode C's ordinary case: a prompt nothing was scored against is not a script."""
    assert ladder.sentence_spans(WORDS, None) == []
    assert ladder.sentence_spans(WORDS, "") == []


def test_a_multi_token_word_does_not_shift_the_mapping() -> None:
    """'well-known' yields two tokens; a naive split desynchronises every later index."""
    words = [
        _word("A", 0.0, 0.1),
        _word("well-known", 0.1, 0.6),
        _word("fact.", 0.6, 0.9),
        _word("Then", 1.1, 1.3),
        _word("everyone", 1.3, 1.8),
        _word("left.", 1.8, 2.1),
    ]
    found = ladder.sentence_spans(words, "A well-known fact. Then everyone left.")
    assert [span.word_indices for span in found] == [(0, 1, 2), (3, 4, 5)]


def test_a_sentence_too_short_to_stand_alone_merges_the_way_the_echo_track_merges_it() -> None:
    """Inherited from `shadowing.MIN_PHRASE_CHARS`, and inherited on purpose.

    The echo track and the practice ladder must not disagree about where a sentence ends, so
    the ladder reuses the split rather than defining a second one. A fragment below the floor
    joins its neighbour in both.
    """
    words = [
        _word("The", 0.0, 0.1),
        _word("cat", 0.1, 0.4),
        _word("sat.", 0.4, 0.7),
        _word("It", 0.9, 1.0),
        _word("did.", 1.0, 1.3),
    ]
    found = ladder.sentence_spans(words, "The cat sat. It did.")
    assert [span.word_indices for span in found] == [(0, 1, 2, 3, 4)]


def test_a_paragraph_span_is_the_whole_recording() -> None:
    whole = ladder.paragraph_span(WORDS, TEXT)
    assert whole is not None
    assert whole.rung is ladder.Rung.PARAGRAPH
    assert whole.word_indices == (0, 1, 2)


def test_a_recording_where_nothing_was_timed_has_no_paragraph_span() -> None:
    assert ladder.paragraph_span([_word("gone", None, None)]) is None


# --- Containment and the walk up ---------------------------------------------------------------


def test_a_sound_sits_inside_its_word() -> None:
    sound = ladder.sound_spans(WORDS)[2]  # /k/ in "cat"
    word = ladder.enclosing(sound, ladder.word_spans(WORDS))
    assert word is not None
    assert word.label == "cat"


def test_containment_is_by_words_not_by_timestamps() -> None:
    """Float comparison at a clip boundary would make containment turn on rounding."""
    sentence = ladder.sentence_spans(WORDS, TEXT)[0]
    for word in ladder.word_spans(WORDS):
        assert sentence.contains(word)


def test_a_word_from_another_sentence_is_not_contained() -> None:
    words = [
        _word("The", 0.0, 0.1),
        _word("cat", 0.1, 0.4),
        _word("sat.", 0.4, 0.7),
        _word("It", 0.9, 1.0),
        _word("slept", 1.0, 1.4),
        _word("soundly.", 1.4, 1.9),
    ]
    first, second = ladder.sentence_spans(words, "The cat sat. It slept soundly.")
    assert not first.contains(ladder.word_spans(words)[4])
    assert second.contains(ladder.word_spans(words)[4])


def test_a_sound_is_played_as_its_word() -> None:
    sound = ladder.sound_spans(WORDS)[3]  # /æ/ in "cat"
    playable = ladder.audible_span_for(sound, WORDS)
    assert playable is not None
    assert playable.rung is ladder.Rung.WORD
    assert playable.label == "cat"


def test_an_audible_rung_is_played_as_itself() -> None:
    word = ladder.word_spans(WORDS)[1]
    assert ladder.audible_span_for(word, WORDS) is word


# --- The cut ------------------------------------------------------------------------------------


def test_a_cut_is_the_length_of_its_span_plus_padding() -> None:
    audio = _wav(2.0)
    out = ladder.slice_wav(audio, 0.5, 1.0)
    assert _seconds(out) == pytest.approx(0.5 + 2 * ladder.PAD_S, abs=0.002)


def test_a_span_at_the_very_start_does_not_return_the_whole_recording() -> None:
    """The Extract part trap, asserted from the outside. A first-word cut is short."""
    audio = _wav(5.0)
    out = ladder.slice_wav(audio, 0.0, 0.30)
    assert _seconds(out) == pytest.approx(0.30 + ladder.PAD_S, abs=0.002)
    assert _seconds(out) < _seconds(audio)


def test_a_span_at_the_very_end_does_not_return_the_whole_recording() -> None:
    audio = _wav(5.0)
    out = ladder.slice_wav(audio, 4.70, 5.00)
    assert _seconds(out) == pytest.approx(0.30 + ladder.PAD_S, abs=0.002)
    assert _seconds(out) < _seconds(audio)


def test_a_cut_covering_the_whole_recording_is_still_bounded_by_it() -> None:
    audio = _wav(1.0)
    assert _seconds(ladder.slice_wav(audio, 0.0, 1.0)) == pytest.approx(1.0, abs=0.002)


def test_a_zero_length_span_refuses() -> None:
    with pytest.raises(ladder.LadderError):
        ladder.slice_wav(_wav(1.0), 0.5, 0.5)


def test_a_span_past_the_end_of_the_recording_refuses() -> None:
    with pytest.raises(ladder.LadderError):
        ladder.slice_wav(_wav(1.0), 3.0, 3.5)


def test_audio_that_is_not_wav_refuses_rather_than_crashes() -> None:
    with pytest.raises(ladder.LadderError):
        ladder.slice_wav(b"not audio at all", 0.0, 0.5)


def test_the_cut_preserves_the_sample_rate_and_channel_count() -> None:
    out = ladder.slice_wav(_wav(2.0), 0.5, 1.0)
    with wave.open(io.BytesIO(out), "rb") as source:
        assert source.getframerate() == RATE
        assert source.getnchannels() == 1
        assert source.getsampwidth() == 2


# --- Arrival bands ------------------------------------------------------------------------------
# The band is the "have I arrived" half of measured resolution. The "did I really move" half is
# `vowel_measure.NoiseFloor` and is tested with it.


def test_a_value_inside_one_sd_has_arrived() -> None:
    band = ladder.Band(metric="npvi", mean=55.0, sd=4.0, voices=16)
    assert band.contains(57.0)
    assert band.contains(51.5)
    assert band.distance(57.0) == 0.0


def test_a_value_outside_the_band_has_not_arrived_and_says_how_far() -> None:
    band = ladder.Band(metric="npvi", mean=55.0, sd=4.0, voices=16)
    assert not band.contains(70.0)
    assert band.distance(63.0) == pytest.approx(1.0)


def test_the_band_is_symmetric() -> None:
    """Arrival cannot be easier from one side, or the flattering direction wins."""
    band = ladder.Band(metric="pitch_range_st", mean=10.0, sd=2.0, voices=16)
    assert band.contains(12.0) == band.contains(8.0)
    assert band.distance(14.0) == band.distance(6.0)


def test_a_band_with_no_spread_refuses_rather_than_accepting_everything() -> None:
    """One voice, or sixteen identical ones, cannot answer 'is this native-like'."""
    band = ladder.Band(metric="npvi", mean=55.0, sd=0.0, voices=1)
    assert not band.contains(55.0)
    assert band.distance(55.0) is None


def test_a_missing_measurement_never_counts_as_arrival() -> None:
    band = ladder.Band(metric="npvi", mean=55.0, sd=4.0, voices=16)
    assert not band.contains(None)
    assert band.distance(None) is None


def test_widening_the_band_is_possible_but_not_the_default() -> None:
    """One SD by default: two would call almost anything arrived."""
    band = ladder.Band(metric="npvi", mean=55.0, sd=4.0, voices=16)
    assert not band.contains(62.0)
    assert band.contains(62.0, width=2.0)


# --- The movement half, and the verdict that joins the two bars -----------------------------------


def test_the_noise_floor_is_the_median_displacement_across_units() -> None:
    floor = ladder.metric_noise_floor(
        {0: {"npvi": 55.0}, 1: {"npvi": 60.0}, 2: {"npvi": 50.0}},
        {0: {"npvi": 57.0}, 1: {"npvi": 66.0}, 2: {"npvi": 51.0}},
    )
    assert floor.per_metric["npvi"] == pytest.approx(2.0)  # median of 2, 6, 1
    assert floor.units == 3


def test_a_unit_measured_in_only_one_read_contributes_no_displacement() -> None:
    floor = ladder.metric_noise_floor({0: {"npvi": 55.0}, 9: {"npvi": 1.0}}, {0: {"npvi": 58.0}})
    assert floor.units == 1
    assert floor.per_metric["npvi"] == pytest.approx(3.0)


def test_a_change_smaller_than_the_floor_is_not_movement() -> None:
    floor = ladder.MetricFloor(per_metric={"npvi": 3.0}, units=5)
    assert floor.within_noise("npvi", 2.5)
    assert not floor.within_noise("npvi", 3.5)


def test_the_floor_refuses_in_the_flattering_direction_too() -> None:
    """The rule is symmetric, and that is the whole point of measuring it."""
    floor = ladder.MetricFloor(per_metric={"npvi": 3.0}, units=5)
    assert floor.within_noise("npvi", 2.5) == floor.within_noise("npvi", -2.5)


def test_a_metric_with_no_floor_cannot_be_called_movement() -> None:
    floor = ladder.MetricFloor(per_metric={}, units=0)
    assert floor.within_noise("npvi", 100.0)


def _span(rung: ladder.Rung = ladder.Rung.SENTENCE) -> ladder.Span:
    return ladder.Span(rung=rung, label="x", start_s=0.0, end_s=1.0, word_indices=(0,))


BANDS = {
    "npvi": ladder.Band("npvi", mean=55.0, sd=4.0, voices=16),
    "pitch_range_st": ladder.Band("pitch_range_st", mean=10.0, sd=2.0, voices=16),
    "terminal_slope_st": ladder.Band("terminal_slope_st", mean=-4.0, sd=1.5, voices=16),
}
FLOOR = ladder.MetricFloor(per_metric={"npvi": 2.0, "pitch_range_st": 1.0}, units=6)


def test_arrival_without_movement_is_not_resolved() -> None:
    """A reading that happened to land says nothing about whether the speaker changed."""
    result = ladder.verdict(
        _span(),
        {"npvi": 55.5},
        BANDS,
        previous={"npvi": 55.0},  # a 0.5 change, under the 2.0 floor
        floor=FLOOR,
    )
    npvi = result.for_metric("npvi")
    assert npvi is not None
    assert npvi.arrived
    assert npvi.moved is False
    assert not npvi.resolved


def test_movement_without_arrival_is_not_resolved() -> None:
    result = ladder.verdict(_span(), {"npvi": 70.0}, BANDS, previous={"npvi": 80.0}, floor=FLOOR)
    npvi = result.for_metric("npvi")
    assert npvi is not None
    assert not npvi.arrived
    assert npvi.moved is True
    assert not npvi.resolved


def test_both_bars_together_resolve() -> None:
    result = ladder.verdict(_span(), {"npvi": 56.0}, BANDS, previous={"npvi": 65.0}, floor=FLOOR)
    npvi = result.for_metric("npvi")
    assert npvi is not None
    assert npvi.resolved


def test_a_first_attempt_has_not_moved_rather_than_failed_to_move() -> None:
    """'First attempt' and 'did not move' are different states; only one is a failure."""
    result = ladder.verdict(_span(), {"npvi": 56.0}, BANDS)
    npvi = result.for_metric("npvi")
    assert npvi is not None
    assert npvi.moved is None
    assert not npvi.resolved


def test_a_rung_with_nothing_judgeable_is_not_resolved() -> None:
    """all(()) is True and would graduate a problem the instruments could not see."""
    result = ladder.verdict(_span(), {}, {})
    assert result.judgeable == ()
    assert not result.resolved


def test_a_span_resolves_only_when_every_judgeable_metric_does() -> None:
    result = ladder.verdict(
        _span(),
        {"npvi": 56.0, "pitch_range_st": 20.0},
        BANDS,
        previous={"npvi": 65.0, "pitch_range_st": 30.0},
        floor=FLOOR,
    )
    assert result.for_metric("npvi") is not None
    assert result.for_metric("npvi").resolved  # type: ignore[union-attr]
    assert not result.resolved  # pitch range is nowhere near its band


def test_the_word_rung_is_judged_on_its_own_metric_only() -> None:
    result = ladder.verdict(_span(ladder.Rung.WORD), {"relative_duration": 1.0}, {})
    assert [m.metric for m in result.metrics] == ["relative_duration"]


def test_the_sound_rung_is_judged_elsewhere_and_carries_no_metrics_here() -> None:
    """model_reference.sd50 already answers for vowel position; a second band would drift."""
    assert ladder.Rung.SOUND not in ladder.METRICS
    assert ladder.verdict(_span(ladder.Rung.SOUND), {}, {}).metrics == ()


# --- The mapping has to survive real speech -------------------------------------------------------
# Found by running the builder against the real reference capture: one voice inserted a word and
# the strict position-for-position mapping refused every sentence in that recording. A human
# stumbles far more than a synthesiser, so this is the ordinary case, not the edge case.


def test_an_inserted_word_does_not_invalidate_the_passage() -> None:
    """The exact failure GuyNeural hit: an extra word heard mid-sentence."""
    words = [
        _word("The", 0.0, 0.1),
        _word("cat", 0.1, 0.3),
        _word("um", 0.3, 0.4),  # never in the script
        _word("sat.", 0.4, 0.7),
        _word("It", 0.9, 1.0),
        _word("slept", 1.0, 1.4),
        _word("soundly.", 1.4, 1.9),
    ]
    found = ladder.sentence_spans(words, "The cat sat. It slept soundly.")
    assert len(found) == 2
    assert found[0].word_indices == (0, 1, 2, 3)
    assert found[1].word_indices == (4, 5, 6)


def test_an_inserted_word_joins_the_sentence_it_was_spoken_inside() -> None:
    words = [
        _word("The", 0.0, 0.1),
        _word("cat", 0.1, 0.3),
        _word("sat.", 0.3, 0.6),
        _word("It", 0.9, 1.0),
        _word("er", 1.0, 1.1),
        _word("slept", 1.1, 1.4),
        _word("soundly.", 1.4, 1.9),
    ]
    first, second = ladder.sentence_spans(words, "The cat sat. It slept soundly.")
    assert 4 not in first.word_indices
    assert 4 in second.word_indices


def test_an_omitted_word_does_not_shift_the_sentences_after_it() -> None:
    words = [
        _word("The", 0.0, 0.1),
        _word("sat.", 0.1, 0.4),  # "cat" never spoken
        _word("It", 0.9, 1.0),
        _word("slept", 1.0, 1.4),
        _word("soundly.", 1.4, 1.9),
    ]
    first, second = ladder.sentence_spans(words, "The cat sat. It slept soundly.")
    assert first.word_indices == (0, 1)
    assert second.word_indices == (2, 3, 4)


def test_a_repeated_word_stays_inside_its_own_sentence() -> None:
    """A stumble is an Insertion to Azure, and the queue must not lose the passage over one."""
    words = [
        _word("The", 0.0, 0.1),
        _word("cat", 0.1, 0.3),
        _word("cat", 0.3, 0.5),
        _word("sat.", 0.5, 0.8),
        _word("It", 1.0, 1.1),
        _word("slept", 1.1, 1.5),
        _word("soundly.", 1.5, 2.0),
    ]
    first, second = ladder.sentence_spans(words, "The cat sat. It slept soundly.")
    assert first.word_indices == (0, 1, 2, 3)
    assert second.word_indices == (4, 5, 6)


def test_a_genuinely_different_text_still_refuses() -> None:
    """Tolerating stumbles must not become tolerating the wrong script."""
    assert ladder.sentence_spans(WORDS, "Entirely unrelated wording appears here instead.") == []


# --- Matching a unit across two readings of the same script ---------------------------------------
# The native leg needs the SAME unit in a model voice's reading. Position will not do it: the
# n-th spoken word in one reading is not the n-th in another as soon as either side stumbled,
# and accent_charts.word_anchors stops dead at the first divergence for exactly that reason.


def _reading(extra: str | None = None) -> list[dict[str, object]]:
    """The same script, optionally with one word stumbled in partway through."""
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


SCRIPT = "The cat sat. It slept soundly."


def test_a_sentence_matches_its_counterpart_in_another_reading() -> None:
    mine, theirs = _reading(), _reading()
    my_spans = ladder.sentence_spans(mine, SCRIPT)
    their_spans = ladder.sentence_spans(theirs, SCRIPT)
    found = ladder.matching_span(
        my_spans[1], ladder.align(mine, SCRIPT), ladder.align(theirs, SCRIPT), theirs, their_spans
    )
    assert found is not None
    assert found.label == "It slept soundly."


def test_a_stumble_in_one_reading_does_not_misalign_the_other() -> None:
    """The failure this exists to prevent: every later unit matched one word to the left."""
    mine, theirs = _reading("um"), _reading()
    my_align, their_align = ladder.align(mine, SCRIPT), ladder.align(theirs, SCRIPT)
    my_spans = ladder.sentence_spans(mine, SCRIPT)
    their_spans = ladder.sentence_spans(theirs, SCRIPT)
    found = ladder.matching_span(my_spans[1], my_align, their_align, theirs, their_spans)
    assert found is not None
    assert found.label == "It slept soundly."


def test_a_word_matches_the_script_word_it_realises_not_its_position() -> None:
    mine, theirs = _reading("um"), _reading()
    my_align, their_align = ladder.align(mine, SCRIPT), ladder.align(theirs, SCRIPT)
    # "slept" is index 5 in the stumbled reading and index 4 in the clean one.
    mine_slept = next(s for s in ladder.word_spans(mine) if s.label == "slept")
    found = ladder.matching_span(mine_slept, my_align, their_align, theirs, [])
    assert found is not None
    assert found.label == "slept"


def test_an_inserted_word_has_no_counterpart_and_says_so() -> None:
    """It stands for nothing in the script, so there is no native version of it to play."""
    mine, theirs = _reading("um"), _reading()
    my_align, their_align = ladder.align(mine, SCRIPT), ladder.align(theirs, SCRIPT)
    stumble = next(s for s in ladder.word_spans(mine) if s.label == "um")
    assert ladder.matching_span(stumble, my_align, their_align, theirs, []) is None


def test_a_reading_of_a_different_script_aligns_to_nothing() -> None:
    assert not ladder.align(_reading(), "Something else was said entirely here.").usable
    assert not ladder.align(_reading(), None).usable
