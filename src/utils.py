"""Shared foundations: configuration, thresholds, hashing, logging, retry policy.

Everything here is deliberately free of Streamlit and of the Azure/Gemini SDKs, so tests
and one-off scripts can import it without a UI runtime or a network stack. The one
exception is `load_config`, which *optionally* reads `st.secrets` behind a guarded import.
"""

from __future__ import annotations

import hashlib
import logging
import os
import random
import re
import time
from collections.abc import Callable, Iterable
from enum import Enum
from typing import Any, Protocol

from dotenv import load_dotenv

logger = logging.getLogger(__name__)


class RowLike(Protocol):
    """A database row as its readers actually use one: subscript by column name.

    `sqlite3.Row` and `dict` both satisfy this and neither is a subtype of the other.
    A Row is not a `Mapping` — it has no `.get`, and it raises `IndexError` where a dict
    raises `KeyError`, so a reader that handles a missing column has to catch both. Readers
    that only subscript should say only that, rather than claiming `Mapping[str, Any]` and
    being handed a `sqlite3.Row` anyway.
    """

    def __getitem__(self, key: str, /) -> Any: ...


class Mode(str, Enum):
    """The two recording modes: read a script, or speak freely.

    UNSCRIPTED measures the register this project is about — generating language and
    monitoring pronunciation at the same time. It is not a longer PARAGRAPH; it is a
    different speech population, assessed by a different path.

    **`DRILL` was removed on 2026-08-25 and its value must still be readable.** Single-shot
    recognition was the only path Azure honoured `enableMiscue` on, but it caps at roughly
    15 s of audio, which is incompatible with reading something as long as you like. Rows
    written before the cut carry `mode = 'drill'`; `mode_of` below maps them onto PARAGRAPH
    so History renders them. Stored rows are never rewritten.
    """

    PARAGRAPH = "paragraph"
    UNSCRIPTED = "unscripted"


# What a stored `mode` string meant, for values the enum no longer has. `drill` was scripted
# single-shot; a legacy row is a scripted row. Anything unrecognised is read as scripted too:
# an unreadable history row is worse than one shown under the wrong-but-adjacent label.
_LEGACY_MODES: dict[str, Mode] = {"drill": Mode.PARAGRAPH}

# Exported so `db._mode_group` can build a History filter that catches legacy rows without
# hard-coding the same list a second time and letting the two drift.
LEGACY_MODE_NAMES: tuple[str, ...] = tuple(_LEGACY_MODES)


def mode_of(value: str | Mode | None) -> Mode:
    """The `Mode` a stored `attempts.mode` string names, legacy values included.

    Read history through this, never through `Mode(value)` — that raises on `'drill'` and
    takes the whole History page down with it.
    """
    if isinstance(value, Mode):
        return value
    text = str(value or "").strip().lower()
    try:
        return Mode(text)
    except ValueError:
        return _LEGACY_MODES.get(text, Mode.PARAGRAPH)


# --- Colour thresholds -------------------------------------------------------------------
# Heuristics chosen for this tool, NOT values Azure defines or endorses. Azure returns a
# 0-100 score and says nothing about where "bad" starts; these are the cut points the UI
# colours against, kept here so there is exactly one place to retune them.
WORD_RED = 80.0  # below this: red
WORD_AMBER = 95.0  # below this: amber, at or above: green
PHONEME_RED = 60.0
PHONEME_AMBER = 85.0


class Band(str, Enum):
    """Which colour band a score falls in.

    `NONE` is not a fourth severity — it means there is no score to band at all. An omitted
    word has no accuracy because it was never spoken, which is a different thing from having
    scored badly, and colouring it red would claim Azure judged something it never heard.
    """

    RED = "red"
    AMBER = "amber"
    GREEN = "green"
    NONE = "none"


def score_band(score: float | None, red: float, amber: float) -> Band:
    """Band one score against a red/amber cut pair. Below `red` is red, below `amber` amber."""
    if score is None:
        return Band.NONE
    if score < red:
        return Band.RED
    if score < amber:
        return Band.AMBER
    return Band.GREEN


def word_band(score: float | None) -> Band:
    """Band a word accuracy score. Kept here so the thresholds have exactly one reader."""
    return score_band(score, WORD_RED, WORD_AMBER)


def phoneme_band(score: float | None) -> Band:
    """Band a phoneme accuracy score. Phonemes are cut lower than words deliberately."""
    return score_band(score, PHONEME_RED, PHONEME_AMBER)


# --- Azure's own score interpretation -----------------------------------------------------
# Unlike WORD_RED/WORD_AMBER/PHONEME_RED/PHONEME_AMBER above — which are heuristics this
# project chose — these four cut points (0-59 / 60-79 / 80-89 / 90-100) are Azure's own
# published convention for pron/accuracy/fluency/prosody scores. Do not merge the two ideas:
# this band applies only to those four scores, never to a word or phoneme accuracy score.
AZURE_LOW_CUT = 60.0
AZURE_FAIR_CUT = 80.0
AZURE_GOOD_CUT = 90.0


class AzureBand(str, Enum):
    """Azure's own qualitative band for a pron/accuracy/fluency/prosody score."""

    LOW = "low"  # 0-59
    FAIR = "fair"  # 60-79
    GOOD = "good"  # 80-89
    EXCELLENT = "excellent"  # 90-100
    NONE = "none"  # no score to band — never render as LOW


def azure_score_band(score: float | None) -> AzureBand:
    """Band a pron/accuracy/fluency/prosody score against Azure's own cut points."""
    if score is None:
        return AzureBand.NONE
    if score < AZURE_LOW_CUT:
        return AzureBand.LOW
    if score < AZURE_FAIR_CUT:
        return AzureBand.FAIR
    if score < AZURE_GOOD_CUT:
        return AzureBand.GOOD
    return AzureBand.EXCELLENT


# How many times a paid call may be sent before giving up. Named here because the budget
# guard has to price the worst case (every attempt reaches the provider and is charged),
# not the lucky case, and the guard and the retry loop must not drift apart.
MAX_SYNTHESIS_ATTEMPTS = 3

# Reference text limits. Azure aligns words against the reference, and a very long one
# both costs alignment quality and is not a realistic single attempt.
MAX_REFERENCE_CHARS = 1000

# Names of the environment variables whose values must never reach a log line.
SECRET_VARS = ("AZURE_SPEECH_KEY", "GEMINI_API_KEY")

# Below this length a "secret" is more likely to be an ordinary word, and redacting it
# would gut every log line it appears in.
MIN_REDACTABLE_SECRET_LENGTH = 8

_REQUIRED_VARS = ("AZURE_SPEECH_KEY", "AZURE_SPEECH_REGION")

_DEFAULTS: dict[str, str] = {
    "GEMINI_MODEL": "gemini-3.6-flash",
    "AZURE_TTS_VOICE": "en-US-BrianNeural",
    "DB_PATH": "./data/coach.db",
    # The floor, and no ceiling. Per-mode maximums were removed on 2026-08-25: a read should
    # be as long as the thing being read. Nothing is lost by removing them — a recording is
    # billed for its own seconds either way, and `budget.py` meters what was actually spent.
    "MIN_DURATION_SECONDS": "1.5",
    "UNSCRIPTED_TWO_PASS": "true",
    "OFFLINE_MODE": "false",
    "MONTHLY_BUDGET_USD": "0.00",
    "AZURE_TIER_CONFIRMED_F0": "false",
    "AZURE_FREE_STT_SECONDS": "18000",
    "AZURE_FREE_TTS_CHARS": "500000",
    "AZURE_STT_USD_PER_HOUR": "1.00",
    "AZURE_PRON_ADDON_USD_PER_HOUR": "0.30",
    "AZURE_TTS_USD_PER_MILLION_CHARS": "16.00",
    "GEMINI_USD_PER_MTOK_IN": "0.00",
    "GEMINI_USD_PER_MTOK_OUT": "0.00",
    # --- Stored recordings -----------------------------------------------------------
    # Keep every recording. `projectbrief.md` permits this — the "no stored audio" rule was
    # lifted on 2026-08-19 — and two surfaces need it: History replays an old attempt months
    # later, and `audio_utils.slice_wav` cuts one word out of it at Azure's own offsets so
    # "how I said it" can sit beside the native rendering. Roughly 2.9 MB per 90-second read,
    # so a hundred reads is about 290 MB. Set false to go back to deleting.
    "KEEP_AUDIO": "true",
    "AUDIO_DIR": "./audio/attempts",
}

_TRUTHY = {"1", "true", "yes", "on"}

_dotenv_loaded = False


class ConfigError(RuntimeError):
    """A required setting is missing or unusable. Never carries a secret's value."""


def _load_dotenv_once() -> None:
    global _dotenv_loaded
    if not _dotenv_loaded:
        load_dotenv()
        _dotenv_loaded = True


def _from_streamlit_secrets(name: str) -> str | None:
    """Read one key from `st.secrets`, or None if Streamlit isn't usable here.

    Streamlit Community Cloud injects secrets this way; Hugging Face Spaces and Docker
    both use environment variables instead. Accessing `st.secrets` outside a Streamlit
    runtime (in pytest, in a script) raises, so every failure mode collapses to None.
    """
    try:
        import streamlit as st

        value = st.secrets[name]
    except Exception:  # noqa: BLE001 — no Streamlit, no secrets file, no key: all mean None
        return None
    return str(value) if value else None


def get(name: str, default: str | None = None) -> str | None:
    """Resolve one setting: os.environ, then st.secrets, then .env, then the default."""
    value = os.environ.get(name)
    if value:
        return value

    value = _from_streamlit_secrets(name)
    if value:
        return value

    _load_dotenv_once()
    value = os.environ.get(name)
    if value:
        return value

    if default is not None:
        return default
    return _DEFAULTS.get(name)


def require(name: str) -> str:
    """Resolve a setting that has no usable default, or raise naming the missing key.

    The message names the variable and never its value, so it is safe to render in the UI.
    """
    value = get(name)
    if not value:
        raise ConfigError(
            f"{name} is not set. Add it to .env (see .env.example), or export it before "
            f"starting the app."
        )
    return value


def get_bool(name: str) -> bool:
    return (get(name) or "").strip().lower() in _TRUTHY


def get_float(name: str) -> float:
    raw = get(name)
    if raw is None:
        raise ConfigError(f"{name} is not set and has no default.")
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}.") from exc


def get_int(name: str) -> int:
    return int(get_float(name))


def offline_mode() -> bool:
    """True when every network call is replaced by a replay of the committed fixture."""
    return get_bool("OFFLINE_MODE")


def check_required() -> list[str]:
    """Return the names of required settings that are missing. Offline needs none."""
    if offline_mode():
        return []
    return [name for name in _REQUIRED_VARS if not get(name)]


def attempt_hash(reference_text: str, audio_bytes: bytes, mode: Mode) -> str:
    """SHA-256 over the mode, the reference text and the audio — the session cache key.

    `mode` has to be part of the digest: the same recording is a legitimate thing to assess
    scripted and then unscripted, and the two go through different Azure code paths — one
    assesses against the typed reference, the other transcribes first and assesses against
    the transcript — producing different result shapes. Without `mode` in the key, switching
    modes and re-assessing identical (text, audio) would hit the other mode's cached entry
    and silently show a stale result — no error, no re-assessment, and a `CachedAttempt.mode`
    that disagrees with what is on screen.
    """
    digest = hashlib.sha256()
    digest.update(mode.value.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(reference_text.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(audio_bytes)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def truncate(text: str, limit: int) -> str:
    """`text` cut to `limit` characters on a word boundary, with an ellipsis when it was cut.

    Used for the History row labels: a 900-word paragraph and a 900-word unscripted
    transcript are both legitimate, and neither is a list entry.
    """
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    cut = cleaned[:limit].rsplit(" ", 1)[0]
    return f"{cut or cleaned[:limit]}…"


def normalise_words(text: str) -> list[str]:
    """Lowercase, punctuation-stripped word list — the basis for reference/heard diffs."""
    return re.findall(r"[a-z0-9']+", text.lower())


# --- Logging -----------------------------------------------------------------------------


class SecretRedactingFormatter(logging.Formatter):
    """Format a record, then scrub secrets from the *rendered* text, traceback included.

    A filter cannot do this: filters run before handlers format, so `record.exc_text` is
    still None at filter time and the traceback is rendered afterwards, unscrubbed. An SDK
    exception whose repr embeds the subscription key would otherwise reach the log in full.
    """

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        for secret in _current_secrets():
            if secret and len(secret) >= MIN_REDACTABLE_SECRET_LENGTH:
                rendered = rendered.replace(secret, SecretRedactingFilter.PLACEHOLDER)
        return rendered


class SecretRedactingFilter(logging.Filter):
    """Replace any configured secret's value with a placeholder before it is emitted.

    Belt and braces: no call site is supposed to log a key, but a third-party SDK logging
    a request URL or an exception repr is outside our control, and a leaked key in a
    surfaced traceback is a real failure mode (master plan acceptance criterion 7).
    """

    PLACEHOLDER = "***redacted***"

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        # Short values would redact half the log; a real key is long.
        self._secrets = sorted(
            {s for s in secrets if s and len(s) >= MIN_REDACTABLE_SECRET_LENGTH},
            key=len,
            reverse=True,
        )

    def _scrub(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, self.PLACEHOLDER)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True
        # Render once here rather than trusting every handler's formatter to do it, so
        # args interpolated later cannot smuggle a key past the filter.
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 — a log filter that raises would lose the record
            message = str(record.msg)
        scrubbed = self._scrub(message)
        if scrubbed != message:
            record.msg = scrubbed
            record.args = ()
        if record.exc_text:
            record.exc_text = self._scrub(record.exc_text)
        return True


def redact(text: str) -> str:
    """Scrub known secret values out of a string bound for a log line or the UI."""
    return SecretRedactingFilter(_current_secrets())._scrub(text)


def _current_secrets() -> list[str]:
    return [value for value in (get(name) for name in SECRET_VARS) if value]


def configure_logging(level: int = logging.INFO) -> None:
    """Install a root handler with secret redaction. Idempotent across Streamlit reruns."""
    root = logging.getLogger()
    redactor = SecretRedactingFilter(_current_secrets())

    for existing in root.handlers:
        if any(isinstance(f, SecretRedactingFilter) for f in existing.filters):
            return

    handler = logging.StreamHandler()
    # The formatter scrubs the rendered text (tracebacks included); the filter scrubs the
    # message before any other handler can see it. Both, because either alone leaves a gap.
    handler.setFormatter(
        SecretRedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    handler.addFilter(redactor)
    root.addHandler(handler)
    root.setLevel(level)


# --- Retry -------------------------------------------------------------------------------


class TransientError(RuntimeError):
    """Worth retrying: the service was busy, timed out, or the connection failed."""


class PermanentError(RuntimeError):
    """Not worth retrying: a bad key, an exhausted quota, or a malformed request."""


def retry_transient[T](
    fn: Callable[[], T],
    *,
    attempts: int = MAX_SYNTHESIS_ATTEMPTS,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    sleep: Callable[[float], None] = time.sleep,
    on_attempt: Callable[[int], None] | None = None,
) -> T:
    """Call `fn`, retrying only `TransientError` with exponential backoff plus jitter.

    `PermanentError` propagates on the first raise. Retrying a 401 just burns time, and
    retrying a 403 quota response can consume more allowance for no benefit.

    `on_attempt` is called with the attempt number before each try. Callers that pay per
    call need it: a retry re-sends the same audio and can consume allowance even when it
    ultimately fails, so the meter has to count attempts rather than successes.
    """
    last: TransientError | None = None
    for attempt in range(1, attempts + 1):
        if on_attempt is not None:
            on_attempt(attempt)
        try:
            return fn()
        except TransientError as exc:
            last = exc
            if attempt == attempts:
                break
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            delay += random.uniform(0, delay / 2)  # jitter: avoid lockstep retries
            logger.warning(
                "Transient failure (attempt %d/%d), retrying in %.1fs: %s",
                attempt,
                attempts,
                delay,
                exc,
            )
            sleep(delay)
    assert last is not None
    raise last
