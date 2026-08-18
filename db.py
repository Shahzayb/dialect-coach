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
