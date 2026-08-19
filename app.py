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
import logging
import sqlite3
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

import streamlit as st

import ai_coach
import audio_utils
import budget
import db
import fallback_coach
import progress_view
import speech_analyzer
import tts
import utils
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
        "Th (/θ/, /ð/)":
            "These three brothers thought the weather was worth the trouble.",
        "V versus W":
            "Very well, we will invite the whole village to the west wing.",
        "Short a versus short e (/æ/, /ɛ/)":
            "That bad man had a red cap and a black pen in his hand.",
        "Sibilants (/s/, /ʃ/, /z/, /dʒ/)":
            "She chose the usual visual measure just as the season closed.",
    },
    Mode.PARAGRAPH: {
        # First, and deliberately so. The progress view identifies a benchmark read by
        # matching this text, so it has to be selected rather than typed from memory — a
        # hand-typed near-copy would quietly start a second series.
        progress_view.BENCHMARK_TITLE: progress_view.BENCHMARK_PASSAGE,
        "Mixed diagnostic paragraph":
            "There are three things I think about whenever I have to explain my work to "
            "someone else. The first is whether the other person actually needs the "
            "detail, or whether they would rather hear the result and move on. The "
            "second is that I tend to speak faster when I am nervous, which makes the "
            "ends of my words disappear. The third is that I value being understood far "
            "more than sounding clever. When I remember all three, the conversation goes "
            "well. When I forget them, I watch the listener's face change and I know I "
            "have lost them somewhere in the middle of a long sentence.",
        "Workplace explanation":
            "The problem was not that the tests failed. The problem was that they passed "
            "for the wrong reason, and nobody thought to check. We had been measuring "
            "whether the service responded at all, rather than whether it responded with "
            "the right thing. Those are very different questions. Once we changed what we "
            "measured, the same code that had looked healthy for months started failing "
            "immediately, which was uncomfortable but useful. I would rather find a bug "
            "on a Wednesday afternoon than have a customer find it for me on a weekend.",
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


def lru_get(cache: "OrderedDict[Any, Any]", key: Any) -> Any | None:
    """Read an entry and mark it most-recently-used.

    Pure, so the eviction policy is testable without a Streamlit runtime.
    """
    if key not in cache:
        return None
    cache.move_to_end(key)
    return cache[key]


def lru_put(cache: "OrderedDict[Any, Any]", key: Any, value: Any, limit: int) -> None:
    """Store an entry, evicting the *least recently used* once over `limit`.

    LRU rather than insertion order because the drill loop re-uses one entry over and over:
    the same sentence assessed again, the same flagged word played again. Evicting by
    insertion order would drop exactly the entry being re-used, and pay to rebuild it.
    """
    cache[key] = value
    cache.move_to_end(key)
    while len(cache) > limit:
        cache.popitem(last=False)


def _session_cache(name: str) -> "OrderedDict[Any, Any]":
    if name not in st.session_state:
        st.session_state[name] = OrderedDict()
    return st.session_state[name]


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

    pairs = [(expected, score) for expected, _produced, score in speech_analyzer.phoneme_pairs(word) if expected]
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
        parts.append(f'<div>{symbol_cells}</div><div>{score_cells}</div>')

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
    pairs = [p for p in speech_analyzer.phoneme_pairs(word) if p[2] is not None]
    if not pairs:
        return ""
    expected, produced, score = min(pairs, key=lambda p: p[2])
    if produced:
        return f"/{expected}/ → sounded like /{produced}/"
    return f"/{expected}/ ({score:.0f})"


# --- Playback ---------------------------------------------------------------------------------


def play(conn: sqlite3.Connection, text: str, *, slow: bool, label: str,
         source: str) -> tuple[str, str] | None:
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
        except (utils.PermanentError, utils.TransientError, tts.SynthesisError,
                speech_analyzer.AssessmentError) as exc:
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
                    conn, characters=payload_characters * attempts_made,
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


def playback_buttons(
    conn: sqlite3.Connection, text: str, *, key_prefix: str, label: str
) -> None:
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
        if st.button("🔊 Hear it", key=f"{key_prefix}-normal", disabled=offline,
                     width="stretch"):
            failure = play(conn, text, slow=False, label=label, source=key_prefix)
    with right:
        if st.button("🐢 Slowly", key=f"{key_prefix}-slow", disabled=offline,
                     width="stretch"):
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
    choice = st.session_state.get(PRESET_KEY)
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
            "The reference text contains digits. Azure normalises \"33\" and "
            "\"thirty-three\" differently, which can throw the word alignment off — "
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


def run_assessment_job(
    conn: sqlite3.Connection,
    wav_bytes: bytes,
    seconds: float,
    reference_text: str,
    mode: Mode,
    cancel_event: threading.Event,
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
                wav_path, reference_text, mode,
                cancel_event=cancel_event, on_attempt=note_attempt,
            )

        if cancel_event.is_set():
            # Drill cannot be interrupted mid-call, so a stop clicked while Azure was
            # answering lands here: the result arrived, and it is thrown away unrecorded.
            return AssessOutcome(cancelled=True, reached_azure=reached_azure)

        with _DB_LOCK:
            attempt_id = db.record_attempt(
                conn,
                mode=mode,
                reference_text=reference_text,
                recognised_text=assessment.recognised_text,
                # Attempts, not successes: a retry re-uploads the same audio. Offline
                # replays report zero attempts and are excluded from the meter anyway.
                audio_seconds=seconds * max(assessment.attempts, 1),
                audio_sha256=utils.sha256_bytes(wav_bytes),
                overall_scores=assessment.overall_scores,
                azure_raw=assessment.raw if len(assessment.raw) > 1 else assessment.raw[0],
                offline=assessment.offline,
            )
        return AssessOutcome(assessment=assessment, attempt_id=attempt_id)

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
    except Exception as exc:  # noqa: BLE001 — nothing may escape a worker thread
        logger.error("Unexpected assessment failure", exc_info=True)
        return AssessOutcome(
            error=("🚫", f"{type(exc).__name__}: {utils.redact(str(exc))}")
        )


def start_assessment(
    conn: sqlite3.Connection, wav_bytes: bytes, seconds: float,
    reference_text: str, mode: Mode, key: str,
) -> None:
    """Spawn the worker for one assessment and remember it for the poll loop."""
    cancel_event = threading.Event()
    job = AssessJob(
        cancel_event=cancel_event, key=key, reference_text=reference_text, mode=mode,
    )

    def work() -> None:
        # Written once, before the thread ends, so the poll loop never reads a half-set job.
        job.outcome = run_assessment_job(
            conn, wav_bytes, seconds, reference_text, mode, cancel_event
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

    _cache_put(CachedAttempt(
        key=job.key, assessment=outcome.assessment, reference_text=job.reference_text,
        attempt_id=outcome.attempt_id, mode=job.mode,
    ))
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


def render_scores(assessment) -> None:
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
            for label, key in
            [("Accuracy score", "accuracy"), ("Fluency score", "fluency"),
             ("Prosody score", "prosody")]
        ),
        unsafe_allow_html=True,
    )
    st.caption(
        "Bands follow Azure's own score interpretation — under 60 low, 60-79 fair, 80-89 "
        "good, 90-100 excellent. A different convention from the word/phoneme colours "
        "further down, which are heuristics this tool chose."
    )


def render_error_counts(assessment) -> None:
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
        return cached

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
        except Exception as exc:  # noqa: BLE001 — a report is promised on every assessment
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
            f'{rank}. /{html.escape(fix.expected_phoneme)}/ '
            f'<span style="opacity:0.55;">→</span> '
            f'/{html.escape(fix.produced_phoneme)}/</div>',
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


def render_diff(assessment, reference_text: str) -> None:
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


def render_colour_coded(assessment) -> None:
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


def render_delivery(assessment) -> None:
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


def render_result(conn: sqlite3.Connection, entry: CachedAttempt, source) -> None:
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


# --- The progress view ----------------------------------------------------------------------


@st.cache_data(show_spinner=False)
def parsed_attempts(_conn: sqlite3.Connection, fingerprint: tuple[int, int]):
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
    st.altair_chart(progress_view.score_chart(frame), use_container_width=True)

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
            f"Benchmark passage last read {when}. The faint points behind it are free "
            f"practice on other texts — shown for context, never comparable to the line or "
            f"to each other, since Drill and Paragraph scores are computed differently."
        )

    parsed = parsed_attempts(conn, fingerprint)
    phonemes = progress_view.flagged_phonemes(parsed)
    words = progress_view.flagged_words(parsed)

    left, right = st.columns(2)
    with left:
        if len(phonemes):
            st.altair_chart(progress_view.phoneme_chart(phonemes), use_container_width=True)
        else:
            st.caption("No sound has been flagged yet.")
    with right:
        if len(words):
            st.altair_chart(progress_view.word_chart(words), use_container_width=True)
        else:
            st.caption("No word has been flagged yet.")

    st.caption(
        "Counted by how many attempts a sound or word was flagged in, not by raw "
        "occurrences, so one long paragraph cannot dominate the list."
    )


def render_practice(conn: sqlite3.Connection, job: "AssessJob | None", running: bool) -> None:
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
        "Practice text", ["Write my own", *presets], key=PRESET_KEY,
        on_change=_apply_preset, args=(mode,),
    )
    reference_text = st.text_area("Reference text", key=TEXT_KEY, height=140)

    audio = st.audio_input("Record", key=f"recording-{_generation('recording')}")
    if audio is not None:
        st.button(
            "🗑️ Delete recording", on_click=_delete_recording, disabled=running,
            help="Discard this take and record again. Your reference text is kept.",
        )
    uploaded = st.file_uploader(
        "…or upload a file", type=list(audio_utils.SUPPORTED_UPLOAD_TYPES),
        key=f"upload-{_generation('upload')}",
    )
    source = audio or uploaded

    # Nothing but the buttons goes inside these columns: a helper called within `with
    # column:` appends into it, and an alert laid out at a button's width is unreadable.
    left, middle, right = st.columns([1, 1, 3])
    with left:
        assess_clicked = st.button(
            "Assess", type="primary", disabled=running or source is None,
            width="stretch",
        )
    with middle:
        stop_clicked = (
            st.button("🛑 Stop", width="stretch") if running else False
        )
    with right:
        st.button("↺ Reset", on_click=_reset_form, disabled=running)

    if stop_clicked and job is not None:
        job.cancel_event.set()

    # Guarded on state, not on the button's `disabled` flag: a click is handled in the same
    # rerun that drew the button, so the on-screen button is still enabled until the next
    # one. Without this a fast double-click starts two assessments.
    if assess_clicked and not running and source is not None:
        if validate_reference(reference_text):
            audio_bytes = source.getvalue()
            key = utils.attempt_hash(reference_text, audio_bytes, mode)
            cached = _cache_get(key)
            if cached is not None:
                # The fastest path in the app: one click to retry the same drill sentence.
                # No thread, no polling, no extra rerun.
                st.session_state["last_key"] = key
                st.session_state["now_playing"] = None
            else:
                wav_bytes, seconds = prepare_audio(conn, audio_bytes, mode)
                if wav_bytes is not None:
                    start_assessment(
                        conn, wav_bytes, seconds, reference_text, mode, key
                    )
                    st.rerun()

    if running:
        st.info("Assessing… click Stop to cancel.", icon="⏳")
        # The only way to wait on a worker thread here: end this pass and start another.
        # Each rerun re-renders Stop and picks up a click made since the last one.
        time.sleep(JOB_POLL_SECONDS)
        st.rerun()

    last_key = st.session_state.get("last_key")
    if last_key:
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
                        "When": r["created_at"], "Mode": r["mode"],
                        "Pron": r["pron_score"], "Accuracy": r["accuracy"],
                        "Prosody": r["prosody"], "Offline": bool(r["offline"]),
                    }
                    for r in recent
                ],
                hide_index=True, width="stretch",
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

    # Two tabs, not two pages: `AppTest.from_file` addresses one script and the bare
    # `render()` below is the entry point, so `st.navigation`/`pages/` would cost more than
    # it buys. Note Streamlit executes BOTH tab bodies on every rerun — which is why the
    # progress view's re-parse is cached rather than recomputed on each of the 0.4 s polls.
    practice_tab, progress_tab = st.tabs(["Practice", "Progress"])
    with practice_tab:
        render_practice(conn, job, running)
    with progress_tab:
        render_progress(conn)
        render_history(conn)


render()
