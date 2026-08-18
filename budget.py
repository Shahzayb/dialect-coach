"""Pre-flight spend guard over the SQLite-derived usage meters.

Read this first: **the real $0 guarantee is at the account layer, not here.** An Azure F0
resource physically cannot bill — it returns 403 once the monthly allowance is gone — and
a Gemini key from a project with no billing account returns 429 rather than a charge.
Creating an S0 resource by mistake is the only way this project costs money.

This module is a second line of defence against your own misconfiguration. It must never
be presented in the UI as though it guarantees anything.
"""

from __future__ import annotations

import logging
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

import db
import utils
from utils import Mode

logger = logging.getLogger(__name__)

SECONDS_PER_HOUR = 3600.0

# Set when Azure itself says the month is gone. The provider is authoritative; a local
# estimate that disagrees is the one that is wrong. Cleared on the next UTC month.
_exhausted_month: str | None = None


class BudgetError(RuntimeError):
    """The next call is refused. Message is safe to show in the UI."""


class TierNotAcknowledged(BudgetError):
    """Running at a $0 budget without confirming the Speech resource is F0."""


@dataclass(frozen=True)
class Meter:
    """One free bucket: how much is used, how much is free, what overage would cost."""

    label: str
    used: float
    free_allowance: float
    unit: str

    @property
    def remaining(self) -> float:
        return max(0.0, self.free_allowance - self.used)

    @property
    def exhausted(self) -> bool:
        return self.used >= self.free_allowance


def stt_meter(conn: sqlite3.Connection, when: datetime | None = None) -> Meter:
    return Meter(
        label="Speech-to-text",
        used=db.monthly_stt_seconds(conn, when),
        free_allowance=utils.get_float("AZURE_FREE_STT_SECONDS"),
        unit="seconds",
    )


def tts_meter(conn: sqlite3.Connection, when: datetime | None = None) -> Meter:
    return Meter(
        label="Text-to-speech",
        used=float(db.monthly_tts_characters(conn, when)),
        free_allowance=utils.get_float("AZURE_FREE_TTS_CHARS"),
        unit="characters",
    )


def passes_for(mode: Mode) -> int:
    """How many times one recording is sent to Azure.

    Mode C's two-pass flow (standard STT for an accurate transcript, then scripted
    assessment against it) charges the audio twice. The multiplier lives here so the guard
    is right the moment Mode C is implemented, rather than under-counting on day one.
    """
    if mode is Mode.UNSCRIPTED and utils.get_bool("UNSCRIPTED_TWO_PASS"):
        return 2
    return 1


def stt_cost_usd(seconds: float) -> float:
    """Overage cost of `seconds` of assessed audio, rounded up to the billed hour-fraction.

    Pronunciation assessment is an add-on charged alongside the base STT rate, so both
    apply. Always rounds the estimate up: erring toward "less remaining than you think" is
    the correct direction for a guard.
    """
    per_hour = (
        utils.get_float("AZURE_STT_USD_PER_HOUR")
        + utils.get_float("AZURE_PRON_ADDON_USD_PER_HOUR")
    )
    billable_seconds = math.ceil(seconds)
    return (billable_seconds / SECONDS_PER_HOUR) * per_hour


def tts_cost_usd(characters: int) -> float:
    per_million = utils.get_float("AZURE_TTS_USD_PER_MILLION_CHARS")
    return (math.ceil(characters) / 1_000_000.0) * per_million


def _month_is_exhausted(when: datetime | None = None) -> bool:
    return _exhausted_month == db.month_prefix(when)


def mark_quota_exhausted(when: datetime | None = None) -> None:
    """Record that Azure returned 403. Overrides the local estimate for the rest of the month."""
    global _exhausted_month
    _exhausted_month = db.month_prefix(when)
    logger.warning("Azure reported the monthly quota exhausted; blocking further calls.")


def reset_exhausted_flag() -> None:
    """Test hook, and the manual escape hatch if a 403 turns out to have been transient."""
    global _exhausted_month
    _exhausted_month = None


def require_f0_acknowledgement() -> None:
    """Refuse to run at a $0 budget unless the F0 tier has been confirmed by a human.

    The SDK cannot read the resource SKU, so an explicit acknowledgement is the only
    available check. Skipped offline: nothing is being spent, and gating the zero-cost path
    behind a tier confirmation would make it harder to use than the paid one.
    """
    if utils.offline_mode():
        return
    if utils.get_float("MONTHLY_BUDGET_USD") > 0:
        return
    if utils.get_bool("AZURE_TIER_CONFIRMED_F0"):
        return
    raise TierNotAcknowledged(
        "MONTHLY_BUDGET_USD is 0.00, so this app will not call Azure until you confirm "
        "your Speech resource is on the free F0 tier and not S0 — an S0 resource bills "
        "for every call. Check the resource's pricing tier in the Azure portal, then set "
        "AZURE_TIER_CONFIRMED_F0=true in .env. To work without any API calls at all, set "
        "OFFLINE_MODE=true instead."
    )


def preflight_stt(conn: sqlite3.Connection, seconds: float, mode: Mode,
                  when: datetime | None = None) -> None:
    """Refuse the *next* STT call if it would push spend past the budget.

    Pre-flight, not post-hoc: duration is known before the call, so an overspend is never
    something to discover afterwards. Counts the attempt, not the success — a call that
    reaches Azure and then fails may still consume allowance.
    """
    if utils.offline_mode():
        return

    require_f0_acknowledgement()

    if _month_is_exhausted(when):
        raise BudgetError(
            "Azure reported this month's free allowance as exhausted. It resets at the "
            "start of the next UTC month."
        )

    billed_seconds = seconds * passes_for(mode)
    meter = stt_meter(conn, when)
    budget = utils.get_float("MONTHLY_BUDGET_USD")

    # Only the portion beyond the free allowance costs anything.
    overage_seconds = max(0.0, (meter.used + billed_seconds) - meter.free_allowance)
    already_charged = max(0.0, meter.used - meter.free_allowance)
    projected_cost = stt_cost_usd(overage_seconds) - stt_cost_usd(already_charged)

    if projected_cost > budget:
        pass_note = " (Mode C sends the audio twice)" if passes_for(mode) > 1 else ""
        raise BudgetError(
            f"Refusing this {seconds:.0f}s attempt{pass_note}: it would use "
            f"{billed_seconds:.0f}s against a free allowance with "
            f"{meter.remaining:.0f}s left, costing about ${projected_cost:.2f} — over the "
            f"${budget:.2f} monthly budget. Raise MONTHLY_BUDGET_USD, or wait for the "
            f"allowance to reset next UTC month."
        )


def preflight_tts(conn: sqlite3.Connection, characters: int,
                  when: datetime | None = None) -> None:
    """The same guard for the separate TTS bucket. Unused until the TTS chunk lands."""
    if utils.offline_mode():
        return

    require_f0_acknowledgement()

    meter = tts_meter(conn, when)
    budget = utils.get_float("MONTHLY_BUDGET_USD")

    overage = max(0.0, (meter.used + characters) - meter.free_allowance)
    already_charged = max(0.0, meter.used - meter.free_allowance)
    projected_cost = tts_cost_usd(int(overage)) - tts_cost_usd(int(already_charged))

    if projected_cost > budget:
        raise BudgetError(
            f"Refusing to synthesise {characters} characters: about ${projected_cost:.2f}, "
            f"over the ${budget:.2f} monthly budget. The free allowance has "
            f"{meter.remaining:.0f} characters left this month."
        )


def summary_line(conn: sqlite3.Connection, when: datetime | None = None) -> str:
    """One line for the UI. Says out loud that it is an estimate."""
    stt = stt_meter(conn, when)
    tts = tts_meter(conn, when)
    return (
        f"≈ {stt.used:.0f} of {stt.free_allowance:.0f} seconds and "
        f"{tts.used:.0f} of {tts.free_allowance:.0f} TTS characters used this month "
        f"(local estimate — the Azure portal is authoritative)"
    )
