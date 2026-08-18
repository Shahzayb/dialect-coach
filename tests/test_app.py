"""The UI itself, run headlessly with Streamlit's AppTest.

Covers the paths a browser cannot easily reach from a test: the startup refusals, the
reference-text validation, and — by seeding the session cache — the result rendering,
including the "—" for an unavailable prosody score.
"""

from __future__ import annotations

import os

import pytest
from streamlit.testing.v1 import AppTest

import app as app_module
import db
import fallback_coach
import speech_analyzer as sa
import utils
from tests.conftest import ROOT
from utils import Mode

APP = str(ROOT / "app.py")
REFERENCE = (
    "The weather this month has been rather unpredictable. Thursday brought thunder "
    "and thick clouds, while Wednesday stayed warm and clear."
)


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A throwaway database per test, and no connection carried over from the last one.

    get_connection is @st.cache_resource, which AppTest shares across runs — without the
    clear, every test after the first would keep writing to the first test's database.
    """
    monkeypatch.setenv("DB_PATH", str(tmp_path / "coach.db"))
    import streamlit as st

    st.cache_resource.clear()


@pytest.fixture
def run_app(monkeypatch: pytest.MonkeyPatch):
    """Run app.py, overriding settings through monkeypatch so nothing leaks between tests."""

    def _run(**env) -> AppTest:
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        app = AppTest.from_file(APP, default_timeout=30)
        app.run()
        return app

    return _run


def test_the_page_renders_offline(run_app) -> None:
    app = run_app()
    assert not app.exception
    assert "Pronunciation Coach" in app.title[0].value
    assert any("OFFLINE_MODE is on" in i.value for i in app.info)


def test_offline_page_renders_without_the_f0_acknowledgement(run_app) -> None:
    """The zero-cost path must not be gated behind a tier confirmation."""
    app = run_app(MONTHLY_BUDGET_USD="0.00", AZURE_TIER_CONFIRMED_F0="false")
    assert not app.exception
    assert not app.error


def test_online_without_the_f0_acknowledgement_is_refused(run_app) -> None:
    app = run_app(
        OFFLINE_MODE="false", MONTHLY_BUDGET_USD="0.00",
        AZURE_TIER_CONFIRMED_F0="false",
    )
    assert app.error, "the app must refuse to start rather than risk an S0 resource"
    assert "F0" in app.error[0].value
    assert "OFFLINE_MODE=true" in app.error[0].value, "the message must offer a way forward"


def test_missing_credentials_are_reported_by_name(run_app) -> None:
    app = run_app(
        OFFLINE_MODE="false", MONTHLY_BUDGET_USD="0.00",
        AZURE_TIER_CONFIRMED_F0="true",
    )
    assert app.error
    assert "AZURE_SPEECH_KEY" in app.error[0].value


def test_the_usage_line_is_always_shown(run_app) -> None:
    app = run_app()
    assert any("Azure portal is authoritative" in c.value for c in app.caption)


def test_presets_change_with_the_mode(run_app) -> None:
    app = run_app()
    drill_options = list(app.selectbox[0].options)
    app.radio[0].set_value("Paragraph — connected speech").run()
    assert list(app.selectbox[0].options) != drill_options


def test_choosing_a_preset_fills_the_reference_text(run_app) -> None:
    app = run_app()
    preset = app.selectbox[0].options[1]
    app.selectbox[0].set_value(preset).run()
    assert app.text_area[0].value


def test_presets_contain_no_digits() -> None:
    """Azure normalises '33' and 'thirty-three' differently, breaking word alignment."""
    import app as app_module

    for presets in app_module.PRESETS.values():
        for name, text in presets.items():
            assert not any(c.isdigit() for c in text), f"{name} contains a digit"


# --- Result rendering ---------------------------------------------------------------------


def seed_result(app: AppTest, assessment, *, attempt_id: int | None = None,
                mode: Mode = Mode.DRILL) -> AppTest:
    """Put an assessment in the session cache the way a successful run would.

    The reference text, the row id and the mode travel with it because the widgets that
    produced them can all be changed without re-running anything — the panel has to render
    the text the scores were computed against, not whatever is in the textarea now.
    """
    from collections import OrderedDict

    key = utils.attempt_hash(REFERENCE, b"audio", mode)
    app.session_state["assessments"] = OrderedDict({
        key: app_module.CachedAttempt(
            key=key, assessment=assessment, reference_text=REFERENCE,
            attempt_id=attempt_id, mode=mode,
        )
    })
    app.session_state["last_key"] = key
    return app.run()


def offline_assessment(mode: Mode = Mode.DRILL):
    return sa.analyse("/nonexistent.wav", REFERENCE, mode)


def test_a_result_renders_the_full_metric_row(run_app) -> None:
    app = seed_result(run_app(), offline_assessment())
    assert not app.exception
    labels = [m.label for m in app.metric]
    assert labels == ["Pronunciation", "Accuracy", "Fluency", "Completeness", "Prosody"]
    assert all(m.value != "—" for m in app.metric), "the fixture has every score populated"


def test_unavailable_prosody_renders_as_a_dash_not_zero(run_app) -> None:
    assessment = offline_assessment()
    assessment.overall_scores["prosody"] = None
    app = seed_result(run_app(), assessment)
    prosody = next(m for m in app.metric if m.label == "Prosody")
    assert prosody.value == "—", "a missing score and a score of zero are different things"


def test_the_result_diffs_the_script_against_what_azure_heard(run_app) -> None:
    """If Azure heard something else, that outranks every per-phoneme score on the page."""
    app = seed_result(run_app(), offline_assessment())
    assert any("what Azure heard" in s.value for s in app.subheader)


def test_flagged_words_get_a_card_each(run_app) -> None:
    app = seed_result(run_app(), offline_assessment())
    assert any("Flagged words" in s.value for s in app.subheader)
    rendered = " ".join(m.value for m in app.markdown)
    assert "→" in rendered, "a card must name what was produced, not only what was expected"


def test_the_colour_coded_text_and_delivery_panel_render(run_app) -> None:
    import app as app_module
    from utils import Band

    app = seed_result(run_app(), offline_assessment())
    subheaders = [s.value for s in app.subheader]
    assert "Word by word" in subheaders
    assert "Delivery" in subheaders
    rendered = " ".join(m.value for m in app.markdown)
    assert app_module.BAND_COLOURS[Band.RED] in rendered, "words must be banded by score"


# --- Playback ------------------------------------------------------------------------------


def test_hear_it_buttons_are_disabled_offline_and_say_why(run_app) -> None:
    """OFFLINE_MODE means no network call, ever — there is no audio fixture to replay."""
    app = seed_result(run_app(), offline_assessment())

    playback = [b for b in app.button if "Hear it" in b.label or "Slowly" in b.label]
    assert playback, "the playback buttons must still render, just disabled"
    assert all(b.disabled for b in playback)
    assert any("OFFLINE_MODE is on" in c.value for c in app.caption)


def test_nothing_is_charged_to_the_tts_meter_while_offline(run_app) -> None:
    import db

    app = seed_result(run_app(), offline_assessment())
    assert not app.exception
    conn = db.connect(os.environ["DB_PATH"])
    assert db.monthly_tts_characters(conn) == 0
    conn.close()


def test_a_cached_phrase_is_not_charged_to_the_meter_twice(tmp_path, monkeypatch) -> None:
    """Streamlit re-runs the whole script on every click.

    Metering ahead of the cache check would charge again on each unrelated interaction, so
    the meter would climb while nothing new was synthesised. The order in `play` is the fix
    and this is the test that pins it.
    """
    from collections import OrderedDict

    import app as app_module
    import db
    import tts

    conn = db.connect(tmp_path / "tts.db")
    monkeypatch.setenv("OFFLINE_MODE", "false")
    monkeypatch.setenv("AZURE_TIER_CONFIRMED_F0", "true")
    monkeypatch.setattr(
        tts, "synthesise",
        lambda text, **kw: tts.Synthesis(audio=b"WAV", characters=len(text),
                                         voice="en-US-BrianNeural", attempts=1),
    )

    cache: OrderedDict = OrderedDict()
    state: dict = {}
    monkeypatch.setattr(app_module, "_session_cache", lambda _name: cache)
    monkeypatch.setattr(app_module.st, "session_state", state, raising=False)

    for _ in range(4):
        app_module.play(conn, "weather", slow=False, label="test", source="word-0")

    assert db.monthly_tts_characters(conn) == len("weather"), "charged once, not four times"
    assert state["now_playing"] == {"key": ("en-US-BrianNeural", "weather", False),
                                    "source": "word-0"}
    conn.close()


def test_paragraph_results_render_too(run_app) -> None:
    app = seed_result(run_app(), offline_assessment(Mode.PARAGRAPH))
    assert not app.exception
    assert app.metric


def test_the_result_panel_shows_the_text_it_was_scored_against(run_app) -> None:
    """Editing the textarea after assessing must not relabel an existing result."""
    app = seed_result(run_app(), offline_assessment())
    app.text_area[0].set_value("Something else entirely.").run()
    assert not app.exception
    rendered = " ".join(m.value for m in app.markdown)
    assert "Something else entirely." not in rendered


def test_the_cache_evicts_least_recently_used() -> None:
    """The drill loop re-uses one entry; insertion-order eviction would drop that one."""
    from collections import OrderedDict

    import app as app_module

    cache: OrderedDict = OrderedDict()
    for i in range(app_module.CACHE_LIMIT):
        app_module.lru_put(cache, f"key{i}", None, app_module.CACHE_LIMIT)

    app_module.lru_get(cache, "key0")          # re-used, so it must survive
    app_module.lru_put(cache, "overflow", None, app_module.CACHE_LIMIT)

    assert len(cache) == app_module.CACHE_LIMIT
    assert "key0" in cache
    assert "key1" not in cache, "the genuinely oldest entry is the one to drop"


def test_the_cache_returns_none_for_a_miss() -> None:
    from collections import OrderedDict

    import app as app_module

    assert app_module.lru_get(OrderedDict(), "nope") is None


def test_the_cache_round_trips_any_value() -> None:
    """Generalised because the same LRU now backs both assessments and synthesised audio."""
    from collections import OrderedDict

    import app as app_module

    cache: OrderedDict = OrderedDict()
    app_module.lru_put(cache, "k", ("assessment", "the text it was scored against"), 10)
    assert app_module.lru_get(cache, "k") == ("assessment", "the text it was scored against")

    audio: OrderedDict = OrderedDict()
    app_module.lru_put(audio, ("voice", "hello", False), b"WAV", 2)
    assert app_module.lru_get(audio, ("voice", "hello", False)) == b"WAV"


def test_a_retried_assessment_charges_the_meter_for_every_attempt(tmp_path) -> None:
    """Three attempts upload the audio three times; recording it once under-reports."""
    import db
    import speech_analyzer as sa
    import utils
    from utils import Mode

    conn = db.connect(tmp_path / "meter.db")
    assessment = sa.analyse("/nonexistent.wav", REFERENCE, Mode.DRILL)
    assessment.attempts = 3

    db.record_attempt(
        conn, mode=Mode.DRILL, reference_text=REFERENCE,
        recognised_text=assessment.recognised_text,
        audio_seconds=12.0 * max(assessment.attempts, 1),
        audio_sha256=utils.sha256_bytes(b"x"),
        overall_scores=assessment.overall_scores, azure_raw=assessment.raw[0],
        offline=False,
    )
    assert db.monthly_stt_seconds(conn) == 36.0
    conn.close()


def test_a_failed_synthesis_is_returned_not_rendered_in_a_narrow_column(
    tmp_path, monkeypatch
) -> None:
    """`play` must hand the message back, not emit it inside an `st.columns` entry.

    Measured against the running app: an alert emitted from inside the button column laid
    out at 124px in a 672px row — a couple of hundred characters of error at one word per
    line. The caller renders it after the columns close.
    """
    from collections import OrderedDict

    import app as app_module
    import db
    import tts

    conn = db.connect(tmp_path / "fail.db")
    monkeypatch.setenv("OFFLINE_MODE", "false")
    monkeypatch.setenv("AZURE_TIER_CONFIRMED_F0", "true")
    monkeypatch.setattr(app_module, "_session_cache", lambda _n: OrderedDict())
    monkeypatch.setattr(app_module.st, "session_state", {}, raising=False)

    def boom(text, **kw):
        raise tts.SynthesisError("Azure rejected the request as malformed.")

    monkeypatch.setattr(tts, "synthesise", boom)
    outcome = app_module.play(conn, "weather", slow=False, label="x", source="word-0")

    assert outcome is not None, "the failure must come back to the caller"
    icon, message = outcome
    assert "malformed" in message
    conn.close()


def test_a_run_that_fails_after_retries_still_charges_the_meter(tmp_path, monkeypatch) -> None:
    """Three uploads reached Azure and may have been billed; recording zero under-reports."""
    from collections import OrderedDict

    import app as app_module
    import db
    import tts
    import utils

    conn = db.connect(tmp_path / "retry.db")
    monkeypatch.setenv("OFFLINE_MODE", "false")
    monkeypatch.setenv("AZURE_TIER_CONFIRMED_F0", "true")
    monkeypatch.setattr(app_module, "_session_cache", lambda _n: OrderedDict())
    monkeypatch.setattr(app_module.st, "session_state", {}, raising=False)

    def exhaust(text, *, slow=False, on_attempt=None, **kw):
        for attempt in (1, 2, 3):
            on_attempt(attempt)
        raise utils.TransientError("Azure was temporarily unavailable")

    monkeypatch.setattr(tts, "synthesise", exhaust)
    assert app_module.play(conn, "weather", slow=False, label="x", source="w") is not None
    assert db.monthly_tts_characters(conn) == len("weather") * 3
    conn.close()


def test_omitted_words_still_offer_playback(run_app) -> None:
    """A word you skipped is the one you most need to hear a native rendering of."""
    assessment = offline_assessment()
    assessment.words = [dict(assessment.words[0], word="unpredictable", accuracy=None,
                             error_type="Omission", error_source="local_diff",
                             phonemes=[], syllables=[])]
    app = seed_result(run_app(), assessment)
    assert not app.exception
    assert any("Hear it" in b.label for b in app.button)
    assert any("did not say this one" in c.value for c in app.caption)


# --- Coaching ---------------------------------------------------------------------------------


def test_the_coaching_report_renders_without_a_key(run_app) -> None:
    """The exit criterion: no GEMINI_API_KEY, and the app still says what to work on."""
    app = seed_result(run_app(), offline_assessment())
    assert not app.exception
    headings = [h.value for h in app.subheader]
    assert "What to work on" in headings
    body = " ".join(m.value for m in app.markdown)
    assert "/θ/" in body and "/s/" in body, "the top fix names the substitution"
    assert "Drill these pairs" in body
    assert "Practice plan" in body


def test_a_visible_note_says_which_coach_wrote_it(run_app) -> None:
    app = seed_result(run_app(), offline_assessment())
    captions = " ".join(c.value for c in app.caption)
    assert "offline coach" in captions
    assert "nothing sent anywhere" in captions.lower()


def test_the_gemini_button_is_disabled_offline_and_says_why(run_app) -> None:
    app = seed_result(run_app(), offline_assessment())
    button = next(b for b in app.button if "Gemini" in b.label)
    assert button.disabled
    assert any("OFFLINE_MODE" in c.value for c in app.caption)


def test_the_button_says_what_a_click_sends(run_app, monkeypatch) -> None:
    """Free-tier prompts may be used to improve Google's products — that is a choice."""
    # Replay the fixture *before* going online — with OFFLINE_MODE off, analyse() would
    # try to open a recording and call Azure for real.
    assessment = offline_assessment()
    monkeypatch.setenv("OFFLINE_MODE", "false")
    monkeypatch.setenv("GEMINI_API_KEY", "placeholder-not-a-real-key")
    monkeypatch.setenv("AZURE_SPEECH_KEY", "placeholder")
    monkeypatch.setenv("AZURE_SPEECH_REGION", "eastus")
    monkeypatch.setenv("AZURE_TIER_CONFIRMED_F0", "true")
    app = seed_result(run_app(), assessment)
    captions = " ".join(c.value for c in app.caption)
    assert "never your audio" in captions.lower()
    assert "Google" in captions


def test_the_report_is_attached_to_the_attempt_row_it_describes(run_app, tmp_path) -> None:
    """Stored verbatim, so changing what this panel shows later is a re-parse."""
    conn = db.connect(str(tmp_path / "coach.db"))
    attempt_id = db.record_attempt(
        conn, mode=Mode.DRILL, reference_text=REFERENCE, recognised_text="x",
        audio_seconds=1.0, audio_sha256="deadbeef", overall_scores={}, azure_raw={},
        offline=True,
    )
    seed_result(run_app(), offline_assessment(), attempt_id=attempt_id)

    row = db.get_attempt(conn, attempt_id)
    assert row["coach_source"] == fallback_coach.SOURCE_FALLBACK
    assert row["gemini_raw_json"], "the report itself is stored on the offline path"


def test_the_report_is_not_rebuilt_on_every_rerun(run_app) -> None:
    """Streamlit re-runs the whole script on every click; a model call here would re-spend."""
    app = seed_result(run_app(), offline_assessment())
    cached = app.session_state["coaching"]
    assert len(cached) == 1
    app.run()
    assert app.session_state["coaching"] is cached


def test_clicking_the_button_swaps_the_models_report_in(run_app, monkeypatch, tmp_path) -> None:
    """The click path, without a live call: app.py and this test share one ai_coach module."""
    import ai_coach

    assessment = offline_assessment()
    conn = db.connect(str(tmp_path / "coach.db"))
    attempt_id = db.record_attempt(
        conn, mode=Mode.DRILL, reference_text=REFERENCE, recognised_text="x",
        audio_seconds=1.0, audio_sha256="deadbeef", overall_scores={}, azure_raw={},
        offline=True,
    )

    improved = fallback_coach.build(assessment, Mode.DRILL).model_copy(
        update={"overall_comment": "A second opinion from the model."}
    )
    monkeypatch.setattr(ai_coach, "coach", lambda *a, **k: ai_coach.CoachingResult(
        report=improved, source=fallback_coach.SOURCE_GEMINI,
        raw={"candidates": [{"content": {"parts": [{"text": improved.model_dump_json()}]}}]},
    ))
    monkeypatch.setenv("OFFLINE_MODE", "false")
    monkeypatch.setenv("GEMINI_API_KEY", "placeholder-not-a-real-key")
    monkeypatch.setenv("AZURE_SPEECH_KEY", "placeholder")
    monkeypatch.setenv("AZURE_SPEECH_REGION", "eastus")
    monkeypatch.setenv("AZURE_TIER_CONFIRMED_F0", "true")

    app = seed_result(run_app(), assessment, attempt_id=attempt_id)
    button = next(b for b in app.button if "Gemini" in b.label)
    assert not button.disabled
    app = button.click().run()

    assert not app.exception
    assert any("second opinion from the model" in m.value for m in app.markdown)
    assert any(ai_coach.model_name() in c.value for c in app.caption)
    assert db.get_attempt(conn, attempt_id)["coach_source"] == fallback_coach.SOURCE_GEMINI


def test_a_second_click_cannot_spend_another_call(run_app, monkeypatch, tmp_path) -> None:
    """Once the model has answered for this attempt, the button is spent."""
    import ai_coach

    assessment = offline_assessment()
    report = fallback_coach.build(assessment, Mode.DRILL)
    calls: list[int] = []

    def once(*args, **kwargs):
        calls.append(1)
        return ai_coach.CoachingResult(
            report=report, source=fallback_coach.SOURCE_GEMINI, raw=report.model_dump()
        )

    monkeypatch.setattr(ai_coach, "coach", once)
    monkeypatch.setenv("OFFLINE_MODE", "false")
    monkeypatch.setenv("GEMINI_API_KEY", "placeholder-not-a-real-key")
    monkeypatch.setenv("AZURE_SPEECH_KEY", "placeholder")
    monkeypatch.setenv("AZURE_SPEECH_REGION", "eastus")
    monkeypatch.setenv("AZURE_TIER_CONFIRMED_F0", "true")

    app = seed_result(run_app(), assessment)
    app = next(b for b in app.button if "Gemini" in b.label).click().run()
    assert len(calls) == 1

    # The click is handled in the pass that rendered the button, so the button on screen is
    # still enabled: clicking it again must cost nothing rather than buying a second call.
    app = next(b for b in app.button if "Gemini" in b.label).click().run()
    assert len(calls) == 1, "a second click must not re-spend"
    assert next(b for b in app.button if "Gemini" in b.label).disabled
    app.run()
    assert len(calls) == 1, "and neither must an unrelated rerun"
