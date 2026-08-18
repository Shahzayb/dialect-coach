"""Schema, both raw-response columns, and the month-scoped meters."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

import db
from utils import Mode

AZURE_PAYLOAD = {"RecognitionStatus": "Success", "NBest": [{"PronunciationAssessment": {}}]}
SCORES = {"pron_score": 82.0, "accuracy": 85.0, "fluency": 90.0,
          "completeness": 100.0, "prosody": 78.0}


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = db.connect(":memory:")
    yield connection
    connection.close()


def add(connection: sqlite3.Connection, **overrides) -> int:
    kwargs = dict(
        mode=Mode.DRILL, reference_text="the thin man", recognised_text="the tin man",
        audio_seconds=12.0, audio_sha256="abc123", overall_scores=SCORES,
        azure_raw=AZURE_PAYLOAD,
    )
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
    assert json.loads(row["azure_raw_json"]) == AZURE_PAYLOAD


def test_gemini_columns_start_null(conn: sqlite3.Connection) -> None:
    # They exist from schema v1 so the coaching chunk is an UPDATE, not a migration.
    row = db.get_attempt(conn, add(conn))
    assert row["gemini_raw_json"] is None
    assert row["coach_source"] is None


def test_attach_coaching_fills_the_second_response(conn: sqlite3.Connection) -> None:
    attempt_id = add(conn)
    db.attach_coaching(conn, attempt_id, gemini_raw={"overall_comment": "ok"},
                       coach_source="gemini")
    row = db.get_attempt(conn, attempt_id)
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
    assert row["pron_score"] == 82.0
    assert row["prosody"] is None, "absent prosody must not be coerced to 0.0"


def test_monthly_stt_seconds_only_counts_the_current_month(conn: sqlite3.Connection) -> None:
    add(conn, audio_seconds=10.0, created_at="2026-08-01T00:00:00Z")
    add(conn, audio_seconds=25.0, created_at="2026-08-31T23:59:59Z")
    add(conn, audio_seconds=99.0, created_at="2026-07-31T23:59:59Z")  # previous month
    when = datetime(2026, 8, 18, tzinfo=timezone.utc)
    assert db.monthly_stt_seconds(conn, when) == 35.0


def test_monthly_stt_seconds_excludes_offline_replays(conn: sqlite3.Connection) -> None:
    add(conn, audio_seconds=10.0, created_at="2026-08-02T00:00:00Z")
    add(conn, audio_seconds=500.0, created_at="2026-08-03T00:00:00Z", offline=True)
    when = datetime(2026, 8, 18, tzinfo=timezone.utc)
    assert db.monthly_stt_seconds(conn, when) == 10.0


def test_monthly_tts_characters_is_a_separate_bucket(conn: sqlite3.Connection) -> None:
    db.record_tts_usage(conn, characters=1200, created_at="2026-08-04T00:00:00Z")
    db.record_tts_usage(conn, characters=300, created_at="2026-07-04T00:00:00Z")
    when = datetime(2026, 8, 18, tzinfo=timezone.utc)
    assert db.monthly_tts_characters(conn, when) == 1200


def test_recent_attempts_is_newest_first_and_omits_raw_json(conn: sqlite3.Connection) -> None:
    add(conn, reference_text="first")
    add(conn, reference_text="second")
    rows = db.recent_attempts(conn, limit=5)
    assert [r["reference_text"] for r in rows] == ["second", "first"]
    assert "azure_raw_json" not in rows[0].keys()


def test_a_newer_schema_version_is_refused(tmp_path) -> None:
    path = tmp_path / "future.db"
    conn = sqlite3.connect(path)
    conn.execute(f"PRAGMA user_version={db.SCHEMA_VERSION + 1}")
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="newer than this code"):
        db.connect(path)
