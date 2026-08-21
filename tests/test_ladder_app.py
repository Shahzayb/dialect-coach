"""The practice ladder as a running surface: it opens, it bails, and it never takes the app down.

The crash this file exists for was not a bad verdict — it was `st.selectbox` handed
`sqlite3.Row` objects. Streamlit deep-copies a widget's options into session state, a Row
cannot be pickled, and the whole script dies. Nothing about the ladder's own arithmetic would
have caught it, and every other tab went down with it.
"""

from __future__ import annotations

import io
import json
import os
import wave
from datetime import datetime
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import db
import ladder
import practice_queue
import progress_view
import shadowing
import utils
from utils import Mode

APP = str(Path(__file__).resolve().parent.parent / "src" / "app.py")


def _app(**state) -> AppTest:
    app = AppTest.from_file(APP, default_timeout=60)
    for key, value in state.items():
        app.session_state[key] = value
    app.run()
    return app


def _wav(seconds: float) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as sink:
        sink.setnchannels(1)
        sink.setsampwidth(2)
        sink.setframerate(16_000)
        sink.writeframes(b"\x00\x00" * int(seconds * 16_000))
    return out.getvalue()


def _seed_audio(attempt_id: int, words: list[dict[str, float | str]], tmp: Path) -> None:
    """Give a seeded reading a recording and word timings, as a real attempt carries."""
    conn = db.connect(os.environ["DB_PATH"])
    path = tmp / f"{attempt_id}.wav"
    path.write_bytes(_wav(70.0))
    db.record_audio(
        conn, attempt_id, path=str(path), sha256=f"a{attempt_id}", size_bytes=1, sample_rate=16000
    )
    conn.execute(
        "UPDATE attempts SET azure_raw_json = ? WHERE id = ?",
        (json.dumps(_payload(words)), attempt_id),
    )
    conn.commit()
    conn.close()


def _payload(words: list[dict[str, float | str]]) -> dict[str, object]:
    """The shape speech_analyzer.normalise reads back."""
    return {
        "NBest": [
            {
                "Display": progress_view.BENCHMARK_PASSAGE,
                "Words": [
                    {
                        "Word": w["word"],
                        "Offset": int(float(w["start_s"]) * 10_000_000),
                        "Duration": int((float(w["end_s"]) - float(w["start_s"])) * 10_000_000),
                        "PronunciationAssessment": {"AccuracyScore": 90.0},
                        "Phonemes": [],
                    }
                    for w in words
                ],
                "PronunciationAssessment": {},
            }
        ]
    }


def _benchmark_words() -> list[dict[str, float | str]]:
    return [
        {"word": w, "start_s": i * 0.3, "end_s": i * 0.3 + 0.25}
        for i, w in enumerate(progress_view.BENCHMARK_PASSAGE.split())
    ]


def _seed_reading(when: str = "2026-07-01T08:00:00Z") -> int:
    """One benchmark reading, which is what the ladder offer looks for."""
    conn = db.connect(os.environ["DB_PATH"])
    attempt_id = db.record_attempt(
        conn,
        mode=Mode.PARAGRAPH,
        reference_text=progress_view.BENCHMARK_PASSAGE,
        recognised_text=progress_view.BENCHMARK_PASSAGE,
        audio_seconds=62.0,
        audio_sha256=f"ladder-{when}",
        overall_scores={"snr_db_min": 30.0},
        azure_raw={},
        created_at=when,
    )
    db.tag_attempt(conn, attempt_id, "read")
    conn.close()
    return attempt_id


# --- It never takes the app down -----------------------------------------------------------------


def test_the_ladder_offer_renders_with_no_readings_at_all() -> None:
    app = _app()
    assert not app.exception


def test_the_ladder_offer_renders_with_a_reading_stored() -> None:
    """The selectbox regression: a Row in a widget's options kills the entire script."""
    _seed_reading()
    app = _app()
    assert not app.exception


def test_the_ladder_offer_survives_several_readings() -> None:
    for minute in ("08:00:00", "08:20:00", "08:40:00"):
        _seed_reading(f"2026-07-01T{minute}Z")
    app = _app()
    assert not app.exception


def test_opening_a_practice_session_does_not_crash_when_the_audio_is_gone() -> None:
    """Recordings are gitignored, so a stored attempt with no audio is an ordinary state."""
    attempt_id = _seed_reading()
    app = _app(
        ladder_practice={"attempt": attempt_id, "rung": ladder.Rung.SENTENCE.value, "unit": 0}
    )
    assert not app.exception
    assert any("no longer readable" in w.value for w in app.warning)


def test_an_open_session_takes_over_the_today_tab() -> None:
    attempt_id = _seed_reading()
    app = _app(ladder_practice={"attempt": attempt_id, "rung": ladder.Rung.WORD.value, "unit": 0})
    assert not app.exception
    assert any("Practising one word" in m.value for m in app.markdown)


# --- Bailing is a first-class outcome ------------------------------------------------------------


def test_dropping_a_unit_leaves_the_queue_alone_and_ends_the_session() -> None:
    attempt_id = _seed_reading()
    app = _app(
        ladder_practice={"attempt": attempt_id, "rung": ladder.Rung.SENTENCE.value, "unit": 0}
    )
    dropped = [b for b in app.button if b.label == "Drop this"]
    assert dropped, "bailing has to be reachable from the surface itself"
    dropped[0].click().run()
    assert not app.exception
    assert "ladder_practice" not in app.session_state


def test_the_offer_says_which_passage_it_can_judge_when_there_is_nothing_to_practise() -> None:
    """Day one has to be words, not an empty picker."""
    app = _app()
    assert any(progress_view.BENCHMARK_TITLE in caption.value for caption in app.caption), (
        "the surface must name the passage its bands were measured on"
    )


# --- Banking is the one thing here that spends anything -------------------------------------------


def _open(attempt_id: int, rung: ladder.Rung = ladder.Rung.SENTENCE) -> AppTest:
    return _app(ladder_practice={"attempt": attempt_id, "rung": rung.value, "unit": 0})


def test_repeating_offers_nothing_to_spend_until_there_is_a_take() -> None:
    """The bank button must not exist before there is something to bank."""
    app = _open(_seed_reading())
    assert not app.exception
    assert not [b for b in app.button if "Bank" in b.label]


def test_the_surface_says_repetition_costs_nothing(tmp_path: Path) -> None:
    """The claim the hybrid rests on has to be on screen, not only in the design."""
    attempt_id = _seed_reading()
    _seed_audio(attempt_id, _benchmark_words(), tmp_path)
    app = _open(attempt_id)
    assert not app.exception
    assert any(
        "no allowance spent" in caption.value or "no network" in caption.value
        for caption in app.caption
    )


def test_offline_mode_is_named_as_what_stands_between_this_and_a_charge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OFFLINE_MODE", "1")
    app = _open(_seed_reading())
    assert not app.exception


def test_a_banked_take_is_tagged_so_it_stays_off_the_chart() -> None:
    """The tag is the whole mechanism; wiring it wrong is invisible until the cloud grows."""
    conn = db.connect(os.environ["DB_PATH"])
    attempt_id = db.record_attempt(
        conn,
        mode=Mode.DRILL,
        reference_text="Nothing here is clever.",
        recognised_text="Nothing here is clever.",
        audio_seconds=3.0,
        audio_sha256="banked-take",
        overall_scores={},
        azure_raw={},
    )
    db.tag_attempt(conn, attempt_id, db.REP_TAG)
    rows = [r for r in db.attempt_series(conn) if int(r["id"]) == attempt_id]
    conn.close()
    assert rows and rows[0]["rep"], "a banked take must be recognisable as one"
    assert not progress_view.spoken_attempts(rows), "and must not reach the progress view"


def test_a_reading_with_audio_reaches_the_three_way_listen(tmp_path: Path) -> None:
    """The end state the whole surface exists for, driven through the real app."""
    attempt_id = _seed_reading()
    _seed_audio(attempt_id, _benchmark_words(), tmp_path)
    app = _open(attempt_id)
    assert not app.exception
    text = " ".join(m.value for m in app.markdown) + " ".join(c.value for c in app.caption)
    assert "your voice, one thing changed" in text
    assert "Mine" in text
    assert "Say it again" in text


def test_the_unit_picker_offers_every_sentence_of_the_passage(tmp_path: Path) -> None:
    attempt_id = _seed_reading()
    _seed_audio(attempt_id, _benchmark_words(), tmp_path)
    app = _open(attempt_id)
    assert not app.exception
    pickers = [s for s in app.selectbox if s.label == "Which one"]
    assert pickers, "there has to be a way to choose which unit to practise"
    assert len(pickers[0].options) == len(shadowing.phrases(progress_view.BENCHMARK_PASSAGE))


# --- Ladder targets on the queue ------------------------------------------------------------------


def _seed_target(item: str = "Nothing here is clever. · sentence") -> int:
    conn = db.connect(os.environ["DB_PATH"])
    target_id = db.upsert_target(
        conn,
        item=item,
        kind="sentence",
        evidence={
            "why": "Outside the native range on that take: terminal fall.",
            "rung": "sentence",
        },
    )
    conn.close()
    return target_id


def test_a_ladder_target_appears_on_today_with_its_rule() -> None:
    _seed_target()
    app = _app()
    assert not app.exception
    assert any("On the ladder" in m.value for m in app.markdown)


def test_the_card_states_the_rule_that_would_take_it_off() -> None:
    """The brief requires the rule be readable, not implicit however carefully implemented."""
    _seed_target()
    app = _app()
    text = " ".join(m.value for m in app.markdown)
    assert "no way to mark this done by hand" in text
    assert "survive inside its paragraph" in text
    assert "come back on its own" in text


def test_a_target_with_no_fresh_take_says_so_rather_than_implying_a_measurement() -> None:
    _seed_target()
    app = _app()
    assert any("Not measured yet" in m.value for m in app.markdown)


def test_a_ladder_target_can_be_taken_off_the_list() -> None:
    """Bailing on a target has to be as available as bailing mid-session."""
    _seed_target()
    app = _app()
    remove = [b for b in app.button if b.label == "Take it off the list"]
    assert remove
    remove[0].click().run()
    assert not app.exception
    conn = db.connect(os.environ["DB_PATH"])
    remaining = [r for r in db.targets(conn) if str(r["kind"]) == "sentence"]
    conn.close()
    assert not remaining


def test_a_ladder_target_does_not_consume_one_of_the_three_perception_slots() -> None:
    """MAX_ACTIVE_TARGETS is about what you can hold in your head while speaking."""
    _seed_target()
    assert not practice_queue.promotable("sentence")


# --- The fixes from review ------------------------------------------------------------------------


def test_the_block_grader_never_touches_a_ladder_target() -> None:
    """It has no blocks by design, so grading one there answers a question it cannot answer."""
    _seed_target()
    conn = db.connect(os.environ["DB_PATH"])
    before = [dict(r) for r in db.targets(conn) if str(r["kind"]) == "sentence"]
    conn.close()
    app = _app()
    assert not app.exception
    conn = db.connect(os.environ["DB_PATH"])
    after = [dict(r) for r in db.targets(conn) if str(r["kind"]) == "sentence"]
    conn.close()
    assert [t["state"] for t in after] == [t["state"] for t in before]
    assert [t["next_due"] for t in after] == [t["next_due"] for t in before]


def test_two_units_sharing_a_long_prefix_are_two_targets() -> None:
    """`(item, kind)` is unique, so a truncated label would silently merge them."""
    conn = db.connect(os.environ["DB_PATH"])
    long_prefix = "The whole value is that the passage never changes, so whatever"
    first = db.upsert_target(
        conn, item=f"2: {long_prefix} moves", kind="sentence", evidence={"script_index": 2}
    )
    second = db.upsert_target(
        conn, item=f"9: {long_prefix} shifts", kind="sentence", evidence={"script_index": 9}
    )
    kept = [r for r in db.targets(conn) if str(r["kind"]) == "sentence"]
    conn.close()
    assert first != second
    assert len(kept) == 2


def test_two_reads_taken_back_to_back_do_not_make_a_noise_floor(tmp_path: Path) -> None:
    """An under-estimated floor calls noise movement, which is the flattering direction.

    Asserted through `ladder.metric_noise_floor`'s inputs rather than the surface, because the
    surface only reaches the floor once there is a recorded take to judge against it.
    """
    first = _seed_reading("2026-07-01T08:00:00Z")
    second = _seed_reading("2026-07-01T08:02:00Z")  # two minutes apart
    _seed_audio(first, _benchmark_words(), tmp_path)
    _seed_audio(second, _benchmark_words(), tmp_path)

    conn = db.connect(os.environ["DB_PATH"])
    rows = [
        r
        for r in db.attempt_series(conn)
        if str(r["reference_text"] or "") == progress_view.BENCHMARK_PASSAGE
    ]
    conn.close()
    assert len(rows) == 2, "the two reads have to be there for the gap check to reject them"
    gap = (
        datetime.strptime(str(rows[1]["created_at"]), "%Y-%m-%dT%H:%M:%SZ")
        - datetime.strptime(str(rows[0]["created_at"]), "%Y-%m-%dT%H:%M:%SZ")
    ).total_seconds() / 60.0
    assert gap < utils.get_float("CALIBRATION_GAP_MINUTES"), (
        "this pair must sit under the bar, or the test proves nothing"
    )
