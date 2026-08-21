"""The progress view: the benchmark passage, the frames, and the chart specs.

Pure — `progress_view` never imports Streamlit, so what the user sees is testable directly
rather than through a headless app run. The wiring into the page is covered in
`test_app.py` instead.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

import db
import progress_view as pv
import rhythm
import speech_analyzer as sa
import utils
from utils import Mode

FIXTURES = Path(__file__).parent / "fixtures"

# The reference the committed fixtures were captured against. Pairing a payload with a
# different reference text makes the Mode B miscue diff mark the whole passage omitted.
FIXTURE_REFERENCE = (
    "The weather this month has been rather unpredictable. Thursday brought Thunder and "
    "thick clouds, while Wednesday stayed warm and clear."
)


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.connect(":memory:")
    yield connection
    connection.close()


def add(connection: sqlite3.Connection, **overrides: Any) -> int:
    """One attempt, mirroring `test_db.add` so chronology and scores are controllable."""
    kwargs: dict[str, Any] = {
        "mode": Mode.DRILL,
        "reference_text": FIXTURE_REFERENCE,
        "recognised_text": FIXTURE_REFERENCE,
        "audio_seconds": 12.0,
        "audio_sha256": "abc123",
        "overall_scores": {
            "pron_score": 82.0,
            "accuracy": 85.0,
            "fluency": 90.0,
            "completeness": 100.0,
            "prosody": 78.0,
        },
        "azure_raw": {"RecognitionStatus": "Success", "NBest": [{"PronunciationAssessment": {}}]},
    }
    kwargs.update(overrides)
    return db.record_attempt(connection, **kwargs)


def rows(*records: dict) -> list[dict]:
    """Score rows in the shape `attempt_series` returns, with sane defaults."""
    out = []
    for index, record in enumerate(records, start=1):
        base = {
            "id": index,
            "created_at": f"2026-07-{index:02d}T08:00:00Z",
            "mode": "paragraph",
            "reference_text": pv.BENCHMARK_PASSAGE,
            "pron_score": 80.0,
            "accuracy": 85.0,
            "fluency": 78.0,
            "prosody": 70.0,
        }
        base.update(record)
        out.append(base)
    return out


# --- The benchmark passage ------------------------------------------------------------------
# The passage is frozen: the series is identified by matching it, so editing a word starts a
# new series. These tests are the guard on that, and on the coverage claim in the plan file.


def test_every_token_the_coverage_table_claims_is_really_in_the_passage() -> None:
    """The justification cannot drift away from the text it justifies.

    `BENCHMARK_COVERAGE` is the passage's whole reason for existing — the claim that one
    read serves both the trajectory chart and a later vowel-measurement instrument. A token
    listed there but absent from the passage would make that claim quietly false.
    """
    present = set(utils.normalise_words(pv.BENCHMARK_PASSAGE))
    missing = {
        symbol: [token for token in tokens if token not in present]
        for symbol, tokens in pv.BENCHMARK_COVERAGE.items()
    }
    assert {k: v for k, v in missing.items() if v} == {}


def test_the_passage_covers_the_consonants_and_the_whole_vowel_inventory() -> None:
    """Both instruments, in one read. The vowel list is `phoneme_reference`'s own."""
    covered = set(pv.BENCHMARK_COVERAGE)
    consonants = {
        "θ",
        "ð",
        "v",
        "w",
        "t",
        "d",
        "ʃ",
        "s",
        "z",
        "dʒ",
        "l (dark, coda)",
        "l (clear, onset)",
        "final clusters",
    }
    vowels = {
        "æ",
        "ɛ",
        "ɪ",
        "i",
        "ɑ",
        "ʌ",
        "ɝ",
        "ə",
        "ʊ",
        "u",
        "ɔ",
        "ɚ",
        "eɪ",
        "aɪ",
        "oʊ",
        "aʊ",
        "ɔɪ",
        "ɑɹ",
        "ɔɹ",
        "ɛɹ",
        "ɪɹ",
        "ʊɹ",
    }
    assert consonants <= covered
    assert vowels <= covered
    # FACE and GOAT are called out in the brief specifically; they are not scraping by.
    assert len(pv.BENCHMARK_COVERAGE["eɪ"]) >= 5
    assert len(pv.BENCHMARK_COVERAGE["oʊ"]) >= 5


def test_the_passage_is_one_minute_and_a_half_of_reading() -> None:
    """Long enough for the vowel tokens, short enough to stay a single sitting."""
    assert 170 <= len(utils.normalise_words(pv.BENCHMARK_PASSAGE)) <= 210


def test_the_passage_carries_nothing_that_breaks_word_alignment() -> None:
    """No digits (Azure normalises "33" and "thirty-three" differently) and no hyphens."""
    assert not any(character.isdigit() for character in pv.BENCHMARK_PASSAGE)
    assert "-" not in pv.BENCHMARK_PASSAGE


def test_a_benchmark_read_is_recognised_however_it_was_typed() -> None:
    assert pv.is_benchmark(pv.BENCHMARK_PASSAGE)
    assert pv.is_benchmark("  " + pv.BENCHMARK_PASSAGE.upper() + "  ")
    assert pv.is_benchmark(pv.BENCHMARK_PASSAGE.replace("\n\n", " "))


def test_anything_else_is_free_practice() -> None:
    assert not pv.is_benchmark("These three brothers thought the weather was worth it.")
    assert not pv.is_benchmark(None)
    assert not pv.is_benchmark("")
    # One word short is not the passage. The series would silently split otherwise.
    assert not pv.is_benchmark(pv.BENCHMARK_PASSAGE.replace("Each morning ", "", 1))


# --- The score frame ------------------------------------------------------------------------


def test_a_missing_prosody_score_is_a_gap_and_never_a_zero() -> None:
    """NULL prosody and a prosody of 0.0 are very different things.

    Azure returns no prosody score on some attempts and `db` stores NULL, never 0.0. Plotting
    that as zero would draw a collapse the speaker never had.
    """
    frame = pv.score_frame(rows({"prosody": None}))
    assert "Prosody" not in set(frame["metric"])
    assert len(frame) == 3
    assert 0.0 not in set(frame["value"])


def test_a_prosody_of_zero_is_still_plotted() -> None:
    """The other half of the same rule: a real zero is data, not a missing value."""
    frame = pv.score_frame(rows({"prosody": 0.0}))
    assert list(frame[frame["metric"] == "Prosody"]["value"]) == [0.0]


def test_the_benchmark_and_free_practice_are_different_series() -> None:
    frame = pv.score_frame(
        rows(
            {"reference_text": pv.BENCHMARK_PASSAGE},
            {"reference_text": "Something I made up this morning.", "mode": "drill"},
        )
    )
    assert set(frame[frame["attempt_id"] == 1]["series"]) == {pv.BENCHMARK_SERIES}
    assert set(frame[frame["attempt_id"] == 2]["series"]) == {pv.FREE_SERIES}


def test_the_two_modes_are_labelled_apart() -> None:
    """Mode A and Mode B numbers are not comparable, so they never carry one label."""
    frame = pv.score_frame(rows({"mode": "drill"}, {"mode": "paragraph"}))
    assert set(frame["mode"]) == {"Drill (Mode A)", "Paragraph (Mode B)"}


def test_the_tooltip_names_the_benchmark_and_truncates_a_free_text() -> None:
    frame = pv.score_frame(
        rows(
            {"reference_text": pv.BENCHMARK_PASSAGE},
            {"reference_text": "A free practice paragraph that runs on well past the label cut."},
        )
    )
    assert set(frame[frame["attempt_id"] == 1]["label"]) == {pv.BENCHMARK_TITLE}
    label = list(frame[frame["attempt_id"] == 2]["label"])[0]
    assert label.endswith("…") and len(label) < 60


def test_an_unparseable_timestamp_drops_the_attempt_rather_than_the_page() -> None:
    frame = pv.score_frame(rows({"created_at": "not a timestamp"}))
    assert frame.empty


def test_an_empty_history_still_has_the_columns() -> None:
    """`score_chart` is handed this frame, so it must be shaped even when it is empty."""
    frame = pv.score_frame([])
    assert frame.empty
    assert list(frame.columns) == list(pv.FRAME_COLUMNS)


def test_offline_replays_never_reach_the_frame(conn: sqlite3.Connection) -> None:
    """A fixture replay scores the same every time; thirty identical points is not progress."""
    add(conn, audio_sha256="real", created_at="2026-07-01T08:00:00Z")
    add(conn, audio_sha256="replay", offline=True, created_at="2026-07-02T08:00:00Z")
    assert len(db.attempt_series(conn)) == 1
    assert len(db.attempt_payloads(conn)) == 1


def test_the_series_is_ordered_by_time_not_by_insertion(conn: sqlite3.Connection) -> None:
    """`record_attempt` takes an explicit `created_at`, so id order is not time order."""
    add(conn, audio_sha256="b", created_at="2026-07-09T08:00:00Z")
    add(conn, audio_sha256="a", created_at="2026-07-02T08:00:00Z")
    assert [r["created_at"] for r in db.attempt_series(conn)] == [
        "2026-07-02T08:00:00Z",
        "2026-07-09T08:00:00Z",
    ]


# --- The chart spec -------------------------------------------------------------------------
# Mode A and Mode B must not share a line. Asserted against the spec itself rather than left
# as a comment, because a later encoding change could reintroduce it silently.


def spec_layers(frame: pd.DataFrame) -> list[dict[str, Any]]:
    layers: list[dict[str, Any]] = pv.score_chart(frame).to_dict()["spec"]["layer"]
    return layers


def test_free_practice_is_never_drawn_as_a_line() -> None:
    """Two line layers now: the cold benchmark and the shadowed one, and nothing else.

    Both are single-mode by construction (a 196-word passage is only ever read in paragraph
    mode), which is what the rule actually requires — see the structural half below. Free
    practice, the one series that spans two modes, still gets points and only points.
    """
    marks = [layer["mark"]["type"] for layer in spec_layers(pv.score_frame(rows({})))]
    assert marks.count("line") == 2
    assert "point" in marks


def test_the_shadowed_series_is_its_own_line() -> None:
    """A shadowed read must never join the cold trajectory — it is what that is measured against."""
    spec = json.dumps(spec_layers(pv.score_frame(rows({}))))
    assert pv.SHADOWED_SERIES in spec
    assert pv.BENCHMARK_SERIES in spec


def test_no_line_layer_encodes_the_mode() -> None:
    """The structural half of the rule: a line that grouped by mode could join A to B."""
    for layer in spec_layers(pv.score_frame(rows({}))):
        if layer["mark"]["type"] == "line":
            assert "mode" not in json.dumps(layer["encoding"])


def test_free_practice_is_points_shaped_by_mode() -> None:
    for layer in spec_layers(pv.score_frame(rows({}))):
        if layer["mark"]["type"] == "point":
            assert layer["encoding"]["shape"]["field"] == "mode"
            break
    else:
        pytest.fail("no point layer in the chart")


def test_the_score_axis_is_pinned_to_the_full_range() -> None:
    """An auto-scaled axis magnifies noise into a trend — the exact failure being guarded."""
    for layer in spec_layers(pv.score_frame(rows({}))):
        assert layer["encoding"]["y"]["scale"]["domain"] == [0, 100]


def test_the_four_metrics_are_faceted_apart() -> None:
    chart = pv.score_chart(pv.score_frame(rows({}))).to_dict()
    assert chart["facet"]["row"]["field"] == "metric"
    assert set(pv.METRIC_ORDER) == {"Pronunciation", "Accuracy", "Fluency", "Prosody"}


def test_an_empty_history_still_produces_a_chart() -> None:
    """The empty state is a message in `app.py`, but building the spec must not raise."""
    assert pv.score_chart(pv.score_frame([])).to_dict()


# --- Days since the benchmark ---------------------------------------------------------------


def test_days_since_the_benchmark_counts_only_benchmark_reads() -> None:
    now = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)
    history = rows(
        {"created_at": "2026-07-10T08:00:00Z", "reference_text": pv.BENCHMARK_PASSAGE},
        {"created_at": "2026-07-19T08:00:00Z", "reference_text": "free practice text"},
    )
    assert pv.days_since_benchmark(history, now=now) == 10


def test_days_since_the_benchmark_is_none_when_it_has_never_been_read() -> None:
    assert pv.days_since_benchmark(rows({"reference_text": "free practice"})) is None


# --- Re-parsing the stored payloads ---------------------------------------------------------


def test_both_stored_shapes_re_parse(conn: sqlite3.Connection) -> None:
    """A drill stores a JSON object, a paragraph a JSON array. Both have to come back."""
    drill = json.loads((FIXTURES / "sample_azure_response.json").read_text())
    paragraph = json.loads((FIXTURES / "sample_azure_continuous.json").read_text())
    add(conn, mode=Mode.DRILL, azure_raw=drill, audio_sha256="d", created_at="2026-07-01T08:00:00Z")
    add(
        conn,
        mode=Mode.PARAGRAPH,
        azure_raw=paragraph,
        audio_sha256="p",
        created_at="2026-07-02T08:00:00Z",
    )

    parsed = pv.parse_attempts(db.attempt_payloads(conn))
    assert [p.mode for p in parsed] == [Mode.DRILL, Mode.PARAGRAPH]
    assert all(p.words for p in parsed)
    assert not any(p.benchmark for p in parsed)


def test_a_corrupt_payload_is_skipped_rather_than_blanking_the_view(
    conn: sqlite3.Connection,
) -> None:
    """One unreadable blob on disk must cost its own row, not the whole view."""
    drill = json.loads((FIXTURES / "sample_azure_response.json").read_text())
    bad = add(conn, audio_sha256="bad", created_at="2026-07-01T08:00:00Z")
    add(conn, azure_raw=drill, audio_sha256="good", created_at="2026-07-02T08:00:00Z")
    # Truncated on the way to disk — the one shape `record_attempt` cannot produce itself.
    conn.execute(
        "UPDATE attempts SET azure_raw_json = ? WHERE id = ?", ('{"NBest": [{"Words": [', bad)
    )

    parsed = pv.parse_attempts(db.attempt_payloads(conn))
    assert [p.attempt_id for p in parsed] == [2]


def test_a_benchmark_read_is_marked_as_one_when_it_is_re_parsed(
    conn: sqlite3.Connection,
) -> None:
    drill = json.loads((FIXTURES / "sample_azure_response.json").read_text())
    add(conn, reference_text=pv.BENCHMARK_PASSAGE, azure_raw=drill)
    assert pv.parse_attempts(db.attempt_payloads(conn))[0].benchmark


# --- What keeps going wrong -----------------------------------------------------------------
# Against the real captured payload rather than a hand-built one, per the standing preference:
# the Azure response differs from its documentation in ways that fail silently.


@pytest.fixture
def real_drill(conn: sqlite3.Connection) -> list[pv.ParsedAttempt]:
    payload = json.loads((FIXTURES / "sample_azure_response.json").read_text())
    add(conn, mode=Mode.DRILL, azure_raw=payload)
    return pv.parse_attempts(db.attempt_payloads(conn))


def test_the_flagship_substitution_surfaces_from_the_real_capture(real_drill) -> None:
    """`/θ/ → /s/` on "thursday" is the fixture's headline fault everywhere else in the app."""
    frame = pv.flagged_phonemes(real_drill)
    assert "/θ/ → /s/" in set(frame["label"])
    row = frame[frame["label"] == "/θ/ → /s/"].iloc[0]
    assert row["expected"] == "θ" and row["produced"] == "s"


def test_the_words_the_app_flags_are_the_words_that_are_counted(real_drill) -> None:
    """One predicate for both, so this view cannot disagree with the word cards."""
    expected = {str(w["word"]).lower() for w in real_drill[0].words if sa.is_flagged(w)}
    assert set(pv.flagged_words(real_drill)["word"]) == expected


def test_a_weak_phoneme_with_no_alternate_is_kept_as_its_own_bucket() -> None:
    """What final-cluster simplification looks like: weakened, not swapped for something."""
    attempt = pv.ParsedAttempt(
        attempt_id=1,
        created_at="2026-07-01T08:00:00Z",
        mode=Mode.DRILL,
        reference_text="asked",
        benchmark=False,
        words=[
            {
                "word": "asked",
                "accuracy": 55.0,
                "error_type": "Mispronunciation",
                "delivery_error_types": [],
                "syllables": [],
                "phonemes": [{"phoneme": "t", "score": 30.0, "nbest": []}],
            }
        ],
    )
    frame = pv.flagged_phonemes([attempt])
    assert list(frame["label"]) == [f"/t/ → {pv.UNCLEAR}"]


def test_a_weak_phoneme_in_an_unflagged_word_is_not_counted() -> None:
    """The ranking follows `is_flagged`; a clean word's phoneme scores are not faults."""
    attempt = pv.ParsedAttempt(
        attempt_id=1,
        created_at="2026-07-01T08:00:00Z",
        mode=Mode.DRILL,
        reference_text="fine",
        benchmark=False,
        words=[
            {
                "word": "fine",
                "accuracy": 100.0,
                "error_type": "None",
                "delivery_error_types": [],
                "syllables": [],
                "phonemes": [{"phoneme": "f", "score": 10.0, "nbest": []}],
            }
        ],
    )
    assert pv.flagged_phonemes([attempt]).empty


def flagged(word: str, phoneme: str, produced: str) -> dict:
    return {
        "word": word,
        "accuracy": 50.0,
        "error_type": "Mispronunciation",
        "delivery_error_types": [],
        "syllables": [],
        "phonemes": [
            {"phoneme": phoneme, "score": 40.0, "nbest": [{"phoneme": produced, "score": 95.0}]}
        ],
    }


def attempt(index: int, words: list[dict], *, benchmark: bool = False) -> pv.ParsedAttempt:
    return pv.ParsedAttempt(
        attempt_id=index,
        created_at=f"2026-07-{index:02d}T08:00:00Z",
        mode=Mode.DRILL,
        reference_text=pv.BENCHMARK_PASSAGE if benchmark else "free",
        benchmark=benchmark,
        words=words,
    )


def test_recurring_across_attempts_outranks_repeating_within_one() -> None:
    """ "Flagged most often" is a question about sessions, not about token counts.

    Otherwise one long paragraph that repeats a word would head the list ahead of a fault
    that has come back every single time.
    """
    parsed = [
        attempt(1, [flagged("thin", "θ", "t")]),
        attempt(2, [flagged("thin", "θ", "t")]),
        attempt(
            3, [flagged("very", "v", "w"), flagged("vowel", "v", "w"), flagged("value", "v", "w")]
        ),
    ]
    frame = pv.flagged_phonemes(parsed)
    assert list(frame["label"])[0] == "/θ/ → /t/"
    assert list(frame[frame["label"] == "/θ/ → /t/"]["attempts"]) == [2]
    assert list(frame[frame["label"] == "/v/ → /w/"]["tokens"]) == [3]


def test_the_benchmark_share_is_carried_alongside_the_total() -> None:
    """On the fixed passage the count is comparable read to read; on free text it is not."""
    parsed = [
        attempt(1, [flagged("thin", "θ", "t")], benchmark=True),
        attempt(2, [flagged("thin", "θ", "t")]),
    ]
    row = pv.flagged_phonemes(parsed).iloc[0]
    assert row["attempts"] == 2 and row["benchmark_attempts"] == 1
    assert pv.flagged_words(parsed).iloc[0]["benchmark_attempts"] == 1


def test_the_rankings_survive_an_empty_history() -> None:
    assert pv.flagged_phonemes([]).empty
    assert pv.flagged_words([]).empty
    assert pv.phoneme_chart(pv.flagged_phonemes([])).to_dict()
    assert pv.word_chart(pv.flagged_words([])).to_dict()


# --- The TTS baseline capture is not a reading --------------------------------------------------


def _capture_row(attempt_id: int = 99) -> dict:
    """The attempts row `scripts/capture_baseline.py` writes: real spend, synthetic voice."""
    return {
        "id": attempt_id,
        "created_at": "2026-08-19T14:24:09Z",
        "mode": Mode.PARAGRAPH.value,
        "reference_text": f"{rhythm.BASELINE_CAPTURE_MARKER} en-US-BrianNeural",
        "recognised_text": "each morning i read these same words out loud",
        "pron_score": 92.0,
        "accuracy": 93.0,
        "fluency": 90.0,
        "completeness": 98.0,
        "prosody": 89.5,
        "azure_raw_json": "{}",
    }


def test_the_baseline_capture_never_reaches_the_trajectory() -> None:
    """It scores 92 and would sit at the top of the chart. Nobody spoke it."""
    frame = pv.score_frame([_capture_row()])
    assert len(frame) == 0


def test_the_baseline_capture_does_not_count_as_reading_the_benchmark() -> None:
    """Otherwise "benchmark last read today" becomes true of a synthesiser."""
    assert pv.days_since_benchmark([_capture_row()]) is None


def test_the_baseline_capture_is_left_out_of_what_keeps_going_wrong() -> None:
    """The voice's own weak sounds are not the speaker's practice material."""
    assert pv.parse_attempts([_capture_row()]) == []


def test_spoken_attempts_keeps_everything_else() -> None:
    """The filter is narrow: only the marked capture row goes."""
    real = _capture_row(1) | {"reference_text": pv.BENCHMARK_PASSAGE}
    kept = pv.spoken_attempts([real, _capture_row(2)])
    assert [row["id"] for row in kept] == [1]


# --- Rhythm over time ---------------------------------------------------------------------


def _benchmark_attempt(attempt_id: int, when: str, words: list[dict]) -> pv.ParsedAttempt:
    return pv.ParsedAttempt(
        attempt_id=attempt_id,
        created_at=when,
        mode=Mode.PARAGRAPH,
        reference_text=pv.BENCHMARK_PASSAGE,
        benchmark=True,
        words=words,
    )


def _fixture_words() -> list[dict]:
    payload = json.loads((FIXTURES / "sample_azure_response.json").read_text())
    _, _, words = sa.normalise([payload], "", Mode.DRILL)
    return words


def test_rhythm_frame_plots_benchmark_reads(fixtures_dir) -> None:
    words = _fixture_words()
    frame = pv.rhythm_frame(
        [
            _benchmark_attempt(1, "2026-08-01T09:00:00Z", words),
            _benchmark_attempt(2, "2026-08-08T09:00:00Z", words),
        ]
    )
    assert list(frame.columns) == list(pv.RHYTHM_COLUMNS)
    assert len(frame) == 2
    assert frame["npvi"].iloc[0] == pytest.approx(55.85, abs=0.01)


def test_free_practice_is_left_out_of_the_rhythm_chart() -> None:
    """nPVI moves with the text. A chart mixing passages would show which was harder."""
    free = pv.ParsedAttempt(
        attempt_id=3,
        created_at="2026-08-01T09:00:00Z",
        mode=Mode.PARAGRAPH,
        reference_text="something else entirely",
        benchmark=False,
        words=_fixture_words(),
    )
    assert len(pv.rhythm_frame([free])) == 0


def test_an_unmeasurable_attempt_produces_no_row_rather_than_a_zero() -> None:
    """Same rule as score_frame: a gap is honest, a zero invents even syllables."""
    sparse = _benchmark_attempt(4, "2026-08-01T09:00:00Z", [])
    assert len(pv.rhythm_frame([sparse])) == 0


def test_an_empty_rhythm_frame_still_has_its_columns() -> None:
    frame = pv.rhythm_frame([])
    assert list(frame.columns) == list(pv.RHYTHM_COLUMNS)


def test_the_rhythm_chart_draws_the_baseline_as_a_rule_not_a_series() -> None:
    """The baseline is one capture, not a history. A line over time would imply otherwise."""
    frame = pv.rhythm_frame([_benchmark_attempt(1, "2026-08-01T09:00:00Z", _fixture_words())])
    spec = pv.rhythm_chart(frame, baseline=58.4).to_dict()
    marks = [layer.get("mark") for layer in spec["layer"]]
    kinds = {m if isinstance(m, str) else m.get("type") for m in marks}
    assert "rule" in kinds
    assert "line" in kinds


def test_the_rhythm_chart_omits_the_rule_when_there_is_no_baseline() -> None:
    frame = pv.rhythm_frame([_benchmark_attempt(1, "2026-08-01T09:00:00Z", _fixture_words())])
    spec = pv.rhythm_chart(frame, baseline=None).to_dict()
    assert "layer" not in spec


def test_the_rhythm_axis_is_not_pinned_to_zero() -> None:
    """nPVI is not a 0-100 score. A zero-based axis flattens the only thing worth seeing."""
    frame = pv.rhythm_frame([_benchmark_attempt(1, "2026-08-01T09:00:00Z", _fixture_words())])
    spec = pv.rhythm_chart(frame, baseline=None).to_dict()
    assert spec["encoding"]["y"]["scale"]["zero"] is False


# --- Weak syllables, the stress evidence ------------------------------------------------------


def _syllable_attempt(word: str, syllables, attempt_id: int = 1):
    return pv.ParsedAttempt(
        attempt_id=attempt_id,
        created_at="2026-08-10T00:00:00Z",
        mode=Mode.PARAGRAPH,
        reference_text="anything",
        benchmark=False,
        words=[
            {
                "word": word,
                "accuracy": 70.0,
                "error_type": "Mispronunciation",
                "syllables": [{"syllable": s, "score": score} for s, score in syllables],
                "phonemes": [],
            }
        ],
    )


def test_a_single_syllable_word_has_no_stress_to_misplace() -> None:
    frame = pv.weak_syllables([_syllable_attempt("think", [("θɪŋk", 20.0)])])
    assert not len(frame)


def test_a_weak_syllable_in_a_multi_syllable_word_is_recorded() -> None:
    frame = pv.weak_syllables(
        [_syllable_attempt("weather", [("wɛð", 100.0), ("ɚ", 30.0)], attempt_id=n) for n in (1, 2)]
    )
    assert list(frame["word"]) == ["weather"]
    assert list(frame["syllable"]) == ["ɚ"]
    assert list(frame["attempts"]) == [2]


def test_a_word_whose_syllables_all_score_well_is_left_alone() -> None:
    frame = pv.weak_syllables([_syllable_attempt("weather", [("wɛð", 100.0), ("ɚ", 90.0)])])
    assert not len(frame)


def test_the_weak_syllable_cut_is_the_one_the_coaching_report_uses() -> None:
    """One definition, so the queue's evidence and the report on screen cannot disagree."""
    import fallback_coach

    just_under = fallback_coach.SYLLABLE_RED - 0.1
    frame = pv.weak_syllables([_syllable_attempt("weather", [("wɛð", 100.0), ("ɚ", just_under)])])
    assert len(frame) == 1


# --- Perception blocks --------------------------------------------------------------------------


def _trial(
    block_id: str,
    correct: bool,
    *,
    alternatives: int = 2,
    novel: bool = True,
    when: str = "2026-08-15T00:00:00Z",
    review: bool = False,
):
    return {
        "block_id": block_id,
        "created_at": when,
        "item": "/θ/ → /s/",
        "word": "think",
        "voice": "v",
        "novel": int(novel),
        "alternatives": alternatives,
        "answered": "think",
        "correct": int(correct),
        "review": int(review),
    }


def test_an_empty_history_gives_an_empty_frame_with_its_columns() -> None:
    frame = pv.perception_frame([])
    assert not len(frame)
    assert list(frame.columns) == list(pv.PERCEPTION_COLUMNS)


def test_a_block_becomes_one_row_carrying_its_chance_floor() -> None:
    trials = [_trial("b1", True)] * 13 + [_trial("b1", False)] * 7
    frame = pv.perception_frame(trials)
    assert len(frame) == 1
    assert frame["accuracy"].iloc[0] == pytest.approx(65.0)
    assert frame["chance"].iloc[0] == pytest.approx(50.0)
    assert frame["total"].iloc[0] == 20


def test_the_chance_floor_follows_the_stored_alternatives() -> None:
    """A four-way task keeps reporting 25% rather than inheriting a hardcoded 50."""
    frame = pv.perception_frame([_trial("b1", True, alternatives=4)])
    assert frame["chance"].iloc[0] == pytest.approx(25.0)


def test_blocks_are_separate_rows() -> None:
    frame = pv.perception_frame(
        [
            _trial("b1", True, when="2026-08-10T00:00:00Z"),
            _trial("b2", False, when="2026-08-11T00:00:00Z"),
        ]
    )
    assert len(frame) == 2


def test_an_incomplete_block_is_still_drawn() -> None:
    """The exclusion belongs to the verdict, not to the picture."""
    frame = pv.perception_frame([_trial("b1", True)] * 3)
    assert frame["total"].iloc[0] == 3


def test_the_chart_draws_the_chance_rule() -> None:
    frame = pv.perception_frame([_trial("b1", True)] * 4)
    spec = pv.perception_chart(frame).to_dict()
    marks = [layer.get("mark") for layer in spec["layer"]]
    rules = [m for m in marks if isinstance(m, dict) and m.get("type") == "rule"]
    assert rules, "the chance floor must be drawn, not only tabulated"
    assert rules[0].get("strokeDash")
    encodings = [layer["encoding"]["y"]["field"] for layer in spec["layer"]]
    assert "chance" in encodings


def test_the_accuracy_axis_is_pinned_so_noise_cannot_look_like_a_trend() -> None:
    frame = pv.perception_frame([_trial("b1", True)] * 4)
    spec = pv.perception_chart(frame).to_dict()
    assert spec["layer"][0]["encoding"]["y"]["scale"]["domain"] == [0, 100]


# --- Cold against shadowed ----------------------------------------------------------------------
# The correctness crux of the shadowing chunk. `is_benchmark` identifies a benchmark read by
# matching its reference text, so a shadowed read of the benchmark passage would otherwise land
# on the headline trajectory as though it were cold — inflating the exact line the whole
# benchmark design exists to keep honest.


def test_a_row_without_the_column_reads_as_cold() -> None:
    """Rows reach these readers from several places. An unknown read belongs on the cold
    trajectory, where it is visible and can be questioned, not hidden in the assisted series."""
    assert not pv.is_shadowed({"id": 1})


def test_cold_attempts_drops_the_shadowed_ones() -> None:
    kept = pv.cold_attempts(rows({"shadowed": 0}, {"shadowed": 1}))
    assert [row["id"] for row in kept] == [1]


def test_cold_attempts_still_drops_the_tts_baseline_capture() -> None:
    """The two exclusions compose rather than replacing one another."""
    kept = pv.cold_attempts(
        rows(
            {"shadowed": 0},
            {"reference_text": f"{rhythm.BASELINE_CAPTURE_MARKER} en-US-BrianNeural"},
        )
    )
    assert [row["id"] for row in kept] == [1]


def test_a_shadowed_benchmark_read_is_not_on_the_headline_series() -> None:
    frame = pv.score_frame(rows({"shadowed": 0}, {"shadowed": 1}))
    headline = frame[frame["series"] == pv.BENCHMARK_SERIES]
    assert set(headline["attempt_id"]) == {1}
    assisted = frame[frame["series"] == pv.SHADOWED_SERIES]
    assert set(assisted["attempt_id"]) == {2}


def test_a_shadowed_free_practice_read_stays_in_the_cloud() -> None:
    """The cloud is joined by nothing, so there is no line for it to inflate — the tooltip
    carries the fact instead of a fourth legend entry."""
    frame = pv.score_frame(rows({"reference_text": "something else", "shadowed": 1}))
    assert set(frame["series"]) == {pv.FREE_SERIES}
    assert bool(frame["shadowed"].iloc[0])


def test_the_last_benchmark_read_means_the_last_cold_one() -> None:
    """A week of shadowing must not report the benchmark as freshly read while the
    unassisted series quietly went stale."""
    now = datetime(2026, 7, 10, 8, 0, 0, tzinfo=UTC)
    both = rows(
        {"created_at": "2026-07-01T08:00:00Z", "shadowed": 0},
        {"created_at": "2026-07-09T08:00:00Z", "shadowed": 1},
    )
    assert pv.days_since_benchmark(both, now=now) == 9


# --- The pairing ---------------------------------------------------------------------------------


def shadow_rows(*records: dict) -> list[dict]:
    """Attempts on the benchmark passage with controllable fluency, prosody and provenance."""
    out = []
    for index, record in enumerate(records, start=1):
        base = {
            "id": index,
            "created_at": f"2026-07-{index:02d}T08:00:00Z",
            "mode": "paragraph",
            "reference_text": pv.BENCHMARK_PASSAGE,
            "pron_score": 80.0,
            "accuracy": 85.0,
            "fluency": 70.0,
            "prosody": 60.0,
            "shadowed": 0,
        }
        base.update(record)
        out.append(base)
    return out


def test_a_shadowed_read_is_paired_with_a_cold_read_of_the_same_passage() -> None:
    frame = pv.shadow_pairs(
        shadow_rows(
            {"fluency": 70.0, "prosody": 60.0},
            {"fluency": 78.0, "prosody": 69.0, "shadowed": 1},
        )
    )
    deltas = dict(zip(frame["metric"], frame["delta"]))
    assert deltas == {"Fluency": pytest.approx(8.0), "Prosody": pytest.approx(9.0)}


def test_only_fluency_and_prosody_are_compared() -> None:
    """Shadowing trains delivery, not articulation. A large accuracy delta would more likely
    be the headphone caveat showing up in the data than a result."""
    frame = pv.shadow_pairs(shadow_rows({}, {"shadowed": 1}))
    assert set(frame["metric"]) == {"Fluency", "Prosody"}


def test_the_pair_is_the_nearest_cold_read_either_side() -> None:
    """Requiring the cold read to come first would throw away every pair from the first
    weeks — exactly the weeks the narrowing question is about."""
    frame = pv.shadow_pairs(
        shadow_rows(
            {"created_at": "2026-07-01T08:00:00Z", "fluency": 50.0},
            {"created_at": "2026-07-10T08:00:00Z", "fluency": 75.0, "shadowed": 1},
            {"created_at": "2026-07-11T08:00:00Z", "fluency": 70.0},
        )
    )
    fluency = frame[frame["metric"] == "Fluency"].iloc[0]
    assert fluency["cold_id"] == 3
    assert fluency["delta"] == pytest.approx(5.0)
    assert fluency["days_apart"] == 1


def test_a_pair_across_different_passages_is_never_made() -> None:
    """The whole comparison rests on holding the text still — a delta across two passages
    measures text difficulty, which is what the benchmark exists to avoid."""
    frame = pv.shadow_pairs(
        shadow_rows(
            {"reference_text": "a different passage entirely"},
            {"shadowed": 1},
        )
    )
    assert frame.empty


def test_a_missing_metric_produces_no_row_rather_than_a_zero() -> None:
    frame = pv.shadow_pairs(shadow_rows({"prosody": None}, {"shadowed": 1}))
    assert set(frame["metric"]) == {"Fluency"}


def test_a_shadowed_read_with_no_cold_partner_is_reported_not_dropped() -> None:
    """'Nothing to compare against yet' is the honest day-one state, and an empty panel
    reads as a broken one."""
    rows_in = shadow_rows({"shadowed": 1})
    assert pv.shadow_pairs(rows_in).empty
    assert pv.unpaired_passages(rows_in) == [pv.BENCHMARK_TITLE]


def test_a_paired_passage_is_not_reported_as_unpaired() -> None:
    assert pv.unpaired_passages(shadow_rows({}, {"shadowed": 1})) == []


def test_the_summary_always_carries_the_number_of_pairs() -> None:
    """Same discipline as the perception trainer's chance floor: a number whose anchor can be
    dropped will be read without it."""
    frame = pv.shadow_pairs(shadow_rows({}, {"shadowed": 1}))
    summary = pv.shadow_summary(frame)
    assert "1 pair" in summary
    assert "an observation, not a result" in summary


def test_a_summary_with_enough_pairs_drops_the_caveat() -> None:
    records = []
    for index in range(pv.MIN_PAIRS_FOR_CLAIM * 2):
        records.append({"shadowed": index % 2, "fluency": 70.0 + (index % 2) * 5})
    frame = pv.shadow_pairs(shadow_rows(*records))
    summary = pv.shadow_summary(frame)
    assert f"{pv.MIN_PAIRS_FOR_CLAIM} pairs" in summary
    assert "an observation, not a result" not in summary


def test_an_empty_comparison_says_what_is_missing() -> None:
    assert "no delta to name" in pv.shadow_summary(pv.shadow_pairs([]))


def test_the_gap_chart_draws_a_rule_at_zero() -> None:
    """Zero is where a shadowed read stops being better than a cold one — full transfer."""
    frame = pv.shadow_pairs(shadow_rows({}, {"shadowed": 1}))
    spec = json.dumps(pv.shadow_gap_chart(frame).to_dict())
    assert "rule" in spec


def test_a_modes_point_shape_does_not_depend_on_which_other_modes_exist() -> None:
    """Mode C exposed this: the shape range used to be a positional slice.

    `range=["triangle-up", "circle", "square"][: len(modes)]` is correct only while every mode
    is present. With no paragraph reads stored, Mode C silently inherited the circle Mode B had
    had — so the same glyph meant two different registers across two databases, and a reader
    comparing them would be comparing read speech against spontaneous speech without knowing it.
    A speaker can perfectly well have Drill and Unscripted attempts and no Paragraph ones.
    """
    import re

    def shapes(modes: list[str]) -> dict[str, str]:
        frame = pd.DataFrame(
            [
                {
                    "when": pd.Timestamp("2026-08-21T09:00:00Z"),
                    "attempt_id": index,
                    "metric": "Pronunciation",
                    "value": 80.0,
                    "series": pv.FREE_SERIES,
                    "mode": mode,
                    "label": "x",
                    "shadowed": "No",
                }
                for index, mode in enumerate(modes)
            ]
        )
        spec = json.dumps(json.loads(pv.score_chart(frame).to_json()))
        found = re.search(r'"domain": (\[[^]]*\]), "range": (\[[^]]*\])', spec)
        assert found, "the free-practice layer must encode shape by mode"
        return dict(zip(json.loads(found.group(1)), json.loads(found.group(2))))

    drill, paragraph, unscripted = (
        pv.MODE_LABELS[mode.value] for mode in (Mode.DRILL, Mode.PARAGRAPH, Mode.UNSCRIPTED)
    )
    everything = shapes([drill, paragraph, unscripted])
    assert shapes([paragraph, unscripted])[unscripted] == everything[unscripted]
    assert shapes([drill, unscripted])[unscripted] == everything[unscripted]
    assert shapes([drill, unscripted])[drill] == everything[drill]
    # And the three are actually distinguishable from each other.
    assert len(set(everything.values())) == 3
