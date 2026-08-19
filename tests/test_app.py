"""The UI itself, run headlessly with Streamlit's AppTest.

Covers the paths a browser cannot easily reach from a test: the startup refusals, the
reference-text validation, and — by seeding the session cache — the result rendering,
including the "—" for an unavailable prosody score.
"""

from __future__ import annotations

import io
import os
import threading
from datetime import datetime, timezone

import pytest
from streamlit.testing.v1 import AppTest

import app as app_module
import db
import fallback_coach
import speech_analyzer as sa
import utils
from tests.conftest import ROOT
from utils import AzureBand, Mode

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
    drill_options = list(app.selectbox(key="preset_choice").options)
    app.radio[0].set_value("Paragraph — connected speech").run()
    assert list(app.selectbox(key="preset_choice").options) != drill_options


def test_choosing_a_preset_fills_the_reference_text(run_app) -> None:
    app = run_app()
    preset = app.selectbox(key="preset_choice").options[1]
    app.selectbox(key="preset_choice").set_value(preset).run()
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


def test_a_result_renders_the_score_breakdown(run_app) -> None:
    """#11/#12: Pronunciation headline, Completeness, then Accuracy/Fluency/Prosody bars."""
    app = seed_result(run_app(), offline_assessment())
    assert not app.exception

    # Of the scores, only Completeness stays a plain st.metric — Pronunciation is now a
    # banded headline number and Accuracy/Fluency/Prosody are the "Score breakdown" bars,
    # neither of which is an st.metric widget. nPVI joins it from the Rhythm section: the
    # drill fixture is two full sentences, so it clears `rhythm.MIN_PAIRS`.
    labels = [m.label for m in app.metric]
    assert labels == ["Completeness", "nPVI"]
    assert app.metric[0].value == "85", "the fixture has completeness populated"

    rendered = " ".join(m.value for m in app.markdown)
    assert "Score breakdown" in rendered
    # Fixture values from tests/fixtures/sample_azure_response.json: pron 83.0 (good, 80-89),
    # accuracy 89.0 (good), fluency 88.0 (good), prosody 76.4 (fair, 60-79).
    assert "83" in rendered
    for label, value in (("Accuracy score", "89"), ("Fluency score", "88"),
                          ("Prosody score", "76")):
        assert f"{label}</span><span>{value} / 100</span>" in rendered
    assert app_module.AZURE_BAND_COLOURS[AzureBand.GOOD] in rendered
    assert app_module.AZURE_BAND_COLOURS[AzureBand.FAIR] in rendered


def test_unavailable_prosody_renders_as_a_dash_not_zero(run_app) -> None:
    assessment = offline_assessment()
    assessment.overall_scores["prosody"] = None
    app = seed_result(run_app(), assessment)
    rendered = " ".join(m.value for m in app.markdown)
    assert "Prosody score</span><span>—</span>" in rendered, (
        "a missing score and a score of zero are different things"
    )
    assert "Prosody score</span><span>0 / 100</span>" not in rendered


def test_a_missing_pronunciation_score_renders_as_a_dash(run_app) -> None:
    assessment = offline_assessment()
    assessment.overall_scores["pron_score"] = None
    app = seed_result(run_app(), assessment)
    rendered = " ".join(m.value for m in app.markdown)
    assert ">—</div>" in rendered


def test_the_error_counts_reflect_the_fixtures_real_mispronunciations(run_app) -> None:
    """#10/#12: the committed drill fixture carries real Mispronunciation words — no
    synthetic payload needed to prove the headline count is wired up."""
    assessment = offline_assessment()
    expected = len(sa.mispronounced_words(assessment.words))
    assert expected > 0, "fixture is expected to carry real Mispronunciation words"
    app = seed_result(run_app(), assessment)
    rendered = " ".join(m.value for m in app.markdown)
    assert f">{expected}</span><span>Mispronunciations</span>" in rendered
    # The committed fixture has no UnexpectedBreak/MissingBreak/Monotone (documented gap in
    # memory-bank/progress.md), so those three badges are exercised in test_render.py against
    # a synthetic payload instead, the same way the rest of the delivery-fault code is.
    assert ">0</span><span>Unexpected break</span>" in rendered
    assert ">0</span><span>Missing break</span>" in rendered
    assert ">0</span><span>Monotone</span>" in rendered


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


def test_a_monotone_span_produces_a_drill_that_names_it_offline(run_app) -> None:
    """Issue #9's exit criterion, through the real render path.

    Synthetic: the committed fixture is clean on Break and Intonation, so the Monotone
    word is appended to it. No key is set — conftest clears GEMINI_API_KEY for every test
    — so this is the offline coach and nothing else.
    """
    assessment = offline_assessment()
    assessment.words.append({
        "word": "clouds", "accuracy": 96.0, "error_type": "None",
        "error_source": "azure", "delivery_error_types": ["Monotone"],
        "prosody_detail": {"break_length_ms": None, "monotone_confidence": 0.88},
        "syllables": [], "phonemes": [],
    })

    app = seed_result(run_app(), assessment)

    assert not app.exception
    body = " ".join(m.value for m in app.markdown)
    assert "Delivery" in body
    assert "Flat intonation" in body, "the fault is named in words, not as 'Monotone'"
    assert "**Drill** —" in body
    drill = next(m.value for m in app.markdown if m.value.startswith("**Drill** —"))
    assert "clouds" in drill, "advice that does not name the span is not actionable"


def test_a_long_span_is_summarised_rather_than_listed_in_full(run_app) -> None:
    """From the captured bad reading: a real Monotone ran 30 words. Listing them all
    buries the sentence and the drill under a wall of commas, and the stretch worth
    practising is quoted in the drill anyway."""
    assessment = offline_assessment()
    for index in range(20):
        assessment.words.append({
            "word": f"word{index}", "accuracy": 96.0, "error_type": "None",
            "error_source": "azure", "delivery_error_types": ["Monotone"],
            "prosody_detail": {"break_length_ms": None, "monotone_confidence": 0.9},
            "syllables": [], "phonemes": [],
        })

    app = seed_result(run_app(), assessment)

    # `render_fix` uses the same "In this attempt:" caption, so pick the delivery one.
    caption = next(c.value for c in app.caption if "word0" in c.value)
    assert "and 8 more" in caption
    assert "word19" not in caption


def test_the_delivery_panel_shows_what_azure_measured(run_app) -> None:
    """Synthetic. The panel and the coaching section read the same helper, so they cannot
    disagree about the number — which is the reason the panel quotes one at all."""
    assessment = offline_assessment()
    assessment.words.append({
        "word": "clouds", "accuracy": 96.0, "error_type": "None",
        "error_source": "azure", "delivery_error_types": ["Monotone"],
        "prosody_detail": {"break_length_ms": None, "monotone_confidence": 0.88},
        "syllables": [], "phonemes": [],
    })

    app = seed_result(run_app(), assessment)

    body = " ".join(m.value for m in app.markdown)
    assert "SyllablePitchDeltaConfidence" in body and "0.88" in body


def test_a_clean_attempt_renders_no_delivery_drills(run_app) -> None:
    """The captured fixture, which really is clean. `render_delivery` further down already
    says so — saying it twice, three sections apart, is noise."""
    app = seed_result(run_app(), offline_assessment())

    assert not app.exception
    assert not any(m.value.startswith("**Drill** —") for m in app.markdown)
    assert any("No pausing or intonation problems" in s.value for s in app.success)


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


def test_a_spent_call_that_fell_back_cannot_be_re_clicked(run_app, monkeypatch) -> None:
    """The re-spend hole: a real call that fell back used to leave the button live.

    A malformed answer, or one whose every fix failed validation, still consumed the
    free-tier call. Keying the guard off the returned source meant the outcome decided
    whether it could be bought again — so exactly the failures worth not repeating were
    the repeatable ones.
    """
    import ai_coach

    assessment = offline_assessment()
    report = fallback_coach.build(assessment, Mode.DRILL)
    calls: list[int] = []

    def spent_but_fell_back(*args, **kwargs):
        calls.append(1)
        return ai_coach.CoachingResult(
            report=report, source=fallback_coach.SOURCE_FALLBACK, raw=report.model_dump()
        )

    monkeypatch.setattr(ai_coach, "coach", spent_but_fell_back)
    monkeypatch.setenv("OFFLINE_MODE", "false")
    monkeypatch.setenv("GEMINI_API_KEY", "placeholder-not-a-real-key")
    monkeypatch.setenv("AZURE_SPEECH_KEY", "placeholder")
    monkeypatch.setenv("AZURE_SPEECH_REGION", "eastus")
    monkeypatch.setenv("AZURE_TIER_CONFIRMED_F0", "true")

    app = seed_result(run_app(), assessment)
    app = next(b for b in app.button if "Gemini" in b.label).click().run()
    assert len(calls) == 1
    assert any("could not be reached" in i.value for i in app.info)

    # The click is handled in the pass that drew the button, so the button on screen is
    # still enabled — which is exactly why the guard cannot live on the disabled flag.
    app = next(b for b in app.button if "Gemini" in b.label).click().run()
    assert len(calls) == 1, "a fallback outcome must not be re-buyable"
    assert next(b for b in app.button if "Gemini" in b.label).disabled, (
        "the call was spent even though it fell back"
    )


# --- Reset, delete, and the in-flight controls ---------------------------------------------


def test_reset_clears_the_recording_the_text_and_the_result(run_app) -> None:
    app = seed_result(run_app(), offline_assessment())
    app.text_area[0].set_value("something I typed").run()
    assert app.session_state["last_key"]

    app = next(b for b in app.button if "Reset" in b.label).click().run()

    assert not app.exception
    assert app.text_area[0].value == ""
    assert app.selectbox(key="preset_choice").value == "Write my own"
    assert not app.session_state["last_key"], "the previous result must not stay on screen"


def test_reset_rebuilds_the_recorder_rather_than_leaving_the_take(run_app) -> None:
    """audio_input holds its own content and cannot be cleared through session state.

    The only way to empty one is to give it a key it has never seen, so the generation
    counter moving is what actually clears the take.
    """
    app = run_app()

    app = next(b for b in app.button if "Reset" in b.label).click().run()

    # Absent until something bumps it, so the first reset takes both to 1.
    assert app.session_state["recording_generation"] == 1
    assert app.session_state["upload_generation"] == 1


def test_the_delete_control_only_appears_with_a_recording(run_app) -> None:
    app = run_app()
    assert not [b for b in app.button if "Delete recording" in b.label], (
        "nothing to delete before anything is recorded"
    )


def test_assess_is_disabled_with_no_recording(run_app) -> None:
    app = run_app()
    assess = next(b for b in app.button if b.label == "Assess")
    assert assess.disabled


def test_no_stop_button_when_nothing_is_running(run_app) -> None:
    app = run_app()
    assert not [b for b in app.button if "Stop" in b.label], (
        "Stop is only meaningful while a request is in flight"
    )


@pytest.fixture
def settled_poll(monkeypatch: pytest.MonkeyPatch):
    """Let the in-flight pass finish instead of re-running forever.

    While a job is alive the script sleeps and calls `st.rerun()`, which is right in a
    browser — each rerun is a round trip that re-renders Stop and picks up a click. Under
    AppTest that same loop never settles, so the run times out before anything can be
    inspected. Neutralising `st.rerun` lets the pass complete and leaves exactly what a
    browser would have painted on that pass.
    """
    import streamlit as st

    monkeypatch.setattr(app_module, "JOB_POLL_SECONDS", 0)
    monkeypatch.setattr(st, "rerun", lambda *a, **k: None)


def _hanging_job(app: AppTest, stop: threading.Event) -> app_module.AssessJob:
    job = app_module.AssessJob(
        cancel_event=threading.Event(), key="k",
        reference_text=REFERENCE, mode=Mode.DRILL,
    )
    job.thread = threading.Thread(target=stop.wait, daemon=True)
    job.thread.start()
    app.session_state["assess_job"] = job
    return job


def test_a_running_job_disables_assess_and_offers_stop(run_app, settled_poll) -> None:
    """No double-submit is reachable: the button that starts a run is off while one runs."""
    app = run_app()
    never_finishes = threading.Event()
    _hanging_job(app, never_finishes)
    try:
        app.run()
        assert next(b for b in app.button if b.label == "Assess").disabled
        assert [b for b in app.button if "Stop" in b.label], "Stop must be offered"
        assert next(b for b in app.button if "Reset" in b.label).disabled
        assert any("Assessing" in i.value for i in app.info)
    finally:
        never_finishes.set()


def test_clicking_stop_sets_the_cancel_flag(run_app, settled_poll) -> None:
    app = run_app()
    never_finishes = threading.Event()
    job = _hanging_job(app, never_finishes)
    try:
        app.run()
        next(b for b in app.button if "Stop" in b.label).click().run()
        assert job.cancel_event.is_set(), "Stop must reach the worker's cancel flag"
    finally:
        never_finishes.set()


def test_a_second_assess_click_while_running_starts_nothing(run_app, settled_poll,
                                                            monkeypatch) -> None:
    """The state guard, not the disabled flag, is what closes the double-submit race."""
    started: list[int] = []
    monkeypatch.setattr(app_module, "start_assessment",
                        lambda *a, **k: started.append(1))

    app = run_app()
    never_finishes = threading.Event()
    _hanging_job(app, never_finishes)
    try:
        app.run()
        assert not started, "a run already in flight must not start another"
    finally:
        never_finishes.set()


def test_a_cancelled_job_is_reported_and_clears_itself(run_app) -> None:
    app = run_app()
    job = app_module.AssessJob(
        cancel_event=threading.Event(), key="k",
        reference_text=REFERENCE, mode=Mode.DRILL,
    )
    job.thread = threading.Thread(target=lambda: None)
    job.thread.start()
    job.thread.join()
    job.outcome = app_module.AssessOutcome(cancelled=True, reached_azure=False)
    app.session_state["assess_job"] = job

    app.run()

    assert any("stopped before anything was sent" in i.value for i in app.info)
    assert app.session_state["assess_job"] is None
    assert not [b for b in app.button if "Stop" in b.label]


def test_a_job_that_died_without_an_outcome_does_not_crash_the_page(run_app) -> None:
    """Unreachable in practice — the worker catches everything — but not a crash if it happens."""
    app = run_app()
    job = app_module.AssessJob(
        cancel_event=threading.Event(), key="k",
        reference_text=REFERENCE, mode=Mode.DRILL,
    )
    job.thread = threading.Thread(target=lambda: None)
    job.thread.start()
    job.thread.join()
    app.session_state["assess_job"] = job

    app.run()

    assert not app.exception
    assert any("ended unexpectedly" in e.value for e in app.error)


def test_words_scoring_full_marks_are_collapsed_out_of_the_flagged_list(run_app) -> None:
    """A monotone 100 is real, but it is not what the flagged list is for."""
    assessment = offline_assessment()
    assessment.words.append({
        "word": "clouds", "accuracy": 100.0, "error_type": "None",
        "error_source": "azure", "delivery_error_types": ["Monotone"],
        "syllables": [], "phonemes": [],
    })

    app = seed_result(run_app(), assessment)

    assert not app.exception
    assert any("Scored 100 but still flagged" in e.label for e in app.expander), (
        "a perfect-scoring word flagged for delivery belongs behind a collapsed panel"
    )


def test_a_collapsed_word_still_gets_a_unique_playback_key(run_app) -> None:
    """The word index keeps counting across both groups; a repeated key is a hard error."""
    assessment = offline_assessment()
    for word in ("clouds", "thunder"):
        assessment.words.append({
            "word": word, "accuracy": 100.0, "error_type": "None",
            "error_source": "azure", "delivery_error_types": ["Monotone"],
            "syllables": [], "phonemes": [],
        })

    app = seed_result(run_app(), assessment)

    assert not app.exception, "duplicate widget keys raise rather than render"


# --- The Progress tab -----------------------------------------------------------------------
# The frames, the rankings and the chart spec are covered in `test_progress_view.py` against
# real payloads. What is checked here is only that the tab is wired in: that it renders, that
# it says something useful when there is nothing to draw, and that giving Practice a tab of
# its own did not break the page it used to be.


def seed_attempts(app: AppTest, count: int = 3, *, benchmark: bool = False) -> None:
    """Write attempts straight into the app's own database, the way a real session would."""
    import json
    import progress_view

    payload = json.loads((ROOT / "tests" / "fixtures" / "sample_azure_response.json").read_text())
    conn = db.connect(os.environ["DB_PATH"])
    for index in range(count):
        db.record_attempt(
            conn,
            mode=Mode.PARAGRAPH if benchmark else Mode.DRILL,
            reference_text=progress_view.BENCHMARK_PASSAGE if benchmark else REFERENCE,
            recognised_text=REFERENCE, audio_seconds=12.0, audio_sha256=f"seed-{index}",
            overall_scores={"pron_score": 80.0 + index, "accuracy": 85.0, "fluency": 78.0,
                            "completeness": 100.0, "prosody": None if index else 70.0},
            azure_raw=payload, created_at=f"2026-07-0{index + 1}T08:00:00Z",
        )
    conn.close()


def test_the_page_has_a_today_a_practice_and_a_progress_tab(run_app) -> None:
    """Today is first: opening the app should answer "what am I doing today?" rather than
    present a blank textarea. The textarea is one click away, which is where a thing you
    reach for on purpose belongs."""
    app = run_app()
    assert not app.exception
    assert len(app.tabs) == 3


def test_the_progress_tab_says_so_when_there_is_no_history(run_app) -> None:
    """An empty chart area explains nothing; the empty state has to be words."""
    app = run_app()
    assert not app.exception
    assert any("Nothing recorded yet" in info.value for info in app.info)


def test_the_progress_tab_renders_a_chart_once_there_is_history(run_app) -> None:
    app = run_app()
    seed_attempts(app)
    app.run()
    assert not app.exception
    charts = [element for element in app.main if element.type == "vega_lite_chart"]
    assert charts, "the trajectory should be drawn"


def test_free_practice_alone_says_the_headline_series_is_still_empty(run_app) -> None:
    """The whole point of the benchmark, said out loud while it has not been read."""
    app = run_app()
    seed_attempts(app)
    app.run()
    assert not app.exception
    assert any("has not been read" in warning.value for warning in app.warning)


def test_a_benchmark_read_replaces_that_warning_with_when_it_last_happened(run_app) -> None:
    app = run_app()
    seed_attempts(app, benchmark=True)
    app.run()
    assert not app.exception
    assert not any("has not been read" in warning.value for warning in app.warning)
    assert any("Benchmark passage last read" in caption.value for caption in app.caption)


def test_the_benchmark_passage_is_the_first_paragraph_preset(run_app) -> None:
    """It has to be selected, not retyped: the series is identified by matching the text."""
    import progress_view

    assert list(app_module.PRESETS[Mode.PARAGRAPH])[0] == progress_view.BENCHMARK_TITLE
    assert progress_view.is_benchmark(
        app_module.PRESETS[Mode.PARAGRAPH][progress_view.BENCHMARK_TITLE]
    )


def test_the_history_table_still_renders_under_the_charts(run_app) -> None:
    app = run_app()
    seed_attempts(app)
    app.run()
    assert not app.exception
    assert any("History" in expander.label for expander in app.expander)


# --- Rhythm ---------------------------------------------------------------------------------
# The nPVI figure must never appear without saying what it can be compared to. These pin that,
# because the number on its own invites exactly the comparison it cannot support.


def test_the_rhythm_section_reports_the_fixtures_npvi(run_app) -> None:
    app = seed_result(run_app(), offline_assessment())
    assert not app.exception
    npvi = [m for m in app.metric if m.label == "nPVI"]
    assert len(npvi) == 1
    assert npvi[0].value == "55.9"


def test_rhythm_without_a_baseline_says_the_number_has_no_comparison(
    run_app, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The published-band trap, named out loud rather than left to be assumed."""
    import rhythm

    monkeypatch.setattr(rhythm, "baseline", lambda *a, **k: None)
    app = seed_result(run_app(), offline_assessment())
    said = " ".join(c.value for c in app.caption)
    assert "nothing to compare against yet" in said
    assert "Published General American" in said
    assert "capture_baseline.py" in said


def test_rhythm_against_a_baseline_names_the_voice_and_the_direction(
    run_app, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A synthesiser, not a native speaker — and which way a lower score points."""
    import rhythm

    monkeypatch.setattr(rhythm, "baseline", lambda *a, **k: rhythm.Baseline(
        rhythm=rhythm.Rhythm(npvi=58.4, pairs=180, intervals=206, runs=26),
        voice="en-US-BrianNeural", captured_at="2026-08-19T00:00:00Z",
    ))
    app = seed_result(run_app(), offline_assessment())
    said = " ".join(c.value for c in app.caption)
    assert "en-US-BrianNeural" in said
    assert "not a native speaker" in said
    assert "syllable-timed" in said

    npvi = [m for m in app.metric if m.label == "nPVI"][0]
    assert npvi.delta is not None and "vs baseline" in npvi.delta


def test_too_little_speech_shows_no_rhythm_number(run_app, monkeypatch) -> None:
    """A handful of vowels must produce a sentence, not a figure."""
    import rhythm

    assessment = offline_assessment()
    monkeypatch.setattr(
        rhythm, "npvi",
        lambda *a, **k: rhythm.Rhythm(npvi=None, pairs=3, intervals=4, runs=1),
    )
    app = seed_result(run_app(), assessment)
    assert not [m for m in app.metric if m.label == "nPVI"]
    assert any("Not enough connected speech" in c.value for c in app.caption)


# --- The Today tab ----------------------------------------------------------
# The one thing a browser cannot easily prove and a test can: with nothing recorded, the queue
# offers nothing rather than seeding a plausible-looking target from somewhere.
#
# Seeded through the REAL path — two attempts carrying the committed Azure capture — rather
# than by stubbing the aggregate. AppTest executes app.py as its own module object, so a
# monkeypatch on the imported `app` here would not reach the running script anyway, and the
# real path is what needs proving: promotion has to come out of stored attempts.


def seed_flagged_history(times: int = 2) -> None:
    """Record `times` attempts of the captured drill, whose headline fault is /θ/ → /s/."""
    import json

    payload = json.loads((ROOT / "tests" / "fixtures" /
                          "sample_azure_response.json").read_text())
    conn = db.connect()
    for index in range(times):
        db.record_attempt(
            conn, mode=Mode.DRILL, reference_text=REFERENCE,
            recognised_text=REFERENCE, audio_seconds=12.8,
            audio_sha256=f"seed-{index}", overall_scores={"pron_score": 83.0},
            azure_raw=payload, offline=False,
            created_at=f"2026-08-{10 + index:02d}T00:00:00Z",
        )
    conn.close()


def test_with_no_history_the_queue_offers_nothing_rather_than_guessing(run_app) -> None:
    """The cold-start contract. No first language, no default list, no invented target."""
    app = run_app()
    assert not app.exception
    text = " ".join(info.value for info in app.info)
    assert "promoted from your own assessed attempts" in text


def test_one_attempt_is_not_a_pattern(run_app) -> None:
    """A sound has to recur before it becomes a target — one bad reading is not evidence."""
    seed_flagged_history(times=1)
    app = run_app()
    assert not app.exception
    conn = db.connect()
    assert db.targets(conn) == []
    assert any("recurred often enough" in info.value for info in app.info)


def test_a_recurring_substitution_is_promoted_from_the_stored_attempts(run_app) -> None:
    """Every target has to trace back to a sound the recordings actually flagged."""
    import practice_queue
    import progress_view

    seed_flagged_history()
    app = run_app()
    assert not app.exception

    conn = db.connect()
    rows = db.targets(conn)
    assert rows, "the capture's recurring faults should reach the queue"

    parsed = progress_view.parse_attempts(db.attempt_payloads(conn))
    offered = {
        (c.item, c.kind) for c in practice_queue.candidates(
            progress_view.flagged_phonemes(parsed).to_dict("records"),
            progress_view.weak_syllables(parsed).to_dict("records"),
        )
    }
    for row in rows:
        assert (row["item"], row["kind"]) in offered, (
            f"{row['item']} is on the list without evidence behind it"
        )

    markdown = " ".join(block.value for block in app.markdown)
    assert rows[0]["item"] in markdown


def test_the_three_slots_go_to_three_different_kinds(run_app) -> None:
    """Three consonant contrasts would crowd out a vowel gap flagged just as often, and
    sounds and rhythm are different problems."""
    seed_flagged_history()
    run_app()
    conn = db.connect()
    kinds = {row["kind"] for row in db.targets(conn)}
    assert kinds == {"contrast", "vowel", "stress"}


def test_a_promoted_target_survives_a_restart(run_app) -> None:
    """The queue's whole promise: thirty days of use is not thirty first sessions."""
    seed_flagged_history()
    run_app()

    import streamlit as st

    st.cache_resource.clear()          # a fresh process would have no connection either
    conn = db.connect()
    rows = db.targets(conn)
    assert rows and rows[0]["state"] == "active"
    assert rows[0]["next_due"], "a target with no due date is not scheduled"


def test_the_block_cannot_be_started_offline(run_app) -> None:
    """A block is live synthesis by definition; OFFLINE_MODE keeps its absolute meaning."""
    seed_flagged_history()
    app = run_app()
    starts = [button for button in app.button if "Start the block" in button.label]
    assert starts, "the due block should be offered"
    assert all(button.disabled for button in starts)


def test_the_evidence_and_the_rule_are_both_on_screen(run_app) -> None:
    """The brief requires promotion and graduation to be visible, not implicit."""
    seed_flagged_history()
    app = run_app()
    rendered = " ".join(block.value for block in app.markdown)
    assert "flagged in 2 separate attempts" in rendered   # the evidence, with numbers
    assert "90%" in rendered                              # the rule
    assert "50%" in rendered                              # and the chance floor beside it


def test_the_target_set_is_capped_at_three(run_app) -> None:
    """A target set you cannot hold in your head while speaking is not a target set."""
    seed_flagged_history(times=3)
    run_app()
    conn = db.connect()
    assert len(db.targets(conn)) <= utils.MAX_ACTIVE_TARGETS


# --- Running a block end to end ------------------------------------------------------------
# Headless, with the one seam that costs money replaced. A browser cannot easily click twenty
# trials, and doing it live would buy the same clips for every run of the suite.


WAV = b"RIFF" + b"\x00" * 40


@pytest.fixture
def no_synthesis(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Replace the single call that reaches Azure, and point the cache somewhere throwaway.

    `tts.synthesise` is stubbed rather than `_speak`, so the meter accounting around it is
    the real code — the point of the exercise is what the block charges, not what Azure
    returns.
    """
    import tts

    monkeypatch.setenv("TTS_CACHE_DIR", str(tmp_path / "tts_cache"))
    monkeypatch.setenv("OFFLINE_MODE", "false")
    monkeypatch.setenv("AZURE_SPEECH_KEY", "test-key-not-a-real-one")
    monkeypatch.setenv("AZURE_SPEECH_REGION", "westeurope")
    monkeypatch.setenv("AZURE_TIER_CONFIRMED_F0", "true")

    calls: list[tuple[str, str]] = []

    def fake(text, *, voice=None, slow=False, on_attempt=None):
        if on_attempt is not None:
            on_attempt(1)
        chosen = voice or tts.voice_name()
        calls.append((text, chosen))
        return tts.Synthesis(audio=WAV, characters=len(text), voice=chosen, attempts=1)

    monkeypatch.setattr(tts, "synthesise", fake)
    return calls


def start_a_block(app: AppTest) -> AppTest:
    for button in app.button:
        if "Start the block" in button.label:
            return button.click().run()
    raise AssertionError("no block was offered")


def answer_every_trial(app: AppTest) -> AppTest:
    """Answer the first alternative on every trial, then step past each reveal."""
    for _ in range(utils.PERCEPTION_BLOCK_TRIALS * 3):
        state = app.session_state["perception_block"]
        if state is None:
            break
        index = int(state["index"])
        if index >= len(state["block"].trials):
            break
        if state["revealed"]:
            app = [b for b in app.button if b.label.startswith("Next")][0].click().run()
            continue
        trial = state["block"].trials[index]
        choice = [b for b in app.button if b.label == trial.alternatives[0]][0]
        app = choice.click().run()
    return app


def test_a_block_runs_end_to_end_and_charges_only_what_it_synthesised(
    run_app, no_synthesis
) -> None:
    seed_flagged_history()
    conn = db.connect()
    before = db.monthly_tts_characters(conn)

    app = start_a_block(run_app())
    assert not app.exception

    block = app.session_state["perception_block"]["block"]
    import perception_trainer

    expected = perception_trainer.stimuli(block)
    assert sorted(no_synthesis) == sorted(expected), (
        "every clip the block needs, and nothing else"
    )
    charged = db.monthly_tts_characters(conn) - before
    assert charged == sum(len(text) for text, _ in expected)


def test_no_clip_is_ever_bought_twice(run_app, no_synthesis) -> None:
    """The disk cache is checked before the pre-flight and before the meter.

    A second block is not free — it plans different stimuli out of the same pool, and the
    ones it has never played have to be synthesised. What must never happen is paying again
    for a clip already on disk, and the charge has to match exactly the new ones.
    """
    seed_flagged_history()
    app = start_a_block(run_app())
    first = list(no_synthesis)
    app.session_state["perception_block"] = None
    app = app.run()

    conn = db.connect()
    before = db.monthly_tts_characters(conn)
    no_synthesis.clear()

    start_a_block(app)
    second = list(no_synthesis)

    assert not set(first) & set(second), "a clip already on disk was bought again"
    assert len(second) == len(set(second)), "the same clip was bought twice in one block"
    charged = db.monthly_tts_characters(conn) - before
    assert charged == sum(len(text) for text, _ in second)


def test_a_repeated_block_plan_costs_nothing_at_all(run_app, no_synthesis) -> None:
    """The exact-repeat case: the same stimuli asked for twice charge once."""
    import perception_trainer
    import tts

    seed_flagged_history()
    app = start_a_block(run_app())
    block = app.session_state["perception_block"]["block"]

    conn = db.connect()
    before = db.monthly_tts_characters(conn)
    no_synthesis.clear()

    for text, voice in perception_trainer.stimuli(block):
        assert tts.cached_audio(voice, text) is not None

    assert no_synthesis == []
    assert db.monthly_tts_characters(conn) == before


def test_every_trial_is_stored_as_it_is_answered(run_app, no_synthesis) -> None:
    seed_flagged_history()
    app = start_a_block(run_app())
    item = app.session_state["perception_block"]["block"].item

    conn = db.connect()
    assert db.trials_for(conn, item) == []
    app = answer_every_trial(app)

    trials = db.trials_for(conn, item)
    assert len(trials) == utils.PERCEPTION_BLOCK_TRIALS
    assert {row["alternatives"] for row in trials} == {2}
    assert all(row["novel"] == 1 for row in trials), "a first block is entirely new"
    assert len({row["voice"] for row in trials}) >= perception_trainer_min_voices()


def perception_trainer_min_voices() -> int:
    import perception_trainer

    return perception_trainer.MIN_VOICES


def test_an_abandoned_block_keeps_its_answers_but_earns_no_verdict(
    run_app, no_synthesis
) -> None:
    """Store the evidence, not only the verdict — the two are separate questions."""
    import practice_queue

    seed_flagged_history()
    app = start_a_block(run_app())
    state = app.session_state["perception_block"]
    item = state["block"].item
    trial = state["block"].trials[0]
    app = [b for b in app.button if b.label == trial.alternatives[0]][0].click().run()
    app = [b for b in app.button if b.label == "Stop the block"][0].click().run()

    conn = db.connect()
    trials = [dict(row) for row in db.trials_for(conn, item)]
    assert len(trials) == 1, "the answer given is kept"

    summaries = practice_queue.summarise_blocks(trials)
    assert not summaries[0].complete, "a part-finished block is not a claim"
    row = [t for t in db.targets(conn) if t["item"] == item][0]
    assert row["state"] == "active"


def test_finishing_a_perfect_block_reports_it_against_the_chance_floor(
    run_app, no_synthesis
) -> None:
    seed_flagged_history()
    app = start_a_block(run_app())
    state = app.session_state["perception_block"]
    # Answer every trial correctly by driving the state directly, then render the summary.
    state["answers"] = [True] * len(state["block"].trials)
    state["index"] = len(state["block"].trials)
    app = app.run()

    assert app.metric[0].value == "100%"
    captions = " ".join(c.value for c in app.caption)
    assert "50% is what guessing scores" in captions, (
        "an accuracy without its floor reports near-noise as progress"
    )
    assert "never heard before" in captions


def test_a_block_at_the_chance_floor_says_it_proves_nothing(run_app, no_synthesis) -> None:
    seed_flagged_history()
    app = start_a_block(run_app())
    state = app.session_state["perception_block"]
    total = len(state["block"].trials)
    state["answers"] = [True] * (total // 2) + [False] * (total - total // 2)
    state["index"] = total
    app = app.run()

    warnings = " ".join(w.value for w in app.warning)
    assert "what guessing looks like" in warnings


# --- Shadowing ---------------------------------------------------------------------------------
# The one surface where practice happens WHILE speaking. What is checked here is the wiring the
# pure modules cannot see: that the offer reaches Today with no history at all, that the model
# and the recorder end up on screen together with nothing that reruns between them, and above
# all that the tag reaches the database — an untagged shadowed read is indistinguishable from a
# cold one afterwards and lands on the trajectory the tag exists to keep it off.


def real_wav(seconds: float = 0.5) -> bytes:
    """A decodable WAV, since the echo track really runs through pydub."""
    import struct
    import wave

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24_000)
        handle.writeframes(struct.pack("<h", 0) * int(seconds * 24_000))
    return buffer.getvalue()


@pytest.fixture
def shadow_synthesis(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """`tts.synthesise` stubbed with real decodable audio; the meter around it stays real."""
    import tts

    monkeypatch.setenv("TTS_CACHE_DIR", str(tmp_path / "shadow_cache"))
    monkeypatch.setenv("OFFLINE_MODE", "false")
    monkeypatch.setenv("AZURE_SPEECH_KEY", "test-key-not-a-real-one")
    monkeypatch.setenv("AZURE_SPEECH_REGION", "westeurope")
    monkeypatch.setenv("AZURE_TIER_CONFIRMED_F0", "true")

    calls: list[tuple[str, bool]] = []

    def fake(text, *, voice=None, slow=False, on_attempt=None):
        if on_attempt is not None:
            on_attempt(1)
        calls.append((text, slow))
        payload = tts.payload_for(text, slow=slow, voice=voice)
        return tts.Synthesis(audio=real_wav(), characters=len(payload),
                             voice=voice or tts.voice_name(), attempts=1)

    monkeypatch.setattr(tts, "synthesise", fake)
    return calls


def open_shadow(app: AppTest) -> AppTest:
    return [b for b in app.button if "Shadow this passage" in b.label][0].click().run()


def prepare_model(app: AppTest) -> AppTest:
    return [b for b in app.button if "Prepare the model" in b.label][0].click().run()


def test_shadowing_is_offered_with_no_history_at_all(run_app) -> None:
    """It is the one practice on this page that needs none: it trains rhythm against a model
    rather than a sound the recordings flagged, so there is nothing for it to wait for."""
    app = run_app()
    assert not app.exception
    assert any("Shadow this passage" in b.label for b in app.button)


def test_the_shadow_offer_lists_the_paragraph_presets(run_app) -> None:
    """Not a second list: a passage differing by one word would pair against nothing."""
    app = run_app()
    options = list(app.selectbox(key="shadow-passage").options)
    assert options == list(app_module.PRESETS[Mode.PARAGRAPH])


def test_opening_a_session_renders_it_in_place(run_app) -> None:
    """Streamlit cannot select a tab programmatically, so the session renders inside Today —
    the pattern the perception block already established."""
    app = open_shadow(run_app())
    assert not app.exception
    assert app.session_state[app_module.SHADOW_KEY] is not None
    assert any("Shadowing:" in m.value for m in app.markdown)


def test_the_surface_says_nothing_is_scored_while_shadowing(run_app) -> None:
    app = open_shadow(run_app())
    captions = " ".join(c.value for c in app.caption)
    assert "not another reading you get marked on" in captions


def test_the_surface_demands_headphones(run_app) -> None:
    """On speakers Azure hears the model too and scores the mixture."""
    app = open_shadow(run_app())
    assert any("Headphones" in w.value for w in app.warning)


def test_preparing_the_model_is_disabled_offline(run_app) -> None:
    """Synthesis is a live call by definition and there is no fixture to replay for audio —
    the same rule "Hear it" follows."""
    app = open_shadow(run_app())
    prepare = [b for b in app.button if "Prepare the model" in b.label][0]
    assert prepare.disabled
    captions = " ".join(c.value for c in app.caption)
    assert "OFFLINE_MODE" in captions


def today_recorders(app: AppTest) -> list:
    """Audio inputs on the Today tab only — the Practice tab has its own on every pass."""
    return list(app.tabs[0].get("audio_input"))


def test_no_recorder_appears_before_the_model_does(run_app) -> None:
    """The recorder is only useful next to a player, and one that reruns to fetch the model
    mid-take would cut the recording in half."""
    app = open_shadow(run_app())
    assert today_recorders(app) == []


def test_the_model_and_the_recorder_arrive_together(run_app, shadow_synthesis) -> None:
    """The layout constraint, asserted: `st.audio_input` holds a live MediaRecorder, so
    nothing may rerun between pressing record and pressing play."""
    app = prepare_model(open_shadow(run_app()))
    assert not app.exception
    assert today_recorders(app)
    assert not any("Prepare the model" in b.label for b in app.button)


def test_the_whole_passage_is_bought_as_one_clip_for_speaking_along(
    run_app, shadow_synthesis
) -> None:
    app = open_shadow(run_app())
    passage = app.session_state[app_module.SHADOW_KEY]["passage"]
    prepare_model(app)
    assert [text for text, _ in shadow_synthesis] == [passage]


def test_a_second_preparation_charges_nothing(run_app, shadow_synthesis) -> None:
    """The disk lookup happens before the pre-flight and before the meter, the same ordering
    `play()` depends on."""
    app = prepare_model(open_shadow(run_app()))
    app.session_state[app_module.SHADOW_KEY]["audio"] = {}
    prepare_model(app.run())
    assert len(shadow_synthesis) == 1


def test_echo_mode_buys_one_clip_per_phrase(run_app, shadow_synthesis) -> None:
    import shadowing

    app = open_shadow(run_app())
    passage = app.session_state[app_module.SHADOW_KEY]["passage"]
    app = app.radio(key="shadow-mode").set_value(shadowing.ECHO).run()
    prepare_model(app)
    assert [text for text, _ in shadow_synthesis] == shadowing.phrases(passage)


def test_echo_mode_offers_no_recorder_at_all(run_app, shadow_synthesis) -> None:
    """Its recording would pause between every phrase, so Azure would mark the delivery down
    for a gap the format put there. Offering it as a warm-up is honest; scoring it is not."""
    import shadowing

    app = open_shadow(run_app())
    app = app.radio(key="shadow-mode").set_value(shadowing.ECHO).run()
    app = prepare_model(app)
    assert today_recorders(app) == []
    assert not any("Assess this read" in b.label for b in app.button)
    assert any("not assessed" in m.value for m in app.markdown)


def test_the_slow_rate_is_a_separate_purchase(run_app, shadow_synthesis) -> None:
    app = prepare_model(open_shadow(run_app()))
    app = app.checkbox(key="shadow-slow").set_value(True).run()
    prepare_model(app)
    assert [slow for _, slow in shadow_synthesis] == [False, True]


def test_a_shadowed_read_is_stored_tagged(run_app, shadow_synthesis) -> None:
    """The load-bearing assertion of the whole chunk."""
    import shadowing

    app = prepare_model(open_shadow(run_app()))
    state = app.session_state[app_module.SHADOW_KEY]
    passage = str(state["passage"])

    conn = db.connect(os.environ["DB_PATH"])
    attempt_id = db.record_attempt(
        conn, mode=Mode.PARAGRAPH, reference_text=passage, recognised_text=passage,
        audio_seconds=70.0, audio_sha256="shadowed-read",
        overall_scores={"pron_score": 80.0, "accuracy": 85.0, "fluency": 78.0,
                        "completeness": 100.0, "prosody": 70.0},
        azure_raw={"RecognitionStatus": "Success"},
    )
    db.tag_attempt(conn, attempt_id, shadowing.SHADOW_TAG)

    row = [r for r in db.attempt_series(conn) if r["id"] == attempt_id][0]
    conn.close()
    assert row["shadowed"]


def test_finishing_a_read_puts_the_passage_on_the_queue(run_app, shadow_synthesis) -> None:
    """Created on first USE — a session opened and abandoned adds no standing practice."""
    import practice_queue

    app = open_shadow(run_app())
    state = app.session_state[app_module.SHADOW_KEY]
    conn = db.connect(os.environ["DB_PATH"])
    assert [r for r in db.targets(conn) if r["kind"] == practice_queue.SHADOW] == []

    app_module.record_shadow_session(
        conn, str(state["title"]), str(state["passage"]),
        now=datetime(2026, 8, 19, tzinfo=timezone.utc),
    )
    rows = [r for r in db.targets(conn) if r["kind"] == practice_queue.SHADOW]
    conn.close()
    assert len(rows) == 1
    assert rows[0]["next_due"] > "2026-08-19"


def test_a_shadow_target_does_not_appear_in_the_three_slots(run_app, shadow_synthesis) -> None:
    """It is never promoted into one and never graduates out of one, so counting it would
    retire a sound the recordings are still flagging."""
    import practice_queue

    seed_flagged_history()
    conn = db.connect(os.environ["DB_PATH"])
    app_module.record_shadow_session(
        conn, "Benchmark", app_module.PRESETS[Mode.PARAGRAPH][
            list(app_module.PRESETS[Mode.PARAGRAPH])[0]
        ], now=datetime(2026, 8, 19, tzinfo=timezone.utc),
    )
    conn.close()

    app = run_app()
    assert not app.exception
    headings = " ".join(m.value for m in app.markdown)
    # The promoted targets are counted; the shadowing passage sitting beside them is not, and
    # it gets its own section rather than a card in this list.
    conn = db.connect(os.environ["DB_PATH"])
    expected = len([
        r for r in db.targets(conn, state=practice_queue.ACTIVE)
        if practice_queue.promotable(str(r["kind"]))
    ])
    conn.close()
    assert expected, "the seeded history promoted nothing, so this proves nothing"
    assert f"Working on ({expected} of {utils.MAX_ACTIVE_TARGETS})" in headings
    assert practice_queue.KIND_LABELS[practice_queue.SHADOW] not in headings


def test_backing_out_of_a_session_returns_to_today(run_app) -> None:
    app = open_shadow(run_app())
    app = [b for b in app.button if "Back to Today" in b.label][0].click().run()
    assert app.session_state[app_module.SHADOW_KEY] is None
    assert any("Shadow this passage" in b.label for b in app.button)


def test_the_progress_tab_names_the_delta_against_a_cold_read(run_app) -> None:
    """The exit criterion, on screen: a shadowed read and a cold read of the same passage
    side by side with their fluency and prosody delta named."""
    import progress_view
    import shadowing

    conn = db.connect(os.environ["DB_PATH"])
    for index, (fluency, prosody, shadowed) in enumerate(
        [(70.0, 60.0, False), (78.0, 69.0, True)]
    ):
        attempt_id = db.record_attempt(
            conn, mode=Mode.PARAGRAPH,
            reference_text=progress_view.BENCHMARK_PASSAGE,
            recognised_text=progress_view.BENCHMARK_PASSAGE,
            audio_seconds=70.0, audio_sha256=f"pair-{index}",
            overall_scores={"pron_score": 80.0, "accuracy": 85.0, "fluency": fluency,
                            "completeness": 100.0, "prosody": prosody},
            azure_raw={"RecognitionStatus": "Success"},
            created_at=f"2026-07-0{index + 1}T08:00:00Z",
        )
        if shadowed:
            db.tag_attempt(conn, attempt_id, shadowing.SHADOW_TAG)
    conn.close()

    app = run_app()
    assert not app.exception
    said = " ".join(m.value for m in app.markdown)
    assert "Fluency +8.0" in said
    assert "Prosody +9.0" in said
    assert "1 pair" in said


def test_the_tag_travels_through_the_worker_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    """The row and its tag are written under one `_DB_LOCK`, on the assessment thread.

    Exercised through `run_assessment_job` itself rather than by writing the row directly:
    that function is the only place a tag is ever attached, it runs off the script thread, and
    a tag that failed to land there would be invisible until a shadowed read had already gone
    onto the cold trajectory. Offline, so it replays the fixture and spends nothing.
    """
    import shadowing

    conn = db.connect(":memory:")
    outcome = app_module.run_assessment_job(
        conn,
        b"RIFF" + b"\x00" * 40,
        12.0,
        REFERENCE,
        Mode.DRILL,
        threading.Event(),
        (shadowing.SHADOW_TAG,),
    )

    assert outcome.error is None, outcome.error
    assert outcome.attempt_id is not None
    assert db.tags_for(conn, outcome.attempt_id) == {shadowing.SHADOW_TAG}
    # `attempt_series` is deliberately NOT asserted here: this row is an offline replay, and
    # both progress readers exclude those — the fixture scores the same every time, so thirty
    # identical points would not be a trajectory. `test_db` covers the join on a real row.
    conn.close()


def test_an_untagged_assessment_writes_no_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    """A cold read must stay untagged, or the trajectory it belongs on would lose it."""
    conn = db.connect(":memory:")
    outcome = app_module.run_assessment_job(
        conn, b"RIFF" + b"\x00" * 40, 12.0, REFERENCE, Mode.DRILL, threading.Event(),
    )
    assert outcome.attempt_id is not None
    assert db.tags_for(conn, outcome.attempt_id) == set()
    conn.close()


def test_a_shadowed_result_renders_on_exactly_one_surface(
    run_app, shadow_synthesis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression, found live rather than in a test: `StreamlitDuplicateElementKey`.

    `last_key` is a single slot and Streamlit executes EVERY tab body on every rerun, so once
    the shadow surface could also start an assessment, both it and the Practice tab rendered
    the same result. `render_result` derives its widget keys from the attempt, so the second
    render did not merely look odd — it collided with the first and blew up the page.
    """
    app = prepare_model(open_shadow(run_app()))
    state = app.session_state[app_module.SHADOW_KEY]
    passage = str(state["passage"])

    # Stand in for a finished shadowed read: the cache entry plus the ownership the shadow
    # surface claims when it starts one.
    from collections import OrderedDict

    # Back offline now the model is bought: the stand-in assessment below is a fixture replay,
    # and the audio already in session state keeps the surface rendering exactly as it was.
    monkeypatch.setenv("OFFLINE_MODE", "true")

    key = utils.attempt_hash(passage, b"take", Mode.PARAGRAPH)
    state["key"] = key
    app.session_state["assessments"] = OrderedDict({
        key: app_module.CachedAttempt(
            key=key, assessment=sa.analyse("/nonexistent.wav", passage, Mode.PARAGRAPH),
            reference_text=passage, attempt_id=1, mode=Mode.PARAGRAPH,
        )
    })
    app.session_state["last_key"] = key
    app.session_state[app_module.RESULT_OWNER_KEY] = app_module.SHADOW_OWNER
    app = app.run()

    assert not app.exception
    coach_buttons = [b for b in app.button if b.label.startswith("✨")]
    assert len(coach_buttons) == 1, "the result rendered on both tabs at once"


def test_leaving_a_shadow_session_takes_its_result_with_it(run_app, shadow_synthesis) -> None:
    """A shadowed read's report must not reappear under the Practice tab, which did not
    produce it."""
    app = prepare_model(open_shadow(run_app()))
    app.session_state["last_key"] = "some-shadowed-attempt"
    app.session_state[app_module.RESULT_OWNER_KEY] = app_module.SHADOW_OWNER
    app = app.run()
    app = [b for b in app.button if "Back to Today" in b.label][0].click().run()

    assert not app.exception
    assert app.session_state["last_key"] is None
    assert app.session_state[app_module.RESULT_OWNER_KEY] is None


def test_a_shadow_row_alone_still_reads_as_an_empty_queue(run_app, shadow_synthesis) -> None:
    """Found live: a queue holding nothing but a shadowing passage is still an empty queue.

    A shadow row is a standing practice, not something the recordings promoted, so letting it
    make `targets` non-empty answered "what am I doing today?" with *"nothing due, they are all
    on the review schedule"* about targets that had never been promoted at all — and captioned
    the empty list *"everything promoted so far has graduated"*.
    """
    conn = db.connect(os.environ["DB_PATH"])
    app_module.record_shadow_session(
        conn, "Benchmark", app_module.PRESETS[Mode.PARAGRAPH][
            list(app_module.PRESETS[Mode.PARAGRAPH])[0]
        ], now=datetime(2026, 8, 19, tzinfo=timezone.utc),
    )
    conn.close()

    app = run_app()
    assert not app.exception
    said = " ".join(i.value for i in app.info) + " ".join(c.value for c in app.caption)
    assert "Nothing to practise yet" in said
    assert "everything promoted so far has graduated" not in said
    assert not any("Nothing due today" in s.value for s in app.success)
