"""The pure rendering helpers: banding, escaping, diffing, and aggregation.

These are deliberately Streamlit-free functions in `app.py`, so the logic that decides what
the user sees is testable directly rather than through a headless app run.
"""

from __future__ import annotations

import pytest

import app as app_module
import utils
from utils import Band


def word(text: str, accuracy=None, error_type="None", error_source="azure",
         delivery=None, phonemes=None, syllables=None) -> dict:
    return {
        "word": text,
        "accuracy": accuracy,
        "error_type": error_type,
        "error_source": error_source,
        "delivery_error_types": delivery or [],
        "syllables": syllables or [],
        "phonemes": phonemes or [],
    }


# --- Banding -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "score, expected",
    [(0.0, Band.RED), (79.9, Band.RED), (80.0, Band.AMBER), (94.9, Band.AMBER),
     (95.0, Band.GREEN), (100.0, Band.GREEN)],
)
def test_word_banding_follows_the_documented_cut_points(score, expected) -> None:
    assert utils.word_band(score) is expected


def test_a_missing_score_bands_as_none_not_red() -> None:
    """An omitted word was never spoken; colouring it red claims a judgement Azure never made."""
    assert utils.word_band(None) is Band.NONE


def test_phonemes_are_cut_lower_than_words() -> None:
    assert utils.phoneme_band(70.0) is Band.AMBER
    assert utils.word_band(70.0) is Band.RED


# --- Colour-coded text ----------------------------------------------------------------------


def test_colour_coded_text_escapes_markup_from_the_reference() -> None:
    """The words originate in a free-text area and are interpolated straight into HTML."""
    rendered = app_module.colour_coded_html([word("<script>alert(1)</script>", 90.0)])
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered


def test_a_quote_cannot_break_out_of_the_hover_attribute() -> None:
    rendered = app_module.colour_coded_html([word('say "this"', 90.0)])
    assert 'title="' in rendered
    assert '&quot;' in rendered


def test_a_low_word_is_coloured_red_and_a_good_one_green() -> None:
    red = app_module.colour_coded_html([word("weather", 40.0)])
    green = app_module.colour_coded_html([word("weather", 99.0)])
    assert app_module.BAND_COLOURS[Band.RED] in red
    assert app_module.BAND_COLOURS[Band.GREEN] in green


def test_an_omission_is_struck_through_rather_than_scored() -> None:
    rendered = app_module.colour_coded_html(
        [word("thunder", None, error_type="Omission", error_source="local_diff")]
    )
    assert "line-through" in rendered
    assert "not spoken" in rendered


def test_the_hover_text_says_who_flagged_the_word() -> None:
    """Continuous mode ignores enableMiscue, so those miscues are ours, not Azure's."""
    text = app_module.hover_text(
        word("thunder", None, error_type="Omission", error_source="local_diff")
    )
    assert "local_diff" in text


def test_words_with_no_text_are_skipped_not_rendered_empty() -> None:
    assert app_module.colour_coded_html([word("")]).count("<span") == 0


# --- Reference versus heard -------------------------------------------------------------------


def test_an_identical_reading_is_all_same() -> None:
    pairs = app_module.reference_vs_heard("the weather is cold", "The weather is cold.")
    assert {tag for tag, _ in pairs} == {"same"}


def test_a_skipped_word_shows_as_missing() -> None:
    pairs = app_module.reference_vs_heard("the cold grey weather", "the grey weather")
    assert ("missing", "cold") in pairs


def test_an_added_word_shows_as_extra() -> None:
    pairs = app_module.reference_vs_heard("the grey weather", "the very grey weather")
    assert ("extra", "very") in pairs


def test_a_substitution_reports_both_sides() -> None:
    """Seeing 'thunder' became 'thounder' is the point; 'something changed' is not."""
    pairs = app_module.reference_vs_heard("brought thunder", "brought thounder")
    assert ("missing", "thunder") in pairs
    assert ("extra", "thounder") in pairs


def test_the_diff_escapes_markup_too() -> None:
    rendered = app_module.diff_html([("extra", "<b>")])
    assert "&lt;b&gt;" in rendered


# --- Phoneme pairs -----------------------------------------------------------------------------


def test_the_produced_phoneme_is_the_best_alternate_that_differs() -> None:
    """'/ð/ → /d/' is actionable; '/ð/ scored 80' is not. This is the tool's whole point."""
    subject = word("this", 97.0, phonemes=[
        {"phoneme": "ð", "score": 80.0, "is_mispronounced": False,
         "nbest": [{"phoneme": "d", "score": 100.0}, {"phoneme": "ð", "score": 92.0}]},
    ])
    assert app_module.phoneme_pairs(subject) == [("ð", "d", 80.0)]


def test_no_substitution_is_reported_when_the_target_wins() -> None:
    subject = word("this", 97.0, phonemes=[
        {"phoneme": "ð", "score": 99.0, "is_mispronounced": False,
         "nbest": [{"phoneme": "ð", "score": 100.0}, {"phoneme": "d", "score": 20.0}]},
    ])
    assert app_module.phoneme_pairs(subject) == [("ð", None, 99.0)]


def test_the_best_alternate_is_taken_by_score_not_by_position() -> None:
    subject = word("this", 50.0, phonemes=[
        {"phoneme": "θ", "score": 40.0, "is_mispronounced": True,
         "nbest": [{"phoneme": "s", "score": 30.0}, {"phoneme": "t", "score": 90.0}]},
    ])
    assert app_module.phoneme_pairs(subject)[0][1] == "t"


def test_a_word_with_no_phonemes_summarises_to_nothing() -> None:
    assert app_module.weakest_phoneme(word("thunder", None, error_type="Omission")) == ""


def test_the_weakest_phoneme_names_the_substitution() -> None:
    subject = word("thought", 55.0, phonemes=[
        {"phoneme": "θ", "score": 30.0, "is_mispronounced": True,
         "nbest": [{"phoneme": "t", "score": 95.0}]},
        {"phoneme": "ɔː", "score": 90.0, "is_mispronounced": False, "nbest": []},
    ])
    assert app_module.weakest_phoneme(subject) == "/θ/ → sounded like /t/"


# --- Delivery -------------------------------------------------------------------------------


def test_delivery_faults_are_aggregated_with_the_words_involved() -> None:
    """Synthetic payload: the captured fixture contains no delivery faults at all.

    Noted in memory-bank/progress.md — the real 12.8s recording came back clean on
    Break and Intonation, so this path can only be covered by a hand-built payload.
    """
    words = [
        word("the", 90.0, delivery=["UnexpectedBreak"]),
        word("weather", 88.0, delivery=["UnexpectedBreak", "Monotone"]),
        word("today", 95.0),
    ]
    summary = app_module.delivery_summary(words)
    assert summary["UnexpectedBreak"] == ["the", "weather"]
    assert summary["Monotone"] == ["weather"]
    assert "MissingBreak" not in summary


def test_a_clean_attempt_has_no_delivery_entries() -> None:
    assert app_module.delivery_summary([word("the", 99.0)]) == {}


def test_every_delivery_fault_has_a_plain_english_label() -> None:
    """The raw names are accurate but say nothing to someone trying to fix their delivery."""
    for fault in ("UnexpectedBreak", "MissingBreak", "Monotone"):
        assert fault in app_module.DELIVERY_LABELS


# --- Flagging and ordering ---------------------------------------------------------------------


def test_a_clean_high_scoring_word_is_not_flagged() -> None:
    assert not app_module.is_flagged(word("weather", 99.0))


def test_a_delivery_only_fault_still_flags_the_word() -> None:
    assert app_module.is_flagged(word("weather", 99.0, delivery=["Monotone"]))


def test_a_word_below_the_amber_cut_is_flagged() -> None:
    assert app_module.is_flagged(word("weather", utils.WORD_AMBER - 0.1))


def test_omissions_sort_ahead_of_merely_bad_scores() -> None:
    """A word never spoken is a worse outcome than one scored badly, and has no score."""
    omission = word("thunder", None, error_type="Omission")
    bad = word("weather", 12.0, error_type="Mispronunciation")
    assert sorted([bad, omission], key=app_module.severity_key)[0] is omission


def test_sorting_does_not_crash_on_a_missing_score() -> None:
    words = [word("a", None, error_type="Mispronunciation"), word("b", 50.0)]
    assert len(sorted(words, key=app_module.severity_key)) == 2


def test_a_phoneme_with_no_symbol_is_not_rendered_as_the_word_none() -> None:
    """Showing "/None/" invents a target sound in a tool whose job is naming sounds."""
    subject = word("odd", 40.0, phonemes=[
        {"phoneme": None, "score": 30.0, "is_mispronounced": True, "nbest": []},
    ])
    assert app_module.phoneme_pairs(subject) == [(None, None, 30.0)]
