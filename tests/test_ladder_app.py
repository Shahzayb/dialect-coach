"""The practice ladder as a running surface: it opens, it bails, and it never takes the app down.

The crash this file exists for was not a bad verdict — it was `st.selectbox` handed
`sqlite3.Row` objects. Streamlit deep-copies a widget's options into session state, a Row
cannot be pickled, and the whole script dies. Nothing about the ladder's own arithmetic would
have caught it, and every other tab went down with it.
"""

from __future__ import annotations

import os
from pathlib import Path

from streamlit.testing.v1 import AppTest

import db
import ladder
import progress_view
from utils import Mode

APP = str(Path(__file__).resolve().parent.parent / "src" / "app.py")


def _app(**state) -> AppTest:
    app = AppTest.from_file(APP, default_timeout=60)
    for key, value in state.items():
        app.session_state[key] = value
    app.run()
    return app


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
