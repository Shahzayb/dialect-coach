"""The Mode C surface: what it offers, what it refuses to claim, and how it says so.

Everything here is a headless `AppTest` run against the committed synthetic Mode C fixture.
The point of most of it is negative — the panels that must NOT appear, and the blanks that
must be words instead.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

import app as app_module
import content_score
import db
import speech_analyzer as sa
import utils
import vowel_measure as vm
from utils import Mode

from conftest import ROOT

APP = str(ROOT / "src" / "app.py")
PROMPT = "Explain a technical decision you made recently."
UNSCRIPTED_LABEL = "Unscripted — speak freely on a prompt"


@pytest.fixture
def run_app(monkeypatch: pytest.MonkeyPatch):
    def _run(**env: str) -> AppTest:
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        app = AppTest.from_file(APP, default_timeout=30)
        app.run()
        return app

    return _run


def _assessment() -> Any:
    """A Mode C assessment, from the committed fixture, through the real parser."""
    return sa.analyse("/nonexistent.wav", "", Mode.UNSCRIPTED, topic=PROMPT)


def seed_unscripted(app: AppTest, *, attempt_id: int | None = 1) -> AppTest:
    key = utils.attempt_hash(PROMPT, b"audio", Mode.UNSCRIPTED)
    app.session_state["assessments"] = OrderedDict(
        {
            key: app_module.CachedAttempt(
                key=key,
                assessment=_assessment(),
                reference_text=PROMPT,
                attempt_id=attempt_id,
                mode=Mode.UNSCRIPTED,
            )
        }
    )
    app.session_state["last_key"] = key
    return app.run()


PARAGRAPH_REFERENCE = (
    "The weather this month has been rather unpredictable. Thursday brought thunder "
    "and thick clouds, while Wednesday stayed warm and clear."
)


def seed_paragraph(app: AppTest) -> AppTest:
    """The same seeding for a Mode B attempt, so the Mode C rules can be shown as rules.

    Kept local rather than imported from `test_app`: a cross-module test import gives one file
    two module names and mypy refuses the whole run over it.
    """
    key = utils.attempt_hash(PARAGRAPH_REFERENCE, b"audio", Mode.PARAGRAPH)
    app.session_state["assessments"] = OrderedDict(
        {
            key: app_module.CachedAttempt(
                key=key,
                assessment=sa.analyse("/nonexistent.wav", PARAGRAPH_REFERENCE, Mode.PARAGRAPH),
                reference_text=PARAGRAPH_REFERENCE,
                attempt_id=1,
                mode=Mode.PARAGRAPH,
            )
        }
    )
    app.session_state["last_key"] = key
    return app.run()


def _text(app: AppTest) -> str:
    """Everything rendered as prose, so a claim can be looked for wherever it landed."""
    groups = (app.markdown, app.caption, app.info, app.subheader, app.warning)
    return "\n".join(str(element.value) for group in groups for element in group)


# --- The mode exists and asks for a prompt ------------------------------------------------


def test_unscripted_is_offered_as_a_mode(run_app) -> None:
    app = run_app()
    assert UNSCRIPTED_LABEL in list(app.radio[0].options)


def test_choosing_it_asks_for_a_prompt_rather_than_a_reference_text(run_app) -> None:
    app = run_app()
    app.radio[0].set_value(UNSCRIPTED_LABEL).run()
    assert not app.exception
    assert "do not read" in app.text_area[0].label
    assert app.selectbox(key="preset_choice").label == "Prompt"


def test_the_prompts_are_prompts_and_not_passages(run_app) -> None:
    """Nothing in Mode C is read aloud, so these have to be subjects, not sentences to say."""
    app = run_app()
    app.radio[0].set_value(UNSCRIPTED_LABEL).run()
    options = list(app.selectbox(key="preset_choice").options)
    assert len(options) > 1
    for prompt in app_module.PRESETS[Mode.UNSCRIPTED].values():
        assert prompt.strip()


def test_the_guidance_states_azures_own_floors_and_the_double_send(run_app) -> None:
    """A speaker who does not know the audio is sent twice will think the meter is broken."""
    app = run_app(
        OFFLINE_MODE="false",
        AZURE_TIER_CONFIRMED_F0="true",
        AZURE_SPEECH_KEY="k",
        AZURE_SPEECH_REGION="r",
        UNSCRIPTED_TWO_PASS="true",
    )
    app.radio[0].set_value(UNSCRIPTED_LABEL).run()
    rendered = _text(app)
    assert str(content_score.MIN_WORDS) in rendered
    assert "twice" in rendered


def test_an_empty_prompt_is_refused_before_anything_is_sent() -> None:
    """The topic score is judged against it, and a calibration pair is matched by it."""
    assert app_module.validate_prompt("   ") is False
    assert app_module.validate_prompt("a real prompt") is True


# --- The transcript comes before anything derived from it ---------------------------------


def test_the_transcript_is_shown_and_named_as_the_reference(run_app) -> None:
    app = seed_unscripted(run_app())
    assert not app.exception
    rendered = _text(app)
    assert "What Azure heard" in rendered
    assert "wrongly blamed sound" in rendered or "weaker" in rendered


def test_the_prompt_is_shown_but_never_as_a_script(run_app) -> None:
    app = seed_unscripted(run_app())
    rendered = _text(app)
    assert PROMPT in rendered
    # The script-versus-heard diff would strike through every word of the prompt.
    assert "Script versus what Azure heard" not in rendered


# --- What Mode C refuses to claim ----------------------------------------------------------


def test_completeness_says_not_applicable_rather_than_showing_a_dash(run_app) -> None:
    """A dash reads as a failed measurement. There is nothing to be complete against."""
    app = seed_unscripted(run_app())
    rendered = _text(app)
    assert "not applicable" in rendered
    assert "no script" in rendered.lower() or "complete against" in rendered


def test_a_paragraph_attempt_still_shows_a_completeness_metric(run_app) -> None:
    """The contrast: the suppression is a Mode C rule, not a lost feature."""
    app = seed_paragraph(run_app())
    assert not app.exception
    assert any("Completeness" in str(m.label) for m in app.metric)


# --- Content scores ------------------------------------------------------------------------


def test_the_content_panel_says_why_it_is_unavailable_offline(run_app) -> None:
    """Never a blank: an empty panel teaches that the feature is broken."""
    app = seed_unscripted(run_app())
    rendered = _text(app)
    assert "Content score" in rendered
    assert "unavailable" in rendered.lower()


def test_the_content_panel_never_appears_for_a_scripted_mode(run_app) -> None:
    """There is no vocabulary or grammar of your own in a passage somebody else wrote."""
    assert "Content score" not in _text(seed_paragraph(run_app()))


def test_the_content_button_is_disabled_offline(run_app) -> None:
    app = seed_unscripted(run_app())
    buttons = [b for b in app.button if "Score the content" in b.label]
    assert buttons, "the button has to be visible so the reason beside it is readable"
    assert buttons[0].disabled


def test_a_stored_verdict_is_re_rendered_rather_than_re_asked(run_app, tmp_path) -> None:
    """'Unavailable, because 429' is a fact about the attempt and must survive a rerun."""
    conn = db.connect(str(tmp_path / "coach.db"))
    attempt_id = db.record_attempt(
        conn,
        mode=Mode.UNSCRIPTED,
        reference_text=PROMPT,
        recognised_text="x",
        audio_seconds=1.0,
        audio_sha256="h",
        overall_scores={},
        azure_raw={},
    )
    db.attach_content_score(
        conn,
        attempt_id,
        scores=content_score.Scores(72.0, 81.0, 90.0, "", content_score.SOURCE_GEMINI),
    )
    conn.close()

    app = seed_unscripted(run_app(DB_PATH=str(tmp_path / "coach.db")), attempt_id=attempt_id)
    rendered = _text(app)
    assert "Vocabulary score" in rendered
    # And it must not be mistaken for an Azure measurement.
    assert "not by Azure" in rendered or "retired" in rendered


# --- The accent surface refuses rather than borrowing --------------------------------------


def test_a_spontaneous_reading_is_never_normalised_against_a_read_baseline() -> None:
    """The scientific rule of this chunk, at the level it is enforced."""
    spontaneous = vm.Measurement(
        tokens=(), ceiling_hz=5000.0, snr_db_min=30.0, style=vm.STYLE_SPONTANEOUS
    )
    assert spontaneous.style != vm.STYLE_READ
    # `plot_gate`'s own refusal is covered in test_accent_gating; this asserts the message the
    # surface shows names both populations, so the reader knows what is missing.
    assert "spontaneous" in vm.STYLE_MISMATCH.format(
        measured=vm.STYLE_SPONTANEOUS, baseline=vm.STYLE_READ
    )


def test_the_calibration_page_offers_a_spontaneous_flow_too(run_app) -> None:
    app = run_app()
    rendered = _text(app)
    assert "Spontaneous speech" in rendered
    assert "same prompt" in rendered.lower() or "same PROMPT" in rendered
    # And says plainly that its floor is wider by construction.
    assert "wider" in rendered
