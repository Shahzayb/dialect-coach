"""Pre-flight guard: tier acknowledgement, two meters, two-pass, and 403 authority."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

import budget
import db
from budget import BudgetError, TierNotAcknowledged
from utils import Mode

WHEN = datetime(2026, 8, 18, tzinfo=UTC)


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = db.connect(":memory:")
    yield connection
    connection.close()


@pytest.fixture(autouse=True)
def clean_exhausted_flag() -> None:
    budget.reset_exhausted_flag()
    yield
    budget.reset_exhausted_flag()


@pytest.fixture
def online(monkeypatch: pytest.MonkeyPatch) -> None:
    """Leave OFFLINE_MODE, since offline short-circuits every guard by design."""
    monkeypatch.setenv("OFFLINE_MODE", "false")
    monkeypatch.setenv("MONTHLY_BUDGET_USD", "0.00")
    monkeypatch.setenv("AZURE_TIER_CONFIRMED_F0", "true")
    monkeypatch.setenv("AZURE_FREE_STT_SECONDS", "18000")
    monkeypatch.setenv("AZURE_FREE_TTS_CHARS", "500000")
    monkeypatch.setenv("AZURE_STT_USD_PER_HOUR", "1.00")
    monkeypatch.setenv("AZURE_PRON_ADDON_USD_PER_HOUR", "0.30")
    monkeypatch.setenv("AZURE_TTS_USD_PER_MILLION_CHARS", "16.00")
    monkeypatch.setenv("UNSCRIPTED_TWO_PASS", "true")


def fill_stt(conn: sqlite3.Connection, seconds: float) -> None:
    db.record_attempt(
        conn,
        mode=Mode.DRILL,
        reference_text="x",
        recognised_text="x",
        audio_seconds=seconds,
        audio_sha256="h",
        overall_scores={},
        azure_raw={},
        created_at="2026-08-05T00:00:00Z",
    )


# --- Tier acknowledgement -----------------------------------------------------------------


def test_zero_budget_requires_f0_confirmation(
    monkeypatch: pytest.MonkeyPatch, online: None
) -> None:
    monkeypatch.setenv("AZURE_TIER_CONFIRMED_F0", "false")
    with pytest.raises(TierNotAcknowledged, match="F0"):
        budget.require_f0_acknowledgement()


def test_offline_never_requires_f0_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    """The zero-cost path must not be harder to use than the paid one."""
    monkeypatch.setenv("OFFLINE_MODE", "true")
    monkeypatch.setenv("AZURE_TIER_CONFIRMED_F0", "false")
    monkeypatch.setenv("MONTHLY_BUDGET_USD", "0.00")
    budget.require_f0_acknowledgement()  # must not raise


def test_a_nonzero_budget_needs_no_acknowledgement(
    monkeypatch: pytest.MonkeyPatch, online: None
) -> None:
    monkeypatch.setenv("AZURE_TIER_CONFIRMED_F0", "false")
    monkeypatch.setenv("MONTHLY_BUDGET_USD", "5.00")
    budget.require_f0_acknowledgement()


def test_tier_message_never_leaks_a_key(monkeypatch: pytest.MonkeyPatch, online: None) -> None:
    monkeypatch.setenv("AZURE_SPEECH_KEY", "sk-secret-value-0123456789")
    monkeypatch.setenv("AZURE_TIER_CONFIRMED_F0", "false")
    with pytest.raises(TierNotAcknowledged) as excinfo:
        budget.require_f0_acknowledgement()
    assert "sk-secret-value-0123456789" not in str(excinfo.value)


# --- Pre-flight ----------------------------------------------------------------------------


def test_within_the_free_allowance_costs_nothing(conn: sqlite3.Connection, online: None) -> None:
    fill_stt(conn, 100.0)
    budget.preflight_stt(conn, 25.0, Mode.DRILL, WHEN)  # must not raise


def test_a_call_past_the_free_allowance_is_refused(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, online: None
) -> None:
    monkeypatch.setenv("AZURE_FREE_STT_SECONDS", "120")
    fill_stt(conn, 119.0)
    with pytest.raises(BudgetError, match="Refusing"):
        budget.preflight_stt(conn, 30.0, Mode.DRILL, WHEN)


def test_the_guard_is_preflight_not_posthoc(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, online: None
) -> None:
    """Nothing is recorded yet, but the *next* call is already known to be too expensive."""
    monkeypatch.setenv("AZURE_FREE_STT_SECONDS", "10")
    assert db.monthly_stt_seconds(conn, WHEN) == 0.0
    with pytest.raises(BudgetError):
        budget.preflight_stt(conn, 300.0, Mode.UNSCRIPTED, WHEN)


def test_offline_skips_the_guard_entirely(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OFFLINE_MODE", "true")
    monkeypatch.setenv("AZURE_FREE_STT_SECONDS", "1")
    budget.preflight_stt(conn, 9999.0, Mode.UNSCRIPTED, WHEN)


def test_two_pass_unscripted_charges_twice(online: None) -> None:
    assert budget.passes_for(Mode.DRILL) == 1
    assert budget.passes_for(Mode.PARAGRAPH) == 1
    assert budget.passes_for(Mode.UNSCRIPTED) == 2


def test_two_pass_can_be_turned_off(monkeypatch: pytest.MonkeyPatch, online: None) -> None:
    monkeypatch.setenv("UNSCRIPTED_TWO_PASS", "false")
    assert budget.passes_for(Mode.UNSCRIPTED) == 1


def test_two_pass_doubles_what_the_guard_counts(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, online: None
) -> None:
    monkeypatch.setenv("AZURE_FREE_STT_SECONDS", "150")
    budget.preflight_stt(conn, 100.0, Mode.PARAGRAPH, WHEN)  # 100 s, fits
    with pytest.raises(BudgetError, match="twice"):
        budget.preflight_stt(conn, 100.0, Mode.UNSCRIPTED, WHEN)  # 200 s, does not


def test_cost_estimate_rounds_up() -> None:
    """Erring toward 'less remaining than you think' is the correct direction."""
    assert budget.stt_cost_usd(3600.5) > budget.stt_cost_usd(3600.0)


def test_stt_cost_includes_the_pronunciation_addon(online: None) -> None:
    # 1 hour at $1.00 base + $0.30 assessment add-on.
    assert budget.stt_cost_usd(3600.0) == pytest.approx(1.30)


# --- Azure is authoritative -----------------------------------------------------------------


def test_a_403_blocks_further_calls_regardless_of_the_meter(
    conn: sqlite3.Connection, online: None
) -> None:
    budget.preflight_stt(conn, 10.0, Mode.DRILL, WHEN)  # meter says there is plenty left
    budget.mark_quota_exhausted(WHEN)
    with pytest.raises(BudgetError, match="exhausted"):
        budget.preflight_stt(conn, 10.0, Mode.DRILL, WHEN)


def test_the_exhausted_flag_does_not_carry_into_the_next_month(
    conn: sqlite3.Connection, online: None
) -> None:
    budget.mark_quota_exhausted(WHEN)
    next_month = datetime(2026, 9, 1, tzinfo=UTC)
    budget.preflight_stt(conn, 10.0, Mode.DRILL, next_month)  # must not raise


# --- Meters -----------------------------------------------------------------------------


def test_the_two_meters_are_independent(conn: sqlite3.Connection, online: None) -> None:
    fill_stt(conn, 60.0)
    db.record_tts_usage(conn, characters=900, created_at="2026-08-06T00:00:00Z")
    assert budget.stt_meter(conn, WHEN).used == 60.0
    assert budget.tts_meter(conn, WHEN).used == 900.0


def test_summary_line_says_it_is_an_estimate(conn: sqlite3.Connection, online: None) -> None:
    line = budget.summary_line(conn, WHEN)
    assert "estimate" in line and "Azure portal is authoritative" in line
