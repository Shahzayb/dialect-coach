"""Streamlit entry point for the pronunciation coach.

UI only. Every API call lives in `speech_analyzer` and `tts`, every write in `db`, every
spend decision in `budget` — this file orchestrates them and renders the result.

The rendering aims at one thing: making the diagnosis legible and audible. Colour-coded
reference text, the reference-vs-heard diff, expected → produced IPA per flagged word, the
delivery panel, and "Hear it" playback against your own recording. The coaching report is
its own chunk of work and is not here.
"""

from __future__ import annotations

import difflib
import html
import logging
import sqlite3
from collections import OrderedDict
from typing import Any

import streamlit as st

import audio_utils
import budget
import db
import speech_analyzer
import tts
import utils
from utils import Band, Mode

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

# What Azure's prosody feedback means in words. The raw names are accurate but say nothing
# to someone trying to fix their delivery.
DELIVERY_LABELS: dict[str, str] = {
    "UnexpectedBreak": "Paused in the middle of a phrase",
    "MissingBreak": "Ran two phrases together with no pause",
    "Monotone": "Flat intonation across the span",
}

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


def _cache_get(key: str) -> tuple | None:
    return lru_get(_session_cache("assessments"), key)


def _cache_put(key: str, assessment: Any, reference_text: str) -> None:
    """The reference text is stored alongside the result — rendering the live textarea
    would otherwise show freshly edited text beside scores from the previous version."""
    lru_put(_session_cache("assessments"), key, (assessment, reference_text), CACHE_LIMIT)


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


def hover_text(word: dict[str, Any]) -> str:
    """The title attribute for a word: its score, and why it was flagged."""
    accuracy = word.get("accuracy")
    parts = [f"{accuracy:.0f}" if isinstance(accuracy, (int, float)) else "not spoken"]
    error_type = word.get("error_type") or "None"
    if error_type != "None":
        # Says whose judgement it is: continuous mode ignores enableMiscue, so omissions
        # and insertions there are our diff, not Azure's.
        parts.append(f"{error_type} (flagged by {word.get('error_source') or 'azure'})")
    parts.extend(word.get("delivery_error_types") or [])
    return " · ".join(parts)


def colour_coded_html(words: list[dict[str, Any]]) -> str:
    """The assessed words as one colour-coded block, each carrying its score on hover.

    Built from the aligned word list rather than the raw reference string, because that is
    what carries the scores — so the original punctuation and capitalisation are not
    reproduced here. The verbatim reference stays visible in the diff panel above it.

    HTML rather than Streamlit's native `:red[…]` markdown because only an attribute can
    carry hover text, and §11 asks for the score on hover. Both the word and the title are
    escaped: they originate in the reference textarea, which is arbitrary user input being
    interpolated into markup.
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
            f'<span style="{style}" title="{html.escape(hover_text(word), quote=True)}">'
            f"{html.escape(text)}</span>"
        )
    return '<div style="line-height:2.4;">' + " ".join(spans) + "</div>"


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
                     use_container_width=True):
            failure = play(conn, text, slow=False, label=label, source=key_prefix)
    with right:
        if st.button("🐢 Slowly", key=f"{key_prefix}-slow", disabled=offline,
                     use_container_width=True):
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


def run_assessment(conn: sqlite3.Connection, audio: bytes, reference_text: str, mode: Mode):
    """Convert, guard, assess, and store. Returns the Assessment, or None on a handled error."""
    try:
        wav_bytes, seconds = audio_utils.prepare(audio, mode)
    except audio_utils.AudioError as exc:
        st.error(str(exc), icon="🎙️")
        return None

    try:
        budget.preflight_stt(conn, seconds, mode)
    except budget.BudgetError as exc:
        st.error(str(exc), icon="💸")
        return None

    try:
        with st.spinner(f"Assessing {seconds:.0f}s of audio…"):
            with audio_utils.temp_wav(wav_bytes) as wav_path:
                assessment = speech_analyzer.analyse(wav_path, reference_text, mode)
    except speech_analyzer.NoSpeechDetected as exc:
        st.warning(str(exc), icon="🤫")
        return None
    except (utils.PermanentError, utils.TransientError, speech_analyzer.AssessmentError) as exc:
        if speech_analyzer.is_quota_exhausted(exc):
            # Azure is authoritative; block the rest of the month regardless of the meter.
            budget.mark_quota_exhausted()
        # redact() rather than str(): SDK error details can echo request context.
        st.error(utils.redact(str(exc)), icon="🚫")
        logger.error("Assessment failed", exc_info=True)
        return None
    except utils.ConfigError as exc:
        st.error(str(exc), icon="🔑")
        return None

    db.record_attempt(
        conn,
        mode=mode,
        reference_text=reference_text,
        recognised_text=assessment.recognised_text,
        # Attempts, not successes: a retry re-uploads the same audio. Offline replays
        # report zero attempts and are excluded from the meter anyway.
        audio_seconds=seconds * max(assessment.attempts, 1),
        audio_sha256=utils.sha256_bytes(wav_bytes),
        overall_scores=assessment.overall_scores,
        azure_raw=assessment.raw if len(assessment.raw) > 1 else assessment.raw[0],
        offline=assessment.offline,
    )
    return assessment


# --- Result rendering --------------------------------------------------------------------------


def render_scores(assessment) -> None:
    scores = assessment.overall_scores
    columns = st.columns(5)
    for column, (label, key) in zip(
        columns,
        [("Pronunciation", "pron_score"), ("Accuracy", "accuracy"), ("Fluency", "fluency"),
         ("Completeness", "completeness"), ("Prosody", "prosody")],
    ):
        with column:
            _metric(label, scores.get(key))


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
        f"Hover any word for its score. Red below {utils.WORD_RED:g}, amber below "
        f"{utils.WORD_AMBER:g}, green above. Struck through: never spoken. Italic: heard "
        f"but not in the script. These cut points are heuristics chosen for this tool — "
        f"Azure returns a 0-100 score and says nothing about where \"bad\" starts."
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
    """Counts and locations of UnexpectedBreak / MissingBreak / Monotone."""
    st.subheader("Delivery")
    summary = speech_analyzer.delivery_summary(assessment.words)
    if not summary:
        st.success("No pausing or intonation problems flagged in that attempt.")
        return
    for fault, words in summary.items():
        st.markdown(
            f"**{DELIVERY_LABELS.get(fault, fault)}** — {len(words)} "
            f"{'word' if len(words) == 1 else 'words'}: {', '.join(words)}"
        )


def render_result(conn: sqlite3.Connection, assessment, reference_text: str, source) -> None:
    render_scores(assessment)
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
    st.subheader(f"Flagged words ({len(flagged)} of {len(assessment.words)})")
    if not flagged:
        st.success("Nothing flagged in that attempt.")
    else:
        st.caption(
            "Worst first. Each word is synthesised on its own, so you hear it in citation "
            "form — right for drilling a sound, but not how it sounds inside the sentence. "
            "Use the whole-text playback above for that."
        )
        for index, word in enumerate(flagged):
            render_word_card(conn, word, index)

    render_delivery(assessment)

    st.caption("The coaching report is still to come.")


def render() -> None:
    st.set_page_config(page_title=PAGE_TITLE, page_icon=PAGE_ICON, layout="centered")
    st.title(f"{PAGE_ICON} {PAGE_TITLE}")
    st.caption("Personal English pronunciation and delivery coach — en-US.")

    check_startup()
    conn = get_connection()

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
    choice = st.selectbox("Practice text", ["Write my own", *presets])
    default_text = presets.get(choice, "")
    reference_text = st.text_area("Reference text", value=default_text, height=140)

    audio = st.audio_input("Record")
    uploaded = st.file_uploader(
        "…or upload a file", type=list(audio_utils.SUPPORTED_UPLOAD_TYPES)
    )
    source = audio or uploaded

    if st.button("Assess", type="primary", disabled=source is None):
        if validate_reference(reference_text):
            audio_bytes = source.getvalue()
            key = utils.attempt_hash(reference_text, audio_bytes)
            cached = _cache_get(key)
            if cached is None:
                assessment = run_assessment(conn, audio_bytes, reference_text, mode)
                if assessment is not None:
                    _cache_put(key, assessment, reference_text)
            else:
                assessment = cached[0]
            st.session_state["last_key"] = key if assessment is not None else None
            # A fresh result must not open with the previous attempt's word still queued.
            st.session_state["now_playing"] = None

    last_key = st.session_state.get("last_key")
    if last_key:
        cached = _cache_get(last_key)
        if cached is not None:
            assessment, assessed_text = cached
            st.divider()
            render_result(conn, assessment, assessed_text, source)

    st.divider()
    st.caption(budget.summary_line(conn))
    recent = db.recent_attempts(conn, limit=5)
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
                hide_index=True, use_container_width=True,
            )


render()
