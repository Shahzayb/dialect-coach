"""Streamlit entry point for the pronunciation coach.

UI only. Every API call lives in `speech_analyzer` and `tts`, every write in `db`, every
spend decision in `budget` — this file orchestrates them and renders the result.

The rendering aims at one thing: making the diagnosis legible, audible and actionable.
Colour-coded reference text, the reference-vs-heard diff, expected → produced IPA per
flagged word, the delivery panel, "Hear it" playback against your own recording, and the
coaching report on top of all of it.

The coaching report is always present and always free: `fallback_coach` builds it from the
Azure data alone. Asking Gemini to improve on it is a button, not a side effect of
assessing — a click is the point at which anything is sent to Google, and the point at
which the free tier's daily allowance is spent.
"""

from __future__ import annotations

import difflib
import html
import json
import logging
import sqlite3
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

import streamlit as st

import accent_view
import acoustics
import ai_coach
import audio_utils
import budget
import db
import fallback_coach
import perception_trainer
import phoneme_reference
import practice_queue
import progress_view
import rhythm
import shadowing
import speech_analyzer
import tts
import utils
import vowel_measure
import vowel_reference
from utils import AzureBand, Band, Mode

logger = logging.getLogger(__name__)

PAGE_TITLE = "Pronunciation Coach"
PAGE_ICON = "🗣️"

# Cap on the session cache. Streamlit re-runs the entire script on every widget
# interaction, so without this a single click on any control would re-run the whole Azure
# pipeline on audio that was already assessed. Cross-session deduplication is a side
# benefit; preventing rerun storms is the actual requirement.
CACHE_LIMIT = 10

# Synthesised audio is orders of magnitude larger than the assessment JSON, so this cache
# holds fewer entries — still far more than one drill sentence's worth of flagged words.
TTS_CACHE_LIMIT = 24

# How long the script pauses before re-running to check on a background assessment. Each
# rerun is a full render, so this trades a little latency on finishing for not spinning the
# CPU while Azure works.
JOB_POLL_SECONDS = 0.4

# The assessment now runs on a worker thread, so the cached connection can be touched from
# two threads at once: the worker's INSERT against the meter reads at the foot of every
# rerun. `check_same_thread=False` and WAL make that safe at the SQLite level; this makes it
# safe at the `sqlite3.Connection` level, which is not documented to be thread-safe itself.
_DB_LOCK = threading.Lock()

MODE_LABELS: dict[str, Mode] = {
    "Drill — one or two sentences": Mode.DRILL,
    "Paragraph — connected speech": Mode.PARAGRAPH,
}

# Chosen to read on both the light and the dark Streamlit theme, and to survive being set
# as a text colour rather than a background — a hardcoded background would fight whichever
# theme the viewer is actually using.
BAND_COLOURS: dict[Band, str] = {
    Band.RED: "#d6455d",
    Band.AMBER: "#c07f16",
    Band.GREEN: "#2f8f63",
    Band.NONE: "#8a8a8a",
}

# Azure's own pron/accuracy/fluency/prosody bands (utils.AzureBand) — a different
# convention from BAND_COLOURS above, which colours word/phoneme accuracy against this
# project's own heuristics. LOW and FAIR intentionally reuse the same red/amber as
# BAND_COLOURS so "bad" reads the same everywhere; GOOD is a distinct step between amber
# and green because Azure's convention, unlike the word/phoneme one, has four bands.
AZURE_BAND_COLOURS: dict[AzureBand, str] = {
    AzureBand.LOW: "#d6455d",
    AzureBand.FAIR: "#c07f16",
    AzureBand.GOOD: "#6fa83f",
    AzureBand.EXCELLENT: "#2f8f63",
    AzureBand.NONE: "#8a8a8a",
}

# What Azure's prosody feedback means in words. The raw names are accurate but say nothing
# to someone trying to fix their delivery. Used in the per-word tooltip and the detailed
# delivery panel; the headline error-count badges use ERROR_BADGE_LABELS instead, which are
# short enough to sit next to a number.
DELIVERY_LABELS: dict[str, str] = {
    "UnexpectedBreak": "Paused in the middle of a phrase",
    "MissingBreak": "Ran two phrases together with no pause",
    "Monotone": "Flat intonation across the span",
}

# Short labels + colours for the headline error-count badges (#10/#12) — distinct from
# DELIVERY_LABELS' longer prose, which explains a fault rather than naming it.
ERROR_BADGES: list[tuple[str, str | None, str]] = [
    # (badge label, delivery_summary() key or None for the mispronunciation count, colour)
    ("Mispronunciations", None, "#c07f16"),
    ("Unexpected break", "UnexpectedBreak", "#d6455d"),
    ("Missing break", "MissingBreak", "#8a8a8a"),
    ("Monotone", "Monotone", "#6a4fa0"),
]

# Chosen to load the sounds most likely to be substituted by Urdu/Punjabi L1 speakers
# (master plan §7): /θ/ /ð/, /v/ vs /w/, /æ/ vs /ɛ/, /ʃ/ /s/ /z/ /dʒ/, dark /l/, and final
# consonant clusters. No digits — Azure normalises "33" and "thirty-three" differently,
# which breaks word alignment.
PRESETS: dict[Mode, dict[str, str]] = {
    Mode.DRILL: {
        "Th (/θ/, /ð/)": "These three brothers thought the weather was worth the trouble.",
        "V versus W": "Very well, we will invite the whole village to the west wing.",
        "Short a versus short e (/æ/, /ɛ/)": (
            "That bad man had a red cap and a black pen in his hand."
        ),
        "Sibilants (/s/, /ʃ/, /z/, /dʒ/)": (
            "She chose the usual visual measure just as the season closed."
        ),
    },
    Mode.PARAGRAPH: {
        # First, and deliberately so. The progress view identifies a benchmark read by
        # matching this text, so it has to be selected rather than typed from memory — a
        # hand-typed near-copy would quietly start a second series.
        progress_view.BENCHMARK_TITLE: progress_view.BENCHMARK_PASSAGE,
        "Mixed diagnostic paragraph": (
            "There are three things I think about whenever I have to explain my work to "
            "someone else. The first is whether the other person actually needs the "
            "detail, or whether they would rather hear the result and move on. The "
            "second is that I tend to speak faster when I am nervous, which makes the "
            "ends of my words disappear. The third is that I value being understood far "
            "more than sounding clever. When I remember all three, the conversation goes "
            "well. When I forget them, I watch the listener's face change and I know I "
            "have lost them somewhere in the middle of a long sentence."
        ),
        "Workplace explanation": (
            "The problem was not that the tests failed. The problem was that they passed "
            "for the wrong reason, and nobody thought to check. We had been measuring "
            "whether the service responded at all, rather than whether it responded with "
            "the right thing. Those are very different questions. Once we changed what we "
            "measured, the same code that had looked healthy for months started failing "
            "immediately, which was uncomfortable but useful. I would rather find a bug "
            "on a Wednesday afternoon than have a customer find it for me on a weekend."
        ),
    },
}


@st.cache_resource
def get_connection() -> sqlite3.Connection:
    """One connection for the whole session.

    Cached because Streamlit re-runs this script on every interaction; reopening SQLite
    each time is the trap. `db.connect` sets check_same_thread=False for the same reason —
    reruns happen on worker threads that outlive no single connection.
    """
    utils.configure_logging()
    return db.connect()


# --- Session caches -------------------------------------------------------------------------


def lru_get(cache: OrderedDict[Any, Any], key: Any) -> Any | None:
    """Read an entry and mark it most-recently-used.

    Pure, so the eviction policy is testable without a Streamlit runtime.
    """
    if key not in cache:
        return None
    cache.move_to_end(key)
    return cache[key]


def lru_put(cache: OrderedDict[Any, Any], key: Any, value: Any, limit: int) -> None:
    """Store an entry, evicting the *least recently used* once over `limit`.

    LRU rather than insertion order because the drill loop re-uses one entry over and over:
    the same sentence assessed again, the same flagged word played again. Evicting by
    insertion order would drop exactly the entry being re-used, and pay to rebuild it.
    """
    cache[key] = value
    cache.move_to_end(key)
    while len(cache) > limit:
        cache.popitem(last=False)


def _session_cache(name: str) -> OrderedDict[Any, Any]:
    if name not in st.session_state:
        st.session_state[name] = OrderedDict()
    cache: OrderedDict[Any, Any] = st.session_state[name]
    return cache


@dataclass(frozen=True)
class CachedAttempt:
    """One assessed attempt, as the session remembers it.

    The reference text, the database row and the mode travel with the assessment because
    every widget that produced them is free to change underneath: the textarea can be
    edited and the mode radio flipped without re-running anything. Rendering the live
    widget values beside the previous attempt's scores would be a quiet lie, and the row id
    is what lets the coaching report be attached to the attempt it was written about.
    """

    key: str
    assessment: Any
    reference_text: str
    attempt_id: int | None
    mode: Mode
    # The vowel measurement for this attempt, or None when it could not be taken. It travels
    # here rather than being re-derived on render because it is expensive — a Burg analysis of
    # the whole recording — and Streamlit re-runs the script on every widget interaction.
    measurement: Any = None


@dataclass
class AssessOutcome:
    """What a background assessment ended up with. Never renders itself.

    `error` is an (icon, message) pair rather than a rendered alert for the same reason
    `play()` returns one: this is produced on a worker thread, where calling into Streamlit
    is unsupported. The main thread renders it once the job is collected.
    """

    assessment: Any = None
    attempt_id: int | None = None
    error: tuple[str, str] | None = None
    cancelled: bool = False
    reached_azure: bool = False
    measurement: Any = None


@dataclass
class AssessJob:
    """One assessment running off the script thread.

    Streamlit re-runs the whole script on every interaction but cannot interrupt a blocking
    call already in progress, so an assessment that ran inline would leave the page frozen
    with no way to render a Stop button, let alone act on it. The work therefore runs on a
    worker thread that touches no Streamlit API, and the script polls it.

    `outcome` is written exactly once, by the worker, immediately before it returns. The
    main thread reads it only after `thread.is_alive()` is False, so the two never race.
    """

    cancel_event: threading.Event
    key: str
    reference_text: str
    mode: Mode
    # How this recording was produced, when it was produced in some way other than reading
    # the text cold. Carried on the job rather than looked up afterwards: once the row is
    # written, a shadowed read is indistinguishable from a cold one, and an untagged one
    # lands on the trajectory the tag exists to keep it off.
    tags: tuple[str, ...] = ()
    thread: threading.Thread | None = None
    outcome: AssessOutcome | None = None

    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()


def _cache_get(key: str) -> CachedAttempt | None:
    return lru_get(_session_cache("assessments"), key)


def _cache_put(entry: CachedAttempt) -> None:
    lru_put(_session_cache("assessments"), entry.key, entry, CACHE_LIMIT)


# --- Pure rendering helpers -----------------------------------------------------------------
# Kept free of Streamlit so the diffing, banding and ordering can be tested directly rather
# than through a headless app run. The readers that answer "what did you actually produce"
# — `phoneme_pairs`, `is_flagged`, `delivery_summary` — live in `speech_analyzer` instead,
# because the coaching layer needs them and cannot import a module that pulls in Streamlit.


def severity_key(word: dict[str, Any]) -> tuple[int, float]:
    """Sort flagged words worst-first.

    Omissions lead: a word that was never spoken is a worse outcome than one merely scored
    badly, and it carries `accuracy: None`, which must not be allowed to sort as a zero
    score or to blow up the comparison against a float.
    """
    accuracy = word.get("accuracy")
    rank = 0 if (word.get("error_type") or "None") == "Omission" else 1
    return (rank, accuracy if isinstance(accuracy, (int, float)) else 0.0)


def _scored_full(word: dict[str, Any]) -> bool:
    """Whether a word scored full marks and was flagged for something other than accuracy.

    An omitted word carries `accuracy: None` and must never land here — it was never
    spoken, which is the opposite of a perfect score.
    """
    accuracy = word.get("accuracy")
    return isinstance(accuracy, (int, float)) and accuracy >= 100


def word_tooltip_html(word: dict[str, Any]) -> str:
    """The rich hover-tooltip content for one word (#13): a `word : score` header, then the
    phoneme breakdown as two aligned rows — symbols, then their scores — followed by why it
    was flagged.

    The phoneme rows are a quick-glance shape, deliberately different from
    `render_word_card`'s "expected → produced" substitution list further down the page:
    both read `speech_analyzer.phoneme_pairs`, the single source for what was actually
    produced, so the two views can never disagree about a phoneme's score even though they
    lay it out differently.
    """
    text = str(word.get("word") or "")
    accuracy = word.get("accuracy")
    score_text = f"{accuracy:.0f}" if isinstance(accuracy, (int, float)) else "not spoken"
    parts = [
        '<div style="font-weight:600;margin-bottom:0.35rem;">'
        f"{html.escape(text)} : {html.escape(score_text)}</div>"
    ]

    pairs = [
        (expected, score)
        for expected, _produced, score in speech_analyzer.phoneme_pairs(word)
        if expected
    ]
    if pairs:
        symbol_cells = "".join(
            f'<span style="color:{BAND_COLOURS[utils.phoneme_band(score)]};min-width:1.6rem;'
            f'display:inline-block;text-align:center;font-weight:600;">'
            f"{html.escape(symbol)}</span>"
            for symbol, score in pairs
        )
        score_cells = "".join(
            '<span style="min-width:1.6rem;display:inline-block;text-align:center;'
            f'opacity:0.8;">{f"{score:.0f}" if isinstance(score, (int, float)) else "—"}'
            "</span>"
            for _symbol, score in pairs
        )
        parts.append(f"<div>{symbol_cells}</div><div>{score_cells}</div>")

    notes = []
    error_type = word.get("error_type") or "None"
    if error_type != "None":
        # Says whose judgement it is: continuous mode ignores enableMiscue, so omissions
        # and insertions there are our diff, not Azure's.
        notes.append(f"{error_type} (flagged by {word.get('error_source') or 'azure'})")
    notes.extend(DELIVERY_LABELS.get(f, f) for f in word.get("delivery_error_types") or [])
    if notes:
        parts.append(
            f'<div style="margin-top:0.35rem;opacity:0.8;">{html.escape(" · ".join(notes))}</div>'
        )
    return "".join(parts)


def colour_coded_html(words: list[dict[str, Any]]) -> str:
    """The assessed words as one colour-coded block, each carrying a rich tooltip on hover.

    Built from the aligned word list rather than the raw reference string, because that is
    what carries the scores — so the original punctuation and capitalisation are not
    reproduced here. The verbatim reference stays visible in the diff panel above it.

    HTML rather than Streamlit's native `:red[…]` markdown because only markup can carry
    hover content, and §11/#13 ask for the score — now the full phoneme breakdown — on
    hover. A CSS `:hover` tooltip rather than a `title=` attribute because a native tooltip
    is one plain line and cannot lay out the phoneme/score rows #13's image asks for. The
    tooltip panel needs a real background to be legible, unlike the inline word colours
    elsewhere in this file (kept as text/border colours so they survive both themes without
    needing one). Streamlit 1.61.1 does not expose its theme as CSS custom properties
    anywhere in the DOM (checked live: `getComputedStyle` returns nothing for
    `--text-color`/`--secondary-background-color` on `body`, `.stApp`, or any Streamlit
    container), so this deliberately uses one fixed light card instead of chasing a
    variable that would never resolve — verified live in the browser on both the light and
    dark Streamlit themes. Both the word and the tooltip content are escaped: they
    originate in the reference textarea, which is arbitrary user input being interpolated
    into markup.
    """
    spans: list[str] = []
    for word in words:
        text = str(word.get("word") or "")
        if not text:
            continue
        colour = BAND_COLOURS[utils.word_band(word.get("accuracy"))]
        style = f"color:{colour};border-bottom:2px solid {colour};padding-bottom:1px;"
        error_type = word.get("error_type") or "None"
        if error_type == "Omission":
            # Never spoken, so it has no score to colour by — struck through instead.
            style += "text-decoration:line-through;opacity:0.7;"
        elif error_type == "Insertion":
            style += "font-style:italic;"
        spans.append(
            '<span class="pa-word-wrap" style="position:relative;display:inline-block;">'
            f'<span style="{style}">{html.escape(text)}</span>'
            f'<span class="pa-tooltip">{word_tooltip_html(word)}</span>'
            "</span>"
        )
    style_block = (
        "<style>"
        ".pa-tooltip{display:none;position:absolute;bottom:100%;left:0;z-index:10;"
        "background:#f0f2f6;color:#31333f;"
        "border:1px solid rgba(128,128,128,0.35);border-radius:6px;"
        "padding:0.5rem 0.65rem;box-shadow:0 2px 10px rgba(0,0,0,0.2);"
        "white-space:nowrap;font-size:0.85rem;line-height:1.5;margin-bottom:6px;}"
        ".pa-word-wrap:hover .pa-tooltip{display:block;}"
        "</style>"
    )
    return style_block + '<div style="line-height:2.6;">' + " ".join(spans) + "</div>"


def reference_vs_heard(reference_text: str, recognised_text: str) -> list[tuple[str, str]]:
    """Diff the script against what Azure actually heard, as (tag, word) pairs.

    If Azure heard something else entirely, that is the single most useful signal available
    and §11 requires it not be buried under per-phoneme scores. Tags are `same`, `missing`
    (in the script, never heard) and `extra` (heard, not in the script).

    Reuses `utils.normalise_words`, so this diff and the miscue diff in `speech_analyzer`
    agree on what counts as a word.
    """
    reference = utils.normalise_words(reference_text)
    heard = utils.normalise_words(recognised_text)

    pairs: list[tuple[str, str]] = []
    matcher = difflib.SequenceMatcher(a=reference, b=heard, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            pairs.extend(("same", word) for word in reference[i1:i2])
        else:
            pairs.extend(("missing", word) for word in reference[i1:i2])
            pairs.extend(("extra", word) for word in heard[j1:j2])
    return pairs


def diff_html(pairs: list[tuple[str, str]]) -> str:
    """Render a `reference_vs_heard` diff: struck-through for missing, italic for extra."""
    styles = {
        "same": "",
        "missing": f"color:{BAND_COLOURS[Band.RED]};text-decoration:line-through;",
        "extra": f"color:{BAND_COLOURS[Band.AMBER]};font-style:italic;",
    }
    spans = [
        f'<span style="{styles[tag]}" title="{tag}">{html.escape(word)}</span>'
        for tag, word in pairs
    ]
    return '<div style="line-height:2;">' + " ".join(spans) + "</div>"


def weakest_phoneme(word: dict[str, Any]) -> str:
    """One-line summary of a word's worst sound, for the card header."""
    pairs = [
        (expected, produced, score)
        for expected, produced, score in speech_analyzer.phoneme_pairs(word)
        if score is not None
    ]
    if not pairs:
        return ""
    expected, produced, score = min(pairs, key=lambda p: p[2])
    if produced:
        return f"/{expected}/ → sounded like /{produced}/"
    return f"/{expected}/ ({score:.0f})"


# --- Playback ---------------------------------------------------------------------------------


def play(
    conn: sqlite3.Connection, text: str, *, slow: bool, label: str, source: str
) -> tuple[str, str] | None:
    """Synthesise `text` unless it is already cached, then queue it for playback.

    Returns None on success, or an (icon, message) pair for the caller to render. It does
    **not** render the failure itself: every call site is inside a narrow `st.columns`
    entry, and an alert emitted here would be laid out at that column's width — a couple of
    hundred characters of error wrapped into ~120 pixels, one word per line. The caller
    renders it once the columns have closed.

    The cache lookup comes **before** the pre-flight and the usage record, and that order
    is the entire point. Streamlit re-runs this script top to bottom on every widget
    interaction, so metering ahead of the cache check would charge the TTS meter again on
    each unrelated click: the meter would climb steadily while nothing new was synthesised
    and nothing new was spent.
    """
    cache = _session_cache("tts_audio")
    key = (tts.voice_name(), text, slow)

    if lru_get(cache, key) is None:
        payload_characters = len(tts.payload_for(text, slow=slow))
        try:
            # Price what a *failing* call can cost, not what a lucky one does: the meter
            # below charges every attempt, so pricing a single attempt here would let the
            # guard approve a call whose real charge lands past the budget.
            budget.preflight_tts(conn, payload_characters * utils.MAX_SYNTHESIS_ATTEMPTS)
        except budget.BudgetError as exc:
            return ("💸", str(exc))

        attempts_made = 0

        def note_attempt(attempt: int) -> None:
            nonlocal attempts_made
            attempts_made = attempt

        try:
            with st.spinner(f"Synthesising {label}…"):
                result = tts.synthesise(text, slow=slow, on_attempt=note_attempt)
        except utils.ConfigError as exc:
            return ("🔑", str(exc))
        except (
            utils.PermanentError,
            utils.TransientError,
            tts.SynthesisError,
            speech_analyzer.AssessmentError,
        ) as exc:
            # AssessmentError belongs here even though this is synthesis: QuotaExhausted
            # subclasses it, and it is shared so that one 403 type drives the budget guard
            # for both paths. Leave it out and the branch below is unreachable.
            if speech_analyzer.is_quota_exhausted(exc):
                # Azure is authoritative; block the rest of the month regardless of meter.
                budget.mark_quota_exhausted()
            # A run that reached Azure and then failed still consumed allowance, so it is
            # metered here rather than only on the success path. Nothing reached Azure on
            # a ConfigError (caught above) or when no attempt was ever started.
            if attempts_made:
                db.record_tts_usage(
                    conn,
                    characters=payload_characters * attempts_made,
                    voice=tts.voice_name(),
                )
            # redact() rather than str(): SDK error details can echo request context.
            logger.error("Synthesis failed", exc_info=True)
            return ("🔇", utils.redact(str(exc)))

        db.record_tts_usage(
            conn,
            # Attempts, not successes: a retry re-sends the text and can consume allowance
            # even when it ultimately fails.
            characters=result.characters * max(result.attempts, 1),
            voice=result.voice,
        )
        lru_put(cache, key, result.audio, TTS_CACHE_LIMIT)

    # `source` identifies the widget that asked, so only that one renders the player.
    st.session_state["now_playing"] = {"key": key, "source": source}
    return None


def playback_buttons(conn: sqlite3.Connection, text: str, *, key_prefix: str, label: str) -> None:
    """A "Hear it" / "Hear it slowly" pair, plus the player for whichever was clicked.

    The player renders here rather than in one fixed place on the page, so the audio
    appears next to the word it belongs to. Keys are prefixed by the caller with the word's
    *index*: a paragraph repeats "the" and "that" constantly, and two buttons sharing a key
    is a hard Streamlit error, not a cosmetic one.
    """
    offline = utils.offline_mode()
    failure: tuple[str, str] | None = None

    left, right, _ = st.columns([1, 1, 3])
    with left:
        if st.button("🔊 Hear it", key=f"{key_prefix}-normal", disabled=offline, width="stretch"):
            failure = play(conn, text, slow=False, label=label, source=key_prefix)
    with right:
        if st.button("🐢 Slowly", key=f"{key_prefix}-slow", disabled=offline, width="stretch"):
            failure = play(conn, text, slow=True, label=label, source=key_prefix)

    # Rendered here, outside the columns, so a long message gets the full width instead of
    # the button's ~120 pixels.
    if failure is not None:
        icon, message = failure
        st.error(message, icon=icon)

    # Matched on the clicking widget, not on the text. A paragraph flags the same common
    # word more than once, and matching by text would render an autoplaying player in
    # every card sharing it — the same clip starting several times at once.
    playing = st.session_state.get("now_playing")
    if playing and playing.get("source") == key_prefix:
        audio = lru_get(_session_cache("tts_audio"), playing["key"])
        if audio:
            # autoplay so one click plays, rather than one click to synthesise and another
            # on the player. The click itself is lost to the rerun either way.
            st.audio(audio, format="audio/wav", autoplay=True)


# --- Startup and input -------------------------------------------------------------------------


# Explicit widget keys, because Reset and Delete write to these through session state.
TEXT_KEY = "reference_text"
PRESET_KEY = "preset_choice"

# Which surface produced the result currently in `last_key`. Two tabs can now start an
# assessment, and `last_key` is a single slot, so without this BOTH render it — Streamlit
# executes every tab body on every rerun, and `render_result` builds widget keys from the
# attempt, so the second render collides with the first on a duplicate key. Found live, not
# in a test: the offline suite never had a shadowed result and a Practice result at once.
RESULT_OWNER_KEY = "result_owner"
SHADOW_OWNER = "shadow"
PRACTICE_OWNER = "practice"


def _generation(name: str) -> int:
    """The current generation of a rebuilt widget's key.

    `st.audio_input` and `st.file_uploader` hold their own content and cannot be cleared by
    writing to `st.session_state`, so the only way to empty one is to give it a key it has
    never seen — which makes Streamlit build a fresh, empty widget. Bumping this counter is
    what does that.
    """
    return int(st.session_state.get(f"{name}_generation", 0))


def _bump(name: str) -> None:
    st.session_state[f"{name}_generation"] = _generation(name) + 1


def _apply_preset(mode: Mode) -> None:
    """Load the chosen preset into the textarea. Runs before the next render."""
    choice = str(st.session_state.get(PRESET_KEY) or "")
    st.session_state[TEXT_KEY] = PRESETS[mode].get(choice, "")


def _delete_recording() -> None:
    """Discard the take, keep everything else — the point is re-recording, not starting over."""
    _bump("recording")
    st.session_state["now_playing"] = None


def _reset_form() -> None:
    """Clear the whole surface back to a fresh start."""
    st.session_state[TEXT_KEY] = ""
    st.session_state[PRESET_KEY] = "Write my own"
    _bump("recording")
    _bump("upload")
    st.session_state["last_key"] = None
    st.session_state[RESULT_OWNER_KEY] = None
    st.session_state["now_playing"] = None


def _metric(label: str, value: float | None) -> None:
    # "—" rather than "0": a missing prosody score and a prosody score of zero mean
    # entirely different things, and showing 0 for "unavailable" is a lie.
    st.metric(label, f"{value:.0f}" if isinstance(value, (int, float)) else "—")


def check_startup() -> None:
    """Refuse to run on a configuration that could spend money or cannot work at all."""
    try:
        budget.require_f0_acknowledgement()
    except budget.TierNotAcknowledged as exc:
        st.error(str(exc), icon="🛑")
        st.stop()

    missing = utils.check_required()
    if missing:
        st.error(
            f"Missing required settings: {', '.join(missing)}. Add them to .env (see "
            f".env.example), or set OFFLINE_MODE=true to work from the committed fixture "
            f"without any API calls.",
            icon="🔑",
        )
        st.stop()


def validate_reference(text: str) -> bool:
    """Check the reference text before anything is sent. Returns False to block the run."""
    if not text.strip():
        st.error("Enter the text you are going to read first.")
        return False
    if len(text) > utils.MAX_REFERENCE_CHARS:
        st.error(
            f"That reference text is {len(text)} characters, over the "
            f"{utils.MAX_REFERENCE_CHARS} limit. Shorten it."
        )
        return False
    if any(char.isdigit() for char in text):
        # Not fatal, but it does degrade the result, so say why rather than just warning.
        st.warning(
            'The reference text contains digits. Azure normalises "33" and '
            '"thirty-three" differently, which can throw the word alignment off — '
            "spell numbers out for a cleaner result.",
            icon="⚠️",
        )
    return True


def prepare_audio(
    conn: sqlite3.Connection, audio: bytes, mode: Mode
) -> tuple[bytes, float] | tuple[None, None]:
    """Convert and price the recording. Returns (wav bytes, seconds), or (None, None).

    Stays on the script thread: both steps are local and fast, and both need to report
    their own failure immediately rather than through the job machinery.
    """
    try:
        wav_bytes, seconds = audio_utils.prepare(audio, mode)
    except audio_utils.AudioError as exc:
        st.error(str(exc), icon="🎙️")
        return None, None

    try:
        budget.preflight_stt(conn, seconds, mode)
    except budget.BudgetError as exc:
        st.error(str(exc), icon="💸")
        return None, None

    return wav_bytes, seconds


# --- The accent measurement ------------------------------------------------------------------

# The speech-style tag. Scripted modes are read speech; Mode C, when it lands in v0.12.0, is
# not. Derived from the mode rather than asked for, so it cannot be forgotten on an attempt.
STYLE_READ = "read"
STYLE_SPONTANEOUS = "spontaneous"


def style_for(mode: Mode) -> str:
    """Which measurement population an attempt belongs to."""
    return STYLE_SPONTANEOUS if mode is Mode.UNSCRIPTED else STYLE_READ


def measure_vowels(
    conn: sqlite3.Connection, assessment: Any, wav_bytes: bytes, mode: Mode
) -> Any | None:
    """Measure this recording's vowels, or return None. Never raises into the worker thread.

    A measurement failure must not cost the user the assessment they just paid Azure for, so
    every exit here is a value. `snr_db_min` comes from `overall_scores`, where
    `speech_analyzer._snr` already put it — the payload is not re-parsed.

    The ceiling comes from the stored baseline when there is one, so every later reading is
    measured in the same space the baseline was. With no baseline yet the sweep runs, which is
    what a calibration read wants.
    """
    if assessment.offline:
        # The offline fixture is a stored payload with no audio behind it. Its phoneme offsets
        # point into a recording that is not here, so slicing them would measure whatever the
        # user happened to record against the wrong transcript.
        return None
    try:
        baseline = db.current_baseline(conn)
        override = (utils.get("LPC_CEILING_HZ") or "").strip()
        ceiling: float | None = None
        if override:
            ceiling = float(override)
        elif baseline is not None:
            ceiling = float(baseline["lpc_ceiling_hz"])
        return vowel_measure.extract(
            assessment.words,
            wav_bytes,
            ceiling_hz=ceiling,
            snr_db_min=assessment.overall_scores.get("snr_db_min"),
            style=style_for(mode),
        )
    except Exception:
        logger.warning("Vowel measurement failed; the attempt is still recorded", exc_info=True)
        return None


def store_measurement(
    conn: sqlite3.Connection,
    attempt_id: int,
    wav_bytes: bytes,
    digest: str,
    measurement: Any,
) -> None:
    """Keep the recording and its tokens. Failure here costs re-derivation, never the attempt."""
    try:
        path = audio_utils.keep(wav_bytes, digest)
        if path is not None:
            db.record_audio(
                conn,
                attempt_id,
                path=str(path),
                sha256=digest,
                size_bytes=len(wav_bytes),
                sample_rate=audio_utils.TARGET_SAMPLE_RATE,
            )
        if measurement is not None:
            db.record_vowel_measurements(conn, attempt_id, vowel_measure.token_rows(measurement))
    except Exception:
        logger.warning("Could not store the recording or its measurements", exc_info=True)


def run_assessment_job(
    conn: sqlite3.Connection,
    wav_bytes: bytes,
    seconds: float,
    reference_text: str,
    mode: Mode,
    cancel_event: threading.Event,
    tags: tuple[str, ...] = (),
) -> AssessOutcome:
    """Assess and store, off the script thread. Renders nothing, raises nothing.

    Every exit is an `AssessOutcome`, including the unexpected ones: this runs on a worker
    thread, where an escaping exception kills the thread silently and would leave the page
    polling a job that never reports anything.

    **A cancelled run is never recorded and never metered.** The check below sits before
    `db.record_attempt`, so a stopped attempt writes no row — which is also what keeps it
    off the usage meter, since the meter is derived from the attempts table. That is a
    different question from the existing rule that a *completed* run counts every attempt
    it made, retries and failures included: those re-uploaded the audio for a result the
    user kept.
    """
    reached_azure = False

    def note_attempt(_attempt: int) -> None:
        nonlocal reached_azure
        reached_azure = True

    try:
        with audio_utils.temp_wav(wav_bytes) as wav_path:
            assessment = speech_analyzer.analyse(
                wav_path,
                reference_text,
                mode,
                cancel_event=cancel_event,
                on_attempt=note_attempt,
            )

        if cancel_event.is_set():
            # Drill cannot be interrupted mid-call, so a stop clicked while Azure was
            # answering lands here: the result arrived, and it is thrown away unrecorded.
            return AssessOutcome(cancelled=True, reached_azure=reached_azure)

        # The vowel measurement runs HERE, inside the assessment request, while `wav_bytes`
        # is still in memory. Recordings are kept since v0.10.0, so this could in principle be
        # a later pass — but the audio is already here and decoded, and a second pass would
        # re-do the work for nothing. What the keeping buys is RE-derivation: when the
        # normalisation scheme or the reference table changes, stored audio is re-measured
        # rather than the passage being read again.
        measurement = measure_vowels(conn, assessment, wav_bytes, mode)

        digest = utils.sha256_bytes(wav_bytes)
        with _DB_LOCK:
            # The tag is written under the same lock as the row it describes, so no reader can
            # ever see a stored attempt whose provenance has not landed yet.
            attempt_id = db.record_attempt(
                conn,
                mode=mode,
                reference_text=reference_text,
                recognised_text=assessment.recognised_text,
                # Attempts, not successes: a retry re-uploads the same audio. Offline
                # replays report zero attempts and are excluded from the meter anyway.
                audio_seconds=seconds * max(assessment.attempts, 1),
                audio_sha256=digest,
                overall_scores=assessment.overall_scores,
                azure_raw=assessment.raw if len(assessment.raw) > 1 else assessment.raw[0],
                offline=assessment.offline,
            )
            for tag in tags:
                db.tag_attempt(conn, attempt_id, tag)
            # The speech-style tag goes on EVERY attempt from day one. Read speech is
            # hyperarticulated and spontaneous speech is systematically more centralised, so
            # pooling them makes a change of register look like a regression toward the middle
            # of the vowel space. v0.12.0 adds spontaneous speech, but the tag has to exist
            # before it: `attempt_tags` takes free text with no migration, and an untagged
            # token can never be reclassified after the fact.
            db.tag_attempt(conn, attempt_id, style_for(mode))
            store_measurement(conn, attempt_id, wav_bytes, digest, measurement)
        return AssessOutcome(assessment=assessment, attempt_id=attempt_id, measurement=measurement)

    except speech_analyzer.Cancelled as exc:
        return AssessOutcome(cancelled=True, reached_azure=exc.reached_azure)
    except speech_analyzer.NoSpeechDetected as exc:
        return AssessOutcome(error=("🤫", str(exc)))
    except (utils.PermanentError, utils.TransientError, speech_analyzer.AssessmentError) as exc:
        if speech_analyzer.is_quota_exhausted(exc):
            # Azure is authoritative; block the rest of the month regardless of the meter.
            budget.mark_quota_exhausted()
        # redact() rather than str(): SDK error details can echo request context.
        logger.error("Assessment failed", exc_info=True)
        return AssessOutcome(error=("🚫", utils.redact(str(exc))))
    except utils.ConfigError as exc:
        return AssessOutcome(error=("🔑", str(exc)))
    except Exception as exc:  # nothing may escape a worker thread
        logger.error("Unexpected assessment failure", exc_info=True)
        return AssessOutcome(error=("🚫", f"{type(exc).__name__}: {utils.redact(str(exc))}"))


def start_assessment(
    conn: sqlite3.Connection,
    wav_bytes: bytes,
    seconds: float,
    reference_text: str,
    mode: Mode,
    key: str,
    tags: tuple[str, ...] = (),
) -> None:
    """Spawn the worker for one assessment and remember it for the poll loop."""
    cancel_event = threading.Event()
    job = AssessJob(
        cancel_event=cancel_event,
        key=key,
        reference_text=reference_text,
        mode=mode,
        tags=tags,
    )

    def work() -> None:
        # Written once, before the thread ends, so the poll loop never reads a half-set job.
        job.outcome = run_assessment_job(
            conn, wav_bytes, seconds, reference_text, mode, cancel_event, tags
        )

    job.thread = threading.Thread(target=work, name="assessment", daemon=True)
    st.session_state["assess_job"] = job
    job.thread.start()


def collect_finished_job() -> None:
    """Fold a finished background assessment into session state. Renders its own alerts.

    Runs before any widget is created, so `Assess` and `Stop` reflect this pass rather than
    the previous one.
    """
    job: AssessJob | None = st.session_state.get("assess_job")
    if job is None or job.running():
        return

    st.session_state["assess_job"] = None
    outcome = job.outcome

    if outcome is None:
        # The worker catches everything, so this means the thread died before its own
        # handler ran. Unreachable in practice; still not a reason to crash the page.
        st.error("The assessment ended unexpectedly. Try it again.", icon="🚫")
        return

    if outcome.cancelled:
        if outcome.reached_azure:
            st.info(
                "Assessment stopped. Some audio may already have reached Azure, but "
                "nothing was recorded here and nothing was added to the meter.",
                icon="🛑",
            )
        else:
            st.info("Assessment stopped before anything was sent to Azure.", icon="🛑")
        return

    if outcome.error is not None:
        icon, message = outcome.error
        st.error(message, icon=icon)
        return

    _cache_put(
        CachedAttempt(
            key=job.key,
            assessment=outcome.assessment,
            reference_text=job.reference_text,
            attempt_id=outcome.attempt_id,
            mode=job.mode,
            measurement=outcome.measurement,
        )
    )
    st.session_state["last_key"] = job.key
    # A fresh result must not open with the previous attempt's word still queued.
    st.session_state["now_playing"] = None


# --- Result rendering --------------------------------------------------------------------------


def _score_bar_html(label: str, score: float | None) -> str:
    """One row of the "Score breakdown" (#11/#12): label, `N / 100`, a banded bar.

    A `None` score renders "—" and an empty bar, never `0 / 100` — a missing prosody score
    (drill mode can return one) is not a zero score, and this is the one place besides
    `_metric` that has to hold that line.
    """
    band = utils.azure_score_band(score)
    colour = AZURE_BAND_COLOURS[band]
    value = f"{score:.0f} / 100" if isinstance(score, (int, float)) else "—"
    width = f"{max(0.0, min(100.0, score)):.1f}%" if isinstance(score, (int, float)) else "0%"
    return (
        '<div style="margin-bottom:0.85rem;">'
        '<div style="display:flex;justify-content:space-between;font-size:0.95rem;">'
        f"<span>{html.escape(label)}</span><span>{html.escape(value)}</span></div>"
        '<div style="background:rgba(128,128,128,0.25);border-radius:4px;height:8px;'
        'margin-top:4px;overflow:hidden;">'
        f'<div style="background:{colour};width:{width};height:100%;"></div>'
        "</div></div>"
    )


def render_scores(assessment: speech_analyzer.Assessment) -> None:
    """Pronunciation headline + Completeness, then the Accuracy/Fluency/Prosody breakdown.

    Banding is presentation only: `overall_scores` keeps the raw floats `normalise()`
    produced, and `utils.azure_score_band` is applied here, at render time, against Azure's
    own 0-59/60-79/80-89/90-100 convention — a different set of cut points from the
    word/phoneme colours in `colour_coded_html`, which are this project's own heuristics.
    """
    scores = assessment.overall_scores
    pron_score = scores.get("pron_score")
    pron_colour = AZURE_BAND_COLOURS[utils.azure_score_band(pron_score)]
    pron_text = f"{pron_score:.0f}" if isinstance(pron_score, (int, float)) else "—"

    left, right = st.columns(2)
    with left:
        st.markdown(
            '<div style="text-align:center;">'
            '<div style="font-size:0.9rem;opacity:0.75;">Pronunciation</div>'
            f'<div style="font-size:2.75rem;font-weight:700;color:{pron_colour};">'
            f"{html.escape(pron_text)}</div></div>",
            unsafe_allow_html=True,
        )
    with right:
        _metric("Completeness", scores.get("completeness"))

    st.markdown("**Score breakdown**")
    st.markdown(
        "".join(
            _score_bar_html(label, scores.get(key))
            for label, key in [
                ("Accuracy score", "accuracy"),
                ("Fluency score", "fluency"),
                ("Prosody score", "prosody"),
            ]
        ),
        unsafe_allow_html=True,
    )
    st.caption(
        "Bands follow Azure's own score interpretation — under 60 low, 60-79 fair, 80-89 "
        "good, 90-100 excellent. A different convention from the word/phoneme colours "
        "further down, which are heuristics this tool chose."
    )


def render_error_counts(assessment: speech_analyzer.Assessment) -> None:
    """Headline counts for #10/#12: Mispronunciations, Unexpected break, Missing break,
    Monotone. Counts only — which words carry each fault is already shown by the flagged-
    word cards (mispronunciations) and `render_delivery` below (the other three), so this
    is not a second copy of that detail, just the number every issue image puts up top.
    """
    mispronounced = speech_analyzer.mispronounced_words(assessment.words)
    summary = speech_analyzer.delivery_summary(assessment.words)

    cells = []
    for label, fault_key, colour in ERROR_BADGES:
        count = len(mispronounced) if fault_key is None else len(summary.get(fault_key, []))
        cells.append(
            '<div style="display:flex;align-items:center;gap:0.4rem;margin:0.2rem 1.3rem '
            '0.2rem 0;">'
            f'<span style="background:{colour};color:#fff;border-radius:4px;'
            f'padding:0.05rem 0.55rem;font-weight:600;min-width:1.4rem;text-align:center;">'
            f"{count}</span><span>{html.escape(label)}</span></div>"
        )
    st.markdown(
        '<div style="display:flex;flex-wrap:wrap;">' + "".join(cells) + "</div>",
        unsafe_allow_html=True,
    )


# --- Coaching ------------------------------------------------------------------------------------


def _gemini_attempted(key: str) -> bool:
    """Whether a real Gemini call has already been bought for this attempt.

    Tracked separately from which coach wrote the report on screen, because a call that
    was spent and then fell back leaves a `fallback` report behind — and that must not read
    as "never asked".
    """
    return bool(lru_get(_session_cache("gemini_attempted"), key))


def _mark_gemini_attempted(key: str) -> None:
    lru_put(_session_cache("gemini_attempted"), key, True, CACHE_LIMIT)


def coaching_for(
    conn: sqlite3.Connection, entry: CachedAttempt, *, ask_model: bool
) -> tuple[Any, str]:
    """The report for this attempt, and which coach wrote it.

    The session cache is checked before anything is produced, for the same reason the TTS
    cache is: Streamlit re-runs this script top to bottom on every widget interaction, and
    a model call sitting in the render path would be spent again on each unrelated click.
    The offline report is cheap to rebuild but is cached too, so the row is written once
    rather than on every rerun.
    """
    cache = _session_cache("coaching")
    cached = lru_get(cache, entry.key)
    if cached is not None and (not ask_model or _gemini_attempted(entry.key)):
        # The attempted-flag is not redundant with the button's disabled flag. The click is
        # handled in the same pass that rendered the button, so the button still shows as
        # enabled until the next rerun — and a second click on it would buy a second call.
        # The guard belongs where the spend is, the same rule the TTS cache follows.
        return cast("tuple[Any, str]", cached)

    if ask_model:
        # Marked before the call, not after, and on the attempt rather than the outcome: a
        # call that reached Gemini and then fell back (malformed JSON, nothing surviving
        # validation) has already been spent, so keying this off the returned source would
        # leave the button live and let the same failure be bought over and over.
        _mark_gemini_attempted(entry.key)
        with st.spinner("Asking Gemini for a second opinion…"):
            result = ai_coach.coach(entry.assessment, entry.reference_text, entry.mode)
    else:
        try:
            report = fallback_coach.build(entry.assessment, entry.mode)
        except Exception as exc:  # a report is promised on every assessment
            logger.error("Could not build the offline report", exc_info=True)
            report = fallback_coach.emergency_report(f"{type(exc).__name__}: {exc}")
        result = ai_coach.CoachingResult(
            report=report, source=fallback_coach.SOURCE_FALLBACK, raw=report.model_dump()
        )

    lru_put(cache, entry.key, (result.report, result.source), CACHE_LIMIT)
    if entry.attempt_id:
        # Verbatim, exactly as the Azure response is stored: changing what this panel shows
        # later is then a re-parse of a stored row rather than another call.
        db.attach_coaching(
            conn, entry.attempt_id, gemini_raw=result.raw, coach_source=result.source
        )
    return result.report, result.source


def render_fix(fix: Any, rank: int) -> None:
    """One priority fix, rendered to dominate the page. Never a raw model blob."""
    with st.container(border=True):
        st.markdown(
            f'<div style="font-size:1.7rem;font-weight:700;line-height:1.3;">'
            f"{rank}. /{html.escape(fix.expected_phoneme)}/ "
            f'<span style="opacity:0.55;">→</span> '
            f"/{html.escape(fix.produced_phoneme)}/</div>",
            unsafe_allow_html=True,
        )
        if fix.affected_words:
            st.caption("In this attempt: " + ", ".join(fix.affected_words))
        if fix.why_it_matters:
            st.markdown(fix.why_it_matters)
        if fix.articulation:
            st.markdown(f"**How to make it** — {fix.articulation}")
        if fix.minimal_pairs:
            pairs = " · ".join(f"**{pair.a}** / {pair.b}" for pair in fix.minimal_pairs)
            st.markdown(f"**Drill these pairs** — {pairs}")


# How many span words to list before saying how many more there are. A real Monotone runs
# long — the captured bad reading flagged 30 words — and a 30-word comma list is a wall
# that buries the sentence and the drill underneath it. The stretch worth practising is
# quoted in the drill itself, so this line only has to say roughly where and how much.
MAX_SPAN_WORDS_SHOWN = 12


def _span_caption(span: list[str]) -> str:
    shown = ", ".join(span[:MAX_SPAN_WORDS_SHOWN])
    remaining = len(span) - MAX_SPAN_WORDS_SHOWN
    return f"{shown} … and {remaining} more" if remaining > 0 else shown


def render_delivery_drills(report: Any) -> None:
    """The delivery faults, each with something to perform about it.

    Issue #9: the prosody score was the one number on this page that could be read but not
    practised. The score and the fault counts above already say *that* something went
    wrong; this says where, and what to do about it.

    Nothing renders when there are none. `render_delivery` further down already reports a
    clean attempt, and a second "no problems here" three sections above it is noise.
    """
    if not report.delivery_drills:
        return

    st.markdown("**Delivery**")
    for drill in report.delivery_drills:
        with st.container(border=True):
            st.markdown(f"**{DELIVERY_LABELS.get(drill.fault, drill.fault)}**")
            if drill.span:
                st.caption("In this attempt: " + _span_caption(drill.span))
            if drill.what_happened:
                st.markdown(drill.what_happened)
            if drill.drill:
                st.markdown(f"**Drill** — {drill.drill}")


def render_coaching(conn: sqlite3.Connection, entry: CachedAttempt) -> None:
    """The report, with the button that offers to spend a Gemini call improving it.

    The button is created before the report is rendered, so the click and the answer land
    in the same rerun rather than showing the previous report for one pass.
    """
    st.subheader("What to work on")

    usable, reason = ai_coach.available()

    asked = st.button(
        "✨ Improve this with Gemini",
        key=f"coach-{entry.key}",
        # Disabled once a call has been *bought* for this attempt, whatever came back:
        # a spent call that fell back must not be re-buyable.
        disabled=not usable or _gemini_attempted(entry.key),
        help="One free-tier call. The report below is already complete without it.",
    )
    if usable:
        st.caption(
            "Sends the compacted analysis and your reference text to Google — never your "
            "audio. Free-tier prompts and responses may be used to improve Google's "
            "products, so this is a click rather than something every assessment does."
        )
    else:
        st.caption(reason)

    report, source = coaching_for(conn, entry, ask_model=asked)

    if asked and source == fallback_coach.SOURCE_FALLBACK:
        st.info(
            "Gemini could not be reached, or answered with something the Azure data did "
            "not support. The report below is the offline one, unchanged.",
            icon="🛟",
        )

    if source == fallback_coach.SOURCE_GEMINI:
        st.caption(
            f"Written by {ai_coach.model_name()} from the Azure findings, then checked "
            f"against them — any sound it named that Azure did not report was removed."
        )
    else:
        st.caption(
            "Written from the Azure data alone by the offline coach. No key, no network, "
            "and nothing sent anywhere."
        )

    st.markdown(report.overall_comment)

    if report.priority_fixes:
        for rank, fix in enumerate(report.priority_fixes, start=1):
            render_fix(fix, rank)
    else:
        st.info("No single sound substitution stood out in that attempt.", icon="✅")

    render_delivery_drills(report)

    if report.stress_and_rhythm.issues or report.stress_and_rhythm.drill:
        st.markdown("**Stress and rhythm**")
        for issue in report.stress_and_rhythm.issues:
            st.markdown(f"- {issue}")
        if report.stress_and_rhythm.drill:
            st.markdown(f"*Drill:* {report.stress_and_rhythm.drill}")

    if report.practice_plan:
        st.markdown("**Practice plan**")
        st.markdown(report.practice_plan)


def render_diff(assessment: speech_analyzer.Assessment, reference_text: str) -> None:
    """What Azure heard, against what was written. The first thing worth looking at."""
    st.subheader("Script versus what Azure heard")
    if not assessment.recognised_text:
        st.warning("Azure did not report any recognised text for this attempt.")
        return

    pairs = reference_vs_heard(reference_text, assessment.recognised_text)
    if all(tag == "same" for tag, _ in pairs):
        st.success("Azure heard every word of the script.")
    else:
        st.markdown(diff_html(pairs), unsafe_allow_html=True)
        st.caption(
            "Struck through: in the script, not heard. Italic: heard, not in the script. "
            "A word Azure heard differently is a bigger problem than a low phoneme score."
        )
    with st.expander("The two texts, verbatim"):
        st.markdown(f"**Script**\n\n{reference_text}")
        st.markdown(f"**Heard**\n\n{assessment.recognised_text}")


def render_colour_coded(assessment: speech_analyzer.Assessment) -> None:
    st.subheader("Word by word")
    st.markdown(colour_coded_html(assessment.words), unsafe_allow_html=True)
    st.caption(
        f"Hover any word for its score and phoneme breakdown. Red below {utils.WORD_RED:g}, "
        f"amber below {utils.WORD_AMBER:g}, green above. Struck through: never spoken. "
        f"Italic: heard but not in the script. These cut points are heuristics chosen for "
        f'this tool — Azure returns a 0-100 score and says nothing about where "bad" starts.'
    )


def render_word_card(conn: sqlite3.Connection, word: dict[str, Any], index: int) -> None:
    """One flagged word: what was expected, what came out, and how it should sound."""
    text = str(word.get("word") or "")
    accuracy = word.get("accuracy")
    error_type = word.get("error_type") or "None"

    with st.container(border=True):
        score = f"{accuracy:.0f}" if isinstance(accuracy, (int, float)) else "not spoken"
        colour = BAND_COLOURS[utils.word_band(accuracy)]
        st.markdown(
            f'<span style="font-size:1.3rem;font-weight:600;color:{colour};">'
            f"{html.escape(text)}</span> "
            f'<span style="opacity:0.7;">— {html.escape(score)}</span>',
            unsafe_allow_html=True,
        )

        # The headline sound, before the full phoneme list. A long word can carry a dozen
        # phonemes, and the one that actually failed should not need hunting for.
        summary = weakest_phoneme(word)
        if summary:
            st.markdown(f"**{summary}**")

        notes = []
        if error_type != "None":
            notes.append(f"{error_type} (flagged by {word.get('error_source') or 'azure'})")
        notes.extend(DELIVERY_LABELS.get(f, f) for f in word.get("delivery_error_types") or [])
        if notes:
            st.caption(" · ".join(notes))

        pairs = speech_analyzer.phoneme_pairs(word)
        if pairs:
            rows = []
            for expected, produced, score_value in pairs:
                phoneme_colour = BAND_COLOURS[utils.phoneme_band(score_value)]
                # "?" rather than "/None/": a payload can omit Phoneme, and rendering the
                # literal string None as a target sound is worse than admitting we do not
                # know it, in a tool whose whole job is naming sounds.
                shown = f"/{html.escape(expected)}/" if expected else "?"
                if produced:
                    shown += f" → <b>/{html.escape(produced)}/</b>"
                title = f"{score_value:.0f}" if score_value is not None else "no score"
                rows.append(
                    f'<span style="color:{phoneme_colour};margin-right:1.1rem;" '
                    f'title="{html.escape(title, quote=True)}">{shown}</span>'
                )
            st.markdown(
                '<div style="line-height:2;">' + "".join(rows) + "</div>",
                unsafe_allow_html=True,
            )
            st.caption("Expected → what you actually produced. Hover for the score.")

        syllables = [s for s in word.get("syllables") or [] if s.get("syllable")]
        if syllables:
            # Misplaced lexical stress is one of the most common intelligibility failures
            # and is invisible at the phoneme level, so it gets its own line.
            rendered = " · ".join(
                f"{s['syllable']}"
                + (f" ({s['score']:.0f})" if isinstance(s.get("score"), (int, float)) else "")
                for s in syllables
            )
            st.caption(f"Syllables: {rendered}")

        if error_type == "Omission":
            # It was never spoken, which is exactly why the target is worth hearing. The
            # word comes from the reference, so there is always something to synthesise.
            st.caption("You did not say this one. Hear what it should sound like:")
        playback_buttons(conn, text, key_prefix=f"word-{index}", label=f"“{text}”")


def render_delivery(assessment: speech_analyzer.Assessment) -> None:
    """Counts, locations and measurements of UnexpectedBreak / MissingBreak / Monotone.

    The evidence for what the Delivery drills in the coaching section above ask for. The
    measurement sentence comes from `fallback_coach.measurement_note`, the same function
    that wrote it up there, so the two cannot quote different numbers for one fault.
    """
    st.subheader("Delivery")
    faults = speech_analyzer.delivery_faults(assessment.words)
    if not faults:
        st.success("No pausing or intonation problems flagged in that attempt.")
        return
    for fault in faults:
        words = fault["words"]
        st.markdown(
            f"**{DELIVERY_LABELS.get(fault['fault'], fault['fault'])}** — {len(words)} "
            f"{'word' if len(words) == 1 else 'words'}: {', '.join(words)}."
            f"{fallback_coach.measurement_note(fault)}"
        )


def render_rhythm(assessment: speech_analyzer.Assessment, reference_text: str) -> None:
    """The nPVI figure for this reading, against the TTS baseline.

    A separate section from `render_delivery` rather than a line inside it, because it is a
    different kind of claim: the delivery panel lists faults Azure flagged, this is a
    continuous measurement of something Azure never scores at all.

    Everything about how the number is arrived at — and why the baseline is the only
    comparison it supports — is in `rhythm.py`'s module docstring. What matters here is that
    the UI never shows the number without also saying what it can and cannot be compared to.
    """
    st.subheader("Rhythm")
    measured = rhythm.npvi(assessment.words)

    if measured.npvi is None:  # i.e. not measured.measured, in a form mypy narrows
        st.caption(
            f"Not enough connected speech to measure rhythm — {measured.pairs} vowel pairs, "
            f"and this needs at least {rhythm.MIN_PAIRS}. nPVI is a statement about how "
            f"vowel lengths vary across running speech, so a few words cannot produce "
            f"one however carefully they are said. Read something longer."
        )
        return

    baseline = rhythm.baseline()
    if baseline is None or baseline.rhythm.npvi is None:
        st.metric("nPVI", f"{measured.npvi:.1f}")
        st.caption(
            "**This number has nothing to compare against yet.** Published General American "
            "nPVI bands are not that comparison: they come from hand-segmented corpora "
            "reading other material, and the figure moves by more than five points on this "
            "same recording just from changing how the segments are cut. Capture the "
            "reference by running `scripts/capture_baseline.py` once — it renders the "
            "benchmark passage through Azure TTS and this same pipeline, which holds "
            "everything but the voice still."
        )
        return

    difference = measured.npvi - baseline.rhythm.npvi
    st.metric(
        "nPVI",
        f"{measured.npvi:.1f}",
        delta=f"{difference:+.1f} vs baseline",
        delta_color="off",
    )
    st.caption(
        f"Baseline {baseline.rhythm.npvi:.1f} — the benchmark passage read by **"
        f"{baseline.voice}** through this same pipeline. It is a fixed reference point, not "
        f"a native speaker: a synthesiser's rhythm is its own. What makes it useful is that "
        f"it does not move, so a change in this gap over weeks is a change in your reading. "
        f"A **lower** nPVI than the reference means your vowels are closer to equal in "
        f"length, which is what a syllable-timed rhythm carried into English sounds like. "
        f"Measured over {measured.pairs} vowel pairs in {measured.runs} "
        f"{'stretch' if measured.runs == 1 else 'stretches'} of unbroken speech. Keep the "
        f"recording format the same between reads — the identical take scores two points "
        f"lower as a compressed upload than as a WAV, which is enough to look like a change."
    )
    if not progress_view.is_benchmark(reference_text):
        st.caption(
            "Read on a different text from the baseline, so some of this gap is the writing "
            "rather than the reading. Read the benchmark passage to compare like with like."
        )


def render_result(conn: sqlite3.Connection, entry: CachedAttempt, source: Any) -> None:
    assessment, reference_text = entry.assessment, entry.reference_text
    render_scores(assessment)
    render_error_counts(assessment)
    # Directly under the scores: what to do about them comes before the evidence for them.
    render_coaching(conn, entry)
    render_diff(assessment, reference_text)
    render_colour_coded(assessment)

    st.subheader("Hear the whole thing")
    playback_buttons(conn, reference_text, key_prefix="whole", label="the full text")
    if source is not None:
        # Your own recording directly beneath the native rendering, so the two can be
        # compared back to back without leaving the page. That comparison is the feature.
        st.caption("Your recording:")
        st.audio(source)
    if utils.offline_mode():
        st.caption(
            "Playback is disabled: OFFLINE_MODE is on, and there is no fixture to replay "
            "for audio the way there is for an assessment. Unset it to hear the target."
        )

    flagged = sorted(
        (w for w in assessment.words if speech_analyzer.is_flagged(w)), key=severity_key
    )
    # A word can score a perfect 100 and still be flagged, because `is_flagged` also fires
    # on a delivery fault. Those are worth keeping — a monotone 100 is real — but they are
    # not what "flagged words" is for, and in a paragraph they bury the words that actually
    # need work. Collapsed, not dropped.
    needs_attention = [w for w in flagged if not _scored_full(w)]
    perfect = [w for w in flagged if _scored_full(w)]

    st.subheader(f"Flagged words ({len(flagged)} of {len(assessment.words)})")
    if not flagged:
        st.success("Nothing flagged in that attempt.")
    else:
        st.caption(
            "Worst first. Each word is synthesised on its own, so you hear it in citation "
            "form — right for drilling a sound, but not how it sounds inside the sentence. "
            "Use the whole-text playback above for that."
        )
        index = 0
        for word in needs_attention:
            render_word_card(conn, word, index)
            index += 1
        if perfect:
            # The index keeps counting across both groups: `render_word_card` builds its
            # playback widget keys from it, and a repeated key is a hard Streamlit error.
            with st.expander(
                f"Scored 100 but still flagged ({len(perfect)}) — delivery, not sounds"
            ):
                for word in perfect:
                    render_word_card(conn, word, index)
                    index += 1

    render_delivery(assessment)
    render_rhythm(assessment, reference_text)
    render_accent_table(conn, entry)


# --- The accent measurement surface ----------------------------------------------------------

# What a five-second room check costs, and what it buys. Formant estimation degrades badly
# with room reverb and a poor microphone, so telling somebody their /ɔ/ is wrong when the real
# finding is that their room is wrong wastes a calibration read and teaches them nothing.
ROOM_CHECK_TEXT = "The quick brown fox jumps over the lazy dog."
ROOM_CHECK_SECONDS = 5.0


def reference_set() -> str:
    """Which published set the numbers are scored against, or "" when it has not been chosen.

    Deliberately has no default. Formants scale with vocal tract length, the men's and women's
    tables sit far apart, and an average of the two describes nobody — so this is refused
    until it is set rather than guessed and then quietly wrong for every reading afterwards.
    """
    chosen = (utils.get("GA_REFERENCE_SET") or "").strip().lower()
    return chosen if chosen in vowel_reference.REFERENCE_SETS else ""


def render_accent_table(conn: sqlite3.Connection, entry: CachedAttempt) -> None:
    """The four-column table for one attempt, under its result."""
    measurement = entry.measurement
    st.subheader("Accent measurement")

    if measurement is None:
        st.caption(
            "No vowel measurement for this attempt. Offline replays have no audio behind "
            "their phoneme offsets, so slicing them would measure the wrong recording."
        )
        return

    chosen = reference_set()
    if not chosen:
        st.warning(
            "Set `GA_REFERENCE_SET` to `men` or `women` in `.env` to score these vowels. "
            "There is no default and no average: formants scale with vocal tract length, so "
            "one reference set is right and the other is wrong by roughly the size of the "
            "effect being measured.",
            icon="📏",
        )
        return

    note = measurement.quality_note()
    if note:
        st.warning(note, icon="🎙️")

    if measurement.alignment_db is not None and measurement.alignment_db < 3.0:
        # The check that the phoneme offsets point where they are believed to. Loud, because
        # every number below it would be plausible and wrong.
        st.error(
            f"The claimed vowel spans are only {measurement.alignment_db:.1f} dB louder than "
            f"everything that is not a vowel. The slices are not landing on the vowels, so "
            f"nothing below should be believed.",
            icon="🚫",
        )

    try:
        normaliser = vowel_measure.lobanov(
            measurement.accepted, categories=vowel_measure.REFERENCE_CATEGORIES
        )
    except vowel_measure.TooFewTokens as exc:
        st.info(str(exc), icon="📉")
        _render_rejections(measurement)
        return

    baseline_row = db.current_baseline(conn)
    noise = (
        vowel_measure.noise_from_json(json.loads(baseline_row["noise_floor_json"]))
        if baseline_row is not None
        else None
    )
    findings = vowel_measure.findings(measurement, normaliser, reference_set=chosen, noise=noise)
    st.markdown(accent_view.to_markdown(findings))
    st.caption(accent_view.PUBLISHED_CAPTION.format(set=chosen))
    if noise is None:
        st.caption(accent_view.noise_caption(None))


def _render_rejections(measurement: Any) -> None:
    """Show what was refused even when nothing could be normalised. A thin table, visibly thin."""
    rejected = measurement.rejected
    if not rejected:
        return
    st.markdown(accent_view.to_markdown(vowel_measure._rejection_findings(measurement.tokens)))


def calibration_reads(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Benchmark-passage attempts that carry usable vowel measurements, newest first.

    The calibration passage is `progress_view.BENCHMARK_PASSAGE` — chosen once, for exactly
    two consumers, and its own comment in `progress_view` says so. Nothing new is written for
    this chunk.
    """
    rows = conn.execute(
        """
        SELECT a.id, a.created_at, a.reference_text,
               COUNT(v.id) FILTER (WHERE v.accepted = 1) AS accepted
        FROM attempts a JOIN vowel_measurements v ON v.attempt_id = a.id
        WHERE a.offline = 0
        GROUP BY a.id ORDER BY a.created_at DESC
        """
    ).fetchall()
    return [
        row
        for row in rows
        if progress_view.is_benchmark(row["reference_text"])
        and not rhythm.is_baseline_capture(row["reference_text"])
        and row["accepted"] > 0
    ]


def _minutes_between(first: str, second: str) -> float:
    start = datetime.strptime(first, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    end = datetime.strptime(second, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return abs((end - start).total_seconds()) / 60.0


def build_baseline(conn: sqlite3.Connection, older: Any, newer: Any) -> str | None:
    """Calibrate from two stored reads. Returns an error message, or None on success."""
    chosen = reference_set()
    if not chosen:
        return "Set GA_REFERENCE_SET to 'men' or 'women' first."

    first = vowel_measure.tokens_from_rows(
        [dict(row) for row in db.vowel_measurements_for(conn, int(older["id"]))]
    )
    second = vowel_measure.tokens_from_rows(
        [dict(row) for row in db.vowel_measurements_for(conn, int(newer["id"]))]
    )
    ceiling = next(
        (float(row["lpc_ceiling_hz"]) for row in db.vowel_measurements_for(conn, int(older["id"]))),
        acoustics.CEILING_TYPICAL_MALE,
    )
    try:
        baseline = vowel_measure.calibrate(
            first,
            second,
            reference_set=chosen,
            ceiling_hz=ceiling,
            attempt_ids=(int(older["id"]), int(newer["id"])),
        )
    except (vowel_measure.CalibrationRefused, vowel_measure.TooFewTokens) as exc:
        return str(exc)

    db.save_baseline(
        conn,
        positions=vowel_measure.positions_to_json(baseline.positions),
        normaliser=vowel_measure.normaliser_to_json(baseline.normaliser),
        noise_floor=vowel_measure.noise_to_json(baseline.noise),
        lpc_ceiling_hz=baseline.ceiling_hz,
        reference_set=baseline.reference_set,
        style_tag=baseline.style,
        tokens=baseline.tokens,
        attempt_ids=baseline.attempt_ids,
    )
    return None


def render_calibration(conn: sqlite3.Connection) -> None:
    """The two-read calibration flow, and what it refuses."""
    st.subheader("Calibration")
    gap = utils.get_float("CALIBRATION_GAP_MINUTES")
    st.markdown(
        f"""
Read the benchmark passage **twice in one sitting, at least {gap:g} minutes apart**, on the
same microphone in the same room. The displacement between the two reads **is** the
measurement noise floor — a vowel centroid moves between sessions from microphone placement,
posture, time of day and warm-up, with no learning at all. Without that number the progress
view would render exactly that wander as progress.
"""
    )

    reads = calibration_reads(conn)
    if len(reads) < 2:
        st.info(
            f"{len(reads)} of 2 calibration reads so far. Read the benchmark passage on the "
            f"Practice tab — it is the same passage the progress chart uses, so the read "
            f"counts for both.",
            icon="🎯",
        )
        return

    newer, older = reads[0], reads[1]
    apart = _minutes_between(str(older["created_at"]), str(newer["created_at"]))
    st.caption(
        f"Two most recent reads: attempt {older['id']} and attempt {newer['id']}, "
        f"{apart:.0f} minutes apart, {older['accepted']} and {newer['accepted']} usable "
        f"vowel tokens."
    )

    if apart < gap:
        st.warning(
            f"Those two reads are only {apart:.0f} minutes apart, and the floor needs "
            f"{gap:g}. Two back-to-back reads measure the microphone holding still, not the "
            f"session-to-session wander this number exists to capture — so the band would "
            f"come out flatteringly small and start licensing noise as progress.",
            icon="⏱️",
        )
        return

    if st.button("Set the baseline from these two reads", type="primary"):
        problem = build_baseline(conn, older, newer)
        if problem:
            st.error(problem, icon="📉")
        else:
            st.success("Baseline and noise floor stored.")
            st.rerun()


def render_baseline(conn: sqlite3.Connection) -> None:
    """The stored baseline: the vowel chart, the noise floor, and the four-column table."""
    row = db.current_baseline(conn)
    if row is None:
        st.info(
            "No baseline yet. Until the calibration passage has been read twice there is no "
            "speaker centroid to normalise against and no noise floor, so no movement on any "
            "accent surface can honestly be called progress.",
            icon="📐",
        )
        return

    chosen = str(row["reference_set"])
    positions = vowel_measure.positions_from_json(json.loads(row["positions_json"]))
    noise = vowel_measure.noise_from_json(json.loads(row["noise_floor_json"]))

    st.subheader("Your vowel space")
    st.caption(
        f"Calibrated {row['created_at']} from attempts {row['attempt_ids']}, "
        f"{row['tokens']} usable tokens, LPC ceiling {row['lpc_ceiling_hz']:.0f} Hz, "
        f"{row['style_tag']} speech."
    )

    frame = accent_view.vowel_frame(positions, vowel_measure.reference_positions(chosen))
    if frame.empty:
        st.caption("Nothing in the baseline could be placed on a chart.")
    else:
        st.altair_chart(accent_view.vowel_chart(frame), theme="streamlit")
        st.caption(accent_view.PUBLISHED_CAPTION.format(set=chosen))

    st.markdown(accent_view.noise_caption(noise))

    with st.expander("The noise floor, vowel by vowel"):
        st.markdown(
            accent_view.to_markdown(
                [
                    vowel_measure.Finding(
                        feature=f"/{vowel}/ {phoneme_reference.keyword_for(vowel)} — "
                        f"between-read displacement",
                        user=f"{band:.2f} z",
                        target="—",
                        delta=(
                            f"Movement smaller than {band:.2f} z is reported as "
                            f"'{vowel_measure.WITHIN_NOISE}', in either direction."
                        ),
                    )
                    for vowel, band in sorted(noise.per_vowel.items())
                ]
            )
        )


def render_room_check(conn: sqlite3.Connection) -> None:
    """Five seconds, one number, and a plain answer about whether this room can be measured."""
    st.subheader("Room and microphone check")
    st.caption(
        f"Before the first calibration read: record about {ROOM_CHECK_SECONDS:.0f} seconds "
        f"and get the measured signal-to-noise back. Formant estimation degrades badly with "
        f"room reverb and a poor microphone, and being told your vowels are wrong when the "
        f"real finding is that the room is wrong wastes a calibration read. Costs about "
        f"{ROOM_CHECK_SECONDS:.0f} seconds of the monthly Azure allowance."
    )
    st.info(f"Read this aloud: **{ROOM_CHECK_TEXT}**", icon="🗣️")

    audio = st.audio_input("Room check recording", key="room_check_audio")
    if audio is None or not st.button("Check the room"):
        return

    wav_bytes, seconds = prepare_audio(conn, audio.getvalue(), Mode.DRILL)
    if wav_bytes is None or seconds is None:
        return
    with st.spinner("Measuring…"):
        outcome = run_assessment_job(
            conn, wav_bytes, seconds, ROOM_CHECK_TEXT, Mode.DRILL, threading.Event()
        )
    if outcome.error is not None:
        icon, message = outcome.error
        st.error(message, icon=icon)
        return

    snr = (outcome.assessment.overall_scores or {}).get("snr_db_min")
    if snr is None:
        st.warning("Azure reported no signal-to-noise ratio, so this is inconclusive.", icon="🤷")
        return

    if snr < vowel_measure.SNR_UNRELIABLE_DB:
        st.error(
            f"**{snr:.1f} dB — this room and microphone cannot support a vowel measurement.** "
            f"Below about {vowel_measure.SNR_UNRELIABLE_DB:.0f} dB the formant estimates are "
            f"describing the room. Get closer to the microphone, turn off fans and air "
            f"conditioning, and add soft furnishings before calibrating.",
            icon="🚫",
        )
    elif snr < vowel_measure.SNR_MARGINAL_DB:
        st.warning(
            f"**{snr:.1f} dB — usable, but not clean.** A calibration read taken here will "
            f"work and every number will be looser than it needs to be.",
            icon="⚠️",
        )
    else:
        st.success(
            f"**{snr:.1f} dB — good enough to measure vowels.** Go ahead and calibrate.",
            icon="✅",
        )


def render_accent(conn: sqlite3.Connection) -> None:
    """The Accent tab: room check, calibration, the baseline, and what it forbids."""
    st.header("Accent")
    st.caption(
        "Azure's diagnosis is categorical — this phoneme is /θ/ or /t/, scored out of a "
        "hundred. Accent is continuous. This page measures the gradient part: where your "
        "vowels sit, how they move, how long they last and how far the unstressed ones "
        "reduce."
    )

    chosen = reference_set()
    if not chosen:
        st.warning(
            "**`GA_REFERENCE_SET` is not set.** Choose `men` or `women` in `.env`. There is "
            "no default and never an average of the two: formants scale with vocal tract "
            "length, so the wrong set is wrong by about the size of the thing being measured.",
            icon="📏",
        )
    else:
        st.caption(f"Scoring against the **{chosen}** reference set.")

    render_baseline(conn)
    render_calibration(conn)
    with st.expander("Check the room first"):
        render_room_check(conn)


# --- The progress view ----------------------------------------------------------------------


@st.cache_data(show_spinner=False)
def parsed_attempts(
    _conn: sqlite3.Connection, fingerprint: tuple[int, int]
) -> list[progress_view.ParsedAttempt]:
    """Re-parse every stored payload, cached against `fingerprint`.

    Not optional. Streamlit renders *both* tab bodies on every rerun, including the 0.4 s
    `JOB_POLL_SECONDS` reruns while an assessment is in flight, and each pass would otherwise
    re-parse tens of 45-170 kB payloads. `fingerprint` is `db.attempt_fingerprint`, which
    changes exactly when a new attempt lands; the connection is `_conn` so Streamlit does not
    try to hash it.
    """
    with _DB_LOCK:
        payloads = db.attempt_payloads(_conn)
    return progress_view.parse_attempts(payloads)


def render_progress(conn: sqlite3.Connection) -> None:
    """The Progress tab: the trajectory, then what keeps going wrong.

    The frames and the chart specs are built in `progress_view`, which never imports
    Streamlit — the same boundary the pure render helpers above sit on. This function is the
    impure half: it reads, calls them, and hands the results to `st.*`.
    """
    with _DB_LOCK:
        rows = db.attempt_series(conn)
        fingerprint = db.attempt_fingerprint(conn)

    st.subheader("Progress")
    if not rows:
        st.info(
            "Nothing recorded yet. Assess an attempt on the Practice tab and it will appear "
            "here. Offline replays are left out — the fixture scores the same every time.",
            icon="📈",
        )
        return

    frame = progress_view.score_frame(rows)
    st.altair_chart(progress_view.score_chart(frame), width="stretch")

    since = progress_view.days_since_benchmark(rows)
    if since is None:
        st.warning(
            f"The headline line is empty because the benchmark passage has not been read "
            f"yet. Pick **{progress_view.BENCHMARK_TITLE}** from the paragraph presets and "
            f"read it. Until then the faint points are all there is, and they are not "
            f"comparable to each other: a score on an easier text is a higher score, not a "
            f"better reading.",
            icon="🎯",
        )
    else:
        when = "today" if since == 0 else f"{since} day{'' if since == 1 else 's'} ago"
        st.caption(
            f"Benchmark passage last read cold {when}. The faint points behind it are free "
            f"practice on other texts — shown for context, never comparable to the line or "
            f"to each other, since Drill and Paragraph scores are computed differently."
        )
    if any(frame["series"] == progress_view.SHADOWED_SERIES):
        # An unexplained dashed line reads as a second trajectory, which is the one thing it
        # must not be taken for.
        st.caption(
            "The dashed line is the same passage read **along with the model**. It is not the "
            "trajectory — it is what the trajectory is measured against, and it is kept off "
            "the solid line for the same reason an easier text is: a read somebody else "
            "carried is not evidence about unassisted speech. The two converging is the "
            "point; see **Shadowed against cold** below."
        )

    parsed = parsed_attempts(conn, fingerprint)
    phonemes = progress_view.flagged_phonemes(parsed)
    words = progress_view.flagged_words(parsed)

    left, right = st.columns(2)
    with left:
        if len(phonemes):
            st.altair_chart(progress_view.phoneme_chart(phonemes), width="stretch")
        else:
            st.caption("No sound has been flagged yet.")
    with right:
        if len(words):
            st.altair_chart(progress_view.word_chart(words), width="stretch")
        else:
            st.caption("No word has been flagged yet.")

    st.caption(
        "Counted by how many attempts a sound or word was flagged in, not by raw "
        "occurrences, so one long paragraph cannot dominate the list."
    )

    render_rhythm_history(parsed)
    render_shadow_comparison(conn, rows)
    render_perception_history(conn)


def render_perception_history(conn: sqlite3.Connection) -> None:
    """Per-contrast identification accuracy over time, against the chance floor.

    The rule is the whole point of the chart. A two-alternative forced choice scores 50% by
    guessing, so a rising line that never clears the dashed one is not improvement — and an
    accuracy plotted against a zero baseline would look like it was.
    """
    st.subheader("Hearing the contrast")
    with _DB_LOCK:
        trials = [dict(row) for row in db.all_trials(conn)]

    frame = progress_view.perception_frame(trials)
    if not len(frame):
        st.caption(
            "No listening block answered yet. Blocks are scheduled on the **Today** tab, "
            "against the sounds your own recordings keep getting flagged on."
        )
        return

    st.altair_chart(progress_view.perception_chart(frame), width="stretch")
    st.caption(
        "The dashed line is the chance floor — what guessing scores on a two-way choice. "
        "Read every point against it, not against zero: a block at 55% is a coin toss with "
        "a good day, not a contrast you can hear. Each point is one block; a spaced review "
        "is a shorter block, which is why its point rests on fewer trials."
    )


def render_rhythm_history(parsed: Sequence[progress_view.ParsedAttempt]) -> None:
    """Benchmark nPVI over time, against the TTS baseline.

    Benchmark reads only. nPVI is text-sensitive, so plotting free practice beside it would
    chart the difficulty of whatever was chosen that day.
    """
    st.subheader("Rhythm over time")
    frame = progress_view.rhythm_frame(parsed)
    baseline = rhythm.baseline()

    if not len(frame):
        st.caption(
            "Nothing to plot yet. Rhythm is measured on benchmark reads only — nPVI moves "
            "with the text as much as with the speaker, so a chart mixing passages would "
            "show which one was harder to read."
        )
        return

    st.altair_chart(
        progress_view.rhythm_chart(
            frame, baseline.rhythm.npvi if baseline and baseline.rhythm.measured else None
        ),
        width="stretch",
    )

    if baseline is None or not baseline.rhythm.measured:
        st.warning(
            "No reference line: the TTS baseline has not been captured on this machine. "
            "Run `scripts/capture_baseline.py` once. Until then the trend is still real — "
            "the passage is fixed, so a move is a move — but there is nothing to say how "
            "far from a steady reference it sits.",
            icon="📏",
        )
    else:
        st.caption(
            f"The dashed line is **{baseline.voice}** reading the same passage through this "
            f"same pipeline ({baseline.rhythm.npvi:.1f}). A fixed point, not a native "
            f"speaker. Published General American bands are deliberately not drawn here: "
            f"they come from hand-segmented corpora reading other material, and on one "
            f"unchanged recording this figure moves more than five points just from how the "
            f"segments are cut — so the distance to a published band would mean less than "
            f"the distance to this line."
        )


# --- Today: the practice queue and the perception trainer -------------------------------------
# The single entry point. Opening the app should answer "what am I doing today?" rather than
# present a blank textarea — thirty sessions that each start from nothing are thirty first
# sessions. The textarea is still there, one tab click away.
#
# Streamlit executes EVERY tab body on every rerun, the 0.4 s assessment polls included, so
# everything expensive here is cached on a fingerprint exactly the way the progress view's
# re-parse is.


BLOCK_KEY = "perception_block"


@st.cache_data(show_spinner=False)
def queue_candidates(
    _conn: sqlite3.Connection, fingerprint: tuple[int, int]
) -> list[practice_queue.Candidate]:
    """What the stored attempts say is worth practising, cached on the attempts fingerprint.

    Reuses `parsed_attempts`, so the Today tab costs no extra re-parse: the two aggregates it
    needs are the same ones the Progress tab draws, which is what stops the queue and the
    chart from disagreeing about what recurs.
    """
    parsed = parsed_attempts(_conn, fingerprint)
    phonemes = progress_view.flagged_phonemes(parsed)
    syllables = progress_view.weak_syllables(parsed)
    return practice_queue.candidates(phonemes.to_dict("records"), syllables.to_dict("records"))


def sync_queue(conn: sqlite3.Connection) -> list[Any]:
    """Promote new targets if there is room, and return the current list.

    Promotion runs on every render rather than on a button, because the queue is meant to
    reflect the recordings — a target that the evidence now supports should not wait for the
    user to ask for it. Nothing is invented: `promote` can only choose from `candidates`,
    which can only come from stored attempts.
    """
    with _DB_LOCK:
        fingerprint = db.attempt_fingerprint(conn)
        existing = [dict(row) for row in db.targets(conn)]

    found = queue_candidates(conn, fingerprint)
    fresh = practice_queue.promote(existing, found)
    if fresh:
        with _DB_LOCK:
            for candidate in fresh:
                db.upsert_target(
                    conn,
                    item=candidate.item,
                    kind=candidate.kind,
                    evidence={**candidate.evidence, "why": candidate.why},
                )
        logger.info("Promoted %d new practice target(s)", len(fresh))

    with _DB_LOCK:
        return [dict(row) for row in db.targets(conn)]


def _accuracy_line(summaries: list[Any]) -> str:
    """Accuracy so far, NEVER without its chance floor beside it.

    Built in one place precisely so the anchor cannot be dropped from one of the several
    spots accuracy appears in. "62%" against an unstated 50% floor is noise dressed as
    progress.
    """
    completed = [b for b in summaries if b.total]
    if not completed:
        return "No block answered yet."
    latest = completed[-1]
    trend = " → ".join(f"{b.accuracy:.0%}" for b in completed[-4:])
    return (
        f"**{trend}** — last block {latest.correct}/{latest.total}. "
        f"{perception_trainer.chance_caption(latest.alternatives)}"
    )


def render_target_card(
    conn: sqlite3.Connection, target: dict[str, Any], still_flagged: set[str]
) -> None:
    """One queue item: what it is, why it is here, and what takes it off.

    Both halves are required by the brief and both are rendered from real values rather than
    described in prose — the evidence is the counts it was promoted on, and the rule is
    `practice_queue.graduation_rule`, which carries the actual thresholds.
    """
    kind = str(target["kind"])
    evidence = practice_queue.evidence_of(target)
    label = practice_queue.KIND_LABELS.get(kind, kind)

    # A shadowing passage has no perception trials and never will, so it does not go looking
    # for any — an empty summary would render "No block answered yet" under an item that has
    # no blocks to answer.
    if kind == practice_queue.SHADOW:
        summaries: list[Any] = []
    else:
        with _DB_LOCK:
            trials = [dict(row) for row in db.trials_for(conn, str(target["item"]))]
        summaries = practice_queue.summarise_blocks(trials)

    if kind == practice_queue.STRESS:
        decision = practice_queue.grade(
            target, summaries, still_flagged=str(target["item"]) in still_flagged
        )
    else:
        decision = practice_queue.grade(target, summaries)

    header = f"**{target['item']}** · {label}"
    if str(target["state"]) == practice_queue.GRADUATED:
        header += " · ✅ graduated"
    st.markdown(header)

    if kind not in (practice_queue.STRESS, practice_queue.SHADOW):
        st.caption(_accuracy_line(summaries))

    with st.expander("Why is this here, and what takes it off?"):
        st.markdown(f"**Why it is here.** {evidence.get('why') or 'No evidence recorded.'}")
        st.markdown(f"**What takes it off.** {practice_queue.graduation_rule(kind)}")
        st.markdown(f"**Where it stands.** {decision.reason}")
        if str(target["state"]) == practice_queue.GRADUATED:
            st.caption(practice_queue.review_horizon(int(target.get("reviews_passed") or 0)))
        added = str(target.get("added") or "")
        due_at = str(target.get("next_due") or "")
        st.caption(f"Added {added or 'unknown'} · next due {due_at or 'unscheduled'}")


def apply_decisions(
    conn: sqlite3.Connection,
    targets: list[dict[str, Any]],
    still_flagged: set[str],
    *,
    now: datetime,
) -> None:
    """Write the scheduling consequences of what the evidence now says.

    Separate from rendering so the page never shows one verdict while the database holds
    another, and so a graduation is persisted the moment it is earned rather than the next
    time someone happens to open the right expander.
    """
    for target in targets:
        kind = str(target["kind"])
        with _DB_LOCK:
            trials = [dict(row) for row in db.trials_for(conn, str(target["item"]))]
        summaries = practice_queue.summarise_blocks(trials)
        if kind == practice_queue.STRESS:
            decision = practice_queue.grade(
                target, summaries, still_flagged=str(target["item"]) in still_flagged
            )
        else:
            decision = practice_queue.grade(target, summaries)

        if decision.state == str(target["state"]) and not decision.regressed:
            continue
        with _DB_LOCK:
            db.update_target(
                conn,
                int(target["id"]),
                state=decision.state,
                next_due=practice_queue.next_due(decision, now=now, kind=kind),
                reviews_passed=decision.reviews_passed,
            )
        target["state"] = decision.state
        logger.info("Target %s → %s", target["item"], decision.state)


def render_today(
    conn: sqlite3.Connection, job: AssessJob | None = None, running: bool = False
) -> None:
    """The Today tab: the one due thing, then the target set and its rules.

    Takes the assessment job because shadowing assesses from this surface — it is the same
    single job the Practice tab drives, not a second one, so only one assessment can ever be
    in flight whichever tab started it.
    """
    st.subheader("Today")

    if st.session_state.get(BLOCK_KEY):
        render_block(conn)
        return

    if st.session_state.get(SHADOW_KEY):
        render_shadow(conn, job, running)
        return

    targets = sync_queue(conn)
    with _DB_LOCK:
        fingerprint = db.attempt_fingerprint(conn)
    found = queue_candidates(conn, fingerprint)
    still_flagged = {c.item for c in found if c.kind == practice_queue.STRESS}

    now = datetime.now(UTC)
    apply_decisions(conn, targets, still_flagged, now=now)

    # Promoted targets only. A shadowing passage is a standing practice, not something the
    # recordings promoted, so a queue holding nothing BUT one is still an empty queue — and
    # falling through here would answer "what am I doing today?" with "nothing due, they are
    # all on the review schedule" about targets that were never promoted in the first place.
    promoted = [t for t in targets if practice_queue.promotable(str(t["kind"]))]
    if not promoted:
        if fingerprint[1] == 0:
            st.info(
                "Nothing to practise yet, and that is the honest answer rather than a "
                "placeholder. Targets are promoted from your own assessed attempts — the "
                "sounds Azure actually flagged — so the queue has nothing to schedule until "
                "there is at least one. Record something on the **Practice** tab.",
                icon="🌱",
            )
        else:
            st.info(
                f"Nothing has recurred often enough to promote yet. A sound has to be "
                f"flagged in {utils.RECUR_ATTEMPTS} separate attempts before it becomes a "
                f"target — one bad reading is not a pattern. Keep practising on the "
                f"**Practice** tab.",
                icon="🌱",
            )
        # Shadowing is offered even here. It is the one practice on this page that needs no
        # history at all: it trains rhythm and intonation against a model rather than a sound
        # your own recordings flagged, so there is nothing for it to wait for.
        st.divider()
        render_shadow_offer(conn, targets, now=now)
        return

    ready = practice_queue.due(promoted, now=now)
    # Split by kind rather than by "not stress": a shadowing passage is also not stress, and
    # handing one to `start_block` would look for a substitution it does not have.
    trainable = [
        t
        for t in ready
        if practice_queue.promotable(str(t["kind"])) and str(t["kind"]) != practice_queue.STRESS
    ]
    drills = [t for t in ready if str(t["kind"]) == practice_queue.STRESS]

    if trainable:
        target = trainable[0]
        review = str(target["state"]) == practice_queue.GRADUATED
        trials = utils.PERCEPTION_REVIEW_TRIALS if review else utils.PERCEPTION_BLOCK_TRIALS
        st.markdown(
            f"### {'Spaced review' if review else 'Listening block'}: {target['item']}\n"
            f"{trials} trials. You hear one word and choose which of the pair it was, in "
            f"{len(perception_trainer.VOICES)} different voices."
        )
        st.caption(
            "Different voices on purpose: hearing a contrast from several talkers is what "
            "makes it carry over to new words and new speakers, rather than teaching you "
            "one synthesiser."
        )
        if st.button(
            "▶ Start the block", type="primary", disabled=utils.offline_mode(), key="start-block"
        ):
            start_block(conn, target, review=review)
        if utils.offline_mode():
            st.caption(
                "Disabled under OFFLINE_MODE: a block is live synthesis by definition and "
                "there is no fixture to replay for audio."
            )
    elif drills:
        render_stress_drill(conn, drills[0])
    else:
        st.success(
            "Nothing due today. The targets below are all either waiting on their next "
            "spaced review or already worked through.",
            icon="✅",
        )

    st.divider()
    render_shadow_offer(conn, targets, now=now)

    st.divider()
    # Shadowing is excluded from both lists on purpose. It is not one of the three slots — it
    # is never promoted into one and never graduates out of one — so counting it against
    # MAX_ACTIVE_TARGETS would retire a sound the recordings are still flagging.
    active = [t for t in promoted if str(t["state"]) == practice_queue.ACTIVE]
    graduated = [t for t in promoted if str(t["state"]) == practice_queue.GRADUATED]

    st.markdown(f"#### Working on ({len(active)} of {utils.MAX_ACTIVE_TARGETS})")
    st.caption(
        "Three at most, because a target set you cannot hold in your head while speaking is "
        "not a target set. Every one of them came from a sound your own recordings kept "
        "getting flagged on — nothing here is chosen for you from a list of what speakers of "
        "some language get wrong."
    )
    if not active:
        st.caption("Nothing active — everything promoted so far has graduated.")
    for target in active:
        render_target_card(conn, target, still_flagged)

    if graduated:
        st.markdown("#### Graduated, and still checked")
        st.caption(
            "A graduated contrast that is never re-tested is an unverified claim, so each "
            "one comes back at widening intervals rather than disappearing."
        )
        for target in graduated:
            render_target_card(conn, target, still_flagged)


def render_shadow_offer(
    conn: sqlite3.Connection, targets: list[dict[str, Any]], *, now: datetime
) -> None:
    """The shadowing section of Today: what is due, or the offer if nothing is on the list yet.

    Its own section rather than a fourth entry in the due list. Shadowing is a different kind
    of practice from a listening block — it needs no flagged history to exist, and it never
    graduates — so burying it behind whichever contrast happens to be due would hide the one
    thing here that is available on day one.
    """
    passages = shadow_passages()
    existing = {str(t["item"]): t for t in targets if str(t["kind"]) == practice_queue.SHADOW}
    ready = list(practice_queue.due(list(existing.values()), now=now))

    st.markdown("#### Shadowing")
    if ready:
        target = ready[0]
        title = str(target["item"])
        st.markdown(f"**Due: {title}**")
    elif existing:
        target = min(existing.values(), key=lambda row: str(row.get("next_due") or ""))
        title = str(target["item"])
        when = str(target.get("next_due") or "")[:10]
        st.markdown(f"**{title}** — next due {when or 'unscheduled'}.")
        st.caption(
            f"Nothing stops you doing it sooner. The {utils.SHADOW_INTERVAL_DAYS}-day gap is "
            f"there to leave room for cold reads in between, since a shadowed read with "
            f"nothing to compare it against says nothing."
        )
    else:
        title = next(iter(passages))
        st.caption(
            "Speak along with a native rendering of a passage instead of reading it and "
            "being marked afterwards. Rhythm, linking and intonation are not learned from a "
            "score. Nothing is scored while you shadow."
        )

    chosen = st.selectbox(
        "Passage",
        list(passages),
        index=list(passages).index(title) if title in passages else 0,
        key="shadow-passage",
    )
    if st.button("🎧 Shadow this passage", type="primary", key="shadow-start"):
        start_shadow(chosen, passages[chosen])
        st.rerun()


def render_stress_drill(conn: sqlite3.Connection, target: Mapping[str, Any]) -> None:
    """A stress item's due action: a drill, not a scored block.

    This is the honest consequence of a real gap rather than a design preference. Azure
    returns per-syllable accuracy but **no stress marks** — checked against the committed
    fixtures, where `unpredictable` comes back as five scored syllables and nothing else — so
    there is no way to ask "which syllable was stressed?" and know the right answer without a
    pronouncing dictionary or another recording, and a recording is speech-recognition spend
    that this feature does not make.
    """
    evidence = practice_queue.evidence_of(target)
    word = str(target["item"])
    syllable = evidence.get("syllable") or ""

    st.markdown(f"### Stress drill: {word}")
    if syllable:
        st.markdown(
            f"The syllable /{syllable}/ keeps scoring well below the rest of the word, which "
            f"is what a misplaced stress looks like in the data."
        )
    st.markdown(
        f"Say **{word}** three times, clapping once on the stressed syllable. Then say it "
        f"inside a sentence, keeping the same beat. Listen first — the voices below read it "
        f"differently from each other, and the stress is the thing they agree on."
    )
    for index, voice in enumerate(perception_trainer.VOICES):
        playback_buttons(conn, word, key_prefix=f"stress-{index}", label=f"{word} ({voice})")


def start_block(conn: sqlite3.Connection, target: Mapping[str, Any], *, review: bool) -> None:
    """Plan a block, buy the audio it needs, and put it on screen.

    All of the audio is synthesised **up front**, as one batch, rather than a clip per trial:
    a stall between trials is the thing most likely to make a daily habit stop being daily,
    and one batch means one visible charge instead of twenty small ones.
    """
    evidence = practice_queue.evidence_of(target)
    expected = str(evidence.get("expected") or "")
    produced = str(evidence.get("produced") or "")
    if not expected or not produced:
        st.error(
            "This target has no stored substitution to build a block from, so there is "
            "nothing to play. It was promoted before the evidence was recorded.",
            icon="🧩",
        )
        return

    with _DB_LOCK:
        heard = db.heard_stimuli(conn, str(target["item"]))
    try:
        block = perception_trainer.build_block(
            item=str(target["item"]),
            expected=expected,
            produced=produced,
            heard=heard,
            review=review,
        )
    except perception_trainer.BlockError as exc:
        st.error(str(exc), icon="🚫")
        return

    audio, failure = buy_block_audio(conn, block)
    if failure is not None:
        icon, message = failure
        st.error(message, icon=icon)
        return

    st.session_state[BLOCK_KEY] = {
        "block": block,
        "target_id": int(target["id"]),
        "audio": audio,
        "index": 0,
        "answers": [],
        "revealed": False,
        "block_id": uuid.uuid4().hex,
    }
    st.rerun()


def synthesise_clip(
    conn: sqlite3.Connection,
    text: str,
    *,
    voice: str,
    slow: bool = False,
) -> tuple[bytes | None, tuple[str, str] | None]:
    """Buy one clip from Azure, meter it, and put it on disk. Returns (audio, failure).

    Extracted so the perception block and the shadowing model read the meter identically —
    two callers charging by two slightly different rules is how a spend guard stops being
    one. It renders nothing, for the reason `play()` returns its failures rather than showing
    them: every call site is inside a narrow column or a progress loop.

    The **pre-flight belongs to the caller**, not here: both callers buy a batch and have to
    price the whole batch before the first call, or the guard would approve a run whose real
    charge lands past the budget partway through.
    """
    attempts_made = 0

    def note_attempt(attempt: int) -> None:
        nonlocal attempts_made
        attempts_made = attempt

    payload_characters = len(tts.payload_for(text, slow=slow, voice=voice))
    try:
        result = tts.synthesise(text, voice=voice, slow=slow, on_attempt=note_attempt)
    except utils.ConfigError as exc:
        return None, ("🔑", str(exc))
    except (
        utils.PermanentError,
        utils.TransientError,
        tts.SynthesisError,
        speech_analyzer.AssessmentError,
    ) as exc:
        if speech_analyzer.is_quota_exhausted(exc):
            budget.mark_quota_exhausted()
        if attempts_made:
            # Reached Azure and failed: the text was still sent and may be charged.
            db.record_tts_usage(conn, characters=payload_characters * attempts_made, voice=voice)
        logger.error("Synthesis failed on %r in %s", text[:40], voice, exc_info=True)
        return None, ("🔇", utils.redact(str(exc)))

    db.record_tts_usage(
        conn,
        characters=result.characters * max(result.attempts, 1),
        voice=result.voice,
    )
    tts.store_audio(voice, text, result.audio, rate=tts.SLOW_RATE if slow else tts.NORMAL_RATE)
    return result.audio, None


def buy_block_audio(
    conn: sqlite3.Connection, block: Any
) -> tuple[dict[tuple[str, str], bytes], tuple[str, str] | None]:
    """Fetch every clip the block needs, from disk where possible and Azure otherwise.

    **The disk lookup happens before the pre-flight and before the meter**, which is the same
    ordering `play()` depends on and for the same reason: Streamlit re-runs this script on
    every interaction, so pricing a call ahead of the cache check would climb the meter while
    nothing was synthesised.

    Plain text at the normal rate, never SSML. The meter charges the payload actually sent and
    SSML bills its full markup — one eight-character word wrapped in SSML measured 167
    characters against the word's own 8.
    """
    audio: dict[tuple[str, str], bytes] = {}
    missing: list[tuple[str, str]] = []

    for text, voice in perception_trainer.stimuli(block):
        stored = tts.cached_audio(voice, text)
        if stored is not None:
            audio[(text, voice)] = stored
        else:
            missing.append((text, voice))

    if not missing:
        logger.info("Block audio served entirely from the disk cache; nothing charged.")
        return audio, None

    characters = sum(len(text) for text, _ in missing)
    try:
        budget.preflight_tts(conn, characters * utils.MAX_SYNTHESIS_ATTEMPTS)
    except budget.BudgetError as exc:
        return audio, ("💸", str(exc))

    progress = st.progress(0.0, text=f"Preparing {len(missing)} clips…")
    for done, (text, voice) in enumerate(missing, start=1):
        clip, failure = synthesise_clip(conn, text, voice=voice)
        if clip is None:
            progress.empty()
            return audio, failure
        audio[(text, voice)] = clip
        progress.progress(done / len(missing), text=f"Preparing {len(missing)} clips…")

    progress.empty()
    logger.info(
        "Block audio: %d clips from cache, %d synthesised (%d characters)",
        len(audio) - len(missing),
        len(missing),
        characters,
    )
    return audio, None


def render_block(conn: sqlite3.Connection) -> None:
    """Run one block: play, choose, score, reveal, next."""
    state = st.session_state[BLOCK_KEY]
    block = state["block"]
    index = int(state["index"])
    total = len(block.trials)

    st.markdown(f"### {'Spaced review' if block.review else 'Listening block'}: {block.item}")

    if index >= total:
        finish_block(conn)
        return

    trial = block.trials[index]
    st.progress((index) / total, text=f"Trial {index + 1} of {total}")

    audio = state["audio"].get((trial.word, trial.voice))
    if audio:
        # Keyed on the trial so Streamlit builds a fresh element each time; re-using one
        # would leave the previous clip in place and autoplay would not fire again.
        st.audio(audio, format="audio/wav", autoplay=not state["revealed"])

    if not state["revealed"]:
        st.markdown("**Which word was that?**")
        columns = st.columns(len(trial.alternatives))
        for column, option in zip(columns, trial.alternatives):
            with column:
                if st.button(option, key=f"choice-{index}-{option}", width="stretch"):
                    answer_trial(conn, option)
                    st.rerun()
        if st.button("🔁 Play it again", key=f"replay-{index}"):
            st.rerun()
    else:
        correct = state["answers"][-1]
        if correct:
            st.success(f"Yes — that was **{trial.word}**.", icon="✅")
        else:
            st.error(f"That was **{trial.word}**, not **{trial.other}**.", icon="❌")

        note = phoneme_reference.why_it_matters(block.expected, block.produced)
        if note:
            st.caption(note)

        left, right = st.columns(2)
        other_audio = state["audio"].get((trial.other, trial.voice))
        with left:
            st.caption(f"Heard: {trial.word}")
            if audio:
                st.audio(audio, format="audio/wav")
        with right:
            st.caption(f"The other one: {trial.other}")
            if other_audio:
                st.audio(other_audio, format="audio/wav")

        if st.button("Next →", type="primary", key=f"next-{index}"):
            state["index"] = index + 1
            state["revealed"] = False
            st.rerun()

    if st.button("Stop the block", key=f"abandon-{index}"):
        st.session_state[BLOCK_KEY] = None
        st.rerun()
    st.caption(
        "Stopping keeps the answers you have already given — they are stored as you give "
        "them — but a part-finished block does not count toward graduating the contrast."
    )


def answer_trial(conn: sqlite3.Connection, chosen: str) -> None:
    """Record one answer, as it is given.

    Written per trial rather than at the end of the block, so an abandoned block keeps its
    evidence. Whether it earns a *verdict* is a separate question, decided by
    `practice_queue` from the trial count — the evidence is kept either way.
    """
    state = st.session_state[BLOCK_KEY]
    block = state["block"]
    trial = block.trials[int(state["index"])]
    correct = chosen == trial.word

    state["answers"].append(correct)
    state["revealed"] = True

    with _DB_LOCK:
        db.record_trial(
            conn,
            block_id=str(state["block_id"]),
            target_id=int(state["target_id"]),
            item=block.item,
            word=trial.word,
            voice=trial.voice,
            novel=trial.novel,
            alternatives=len(trial.alternatives),
            answered=chosen,
            correct=correct,
            review=bool(block.review),
        )


def finish_block(conn: sqlite3.Connection) -> None:
    """The end of a block: the score, always with its chance floor, then the verdict."""
    state = st.session_state[BLOCK_KEY]
    block = state["block"]
    result = perception_trainer.score(
        state["answers"],
        alternatives=block.alternatives,
        novel=block.novel_count,
        planned=len(block.trials),
    )

    st.metric(
        "This block",
        f"{result.accuracy:.0%}",
        delta=f"{(result.accuracy - result.chance) * 100:+.0f} pts vs guessing",
    )
    st.caption(
        f"{result.correct} of {result.total} right. "
        f"{perception_trainer.chance_caption(result.alternatives)}"
    )
    if not result.above_chance:
        st.warning(
            "At or below the chance floor, which means this block says nothing yet about "
            "whether you can hear the contrast — it is what guessing looks like.",
            icon="🎲",
        )
    st.caption(
        f"{result.novel} of {result.total} trials used a word-and-voice combination you had "
        f"never heard before ({result.novel_fraction:.0%}). Graduation counts blocks made "
        f"mostly of those, because getting familiar clips right is a memory result, not a "
        f"hearing one."
    )

    now = datetime.now(UTC)
    with _DB_LOCK:
        db.update_target(conn, int(state["target_id"]), last_seen=db.utc_now_iso())
        target = next(
            (dict(row) for row in db.targets(conn) if int(row["id"]) == int(state["target_id"])),
            None,
        )
        trials = [dict(row) for row in db.trials_for(conn, block.item)]

    if target is not None:
        decision = practice_queue.grade(target, practice_queue.summarise_blocks(trials))
        st.info(decision.reason, icon="🎯")
        if decision.state != str(target["state"]) or decision.regressed:
            with _DB_LOCK:
                db.update_target(
                    conn,
                    int(target["id"]),
                    state=decision.state,
                    next_due=practice_queue.next_due(decision, now=now, kind=str(target["kind"])),
                    reviews_passed=decision.reviews_passed,
                )

    if st.button("Done", type="primary", key="finish-block"):
        st.session_state[BLOCK_KEY] = None
        st.rerun()


# --- Shadowing --------------------------------------------------------------------------------
# The one surface in this app where practice happens WHILE speaking rather than afterwards.
# Nothing here scores anything: no meter, no per-phrase feedback, no accuracy read-out. When
# a speak-along read is finished it goes through the ordinary Mode B path and is rendered by
# the ordinary `render_result`; the shadowed-versus-cold comparison lives on the Progress tab,
# where every other measurement lives. See `shadowing.py` for why only one of the two modes is
# ever assessed.

SHADOW_KEY = "shadow_session"


def shadow_passages() -> dict[str, str]:
    """What can be shadowed: the paragraph presets, benchmark first.

    Not a second list. `PRESETS[Mode.PARAGRAPH]` already holds exactly these, and a passage
    that differed from the one the Practice tab offers by a single word would silently pair
    against nothing — the comparison matches a shadowed read to a cold one by normalised text.
    """
    return PRESETS[Mode.PARAGRAPH]


def start_shadow(title: str, passage: str) -> None:
    """Open a shadowing session in place, the way a listening block opens in place.

    Streamlit exposes no way to select a tab programmatically, so a "go to the Shadow tab"
    button cannot exist. Rendering the session inside Today instead is the pattern the
    perception block already established.
    """
    st.session_state[SHADOW_KEY] = {
        "title": title,
        "passage": passage,
        "mode": shadowing.SIMULTANEOUS,
        "slow": False,
        "audio": {},
        "key": None,
    }


def record_shadow_session(
    conn: sqlite3.Connection, title: str, passage: str, *, now: datetime
) -> None:
    """Put this passage on the queue and push its next due date out. Once per finished read.

    The target is created **on first use, never by `promote()`**, and "use" means a read that
    was actually assessed rather than a session that was opened and abandoned. The brief's
    rule that the queue never invents a target is about claims made from the user's own
    flagged history — a standing practice makes no such claim, so it does not belong in
    promotion, and `practice_queue` keeps `SHADOW` out of `KIND_ORDER` for the same reason.
    """
    session = shadowing.Session(
        title=title,
        passage=passage,
        mode=shadowing.SIMULTANEOUS,
        slow=False,
    )
    with _DB_LOCK:
        target_id = db.upsert_target(
            conn,
            item=title,
            kind=practice_queue.SHADOW,
            evidence=shadowing.evidence_for(session),
        )
        db.update_target(
            conn,
            target_id,
            last_seen=db.utc_now_iso(),
            next_due=practice_queue.next_due(
                practice_queue.Decision(practice_queue.ACTIVE, ""),
                now=now,
                kind=practice_queue.SHADOW,
            ),
        )
    logger.info("Shadowing session recorded for %r", title)


def buy_shadow_audio(
    conn: sqlite3.Connection, passage: str, *, mode: str, slow: bool
) -> tuple[bytes | None, tuple[str, str] | None]:
    """Fetch the model audio for one shadowing session. Returns (audio, failure).

    Same ordering rule the block buyer and `play()` both depend on: **the disk cache is
    checked before the pre-flight and before the meter**, because Streamlit re-runs this
    script on every interaction and pricing ahead of the cache lookup would climb the meter
    while nothing was synthesised.

    Simultaneous mode buys one clip of the whole passage. Echo mode buys one clip per phrase
    and stitches them with `audio_utils.echo_track`; the phrase clips are cached individually,
    the stitched track is not, because it is derived and cheap to rebuild from them.
    """
    voice = tts.voice_name()
    rate = tts.SLOW_RATE if slow else tts.NORMAL_RATE
    texts = [passage] if mode == shadowing.SIMULTANEOUS else shadowing.phrases(passage)
    if not texts:
        return None, ("🧩", "There is nothing in this passage to synthesise.")

    clips: list[bytes | None] = [tts.cached_audio(voice, text, rate) for text in texts]
    missing = [text for text, clip in zip(texts, clips) if clip is None]

    if missing:
        characters = sum(len(tts.payload_for(text, slow=slow, voice=voice)) for text in missing)
        try:
            budget.preflight_tts(conn, characters * utils.MAX_SYNTHESIS_ATTEMPTS)
        except budget.BudgetError as exc:
            return None, ("💸", str(exc))

        progress = st.progress(0.0, text=f"Preparing {len(missing)} clip(s)…")
        done = 0
        for index, text in enumerate(texts):
            if clips[index] is not None:
                continue
            clip, failure = synthesise_clip(conn, text, voice=voice, slow=slow)
            if clip is None:
                progress.empty()
                return None, failure
            clips[index] = clip
            done += 1
            progress.progress(done / len(missing), text=f"Preparing {len(missing)} clip(s)…")
        progress.empty()
        logger.info(
            "Shadow audio: %d clip(s) synthesised, %d from the disk cache",
            len(missing),
            len(texts) - len(missing),
        )
    else:
        logger.info("Shadow audio served entirely from the disk cache; nothing charged.")

    ready = [clip for clip in clips if clip is not None]
    if mode == shadowing.SIMULTANEOUS:
        return ready[0], None
    try:
        return audio_utils.echo_track(ready, tail_ms=shadowing.ECHO_TAIL_MS), None
    except audio_utils.AudioError as exc:
        return None, ("🔇", str(exc))


def render_shadow(conn: sqlite3.Connection, job: AssessJob | None, running: bool) -> None:
    """One shadowing session, rendered in place inside Today."""
    state = st.session_state[SHADOW_KEY]
    passage = str(state["passage"])

    st.markdown(f"### Shadowing: {state['title']}")
    st.caption(shadowing.NOT_A_MEASUREMENT)
    st.warning(shadowing.HEADPHONES, icon="🎧")

    mode = st.radio(
        "How",
        list(shadowing.MODE_LABELS),
        format_func=lambda value: shadowing.MODE_LABELS[value],
        horizontal=True,
        key="shadow-mode",
        disabled=running,
    )
    slow = st.checkbox("Slow it down (35% slower)", key="shadow-slow", disabled=running)
    st.caption(shadowing.SLOW_NOTE)
    state["mode"], state["slow"] = mode, slow

    with st.expander("The passage"):
        st.write(passage)

    offline = utils.offline_mode()
    cached = state["audio"].get((mode, slow))

    if cached is None:
        phrase_count = len(shadowing.phrases(passage))
        st.markdown(
            "The model is synthesised once and kept on disk, so this is the only time it "
            "costs anything."
            + (
                f" Echo mode builds it from {phrase_count} phrases."
                if mode == shadowing.ECHO
                else ""
            )
        )
        if st.button(
            "🎧 Prepare the model",
            type="primary",
            disabled=offline or running,
            key="shadow-prepare",
        ):
            audio, failure = buy_shadow_audio(conn, passage, mode=mode, slow=slow)
            if failure is not None:
                icon, message = failure
                st.error(message, icon=icon)
            else:
                state["audio"][(mode, slow)] = audio
                st.rerun()
        if offline:
            st.caption(
                "Disabled under OFFLINE_MODE: the model is a live synthesis by definition and "
                'there is no fixture to replay for audio, the same rule "Hear it" follows.'
            )
    elif mode == shadowing.ECHO:
        st.markdown(shadowing.ECHO_STEPS)
        st.audio(cached, format="audio/wav")
    else:
        st.markdown(shadowing.SIMULTANEOUS_STEPS)
        # The player and the recorder are both on screen BEFORE recording starts, with no
        # button between them and no autoplay. `st.audio_input` holds a live MediaRecorder in
        # the browser and a Streamlit rerun re-renders that component, so anything that reruns
        # between pressing record and pressing play would cut the take in half.
        st.audio(cached, format="audio/wav")
        recording = st.audio_input(
            "Record yourself speaking along",
            key=f"shadow-recording-{_generation('shadow-recording')}",
        )
        render_shadow_assess(conn, state, recording, job, running)

    st.divider()
    if st.button("← Back to Today", key="shadow-back", disabled=running):
        st.session_state[SHADOW_KEY] = None
        st.session_state["now_playing"] = None
        # The result goes with the session that produced it. Leaving it in `last_key` would
        # surface a shadowed read's report on the Practice tab, which did not produce it.
        if st.session_state.get(RESULT_OWNER_KEY) == SHADOW_OWNER:
            st.session_state["last_key"] = None
            st.session_state[RESULT_OWNER_KEY] = None
        st.rerun()


def render_shadow_assess(
    conn: sqlite3.Connection,
    state: dict[str, Any],
    recording: Any,
    job: AssessJob | None,
    running: bool,
) -> None:
    """Send a finished speak-along read down the ordinary Mode B path, tagged.

    Nothing about the analysis changes here — this is the same `prepare_audio` and the same
    `start_assessment` the Practice tab calls, with one tag added. The result is rendered by
    the same `render_result` too: no scoring surface is written for shadowing.
    """
    left, middle, _ = st.columns([1, 1, 3])
    with left:
        assess_clicked = st.button(
            "Assess this read",
            type="primary",
            width="stretch",
            disabled=running or recording is None,
            key="shadow-assess",
        )
    with middle:
        stop_clicked = (
            st.button("🛑 Stop", width="stretch", key="shadow-stop") if running else False
        )
    if recording is not None and not running:
        st.button(
            "🗑️ Delete recording",
            key="shadow-delete",
            on_click=_bump,
            args=("shadow-recording",),
            help="Discard this take and shadow it again. The model stays prepared.",
        )

    if stop_clicked and job is not None:
        job.cancel_event.set()

    # Guarded on state rather than on the button's `disabled` flag, for the reason the
    # Practice tab already documents: a click is handled in the rerun that drew the button.
    if assess_clicked and not running and recording is not None:
        passage = str(state["passage"])
        if validate_reference(passage):
            audio_bytes = recording.getvalue()
            key = utils.attempt_hash(passage, audio_bytes, Mode.PARAGRAPH)
            state["key"] = key
            st.session_state[RESULT_OWNER_KEY] = SHADOW_OWNER
            cached = _cache_get(key)
            if cached is not None:
                st.session_state["last_key"] = key
                st.session_state["now_playing"] = None
            else:
                wav_bytes, seconds = prepare_audio(conn, audio_bytes, Mode.PARAGRAPH)
                if wav_bytes is not None and seconds is not None:
                    start_assessment(
                        conn,
                        wav_bytes,
                        seconds,
                        passage,
                        Mode.PARAGRAPH,
                        key,
                        tags=(shadowing.SHADOW_TAG,),
                    )
                    st.rerun()

    if running:
        st.info("Assessing your shadowed read… click Stop to cancel.", icon="⏳")
        # This tab body runs before the Practice tab's identical poll, and `st.rerun()` ends
        # the script, so exactly one of the two ever fires.
        time.sleep(JOB_POLL_SECONDS)
        st.rerun()

    if state.get("key"):
        finished = _cache_get(str(state["key"]))
        if finished is not None:
            # Once per finished read, not once per rerun: this tab body re-executes on every
            # interaction, and pushing the due date out each time would mean the passage was
            # never due again.
            if finished.attempt_id and state.get("scheduled") != finished.attempt_id:
                record_shadow_session(
                    conn,
                    str(state["title"]),
                    str(state["passage"]),
                    now=datetime.now(UTC),
                )
                state["scheduled"] = finished.attempt_id
            st.divider()
            st.caption(
                "Stored as an ordinary paragraph attempt, tagged as shadowed. It is kept off "
                "the cold trajectory on the Progress tab and compared against it there."
            )
            render_result(conn, finished, recording)


def render_shadow_comparison(conn: sqlite3.Connection, rows: Any) -> None:
    """Shadowed against cold: the acceptance test this feature carries, on screen.

    Both reads are already stored attempts, so this costs nothing to draw. What it is
    expected to show — and what it means if it does not — is written out in
    `progress_view`'s section header rather than only in a plan file, because an outcome
    nobody wrote down in advance gets explained away when it arrives.
    """
    st.subheader("Shadowed against cold")
    frame = progress_view.shadow_pairs(rows)
    st.markdown(progress_view.shadow_summary(frame))

    orphans = progress_view.unpaired_passages(rows)
    if orphans:
        st.caption(
            "Shadowed but never read cold, so there is nothing to compare: "
            + ", ".join(f"*{name}*" for name in orphans)
            + (
                ". Read it on the Practice tab without the model."
                if len(orphans) == 1
                else ". Read one of them on the Practice tab without the model."
            )
        )

    if frame.empty:
        return

    st.caption(
        "Shadowing should score higher on fluency and prosody than a cold read — the model "
        "carries the timing. **The gap is meant to narrow**, as the shadowed pattern becomes "
        "the cold-read pattern; that narrowing is the only evidence the practice transfers. "
        "A gap that stays flat over weeks means the model is a crutch, not a teacher, and "
        "this chart is where that would show. Accuracy is deliberately not compared — "
        "shadowing trains delivery, not articulation."
    )
    st.altair_chart(progress_view.shadow_gap_chart(frame), width="stretch")


def render_practice(conn: sqlite3.Connection, job: AssessJob | None, running: bool) -> None:
    """The Practice tab: record or upload, assess, and read the result.

    Unchanged in substance from when this was the whole page — it moved into a tab so the
    progress view could have a surface of its own rather than sitting under every result.
    """
    if utils.offline_mode():
        st.info(
            "OFFLINE_MODE is on: results are replayed from the committed fixture and no "
            "audio is sent anywhere.",
            icon="📴",
        )

    mode = MODE_LABELS[st.radio("Mode", list(MODE_LABELS), horizontal=True)]
    st.caption(
        "Unscripted mode — free speech with vocabulary, grammar and topic scores — is not "
        "built yet."
    )

    presets = PRESETS[mode]
    st.selectbox(
        "Practice text",
        ["Write my own", *presets],
        key=PRESET_KEY,
        on_change=_apply_preset,
        args=(mode,),
    )
    reference_text = st.text_area("Reference text", key=TEXT_KEY, height=140)

    audio = st.audio_input("Record", key=f"recording-{_generation('recording')}")
    if audio is not None:
        st.button(
            "🗑️ Delete recording",
            on_click=_delete_recording,
            disabled=running,
            help="Discard this take and record again. Your reference text is kept.",
        )
    uploaded = st.file_uploader(
        "…or upload a file",
        type=list(audio_utils.SUPPORTED_UPLOAD_TYPES),
        key=f"upload-{_generation('upload')}",
    )
    source = audio or uploaded

    # Nothing but the buttons goes inside these columns: a helper called within `with
    # column:` appends into it, and an alert laid out at a button's width is unreadable.
    left, middle, right = st.columns([1, 1, 3])
    with left:
        assess_clicked = st.button(
            "Assess",
            type="primary",
            disabled=running or source is None,
            width="stretch",
        )
    with middle:
        stop_clicked = st.button("🛑 Stop", width="stretch") if running else False
    with right:
        st.button("↺ Reset", on_click=_reset_form, disabled=running)

    if stop_clicked and job is not None:
        job.cancel_event.set()

    # Guarded on state, not on the button's `disabled` flag: a click is handled in the same
    # rerun that drew the button, so the on-screen button is still enabled until the next
    # one. Without this a fast double-click starts two assessments.
    if assess_clicked and not running and source is not None:  # noqa: SIM102
        # Kept nested deliberately: the guard above is about session state, this one is
        # about the input, and `validate_reference` renders the error as a side effect.
        if validate_reference(reference_text):
            audio_bytes = source.getvalue()
            key = utils.attempt_hash(reference_text, audio_bytes, mode)
            st.session_state[RESULT_OWNER_KEY] = PRACTICE_OWNER
            cached = _cache_get(key)
            if cached is not None:
                # The fastest path in the app: one click to retry the same drill sentence.
                # No thread, no polling, no extra rerun.
                st.session_state["last_key"] = key
                st.session_state["now_playing"] = None
            else:
                wav_bytes, seconds = prepare_audio(conn, audio_bytes, mode)
                if wav_bytes is not None and seconds is not None:
                    start_assessment(conn, wav_bytes, seconds, reference_text, mode, key)
                    st.rerun()

    if running:
        st.info("Assessing… click Stop to cancel.", icon="⏳")
        # The only way to wait on a worker thread here: end this pass and start another.
        # Each rerun re-renders Stop and picks up a click made since the last one.
        time.sleep(JOB_POLL_SECONDS)
        st.rerun()

    # Only the surface that produced the result renders it. Both tab bodies execute on every
    # rerun, so rendering it in both would draw the same report twice and — because
    # `render_result` derives its widget keys from the attempt — raise a duplicate-key error
    # rather than merely looking odd.
    last_key = st.session_state.get("last_key")
    owner = st.session_state.get(RESULT_OWNER_KEY)
    if last_key and owner != SHADOW_OWNER:
        cached = _cache_get(last_key)
        if cached is not None:
            st.divider()
            render_result(conn, cached, source)


def render_history(conn: sqlite3.Connection) -> None:
    """The usage meter and the recent-attempts table, under the progress charts."""
    st.divider()
    # Locked: a background assessment may be inserting its row against these same reads.
    with _DB_LOCK:
        summary = budget.summary_line(conn)
        recent = db.recent_attempts(conn, limit=5)
    st.caption(summary)
    if recent:
        with st.expander(f"History ({len(recent)} most recent)"):
            st.dataframe(
                [
                    {
                        "When": r["created_at"],
                        "Mode": r["mode"],
                        "Pron": r["pron_score"],
                        "Accuracy": r["accuracy"],
                        "Prosody": r["prosody"],
                        "Offline": bool(r["offline"]),
                    }
                    for r in recent
                ],
                hide_index=True,
                width="stretch",
            )


def render() -> None:
    st.set_page_config(page_title=PAGE_TITLE, page_icon=PAGE_ICON, layout="centered")
    st.title(f"{PAGE_ICON} {PAGE_TITLE}")
    st.caption("Personal English pronunciation and delivery coach — en-US.")

    check_startup()
    conn = get_connection()

    # Before any widget exists, so `running` below describes this pass and not the last one.
    collect_finished_job()
    job: AssessJob | None = st.session_state.get("assess_job")
    running = job is not None

    # Four tabs, not four pages: `AppTest.from_file` addresses one script and the bare
    # `render()` below is the entry point, so `st.navigation`/`pages/` would cost more than
    # it buys. Note Streamlit executes EVERY tab body on every rerun — which is why both the
    # progress view's re-parse and the queue's candidate ranking are cached rather than
    # recomputed on each of the 0.4 s polls.
    #
    # `Today` is first deliberately. Opening the app should answer "what am I doing today?"
    # rather than present a blank textarea; the textarea is one click away, which is where a
    # thing you reach for on purpose belongs.
    today_tab, practice_tab, progress_tab, accent_tab = st.tabs(
        ["Today", "Practice", "Progress", "Accent"]
    )
    with today_tab:
        render_today(conn, job, running)
    with practice_tab:
        render_practice(conn, job, running)
    with progress_tab:
        render_progress(conn)
        render_history(conn)
    with accent_tab:
        render_accent(conn)


render()
