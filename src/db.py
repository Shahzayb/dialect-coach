"""Local SQLite history: one row per attempt, holding both raw API responses verbatim.

Why verbatim: the normalised shape this app renders is a lossy projection chosen for
today's UI. Keeping exactly what Azure and Gemini returned means a later change of mind
about what to surface is a re-parse of stored rows, not a re-recording that spends quota
again.

**Audio is now kept too, since v0.10.0.** This used to say the brief ruled it out; that
stopped being true on 2026-08-19, when the "no stored audio" rule was lifted — recordings
may be kept locally, never committed, with the path and hash in the database. The accent
measurement is the first feature that needs it, for exactly the reason the raw payloads are
kept: a changed normalisation scheme or reference table has to be a re-derivation, and a
re-derivation must never require that the passage be read again. The bytes live under a
gitignored directory and only the path and digest are stored here (see `attempt_audio`).

This module never imports Streamlit, so tests and scripts can use it. `app.py` is
responsible for wrapping `connect()` in `@st.cache_resource`: Streamlit re-runs the whole
script on every widget interaction, and reopening the connection each time is the trap.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import utils
from shadowing import SHADOW_TAG
from utils import Mode

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS attempts (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at       TEXT    NOT NULL,   -- UTC ISO-8601, always 'Z'-suffixed
  mode             TEXT    NOT NULL,   -- drill | paragraph | unscripted
  -- The text for a scripted mode. For unscripted it is the PROMPT: nothing is scored against
  -- it, but it is what pairs two recordings into a spontaneous calibration, and it is the only
  -- thing that makes a Mode C row readable in the history table.
  reference_text   TEXT,
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

-- How an attempt was produced, when it was produced in some way other than reading the text
-- cold. A separate table rather than a column on `attempts`: that table is created with
-- CREATE TABLE IF NOT EXISTS, so a new column would need a real ALTER TABLE and `_migrate`
-- has no upgrade path. Additive, exactly like the two tables above, so an existing
-- version-1 database gains it on the next connect() and `user_version` never moves.
--
-- The other candidate was a marker prefixed onto `reference_text`, the way the TTS rhythm
-- baseline capture is marked. It is not reusable here: the shadowed-versus-cold comparison
-- pairs two attempts BY MATCHING that text, so a marker in it would break the very match
-- the feature depends on.
CREATE TABLE IF NOT EXISTS attempt_tags (
  attempt_id  INTEGER NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
  tag         TEXT    NOT NULL,   -- 'shadowed' is the only value written today
  created_at  TEXT    NOT NULL,
  PRIMARY KEY (attempt_id, tag)
);

CREATE INDEX IF NOT EXISTS idx_attempt_tags_tag ON attempt_tags(tag);

-- Vocabulary/grammar/topic for one unscripted attempt, or the stated reason there are none.
--
-- **Its own table, not a column on `attempts`, for exactly the reason written above
-- `attempt_tags`**: that table is created with CREATE TABLE IF NOT EXISTS and `_migrate` has no
-- upgrade path, so a new column would need a real ALTER TABLE. Additive here, so an existing
-- database gains it on the next connect() and `user_version` never moves.
--
-- `source` is not decoration and is never derivable later. These numbers do not come from Azure
-- — content assessment was retired from the Speech SDK at 1.46.0 and this project pins 1.51.1 —
-- so they are Gemini's reading of the transcript against Microsoft's own published rubric, and
-- a stored score whose provenance was forgotten is a score that will eventually be presented as
-- Azure's. `unavailable` rows are stored too, with their reason: "we asked and could not get
-- one, because X" is a fact about the attempt, and re-rendering it must not mean re-asking.
CREATE TABLE IF NOT EXISTS attempt_content_scores (
  attempt_id  INTEGER PRIMARY KEY REFERENCES attempts(id) ON DELETE CASCADE,
  created_at  TEXT    NOT NULL,
  source      TEXT    NOT NULL,   -- 'gemini' | 'azure' | 'unavailable'
  vocabulary  REAL,               -- NULL when unavailable; never 0.0 standing in for absent
  grammar     REAL,
  topic       REAL,
  payload     TEXT    NOT NULL    -- the scores plus their notes and reason, verbatim
);

-- Where an attempt's recording is on disk. The bytes are NOT in the database: a WAV per
-- attempt would grow the file past the point where the WAL and the verbatim payloads are
-- comfortable, and a file on disk is what parselmouth and ffmpeg both want anyway.
--
-- Content-addressed by the SHA-256 `attempts.audio_sha256` already stores, so re-reading the
-- same passage twice with byte-identical audio writes one file and two rows. Additive, like
-- every table below the first: `user_version` never moves.
CREATE TABLE IF NOT EXISTS attempt_audio (
  attempt_id   INTEGER PRIMARY KEY REFERENCES attempts(id) ON DELETE CASCADE,
  path         TEXT    NOT NULL,   -- relative to AUDIO_DIR's parent; never committed
  sha256       TEXT    NOT NULL,
  bytes        INTEGER NOT NULL,
  sample_rate  INTEGER NOT NULL,
  created_at   TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_attempt_audio_sha ON attempt_audio(sha256);

-- The calibrated speaker: where their vowels sit, and how far that wanders on its own.
--
-- One row is current and the rest are history — `superseded_at` is NULL on exactly one. Kept
-- rather than overwritten because a re-calibration changes the space every stored measurement
-- is expressed in, and a chart drawn last month has to stay explicable.
--
-- `noise_floor_json` is the reason the calibration passage is read TWICE. The per-vowel
-- displacement between two reads taken in one sitting, with no learning possible in between,
-- IS the measurement noise floor. Without it the progress view renders microphone placement
-- as progress.
CREATE TABLE IF NOT EXISTS speaker_baseline (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at      TEXT    NOT NULL,
  positions_json  TEXT    NOT NULL,  -- per-vowel means in Hz and z, with token counts
  normaliser_json TEXT    NOT NULL,  -- the Lobanov centroid these z-scores are relative to
  noise_floor_json TEXT   NOT NULL,  -- per-vowel displacement between the two reads
  lpc_ceiling_hz  REAL    NOT NULL,  -- the sweep's winner; every later reading reuses it
  reference_set   TEXT    NOT NULL,  -- 'men' | 'women'. Never an average of the two.
  style_tag       TEXT    NOT NULL,  -- 'read' | 'spontaneous'
  tokens          INTEGER NOT NULL,
  attempt_ids     TEXT    NOT NULL,  -- JSON array: the two calibration attempts
  superseded_at   TEXT               -- NULL on the current row, set when replaced
);

CREATE INDEX IF NOT EXISTS idx_speaker_baseline_current
  ON speaker_baseline(superseded_at);

-- One row per vowel token, accepted or rejected.
--
-- **Raw measurements, never only derived positions.** Normalisation schemes and reference
-- tables will change; re-deriving must be a query over these rows, never a re-recording.
-- That is also why `f3_*`, `rms_dbfs` and `stressed` are columns from day one even though
-- nothing reads some of them until v0.11.0 — a column costs nothing and a re-recording is
-- impossible for anything already spoken.
--
-- `lpc_ceiling_hz` and `snr_db_min` travel on every row so an old row stays interpretable
-- after a re-calibration moves the ceiling, and so a reading taken in a bad room can be
-- excluded later rather than silently averaged in.
CREATE TABLE IF NOT EXISTS vowel_measurements (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  attempt_id      INTEGER NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
  vowel           TEXT    NOT NULL,   -- Azure's IPA
  word            TEXT,
  word_index      INTEGER NOT NULL,
  start_s         REAL    NOT NULL,
  duration_ms     REAL    NOT NULL,
  f1_20 REAL, f2_20 REAL, f3_20 REAL,
  f1_50 REAL, f2_50 REAL, f3_50 REAL,
  f1_80 REAL, f2_80 REAL, f3_80 REAL,
  rms_dbfs        REAL,               -- dBFS: comparable WITHIN a recording, never across
  f0_hz           REAL,
  stressed        INTEGER,            -- 1/0 from CMUdict; NULL when the word did not align
  stress_digit    INTEGER,            -- 0 reduced, 1 primary, 2 secondary
  azure_score     REAL,
  coda_voiceless  INTEGER,            -- NULL when no consonant follows inside the word
  snr_db_min      REAL,
  lpc_ceiling_hz  REAL    NOT NULL,
  style_tag       TEXT    NOT NULL,
  accepted        INTEGER NOT NULL,
  rejected_reason TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_vowel_measurements_attempt
  ON vowel_measurements(attempt_id);
CREATE INDEX IF NOT EXISTS idx_vowel_measurements_vowel
  ON vowel_measurements(vowel, accepted);

-- One neural voice reading one text, synthesised and then assessed through the SAME pipeline
-- that measures the user. This is what a pitch overlay and a corrected-pitch resynthesis are
-- drawn against, and what the General American vowel reference is built from.
--
-- **Assessed, not just synthesised, and that is the expensive half.** Azure's synthesiser will
-- report word boundaries for free during synthesis, which would have been cheaper — but those
-- offsets come from the synthesiser's own clock, and the user's come from the recogniser. Two
-- contours anchored on two different segmenters are not aligned, they are approximately
-- aligned, and timing error is one of the things being measured. So the model's rendering goes
-- back through pronunciation assessment and both sides carry offsets from one segmenter.
--
-- Bought once per (voice, text) and kept forever: the audio in the gitignored audio/ directory
-- like every other recording, the payload here. Re-deriving a reference table must never mean
-- re-spending, for the same reason re-deriving a measurement must never mean re-recording.
CREATE TABLE IF NOT EXISTS native_renderings (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  voice          TEXT    NOT NULL,   -- the exact en-US neural voice, never inferred later
  text_key       TEXT    NOT NULL,   -- tts.cache_key: one definition of "the same text"
  reference_text TEXT    NOT NULL,
  wav_path       TEXT    NOT NULL,   -- never committed
  payloads_json  TEXT    NOT NULL,   -- the raw Azure assessment, verbatim
  seconds        REAL    NOT NULL,   -- what it cost in STT allowance
  characters     INTEGER NOT NULL,   -- what it cost in TTS allowance
  created_at     TEXT    NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_native_renderings_identity
  ON native_renderings(voice, text_key);
CREATE INDEX IF NOT EXISTS idx_native_renderings_text ON native_renderings(text_key);
"""


def utc_now_iso() -> str:
    """UTC, second precision, 'Z'-suffixed — so string comparison is chronological."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def month_prefix(when: datetime | None = None) -> str:
    """'YYYY-MM' for the UTC month `when` falls in. The meter's bucket key."""
    moment = when or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%Y-%m")


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


def tag_attempt(
    conn: sqlite3.Connection, attempt_id: int, tag: str, *, created_at: str | None = None
) -> None:
    """Mark how an attempt was produced. Idempotent — re-tagging the same row is a no-op.

    Written in the same transaction as `record_attempt` by the caller that knows how the
    audio was made, because an untagged shadowed read is indistinguishable from a cold one
    afterwards and would land on the trajectory the tag exists to keep it off.
    """
    conn.execute(
        "INSERT OR IGNORE INTO attempt_tags (attempt_id, tag, created_at) VALUES (?, ?, ?)",
        (int(attempt_id), tag, created_at or utc_now_iso()),
    )
    conn.commit()


def tags_for(conn: sqlite3.Connection, attempt_id: int) -> set[str]:
    """Every tag on one attempt."""
    rows = conn.execute(
        "SELECT tag FROM attempt_tags WHERE attempt_id = ?", (int(attempt_id),)
    ).fetchall()
    return {str(row["tag"]) for row in rows}


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
        (
            json.dumps(gemini_raw, ensure_ascii=False) if gemini_raw is not None else None,
            coach_source,
            attempt_id,
        ),
    )
    conn.commit()


def attach_content_score(
    conn: sqlite3.Connection,
    attempt_id: int,
    *,
    scores: Any,
    created_at: str | None = None,
) -> None:
    """Store one attempt's content scores, or the stated reason it has none.

    `scores` is a `content_score.Scores`; it is passed structurally rather than imported so
    `db` keeps knowing nothing about the coaching layer. Replaces on re-score — an attempt has
    one content verdict, and a second one supersedes rather than accumulates.
    """
    conn.execute(
        """
        INSERT OR REPLACE INTO attempt_content_scores (
            attempt_id, created_at, source, vocabulary, grammar, topic, payload
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(attempt_id),
            created_at or utc_now_iso(),
            scores.source,
            scores.vocabulary,
            scores.grammar,
            scores.topic,
            json.dumps(scores.to_json(), ensure_ascii=False),
        ),
    )
    conn.commit()


def content_score_for(conn: sqlite3.Connection, attempt_id: int) -> dict[str, Any] | None:
    """The stored content verdict for one attempt, as the payload it was written from."""
    row = conn.execute(
        "SELECT payload FROM attempt_content_scores WHERE attempt_id = ?", (int(attempt_id),)
    ).fetchone()
    if row is None:
        return None
    loaded: dict[str, Any] = json.loads(row["payload"])
    return loaded


def record_tts_usage(
    conn: sqlite3.Connection,
    *,
    characters: int,
    voice: str | None = None,
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
        "SELECT COALESCE(SUM(characters), 0) AS total FROM tts_usage WHERE created_at LIKE ?",
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
    row = conn.execute("SELECT * FROM attempts WHERE id = ?", (attempt_id,)).fetchone()
    return cast("sqlite3.Row | None", row)


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
        SELECT a.id, a.created_at, a.mode, a.reference_text, a.recognised_text,
               a.audio_seconds, a.pron_score, a.accuracy, a.fluency, a.completeness,
               a.prosody, a.coach_source, a.offline,
               EXISTS (SELECT 1 FROM attempt_tags t
                       WHERE t.attempt_id = a.id AND t.tag = ?) AS shadowed
        FROM attempts a WHERE a.offline = 0 ORDER BY a.created_at, a.id
        """,
        (SHADOW_TAG,),
    ).fetchall()


def attempt_payloads(conn: sqlite3.Connection) -> Sequence[sqlite3.Row]:
    """Every real attempt with its verbatim Azure payload, oldest first.

    Separate from `attempt_series` because these blobs are 45-170 kB each: the score chart
    never needs them, and only the phoneme/word aggregation pays for reading them.
    """
    return conn.execute(
        """
        SELECT a.id, a.created_at, a.mode, a.reference_text, a.azure_raw_json,
               EXISTS (SELECT 1 FROM attempt_tags t
                       WHERE t.attempt_id = a.id AND t.tag = ?) AS shadowed
        FROM attempts a WHERE a.offline = 0 ORDER BY a.created_at, a.id
        """,
        (SHADOW_TAG,),
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
        return conn.execute("SELECT * FROM practice_targets ORDER BY added, id").fetchall()
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
        ("state", state),
        ("next_due", next_due),
        ("last_seen", last_seen),
        ("reviews_passed", reviews_passed),
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
    conn.execute("UPDATE perception_trials SET target_id = NULL WHERE target_id = ?", (target_id,))
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
        (
            block_id,
            target_id,
            created_at or utc_now_iso(),
            item,
            word,
            voice,
            int(novel),
            int(alternatives),
            answered,
            int(correct),
            int(review),
        ),
    )
    conn.commit()


def trials_for(conn: sqlite3.Connection, item: str) -> Sequence[sqlite3.Row]:
    """Every trial ever answered for one item, oldest first."""
    return conn.execute(
        "SELECT * FROM perception_trials WHERE item = ? ORDER BY created_at, id", (item,)
    ).fetchall()


def all_trials(conn: sqlite3.Connection) -> Sequence[sqlite3.Row]:
    """Every trial, oldest first. The perception chart's input."""
    return conn.execute("SELECT * FROM perception_trials ORDER BY created_at, id").fetchall()


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


# --- Audio, baselines and vowel measurements -------------------------------------------------
# Added in v0.10.0. SQL only, like everything else here: what a baseline MEANS and when it is
# stale is `vowel_measure`'s business, and the split is the same one `practice_queue` has.


def record_audio(
    conn: sqlite3.Connection,
    attempt_id: int,
    *,
    path: str,
    sha256: str,
    size_bytes: int,
    sample_rate: int,
    created_at: str | None = None,
) -> None:
    """Remember where an attempt's recording is. Idempotent per attempt.

    The row is written in the same transaction as the attempt it belongs to, so no reader can
    ever see an attempt whose audio location has not landed yet — the same rule `tag_attempt`
    follows for provenance.
    """
    conn.execute(
        """
        INSERT INTO attempt_audio (attempt_id, path, sha256, bytes, sample_rate, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(attempt_id) DO UPDATE SET
            path = excluded.path, sha256 = excluded.sha256, bytes = excluded.bytes
        """,
        (
            int(attempt_id),
            path,
            sha256,
            int(size_bytes),
            int(sample_rate),
            created_at or utc_now_iso(),
        ),
    )
    conn.commit()


def audio_for(conn: sqlite3.Connection, attempt_id: int) -> sqlite3.Row | None:
    """Where one attempt's recording is, or None when it was not kept."""
    row = conn.execute(
        "SELECT * FROM attempt_audio WHERE attempt_id = ?", (int(attempt_id),)
    ).fetchone()
    return cast("sqlite3.Row | None", row)


def stored_audio(conn: sqlite3.Connection) -> Sequence[sqlite3.Row]:
    """Every kept recording, oldest first. What a re-derivation pass walks."""
    return conn.execute(
        """
        SELECT a.attempt_id, a.path, a.sha256, a.bytes, t.mode, t.reference_text,
               t.azure_raw_json, t.created_at
        FROM attempt_audio a JOIN attempts t ON t.id = a.attempt_id
        ORDER BY t.created_at, a.attempt_id
        """
    ).fetchall()


def record_vowel_measurements(
    conn: sqlite3.Connection, attempt_id: int, rows: Sequence[dict[str, Any]]
) -> int:
    """Store one attempt's vowel tokens, replacing any already there.

    Replacing rather than appending: re-deriving an attempt's measurements after a change to
    the pipeline must leave one set of rows, not two generations interleaved.
    """
    conn.execute("DELETE FROM vowel_measurements WHERE attempt_id = ?", (int(attempt_id),))
    conn.executemany(
        """
        INSERT INTO vowel_measurements (
            attempt_id, vowel, word, word_index, start_s, duration_ms,
            f1_20, f2_20, f3_20, f1_50, f2_50, f3_50, f1_80, f2_80, f3_80,
            rms_dbfs, f0_hz, stressed, stress_digit, azure_score, coda_voiceless,
            snr_db_min, lpc_ceiling_hz, style_tag, accepted, rejected_reason
        ) VALUES (
            :attempt_id, :vowel, :word, :word_index, :start_s, :duration_ms,
            :f1_20, :f2_20, :f3_20, :f1_50, :f2_50, :f3_50, :f1_80, :f2_80, :f3_80,
            :rms_dbfs, :f0_hz, :stressed, :stress_digit, :azure_score, :coda_voiceless,
            :snr_db_min, :lpc_ceiling_hz, :style_tag, :accepted, :rejected_reason
        )
        """,
        [{**row, "attempt_id": int(attempt_id)} for row in rows],
    )
    conn.commit()
    return len(rows)


def vowel_measurements_for(conn: sqlite3.Connection, attempt_id: int) -> Sequence[sqlite3.Row]:
    """Every token measured for one attempt, in the order it was spoken."""
    return conn.execute(
        "SELECT * FROM vowel_measurements WHERE attempt_id = ? ORDER BY start_s, id",
        (int(attempt_id),),
    ).fetchall()


def vowel_measurement_series(
    conn: sqlite3.Connection, *, style_tag: str | None = None
) -> Sequence[sqlite3.Row]:
    """Accepted tokens across every real attempt, oldest first — the trend query's input.

    Filters `offline = 1` for the reason every other progress reader does: a replayed fixture
    is a constant, and thirty identical points is not a trajectory.

    **`style_tag` is not optional in spirit even though it defaults to None.** Read speech is
    hyperarticulated and spontaneous speech is systematically more centralised; pooling the two
    makes a change of register look like a regression toward the middle of the vowel space.
    Every trend surface passes one.
    """
    sql = """
        SELECT v.*, a.created_at
        FROM vowel_measurements v JOIN attempts a ON a.id = v.attempt_id
        WHERE a.offline = 0 AND v.accepted = 1
    """
    params: list[Any] = []
    if style_tag is not None:
        sql += " AND v.style_tag = ?"
        params.append(style_tag)
    return conn.execute(sql + " ORDER BY a.created_at, v.id", params).fetchall()


def save_baseline(
    conn: sqlite3.Connection,
    *,
    positions: Any,
    normaliser: Any,
    noise_floor: Any,
    lpc_ceiling_hz: float,
    reference_set: str,
    style_tag: str,
    tokens: int,
    attempt_ids: Sequence[int],
    created_at: str | None = None,
) -> int:
    """Store a new baseline and retire the one it replaces.

    Both in one transaction: a moment with two current baselines, or none, would make every
    z-score on screen ambiguous about which space it is in.
    """
    when = created_at or utc_now_iso()
    with conn:
        # **Scoped to this style.** Read speech and spontaneous speech are different
        # populations — vowels centralise and unstressed syllables collapse further toward
        # schwa the moment a speaker generates language instead of reading it — so each style
        # has its own current baseline and neither ever retires or averages into the other.
        # Superseding across styles would mean the first Mode C calibration silently deleted
        # the read baseline every Mode B reading is normalised against.
        conn.execute(
            "UPDATE speaker_baseline SET superseded_at = ? "
            "WHERE superseded_at IS NULL AND style_tag = ?",
            (when, style_tag),
        )
        cursor = conn.execute(
            """
            INSERT INTO speaker_baseline (
                created_at, positions_json, normaliser_json, noise_floor_json,
                lpc_ceiling_hz, reference_set, style_tag, tokens, attempt_ids
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                when,
                json.dumps(positions, ensure_ascii=False),
                json.dumps(normaliser, ensure_ascii=False),
                json.dumps(noise_floor, ensure_ascii=False),
                float(lpc_ceiling_hz),
                reference_set,
                style_tag,
                int(tokens),
                json.dumps(list(attempt_ids)),
            ),
        )
    return int(cursor.lastrowid or 0)


def current_baseline(conn: sqlite3.Connection, *, style: str) -> sqlite3.Row | None:
    """The baseline in force **for one speech style**, or None if that style has none yet.

    `style` is required and has no default. A default would be a guess about which population a
    reading belongs to, and getting it wrong normalises spontaneous speech against a read
    centroid — which makes a change of register look like a regression toward the middle of the
    vowel space. There are up to two current rows, one per style; asking without saying which
    is not a question this table can answer.
    """
    row = conn.execute(
        "SELECT * FROM speaker_baseline WHERE superseded_at IS NULL AND style_tag = ? "
        "ORDER BY id DESC LIMIT 1",
        (style,),
    ).fetchone()
    return cast("sqlite3.Row | None", row)


def any_current_baseline(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """The oldest current baseline of any style. **Used for the LPC ceiling and nothing else.**

    The ceiling has to match vocal tract length, which is a property of the speaker and not of
    whether they happen to be reading or talking. It is established once by a sweep and then
    held still, because a reading measured at a different ceiling is not comparable to the
    baseline it is set against — so a later spontaneous calibration reuses the ceiling the read
    calibration already settled rather than sweeping to a second, incompatible one. Oldest
    first, deliberately: the first sweep is the one everything else was measured against.

    Never use this to pick a normaliser. That is `current_baseline(conn, style=...)`, and the
    whole point of the split is that a centroid IS style-specific even though a ceiling is not.
    """
    row = conn.execute(
        "SELECT * FROM speaker_baseline WHERE superseded_at IS NULL ORDER BY id ASC LIMIT 1"
    ).fetchone()
    return cast("sqlite3.Row | None", row)


def baseline_history(conn: sqlite3.Connection) -> Sequence[sqlite3.Row]:
    """Every baseline ever set, newest first. A re-calibration is a fact worth keeping."""
    return conn.execute("SELECT * FROM speaker_baseline ORDER BY id DESC").fetchall()


# --- The model's own readings -----------------------------------------------------------------


def record_native_rendering(
    conn: sqlite3.Connection,
    *,
    voice: str,
    text_key: str,
    reference_text: str,
    wav_path: str,
    payloads: Any,
    seconds: float,
    characters: int,
    created_at: str | None = None,
) -> None:
    """Store one voice's assessed reading of one text. Idempotent per (voice, text).

    Upserts rather than inserting, so a re-capture after a better voice list or a longer
    passage replaces the row instead of leaving two readings that disagree about what the
    model does.
    """
    conn.execute(
        """
        INSERT INTO native_renderings
            (voice, text_key, reference_text, wav_path, payloads_json, seconds, characters,
             created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(voice, text_key) DO UPDATE SET
            reference_text = excluded.reference_text,
            wav_path = excluded.wav_path,
            payloads_json = excluded.payloads_json,
            seconds = excluded.seconds,
            characters = excluded.characters,
            created_at = excluded.created_at
        """,
        (
            voice,
            text_key,
            reference_text,
            wav_path,
            json.dumps(payloads, ensure_ascii=False),
            float(seconds),
            int(characters),
            created_at or utc_now_iso(),
        ),
    )
    conn.commit()


def native_rendering(conn: sqlite3.Connection, voice: str, text_key: str) -> sqlite3.Row | None:
    """One voice's reading of one text, or None when it has not been captured."""
    row = conn.execute(
        "SELECT * FROM native_renderings WHERE voice = ? AND text_key = ?",
        (voice, text_key),
    ).fetchone()
    return cast("sqlite3.Row | None", row)


def native_renderings_for(conn: sqlite3.Connection, text_key: str) -> Sequence[sqlite3.Row]:
    """Every captured voice for one text, by voice name. The population a band is drawn from."""
    return conn.execute(
        "SELECT * FROM native_renderings WHERE text_key = ? ORDER BY voice",
        (text_key,),
    ).fetchall()


def native_rendering_voices(conn: sqlite3.Connection, text_key: str) -> set[str]:
    """Which voices already exist for this text. What makes a capture run resumable."""
    return {
        str(row["voice"])
        for row in conn.execute(
            "SELECT voice FROM native_renderings WHERE text_key = ?", (text_key,)
        ).fetchall()
    }
