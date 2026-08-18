"""Streamlit entry point for the pronunciation coach.

UI only. Every API call lives in `speech_analyzer`, every write in `db`, every spend
decision in `budget` — this file orchestrates them and renders the result.

Minimal on purpose: recognised-vs-reference text, the metric row, and a plain word table.
Colour-coded reference text, the reference/heard diff, the delivery panel, "Hear it"
playback, and the coaching report are each their own chunk of work.
"""

from __future__ import annotations

import logging
import sqlite3

import streamlit as st

import audio_utils
import budget
import db
import speech_analyzer
import utils
from utils import Mode

logger = logging.getLogger(__name__)

PAGE_TITLE = "Pronunciation Coach"
PAGE_ICON = "🗣️"

# Cap on the session cache. Streamlit re-runs the entire script on every widget
# interaction, so without this a single click on any control would re-run the whole Azure
# pipeline on audio that was already assessed. Cross-session deduplication is a side
# benefit; preventing rerun storms is the actual requirement.
CACHE_LIMIT = 10

MODE_LABELS: dict[str, Mode] = {
    "Drill — one or two sentences": Mode.DRILL,
    "Paragraph — connected speech": Mode.PARAGRAPH,
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


def _cache_get(key: str):
    return st.session_state.get("assessments", {}).get(key)


def _cache_put(key: str, value) -> None:
    cache: dict = st.session_state.setdefault("assessments", {})
    cache[key] = value
    while len(cache) > CACHE_LIMIT:
        cache.pop(next(iter(cache)))


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
        audio_seconds=seconds,
        audio_sha256=utils.sha256_bytes(wav_bytes),
        overall_scores=assessment.overall_scores,
        azure_raw=assessment.raw if len(assessment.raw) > 1 else assessment.raw[0],
        offline=assessment.offline,
    )
    return assessment


def render_result(assessment, reference_text: str) -> None:
    scores = assessment.overall_scores

    columns = st.columns(5)
    for column, (label, key) in zip(
        columns,
        [("Pronunciation", "pron_score"), ("Accuracy", "accuracy"), ("Fluency", "fluency"),
         ("Completeness", "completeness"), ("Prosody", "prosody")],
    ):
        with column:
            _metric(label, scores.get(key))

    # What Azure heard is often the single most useful signal on the page — if it heard
    # something else entirely, no per-phoneme score matters yet.
    st.subheader("What Azure heard")
    st.write(assessment.recognised_text or "_nothing_")
    with st.expander("What you meant to say"):
        st.write(reference_text)

    flagged = [
        w for w in assessment.words
        if w["error_type"] != "None"
        or (w["accuracy"] is not None and w["accuracy"] < utils.WORD_AMBER)
        or w["delivery_error_types"]
    ]

    st.subheader(f"Words ({len(flagged)} of {len(assessment.words)} flagged)")
    if not flagged:
        st.success("Nothing flagged in that attempt.")
    else:
        st.dataframe(
            [
                {
                    "Word": w["word"],
                    "Score": round(w["accuracy"], 1) if w["accuracy"] is not None else None,
                    "Error": w["error_type"],
                    # Says whose judgement it is: continuous mode ignores enableMiscue, so
                    # omissions and insertions there are our diff, not Azure's.
                    "Flagged by": w["error_source"],
                    "Delivery": ", ".join(w["delivery_error_types"]) or "",
                    "Weakest sound": _weakest_phoneme(w),
                }
                for w in flagged
            ],
            hide_index=True,
            use_container_width=True,
        )

    st.caption(
        "Expected → produced sounds, colour-coded text, the delivery panel, playback, and "
        "the coaching report are still to come."
    )


def _weakest_phoneme(word: dict) -> str:
    """Lowest-scoring phoneme, and what was produced instead. The diagnostic bit."""
    scored = [p for p in word["phonemes"] if p["score"] is not None]
    if not scored:
        return ""
    worst = min(scored, key=lambda p: p["score"])
    alternates = [a for a in worst["nbest"] if a["phoneme"] != worst["phoneme"]]
    if alternates and worst["is_mispronounced"]:
        return f"/{worst['phoneme']}/ → sounded like /{alternates[0]['phoneme']}/"
    return f"/{worst['phoneme']}/ ({worst['score']:.0f})"


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
            assessment = _cache_get(key)
            if assessment is None:
                assessment = run_assessment(conn, audio_bytes, reference_text, mode)
                if assessment is not None:
                    _cache_put(key, assessment)
            st.session_state["last_key"] = key if assessment is not None else None

    last_key = st.session_state.get("last_key")
    if last_key:
        cached = _cache_get(last_key)
        if cached is not None:
            st.divider()
            render_result(cached, reference_text)
            if source is not None:
                # Your own recording, directly under the scores. Comparing it against a
                # native rendering is the "Hear it" chunk; this is half of that.
                st.audio(source)

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
