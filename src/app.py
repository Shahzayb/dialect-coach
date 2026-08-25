"""Streamlit entry point for the pronunciation coach.

UI only. Every API call lives in `speech_analyzer` and `tts`, every write in `db`, every
spend decision in `budget` — this file orchestrates them and renders the result.

**Two tabs, and that is the whole application.** Analyze records or uploads a reading and
shows every result Azure returned for it; History pages back through what has been recorded
and re-opens any of it. Everything else — the perception trainer, shadowing, the practice
ladder, the progress charts, the accent measurement and resynthesis — was deleted on
2026-08-25 and is recoverable at tag `v0.12.0-full`. Do not re-add a surface here without a
plan file: this file was 5,260 lines when it had four tabs.

The rendering aims at one thing: making the diagnosis legible, audible and actionable.
Colour-coded reference text, the reference-vs-heard diff, expected → produced IPA per
flagged word, your own audio for that word beside a native rendering of it, the delivery
panel, and the coaching report on top of all of it.

The coaching report is always present and always free: `fallback_coach` builds it from the
Azure data alone, with no key and no network. Gemini's only remaining job is the prosody
annotation — the same words marked up with stress, pauses and linking — and asking for it
is a button, not a side effect of assessing, because a click is the point at which anything
is sent to Google.
"""

from __future__ import annotations

import difflib
import html
import json
import logging
import sqlite3
import threading
import time
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import streamlit as st

import ai_coach
import audio_utils
import budget
import db
import fallback_coach
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

# Two, since 2026-08-25. Scripted is continuous recognition whatever its length — the
# single-shot "Drill" mode went with the duration ceilings, because `recognize_once_async`
# caps at roughly 15 s and a mode that silently truncates a long read is worse than one code
# path with locally-diffed miscues.
MODE_LABELS: dict[str, Mode] = {
    "Scripted — read a text": Mode.PARAGRAPH,
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

# Azure's own guidance for unscripted assessment: 15 seconds — "equivalent to more than 50
# words" — up to 10 minutes. The target here is the middle of that. There is no hard ceiling
# any more; this is what to aim for, not what is allowed.
UNSCRIPTED_TARGET_SECONDS = (180, 240)

# Azure needs at least 15 seconds of unscripted speech for the assessment to mean anything.
# Its own wording for that length is "equivalent to more than 50 words".
UNSCRIPTED_MIN_WORDS = 50

PRESETS: dict[Mode, dict[str, str]] = {
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
        # Short sound-loaded lines first, then the connected-speech paragraphs. These four
        # were the old Drill presets and they outlived that mode: they load the sounds most
        # likely to be substituted by Urdu/Punjabi L1 speakers — /θ/ /ð/, /v/ vs /w/, /æ/ vs
        # /ɛ/, /ʃ/ /s/ /z/ /dʒ/ — and a short reading is still a legitimate scripted reading,
        # it just goes through continuous recognition now like everything else. No digits in
        # any of them: Azure normalises "33" and "thirty-three" differently, which breaks
        # word alignment.
        "Th (/θ/, /ð/)": "These three brothers thought the weather was worth the trouble.",
        "V versus W": "Very well, we will invite the whole village to the west wing.",
        "Short a versus short e (/æ/, /ɛ/)": (
            "That bad man had a red cap and a black pen in his hand."
        ),
        "Sibilants (/s/, /ʃ/, /z/, /dʒ/)": (
            "She chose the usual visual measure just as the season closed."
        ),
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

# Which attempt History has open, or None. A plain session key rather than a widget key:
# Streamlit deletes a widget's value when the widget is not registered on a pass, and the
# Analyze tab body runs first on every rerun — a keyed value would be dropped before History
# ever drew the control that owns it. Same mechanism the old Accent picker needed.
HISTORY_OPEN_KEY = "history_open_attempt"
HISTORY_PAGE_KEY = "history_page"
HISTORY_MODE_KEY = "history_mode_filter"

# Rows per History page. Twelve fits a screen without scrolling past the pager.
HISTORY_PAGE_SIZE = 12


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
    st.caption(
        f"**Talk, do not read.** Azure needs at least 15 seconds — more than "
        f"{UNSCRIPTED_MIN_WORDS} words — for an unscripted assessment to mean anything. Aim "
        f"for {low // 60}-{high // 60} minutes. There is no upper limit; talk as long as you "
        f"have something to say."
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
        wav_bytes, seconds = audio_utils.prepare(audio)
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
            # A stop can land between the SDK returning and this line: the result arrived,
            # and it is thrown away unrecorded rather than charged for and shown.
            return AssessOutcome(cancelled=True, reached_azure=reached_azure)

        digest = utils.sha256_bytes(wav_bytes)
        with _DB_LOCK:
            # The audio row is written under the same lock as the attempt it belongs to, so no
            # reader can ever see a stored attempt whose recording has not landed yet.
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
            _keep_recording(conn, attempt_id, wav_bytes, digest)
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
) -> None:
    """Spawn the worker for one assessment and remember it for the poll loop."""
    cancel_event = threading.Event()
    job = AssessJob(
        cancel_event=cancel_event,
        key=key,
        reference_text=reference_text,
        mode=mode,
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

    _cache_put(
        CachedAttempt(
            key=job.key,
            assessment=outcome.assessment,
            reference_text=job.reference_text,
            attempt_id=outcome.attempt_id,
            mode=job.mode,
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


def _annotation_attempted(key: str) -> bool:
    """Whether a real Gemini call has already been bought for this attempt.

    Tracked separately from whether an annotation is on screen, because a call that was
    spent and then rejected — malformed JSON, a changed word sequence — leaves nothing
    behind, and that must not read as "never asked". The guard belongs where the spend is.
    """
    return bool(lru_get(_session_cache("annotation_attempted"), key))


def _mark_annotation_attempted(key: str) -> None:
    lru_put(_session_cache("annotation_attempted"), key, True, CACHE_LIMIT)


def coaching_for(conn: sqlite3.Connection, entry: CachedAttempt) -> tuple[Any, str]:
    """The report for this attempt, and which coach wrote it.

    **There is one coach now.** `fallback_coach` builds the whole report from the Azure data
    alone — no key, no network, no button — and Gemini's remaining job is the separate
    prosody annotation below. The `source` half of the return survives because History
    re-reads rows written when Gemini did write coaching, and the page says which it is
    looking at.

    The session cache is checked before anything is produced, for the same reason the TTS
    cache is: Streamlit re-runs this script top to bottom on every widget interaction. The
    report is cheap to rebuild but is cached anyway, so the row is written once rather than
    on every rerun.
    """
    cache = _session_cache("coaching")
    cached = lru_get(cache, entry.key)
    if cached is not None:
        return cast("tuple[Any, str]", cached)

    try:
        report = fallback_coach.build(entry.assessment, entry.mode)
    except Exception as exc:  # a report is promised on every assessment
        logger.error("Could not build the coaching report", exc_info=True)
        report = fallback_coach.emergency_report(f"{type(exc).__name__}: {exc}")

    source = fallback_coach.SOURCE_FALLBACK
    lru_put(cache, entry.key, (report, source), CACHE_LIMIT)
    if entry.attempt_id:
        # Verbatim, exactly as the Azure response is stored: changing what this panel shows
        # later is then a re-parse of a stored row rather than another call.
        db.attach_coaching(
            conn, entry.attempt_id, gemini_raw=report.model_dump(), coach_source=source
        )
    return report, source


def annotation_for(
    conn: sqlite3.Connection, entry: CachedAttempt, *, ask_model: bool
) -> tuple[Any, str]:
    """Gemini's prosody annotation for this attempt, and why there is none when there is not.

    Three sources, in order: the session cache, the stored row, then a paid call — and only
    a click reaches the third. An attempt re-opened from History renders its stored
    annotation for free; asking for one it never had spends a call, which is why this is a
    button and not something every assessment does.
    """
    cache = _session_cache("annotation")
    cached = lru_get(cache, entry.key)
    if cached is not None and (not ask_model or _annotation_attempted(entry.key)):
        return cast("tuple[Any, str]", cached)

    if cached is None and entry.attempt_id:
        stored = ai_coach.annotation_from_raw(db.annotation_for(conn, entry.attempt_id))
        if stored is not None:
            found: tuple[Any, str] = (stored, "")
            lru_put(cache, entry.key, found, CACHE_LIMIT)
            if not ask_model:
                return found

    if not ask_model:
        return None, ""

    # Marked before the call, not after: a call that reached Gemini and was then rejected has
    # already been spent, so keying this off the outcome would leave the button live and let
    # the same failure be bought over and over.
    _mark_annotation_attempted(entry.key)
    with st.spinner("Asking Gemini how it should have been read…"):
        outcome = ai_coach.annotate(entry.assessment, entry.reference_text, entry.mode)

    result: tuple[Any, str] = (outcome.annotation, outcome.reason)
    lru_put(cache, entry.key, result, CACHE_LIMIT)
    if outcome.annotation is not None and entry.attempt_id:
        db.attach_annotation(conn, entry.attempt_id, raw=outcome.raw)
    return result


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
    """What is wrong, and what to do about it. Free, offline, and on every attempt.

    No button and no spend: `fallback_coach` builds this from the Azure data alone. The
    Gemini path that used to sit here writes the prosody annotation now — see
    `render_annotation` below, which is the one thing on this page that costs a call.
    """
    st.subheader("What to work on")

    report, source = coaching_for(conn, entry)

    if source == fallback_coach.SOURCE_GEMINI:
        # A row from before 2026-08-25, re-opened from History. Gemini wrote coaching then;
        # it does not now, and the page says which it is showing rather than implying the
        # current coach produced it.
        st.caption(
            f"Written by {ai_coach.model_name()} when this attempt was assessed, then checked "
            f"against the Azure findings. Gemini no longer writes coaching — newer attempts "
            f"are scored by the offline coach below."
        )
    else:
        st.caption(
            "Written from the Azure data alone. No key, no network, and nothing sent "
            "anywhere — this report is on every attempt whether or not Gemini is reachable."
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


# How an annotated word is drawn. Stress is weight rather than colour, because the word
# colours on this page already mean accuracy and a second colour scheme on the same words
# would be read as the same scale. The break markers are punctuation-like for the same
# reason: they say where to stop, not how badly anything went.
BREAK_MARKS: dict[str, str] = {"none": "", "minor": " |", "major": " ‖"}


def annotated_html(words: Sequence[Any]) -> str:
    """The annotated passage as one flowing block. Reads as prose, not as a table."""
    parts: list[str] = []
    for word in words:
        text = html.escape(word.word)
        if word.stress:
            text = f'<b style="border-bottom:2px solid currentColor;">{text}</b>'
        if word.linked:
            # A tie bar after the word, on the join it describes. U+2040 rather than an
            # underscore: it sits on the baseline between the two words instead of under one.
            text += '<span style="opacity:0.45;">⁀</span>'
        mark = BREAK_MARKS.get(word.break_after, "")
        if mark:
            text += f'<span style="opacity:0.55;font-weight:600;">{mark}</span>'
        title = html.escape(word.note, quote=True) if word.note else ""
        parts.append(f'<span title="{title}">{text}</span>')
    return '<div style="line-height:2.2;font-size:1.05rem;">' + " ".join(parts) + "</div>"


def render_annotation(conn: sqlite3.Connection, entry: CachedAttempt) -> None:
    """Gemini's read-it-this-way markup of the passage. The one paid button on the page.

    The button is created before the annotation is rendered, so the click and the answer
    land in the same rerun rather than showing the previous state for one pass.
    """
    st.subheader("How it should have been read")

    usable, reason = ai_coach.available()
    existing, _ = annotation_for(conn, entry, ask_model=False)

    asked = st.button(
        "✨ Mark up the passage with Gemini",
        key=f"annotate-{entry.key}",
        # Disabled once a call has been *bought* for this attempt, whatever came back: a
        # spent call that was rejected must not be re-buyable.
        disabled=not usable or _annotation_attempted(entry.key),
        help="One free-tier call. Everything else on this page is already complete without it.",
    )
    if usable:
        st.caption(
            "Sends the passage and the delivery findings to Google — never your audio. "
            "Free-tier prompts and responses may be used to improve Google's products, so "
            "this is a click rather than something every assessment does."
        )
    else:
        st.caption(reason)

    annotation, why_not = annotation_for(conn, entry, ask_model=asked)
    if annotation is None:
        annotation = existing
    if annotation is None:
        if asked and why_not:
            st.info(why_not, icon="🛟")
        return

    if why_not:
        st.caption(why_not)
    st.markdown(annotated_html(annotation.words), unsafe_allow_html=True)
    st.caption(
        "**Bold underlined**: carries sentence stress. `|` a short pause, `‖` a full stop. "
        "The tie between two words means they run together with no gap. Hover a word for "
        "its note. The words are your own — the model is not allowed to change any of them, "
        "and an answer that did is dropped rather than shown."
    )
    if annotation.summary:
        st.markdown(annotation.summary)


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


def word_clip(recording: bytes | None, word: dict[str, Any]) -> bytes | None:
    """Your own audio for one word, cut at Azure's offsets. None when it cannot be cut.

    Every reason this returns None is ordinary rather than an error: the recording was not
    kept, the word was omitted so it has no span, or the span falls outside the audio. The
    caller renders nothing in that case — an empty player would imply the clip exists.
    """
    if recording is None:
        return None
    start, end = word.get("start_s"), word.get("end_s")
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        return None
    try:
        return audio_utils.slice_wav(recording, float(start), float(end))
    except audio_utils.AudioError:
        return None


def render_azure_detail(word: dict[str, Any]) -> None:
    """Everything Azure returned about this word that the card above does not already show.

    Collapsed, because it is reference rather than diagnosis: the phoneme row, the syllable
    line and the score are what the learner acts on, and these are the numbers underneath
    them. Offsets are shown in seconds AND in the raw 100-ns ticks Azure sent, because the
    ticks are what a stored payload holds and a reader checking one against the other should
    not have to do the arithmetic.
    """
    rows: list[str] = []
    start, end = word.get("start_s"), word.get("end_s")
    if isinstance(start, (int, float)) and isinstance(end, (int, float)):
        rows.append(
            f"- **Span** {start:.2f}s → {end:.2f}s ({end - start:.3f}s) · "
            f"offset {word.get('offset_ticks')} ticks, duration {word.get('duration_ticks')} ticks"
        )
    for key, label in (("error_type", "Error type"), ("disfluency", "Disfluency")):
        value = word.get(key)
        if value and value != "None":
            rows.append(f"- **{label}** {value}")
    detail = word.get("prosody_detail") or {}
    if detail:
        rows.append(f"- **Prosody feedback** `{json.dumps(detail, ensure_ascii=False)}`")
    for syllable in word.get("syllables") or []:
        if not syllable.get("syllable"):
            continue
        score = syllable.get("score")
        shown = f"{score:.1f}" if isinstance(score, (int, float)) else "—"
        rows.append(
            f"- **Syllable** `{syllable['syllable']}` {shown} · "
            f"offset {syllable.get('offset_ticks')}, duration {syllable.get('duration_ticks')}"
        )
    for phoneme in word.get("phonemes") or []:
        score = phoneme.get("score")
        shown = f"{score:.1f}" if isinstance(score, (int, float)) else "—"
        alternates = ", ".join(
            f"/{alt['phoneme']}/ {alt['score']:.1f}"
            for alt in (phoneme.get("nbest") or [])
            if alt.get("phoneme")
        )
        line = (
            f"- **Phoneme** /{phoneme.get('phoneme')}/ {shown} · "
            f"offset {phoneme.get('offset_ticks')}, duration {phoneme.get('duration_ticks')}"
        )
        if alternates:
            line += f" · alternates: {alternates}"
        rows.append(line)

    if not rows:
        return
    with st.expander("Everything Azure returned for this word"):
        st.markdown("\n".join(rows))


def render_word_card(
    conn: sqlite3.Connection,
    word: dict[str, Any],
    index: int,
    recording: bytes | None = None,
) -> None:
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

        # **Your own audio for this word, directly under the native one.** The comparison is
        # the feature: a phoneme row says /ɹ/ became /w/, and this is the half that lets you
        # hear that it did. Cut from the stored recording at Azure's own word offsets, so the
        # clip is the recogniser's idea of the word rather than a guess at where it fell.
        clip = word_clip(recording, word)
        if clip is not None:
            st.caption("How you said it:")
            st.audio(clip, format="audio/wav")

        render_azure_detail(word)


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


def render_transcript(entry: CachedAttempt) -> None:
    """What Mode C's phoneme diagnosis was actually scored against.

    **Shown before anything derived from it**, because everything below is only as good as
    this: the second pass scores each phoneme against the word this transcript says was there.
    If a word here is wrong, the sounds blamed for it are wrong too, and the reader is the only
    one who can notice. Scripted mode already shows the reference text the user typed; this
    is the equivalent for the mode where nobody typed one.
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
    """One assessed attempt, whole. Shared by Analyze and by History.

    `source` is the recording to play back: the live `audio_input` value on Analyze, the
    stored bytes on History, and None when neither exists. It is also what the per-word
    clips are cut from, so a re-opened attempt whose audio was not kept simply shows no
    "how you said it" players rather than an error.
    """
    assessment, reference_text = entry.assessment, entry.reference_text
    if entry.mode is Mode.UNSCRIPTED:
        st.caption(f"Prompt: _{reference_text}_" if reference_text else "No prompt recorded.")
    render_transcript(entry)
    render_scores(assessment, entry.mode)
    render_error_counts(assessment)
    # Directly under the scores: what to do about them comes before the evidence for them.
    render_coaching(conn, entry)
    render_annotation(conn, entry)
    # **No script-versus-heard diff in unscripted mode.** There is no script: `reference_text`
    # holds the PROMPT, and diffing a prompt against what somebody freely said would strike
    # through every word of the prompt and italicise every word they spoke. `render_transcript`
    # above is what answers "what did Azure hear" for this mode.
    if entry.mode is not Mode.UNSCRIPTED:
        render_diff(assessment, reference_text)
    render_colour_coded(assessment, entry.mode)

    st.subheader("Hear the whole thing")
    # Unscripted synthesises the TRANSCRIPT, not `reference_text` — the prompt was never spoken
    # and hearing a native reading of "Explain a technical decision" teaches nothing. Scripted
    # keeps the script for the reason `techContext` already records: the reference text is what
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

    # Read once for the whole page rather than per card: a paragraph can flag thirty words,
    # and re-reading a 3 MB WAV off disk thirty times per rerun is the kind of cost Streamlit's
    # re-run-everything model turns into a frozen page.
    recording = _recording_bytes(conn, entry, source)

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
            "Worst first. The native version of each word is synthesised on its own, so you "
            "hear it in citation form — right for drilling a sound, but not how it sounds "
            "inside the sentence. Your own clip beneath it is cut from this recording at "
            "Azure's word boundaries, so it is the word in context. Use the whole-text "
            "playback above to compare the two at sentence length."
        )
        index = 0
        for word in needs_attention:
            render_word_card(conn, word, index, recording)
            index += 1
        if perfect:
            # The index keeps counting across both groups: `render_word_card` builds its
            # playback widget keys from it, and a repeated key is a hard Streamlit error.
            with st.expander(
                f"Scored 100 but still flagged ({len(perfect)}) — delivery, not sounds"
            ):
                for word in perfect:
                    render_word_card(conn, word, index, recording)
                    index += 1

    render_delivery(assessment)
    render_attempt_detail(assessment)


def _recording_bytes(conn: sqlite3.Connection, entry: CachedAttempt, source: Any) -> bytes | None:
    """The attempt's audio as PCM WAV, for slicing. None when there is nothing to slice.

    `source` on Analyze is Streamlit's `UploadedFile`, whose bytes are whatever the browser
    recorded — webm, usually — and `slice_wav` needs PCM. The stored copy is already
    converted, so it is preferred; the live object is converted on the spot only when the
    row has not landed yet.
    """
    if entry.attempt_id:
        stored = stored_audio_bytes(conn, entry.attempt_id)
        if stored is not None:
            return stored
    if source is None:
        return None
    try:
        return audio_utils.to_pcm_wav(source.getvalue())
    except (AttributeError, audio_utils.AudioError):
        return None


def render_attempt_detail(assessment: speech_analyzer.Assessment) -> None:
    """The whole-attempt numbers Azure returned that no panel above claims.

    Collapsed, like the per-word detail: this is the rest of the payload, present so that
    "show me everything Azure said" is true of this page rather than nearly true.
    """
    scores = dict(assessment.overall_scores or {})
    with st.expander("Everything Azure returned for this attempt"):
        rows = [f"- **{key}** {value}" for key, value in scores.items() if value is not None]
        missing = [key for key, value in scores.items() if value is None]
        if missing:
            # Named rather than omitted: "Azure did not return a prosody score" is a fact
            # about the attempt, and a silently absent row reads as a rendering bug.
            rows.append(f"- **Not returned by Azure** {', '.join(sorted(missing))}")
        rows.append(f"- **Words** {len(assessment.words)}")
        rows.append(f"- **Azure payloads (utterances)** {len(assessment.raw)}")
        rows.append(f"- **Recognition passes charged** {assessment.attempts}")
        if assessment.offline:
            rows.append("- **Replayed from a committed fixture** — no call was made")
        st.markdown("\n".join(rows))


# --- Stored recordings ------------------------------------------------------------------------


def _keep_recording(
    conn: sqlite3.Connection, attempt_id: int, wav_bytes: bytes, digest: str
) -> None:
    """Write the recording to disk and remember where it went. Never raises.

    Called on the worker thread, inside `_DB_LOCK` and in the same transaction as the attempt
    row, so no reader can see an attempt whose recording has not landed yet.

    A failure here must not lose an assessment that was already paid for: History falls back
    to "the recording was not kept" and the word-level clips are simply absent, which is a
    smaller loss than discarding the Azure result over a full disk.
    """
    try:
        path = audio_utils.keep(wav_bytes, digest)
    except Exception:
        logger.warning("Could not keep the recording for attempt %s", attempt_id, exc_info=True)
        return
    if path is None:
        return
    db.record_audio(
        conn,
        attempt_id,
        path=str(path),
        sha256=digest,
        size_bytes=len(wav_bytes),
        sample_rate=audio_utils.TARGET_SAMPLE_RATE,
    )


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


# --- Analyze ----------------------------------------------------------------------------------
# Record or upload, assess, and read the result. One of the two surfaces in this application.


def render_analyze(conn: sqlite3.Connection, job: AssessJob | None, running: bool) -> None:
    """The Analyze tab: record or upload, assess, and read every result Azure returned."""
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
    # **The same widget, holding a different kind of thing.** When unscripted this is the
    # PROMPT: it is never read aloud, never scored against, and never sent to Azure as a
    # reference text. It is stored anyway, because it is the only thing that makes an
    # unscripted row readable in History.
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

    # Streamlit executes BOTH tab bodies on every rerun, and `render_result` derives its
    # widget keys from the attempt — so a result drawn here and again in History is not
    # merely odd, it is a hard duplicate-key error. History renders only when it has an
    # attempt open, and opening one is what clears `last_key`; see `_open_history_attempt`.
    last_key = st.session_state.get("last_key")
    if last_key and not st.session_state.get(HISTORY_OPEN_KEY):
        cached = _cache_get(last_key)
        if cached is not None:
            st.divider()
            render_result(conn, cached, source)


def _open_history_attempt(attempt_id: int) -> None:
    """Open one stored attempt. Clears the live result so only one `render_result` runs.

    Both tab bodies execute on every rerun and `render_result` derives its widget keys from
    the attempt, so two of them on one pass is a hard duplicate-key error rather than a
    cosmetic one. Clearing `last_key` here is what makes Analyze stand down.
    """
    st.session_state[HISTORY_OPEN_KEY] = int(attempt_id)
    st.session_state["last_key"] = None
    st.session_state["now_playing"] = None


def _close_history_attempt() -> None:
    st.session_state[HISTORY_OPEN_KEY] = None
    st.session_state["now_playing"] = None


def attempt_from_row(row: Any) -> CachedAttempt | None:
    """Rebuild a renderable attempt from a stored row. None when the payload will not parse.

    The whole reason `azure_raw_json` is kept verbatim: re-opening an attempt from History
    is a re-parse of stored bytes, not another call to Azure. `utils.mode_of` rather than
    `Mode(...)` because rows written before 2026-08-25 carry `'drill'`, which would raise.
    """
    try:
        payload = json.loads(row["azure_raw_json"])
    except (ValueError, TypeError):
        logger.warning("Attempt %s has an unreadable Azure payload", row["id"])
        return None
    payloads = payload if isinstance(payload, list) else [payload]
    mode = utils.mode_of(row["mode"])
    try:
        assessment = speech_analyzer.assessment_from_payloads(
            payloads, str(row["reference_text"] or ""), mode
        )
    except Exception:
        logger.warning("Could not re-normalise attempt %s", row["id"], exc_info=True)
        return None
    return CachedAttempt(
        key=f"history-{row['id']}",
        assessment=assessment,
        reference_text=str(row["reference_text"] or ""),
        attempt_id=int(row["id"]),
        mode=mode,
    )


def render_history_detail(conn: sqlite3.Connection, attempt_id: int) -> None:
    """One stored attempt, rendered as Analyze renders a fresh one — minus the inputs."""
    st.button("← Back to the list", on_click=_close_history_attempt)

    with _DB_LOCK:
        row = db.get_attempt(conn, attempt_id)
    if row is None:
        st.warning("That attempt is no longer in the database.")
        return

    entry = attempt_from_row(row)
    if entry is None:
        st.error(
            "The stored Azure payload for this attempt could not be read, so there is "
            "nothing to render. The row itself is intact.",
            icon="🗄️",
        )
        return

    st.caption(
        f"Recorded {row['created_at']} · {entry.mode.value} · "
        f"{float(row['audio_seconds']):.1f}s"
        + (" · replayed from a fixture" if row["offline"] else "")
    )
    # The stored recording stands in for the live `audio_input`. None when it was not kept,
    # in which case `render_result` shows no playback and no per-word clips rather than an
    # error — a gitignored file can legitimately be gone.
    recording = stored_audio_bytes(conn, attempt_id)
    if recording is None:
        st.caption("The recording for this attempt was not kept, so there is nothing to play.")
    st.divider()
    render_result(conn, entry, recording)


def render_history(conn: sqlite3.Connection) -> None:
    """Everything recorded, paginated, newest first. Click a row to re-open it.

    `offline = 1` rows are INCLUDED and labelled. A fixture replay is a real row that a real
    click produced, and hiding it made History disagree with the database.
    """
    open_id = st.session_state.get(HISTORY_OPEN_KEY)
    if open_id:
        render_history_detail(conn, int(open_id))
        return

    with _DB_LOCK:
        st.caption(budget.summary_line(conn))

    choice = st.radio(
        "Show",
        ["All", "Scripted", "Unscripted"],
        horizontal=True,
        key=HISTORY_MODE_KEY,
    )
    mode_filter = {
        "Scripted": Mode.PARAGRAPH.value,
        "Unscripted": Mode.UNSCRIPTED.value,
    }.get(choice)

    with _DB_LOCK:
        total = db.attempt_count(conn, mode=mode_filter)

    if not total:
        st.info("Nothing recorded yet. Assess something on the Analyze tab.", icon="🗒️")
        return

    pages = max(1, -(-total // HISTORY_PAGE_SIZE))
    # Clamped rather than trusted: changing the filter can leave the stored page number past
    # the end of the shorter list, and an out-of-range OFFSET renders an empty page with no
    # explanation.
    page = min(int(st.session_state.get(HISTORY_PAGE_KEY, 1)), pages)
    st.session_state[HISTORY_PAGE_KEY] = page

    with _DB_LOCK:
        rows = db.attempt_page(
            conn,
            limit=HISTORY_PAGE_SIZE,
            offset=(page - 1) * HISTORY_PAGE_SIZE,
            mode=mode_filter,
        )

    st.caption(f"{total} attempt{'' if total == 1 else 's'} · page {page} of {pages}")

    for row in rows:
        render_history_row(conn, row)

    if pages > 1:
        render_pager(page, pages)


def render_history_row(conn: sqlite3.Connection, row: Any) -> None:
    """One line of the list: what it was, how it scored, and the two things you can do to it."""
    attempt_id = int(row["id"])
    mode = utils.mode_of(row["mode"])
    text = str(row["reference_text"] or row["recognised_text"] or "").strip()
    label = utils.truncate(text, 70) if text else "(no text recorded)"
    score = row["pron_score"]
    shown = f"{float(score):.0f}" if isinstance(score, (int, float)) else "—"

    with st.container(border=True):
        left, right = st.columns([5, 2])
        with left:
            st.markdown(f"**{html.escape(label)}**")
            flags = [row["created_at"], mode.value, f"{float(row['audio_seconds']):.0f}s"]
            if row["offline"]:
                flags.append("fixture replay")
            if str(row["mode"]) != mode.value:
                # A legacy `drill` row. Named rather than silently relabelled, so the list
                # never claims a row was recorded in a mode that did not exist for it.
                flags.append(f"recorded as {row['mode']}")
            st.caption(" · ".join(flags))
        with right:
            st.markdown(f"Pron **{shown}**")

        # Nothing but the buttons goes inside these columns: an alert laid out at a button's
        # width is unreadable, and a helper called within `with column:` appends into it.
        open_col, delete_col = st.columns(2)
        with open_col:
            st.button(
                "Open",
                key=f"open-{attempt_id}",
                on_click=_open_history_attempt,
                args=(attempt_id,),
                width="stretch",
            )
        with delete_col:
            confirm_key = f"confirm-delete-{attempt_id}"
            if st.session_state.get(confirm_key):
                st.button(
                    "Really delete",
                    key=f"do-delete-{attempt_id}",
                    type="primary",
                    on_click=_delete_attempt,
                    args=(conn, attempt_id),
                    width="stretch",
                )
            else:
                st.button(
                    "🗑️ Delete",
                    key=f"ask-delete-{attempt_id}",
                    on_click=lambda k=confirm_key: st.session_state.__setitem__(k, True),
                    width="stretch",
                )
    if st.session_state.get(f"confirm-delete-{attempt_id}"):
        # Outside the columns, deliberately: see the note above about alert width.
        st.warning(
            "This removes the attempt, its recording, its coaching and its annotation. "
            "It cannot be undone.",
            icon="⚠️",
        )


def _delete_attempt(conn: sqlite3.Connection, attempt_id: int) -> None:
    """Delete one attempt and unlink its recording. Never raises into the callback.

    The row goes first and the file second: a row without its audio renders fine, and an
    orphaned file is a wasted few megabytes. The reverse — a row pointing at audio that was
    deleted out from under it — is the failure worth avoiding.
    """
    with _DB_LOCK:
        path = db.delete_attempt(conn, attempt_id)
    if path:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove the recording at %s", path, exc_info=True)
    st.session_state.pop(f"confirm-delete-{attempt_id}", None)
    if st.session_state.get(HISTORY_OPEN_KEY) == attempt_id:
        st.session_state[HISTORY_OPEN_KEY] = None


def _set_history_page(page: int) -> None:
    st.session_state[HISTORY_PAGE_KEY] = page


def render_pager(page: int, pages: int) -> None:
    """Previous / next, with the position between them."""
    previous, position, following = st.columns([1, 2, 1])
    with previous:
        st.button(
            "← Newer",
            disabled=page <= 1,
            on_click=_set_history_page,
            args=(page - 1,),
            width="stretch",
        )
    with position:
        st.markdown(
            f'<div style="text-align:center;padding-top:0.4rem;">{page} / {pages}</div>',
            unsafe_allow_html=True,
        )
    with following:
        st.button(
            "Older →",
            disabled=page >= pages,
            on_click=_set_history_page,
            args=(page + 1,),
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

    # Two tabs, not two pages: `AppTest.from_file` addresses one script and the bare
    # `render()` below is the entry point, so `st.navigation`/`pages/` would cost more than
    # it buys. Note Streamlit executes BOTH tab bodies on every rerun, which is why History
    # and Analyze coordinate over `HISTORY_OPEN_KEY` about which of them draws a result:
    # `render_result` builds widget keys from the attempt, and two of them on one pass is a
    # hard duplicate-key error.
    analyze_tab, history_tab = st.tabs(["Analyze", "History"])
    with analyze_tab:
        render_analyze(conn, job, running)
    with history_tab:
        render_history(conn)


render()
