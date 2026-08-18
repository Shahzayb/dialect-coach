"""The UI itself, run headlessly with Streamlit's AppTest.

Covers the paths a browser cannot easily reach from a test: the startup refusals, the
reference-text validation, and — by seeding the session cache — the result rendering,
including the "—" for an unavailable prosody score.
"""

from __future__ import annotations

import json

import pytest
from streamlit.testing.v1 import AppTest

import speech_analyzer as sa
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


def seed_result(app: AppTest, assessment) -> AppTest:
    """Put an assessment in the session cache the way a successful run would.

    Entries are (assessment, reference_text) so the panel renders the text the scores were
    computed against rather than whatever is in the textarea now.
    """
    from collections import OrderedDict

    import utils

    key = utils.attempt_hash(REFERENCE, b"audio")
    app.session_state["assessments"] = OrderedDict({key: (assessment, REFERENCE)})
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


def test_the_result_shows_what_azure_heard(run_app) -> None:
    app = seed_result(run_app(), offline_assessment())
    assert any("What Azure heard" in s.value for s in app.subheader)


def test_flagged_words_are_listed(run_app) -> None:
    app = seed_result(run_app(), offline_assessment())
    assert app.dataframe, "the word table should render for a fixture with mispronunciations"
    rendered = json.dumps(app.dataframe[0].value.to_dict())
    assert "Flagged by" in rendered or "Word" in rendered


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


def test_the_result_cache_evicts_least_recently_used() -> None:
    """The drill loop re-uses one entry; insertion-order eviction would drop that one."""
    from collections import OrderedDict

    import app as app_module

    cache: OrderedDict[str, tuple] = OrderedDict()
    for i in range(app_module.CACHE_LIMIT):
        app_module.cache_store(cache, f"key{i}", None, "")

    app_module.cache_fetch(cache, "key0")          # re-used, so it must survive
    app_module.cache_store(cache, "overflow", None, "")

    assert len(cache) == app_module.CACHE_LIMIT
    assert "key0" in cache
    assert "key1" not in cache, "the genuinely oldest entry is the one to drop"


def test_the_cache_returns_none_for_a_miss() -> None:
    from collections import OrderedDict

    import app as app_module

    assert app_module.cache_fetch(OrderedDict(), "nope") is None


def test_the_cache_round_trips_the_reference_text() -> None:
    from collections import OrderedDict

    import app as app_module

    cache: OrderedDict[str, tuple] = OrderedDict()
    app_module.cache_store(cache, "k", "assessment", "the text it was scored against")
    assert app_module.cache_fetch(cache, "k") == ("assessment", "the text it was scored against")


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
