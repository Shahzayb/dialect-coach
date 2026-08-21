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
import statistics
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import streamlit as st

import accent_charts
import accent_resynth
import accent_view
import acoustics
import ai_coach
import audio_utils
import budget
import content_score
import db
import fallback_coach
import ladder
import ladder_practice
import native_model
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
    "Unscripted — speak freely on a prompt": Mode.UNSCRIPTED,
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

# The Accent chart picker's remembered reading. A PLAIN session key, deliberately not the
# selectbox's own key: Streamlit deletes a widget's value when the widget is not registered on
# a pass, and any `st.rerun()` in an earlier tab ends the script before the Accent tab renders.
# See `render_accent_charts` for the whole mechanism.
ACCENT_CHART_CHOICE = "accent_chart_attempt_id"

# What a badge's number counts. Not a bool, so the row reads as what it is.
COUNT_WORDS = "words"
COUNT_STRETCHES = "stretches"

# Short labels + colours for the headline error-count badges (#10/#12) — distinct from
# DELIVERY_LABELS' longer prose, which explains a fault rather than naming it.
#
# The unit is part of the badge, because the row used to count words for all four and two of
# those counts do not mean the same thing. "2 Mispronunciations" is two independently wrong
# words; "28 Monotone" was ONE flat stretch spanning 28 words — which is exactly how the
# Delivery panel below words the same fault. Read together the row implied the monotone problem
# was fourteen times the articulation problem, and because prose comes in spans the monotone
# badge is structurally always the largest and least informative number on the row.
#
# A break stays a word count on purpose: an unexpected or missing break is a point event
# located at a word, not a span, so two flagged words are two breaks.
ERROR_BADGES: list[tuple[str, str | None, str, str]] = [
    # (badge label, delivery_summary() key or None for the mispronunciation count, colour, unit)
    ("Mispronunciations", None, "#c07f16", COUNT_WORDS),
    ("Unexpected break", "UnexpectedBreak", "#d6455d", COUNT_WORDS),
    ("Missing break", "MissingBreak", "#8a8a8a", COUNT_WORDS),
    ("Monotone", "Monotone", "#6a4fa0", COUNT_STRETCHES),
]

# Chosen to load the sounds most likely to be substituted by Urdu/Punjabi L1 speakers
# (master plan §7): /θ/ /ð/, /v/ vs /w/, /æ/ vs /ɛ/, /ʃ/ /s/ /z/ /dʒ/, dark /l/, and final
# consonant clusters. No digits — Azure normalises "33" and "thirty-three" differently,
# which breaks word alignment.
# Azure's own guidance for unscripted assessment: 15 seconds — "equivalent to more than 50
# words" — up to 10 minutes. The target here is the middle of that, and `MAX_DURATION_SECONDS_
# UNSCRIPTED` (300 s) is the hard ceiling the audio guard enforces.
UNSCRIPTED_TARGET_SECONDS = (180, 240)

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
    # **Prompts, not texts.** Nothing here is read aloud or scored against: these are subjects
    # to talk about. Chosen for the register this project actually exists for — being
    # understood in an interview, on a call, explaining something technical — rather than for
    # phoneme coverage, because free speech samples the vowel space wherever the speaker's own
    # vocabulary sends it and pretending otherwise would be the read-speech habit in disguise.
    #
    # Each is open enough to sustain three or four minutes and concrete enough that a speaker
    # does not spend the first minute deciding what to say.
    Mode.UNSCRIPTED: {
        "Explain a technical decision": (
            "Explain a technical decision you made recently — what the options were, what you "
            "chose, and why — to somebody who knows the field but not this project."
        ),
        "Tell me about yourself": (
            "Answer the interview opener: who you are, what you do, and what you are looking "
            "for next. Aim for the version you would actually say out loud, not a CV."
        ),
        "Something that went wrong": (
            "Describe something that went wrong at work, how you found out, and what you did "
            "about it. Include the part where you were unsure what to do."
        ),
        "Explain your work to a non-specialist": (
            "Explain what you do to somebody outside your field — a relative, a friend in a "
            "different profession — without using jargon you would have to define first."
        ),
        "Argue for something you believe": (
            "Make the case for an opinion you hold about your own field, and then give the "
            "strongest argument against it that you can."
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
    """One-line summary of a word's worst sound, for the card header.

    Silent on a word the speaker stumbled over. When a word is said twice the aligner reads
    across the repeat — the /eɪ/ ending the first "Wednes-day" pairs against the /w/ onset of
    the second — and describing that as a substitution is advice to drill a sound the speaker
    never produced, on what will usually be the lowest-scoring word of the attempt. The
    stumble itself is real and the card still says so; see `render_word_card`.
    """
    if word.get("disfluency") == speech_analyzer.REPETITION:
        return ""
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


def validate_prompt(text: str) -> bool:
    """Check Mode C's prompt before anything is sent. Returns False to block the run.

    A far lighter check than `validate_reference`, and deliberately so: this text is never read
    aloud, never scored against and never aligned word-for-word, so the digit warning and the
    length ceiling that protect a reference text have nothing to protect here. What it does
    need is to exist — the content scorer judges topic relevance against it, and two recordings
    are paired into a spontaneous calibration by matching it.
    """
    if not text.strip():
        st.error("Choose or write a prompt first — it is what the topic score is judged against.")
        return False
    return True


def render_unscripted_guidance(mode: Mode) -> None:
    """What a good Mode C recording looks like, and what it costs. Said before the record button.

    The cost line is not decoration. Mode C is the only mode that sends the audio twice, and a
    speaker who does not know that will read the meter afterwards and think it is broken.
    """
    low, high = UNSCRIPTED_TARGET_SECONDS
    ceiling = utils.max_duration_seconds(mode)
    st.caption(
        f"**Talk, do not read.** Azure needs at least 15 seconds — more than "
        f"{content_score.MIN_WORDS} words — for an unscripted assessment to mean anything, and "
        f"a topic score needs at least {content_score.MIN_SENTENCES} sentences. Aim for "
        f"{low // 60}-{high // 60} minutes; the hard ceiling here is {ceiling / 60:.0f} minutes."
    )
    if utils.get_bool("UNSCRIPTED_TWO_PASS") and not utils.offline_mode():
        st.caption(
            "**This recording is sent to Azure twice** — once for an accurate transcript, then "
            "again to score the pronunciation against it. Unscripted assessment runs on a "
            "weaker recogniser, and a phoneme diagnosis against a wrong transcript blames the "
            "wrong sounds. The cost check before the first pass already counts both."
        )


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

# The speech-style tag. Scripted modes are read speech; Mode C is not. Derived from the mode
# rather than asked for, so it cannot be forgotten on an attempt. The values themselves live in
# `vowel_measure`, which is where every consumer of them already is.
STYLE_READ = vowel_measure.STYLE_READ
STYLE_SPONTANEOUS = vowel_measure.STYLE_SPONTANEOUS


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

    **The ceiling is read style-agnostically** (`any_current_baseline`) even though everything
    else here is style-scoped: it tracks vocal tract length, not register, and a spontaneous
    calibration that swept to its own ceiling would produce formants incomparable with every
    reading already stored.
    """
    if assessment.offline:
        # The offline fixture is a stored payload with no audio behind it. Its phoneme offsets
        # point into a recording that is not here, so slicing them would measure whatever the
        # user happened to record against the wrong transcript.
        return None
    try:
        baseline = db.any_current_baseline(conn)
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
            # In Mode C `reference_text` is the PROMPT, so it travels as `topic` and NOT as
            # something to score against: nothing is scored against a prompt, and passing it as
            # a reference text would turn Mode C into a scripted assessment of a question the
            # speaker was answering rather than reading.
            unscripted = mode is Mode.UNSCRIPTED
            assessment = speech_analyzer.analyse(
                wav_path,
                "" if unscripted else reference_text,
                mode,
                cancel_event=cancel_event,
                on_attempt=note_attempt,
                topic=reference_text if unscripted else "",
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


def _headline_html(label: str, score: float | None) -> str:
    """The big banded number. `—` for a score that was not measured, never 0."""
    colour = AZURE_BAND_COLOURS[utils.azure_score_band(score)]
    text = f"{score:.0f}" if isinstance(score, (int, float)) else "—"
    return (
        '<div style="text-align:center;">'
        f'<div style="font-size:0.9rem;opacity:0.75;">{html.escape(label)}</div>'
        f'<div style="font-size:2.75rem;font-weight:700;color:{colour};">'
        f"{html.escape(text)}</div></div>"
    )


def render_scores(assessment: speech_analyzer.Assessment, mode: Mode = Mode.PARAGRAPH) -> None:
    """Pronunciation headline + Completeness, then the Accuracy/Fluency/Prosody breakdown.

    Banding is presentation only: `overall_scores` keeps the raw floats `normalise()`
    produced, and `utils.azure_score_band` is applied here, at render time, against Azure's
    own 0-59/60-79/80-89/90-100 convention — a different set of cut points from the
    word/phoneme colours in `colour_coded_html`, which are this project's own heuristics.

    **Mode C has no Completeness and the panel says so in words**, rather than rendering a dash
    that reads as a failed measurement. There is nothing for unscripted speech to be complete
    against: Azure's own unscripted results table omits the score, and the composite for the
    speaking scenario is defined without it.
    """
    scores = assessment.overall_scores
    unscripted = mode is Mode.UNSCRIPTED

    left, right = st.columns(2)
    with left:
        st.markdown(
            _headline_html("Pronunciation", scores.get("pron_score")), unsafe_allow_html=True
        )
    with right:
        if unscripted:
            st.markdown(
                '<div style="text-align:center;">'
                '<div style="font-size:0.9rem;opacity:0.75;">Completeness</div>'
                '<div style="font-size:1.1rem;font-weight:600;opacity:0.7;'
                'padding-top:0.9rem;">not applicable</div></div>',
                unsafe_allow_html=True,
            )
            st.caption(
                "Unscripted speech has no script to be complete against.",
                help=(
                    "Azure's own results table for unscripted assessment carries no "
                    "CompletenessScore, and the pronunciation composite for the speaking "
                    "scenario is defined without one."
                ),
            )
        else:
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


CONTENT_BARS: tuple[tuple[str, str], ...] = (
    ("Vocabulary score", "vocabulary"),
    ("Grammar score", "grammar"),
    ("Topic score", "topic"),
)

# Where the content numbers come from, said on the page. Not a footnote: these are NOT Azure
# scores — Azure retired content assessment at Speech SDK 1.46.0 and this project pins 1.51.1 —
# and a reader who assumes they are will over-trust them against the pronunciation scores beside
# them, which are measurements of a signal rather than a model's reading of a transcript.
CONTENT_SOURCE_CAPTION: dict[str, str] = {
    content_score.SOURCE_GEMINI: (
        "Scored by Gemini against Microsoft's own published rubric, from the transcript above — "
        "**not by Azure**, which retired content assessment at Speech SDK 1.46.0. It is a "
        "model's reading of what you said, not a measurement of the audio, and it deserves less "
        "weight than the pronunciation scores above it."
    ),
    content_score.SOURCE_AZURE: (
        "Returned by Azure itself. Unexpected — content assessment is documented as retired "
        "from Speech SDK 1.46.0 and this project pins 1.51.1 — and worth recording as a fact."
    ),
}


def render_content_scores(scores: Any) -> None:
    """The Content score panel: Vocabulary, Grammar, Topic — or why there are none.

    **Never a blank and never a substitute.** An empty panel teaches the reader that the feature
    is broken; a panel that says "no Gemini key" teaches them what to do. And a scripted-mode
    number standing in here would be a different measurement wearing this one's label.

    The headline is the plain mean of the three and is captioned as exactly that. Azure never
    published its own aggregate weighting, so it is not reconstructed: a mean that admits it is
    a mean is honest, and an invented composite that looks official is not.
    """
    st.markdown("**Content score**")
    if not scores.available:
        st.info(
            f"**Content scores are unavailable for this attempt** — {scores.reason}",
            icon="🚫",
        )
        return

    st.markdown(_headline_html("Content", scores.overall), unsafe_allow_html=True)
    st.caption("The plain mean of the three below. Azure never published its own weighting.")
    st.markdown(
        "".join(_score_bar_html(label, getattr(scores, key)) for label, key in CONTENT_BARS),
        unsafe_allow_html=True,
    )
    if scores.notes:
        st.caption(scores.notes)
    st.caption(CONTENT_SOURCE_CAPTION.get(scores.source, ""))


NOT_ASKED = (
    "no call has been made for this attempt yet. Content scoring spends one free-tier Gemini "
    "call, so it is a click rather than something every assessment does."
)


def _content_asked(key: str) -> bool:
    """Whether a content-scoring call has already been bought for this attempt.

    The same rule the coaching button follows: a call that was spent and came back unusable
    must not be re-buyable, so this tracks the ATTEMPT rather than the outcome.
    """
    return bool(lru_get(_session_cache("content_asked"), key))


def content_scores_for(conn: sqlite3.Connection, entry: CachedAttempt, *, ask_model: bool) -> Any:
    """This attempt's content scores: from Azure, from storage, from Gemini, or unavailable.

    Order matters. **Azure first**, in the one case it ever answers — if the retired fields
    under `UNSCRIPTED_CONTENT_PROBE` come back with numbers, those are a measurement from the
    service and no model needs asking. Then the stored verdict, so re-rendering never means
    re-asking and an "unavailable, because 429" survives a rerun as the fact it is.
    """
    azure = entry.assessment.overall_scores.get("content")
    if isinstance(azure, dict) and azure:
        return content_score.from_azure(azure)

    cache = _session_cache("content_scores")
    cached = lru_get(cache, entry.key)
    if cached is not None and (not ask_model or _content_asked(entry.key)):
        return cached

    if cached is None and entry.attempt_id is not None:
        stored = content_score.Scores.from_json(db.content_score_for(conn, entry.attempt_id))
        if stored is not None and not ask_model:
            lru_put(cache, entry.key, stored, CACHE_LIMIT)
            return stored

    if not ask_model:
        _, why_not = content_score.available()
        reason = why_not or content_score.too_short(entry.assessment.recognised_text) or NOT_ASKED
        scores = content_score.Scores.unavailable(reason)
        lru_put(cache, entry.key, scores, CACHE_LIMIT)
        return scores

    # Marked before the call, not after: a call that reached Gemini and came back unusable has
    # already been spent, and keying this off the result would let the same failure be bought
    # over and over.
    lru_put(_session_cache("content_asked"), entry.key, True, CACHE_LIMIT)
    with st.spinner("Scoring the content…"):
        scores = content_score.score(entry.assessment.recognised_text, entry.reference_text)
    lru_put(cache, entry.key, scores, CACHE_LIMIT)
    if entry.attempt_id is not None:
        with _DB_LOCK:
            db.attach_content_score(conn, entry.attempt_id, scores=scores)
    return scores


def render_content(conn: sqlite3.Connection, entry: CachedAttempt) -> None:
    """The Content score panel and the button that buys it. Mode C only.

    Content scores exist for unscripted speech and nothing else: there is no vocabulary or
    grammar of your own in a passage somebody wrote for you to read.
    """
    if entry.mode is not Mode.UNSCRIPTED:
        return

    azure = entry.assessment.overall_scores.get("content")
    if isinstance(azure, dict) and azure:
        render_content_scores(content_scores_for(conn, entry, ask_model=False))
        return

    usable, _ = content_score.available()
    short = content_score.too_short(entry.assessment.recognised_text)
    asked = st.button(
        "✨ Score the content with Gemini",
        key=f"content-{entry.key}",
        disabled=not usable or bool(short) or _content_asked(entry.key),
        help="One free-tier call. Vocabulary, grammar and topic relevance.",
    )
    render_content_scores(content_scores_for(conn, entry, ask_model=asked))
    if usable and not short:
        st.caption(
            "Sends the transcript above and your prompt to Google — never your audio. "
            "Free-tier prompts and responses may be used to improve Google's products, so "
            "this is a click rather than something every assessment does."
        )


def error_count_badges(assessment: speech_analyzer.Assessment) -> list[tuple[int, str]]:
    """The headline row as (count, label) pairs. Streamlit-free, so the units are assertable.

    A stretch badge says `1 · Monotone stretch (28 words)`, because that is the true thing: one
    flat passage spanning 28 words, worded the way the Delivery panel below already words it.
    The word count stays in the label rather than becoming a second badge — this is a headline
    count row, not a second copy of the Delivery panel's detail.

    The stretch count is `delivery_faults`' own `runs`, never recomputed here, so the row and
    the panel below it cannot disagree about how many stretches there were.
    """
    mispronounced = speech_analyzer.mispronounced_words(assessment.words)
    summary = speech_analyzer.delivery_summary(assessment.words)
    runs = {
        str(fault["fault"]): fault["runs"]
        for fault in speech_analyzer.delivery_faults(assessment.words)
    }

    badges: list[tuple[int, str]] = []
    for label, fault_key, _colour, unit in ERROR_BADGES:
        if fault_key is None:
            badges.append((len(mispronounced), label))
            continue
        words = summary.get(fault_key, [])
        if unit != COUNT_STRETCHES:
            badges.append((len(words), label))
            continue
        stretches = runs.get(fault_key, [])
        if not stretches:
            badges.append((0, f"{label} stretches"))
            continue
        noun = "stretch" if len(stretches) == 1 else "stretches"
        plural = "word" if len(words) == 1 else "words"
        badges.append((len(stretches), f"{label} {noun} ({len(words)} {plural})"))
    return badges


def render_error_counts(assessment: speech_analyzer.Assessment) -> None:
    """Headline counts for #10/#12: Mispronunciations, Unexpected break, Missing break,
    Monotone. Counts only — which words carry each fault is already shown by the flagged-
    word cards (mispronunciations) and `render_delivery` below (the other three), so this
    is not a second copy of that detail, just the number every issue image puts up top.
    """
    cells = []
    for (count, label), (_label, _fault_key, colour, _unit) in zip(
        error_count_badges(assessment), ERROR_BADGES
    ):
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

    # Computed before either branch so both coaches see the same evidence, and inside the
    # cache guard so it is not re-derived on every unrelated rerun.
    try:
        gaps = geometry_gaps(conn, entry)
    except Exception:  # a report is promised on every assessment, geometry or not
        logger.warning("Could not derive the vowel geometry for the coach", exc_info=True)
        gaps = []

    if ask_model:
        # Marked before the call, not after, and on the attempt rather than the outcome: a
        # call that reached Gemini and then fell back (malformed JSON, nothing surviving
        # validation) has already been spent, so keying this off the returned source would
        # leave the button live and let the same failure be bought over and over.
        _mark_gemini_attempted(entry.key)
        with st.spinner("Asking Gemini for a second opinion…"):
            result = ai_coach.coach(entry.assessment, entry.reference_text, entry.mode, gaps=gaps)
    else:
        try:
            report = fallback_coach.build(entry.assessment, entry.mode, gaps=gaps)
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

    render_bridging_phrases(conn, report, entry)


def geometry_gaps(conn: sqlite3.Connection, entry: CachedAttempt) -> list[Any]:
    """What the vowel measurements say is worth practising, for the coach payload.

    **This is the half of the diagnosis Azure cannot see.** The phoneme scores are categorical
    — this sound or that one — and the geometry is continuous: how far each vowel sits from
    General American, in which direction, net of the measurement noise floor. Without this the
    coach writes from the phoneme payload alone and the whole measurement chunk stops at the
    charts, which is where it sat until this was wired.

    Empty is the ordinary answer and never an error: it needs a stored baseline, a reference
    set and a measurement, and most attempts have none of the three. The report has to come
    out the same shape either way.
    """
    measurement = entry.measurement
    chosen = reference_set()
    if measurement is None or not chosen:
        return []
    context = baseline_context(conn, style=measurement.style)
    gate = vowel_measure.plot_gate(
        measurement,
        baseline_normaliser=context.normaliser,
        baseline_style=context.style,
    )
    if not gate.ok or gate.normaliser is None:
        return []
    gaps = vowel_measure.ranked_gaps(
        measurement,
        gate.normaliser,
        reference_set=chosen,
        noise=context.noise,
        minimum=gate.minimum_tokens,
    )
    # Rhythm is a property of the whole reading rather than of any token, so it comes from
    # `rhythm.py` and not from the measurement — but it belongs in the same section, because
    # what the coach needs is one continuous picture and not two.
    if entry.attempt_id:
        words = _words_for(conn, entry.attempt_id, entry.reference_text)
        baseline = rhythm.baseline()
        pace = vowel_measure.rhythm_gap(
            rhythm.npvi(words).npvi if words else None,
            baseline.rhythm.npvi if baseline is not None else None,
        )
        if pace is not None:
            gaps = [*gaps, pace]
    return gaps


def render_bridging_phrases(conn: sqlite3.Connection, report: Any, entry: CachedAttempt) -> None:
    """The vowel geometry's answer: a sentence to say, one click from being a drill.

    **The point of the ranking is the next drill, not the chart.** A phrase the user has to
    retype is a phrase they will not practise, so the button fills the practice textarea
    directly and a second one puts the target on the queue, where it outlives the session that
    produced it.
    """
    phrases = list(getattr(report, "bridging_phrases", ()) or ())
    if not phrases:
        return

    st.markdown("**Bridging phrases** — from the vowel measurements, not the phoneme scores")
    st.caption(
        "Each one forces a single vowel several times over in different consonant contexts. "
        "That is the part a word list cannot drill: a vowel is easy to hit on its own and "
        "hard to hold through whatever comes next."
    )
    for index, phrase in enumerate(phrases):
        label = f"/{phrase.vowel}/ {phrase.keyword}".strip()
        st.markdown(f"**{label}** — {phrase.why}")
        st.markdown(f"> {phrase.phrase}")
        drill, queue = st.columns(2)
        drill.button(
            "Drill this now",
            key=f"bridge_drill_{index}",
            on_click=_load_drill,
            args=(phrase.phrase,),
        )
        if queue.button("Add to practice queue", key=f"bridge_queue_{index}"):
            _promote_vowel_target(conn, phrase, entry)


def _load_drill(phrase: str) -> None:
    """Pre-fill the practice textarea. Runs before the next render, like `_apply_preset`."""
    st.session_state[TEXT_KEY] = phrase
    st.session_state[PRESET_KEY] = ""


def _promote_vowel_target(conn: sqlite3.Connection, phrase: Any, entry: CachedAttempt) -> None:
    """Put a measured vowel gap on the practice queue.

    **A rhoticity or reduction target is a `vowel` target with its evidence**, not a new kind.
    `practice_targets` already carries that kind and `practice_queue` already grades it, so
    inventing a fourth would mean a fourth graduation rule for the same underlying question.
    """
    item = f"/{phrase.vowel}/ {phrase.keyword}".strip()
    try:
        db.upsert_target(
            conn,
            item=item,
            kind=practice_queue.VOWEL,
            evidence={
                "source": "vowel_geometry",
                "why": phrase.why,
                "phrase": phrase.phrase,
                "attempt_id": entry.attempt_id,
            },
        )
    except Exception as exc:  # noqa: BLE001 — a queue failure must not lose the report
        st.error(f"Could not add that to the queue: {utils.redact(str(exc))}", icon="⚠️")
        return
    st.success(f"{item} is on the practice queue — it will show up on Today.", icon="🎯")


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


def render_colour_coded(
    assessment: speech_analyzer.Assessment, mode: Mode = Mode.PARAGRAPH
) -> None:
    st.subheader("Word by word")
    st.markdown(colour_coded_html(assessment.words), unsafe_allow_html=True)
    # The omission/insertion half of the legend describes marks that only a miscue diff can
    # produce, and Mode C deliberately runs none — see `speech_analyzer.normalise`. Promising
    # them here would have the reader hunting for marks the mode cannot draw.
    marks = (
        ""
        if mode is Mode.UNSCRIPTED
        else " Struck through: never spoken. Italic: heard but not in the script."
    )
    st.caption(
        f"Hover any word for its score and phoneme breakdown. Red below {utils.WORD_RED:g}, "
        f"amber below {utils.WORD_AMBER:g}, green above.{marks} These cut points are "
        f"heuristics chosen for this tool — Azure returns a 0-100 score and says nothing "
        f'about where "bad" starts.'
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
        if word.get("disfluency") == speech_analyzer.REPETITION:
            # Said twice. The score is the stumble, not a sound — and the diff above already
            # showed the repeat, so the two surfaces now tell the same story about one word.
            st.markdown("**You said this word twice — a stumble, not a sound to drill.**")
            st.caption(
                "Azure aligned across the repeat, so the phonemes below pair the end of one "
                "attempt against the start of the next. That is why the score is so low; it "
                "is not a substitution."
            )
        else:
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


def render_transcript(entry: CachedAttempt) -> None:
    """What Mode C's phoneme diagnosis was actually scored against.

    **Shown before anything derived from it**, because everything below is only as good as
    this: the second pass scores each phoneme against the word this transcript says was there.
    If a word here is wrong, the sounds blamed for it are wrong too, and the reader is the only
    one who can notice. Modes A and B already show the reference text the user typed; this is
    the equivalent for the mode where nobody typed one.
    """
    if entry.mode is not Mode.UNSCRIPTED:
        return
    st.subheader("What Azure heard")
    transcript = entry.assessment.scored_against or entry.assessment.recognised_text
    st.markdown(f"> {transcript}" if transcript else "_Nothing was transcribed._")
    if utils.get_bool("UNSCRIPTED_TWO_PASS"):
        st.caption(
            "Transcribed by standard Azure speech-to-text, then used as the reference text for "
            "the pronunciation assessment — the two-pass flow Microsoft recommends, because "
            "unscripted assessment runs on a weaker recogniser. Every sound named below was "
            "scored against these words, so a wrong word here means a wrongly blamed sound."
        )
    else:
        st.caption(
            "UNSCRIPTED_TWO_PASS is off, so this came from the unscripted assessment's own "
            "recogniser — which Microsoft documents as weaker than standard Azure "
            "speech-to-text. Treat the phoneme diagnosis below with correspondingly less "
            "confidence."
        )


def render_result(conn: sqlite3.Connection, entry: CachedAttempt, source: Any) -> None:
    assessment, reference_text = entry.assessment, entry.reference_text
    if entry.mode is Mode.UNSCRIPTED:
        st.caption(f"Prompt: _{reference_text}_" if reference_text else "No prompt recorded.")
    render_transcript(entry)
    render_scores(assessment, entry.mode)
    render_content(conn, entry)
    render_error_counts(assessment)
    # Directly under the scores: what to do about them comes before the evidence for them.
    render_coaching(conn, entry)
    # **No script-versus-heard diff in Mode C.** There is no script: `reference_text` holds the
    # PROMPT, and diffing a prompt against what somebody freely said would strike through every
    # word of the prompt and italicise every word they spoke. `render_transcript` above is what
    # answers "what did Azure hear" for this mode.
    if entry.mode is not Mode.UNSCRIPTED:
        render_diff(assessment, reference_text)
    render_colour_coded(assessment, entry.mode)

    st.subheader("Hear the whole thing")
    # Mode C synthesises the TRANSCRIPT, not `reference_text` — the prompt was never spoken and
    # hearing a native reading of "Explain a technical decision" teaches nothing. Modes A and B
    # keep the script for the reason `techContext` already records: the reference text is what
    # they were trying to say, and a re-reading of what they actually said would not be a target.
    spoken = assessment.scored_against or assessment.recognised_text
    playback_text = spoken if entry.mode is Mode.UNSCRIPTED else reference_text
    playback_buttons(conn, playback_text, key_prefix="whole", label="the full text")
    if entry.mode is Mode.UNSCRIPTED and playback_text:
        st.caption("A native rendering of your own words, so the target is what you tried to say.")
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


@dataclass(frozen=True)
class BaselineContext:
    """The stored baseline, unpacked once per render.

    Every accent surface needs the same four things out of `speaker_baseline`, and each was
    previously doing its own `json.loads` at its own call site. One loader, so the noise floor
    a chart draws and the noise floor a table quotes cannot come from different rows.
    """

    normaliser: Any | None
    noise: Any | None
    style: str
    reference_set: str
    row: Any | None = None

    @property
    def calibrated(self) -> bool:
        return self.normaliser is not None


def baseline_context(conn: sqlite3.Connection, *, style: str) -> BaselineContext:
    """Load the current baseline **for one speech style**, or an empty context when it has none.

    `style` is required and never guessed: there is one current baseline per style, and asking
    without saying which would silently hand spontaneous speech a read centroid.
    """
    row = db.current_baseline(conn, style=style)
    if row is None:
        return BaselineContext(normaliser=None, noise=None, style="", reference_set="")
    return BaselineContext(
        normaliser=vowel_measure.normaliser_from_json(json.loads(row["normaliser_json"])),
        noise=vowel_measure.noise_from_json(json.loads(row["noise_floor_json"])),
        style=str(row["style_tag"]),
        reference_set=str(row["reference_set"]),
        row=row,
    )


def refusal_reason(conn: sqlite3.Connection, measurement: Any, gate: Any) -> str:
    """Why this measurement is not being charted, said in terms of what is actually missing.

    `plot_gate` sees one normaliser and cannot tell "you have never calibrated" from "you have
    calibrated, but for the other speech style" — it is handed the style-specific baseline and
    a missing one looks the same either way. That distinction is the whole difference between
    "read the passage twice" and "record this prompt again", so it is resolved here, where the
    other styles' baselines can be looked up.
    """
    if gate.reason != vowel_measure.NO_BASELINE:
        return str(gate.reason)
    other = db.any_current_baseline(conn)
    if other is None:
        return vowel_measure.NO_BASELINE
    return vowel_measure.WRONG_STYLE_BASELINE.format(
        measured=measurement.style or vowel_measure.STYLE_READ,
        baseline=str(other["style_tag"]),
    )


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

    context = baseline_context(conn, style=measurement.style)
    gate = vowel_measure.plot_gate(
        measurement,
        baseline_normaliser=context.normaliser,
        baseline_style=context.style,
    )

    if gate.ok and gate.normaliser is not None:
        # The stored baseline supplies the space, so this reading only has to supply the
        # token. That is what lets a three-word drill produce a row at all: normalising a
        # drill against itself needs a full inventory and would refuse every time. Free speech
        # does not get that latitude — see `minimum_tokens_for`.
        normaliser = gate.normaliser
        minimum = vowel_measure.minimum_tokens_for(measurement.style, gate.minimum_tokens)
    elif context.normaliser is not None or gate.reason == vowel_measure.NOTHING_MEASURABLE:
        st.info(gate.reason, icon="📉")
        _render_rejections(measurement)
        return
    elif measurement.style == vowel_measure.STYLE_SPONTANEOUS:
        # **Never fall back to normalising spontaneous speech against itself either.** A single
        # free-speech recording cannot supply a full inventory — that is the point of the mode —
        # and the categories it does carry are exactly the ones that sentence happened to use,
        # so a centroid built from them is a centroid of the topic. Say what is missing instead.
        st.info(refusal_reason(conn, measurement, gate), icon="📐")
        _render_rejections(measurement)
        return
    else:
        # No baseline yet. Fall back to normalising this reading against itself, which needs a
        # full inventory — and says so rather than guessing when it does not have one.
        try:
            normaliser = vowel_measure.lobanov(
                measurement.accepted, categories=vowel_measure.REFERENCE_CATEGORIES
            )
        except vowel_measure.TooFewTokens as exc:
            st.info(str(exc), icon="📉")
            _render_rejections(measurement)
            return
        minimum = vowel_measure.MIN_TOKENS_PER_CATEGORY

    findings = vowel_measure.findings(
        measurement,
        normaliser,
        reference_set=chosen,
        noise=context.noise,
        minimum=minimum,
    )
    st.markdown(accent_view.to_markdown(findings))
    st.caption(accent_view.PUBLISHED_CAPTION.format(set=chosen))
    if context.noise is None:
        st.caption(accent_view.noise_caption(None))


def _render_rejections(measurement: Any) -> None:
    """Show what was refused even when nothing could be normalised. A thin table, visibly thin."""
    rejected = measurement.rejected
    if not rejected:
        return
    st.markdown(accent_view.to_markdown(vowel_measure.rejection_findings(measurement.tokens)))


def _measurable_attempts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every non-offline attempt with at least one accepted vowel token, newest first."""
    rows = conn.execute(
        """
        SELECT a.id, a.created_at, a.mode, a.reference_text,
               COUNT(v.id) FILTER (WHERE v.accepted = 1) AS accepted
        FROM attempts a JOIN vowel_measurements v ON v.attempt_id = a.id
        WHERE a.offline = 0
        GROUP BY a.id ORDER BY a.created_at DESC
        """
    ).fetchall()
    return [row for row in rows if row["accepted"] > 0]


def calibration_reads(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Benchmark-passage attempts that carry usable vowel measurements, newest first.

    The calibration passage is `progress_view.BENCHMARK_PASSAGE` — chosen once, for exactly
    two consumers, and its own comment in `progress_view` says so. Nothing new is written for
    this chunk.
    """
    return [
        row
        for row in _measurable_attempts(conn)
        if progress_view.is_benchmark(row["reference_text"])
        and not rhythm.is_baseline_capture(row["reference_text"])
    ]


def spontaneous_calibration_reads(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Mode C attempts on the SAME prompt that carry usable vowel measurements, newest first.

    A spontaneous baseline cannot be built the way the read one was, because you cannot read the
    same passage twice when you are not reading. The nearest available analogue is the same
    PROMPT twice: the content is not identical, so the displacement between the two recordings
    carries content variation on top of measurement noise.

    That makes the resulting floor an **upper bound** rather than an equal of the read floor —
    wider — and wider is the conservative direction for a guard. A floor that is too narrow
    licenses reporting noise as progress; one that is too wide only refuses to call something
    progress until it is bigger. The surface says which of the two this is.

    Grouped rather than just "the two most recent": two Mode C recordings on different prompts
    would compare two different vocabularies, and the vowel inventory of free speech is decided
    by the words the topic pulled in.
    """
    rows = [
        row
        for row in _measurable_attempts(conn)
        if str(row["mode"]) == Mode.UNSCRIPTED.value and (row["reference_text"] or "").strip()
    ]
    by_prompt: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        by_prompt.setdefault(str(row["reference_text"]).strip().lower(), []).append(row)
    pairs = [group for group in by_prompt.values() if len(group) >= 2]
    if not pairs:
        return rows[:1] if rows else []
    # The prompt whose most recent recording is newest, so a session in progress is the one
    # offered rather than an older prompt that happens to already have a pair.
    return max(pairs, key=lambda group: str(group[0]["created_at"]))


def _minutes_between(first: str, second: str) -> float:
    start = datetime.strptime(first, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    end = datetime.strptime(second, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return abs((end - start).total_seconds()) / 60.0


def build_baseline(
    conn: sqlite3.Connection, older: Any, newer: Any, *, style: str = STYLE_READ
) -> str | None:
    """Calibrate from two stored reads. Returns an error message, or None on success.

    `style` decides which population the baseline is stored under, and `vowel_measure.calibrate`
    refuses if the two readings' own tokens disagree with it — a baseline filed under the wrong
    style is applied to the wrong readings for as long as it stands.
    """
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
            style=style,
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


def _render_calibration_pair(
    conn: sqlite3.Connection,
    reads: Sequence[Any],
    *,
    style: str,
    button_label: str,
    key: str,
) -> None:
    """The half of a calibration flow that is identical for both styles.

    Both need the same two things checked and refused in the same way: two usable recordings,
    and enough time between them that the displacement is a session-to-session wander rather
    than a microphone holding still.
    """
    gap = utils.get_float("CALIBRATION_GAP_MINUTES")
    if len(reads) < 2:
        return

    newer, older = reads[0], reads[1]
    apart = _minutes_between(str(older["created_at"]), str(newer["created_at"]))
    st.caption(
        f"Two most recent: attempt {older['id']} and attempt {newer['id']}, "
        f"{apart:.0f} minutes apart, {older['accepted']} and {newer['accepted']} usable "
        f"vowel tokens."
    )

    if apart < gap:
        st.warning(
            f"Those two recordings are only {apart:.0f} minutes apart, and the floor needs "
            f"{gap:g}. Two back-to-back takes measure the microphone holding still, not the "
            f"session-to-session wander this number exists to capture — so the band would "
            f"come out flatteringly small and start licensing noise as progress.",
            icon="⏱️",
        )
        return

    if st.button(button_label, type="primary", key=key):
        problem = build_baseline(conn, older, newer, style=style)
        if problem:
            st.error(problem, icon="📉")
        else:
            st.success(f"{style.capitalize()} baseline and noise floor stored.")
            st.rerun()


def render_calibration(conn: sqlite3.Connection) -> None:
    """The two calibration flows — one per speech style — and what they refuse."""
    st.subheader("Calibration")
    gap = utils.get_float("CALIBRATION_GAP_MINUTES")
    st.markdown(
        f"""
**Each speech style needs its own baseline.** Read speech and spontaneous speech are different
populations, not the same measurement made under harder conditions: speakers hyperarticulate
when reading and reduce far more when generating language. A read baseline normalises read
speech, so a spontaneous recording is either normalised against a spontaneous baseline or it is
not normalised at all. Neither ever supersedes or averages into the other.

Both are built the same way: the same material **twice in one sitting, at least {gap:g} minutes
apart**, on the same microphone in the same room. The displacement between the two **is** the
measurement noise floor — a vowel centroid moves between sessions from microphone placement,
posture, time of day and warm-up, with no learning at all. Without that number the progress view
would render exactly that wander as progress.
"""
    )

    st.markdown("#### Read speech — the benchmark passage, twice")
    reads = calibration_reads(conn)
    if len(reads) < 2:
        st.info(
            f"{len(reads)} of 2 calibration reads so far. Read the benchmark passage on the "
            f"Practice tab — it is the same passage the progress chart uses, so the read "
            f"counts for both.",
            icon="🎯",
        )
    else:
        _render_calibration_pair(
            conn,
            reads,
            style=STYLE_READ,
            button_label="Set the read baseline from these two reads",
            key="calibrate_read",
        )

    st.markdown("#### Spontaneous speech — the same prompt, twice")
    st.caption(
        "You cannot read the same passage twice when you are not reading, so the nearest "
        "available analogue is the same PROMPT twice. The content will not be identical, which "
        "means this floor carries content variation on top of measurement noise and comes out "
        "**wider** than the read one. That is an upper bound, and an upper bound is the safe "
        "direction: it can only refuse to call something progress until the change is bigger."
    )
    spontaneous = spontaneous_calibration_reads(conn)
    if len(spontaneous) < 2:
        st.info(
            f"{len(spontaneous)} of 2 recordings on one prompt so far. Record the same "
            f"Unscripted prompt again on the Practice tab, at least {gap:g} minutes after the "
            f"first — both takes have to be on the SAME prompt, because free speech samples the "
            f"vowel space wherever the topic sends it.",
            icon="🗣️",
        )
        return
    st.caption(f"Prompt: _{str(spontaneous[0]['reference_text'])[:120]}_")
    _render_calibration_pair(
        conn,
        spontaneous,
        style=STYLE_SPONTANEOUS,
        button_label="Set the spontaneous baseline from these two recordings",
        key="calibrate_spontaneous",
    )


def render_baseline(conn: sqlite3.Connection) -> None:
    """The stored baselines — one per speech style — each with its chart and its noise floor."""
    stored = [
        (style, db.current_baseline(conn, style=style)) for style in (STYLE_READ, STYLE_SPONTANEOUS)
    ]
    present = [(style, row) for style, row in stored if row is not None]
    if not present:
        st.info(
            "No baseline yet. Until the calibration material has been recorded twice there is "
            "no speaker centroid to normalise against and no noise floor, so no movement on any "
            "accent surface can honestly be called progress.",
            icon="📐",
        )
        return

    missing = [style for style, row in stored if row is None]
    if missing:
        st.caption(
            f"No {' or '.join(missing)} baseline yet, so {' and '.join(missing)} recordings are "
            f"not normalised at all — they are never borrowed against the other style's "
            f"centroid."
        )
    for style, row in present:
        _render_one_baseline(style, row)


def _render_one_baseline(style: str, row: Any) -> None:
    """One stored baseline: its provenance, its vowel chart, and its noise floor."""
    chosen = str(row["reference_set"])
    positions = vowel_measure.positions_from_json(json.loads(row["positions_json"]))
    noise = vowel_measure.noise_from_json(json.loads(row["noise_floor_json"]))

    st.markdown(f"#### Your vowel space — {style} speech")
    st.caption(
        f"Calibrated {row['created_at']} from attempts {row['attempt_ids']}, "
        f"{row['tokens']} usable tokens, LPC ceiling {row['lpc_ceiling_hz']:.0f} Hz, "
        f"{row['style_tag']} speech."
    )
    if style == STYLE_SPONTANEOUS:
        st.caption(
            "This floor was measured across two recordings on one prompt, so it carries "
            "content variation on top of measurement noise and is **wider than the read "
            "floor by construction**. Treat it as an upper bound: it under-reports change "
            "rather than over-reporting it."
        )

    frame = accent_view.vowel_frame(positions, vowel_measure.reference_positions(chosen))
    if frame.empty:
        st.caption("Nothing in the baseline could be placed on a chart.")
    else:
        st.altair_chart(accent_view.vowel_chart(frame), theme="streamlit")
        st.caption(accent_view.PUBLISHED_CAPTION.format(set=chosen))

    st.markdown(accent_view.noise_caption(noise))

    with st.expander(f"The {style} noise floor, vowel by vowel"):
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


def measured_attempts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every real attempt carrying vowel tokens, newest first. What the chart picker offers.

    Broader than `calibration_reads`, deliberately: a drill is exactly what these charts exist
    to make plottable, so restricting the picker to the benchmark passage would rebuild the
    gate `plot_gate` was written to remove.
    """
    rows = conn.execute(
        """
        SELECT a.id, a.created_at, a.reference_text, a.mode,
               COUNT(v.id) FILTER (WHERE v.accepted = 1) AS accepted
        FROM attempts a JOIN vowel_measurements v ON v.attempt_id = a.id
        WHERE a.offline = 0
        GROUP BY a.id ORDER BY a.created_at DESC
        """
    ).fetchall()
    return [
        row
        for row in rows
        if row["accepted"] > 0 and not rhythm.is_baseline_capture(row["reference_text"])
    ]


def measurement_for(conn: sqlite3.Connection, attempt_id: int) -> Any | None:
    """Rebuild one attempt's `Measurement` from its stored tokens.

    The re-derivation path v0.10.0 kept the rows for: everything a chart needs comes back out
    of the database, so a changed reference table or normalisation scheme is a query rather
    than a request that the passage be read again.
    """
    rows = [dict(row) for row in db.vowel_measurements_for(conn, attempt_id)]
    if not rows:
        return None
    tokens = vowel_measure.tokens_from_rows(rows)
    first = rows[0]
    return vowel_measure.Measurement(
        tokens=tuple(tokens),
        ceiling_hz=float(first.get("lpc_ceiling_hz") or 5000.0),
        snr_db_min=first.get("snr_db_min"),
        style=str(first.get("style_tag") or STYLE_READ),
    )


def _chart_with_table(caption: str, chart: Any, rows: Sequence[Any], empty: str) -> None:
    """One instrument: its picture, then its numbers. Never one without the other.

    The chart carries the shape and the table carries the numbers, and the table is the half
    that survives being pasted into a plan file or a commit message. Both come from the same
    computation — `findings_by_instrument` — so they cannot disagree.
    """
    if chart is not None:
        st.altair_chart(chart, theme="streamlit")
    else:
        st.caption(empty)
    st.caption(caption)
    st.markdown(accent_view.to_markdown(list(rows)))


def render_accent_charts(conn: sqlite3.Connection) -> None:
    """The six chart-and-table pairs, for one stored attempt."""
    st.subheader("Where your vowels sit, and where they are going")
    st.caption(accent_charts.POST_HOC)

    chosen_set = reference_set()
    if not chosen_set:
        return

    attempts = measured_attempts(conn)
    if not attempts:
        st.info(
            "No recording carries vowel measurements yet. Read something on the Practice tab.",
            icon="🎙️",
        )
        return

    labels = {
        int(row["id"]): (
            f"#{row['id']} · {row['created_at'][:16].replace('T', ' ')} · "
            f"{row['accepted']} tokens · {str(row['reference_text'])[:40]}"
        )
        for row in attempts
    }
    # `index=` is not optional here, and the reason is worth keeping. Any `st.rerun()` raised in
    # the Today or Practice tab ends the script before this tab is reached, so the selector is
    # not registered on that pass and Streamlit deletes its stored value as stale
    # (`session_state._remove_stale_widgets`). A server-initiated rerun also carries no widget
    # states back from the browser (`st.rerun` builds `RerunData()` with none), so nothing
    # restores it: the next full run re-registers the widget from scratch and a positional
    # default lands on index 0 — whichever reading happens to be newest. The browser is never
    # told, because a first registration sets neither `value_changed` nor `value_needs_reset`,
    # so it keeps painting the label it already had. That is the 2026-08-20 mismatch, and it
    # fired on every early-terminated rerun, not only the ones where a new attempt landed.
    #
    # The remembered id lives under a PLAIN session key, never a widget key: stale-widget
    # cleanup only strips element ids, so a plain key is the one thing here that survives. The
    # default is then resolved by identity rather than position, exactly as the shadowing
    # passage picker already does, and the chosen reading stays chosen — a new reading appears
    # at the top of the list without taking the selection.
    options = list(labels)
    remembered = st.session_state.get(ACCENT_CHART_CHOICE)
    attempt_id = int(
        st.selectbox(
            "Which reading?",
            options=options,
            index=options.index(remembered) if remembered in labels else 0,
            format_func=lambda key: labels[key],
            key="accent_chart_attempt",
        )
    )
    st.session_state[ACCENT_CHART_CHOICE] = attempt_id

    measurement = measurement_for(conn, attempt_id)
    if measurement is None:
        st.caption("That attempt's tokens could not be read back.")
        return

    chosen = next(row for row in attempts if int(row["id"]) == attempt_id)
    mismatch = vowel_measure.label_matches_measurement(int(chosen["accepted"]), measurement)
    if mismatch:
        st.error(mismatch, icon="🏷️")
        return
    # Said from the measurement that was actually loaded, not from the widget. If the selector
    # ever lies again, this line and the label above it disagree in plain sight rather than
    # leaving the reader to notice that n=2 cannot come out of a 138-token read.
    st.caption(
        f"Plotting #{attempt_id} · {len(measurement.accepted)} accepted tokens · "
        f"{str(chosen['created_at'])[:16].replace('T', ' ')}"
    )

    # Resolved from the SELECTED reading's own style, not from the page: there is one current
    # baseline per style, and which one applies is a property of the recording being drawn.
    context = baseline_context(conn, style=measurement.style)
    gate = vowel_measure.plot_gate(
        measurement,
        baseline_normaliser=context.normaliser,
        baseline_style=context.style,
    )
    if not gate.ok or gate.normaliser is None:
        st.info(refusal_reason(conn, measurement, gate), icon="📐")
        return
    # Free speech samples the vowel space wherever the sentence went, so a lone token is not a
    # deliberate probe the way a drill token is. See `vowel_measure.minimum_tokens_for`.
    minimum = vowel_measure.minimum_tokens_for(measurement.style, gate.minimum_tokens)
    normaliser = gate.normaliser
    accepted = measurement.accepted
    speaker = vowel_measure.positions(accepted, normaliser, minimum=minimum)
    published = vowel_measure.reference_positions(
        chosen_set, source=vowel_measure.REFERENCE_PUBLISHED
    )
    modelled = vowel_measure.reference_positions(chosen_set, source=vowel_measure.REFERENCE_VOICE)
    grouped = vowel_measure.findings_by_instrument(
        measurement, normaliser, reference_set=chosen_set, noise=context.noise, minimum=minimum
    )

    # 1. Rhoticity FIRST. For a General American target it is routinely the largest single gap
    # on the page, and it is the most correctable thing on it.
    st.markdown("### Rhoticity — F3 against F2")
    rhotic = accent_charts.rhoticity_frame(accepted, modelled or published)
    _chart_with_table(
        "Every r-coloured token, not a category mean — r-colouring arrives on the stressed "
        "NURSE and vanishes on the unstressed lettER, and a mean is the statistic that hides "
        "exactly that. Lower is more r-coloured.",
        accent_charts.rhoticity_chart(rhotic) if not rhotic.empty else None,
        grouped.get(vowel_measure.RHOTICITY, []),
        "No r-coloured vowel was measured in this reading.",
    )

    # 2. The quadrant, with an arrow per vowel.
    st.markdown("### Vowel space — the arrow is the instruction")
    quadrant = accent_charts.quadrant_frame(speaker, published, context.noise)
    rings = accent_charts.noise_ring_frame(quadrant)
    _chart_with_table(
        "Each arrow runs from where you said the vowel to the General American target: its "
        "direction is the instruction and its length is the priority. The faint ring is the "
        "measurement noise floor — an arrow shorter than the ring is not a finding, and none "
        "is drawn.",
        accent_charts.quadrant_chart(quadrant, rings) if not quadrant.empty else None,
        grouped.get(vowel_measure.POSITION, []),
        "Nothing in this reading could be placed in the vowel space.",
    )

    # 3. Trajectories — the exit condition.
    st.markdown("### Diphthongs — a stroke, or a dot")
    strokes = accent_charts.trajectory_frame(
        vowel_measure.trajectories(accepted, normaliser, minimum=minimum)
    )
    _chart_with_table(
        "Each vowel drawn from 20% of its duration to 80%. A diphthong that is being "
        "monophthongised renders as a dot where a native rendering renders as a stroke — and "
        "the steady vowels beside it are what make that visible.",
        accent_charts.trajectory_chart(strokes) if not strokes.empty else None,
        grouped.get(vowel_measure.TRAJECTORY, []),
        f"No vowel in this reading reached {vowel_measure.MIN_TRAJECTORY_MS:.0f} ms, the "
        f"length a glide needs before the 20% and 80% analysis windows fit inside it.",
    )

    # 4. Pitch.
    st.markdown("### Intonation — your contour against the model's")
    render_pitch_overlay(conn, int(attempt_id), measurement, modelled, published, normaliser)

    # 5. Duration.
    st.markdown("### Vowel length")
    durations = accent_charts.duration_frame(speaker, published, modelled)
    _chart_with_table(
        "Three bars, and the third is the one a difference can be read from. Hillenbrand's "
        "durations are citation-form words read in isolation, so connected speech is bound to "
        "look short against them by an amount that is the reference's artefact rather than "
        "your accent. The model bar is the same passage in connected speech.",
        accent_charts.duration_chart(durations) if not durations.empty else None,
        grouped.get(vowel_measure.DURATION, []) + grouped.get(vowel_measure.REDUCTION, []),
        "No vowel in this reading has a duration to compare.",
    )

    # 6. Rhythm.
    st.markdown("### Rhythm")
    render_rhythm_chart(conn, int(attempt_id), grouped)

    with st.expander("What was refused, and why"):
        st.markdown(accent_view.to_markdown(grouped.get(vowel_measure.REJECTED, [])))


def stored_audio_bytes(conn: sqlite3.Connection, attempt_id: int) -> bytes | None:
    """This attempt's kept recording, or None. Gitignored, so it can legitimately be gone."""
    row = db.audio_for(conn, attempt_id)
    if row is None:
        return None
    try:
        return Path(str(row["path"])).read_bytes()
    except OSError:
        logger.warning("Kept recording missing for attempt %s", attempt_id)
        return None


def render_pitch_overlay(
    conn: sqlite3.Connection,
    attempt_id: int,
    measurement: Any,
    modelled: Mapping[str, Any],
    published: Mapping[str, Any],
    normaliser: Any,
) -> None:
    """Two contours on one time axis, plus the resynthesis that makes the gap audible."""
    attempt = db.get_attempt(conn, attempt_id)
    if attempt is None:
        return
    reference_text = str(attempt["reference_text"])
    wav_bytes = stored_audio_bytes(conn, attempt_id)
    if wav_bytes is None:
        st.caption(
            "This attempt's recording is no longer on disk, so there is no pitch track to "
            "draw. Recordings live under a gitignored directory and are never committed."
        )
        return

    renderings = native_model.renderings_for(conn, reference_text)
    if not renderings:
        st.caption(
            f"The model has not read this text yet, so there is nothing to overlay. "
            f"Capturing it costs about {native_model.estimate(reference_text, ['x'])[0]:,} "
            f"characters of the monthly 500,000 and roughly "
            f"{native_model.estimate(reference_text, ['x'])[1]:.0f} seconds of the 18,000."
        )
        if st.button("Capture the model's reading", key=f"capture_native_{attempt_id}"):
            _capture_native(conn, reference_text)
        return

    user_words = _words_for(conn, attempt_id, reference_text)
    if not user_words:
        st.caption("This attempt's word timings could not be read back.")
        return

    anchors = accent_charts.word_anchors(user_words, renderings[0].words())
    if len(anchors) < 2:
        st.caption(
            "The two readings could not be anchored on shared words, so there is no honest "
            "way to put them on one time axis."
        )
        return

    user_track = accent_resynth.pitch_track(wav_bytes)
    model_tracks = []
    for rendering in renderings:
        audio = rendering.audio()
        if audio is not None:
            model_tracks.append(accent_resynth.pitch_track(audio))

    frame = accent_charts.pitch_frame(user_track, model_tracks, anchors)
    boundaries = accent_charts.word_boundary_frame(anchors)
    rows = _pitch_findings(user_track, model_tracks)
    _chart_with_table(
        f"Both contours in semitones relative to each speaker's OWN median, never hertz — a "
        f"low voice and a synthetic one only overlay meaningfully that way, and what is left "
        f"on the chart is the SHAPE. Aligned on word starts, not by time-warping: a warp that "
        f"minimises distance would hide the timing errors this page also measures. "
        f"{len(model_tracks)} model "
        f"{'voice' if len(model_tracks) == 1 else 'voices'}.",
        accent_charts.pitch_chart(frame, boundaries) if not frame.empty else None,
        rows,
        "No pitch could be tracked in this recording.",
    )
    render_resynthesis(
        wav_bytes,
        user_track,
        model_tracks,
        anchors,
        attempt_id,
        measurement,
        modelled,
        published,
        normaliser,
    )


def _words_for(
    conn: sqlite3.Connection, attempt_id: int, reference_text: str
) -> list[dict[str, Any]]:
    """The normalised words for a stored attempt, re-parsed from its verbatim payload."""
    attempt = db.get_attempt(conn, attempt_id)
    if attempt is None:
        return []
    try:
        payload = json.loads(str(attempt["azure_raw_json"]))
        payloads = payload if isinstance(payload, list) else [payload]
        mode = Mode(str(attempt["mode"]))
        _, _, words = speech_analyzer.normalise(payloads, reference_text, mode)
        return list(words)
    except Exception:  # an unreadable payload is a missing chart, not a crash
        logger.warning("Could not re-parse attempt %s", attempt_id, exc_info=True)
        return []


def _pitch_findings(
    user_track: Sequence[tuple[float, float]],
    model_tracks: Sequence[Sequence[tuple[float, float]]],
) -> list[Any]:
    """Range and terminal slope, in semitones, for the four-column table."""
    rows: list[Any] = []
    mine_range = accent_charts.pitch_range_semitones(user_track)
    model_ranges = [
        value
        for value in (accent_charts.pitch_range_semitones(track) for track in model_tracks)
        if value is not None
    ]
    target_range = statistics.fmean(model_ranges) if model_ranges else None
    rows.append(
        vowel_measure.Finding(
            feature="Pitch range (10th–90th percentile)",
            user=("—" if mine_range is None else f"{mine_range:.1f} st"),
            target=("—" if target_range is None else f"{target_range:.1f} st"),
            delta=(
                "Not measurable from this recording."
                if mine_range is None or target_range is None
                else (
                    f"{mine_range - target_range:+.1f} st → "
                    + (
                        "your pitch moves less than the model's; a narrow range reads as flat "
                        "regardless of how accurate the sounds are"
                        if mine_range < target_range
                        else "your pitch moves more than the model's"
                    )
                )
            ),
        )
    )
    mine_slope = accent_charts.terminal_slope_semitones(user_track)
    model_slopes = [
        value
        for value in (accent_charts.terminal_slope_semitones(track) for track in model_tracks)
        if value is not None
    ]
    target_slope = statistics.fmean(model_slopes) if model_slopes else None
    rows.append(
        vowel_measure.Finding(
            feature="Terminal slope (last 0.35 s)",
            user=("—" if mine_slope is None else f"{mine_slope:+.1f} st"),
            target=("—" if target_slope is None else f"{target_slope:+.1f} st"),
            delta=(
                "Not measurable from this recording."
                if mine_slope is None or target_slope is None
                else (
                    f"{mine_slope - target_slope:+.1f} st → "
                    + (
                        "your phrase does not fall as far at the end. A level or rising "
                        "terminal on a statement reads as uncertainty in General American."
                        if mine_slope > target_slope
                        else "your phrase falls at least as far as the model's at the end"
                    )
                )
            ),
        )
    )
    return rows


def render_resynthesis(
    wav_bytes: bytes,
    user_track: Sequence[tuple[float, float]],
    model_tracks: Sequence[Sequence[tuple[float, float]]],
    anchors: Sequence[Any],
    attempt_id: int,
    measurement: Any,
    modelled: Mapping[str, Any],
    published: Mapping[str, Any],
    normaliser: Any,
) -> None:
    """Your own voice with the model's intonation, played after your own voice unchanged.

    **The ordering is the feature, not the layout.** A modified clip heard alone teaches
    nothing — the listener has nothing to difference it against and hears whatever they
    expected to hear. Original first, labelled, every time.
    """
    st.markdown("#### Hear it — your voice, one thing changed")
    st.caption(accent_resynth.OWN_VOICE_NOTICE)

    if not model_tracks:
        st.caption("No model contour has been captured for this text yet.")
        return

    key = f"resynth_{attempt_id}"
    # In the brief's order of value: intonation is the one that lands hardest, timing makes
    # under-reduction audible, and the single-vowel shift is the narrowest and most fragile.
    intonation, timing, vowel = st.columns(3)

    def run(label: str, build: Any) -> None:
        try:
            st.session_state[key] = build()
        except accent_resynth.ResynthesisError as exc:
            st.session_state.pop(key, None)
            st.warning(str(exc), icon="🎚️")

    if intonation.button("Native intonation", key=f"{key}_pitch"):
        run(
            "pitch",
            lambda: accent_resynth.corrected_pitch(
                wav_bytes, _target_contour(model_tracks, anchors)
            ),
        )
    if timing.button("Native vowel lengths", key=f"{key}_timing"):
        run(
            "timing",
            lambda: accent_resynth.corrected_timing(
                wav_bytes, _duration_stretches(measurement, modelled)
            ),
        )
    if vowel.button("Fix one vowel", key=f"{key}_vowel"):
        run(
            "vowel",
            lambda: _correct_worst_vowel(wav_bytes, measurement, published, normaliser),
        )

    result = st.session_state.get(key)
    if result is None:
        return

    # ORIGINAL FIRST. Always, in this order, both labelled.
    st.caption(f"**1. {accent_resynth.ORIGINAL_LABEL}**")
    st.audio(wav_bytes, format="audio/wav")
    st.caption(f"**2. {result.label}**")
    st.audio(result.audio, format="audio/wav")
    if result.note:
        st.info(result.note, icon="🎚️")


def _duration_stretches(
    measurement: Any, modelled: Mapping[str, Any]
) -> list[tuple[float, float, float]]:
    """(start, end, ratio) per vowel, toward the model's connected-speech durations.

    Against the MODEL table and never the published one: Hillenbrand's durations are
    citation-form words read in isolation, so stretching a vowel toward one of those would
    make every vowel in the clip roughly three times too long — a demonstration of the
    reference's artefact rather than of the speaker's timing.
    """
    stretches: list[tuple[float, float, float]] = []
    for token in measurement.accepted:
        target = modelled.get(token.vowel)
        if target is None or not target.duration_ms or not token.duration_ms:
            continue
        stretches.append((token.start_s, token.end_s, target.duration_ms / token.duration_ms))
    return stretches


def _correct_worst_vowel(
    wav_bytes: bytes, measurement: Any, published: Mapping[str, Any], normaliser: Any
) -> Any:
    """Shift the single worst-placed vowel token and leave the rest of the clip untouched.

    The worst token, not the worst category: the point of this surface is that everything
    either side is bit-identical, so it has to act on one span of audio.

    **Chosen and shifted in z, never in raw hertz.** Formants scale with vocal tract length,
    so ranking tokens by their hertz distance to a reference talker's mean picks whichever
    vowel carries the largest ANATOMICAL offset — a speaker with a longer tract has every F2
    low, and the demonstration would then shift a vowel toward the reference's larynx rather
    than toward the target accent. The gap is measured in the speaker's own normalised space,
    and the target is mapped back out through `Normaliser.hz` to ask the only question the
    manipulation can act on: where would THIS speaker's F2 be if the vowel sat on target.
    """
    worst: tuple[Any, float] | None = None
    worst_gap = 0.0
    for token in measurement.accepted:
        target = published.get(token.vowel)
        if target is None or target.f2_z is None or token.at50.f2 is None:
            continue
        _, produced_z, _ = normaliser.z(token.at50)
        if produced_z is None:
            continue
        gap = abs(target.f2_z - produced_z)
        if gap > worst_gap:
            _, aim_hz = normaliser.hz(None, target.f2_z)
            if aim_hz is None or aim_hz <= 0:
                continue
            worst, worst_gap = (token, aim_hz), gap
    if worst is None:
        raise accent_resynth.ResynthesisError(
            "No vowel in this recording has both a measurement and a target to move toward."
        )
    token, aim_hz = worst
    return accent_resynth.corrected_vowel(
        wav_bytes, token.start_s, token.end_s, float(token.at50.f2), float(aim_hz)
    )


def _target_contour(
    model_tracks: Sequence[Sequence[tuple[float, float]]], anchors: Sequence[Any]
) -> list[tuple[float, float]]:
    """The model's contour on the USER's clock, in semitones, averaged across voices.

    Averaged rather than one voice picked: a single synthesiser's contour carries its own
    habits, and what should transfer is the General American tendency.
    """
    per_time: dict[float, list[float]] = {}
    for track in model_tracks:
        if not track:
            continue
        median = statistics.median([hz for _, hz in track])
        for model_time, hz in track:
            mapped = accent_charts.to_user_clock(model_time, anchors)
            if mapped is None:
                continue
            per_time.setdefault(round(mapped, 2), []).append(accent_resynth.semitones(hz, median))
    return [(time_s, statistics.fmean(values)) for time_s, values in sorted(per_time.items())]


def render_rhythm_chart(
    conn: sqlite3.Connection, attempt_id: int, grouped: Mapping[str, Any]
) -> None:
    """One bar per vocalic interval, plus the nPVI figure against the TTS baseline."""
    attempt = db.get_attempt(conn, attempt_id)
    if attempt is None:
        return
    words = _words_for(conn, attempt_id, str(attempt["reference_text"]))
    if not words:
        st.caption("This attempt's timings could not be read back.")
        return

    runs = rhythm.vocalic_intervals(words)
    measured = rhythm.npvi(words)
    baseline = rhythm.baseline()
    reference_npvi = baseline.rhythm.npvi if baseline is not None else None
    gap = vowel_measure.rhythm_gap(measured.npvi, reference_npvi)

    rows = [
        vowel_measure.Finding(
            feature="Rhythm — nPVI over vocalic intervals",
            user=(
                "—"
                if measured.npvi is None
                else f"{measured.npvi:.1f} ({measured.pairs} pairs, {measured.runs} stretches)"
            ),
            target=("—" if reference_npvi is None else f"{reference_npvi:.1f} (TTS baseline)"),
            delta=(
                gap.detail
                if gap is not None
                else (
                    "Not enough connected speech to measure rhythm."
                    if measured.npvi is None
                    else "Within a point of the reference."
                )
            ),
        )
    ]
    frame = accent_charts.rhythm_frame(runs)
    _chart_with_table(
        "One bar per vowel, in the order you said them, broken at every pause — nPVI is "
        "computed inside unbroken stretches and never across one. A row of near-equal bars is "
        "what a syllable-timed rhythm carried into English looks like.",
        accent_charts.rhythm_chart(frame) if not frame.empty else None,
        rows,
        "Not enough connected speech in this reading to draw a rhythm.",
    )


def _capture_native(conn: sqlite3.Connection, reference_text: str) -> None:
    """Buy the model's reading of this text. Says what it spent, afterwards."""
    voice = tts.voice_name()
    try:
        with st.spinner(f"Synthesising and assessing with {voice}…"):
            rendering = native_model.capture(conn, reference_text, voice)
    except Exception as exc:  # noqa: BLE001 — every failure here is a message, not a crash
        st.error(utils.redact(str(exc)), icon="💸")
        return
    tts_left = budget.tts_meter(conn).remaining
    stt_left = budget.stt_meter(conn).remaining
    st.success(
        f"Captured {voice}: {rendering.characters:,} characters and "
        f"{rendering.seconds:.0f} seconds. Remaining this month: {tts_left:,.0f} characters, "
        f"{stt_left:,.0f} seconds.",
        icon="✅",
    )
    st.rerun()


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
    render_accent_charts(conn)
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

    if st.session_state.get(LADDER_KEY):
        render_ladder_practice(conn, job, running)
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
        render_ladder_offer(conn)
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
    render_ladder_offer(conn)

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


def render_ladder_offer(conn: sqlite3.Connection) -> None:
    """The way into the practice ladder: pick a reading, pick a level, start.

    Offered from Today rather than given its own tab. These are queue targets and Today is
    where "what am I doing today?" is answered — the same reason a perception block opens in
    place here instead of somewhere else.
    """
    st.markdown("#### Practise one unit against your own corrected voice")
    readings = [
        row
        for row in _measurable_attempts(conn)
        if ladder_practice.covers(str(row["reference_text"] or ""))
    ]
    if not readings:
        st.caption(
            f"Nothing to practise yet. The arrival bands were measured on one passage — "
            f"**{progress_view.BENCHMARK_TITLE}** — so a reading of that passage is what "
            f"this can judge. Read it on the Practice tab and it appears here."
        )
        return

    st.caption(
        "You hear what you said, a native saying it, and your own voice with one thing "
        "changed. Then you say it again, as many times as you like — every repetition is "
        "measured here, with no network and no allowance spent."
    )
    # Ids, never the rows themselves: Streamlit deep-copies a widget's options into session
    # state, and a `sqlite3.Row` cannot be pickled — which takes down the whole script, not
    # just this widget.
    labels = {
        int(row["id"]): f"#{row['id']} · {str(row['created_at'])[:16].replace('T', ' ')}"
        for row in readings
    }
    reading, level, go = st.columns([3, 2, 1])
    chosen = reading.selectbox(
        "From which reading",
        list(labels),
        format_func=lambda attempt_id: labels[attempt_id],
        key="ladder_reading",
    )
    rung = level.selectbox(
        "At what level",
        [ladder.Rung.WORD, ladder.Rung.SENTENCE, ladder.Rung.PARAGRAPH],
        index=1,
        format_func=lambda r: ladder.RUNG_LABELS[r].title(),
        key="ladder_rung",
        help=(
            "A sound is measured but never played on its own — you hear it inside its word. "
            "The sentence is where the difficulty actually is."
        ),
    )
    go.markdown("&nbsp;")
    if go.button("▶ Start", type="primary", key="ladder_start"):
        open_ladder(int(chosen), rung)
        st.rerun()


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
    unscripted = mode is Mode.UNSCRIPTED

    presets = PRESETS[mode]
    st.selectbox(
        "Prompt" if unscripted else "Practice text",
        ["Write my own", *presets],
        key=PRESET_KEY,
        on_change=_apply_preset,
        args=(mode,),
    )
    # **The same widget, holding a different kind of thing.** In Mode C this is the PROMPT: it
    # is never read aloud, never scored against, and never sent to Azure as a reference text.
    # It is stored so two recordings on one prompt can be paired into a spontaneous
    # calibration, and it is what the content scorer uses as the title to judge relevance.
    reference_text = st.text_area(
        "Prompt — talk about this, do not read it" if unscripted else "Reference text",
        key=TEXT_KEY,
        height=140,
    )
    if unscripted:
        render_unscripted_guidance(mode)

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
        # about the input, and both validators render their error as a side effect.
        if (validate_prompt if unscripted else validate_reference)(reference_text):
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


# --- The practice ladder ---------------------------------------------------------------------
# The three-way listen, as the practice surface rather than a demo inside a chart. Opens in
# place on Today, the way a perception block does, because these are queue targets and Today is
# where "what am I doing today?" is answered.

LADDER_KEY = "ladder_practice"
LADDER_TAKE = "ladder_take"


@dataclass(frozen=True)
class LadderContext:
    """Everything one practice session needs, loaded once per rerun."""

    attempt_id: int
    reference_text: str
    words: list[dict[str, Any]]
    audio: bytes
    alignment: Any
    voice: str | None
    native_words: list[dict[str, Any]]
    native_audio: bytes | None
    native_alignment: Any


def open_ladder(attempt_id: int, rung: ladder.Rung, unit: int = 0) -> None:
    st.session_state[LADDER_KEY] = {"attempt": attempt_id, "rung": rung.value, "unit": unit}
    st.session_state.pop(LADDER_TAKE, None)


def close_ladder() -> None:
    """Bailing. A first-class outcome, not a failure — and it must not stall the queue."""
    st.session_state.pop(LADDER_KEY, None)
    st.session_state.pop(LADDER_TAKE, None)


def ladder_context(conn: sqlite3.Connection, attempt_id: int) -> LadderContext | None:
    """Load the speaker's reading and the nearest reference voice's reading of the same text."""
    attempt = db.get_attempt(conn, attempt_id)
    if attempt is None:
        return None
    reference_text = str(attempt["reference_text"] or "")
    audio = stored_audio_bytes(conn, attempt_id)
    words = _words_for(conn, attempt_id, reference_text)
    if audio is None or not words:
        return None

    track = accent_resynth.pitch_track(audio)
    tracked = [hz for _, hz in track if hz > 0]
    voice = ladder_practice.nearest_voice(statistics.median(tracked) if tracked else None)
    rendering = (
        native_model.rendering_for(conn, reference_text, voice) if voice is not None else None
    )
    native_words = list(rendering.words()) if rendering is not None else []
    native_audio = rendering.audio() if rendering is not None else None

    return LadderContext(
        attempt_id=attempt_id,
        reference_text=reference_text,
        words=words,
        audio=audio,
        alignment=ladder.align(words, reference_text),
        voice=voice if native_audio is not None else None,
        native_words=native_words,
        native_audio=native_audio,
        native_alignment=ladder.align(native_words, reference_text),
    )


def ladder_floor(conn: sqlite3.Connection, reference_text: str) -> ladder.MetricFloor | None:
    """The speaker's own noise floor for these metrics, from two reads of the same passage.

    The same construction the vowel noise floor uses, over the same two recordings: how far
    these numbers wander between two reads with no learning in between. Without it nothing can
    be called movement, so nothing can resolve — which is the honest state, not a bug.
    """
    rows = [
        row
        for row in db.attempt_series(conn)
        if str(row["reference_text"] or "") == reference_text and not row["rep"]
    ]
    if len(rows) < 2:
        return None
    readings: list[dict[int, dict[str, float]]] = []
    for row in rows[:2]:
        audio = stored_audio_bytes(conn, int(row["id"]))
        words = _words_for(conn, int(row["id"]), reference_text)
        if audio is None or not words:
            continue
        track = accent_resynth.pitch_track(audio)
        divisor = ladder.mean_word_seconds(words)
        found: dict[int, dict[str, float]] = {}
        for unit in ladder_practice.units(words, reference_text, ladder.Rung.SENTENCE):
            if unit.script_index is not None:
                found[unit.script_index] = ladder.scalars(unit.span, words, track, divisor=divisor)
        readings.append(found)
    if len(readings) < 2:
        return None
    return ladder.metric_noise_floor(readings[0], readings[1])


def _metric_line(judged: Any) -> str:
    """One metric, in words, saying which bar it cleared and which it did not."""
    label = ladder.METRIC_LABELS.get(judged.metric, judged.metric)
    if judged.value is None:
        return f"**{label}** — not measurable on this take."
    if judged.band is None:
        return f"**{label}** — {judged.value:.2f}, with no native band for this unit to judge it."
    where = (
        "inside the native range"
        if judged.arrived
        else f"{judged.distance_sd:.1f} SD outside the native range"
    )
    moved = {
        None: "first take, so there is nothing to have moved from",
        True: "and the change clears your own variation",
        False: "but the change is smaller than your own session-to-session variation",
    }[judged.moved]
    return (
        f"**{label}** — you {judged.value:.2f}, native "
        f"{judged.band.mean:.2f} ± {judged.band.sd:.2f} · {where}, {moved}."
    )


def render_ladder_practice(
    conn: sqlite3.Connection, job: AssessJob | None = None, running: bool = False
) -> None:
    """Mine, native, mine-with-one-thing-changed — then say it again and see where it lands."""
    state = st.session_state.get(LADDER_KEY) or {}
    rung = ladder.Rung(str(state.get("rung") or ladder.Rung.SENTENCE.value))
    context = ladder_context(conn, int(state.get("attempt") or 0))

    header, bail = st.columns([5, 1])
    header.markdown(f"### Practising one {ladder.RUNG_LABELS[rung]}")
    if bail.button("Drop this", key="ladder_drop"):
        # First-class outcome. Nothing is written, nothing is marked failed.
        close_ladder()
        st.rerun()

    if context is None:
        st.warning(
            "That attempt's recording or its word timings are no longer readable, so there is "
            "nothing to practise against. Recordings live under a gitignored directory.",
            icon="🎙️",
        )
        return

    units = ladder_practice.units(context.words, context.reference_text, rung)
    if not units:
        st.info(
            "This recording has no practisable unit at this level. A sentence needs the "
            "reading to line up with its script, which free speech has none of.",
            icon="🪜",
        )
        return

    index = min(int(state.get("unit") or 0), len(units) - 1)
    unit = units[index]
    span = unit.span

    if len(units) > 1:
        chosen = st.selectbox(
            "Which one",
            range(len(units)),
            index=index,
            format_func=lambda i: f"{i + 1}. {units[i].span.label[:70]}",
            key="ladder_unit",
        )
        if chosen != index:
            st.session_state[LADDER_KEY] = {**state, "unit": int(chosen)}
            st.session_state.pop(LADDER_TAKE, None)
            st.rerun()

    st.markdown(f"> {span.label}")

    # --- The three-way listen. Original first, always, and labelled.
    st.markdown("#### Hear it — your voice, one thing changed")
    st.caption(accent_resynth.OWN_VOICE_NOTICE)

    try:
        mine = ladder.cut(context.audio, span)
    except ladder.LadderError as exc:
        st.warning(str(exc), icon="✂️")
        return

    st.caption("**1. Mine** — what you actually said")
    st.audio(mine, format="audio/wav")

    leg = None
    if context.native_audio is not None and context.voice is not None:
        leg = ladder_practice.native_leg(
            span,
            context.alignment,
            context.native_alignment,
            context.native_words,
            context.native_audio,
            context.voice,
        )
    if leg is not None:
        st.caption(f"**2. Native** — {leg.voice.replace('en-US-', '').replace('Neural', '')}")
        st.audio(leg.audio, format="audio/wav")
    else:
        st.caption(
            "**2. Native** — no stored voice has read this unit, so there is nothing to play. "
            "A word you said that is not in the script has no native version by definition."
        )

    render_ladder_corrections(context, span)

    st.divider()
    render_ladder_repeat(conn, context, unit, job, running)


def render_ladder_corrections(context: LadderContext, span: ladder.Span) -> None:
    """The third leg. Three separate corrections, each changing exactly one thing."""
    key = f"ladder_fix_{span.rung.value}_{span.start_s:.2f}"
    pitch, timing = st.columns(2)

    def run(build: Any) -> None:
        try:
            st.session_state[key] = build()
        except accent_resynth.ResynthesisError as exc:
            st.session_state.pop(key, None)
            st.warning(str(exc), icon="🎚️")

    model_tracks = (
        [accent_resynth.pitch_track(context.native_audio)]
        if context.native_audio is not None
        else []
    )
    anchors = accent_charts.word_anchors(context.words, context.native_words)

    if pitch.button("Native intonation", key=f"{key}_pitch", disabled=not model_tracks):
        run(
            lambda: ladder_practice.corrected_pitch_in(
                context.audio, span, _target_contour(model_tracks, anchors)
            )
        )
    if timing.button("Native vowel lengths", key=f"{key}_timing"):
        st.session_state.pop(key, None)
        st.caption("Vowel lengths need the measurement from a banked take.")

    result = st.session_state.get(key)
    if result is None:
        return
    st.caption(f"**3. {result.label}**")
    st.audio(result.audio, format="audio/wav")
    if result.note:
        st.info(result.note, icon="🎚️")


def render_ladder_repeat(
    conn: sqlite3.Connection,
    context: LadderContext,
    unit: ladder_practice.Unit,
    job: AssessJob | None = None,
    running: bool = False,
) -> None:
    """Say it again, as many times as you like, and see where each one lands."""
    st.markdown("#### Say it again")
    st.caption(
        "Every repetition is measured here on this machine — no network, no waiting, no "
        "allowance spent, however many times you go round. Rhythm and word length need "
        "Azure's phoneme boundaries, so they stay dark until you bank a take."
    )

    take = st.audio_input("Your attempt", key=LADDER_TAKE)
    if take is None:
        return

    try:
        wav_bytes, _ = audio_utils.prepare(take.getvalue(), Mode.DRILL)
    except audio_utils.AudioError as exc:
        st.warning(str(exc), icon="🎙️")
        return

    values = ladder.local_scalars(wav_bytes, unit.span.rung)
    floor = ladder_floor(conn, context.reference_text)
    previous = ladder.local_scalars(ladder.cut(context.audio, unit.span), unit.span.rung)
    judged = ladder.verdict(unit.span, values, unit.bands, previous=previous, floor=floor)

    st.audio(wav_bytes, format="audio/wav")
    if not unit.judgeable:
        st.info(
            "Nothing here can be judged: this passage has no measured native band, so there "
            "is no range to be inside. Practising still works; resolving does not.",
            icon="📏",
        )
        return

    for metric in judged.metrics:
        if metric.metric in ladder.LOCAL_METRICS:
            st.markdown(f"- {_metric_line(metric)}")
    dark = [m for m in judged.metrics if m.metric not in ladder.LOCAL_METRICS]
    if dark:
        names = ", ".join(ladder.METRIC_LABELS.get(m.metric, m.metric) for m in dark)
        st.caption(f"Dark until you bank a take: {names}.")
    if floor is None:
        st.caption(
            "No movement can be called progress yet — that needs two reads of this passage "
            "to know how much these numbers wander on their own."
        )

    render_ladder_bank(conn, unit, wav_bytes, job, running)


def render_ladder_bank(
    conn: sqlite3.Connection,
    unit: ladder_practice.Unit,
    wav_bytes: bytes,
    job: AssessJob | None,
    running: bool,
) -> None:
    """Spend an assessment on this take, so the dark instruments light up.

    The deliberate half of the hybrid: repetition is free and unlimited, and buying the full
    instrument set is a button you press rather than something that happens to you. Priced
    before it is offered, per the standing rule that quota is spent deliberately and said so.

    The take is stored as an ordinary attempt tagged `rep` — same scores, same payload, same
    re-derivability — and `progress_view.without_reps` keeps it out of the free-practice cloud.
    """
    st.markdown("##### Bank this take")
    seconds = audio_utils.duration_seconds(wav_bytes)
    try:
        audio_utils.validate_duration(seconds, Mode.DRILL)
    except audio_utils.AudioError as exc:
        st.caption(f"Too short or too long to assess: {exc}")
        return

    dark = ", ".join(
        ladder.METRIC_LABELS.get(metric, metric)
        for metric in ladder.METRICS.get(unit.span.rung, ())
        if metric not in ladder.LOCAL_METRICS
    )
    st.caption(
        f"Spends **{seconds:.1f} s** of the monthly speech allowance to score this take the "
        f"way a full attempt is scored — which is what lights up {dark or 'the rest'}. "
        f"{budget.summary_line(conn)}"
    )
    if st.button(
        "💾 Bank this take",
        key="ladder_bank",
        disabled=running or utils.offline_mode(),
        help="Repeating is free. This is the one thing here that spends anything.",
    ):
        try:
            budget.preflight_stt(conn, seconds, Mode.DRILL)
        except budget.BudgetError as exc:
            st.warning(str(exc), icon="💸")
            return
        start_assessment(
            conn,
            wav_bytes,
            seconds,
            unit.span.label,
            Mode.DRILL,
            key=f"ladder-{unit.span.rung.value}-{unit.script_index}",
            tags=(db.REP_TAG,),
        )
        st.rerun()
    if running:
        st.caption("An assessment is already in flight — only one runs at a time.")
    elif utils.offline_mode():
        st.caption("Disabled under OFFLINE_MODE, which is what stands between this and a charge.")


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
