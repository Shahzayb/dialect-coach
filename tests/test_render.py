"""The pure rendering helpers: banding, escaping, diffing, and aggregation.

These are deliberately Streamlit-free functions in `app.py`, so the logic that decides what
the user sees is testable directly rather than through a headless app run.
"""

from __future__ import annotations

import pytest

import app as app_module
import utils
from utils import AzureBand, Band


def word(
    text: str,
    accuracy=None,
    error_type="None",
    error_source="azure",
    delivery=None,
    phonemes=None,
    syllables=None,
) -> dict:
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
    [
        (0.0, Band.RED),
        (79.9, Band.RED),
        (80.0, Band.AMBER),
        (94.9, Band.AMBER),
        (95.0, Band.GREEN),
        (100.0, Band.GREEN),
    ],
)
def test_word_banding_follows_the_documented_cut_points(score, expected) -> None:
    assert utils.word_band(score) is expected


def test_a_missing_score_bands_as_none_not_red() -> None:
    """An omitted word was never spoken; colouring it red claims a judgement Azure never made."""
    assert utils.word_band(None) is Band.NONE


def test_phonemes_are_cut_lower_than_words() -> None:
    assert utils.phoneme_band(70.0) is Band.AMBER
    assert utils.word_band(70.0) is Band.RED


@pytest.mark.parametrize(
    "score, expected",
    [
        (0.0, AzureBand.LOW),
        (59.9, AzureBand.LOW),
        (60.0, AzureBand.FAIR),
        (79.9, AzureBand.FAIR),
        (80.0, AzureBand.GOOD),
        (89.9, AzureBand.GOOD),
        (90.0, AzureBand.EXCELLENT),
        (100.0, AzureBand.EXCELLENT),
    ],
)
def test_azure_score_banding_follows_azures_own_cut_points(score, expected) -> None:
    """0-59 / 60-79 / 80-89 / 90-100 — Azure's own convention, not this project's word/
    phoneme heuristics tested just above."""
    assert utils.azure_score_band(score) is expected


def test_a_missing_azure_score_bands_as_none_not_low() -> None:
    """A missing prosody score is not a bad score — it is no score at all."""
    assert utils.azure_score_band(None) is AzureBand.NONE


# --- Colour-coded text ----------------------------------------------------------------------


def test_colour_coded_text_escapes_markup_from_the_reference() -> None:
    """The words originate in a free-text area and are interpolated straight into HTML."""
    rendered = app_module.colour_coded_html([word("<script>alert(1)</script>", 90.0)])
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered


def test_a_quote_in_the_word_is_escaped_in_the_tooltip() -> None:
    rendered = app_module.colour_coded_html([word('say "this"', 90.0)])
    assert "&quot;" in rendered
    assert 'say "this"' not in rendered


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


def test_the_tooltip_says_who_flagged_the_word() -> None:
    """Continuous mode ignores enableMiscue, so those miscues are ours, not Azure's."""
    text = app_module.word_tooltip_html(
        word("thunder", None, error_type="Omission", error_source="local_diff")
    )
    assert "local_diff" in text


def test_words_with_no_text_are_skipped_not_rendered_empty() -> None:
    rendered = app_module.colour_coded_html([word("")])
    assert 'class="pa-word-wrap"' not in rendered


# --- Per-word tooltip (#13) -------------------------------------------------------------------


def test_the_tooltip_header_names_the_word_and_its_score() -> None:
    text = app_module.word_tooltip_html(word("long", 96.0))
    assert "long : 96" in text


def test_a_word_never_spoken_says_so_rather_than_a_score() -> None:
    text = app_module.word_tooltip_html(word("thunder", None, error_type="Omission"))
    assert "thunder : not spoken" in text


def test_the_tooltip_lays_out_phonemes_and_their_scores_as_two_rows() -> None:
    subject = word(
        "long",
        96.0,
        phonemes=[
            {"phoneme": "l", "score": 91.0, "is_mispronounced": False, "nbest": []},
            {"phoneme": "ɔ", "score": 100.0, "is_mispronounced": False, "nbest": []},
            {"phoneme": "ŋ", "score": 100.0, "is_mispronounced": False, "nbest": []},
        ],
    )
    text = app_module.word_tooltip_html(subject)
    # Symbol row first, score row underneath it — matching the issue-13 image's two stacked
    # rows, not the flagged-word card's inline "expected → produced" pairing.
    assert text.index(">l<") < text.index(">ɔ<") < text.index(">ŋ<") < text.index(">91<")


def test_a_missing_phoneme_score_shows_a_dash_not_zero() -> None:
    subject = word(
        "long",
        96.0,
        phonemes=[
            {"phoneme": "l", "score": None, "is_mispronounced": False, "nbest": []},
        ],
    )
    text = app_module.word_tooltip_html(subject)
    assert ">—<" in text
    assert ">0<" not in text


def test_tooltip_markup_escapes_the_word() -> None:
    rendered = app_module.word_tooltip_html(word("<script>alert(1)</script>", 90.0))
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered


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


def test_a_word_with_no_phonemes_summarises_to_nothing() -> None:
    assert app_module.weakest_phoneme(word("thunder", None, error_type="Omission")) == ""


def test_the_weakest_phoneme_names_the_substitution() -> None:
    subject = word(
        "thought",
        55.0,
        phonemes=[
            {
                "phoneme": "θ",
                "score": 30.0,
                "is_mispronounced": True,
                "nbest": [{"phoneme": "t", "score": 95.0}],
            },
            {"phoneme": "ɔː", "score": 90.0, "is_mispronounced": False, "nbest": []},
        ],
    )
    assert app_module.weakest_phoneme(subject) == "/θ/ → sounded like /t/"


# --- Delivery -------------------------------------------------------------------------------


def test_every_delivery_fault_has_a_plain_english_label() -> None:
    """The raw names are accurate but say nothing to someone trying to fix their delivery."""
    for fault in ("UnexpectedBreak", "MissingBreak", "Monotone"):
        assert fault in app_module.DELIVERY_LABELS


# --- Flagging and ordering ---------------------------------------------------------------------


def test_omissions_sort_ahead_of_merely_bad_scores() -> None:
    """A word never spoken is a worse outcome than one scored badly, and has no score."""
    omission = word("thunder", None, error_type="Omission")
    bad = word("weather", 12.0, error_type="Mispronunciation")
    assert sorted([bad, omission], key=app_module.severity_key)[0] is omission


def test_sorting_does_not_crash_on_a_missing_score() -> None:
    words = [word("a", None, error_type="Mispronunciation"), word("b", 50.0)]
    assert len(sorted(words, key=app_module.severity_key)) == 2
