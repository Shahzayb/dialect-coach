"""Local SQLite history: one row per attempt, holding both raw API responses verbatim.

Why verbatim: the normalised shape this app renders is a lossy projection chosen for
today's UI. Keeping exactly what Azure and Gemini returned means a later change of mind
about what to surface is a re-parse of stored rows, not a re-recording that spends quota
again. No audio is ever stored — the brief rules that out, and the SHA-256 is enough to
recognise a repeat attempt.

This module never imports Streamlit, so tests and scripts can use it. `app.py` is
responsible for wrapping `connect()` in `@st.cache_resource`: Streamlit re-runs the whole
script on every widget interaction, and reopening the connection each time is the trap.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import utils
from utils import Mode

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS attempts (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at       TEXT    NOT NULL,   -- UTC ISO-8601, always 'Z'-suffixed
  mode             TEXT    NOT NULL,   -- drill | paragraph | unscripted
  reference_text   TEXT,               -- NULL for unscripted, which has no reference
  recognised_text  TEXT,
  audio_seconds    REAL    NOT NULL,   -- what the STT meter is charged for this attempt
  audio_sha256     TEXT    NOT NULL,   -- recognises a repeat; the audio itself is not kept
  pron_score       REAL,
  accuracy         REAL,
  fluency          REAL,
  completeness     REAL,
  prosody          REAL,               -- NULL, never 0.0, when Azure did not return one
  azure_raw_json   TEXT    NOT NULL,   -- verbatim; a JSON array in continuous mode
  gemini_raw_json  TEXT,               -- NULL when the offline coach wrote the report
  coach_source     TEXT,               -- 'gemini' | 'fallback' | NULL
  offline          INTEGER NOT NULL DEFAULT 0  -- 1 when replayed from the fixture
);

CREATE INDEX IF NOT EXISTS idx_attempts_created_at ON attempts(created_at);

-- Separate from attempts because TTS is a separate free bucket with its own price, and a
-- single combined meter would mispredict. Written by the TTS chunk.
CREATE TABLE IF NOT EXISTS tts_usage (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at  TEXT    NOT NULL,
  characters  INTEGER NOT NULL,
  voice       TEXT
);

CREATE INDEX IF NOT EXISTS idx_tts_usage_created_at ON tts_usage(created_at);

-- The practice queue. What is being worked on right now, why it earned a place, and when
-- it is next due. Additive: created here with IF NOT EXISTS, so an existing version-1
-- database gains it on the next connect() and `user_version` never moves — the same
-- reasoning the v1 coaching columns were created NULL under.
CREATE TABLE IF NOT EXISTS practice_targets (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  item            TEXT    NOT NULL,   -- '/θ/ → /s/' for a sound, the word for a stress item
  kind            TEXT    NOT NULL,   -- contrast | vowel | stress
  added           TEXT    NOT NULL,
  last_seen       TEXT,               -- NULL until it has actually been practised once
  next_due        TEXT,
  state           TEXT    NOT NULL,   -- active | graduated
  -- One column beyond what the brief lists. The alternative was a schedule pointer inside
  -- `evidence`, which is for evidence.
  reviews_passed  INTEGER NOT NULL DEFAULT 0,
  -- JSON, and the verdict is not enough on its own: this holds the counts the item was
  -- promoted on, so the UI can answer "why is this here" with numbers rather than a claim.
  evidence        TEXT    NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_practice_targets_item
  ON practice_targets(item, kind);
CREATE INDEX IF NOT EXISTS idx_practice_targets_due ON practice_targets(next_due);

-- One row per answered trial, not one per block. Storing the evidence rather than only the
-- verdict is what lets a later question ("was it one voice I could never hear?") be a query
-- instead of a re-run.
CREATE TABLE IF NOT EXISTS perception_trials (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  block_id      TEXT    NOT NULL,   -- one id per block, so a block is recoverable as a unit
  target_id     INTEGER REFERENCES practice_targets(id),
  created_at    TEXT    NOT NULL,
  item          TEXT    NOT NULL,   -- denormalised: a deleted target keeps its history
  word          TEXT    NOT NULL,   -- the word actually played
  voice         TEXT    NOT NULL,
  novel         INTEGER NOT NULL,   -- 1 when this (word, voice) had never been presented
  -- Stored per trial so the chance floor is a FACT ON THE ROW rather than an assumption
  -- baked into whatever reads it later. A two-alternative forced choice scores 50% by
  -- guessing; if a three-alternative task ever ships, these rows keep reporting their own.
  alternatives  INTEGER NOT NULL,
  answered      TEXT    NOT NULL,
  correct       INTEGER NOT NULL,
  review        INTEGER NOT NULL DEFAULT 0  -- 1 when this was a spaced-review block
);

CREATE INDEX IF NOT EXISTS idx_perception_trials_block ON perception_trials(block_id);
CREATE INDEX IF NOT EXISTS idx_perception_trials_item ON perception_trials(item);
"""


def utc_now_iso() -> str:
    """UTC, second precision, 'Z'-suffixed — so string comparison is chronological."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def month_prefix(when: datetime | None = None) -> str:
    """'YYYY-MM' for the UTC month `when` falls in. The meter's bucket key."""
    moment = when or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m")


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Open (and if needed create) the database, returning a ready connection.

    `check_same_thread=False` because Streamlit runs script reruns on worker threads while
    the cached connection lives past any one of them.
    """
    target = str(path or utils.get("DB_PATH"))
    if target != ":memory:":
        Path(target).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(target, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # WAL so a read (the usage meter) never blocks the write that follows it.
    if target != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Create the schema and stamp its version. One version so far, so no upgrade path."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current > SCHEMA_VERSION:
        raise RuntimeError(
            f"Database schema is version {current}, newer than this code understands "
            f"({SCHEMA_VERSION}). Point DB_PATH elsewhere or update the app."
        )
    conn.executescript(_SCHEMA)
    if current != SCHEMA_VERSION:
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    conn.commit()


def record_attempt(
    conn: sqlite3.Connection,
    *,
    mode: Mode,
    reference_text: str | None,
    recognised_text: str | None,
    audio_seconds: float,
    audio_sha256: str,
    overall_scores: dict[str, Any],
    azure_raw: Any,
    offline: bool = False,
    created_at: str | None = None,
) -> int:
    """Store one assessment. `azure_raw` is serialised as given, not reshaped.

    Returns the new row's id, which `attach_coaching` uses to attach the coaching report.
    """
    scores = overall_scores or {}
    cursor = conn.execute(
        """
        INSERT INTO attempts (
            created_at, mode, reference_text, recognised_text, audio_seconds,
            audio_sha256, pron_score, accuracy, fluency, completeness, prosody,
            azure_raw_json, offline
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            created_at or utc_now_iso(),
            mode.value,
            reference_text,
            recognised_text,
            float(audio_seconds),
            audio_sha256,
            scores.get("pron_score"),
            scores.get("accuracy"),
            scores.get("fluency"),
            scores.get("completeness"),
            scores.get("prosody"),
            json.dumps(azure_raw, ensure_ascii=False),
            int(offline),
        ),
    )
    conn.commit()
    attempt_id = int(cursor.lastrowid or 0)
    logger.info("Recorded attempt %d (%s, %.1fs)", attempt_id, mode.value, audio_seconds)
    return attempt_id


def attach_coaching(
    conn: sqlite3.Connection,
    attempt_id: int,
    *,
    gemini_raw: Any,
    coach_source: str,
) -> None:
    """Attach the coaching response to an existing attempt.

    Called once per attempt, right after `ai_coach.coach` returns — whichever coach wrote
    the report. The columns have existed since schema version 1, so this was always an
    UPDATE over rows already recorded rather than a migration.
    """
    conn.execute(
        "UPDATE attempts SET gemini_raw_json = ?, coach_source = ? WHERE id = ?",
        (json.dumps(gemini_raw, ensure_ascii=False) if gemini_raw is not None else None,
         coach_source, attempt_id),
    )
    conn.commit()


def record_tts_usage(
    conn: sqlite3.Connection, *, characters: int, voice: str | None = None,
    created_at: str | None = None,
) -> None:
    """Charge the TTS meter. Unused until the TTS chunk lands."""
    conn.execute(
        "INSERT INTO tts_usage (created_at, characters, voice) VALUES (?, ?, ?)",
        (created_at or utc_now_iso(), int(characters), voice),
    )
    conn.commit()


# --- Meters -------------------------------------------------------------------------------
# Derived from the attempts table rather than a separate .usage.json. One store cannot
# disagree with itself, and the file would have been a second thing to keep in sync.


def monthly_stt_seconds(conn: sqlite3.Connection, when: datetime | None = None) -> float:
    """Audio seconds sent to Azure this UTC month. Offline replays are excluded."""
    row = conn.execute(
        "SELECT COALESCE(SUM(audio_seconds), 0.0) AS total FROM attempts "
        "WHERE offline = 0 AND created_at LIKE ?",
        (f"{month_prefix(when)}-%",),
    ).fetchone()
    return float(row["total"])


def monthly_tts_characters(conn: sqlite3.Connection, when: datetime | None = None) -> int:
    """Characters synthesised this UTC month."""
    row = conn.execute(
        "SELECT COALESCE(SUM(characters), 0) AS total FROM tts_usage "
        "WHERE created_at LIKE ?",
        (f"{month_prefix(when)}-%",),
    ).fetchone()
    return int(row["total"])


def recent_attempts(conn: sqlite3.Connection, limit: int = 10) -> Sequence[sqlite3.Row]:
    """Most recent attempts, newest first. Raw JSON columns are omitted — they are large."""
    return conn.execute(
        """
        SELECT id, created_at, mode, reference_text, recognised_text, audio_seconds,
               pron_score, accuracy, fluency, completeness, prosody, coach_source, offline
        FROM attempts ORDER BY id DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()


def get_attempt(conn: sqlite3.Connection, attempt_id: int) -> sqlite3.Row | None:
    """One full attempt row, raw JSON columns included."""
    return conn.execute("SELECT * FROM attempts WHERE id = ?", (attempt_id,)).fetchone()


# --- Readers for the progress view ----------------------------------------------------------
# Both exclude `offline = 1` deliberately. An OFFLINE_MODE run replays the same committed
# fixture every time, so its scores are a constant; thirty identical points is not a
# trajectory, and the words it flags are the fixture's, not the speaker's.
#
# Both also order by `created_at`, not by `id` as `recent_attempts` does. A time series has
# to be ordered by its timestamp — `record_attempt` accepts an explicit `created_at`, so id
# order and chronological order are not the same thing. `idx_attempts_created_at` backs it.


def attempt_series(conn: sqlite3.Connection) -> Sequence[sqlite3.Row]:
    """Every real attempt, oldest first, without the raw JSON. The progress chart's input."""
    return conn.execute(
        """
        SELECT id, created_at, mode, reference_text, recognised_text, audio_seconds,
               pron_score, accuracy, fluency, completeness, prosody, coach_source, offline
        FROM attempts WHERE offline = 0 ORDER BY created_at, id
        """
    ).fetchall()


def attempt_payloads(conn: sqlite3.Connection) -> Sequence[sqlite3.Row]:
    """Every real attempt with its verbatim Azure payload, oldest first.

    Separate from `attempt_series` because these blobs are 45-170 kB each: the score chart
    never needs them, and only the phoneme/word aggregation pays for reading them.
    """
    return conn.execute(
        """
        SELECT id, created_at, mode, reference_text, azure_raw_json
        FROM attempts WHERE offline = 0 ORDER BY created_at, id
        """
    ).fetchall()


def attempt_fingerprint(conn: sqlite3.Connection) -> tuple[int, int]:
    """(highest attempt id, row count) — a cheap cache key for the re-parsed aggregates.

    Re-parsing every stored payload on every Streamlit rerun is not affordable, and both tab
    bodies render on each of the 0.4 s poll reruns during an assessment. This is what
    `app.py` keys its `@st.cache_data` on: it changes exactly when a new attempt lands.
    """
    row = conn.execute(
        "SELECT COALESCE(MAX(id), 0) AS top, COUNT(*) AS total FROM attempts WHERE offline = 0"
    ).fetchone()
    return int(row["top"]), int(row["total"])


# --- The practice queue ---------------------------------------------------------------------
# SQL only. Every rule about *what* gets promoted, *when* it is due and *whether* it has
# graduated lives in `practice_queue.py`, which is pure and testable without a database.
# Keeping the two apart is the same split `progress_view.py` has against this module.

ACTIVE = "active"
GRADUATED = "graduated"


def upsert_target(
    conn: sqlite3.Connection,
    *,
    item: str,
    kind: str,
    evidence: Any,
    added: str | None = None,
    next_due: str | None = None,
    state: str = ACTIVE,
) -> int:
    """Add a target, or refresh the evidence on one that is already there.

    `(item, kind)` is unique, so re-running promotion after a new attempt updates the counts
    behind an existing target rather than creating a duplicate — and it deliberately leaves
    `added`, `state`, `next_due` and `reviews_passed` alone, because re-reading the same
    evidence is not a reason to reset an item's schedule or un-graduate it.
    """
    when = added or utc_now_iso()
    payload = json.dumps(evidence, ensure_ascii=False)
    conn.execute(
        """
        INSERT INTO practice_targets (item, kind, added, next_due, state, evidence)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(item, kind) DO UPDATE SET evidence = excluded.evidence
        """,
        (item, kind, when, next_due or when, state, payload),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id FROM practice_targets WHERE item = ? AND kind = ?", (item, kind)
    ).fetchone()
    return int(row["id"])


def targets(conn: sqlite3.Connection, state: str | None = None) -> Sequence[sqlite3.Row]:
    """Every target, oldest first, optionally filtered to one state."""
    if state is None:
        return conn.execute(
            "SELECT * FROM practice_targets ORDER BY added, id"
        ).fetchall()
    return conn.execute(
        "SELECT * FROM practice_targets WHERE state = ? ORDER BY added, id", (state,)
    ).fetchall()


def update_target(
    conn: sqlite3.Connection,
    target_id: int,
    *,
    state: str | None = None,
    next_due: str | None = None,
    last_seen: str | None = None,
    reviews_passed: int | None = None,
) -> None:
    """Apply a scheduling decision. Only the fields given are written."""
    sets: list[str] = []
    values: list[Any] = []
    for column, value in (
        ("state", state), ("next_due", next_due),
        ("last_seen", last_seen), ("reviews_passed", reviews_passed),
    ):
        if value is not None:
            sets.append(f"{column} = ?")
            values.append(value)
    if not sets:
        return
    values.append(target_id)
    conn.execute(f"UPDATE practice_targets SET {', '.join(sets)} WHERE id = ?", values)
    conn.commit()


def remove_target(conn: sqlite3.Connection, target_id: int) -> None:
    """Drop a target. Its trials keep their own `item` string, so the history survives it."""
    conn.execute("UPDATE perception_trials SET target_id = NULL WHERE target_id = ?",
                 (target_id,))
    conn.execute("DELETE FROM practice_targets WHERE id = ?", (target_id,))
    conn.commit()


def record_trial(
    conn: sqlite3.Connection,
    *,
    block_id: str,
    target_id: int | None,
    item: str,
    word: str,
    voice: str,
    novel: bool,
    alternatives: int,
    answered: str,
    correct: bool,
    review: bool = False,
    created_at: str | None = None,
) -> None:
    """Store one answered trial, as it is answered rather than at the end of the block.

    Writing per answer means an abandoned block still leaves its evidence behind. Whether
    that block *counts* toward graduation is a separate question, decided in
    `practice_queue` from the trial count — the evidence is kept either way.
    """
    conn.execute(
        """
        INSERT INTO perception_trials (
            block_id, target_id, created_at, item, word, voice, novel,
            alternatives, answered, correct, review
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (block_id, target_id, created_at or utc_now_iso(), item, word, voice,
         int(novel), int(alternatives), answered, int(correct), int(review)),
    )
    conn.commit()


def trials_for(conn: sqlite3.Connection, item: str) -> Sequence[sqlite3.Row]:
    """Every trial ever answered for one item, oldest first."""
    return conn.execute(
        "SELECT * FROM perception_trials WHERE item = ? ORDER BY created_at, id", (item,)
    ).fetchall()


def all_trials(conn: sqlite3.Connection) -> Sequence[sqlite3.Row]:
    """Every trial, oldest first. The perception chart's input."""
    return conn.execute(
        "SELECT * FROM perception_trials ORDER BY created_at, id"
    ).fetchall()


def heard_stimuli(conn: sqlite3.Connection, item: str) -> dict[tuple[str, str], str]:
    """`(word, voice) -> when it was last played`, for one item.

    This is what makes "unseen" mean something: a block prefers combinations absent from
    this map, and falls back to the least recently heard once they run out.
    """
    rows = conn.execute(
        "SELECT word, voice, MAX(created_at) AS last FROM perception_trials "
        "WHERE item = ? GROUP BY word, voice",
        (item,),
    ).fetchall()
    return {(str(r["word"]), str(r["voice"])): str(r["last"]) for r in rows}


def queue_fingerprint(conn: sqlite3.Connection) -> tuple[int, int, int]:
    """(target count, highest target id, trial count) — a cache key for the Today tab.

    Streamlit runs every tab body on every rerun, the 0.4 s assessment polls included, so
    the queue's reads need the same treatment `attempt_fingerprint` gives the progress view.
    """
    targets_row = conn.execute(
        "SELECT COUNT(*) AS total, COALESCE(MAX(id), 0) AS top FROM practice_targets"
    ).fetchone()
    trials_row = conn.execute("SELECT COUNT(*) AS total FROM perception_trials").fetchone()
    return int(targets_row["total"]), int(targets_row["top"]), int(trials_row["total"])
