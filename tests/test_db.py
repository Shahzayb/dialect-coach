"""Schema, both raw-response columns, and the month-scoped meters."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest

import db
from utils import Mode

AZURE_PAYLOAD = {"RecognitionStatus": "Success", "NBest": [{"PronunciationAssessment": {}}]}
SCORES = {
    "pron_score": 82.0,
    "accuracy": 85.0,
    "fluency": 90.0,
    "completeness": 100.0,
    "prosody": 78.0,
}


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.connect(":memory:")
    yield connection
    connection.close()


def add(connection: sqlite3.Connection, **overrides: Any) -> int:
    kwargs: dict[str, Any] = {
        "mode": Mode.PARAGRAPH,
        "reference_text": "the thin man",
        "recognised_text": "the tin man",
        "audio_seconds": 12.0,
        "audio_sha256": "abc123",
        "overall_scores": SCORES,
        "azure_raw": AZURE_PAYLOAD,
    }
    kwargs.update(overrides)
    return db.record_attempt(connection, **kwargs)


def test_connect_stamps_the_schema_version(conn: sqlite3.Connection) -> None:
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION


def test_connect_is_idempotent(tmp_path) -> None:
    path = tmp_path / "nested" / "coach.db"
    first = db.connect(path)
    add(first)
    first.close()
    second = db.connect(path)  # must not wipe or fail on the existing schema
    assert len(db.recent_attempts(second)) == 1
    second.close()


def test_connect_creates_the_parent_directory(tmp_path) -> None:
    path = tmp_path / "does" / "not" / "exist" / "coach.db"
    db.connect(path).close()
    assert path.exists()


def test_azure_response_is_stored_verbatim(conn: sqlite3.Connection) -> None:
    attempt_id = add(conn)
    row = db.get_attempt(conn, attempt_id)
    assert row is not None
    assert json.loads(row["azure_raw_json"]) == AZURE_PAYLOAD


def test_gemini_columns_start_null(conn: sqlite3.Connection) -> None:
    # They exist from schema v1 so the coaching chunk is an UPDATE, not a migration.
    row = db.get_attempt(conn, add(conn))
    assert row is not None
    assert row["gemini_raw_json"] is None
    assert row["coach_source"] is None


def test_attach_coaching_fills_the_second_response(conn: sqlite3.Connection) -> None:
    attempt_id = add(conn)
    db.attach_coaching(
        conn, attempt_id, gemini_raw={"overall_comment": "ok"}, coach_source="gemini"
    )
    row = db.get_attempt(conn, attempt_id)
    assert row is not None
    assert json.loads(row["gemini_raw_json"]) == {"overall_comment": "ok"}
    assert row["coach_source"] == "gemini"


def test_no_audio_column_exists(conn: sqlite3.Connection) -> None:
    """The brief rules out stored audio; only its hash is kept."""
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(attempts)")}
    assert "audio_sha256" in columns
    assert not {c for c in columns if "blob" in c.lower() or c in {"audio", "audio_bytes"}}


def test_scores_round_trip_and_missing_prosody_stays_null(conn: sqlite3.Connection) -> None:
    scores = dict(SCORES, prosody=None)
    row = db.get_attempt(conn, add(conn, overall_scores=scores))
    assert row is not None
    assert row["pron_score"] == 82.0
    assert row["prosody"] is None, "absent prosody must not be coerced to 0.0"


def test_monthly_stt_seconds_only_counts_the_current_month(conn: sqlite3.Connection) -> None:
    add(conn, audio_seconds=10.0, created_at="2026-08-01T00:00:00Z")
    add(conn, audio_seconds=25.0, created_at="2026-08-31T23:59:59Z")
    add(conn, audio_seconds=99.0, created_at="2026-07-31T23:59:59Z")  # previous month
    when = datetime(2026, 8, 18, tzinfo=UTC)
    assert db.monthly_stt_seconds(conn, when) == 35.0


def test_monthly_stt_seconds_excludes_offline_replays(conn: sqlite3.Connection) -> None:
    add(conn, audio_seconds=10.0, created_at="2026-08-02T00:00:00Z")
    add(conn, audio_seconds=500.0, created_at="2026-08-03T00:00:00Z", offline=True)
    when = datetime(2026, 8, 18, tzinfo=UTC)
    assert db.monthly_stt_seconds(conn, when) == 10.0


def test_monthly_tts_characters_is_a_separate_bucket(conn: sqlite3.Connection) -> None:
    db.record_tts_usage(conn, characters=1200, created_at="2026-08-04T00:00:00Z")
    db.record_tts_usage(conn, characters=300, created_at="2026-07-04T00:00:00Z")
    when = datetime(2026, 8, 18, tzinfo=UTC)
    assert db.monthly_tts_characters(conn, when) == 1200


def test_recent_attempts_is_newest_first_and_omits_raw_json(conn: sqlite3.Connection) -> None:
    add(conn, reference_text="first")
    add(conn, reference_text="second")
    rows = db.recent_attempts(conn, limit=5)
    assert [r["reference_text"] for r in rows] == ["second", "first"]
    assert "azure_raw_json" not in rows[0]


def test_a_newer_schema_version_is_refused(tmp_path) -> None:
    path = tmp_path / "future.db"
    conn = sqlite3.connect(path)
    conn.execute(f"PRAGMA user_version={db.SCHEMA_VERSION + 1}")
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="newer than this code"):
        db.connect(path)


# --- The tables that outlived their features ---------------------------------------------------
# `practice_targets`, `perception_trials`, `attempt_tags`, `attempt_content_scores`,
# `speaker_baseline`, `vowel_measurements` and `native_renderings` lost their writers on
# 2026-08-25. The DDL and the rows stay: they hold real recorded evidence — calibration reads,
# answered perception trials — and dropping them is unrecoverable in a way that deleting the
# code is not. These tests are what stops a later cleanup pass quietly removing them.


def test_the_retired_tables_still_exist_so_their_rows_are_not_lost(tmp_path) -> None:
    path = tmp_path / "coach.db"
    conn = db.connect(path)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {
        "practice_targets",
        "perception_trials",
        "attempt_tags",
        "attempt_content_scores",
        "speaker_baseline",
        "vowel_measurements",
        "native_renderings",
    } <= tables
    conn.close()


def test_the_schema_version_never_moved(tmp_path) -> None:
    """Every table below the first is additive, so an existing v1 database gains them all."""
    conn = db.connect(tmp_path / "coach.db")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION == 1
    conn.close()


# --- The History page --------------------------------------------------------------------------


def test_a_page_is_newest_first_and_offset_walks_backwards(conn: sqlite3.Connection) -> None:
    ids = [add(conn, created_at=f"2026-08-0{n}T08:00:00Z") for n in range(1, 6)]
    first = [row["id"] for row in db.attempt_page(conn, limit=2)]
    second = [row["id"] for row in db.attempt_page(conn, limit=2, offset=2)]
    assert first == [ids[4], ids[3]]
    assert second == [ids[2], ids[1]]


def test_a_page_omits_the_raw_json_columns(conn: sqlite3.Connection) -> None:
    """A page of twelve would otherwise carry twelve verbatim Azure payloads."""
    add(conn)
    row = db.attempt_page(conn, limit=1)[0]
    assert "azure_raw_json" not in row
    assert "gemini_raw_json" not in row


def test_offline_replays_appear_in_history(conn: sqlite3.Connection) -> None:
    """Unlike the progress readers this replaced: a fixture replay is a real recorded row."""
    add(conn, offline=True)
    assert db.attempt_count(conn) == 1
    assert len(db.attempt_page(conn, limit=10)) == 1


def test_the_mode_filter_catches_legacy_drill_rows(conn: sqlite3.Connection) -> None:
    """Rows written before 2026-08-25 carry `mode = 'drill'` and must not be stranded."""
    scripted = add(conn)
    unscripted = add(conn, mode=Mode.UNSCRIPTED)
    legacy = add(conn)
    conn.execute("UPDATE attempts SET mode = 'drill' WHERE id = ?", (legacy,))
    conn.commit()

    ids = {row["id"] for row in db.attempt_page(conn, limit=10, mode=Mode.PARAGRAPH.value)}
    assert ids == {scripted, legacy}
    assert db.attempt_count(conn, mode=Mode.PARAGRAPH.value) == 2
    assert db.attempt_count(conn, mode=Mode.UNSCRIPTED.value) == 1
    assert {row["id"] for row in db.attempt_page(conn, limit=10, mode=Mode.UNSCRIPTED.value)} == {
        unscripted
    }


def test_the_count_matches_what_the_pages_actually_walk(conn: sqlite3.Connection) -> None:
    for _ in range(7):
        add(conn)
    total = db.attempt_count(conn)
    walked: list[int] = []
    offset = 0
    while True:
        page = db.attempt_page(conn, limit=3, offset=offset)
        if not page:
            break
        walked.extend(row["id"] for row in page)
        offset += 3
    assert len(walked) == total == 7


def test_deleting_an_attempt_takes_its_cascaded_rows_with_it(conn: sqlite3.Connection) -> None:
    attempt_id = add(conn)
    db.record_audio(
        conn, attempt_id, path="/tmp/x.wav", sha256="h", size_bytes=1, sample_rate=16_000
    )
    db.attach_annotation(conn, attempt_id, raw={"words": []})

    path = db.delete_attempt(conn, attempt_id)

    assert path == "/tmp/x.wav"
    assert db.get_attempt(conn, attempt_id) is None
    assert db.audio_for(conn, attempt_id) is None
    assert db.annotation_for(conn, attempt_id) is None


def test_deleting_an_attempt_without_a_recording_reports_no_path(
    conn: sqlite3.Connection,
) -> None:
    """The caller unlinks the file, so None has to mean "there is nothing to unlink"."""
    assert db.delete_attempt(conn, add(conn)) is None


# --- The prosody annotation ---------------------------------------------------------------------


def test_an_annotation_round_trips(conn: sqlite3.Connection) -> None:
    attempt_id = add(conn)
    db.attach_annotation(conn, attempt_id, raw={"words": [{"word": "hello"}]})
    assert db.annotation_for(conn, attempt_id) == {"words": [{"word": "hello"}]}


def test_re_annotating_replaces_rather_than_accumulating(conn: sqlite3.Connection) -> None:
    """One right answer per passage; the newest model output is the one worth keeping."""
    attempt_id = add(conn)
    db.attach_annotation(conn, attempt_id, raw={"words": [], "summary": "first"})
    db.attach_annotation(conn, attempt_id, raw={"words": [], "summary": "second"})
    stored = db.annotation_for(conn, attempt_id)
    assert stored is not None
    assert stored["summary"] == "second"
    rows = conn.execute("SELECT COUNT(*) FROM attempt_annotations").fetchone()[0]
    assert rows == 1


def test_an_attempt_with_no_annotation_reads_as_none(conn: sqlite3.Connection) -> None:
    assert db.annotation_for(conn, add(conn)) is None


def test_an_unreadable_stored_annotation_is_none_rather_than_a_crash(
    conn: sqlite3.Connection,
) -> None:
    """A bad row must not take the History page down with it."""
    attempt_id = add(conn)
    conn.execute(
        "INSERT INTO attempt_annotations (attempt_id, raw_json, created_at) VALUES (?, ?, ?)",
        (attempt_id, "{not json", db.utc_now_iso()),
    )
    conn.commit()
    assert db.annotation_for(conn, attempt_id) is None
