"""The arrival bands this project measured for itself.

`src/ladder_reference.py` is a GENERATED file derived from renderings bought with real Azure
allowance, so these tests do the job ordinary unit tests do not: check that the generator did
not silently produce a plausible-looking table, and pin the decisions that make the numbers
mean what the surface says they mean.
"""

from __future__ import annotations

import pytest

import ladder
import ladder_reference
import progress_view
import shadowing
from native_model import MIN_VOICES_PER_SET


def _all_bands() -> list[ladder.Band]:
    found = list(ladder_reference.PARAGRAPH.values())
    for units in (ladder_reference.WORD, ladder_reference.SENTENCE):
        for metrics in units.values():
            found.extend(metrics.values())
    return found


# --- It exists, and it is not empty -------------------------------------------------------------


def test_every_rung_that_should_have_bands_has_them() -> None:
    assert ladder_reference.WORD
    assert ladder_reference.SENTENCE
    assert ladder_reference.PARAGRAPH


def test_it_rests_on_the_full_reference_set() -> None:
    assert ladder_reference.VOICES >= MIN_VOICES_PER_SET * 2


def test_every_band_rests_on_enough_talkers() -> None:
    for band in _all_bands():
        assert band.voices >= MIN_VOICES_PER_SET, band


def test_every_band_has_a_real_spread() -> None:
    """A band with sd of 0 accepts nothing, so publishing one would be a silent dead end."""
    for band in _all_bands():
        assert band.sd > 0.0, band


# --- The decisions the numbers depend on ---------------------------------------------------------


def test_the_sound_rung_is_absent_and_left_to_the_vowel_tables() -> None:
    """Two sources of truth for one claim would drift. model_reference.sd50 owns this."""
    assert ladder.Rung.SOUND not in ladder.METRICS


def test_azure_prosody_is_not_a_band() -> None:
    """Across 16 voices it spans 1.2 points — how uniform the TTS is, not how talkers vary.

    It is also the one metric that would have jammed the whole ladder: a span resolves only
    when every judgeable metric clears, so a band no real speaker can land inside would block
    the paragraph rung and, through the level-above rule, every sentence beneath it.
    """
    assert "prosody" not in ladder.METRICS[ladder.Rung.PARAGRAPH]
    for metrics in ladder_reference.SENTENCE.values():
        assert "prosody" not in metrics
    assert "prosody" not in ladder_reference.PARAGRAPH


def test_word_bands_are_relative_never_milliseconds() -> None:
    """A band in ms would mostly measure speaking rate. Relative measures reduction."""
    assert ladder.METRICS[ladder.Rung.WORD] == ("relative_duration",)
    for metrics in ladder_reference.WORD.values():
        assert set(metrics) <= {"relative_duration"}
        # Divided by the reading's own mean word, so the values straddle 1.0.
        assert 0.1 < metrics["relative_duration"].mean < 5.0


def test_the_relative_word_durations_average_about_one() -> None:
    """They are each word over the same reading's mean word, so the set has to centre on 1."""
    means = [m["relative_duration"].mean for m in ladder_reference.WORD.values()]
    assert sum(means) / len(means) == pytest.approx(1.0, abs=0.1)


def test_every_band_is_declared_for_the_metric_it_is_keyed_under() -> None:
    """A band filed under the wrong metric would compare two different quantities."""
    for units in (ladder_reference.WORD, ladder_reference.SENTENCE):
        for metrics in units.values():
            for metric, band in metrics.items():
                assert band.metric == metric
    for metric, band in ladder_reference.PARAGRAPH.items():
        assert band.metric == metric


def test_no_band_is_published_for_a_metric_its_rung_is_not_judged_on() -> None:
    for index, metrics in ladder_reference.SENTENCE.items():
        assert set(metrics) <= set(ladder.METRICS[ladder.Rung.SENTENCE]), index
    assert set(ladder_reference.PARAGRAPH) <= set(ladder.METRICS[ladder.Rung.PARAGRAPH])


# --- It describes the passage it was measured on -------------------------------------------------


def test_it_is_keyed_to_the_benchmark_passage_it_was_measured_from() -> None:
    """A changed passage invalidates every index in here, which is what the version records."""
    assert ladder_reference.BENCHMARK_VERSION == progress_view.BENCHMARK_VERSION


def test_the_sentence_keys_match_the_split_the_app_uses() -> None:
    phrases = shadowing.phrases(progress_view.BENCHMARK_PASSAGE)
    assert len(ladder_reference.SENTENCE_TEXT) == len(phrases)
    for index, text in ladder_reference.SENTENCE_TEXT.items():
        assert text == phrases[index]


def test_every_sentence_band_indexes_a_real_sentence() -> None:
    for index in ladder_reference.SENTENCE:
        assert index in ladder_reference.SENTENCE_TEXT


def test_every_word_band_indexes_a_word_of_the_passage() -> None:
    count = len(progress_view.BENCHMARK_PASSAGE.split())
    for index in ladder_reference.WORD:
        assert 0 <= index < count


# --- The bands are usable as an arrival bar ------------------------------------------------------


def test_a_reading_at_the_reference_mean_counts_as_arrived() -> None:
    for metric, band in ladder_reference.PARAGRAPH.items():
        assert band.contains(band.mean), metric


def test_a_reading_far_outside_does_not() -> None:
    band = ladder_reference.PARAGRAPH["npvi"]
    assert not band.contains(band.mean + band.sd * 5)
    assert not band.contains(band.mean - band.sd * 5)
