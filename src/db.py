"""Local SQLite history: one row per attempt, holding both raw API responses verbatim.

Why verbatim: the normalised shape this app renders is a lossy projection chosen for
today's UI. Keeping exactly what Azure and Gemini returned means a later change of mind
about what to surface is a re-parse of stored rows, not a re-recording that spends quota
again.

**Audio is now kept too, since v0.10.0.** This used to say the brief ruled it out; that
stopped being true on 2026-08-19, when the "no stored audio" rule was lifted — recordings
may be kept locally, never committed, with the path and hash in the database. Two surfaces
need it, for exactly the reason the raw payloads are kept: History replays an attempt's own
recording months later, and `audio_utils.slice_wav` cuts one word out of it at Azure's own
offsets so "how I said it" can sit beside the native rendering. Neither may require that the
passage be read again. The bytes live under a gitignored directory and only the path and
digest are stored here (see `attempt_audio`).

**Several tables here have no writer any more.** `practice_targets`, `perception_trials`,
`attempt_tags`, `attempt_content_scores`, `speaker_baseline`, `vowel_measurements` and
`native_renderings` belonged to features deleted on 2026-08-25 (tag `v0.12.0-full`). Their
DDL stays and their rows stay: they hold real recorded evidence — calibration reads, answered
perception trials — and dropping them is unrecoverable in a way that removing the code is
not. `user_version` does not move.

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

-- Gemini's prosody annotation of one attempt's passage: the same words, marked up with stress,
-- phrase boundaries and linking. Added 2026-08-25 when Gemini stopped writing the coaching.
--
-- **Its own table rather than a reuse of `gemini_raw_json`.** That column now holds the
-- deterministic coaching report with `coach_source = 'fallback'`, and rows written before this
-- date hold a Gemini *CoachingReport* there. Writing an annotation into the same column would
-- make one column mean three things and make every stored row ambiguous to re-read.
--
-- Verbatim, like every other raw payload here, so changing what the UI renders is a re-parse
-- rather than another call. Additive: `user_version` never moves.
CREATE TABLE IF NOT EXISTS attempt_annotations (
  attempt_id  INTEGER PRIMARY KEY REFERENCES attempts(id) ON DELETE CASCADE,
  raw_json    TEXT    NOT NULL,
  created_at  TEXT    NOT NULL
);
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


def attach_coaching(
    conn: sqlite3.Connection,
    attempt_id: int,
    *,
    gemini_raw: Any,
    coach_source: str,
) -> None:
    """Attach the coaching report to an existing attempt.

    Called once per attempt, right after the coach runs. The columns have existed since
    schema version 1, so this was always an UPDATE over rows already recorded rather than a
    migration.

    **`coach_source` still matters even though there is only one coach now.** As of
    2026-08-25 every new row is written `'fallback'` — Gemini no longer writes coaching, it
    writes the prosody annotation, which lives in `attempt_annotations`. Rows written before
    that date carry `'gemini'` and hold a Gemini-authored report in `gemini_raw_json`. Both
    shapes are the same `CoachingReport` schema and both still re-read; the column is what
    tells History which coach a row's advice came from.
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


# --- Stored recordings -------------------------------------------------------------------------
# Added in v0.10.0 for the accent measurement, which is gone. Two surfaces need it now: History
# replays an attempt's own recording months later, and `audio_utils.slice_wav` cuts one word out
# of it at Azure's own offsets so "how I said it" can sit beside the native rendering.


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


def attach_annotation(
    conn: sqlite3.Connection,
    attempt_id: int,
    *,
    raw: Any,
    created_at: str | None = None,
) -> None:
    """Store Gemini's prosody annotation for an attempt. Idempotent per attempt.

    Only ever called with an annotation that already passed `ai_coach.validated`, so a row
    here is one whose word sequence matched the passage. Re-annotating replaces it rather
    than accumulating, because there is one right answer per passage and the newest model
    output is the one worth keeping.
    """
    conn.execute(
        """
        INSERT INTO attempt_annotations (attempt_id, raw_json, created_at)
        VALUES (?, ?, ?)
        ON CONFLICT(attempt_id) DO UPDATE SET
            raw_json = excluded.raw_json, created_at = excluded.created_at
        """,
        (int(attempt_id), json.dumps(raw, ensure_ascii=False), created_at or utc_now_iso()),
    )
    conn.commit()


def annotation_for(conn: sqlite3.Connection, attempt_id: int) -> Any | None:
    """One attempt's stored annotation payload, or None. Never raises on a bad row."""
    row = conn.execute(
        "SELECT raw_json FROM attempt_annotations WHERE attempt_id = ?", (int(attempt_id),)
    ).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["raw_json"])
    except (ValueError, TypeError):
        logger.warning("Attempt %d has an unreadable stored annotation", attempt_id)
        return None


# --- The History page --------------------------------------------------------------------------
# Ordered by `id DESC`, not `created_at`: `record_attempt` accepts an explicit `created_at`, so
# the two orders are not the same, and History is a list of what was recorded rather than a time
# series. `idx_attempts_created_at` is left in place for the date filter a later reader may want.
#
# **`offline = 1` rows are INCLUDED here**, unlike the progress readers this replaced. A fixture
# replay is a real row that a real click produced; hiding it made History disagree with the
# database. The surface labels them instead.


def attempt_page(
    conn: sqlite3.Connection,
    *,
    limit: int,
    offset: int = 0,
    mode: str | None = None,
) -> Sequence[sqlite3.Row]:
    """One page of attempts, newest first. Raw JSON columns omitted — they are large.

    `mode` filters on the stored string rather than on a `Mode`, deliberately: rows written
    before 2026-08-25 carry `'drill'`, which is no longer an enum member. The caller passes
    `utils.Mode.PARAGRAPH.value` and gets scripted rows; legacy rows are picked up by the
    `IN` list below rather than being stranded outside every filter.
    """
    sql = """
        SELECT id, created_at, mode, reference_text, recognised_text, audio_seconds,
               pron_score, accuracy, fluency, completeness, prosody, coach_source, offline
        FROM attempts
    """
    params: list[Any] = []
    if mode is not None:
        names = _mode_group(mode)
        sql += f" WHERE mode IN ({', '.join('?' * len(names))})"
        params.extend(names)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    params.extend([int(limit), int(offset)])
    return conn.execute(sql, params).fetchall()


def attempt_count(conn: sqlite3.Connection, *, mode: str | None = None) -> int:
    """How many attempts a page listing would walk. Same filter as `attempt_page`."""
    if mode is None:
        row = conn.execute("SELECT COUNT(*) AS total FROM attempts").fetchone()
    else:
        names = _mode_group(mode)
        row = conn.execute(
            f"SELECT COUNT(*) AS total FROM attempts WHERE mode IN ({', '.join('?' * len(names))})",
            names,
        ).fetchone()
    return int(row["total"])


def _mode_group(mode: str) -> list[str]:
    """Every stored `mode` string that reads as `mode` today, legacy values included.

    `'drill'` was scripted single-shot, so it belongs with `'paragraph'`. Derived from
    `utils.mode_of` rather than hard-coded a second time, so adding a legacy alias there
    cannot leave this filter silently behind.
    """
    target = utils.mode_of(mode)
    return sorted(
        {
            name
            for name in (*(m.value for m in Mode), *utils.LEGACY_MODE_NAMES)
            if utils.mode_of(name) is target
        }
    )


def delete_attempt(conn: sqlite3.Connection, attempt_id: int) -> str | None:
    """Delete one attempt and everything hanging off it. Returns the audio path, if any.

    The recording FILE is not deleted here — this module owns rows, not the filesystem, and
    a delete that half-succeeded because a file was locked would leave a row pointing at
    audio that may or may not exist. The path comes back so the caller can unlink it after
    the transaction commits, and a failure there is a stray file rather than a broken row.

    The cascades do the rest: `attempt_audio`, `attempt_annotations`, `attempt_tags` and
    `attempt_content_scores` all declare ON DELETE CASCADE and `connect` turns foreign keys
    on. `vowel_measurements` rows for the attempt go the same way.
    """
    row = conn.execute(
        "SELECT path FROM attempt_audio WHERE attempt_id = ?", (int(attempt_id),)
    ).fetchone()
    conn.execute("DELETE FROM attempts WHERE id = ?", (int(attempt_id),))
    conn.commit()
    return str(row["path"]) if row is not None else None
