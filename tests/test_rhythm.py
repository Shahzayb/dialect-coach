"""nPVI against the real captured payloads, and against durations built by hand.

Two kinds of test here and they do different jobs. The fixture tests pin the number this
project actually produces, so a change in the parser or in the segmentation policy shows up
as a moved value rather than as a quietly different chart. The hand-built ones pin the
formula itself against inputs whose answer can be worked out on paper — a fixture can only
ever say "still 55.85", never "55.85 is right".
"""

from __future__ import annotations

import json

import pytest

import rhythm
import speech_analyzer as sa
from utils import Mode

TICKS_PER_MS = sa.TICKS_PER_MS


@pytest.fixture
def drill_words(fixtures_dir) -> list[dict]:
    payload = json.loads((fixtures_dir / "sample_azure_response.json").read_text())
    reference = (
        "The weather this month has been rather unpredictable. Thursday brought thunder "
        "and thick clouds, while Wednesday stayed warm and clear."
    )
    _, _, words = sa.normalise([payload], reference, Mode.DRILL)
    return words


def word_of(*phonemes: tuple[str, float, float]) -> dict:
    """A normalised word carrying only what `rhythm` reads: symbol, offset and duration in ms."""
    return {
        "word": "synthetic",
        "phonemes": [
            {
                "phoneme": symbol,
                "offset_ticks": int(start * TICKS_PER_MS),
                "duration_ticks": int(length * TICKS_PER_MS),
            }
            for symbol, start, length in phonemes
        ],
    }


def stream(durations: list[float], *, symbol: str = "æ", separator: str = "t",
           separator_ms: float = 50.0, gap_before: dict[int, float] | None = None) -> dict:
    """A word of alternating vowels and consonants with the given vowel durations.

    Consonants sit between the vowels because that is what a real reading looks like and what
    keeps each vowel a separate interval. `gap_before` inserts extra silence before the nth
    vowel, for testing where runs break. Segments are laid out with the one-frame seam the
    real payloads carry, so the contiguity arithmetic under test is the arithmetic in use.
    """
    seam = sa.FRAME_TICKS / TICKS_PER_MS
    gaps = gap_before or {}
    phonemes: list[tuple[str, float, float]] = []
    cursor = 0.0
    for index, duration in enumerate(durations):
        if index:
            phonemes.append((separator, cursor, separator_ms))
            cursor += separator_ms + seam
        cursor += gaps.get(index, 0.0)
        phonemes.append((symbol, cursor, duration))
        cursor += duration + seam
    return word_of(*phonemes)


# --- The formula ------------------------------------------------------------------------------


def test_identical_durations_have_no_variability() -> None:
    """The floor. Every vowel the same length is perfectly syllable-timed: nPVI 0."""
    measured = rhythm.npvi([stream([100.0] * 30)])
    assert measured.npvi == pytest.approx(0.0)
    assert measured.pairs == 29


def test_alternating_durations_match_the_hand_computed_value() -> None:
    """Alternating 200 ms and 100 ms.

    Every pair differs by 100 with a mean of 150, so every term is 100/150 = 2/3 and the
    index is 100 * 2/3 = 66.67 regardless of how many pairs there are.
    """
    measured = rhythm.npvi([stream([200.0, 100.0] * 15)])
    assert measured.npvi == pytest.approx(200.0 / 3.0)


def test_the_index_does_not_move_with_speaking_rate() -> None:
    """Halving every duration leaves nPVI unchanged. That is the whole point of normalising.

    Without the per-pair mean in the denominator this would be a measure of how fast the
    passage was read, and a reading that sped up would look like a rhythm change.
    """
    durations = [180.0, 60.0, 240.0, 90.0, 150.0, 55.0, 210.0, 70.0] * 4
    slow = rhythm.npvi([stream(durations)])
    fast = rhythm.npvi([stream([d / 2 for d in durations])])
    assert slow.npvi == pytest.approx(fast.npvi)


# --- What counts as an interval -----------------------------------------------------------------


def test_adjacent_vowels_merge_into_one_interval() -> None:
    """Contiguous vocalic material is one interval, not one per phoneme.

    Real: the drill fixture runs "rather" /ɚ/ straight into "unpredictable" /ʌ/. Splitting
    that into two 50 ms neighbours would invent a perfectly matched pair and drag the index
    down; merged, it is one 100 ms interval.
    """
    seam = sa.FRAME_TICKS / TICKS_PER_MS
    merged = rhythm.vocalic_intervals(
        [word_of(("ɚ", 0.0, 50.0), ("ʌ", 50.0 + seam, 50.0))]
    )
    assert merged == [[100.0 + seam]]  # span, not 50 + 50


def test_a_consonant_between_two_vowels_keeps_them_apart() -> None:
    assert rhythm.vocalic_intervals([stream([80.0, 120.0])]) == [[80.0, 120.0]]


def test_consonants_are_not_measured() -> None:
    """Only vowels, diphthongs and r-coloured vowels. nPVI is a vocalic measure."""
    intervals = rhythm.vocalic_intervals(
        [word_of(("s", 0.0, 90.0), ("t", 100.0, 90.0), ("æ", 200.0, 70.0))]
    )
    assert intervals == [[70.0]]


def test_the_vowel_predicate_covers_every_symbol_in_every_fixture(fixtures_dir) -> None:
    """No phoneme Azure actually emits falls through `phoneme_reference` unclassified.

    A symbol it does not know is silently treated as a consonant and drops out of the
    measurement, so this asserts the coverage rather than the classification.
    """
    seen: set[str] = set()
    for name in ("sample_azure_response.json", "sample_azure_continuous.json",
                 "bad_delivery_capture.json", "synthetic_delivery_faults.json"):
        payload = json.loads((fixtures_dir / name).read_text())
        for utterance in payload if isinstance(payload, list) else [payload]:
            for word in utterance["NBest"][0].get("Words") or []:
                for phoneme in word.get("Phonemes") or []:
                    seen.add(phoneme["Phoneme"])
    assert seen
    unknown = [s for s in seen if phoneme_reference_lookup(s) is None]
    assert not unknown, f"unclassified phoneme symbols: {sorted(unknown)}"


def phoneme_reference_lookup(symbol: str):
    import phoneme_reference

    return phoneme_reference.lookup(symbol)


# --- Pauses -------------------------------------------------------------------------------------


def test_no_pair_spans_a_pause() -> None:
    """A pause ends the run. Pairing across it would measure the breath, not the rhythm."""
    words = [stream([100.0] * 6, gap_before={3: 500.0})]
    runs = rhythm.vocalic_intervals(words)
    assert [len(run) for run in runs] == [3, 3]

    measured = rhythm.npvi(words)
    assert measured.intervals == 6
    # Five adjacent differences exist in sequence; the one across the pause is not one of them.
    assert measured.pairs == 4
    assert measured.runs == 2


def test_a_gap_below_the_threshold_does_not_break_the_run() -> None:
    words = [stream([100.0] * 6, gap_before={3: rhythm.PAUSE_BREAK_MS - 10.0})]
    assert len(rhythm.vocalic_intervals(words)) == 1


def test_pairs_never_span_an_utterance_boundary(fixtures_dir) -> None:
    """The captured bad reading is seven utterances with real silence between them.

    Its ten runs are what the pause rule found; a naive read of the concatenated word list
    would produce one.
    """
    payloads = json.loads((fixtures_dir / "bad_delivery_capture.json").read_text())
    _, _, words = sa.normalise(payloads, "", Mode.PARAGRAPH)
    measured = rhythm.npvi(words)
    assert measured.runs > 1
    assert measured.pairs == measured.intervals - measured.runs


# --- Not enough to measure -----------------------------------------------------------------------


def test_too_little_speech_reports_no_number_but_still_counts() -> None:
    """None, not a number computed from four vowels.

    The counts are still reported: "we measured nothing" and "we measured 4 pairs" are
    different answers, and the UI decides what to say from the second one.
    """
    measured = rhythm.npvi([stream([100.0, 200.0, 150.0, 90.0, 130.0])])
    assert measured.npvi is None
    assert measured.measured is False
    assert measured.pairs == 4


def test_a_reading_with_no_words_at_all_is_not_a_crash() -> None:
    assert rhythm.npvi([]) == rhythm.Rhythm(npvi=None, pairs=0, intervals=0, runs=0)


def test_an_omitted_word_contributes_nothing() -> None:
    """Omissions carry no timing. They must drop out rather than land at offset zero."""
    words = [sa._omission("ghost"), stream([100.0] * 30), sa._omission("vanished")]
    assert rhythm.npvi(words).intervals == 30


def test_zero_length_intervals_cannot_divide_by_zero() -> None:
    words = [word_of(("æ", 0.0, 0.0), ("t", 10.0, 50.0), ("æ", 70.0, 0.0))]
    assert rhythm.npvi(words) == rhythm.Rhythm(npvi=None, pairs=0, intervals=0, runs=0)


# --- Against the real recording -------------------------------------------------------------------


def test_the_committed_fixture_scores_the_documented_value(drill_words: list[dict]) -> None:
    """55.85 over 25 pairs.

    Pinned deliberately. This is the number the module's docstring quotes as the anchor of its
    argument about which comparison is primary, and the number the other three segmentation
    variants there are measured against. If a parser change moves it, that argument needs
    rewriting and this test is where it says so.
    """
    measured = rhythm.npvi(drill_words)
    assert measured.npvi == pytest.approx(55.85, abs=0.01)
    assert measured.pairs == 25
    assert measured.intervals == 28


def test_the_same_recording_scores_the_same_through_either_mode(fixtures_dir) -> None:
    """The drill and continuous fixtures are the same reading captured twice.

    Close, not identical: they are separate API calls on separate recordings of the same
    passage. Well inside the 5.4-point spread the segmentation choices themselves produce,
    which is the point being made.
    """
    payloads = json.loads((fixtures_dir / "sample_azure_continuous.json").read_text())
    reference = (
        "The weather this month has been rather unpredictable. Thursday brought thunder "
        "and thick clouds, while Wednesday stayed warm and clear."
    )
    _, _, words = sa.normalise(payloads, reference, Mode.PARAGRAPH)
    assert rhythm.npvi(words).npvi == pytest.approx(55.26, abs=0.01)


# --- The baseline -----------------------------------------------------------------------------------


def test_a_missing_baseline_is_a_state_not_an_error(tmp_path) -> None:
    """`capture_baseline.py` spends real quota and is run by hand, so absent is normal."""
    assert rhythm.baseline(tmp_path / "not_captured.json") is None


def test_a_corrupt_baseline_is_treated_as_absent(tmp_path) -> None:
    """One unreadable file must not take the Progress tab down with it."""
    broken = tmp_path / "benchmark_tts_baseline.json"
    broken.write_text("{not json at all", encoding="utf-8")
    assert rhythm.baseline(broken) is None


def test_a_captured_baseline_is_measured_and_keeps_its_provenance(tmp_path, fixtures_dir) -> None:
    """The voice is carried because a different voice is a different baseline."""
    payload = json.loads((fixtures_dir / "sample_azure_response.json").read_text())
    path = tmp_path / "benchmark_tts_baseline.json"
    path.write_text(json.dumps({
        "voice": "en-US-BrianNeural",
        "captured_at": "2026-08-19T00:00:00Z",
        "reference_text": "",
        "payloads": [payload],
    }), encoding="utf-8")

    captured = rhythm.baseline(path)
    assert captured is not None
    assert captured.voice == "en-US-BrianNeural"
    assert captured.rhythm.npvi == pytest.approx(55.85, abs=0.01)
