"""Schema, both raw-response columns, and the month-scoped meters."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

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
def conn() -> sqlite3.Connection:
    connection = db.connect(":memory:")
    yield connection
    connection.close()


def add(connection: sqlite3.Connection, **overrides) -> int:
    kwargs = {
        "mode": Mode.DRILL,
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
    assert json.loads(row["azure_raw_json"]) == AZURE_PAYLOAD


def test_gemini_columns_start_null(conn: sqlite3.Connection) -> None:
    # They exist from schema v1 so the coaching chunk is an UPDATE, not a migration.
    row = db.get_attempt(conn, add(conn))
    assert row["gemini_raw_json"] is None
    assert row["coach_source"] is None


def test_attach_coaching_fills_the_second_response(conn: sqlite3.Connection) -> None:
    attempt_id = add(conn)
    db.attach_coaching(
        conn, attempt_id, gemini_raw={"overall_comment": "ok"}, coach_source="gemini"
    )
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


# --- The practice queue ---------------------------------------------------------------------
# The queue's whole promise is that it survives a restart. These run against a real file
# rather than :memory: so "reconnect" means what it says.


def test_the_new_tables_appear_without_moving_the_schema_version(tmp_path) -> None:
    """Additive, so an existing version-1 database gains them on the next connect."""
    path = tmp_path / "coach.db"
    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION == 1
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"practice_targets", "perception_trials"} <= tables
    conn.close()


def test_a_target_and_its_due_date_survive_a_restart(tmp_path) -> None:
    path = tmp_path / "coach.db"
    conn = db.connect(path)
    db.upsert_target(
        conn,
        item="/θ/ → /s/",
        kind="contrast",
        evidence={"attempts": 4},
        next_due="2026-09-01T00:00:00Z",
    )
    conn.close()

    reopened = db.connect(path)
    rows = reopened.execute("SELECT * FROM practice_targets").fetchall()
    assert len(rows) == 1
    assert rows[0]["item"] == "/θ/ → /s/"
    assert rows[0]["next_due"] == "2026-09-01T00:00:00Z"
    assert rows[0]["state"] == "active"
    assert json.loads(rows[0]["evidence"]) == {"attempts": 4}
    reopened.close()


def test_upserting_refreshes_evidence_without_resetting_the_schedule(tmp_path) -> None:
    """Re-reading the same evidence is not a reason to un-graduate an item."""
    conn = db.connect(tmp_path / "coach.db")
    target_id = db.upsert_target(conn, item="/v/ → /w/", kind="contrast", evidence={"attempts": 2})
    db.update_target(
        conn, target_id, state="graduated", reviews_passed=2, next_due="2026-12-01T00:00:00Z"
    )
    again = db.upsert_target(conn, item="/v/ → /w/", kind="contrast", evidence={"attempts": 5})
    assert again == target_id

    row = db.targets(conn)[0]
    assert row["state"] == "graduated"
    assert row["reviews_passed"] == 2
    assert row["next_due"] == "2026-12-01T00:00:00Z"
    assert json.loads(row["evidence"])["attempts"] == 5


def test_trials_are_stored_with_their_own_chance_floor(tmp_path) -> None:
    """`alternatives` is a fact on the row, not an assumption in whatever reads it."""
    conn = db.connect(tmp_path / "coach.db")
    target_id = db.upsert_target(conn, item="/θ/ → /s/", kind="contrast", evidence={})
    db.record_trial(
        conn,
        block_id="b1",
        target_id=target_id,
        item="/θ/ → /s/",
        word="think",
        voice="en-US-AvaNeural",
        novel=True,
        alternatives=2,
        answered="sink",
        correct=False,
    )
    row = db.all_trials(conn)[0]
    assert row["alternatives"] == 2
    assert row["correct"] == 0 and row["novel"] == 1 and row["review"] == 0


def test_heard_stimuli_reports_the_last_time_each_combination_played(tmp_path) -> None:
    conn = db.connect(tmp_path / "coach.db")
    for when in ("2026-08-01T00:00:00Z", "2026-08-09T00:00:00Z"):
        db.record_trial(
            conn,
            block_id="b",
            target_id=None,
            item="/θ/ → /s/",
            word="think",
            voice="en-US-AvaNeural",
            novel=False,
            alternatives=2,
            answered="think",
            correct=True,
            created_at=when,
        )
    heard = db.heard_stimuli(conn, "/θ/ → /s/")
    assert heard[("think", "en-US-AvaNeural")] == "2026-08-09T00:00:00Z"


def test_trials_outlive_the_target_they_belonged_to(tmp_path) -> None:
    """The item string is denormalised precisely so history is not lost with a row."""
    conn = db.connect(tmp_path / "coach.db")
    target_id = db.upsert_target(conn, item="/θ/ → /s/", kind="contrast", evidence={})
    db.record_trial(
        conn,
        block_id="b1",
        target_id=target_id,
        item="/θ/ → /s/",
        word="think",
        voice="v",
        novel=True,
        alternatives=2,
        answered="think",
        correct=True,
    )
    db.remove_target(conn, target_id)
    assert db.targets(conn) == []
    assert len(db.trials_for(conn, "/θ/ → /s/")) == 1


def test_the_queue_fingerprint_moves_when_anything_it_covers_does(tmp_path) -> None:
    conn = db.connect(tmp_path / "coach.db")
    start = db.queue_fingerprint(conn)
    target_id = db.upsert_target(conn, item="x", kind="contrast", evidence={})
    after_target = db.queue_fingerprint(conn)
    assert after_target != start
    db.record_trial(
        conn,
        block_id="b",
        target_id=target_id,
        item="x",
        word="w",
        voice="v",
        novel=True,
        alternatives=2,
        answered="w",
        correct=True,
    )
    assert db.queue_fingerprint(conn) != after_target


# --- Attempt tags -----------------------------------------------------------------------------
# How an attempt was produced, when it was produced in some way other than reading the text
# cold. A separate table rather than a column: `attempts` is created with CREATE TABLE IF NOT
# EXISTS, so a column would need a real ALTER TABLE and `_migrate` has no upgrade path.


def test_tagging_an_attempt_is_readable_back(conn: sqlite3.Connection) -> None:
    attempt_id = add(conn)
    db.tag_attempt(conn, attempt_id, db.SHADOW_TAG)
    assert db.tags_for(conn, attempt_id) == {db.SHADOW_TAG}


def test_an_untagged_attempt_has_no_tags(conn: sqlite3.Connection) -> None:
    assert db.tags_for(conn, add(conn)) == set()


def test_tagging_twice_is_a_no_op(conn: sqlite3.Connection) -> None:
    """Streamlit re-runs the script constantly; a second write must not raise or duplicate."""
    attempt_id = add(conn)
    db.tag_attempt(conn, attempt_id, db.SHADOW_TAG)
    db.tag_attempt(conn, attempt_id, db.SHADOW_TAG)
    assert db.tags_for(conn, attempt_id) == {db.SHADOW_TAG}


def test_the_tag_table_did_not_move_the_schema_version(conn: sqlite3.Connection) -> None:
    """Additive, exactly like the v0.7.0 queue tables: an existing v1 database gains it on the
    next connect() and `user_version` never moves."""
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION == 1


def test_the_series_reader_carries_the_shadow_flag(conn: sqlite3.Connection) -> None:
    cold = add(conn, created_at="2026-08-01T08:00:00Z")
    shadowed = add(conn, created_at="2026-08-02T08:00:00Z")
    db.tag_attempt(conn, shadowed, db.SHADOW_TAG)

    by_id = {row["id"]: row for row in db.attempt_series(conn)}
    assert not by_id[cold]["shadowed"]
    assert by_id[shadowed]["shadowed"]


def test_the_payload_reader_carries_the_shadow_flag_too(conn: sqlite3.Connection) -> None:
    """Both readers, or the rhythm chart and the score chart would disagree about one row."""
    shadowed = add(conn)
    db.tag_attempt(conn, shadowed, db.SHADOW_TAG)
    assert db.attempt_payloads(conn)[0]["shadowed"]


def test_a_tag_never_reaches_the_meter(conn: sqlite3.Connection) -> None:
    """A shadowed read is real billable audio and stays on the meter like any other."""
    attempt_id = add(conn, audio_seconds=70.0, created_at=f"{db.month_prefix()}-05T08:00:00Z")
    db.tag_attempt(conn, attempt_id, db.SHADOW_TAG)
    assert db.monthly_stt_seconds(conn) == pytest.approx(70.0)
