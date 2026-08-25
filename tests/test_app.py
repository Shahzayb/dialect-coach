"""The UI itself, run headlessly with Streamlit's AppTest.

Covers the paths a browser cannot easily reach from a test: the startup refusals, the
reference-text validation, and — by seeding the session cache — the result rendering,
including the "—" for an unavailable prosody score.
"""

from __future__ import annotations

import io
import os
import threading

import pytest
from streamlit.testing.v1 import AppTest

import app as app_module
import db
import fallback_coach
import speech_analyzer as sa
import utils
from utils import AzureBand, Mode

from conftest import ROOT

APP = str(ROOT / "src" / "app.py")
REFERENCE = (
    "The weather this month has been rather unpredictable. Thursday brought thunder "
    "and thick clouds, while Wednesday stayed warm and clear."
)


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
        OFFLINE_MODE="false",
        MONTHLY_BUDGET_USD="0.00",
        AZURE_TIER_CONFIRMED_F0="false",
    )
    assert app.error, "the app must refuse to start rather than risk an S0 resource"
    assert "F0" in app.error[0].value
    assert "OFFLINE_MODE=true" in app.error[0].value, "the message must offer a way forward"


def test_missing_credentials_are_reported_by_name(run_app) -> None:
    app = run_app(
        OFFLINE_MODE="false",
        MONTHLY_BUDGET_USD="0.00",
        AZURE_TIER_CONFIRMED_F0="true",
    )
    assert app.error
    assert "AZURE_SPEECH_KEY" in app.error[0].value


def test_the_usage_line_is_always_shown(run_app) -> None:
    app = run_app()
    assert any("Azure portal is authoritative" in c.value for c in app.caption)


def test_presets_change_with_the_mode(run_app) -> None:
    app = run_app()
    scripted_options = list(app.selectbox(key="preset_choice").options)
    app.radio[0].set_value("Unscripted — speak freely on a prompt").run()
    assert list(app.selectbox(key="preset_choice").options) != scripted_options


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


def seed_result(
    app: AppTest, assessment, *, attempt_id: int | None = None, mode: Mode = Mode.PARAGRAPH
) -> AppTest:
    """Put an assessment in the session cache the way a successful run would.

    The reference text, the row id and the mode travel with it because the widgets that
    produced them can all be changed without re-running anything — the panel has to render
    the text the scores were computed against, not whatever is in the textarea now.
    """
    from collections import OrderedDict

    key = utils.attempt_hash(REFERENCE, b"audio", mode)
    app.session_state["assessments"] = OrderedDict(
        {
            key: app_module.CachedAttempt(
                key=key,
                assessment=assessment,
                reference_text=REFERENCE,
                attempt_id=attempt_id,
                mode=mode,
            )
        }
    )
    app.session_state["last_key"] = key
    return app.run()


def offline_assessment(mode: Mode = Mode.PARAGRAPH):
    return sa.analyse("/nonexistent.wav", REFERENCE, mode)


def test_a_result_renders_the_score_breakdown(run_app) -> None:
    """#11/#12: Pronunciation headline, Completeness, then Accuracy/Fluency/Prosody bars."""
    app = seed_result(run_app(), offline_assessment())
    assert not app.exception

    # Of the scores, only Completeness stays a plain st.metric — Pronunciation is now a
    # banded headline number and Accuracy/Fluency/Prosody are the "Score breakdown" bars,
    # neither of which is an st.metric widget. nPVI used to appear beside it and went with
    # the rhythm section on 2026-08-25: it needed a native rendering of the same passage
    # through the same pipeline to compare against, and that pipeline is gone.
    labels = [m.label for m in app.metric]
    assert labels == ["Completeness"]
    # 100, not the 85 Azure sent. Completeness is RECOMPUTED from the local miscue diff
    # since scripted assessment went continuous-only on 2026-08-25, and the fixture's
    # recognised words cover its reference text. Azure's own figure is no longer taken as-is
    # on any path — see `speech_analyzer.normalise`.
    assert app.metric[0].value == "100", "recomputed from the diff, not Azure's own number"

    rendered = " ".join(m.value for m in app.markdown)
    assert "Score breakdown" in rendered
    # Fixture values from tests/fixtures/sample_azure_response.json: pron 83.0 (good, 80-89),
    # accuracy 89.0 (good), fluency 88.0 (good), prosody 76.4 (fair, 60-79).
    assert "83" in rendered
    for label, value in (
        ("Accuracy score", "89"),
        ("Fluency score", "88"),
        ("Prosody score", "76"),
    ):
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
    """#10/#12: the committed fixture carries real Mispronunciation words — no synthetic
    payload needed to prove the headline count is wired up."""
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
    # "stretches", not "Monotone": the badge counts flat passages, not the words inside them.
    # With none to count there is no word total to put in brackets either.
    assert ">0</span><span>Monotone stretches</span>" in rendered


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
        tts,
        "synthesise",
        lambda text, **kw: tts.Synthesis(
            audio=b"WAV", characters=len(text), voice="en-US-BrianNeural", attempts=1
        ),
    )

    cache: OrderedDict = OrderedDict()
    state: dict = {}
    monkeypatch.setattr(app_module, "_session_cache", lambda _name: cache)
    monkeypatch.setattr(app_module.st, "session_state", state, raising=False)

    for _ in range(4):
        app_module.play(conn, "weather", slow=False, label="test", source="word-0")

    assert db.monthly_tts_characters(conn) == len("weather"), "charged once, not four times"
    assert state["now_playing"] == {
        "key": ("en-US-BrianNeural", "weather", False),
        "source": "word-0",
    }
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

    app_module.lru_get(cache, "key0")  # re-used, so it must survive
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
    assessment = sa.analyse("/nonexistent.wav", REFERENCE, Mode.PARAGRAPH)
    assessment.attempts = 3

    db.record_attempt(
        conn,
        mode=Mode.PARAGRAPH,
        reference_text=REFERENCE,
        recognised_text=assessment.recognised_text,
        audio_seconds=12.0 * max(assessment.attempts, 1),
        audio_sha256=utils.sha256_bytes(b"x"),
        overall_scores=assessment.overall_scores,
        azure_raw=assessment.raw[0],
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
    _icon, message = outcome
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
    assessment.words = [
        dict(
            assessment.words[0],
            word="unpredictable",
            accuracy=None,
            error_type="Omission",
            error_source="local_diff",
            phonemes=[],
            syllables=[],
        )
    ]
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
    assessment.words.append(
        {
            "word": "clouds",
            "accuracy": 96.0,
            "error_type": "None",
            "error_source": "azure",
            "delivery_error_types": ["Monotone"],
            "prosody_detail": {"break_length_ms": None, "monotone_confidence": 0.88},
            "syllables": [],
            "phonemes": [],
        }
    )

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
        assessment.words.append(
            {
                "word": f"word{index}",
                "accuracy": 96.0,
                "error_type": "None",
                "error_source": "azure",
                "delivery_error_types": ["Monotone"],
                "prosody_detail": {"break_length_ms": None, "monotone_confidence": 0.9},
                "syllables": [],
                "phonemes": [],
            }
        )

    app = seed_result(run_app(), assessment)

    # `render_fix` uses the same "In this attempt:" caption, so pick the delivery one.
    caption = next(c.value for c in app.caption if "word0" in c.value)
    assert "and 8 more" in caption
    assert "word19" not in caption


def test_the_delivery_panel_shows_what_azure_measured(run_app) -> None:
    """Synthetic. The panel and the coaching section read the same helper, so they cannot
    disagree about the number — which is the reason the panel quotes one at all."""
    assessment = offline_assessment()
    assessment.words.append(
        {
            "word": "clouds",
            "accuracy": 96.0,
            "error_type": "None",
            "error_source": "azure",
            "delivery_error_types": ["Monotone"],
            "prosody_detail": {"break_length_ms": None, "monotone_confidence": 0.88},
            "syllables": [],
            "phonemes": [],
        }
    )

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


def test_a_visible_note_says_where_the_coaching_came_from(run_app) -> None:
    app = seed_result(run_app(), offline_assessment())
    captions = " ".join(c.value for c in app.caption)
    assert "Azure data alone" in captions
    assert "nothing sent anywhere" in captions.lower()


def test_the_coaching_has_no_button_and_costs_nothing(run_app) -> None:
    """Gemini stopped writing coaching on 2026-08-25. Nothing on this panel is buyable."""
    app = seed_result(run_app(), offline_assessment())
    labels = [b.label for b in app.button]
    assert not any("Improve this" in label for label in labels)
    assert any("Mark up the passage" in label for label in labels), (
        "the annotation is the one paid button left, and it is a separate panel"
    )


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
        conn,
        mode=Mode.PARAGRAPH,
        reference_text=REFERENCE,
        recognised_text="x",
        audio_seconds=1.0,
        audio_sha256="deadbeef",
        overall_scores={},
        azure_raw={},
        offline=True,
    )
    seed_result(run_app(), offline_assessment(), attempt_id=attempt_id)

    row = db.get_attempt(conn, attempt_id)
    assert row is not None
    assert row["coach_source"] == fallback_coach.SOURCE_FALLBACK
    assert row["gemini_raw_json"], "the report itself is stored on the offline path"


def test_the_report_is_not_rebuilt_on_every_rerun(run_app) -> None:
    """Streamlit re-runs the whole script on every click; a model call here would re-spend."""
    app = seed_result(run_app(), offline_assessment())
    cached = app.session_state["coaching"]
    assert len(cached) == 1
    app.run()
    assert app.session_state["coaching"] is cached


def _online(monkeypatch: pytest.MonkeyPatch) -> None:
    """Everything `annotate` and `check_startup` need to consider the model path usable."""
    monkeypatch.setenv("OFFLINE_MODE", "false")
    monkeypatch.setenv("GEMINI_API_KEY", "placeholder-not-a-real-key")
    monkeypatch.setenv("AZURE_SPEECH_KEY", "placeholder")
    monkeypatch.setenv("AZURE_SPEECH_REGION", "eastus")
    monkeypatch.setenv("AZURE_TIER_CONFIRMED_F0", "true")


def _annotation(words: list[str]):
    import ai_coach

    return ai_coach.ProsodyAnnotation.model_validate(
        {
            "words": [
                {"word": word, "stress": index == 0, "break_after": "none", "linked": False}
                for index, word in enumerate(words)
            ],
            "summary": "Lift the pitch across the last phrase.",
        }
    )


def test_clicking_the_button_renders_the_annotated_passage(run_app, monkeypatch, tmp_path) -> None:
    """The click path, without a live call: app.py and this test share one ai_coach module."""
    import ai_coach

    assessment = offline_assessment()
    conn = db.connect(str(tmp_path / "coach.db"))
    attempt_id = db.record_attempt(
        conn,
        mode=Mode.PARAGRAPH,
        reference_text=REFERENCE,
        recognised_text="x",
        audio_seconds=1.0,
        audio_sha256="deadbeef",
        overall_scores={},
        azure_raw={},
        offline=True,
    )

    annotation = _annotation(REFERENCE.split())
    monkeypatch.setattr(
        ai_coach,
        "annotate",
        lambda *a, **k: ai_coach.AnnotationResult(
            annotation=annotation, raw=annotation.model_dump()
        ),
    )
    _online(monkeypatch)

    app = seed_result(
        run_app(DB_PATH=str(tmp_path / "coach.db")), assessment, attempt_id=attempt_id
    )
    button = next(b for b in app.button if "Mark up the passage" in b.label)
    assert not button.disabled
    app = button.click().run()

    assert not app.exception
    rendered = " ".join(m.value for m in app.markdown)
    assert "Lift the pitch across the last phrase" in rendered
    # The first word, marked as stressed, reaches the page inside the annotation block.
    assert REFERENCE.split()[0] in rendered
    assert db.annotation_for(conn, attempt_id) is not None


def test_a_stored_annotation_is_re_rendered_rather_than_re_asked(
    run_app, monkeypatch, tmp_path
) -> None:
    """Re-opening an attempt must not spend a call for something already bought."""
    import ai_coach

    assessment = offline_assessment()
    conn = db.connect(str(tmp_path / "coach.db"))
    attempt_id = db.record_attempt(
        conn,
        mode=Mode.PARAGRAPH,
        reference_text=REFERENCE,
        recognised_text="x",
        audio_seconds=1.0,
        audio_sha256="deadbeef",
        overall_scores={},
        azure_raw={},
        offline=True,
    )
    db.attach_annotation(conn, attempt_id, raw=_annotation(REFERENCE.split()).model_dump())
    conn.close()

    calls: list[int] = []

    def never_called(*args, **kwargs):
        calls.append(1)
        return ai_coach.AnnotationResult()

    monkeypatch.setattr(ai_coach, "annotate", never_called)
    _online(monkeypatch)

    app = seed_result(
        run_app(DB_PATH=str(tmp_path / "coach.db")), assessment, attempt_id=attempt_id
    )
    rendered = " ".join(m.value for m in app.markdown)
    assert "Lift the pitch across the last phrase" in rendered
    assert calls == [], "a stored annotation is a re-parse, never another call"


def test_a_second_click_cannot_spend_another_call(run_app, monkeypatch, tmp_path) -> None:
    """Once the model has answered for this attempt, the button is spent."""
    import ai_coach

    assessment = offline_assessment()
    annotation = _annotation(REFERENCE.split())
    calls: list[int] = []

    def once(*args, **kwargs):
        calls.append(1)
        return ai_coach.AnnotationResult(annotation=annotation, raw=annotation.model_dump())

    monkeypatch.setattr(ai_coach, "annotate", once)
    _online(monkeypatch)

    app = seed_result(run_app(), assessment)
    app = next(b for b in app.button if "Mark up the passage" in b.label).click().run()
    assert len(calls) == 1

    # The click is handled in the pass that rendered the button, so the button on screen is
    # still enabled: clicking it again must cost nothing rather than buying a second call.
    app = next(b for b in app.button if "Mark up the passage" in b.label).click().run()
    assert len(calls) == 1, "a second click must not re-spend"
    assert next(b for b in app.button if "Mark up the passage" in b.label).disabled
    app.run()
    assert len(calls) == 1, "and neither must an unrelated rerun"


def test_a_spent_call_that_produced_nothing_cannot_be_re_clicked(run_app, monkeypatch) -> None:
    """The re-spend hole: a real call that came back unusable used to leave the button live.

    A malformed answer, or one whose word sequence failed validation, still consumed the
    free-tier call. Keying the guard off the outcome would mean exactly the failures worth
    not repeating are the repeatable ones.
    """
    import ai_coach

    assessment = offline_assessment()
    calls: list[int] = []

    def spent_but_rejected(*args, **kwargs):
        calls.append(1)
        return ai_coach.AnnotationResult(reason="The model changed the wording.")

    monkeypatch.setattr(ai_coach, "annotate", spent_but_rejected)
    _online(monkeypatch)

    app = seed_result(run_app(), assessment)
    app = next(b for b in app.button if "Mark up the passage" in b.label).click().run()
    assert len(calls) == 1
    assert any("changed the wording" in i.value for i in app.info)

    # The click is handled in the pass that drew the button, so the button on screen is
    # still enabled — which is exactly why the guard cannot live on the disabled flag.
    app = next(b for b in app.button if "Mark up the passage" in b.label).click().run()
    assert len(calls) == 1, "a rejected outcome must not be re-buyable"
    assert next(b for b in app.button if "Mark up the passage" in b.label).disabled, (
        "the call was spent even though nothing usable came back"
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
        cancel_event=threading.Event(),
        key="k",
        reference_text=REFERENCE,
        mode=Mode.PARAGRAPH,
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


def test_a_second_assess_click_while_running_starts_nothing(
    run_app, settled_poll, monkeypatch
) -> None:
    """The state guard, not the disabled flag, is what closes the double-submit race."""
    started: list[int] = []
    monkeypatch.setattr(app_module, "start_assessment", lambda *a, **k: started.append(1))

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
        cancel_event=threading.Event(),
        key="k",
        reference_text=REFERENCE,
        mode=Mode.PARAGRAPH,
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
        cancel_event=threading.Event(),
        key="k",
        reference_text=REFERENCE,
        mode=Mode.PARAGRAPH,
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
    assessment.words.append(
        {
            "word": "clouds",
            "accuracy": 100.0,
            "error_type": "None",
            "error_source": "azure",
            "delivery_error_types": ["Monotone"],
            "syllables": [],
            "phonemes": [],
        }
    )

    app = seed_result(run_app(), assessment)

    assert not app.exception
    assert any("Scored 100 but still flagged" in e.label for e in app.expander), (
        "a perfect-scoring word flagged for delivery belongs behind a collapsed panel"
    )


def test_a_collapsed_word_still_gets_a_unique_playback_key(run_app) -> None:
    """The word index keeps counting across both groups; a repeated key is a hard error."""
    assessment = offline_assessment()
    for word in ("clouds", "thunder"):
        assessment.words.append(
            {
                "word": word,
                "accuracy": 100.0,
                "error_type": "None",
                "error_source": "azure",
                "delivery_error_types": ["Monotone"],
                "syllables": [],
                "phonemes": [],
            }
        )

    app = seed_result(run_app(), assessment)

    assert not app.exception, "duplicate widget keys raise rather than render"


# --- Everything Azure returned ------------------------------------------------------------


def test_the_per_word_azure_detail_is_rendered(run_app) -> None:
    """ "Show me everything Azure said" has to be true of this page, not nearly true."""
    app = seed_result(run_app(), offline_assessment())
    labels = [e.label for e in app.expander]
    assert any("Everything Azure returned for this word" in label for label in labels)
    assert any("Everything Azure returned for this attempt" in label for label in labels)


def test_the_detail_names_a_score_azure_did_not_return(run_app) -> None:
    """A silently absent row reads as a rendering bug, not as a fact about the attempt."""
    assessment = offline_assessment()
    assessment.overall_scores["prosody"] = None
    app = seed_result(run_app(), assessment)
    rendered = " ".join(m.value for m in app.markdown)
    assert "Not returned by Azure" in rendered
    assert "prosody" in rendered


def test_a_word_clip_is_cut_at_azures_own_offsets() -> None:
    """The "how I said it" half of a flagged word. One second of speech, one word's span."""
    import audio_utils

    recording, _ = audio_utils.prepare(_wav_bytes(2.0))
    clip = app_module.word_clip(recording, {"start_s": 0.5, "end_s": 1.0})
    assert clip is not None
    assert audio_utils.duration_seconds(clip) == pytest.approx(0.54, abs=0.05)


def test_a_word_with_no_span_has_no_clip() -> None:
    """An omitted word was never spoken, so there is nothing of yours to play."""
    import audio_utils

    recording, _ = audio_utils.prepare(_wav_bytes(2.0))
    assert app_module.word_clip(recording, {"start_s": None, "end_s": None}) is None
    assert app_module.word_clip(recording, {}) is None


def test_no_recording_means_no_clip_rather_than_an_error() -> None:
    """A gitignored file can legitimately be gone, months after the attempt."""
    assert app_module.word_clip(None, {"start_s": 0.0, "end_s": 1.0}) is None


def test_a_span_past_the_end_of_the_recording_has_no_clip() -> None:
    import audio_utils

    recording, _ = audio_utils.prepare(_wav_bytes(2.0))
    assert app_module.word_clip(recording, {"start_s": 30.0, "end_s": 31.0}) is None


def _wav_bytes(seconds: float) -> bytes:
    """A real WAV of a quiet sine, so pydub has something genuine to decode."""
    import math
    import struct
    import wave

    rate = 16_000
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(
            b"".join(
                struct.pack("<h", int(6000 * math.sin(2 * math.pi * 220 * n / rate)))
                for n in range(int(seconds * rate))
            )
        )
    return buffer.getvalue()


# --- The History tab ------------------------------------------------------------------------


def seed_history(app: AppTest, count: int, *, mode: Mode = Mode.PARAGRAPH) -> list[int]:
    """Real attempt rows in the app's own database, the way an assessment would leave them."""
    conn = app_module.get_connection()
    return [
        db.record_attempt(
            conn,
            mode=mode,
            reference_text=f"Reading number {n}",
            recognised_text="x",
            audio_seconds=12.0,
            audio_sha256=f"hash{n}",
            overall_scores={"pron_score": 80.0 + n},
            azure_raw=offline_assessment().raw,
            created_at=f"2026-08-{n + 1:02d}T08:00:00Z",
        )
        for n in range(count)
    ]


def test_the_page_has_exactly_two_tabs(run_app) -> None:
    """Analyze and History. Anything else is scope this project deleted on purpose."""
    app = run_app()
    assert not app.exception
    assert [t.label for t in app.tabs] == ["Analyze", "History"]


def test_an_empty_history_says_so_rather_than_rendering_a_blank(run_app) -> None:
    app = run_app()
    assert any("Nothing recorded yet" in i.value for i in app.info)


def test_history_lists_what_was_recorded_newest_first(run_app) -> None:
    app = run_app()
    seed_history(app, 3)
    app.run()
    rendered = " ".join(m.value for m in app.markdown)
    assert "3 attempts" in " ".join(c.value for c in app.caption)
    assert "Reading number 2" in rendered


def test_history_paginates_rather_than_listing_everything(run_app) -> None:
    app = run_app()
    seed_history(app, app_module.HISTORY_PAGE_SIZE + 3)
    app.run()

    opens = [b for b in app.button if b.label == "Open"]
    assert len(opens) == app_module.HISTORY_PAGE_SIZE
    assert any("page 1 of 2" in c.value for c in app.caption)

    app = next(b for b in app.button if "Older" in b.label).click().run()
    assert not app.exception
    assert len([b for b in app.button if b.label == "Open"]) == 3
    assert any("page 2 of 2" in c.value for c in app.caption)


def test_offline_replays_appear_in_history_and_are_labelled(run_app) -> None:
    """A fixture replay is a real row a real click produced. Hiding it made History lie."""
    app = run_app()
    conn = app_module.get_connection()
    db.record_attempt(
        conn,
        mode=Mode.PARAGRAPH,
        reference_text="A replayed reading",
        recognised_text="x",
        audio_seconds=1.0,
        audio_sha256="h",
        overall_scores={},
        azure_raw=offline_assessment().raw,
        offline=True,
    )
    app.run()
    captions = " ".join(c.value for c in app.caption)
    assert "A replayed reading" in " ".join(m.value for m in app.markdown)
    assert "fixture replay" in captions


def test_the_mode_filter_narrows_the_list(run_app) -> None:
    app = run_app()
    seed_history(app, 2)
    seed_history(app, 1, mode=Mode.UNSCRIPTED)
    app.run()
    assert any("3 attempts" in c.value for c in app.caption)

    app = app.radio(key=app_module.HISTORY_MODE_KEY).set_value("Unscripted").run()
    assert not app.exception
    assert any("1 attempt " in c.value or "1 attempt·" in c.value for c in app.caption)


def test_a_legacy_drill_row_still_renders_and_says_how_it_was_recorded(run_app) -> None:
    """Rows written before 2026-08-25 carry `mode = 'drill'`, which the enum no longer has."""
    app = run_app()
    conn = app_module.get_connection()
    attempt_id = seed_history(app, 1)[0]
    conn.execute("UPDATE attempts SET mode = 'drill' WHERE id = ?", (attempt_id,))
    conn.commit()
    app.run()

    assert not app.exception, "Mode('drill') would raise and take the page down"
    assert "recorded as drill" in " ".join(c.value for c in app.caption)

    app = next(b for b in app.button if b.label == "Open").click().run()
    assert not app.exception
    assert any("Score breakdown" in m.value for m in app.markdown)


def test_opening_an_attempt_renders_the_result_without_the_inputs(run_app) -> None:
    app = run_app()
    seed_history(app, 1)
    app.run()
    app = next(b for b in app.button if b.label == "Open").click().run()

    assert not app.exception
    rendered = " ".join(m.value for m in app.markdown)
    assert "Score breakdown" in rendered
    # The Analyze tab still draws its own inputs — this asserts History added none of its own,
    # and that the two tabs did not both render a result and collide on a widget key.
    assert len(app.file_uploader) == 1
    assert len(app.text_area) == 1
    assert any("Back to the list" in b.label for b in app.button)


def test_opening_an_attempt_stands_the_analyze_result_down(run_app) -> None:
    """Both tab bodies run on every rerun, and two `render_result`s collide on widget keys."""
    app = seed_result(run_app(), offline_assessment())
    seed_history(app, 1)
    app.run()
    app = next(b for b in app.button if b.label == "Open").click().run()
    assert not app.exception
    assert app.session_state["last_key"] is None


def test_closing_an_opened_attempt_returns_to_the_list(run_app) -> None:
    app = run_app()
    seed_history(app, 2)
    app.run()
    app = next(b for b in app.button if b.label == "Open").click().run()
    app = next(b for b in app.button if "Back to the list" in b.label).click().run()
    assert not app.exception
    assert len([b for b in app.button if b.label == "Open"]) == 2


def test_an_unreadable_stored_payload_says_so_instead_of_crashing(run_app) -> None:
    """One bad row must not take the page down; the row itself is still intact."""
    app = run_app()
    attempt_id = seed_history(app, 1)[0]
    conn = app_module.get_connection()
    conn.execute("UPDATE attempts SET azure_raw_json = ? WHERE id = ?", ("{not json", attempt_id))
    conn.commit()
    app.run()
    app = next(b for b in app.button if b.label == "Open").click().run()

    assert not app.exception
    assert any("could not be read" in e.value for e in app.error)


def test_deleting_asks_before_it_deletes(run_app) -> None:
    """Nothing in this app could destroy history before, so the click has to be deliberate."""
    app = run_app()
    seed_history(app, 1)
    app.run()

    app = next(b for b in app.button if "Delete" in b.label).click().run()
    assert any("cannot be undone" in w.value for w in app.warning)
    assert app_module.db.attempt_count(app_module.get_connection()) == 1

    app = next(b for b in app.button if "Really delete" in b.label).click().run()
    assert not app.exception
    assert app_module.db.attempt_count(app_module.get_connection()) == 0
    assert any("Nothing recorded yet" in i.value for i in app.info)


def test_deleting_removes_the_recording_from_disk_too(run_app, tmp_path) -> None:
    app = run_app()
    attempt_id = seed_history(app, 1)[0]
    conn = app_module.get_connection()
    recording = tmp_path / "kept.wav"
    recording.write_bytes(_wav_bytes(0.5))
    db.record_audio(
        conn,
        attempt_id,
        path=str(recording),
        sha256="h",
        size_bytes=recording.stat().st_size,
        sample_rate=16_000,
    )
    app.run()

    app = next(b for b in app.button if "Delete" in b.label).click().run()
    app = next(b for b in app.button if "Really delete" in b.label).click().run()

    assert not app.exception
    assert not recording.exists()
    assert db.audio_for(conn, attempt_id) is None
