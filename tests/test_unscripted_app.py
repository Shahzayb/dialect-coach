"""The unscripted surface: what it offers, what it refuses to claim, and how it says so.

Everything here is a headless `AppTest` run against the committed unscripted fixture. The
point of most of it is negative — the panels that must NOT appear, and the blanks that must
be words instead.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

import app as app_module
import speech_analyzer as sa
import utils
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
    """An unscripted assessment, from the committed fixture, through the real parser."""
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
    """The same seeding for a scripted attempt, so the unscripted rules read as rules.

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
    """Nothing unscripted is read aloud, so these are subjects, not sentences to say."""
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
    assert str(app_module.UNSCRIPTED_MIN_WORDS) in rendered
    assert "twice" in rendered


def test_an_empty_prompt_is_refused_before_anything_is_sent() -> None:
    """It is the only thing that makes an unscripted row readable in History."""
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


def test_a_scripted_attempt_still_shows_a_completeness_metric(run_app) -> None:
    """The contrast: the suppression is an unscripted rule, not a lost feature."""
    app = seed_paragraph(run_app())
    assert not app.exception
    assert any("Completeness" in str(m.label) for m in app.metric)


# --- What went with the two-page cut ---------------------------------------------------------
# The content-score panel (Gemini's vocabulary/grammar/topic read of the transcript) and the
# spontaneous-baseline refusal both had tests here. Both features were deleted on 2026-08-25 —
# Gemini now only annotates prosody, and there is no accent measurement to refuse a baseline
# for — so the tests went with them rather than being retargeted at something else.


def test_the_content_panel_is_gone_rather_than_empty(run_app) -> None:
    """A retired panel must leave no stub behind that reads as a broken feature."""
    rendered = _text(seed_unscripted(run_app()))
    assert "Content score" not in rendered
    assert "Vocabulary score" not in rendered
