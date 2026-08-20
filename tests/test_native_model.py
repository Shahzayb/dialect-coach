"""Choosing and storing the voices the General American reference is built from.

The selection rules are all refusals, and each one is a way the reference could come out
confidently wrong while still looking like a vowel chart. They are tested against a synthetic
roster rather than a live one, because the suite never touches the network — and because a
rule that only holds for today's roster is not a rule.
"""

from __future__ import annotations

import pytest

import db
import native_model
from native_model import MEN, WOMEN, RosterVoice


def voice(name: str, gender: str = "Female", kind: str = "OnlineNeural") -> RosterVoice:
    return RosterVoice(short_name=name, gender=gender, voice_type=kind)


ROSTER = [
    voice("en-US-AndrewNeural", "Male"),
    voice("en-US-BrianNeural", "Male"),
    voice("en-US-GuyNeural", "Male"),
    voice("en-US-TonyNeural", "Male"),
    voice("en-US-DavisNeural", "Male"),
    voice("en-US-EricNeural", "Male"),
    voice("en-US-AriaNeural"),
    voice("en-US-AvaNeural"),
    voice("en-US-JennyNeural"),
    voice("en-US-EmmaNeural"),
    voice("en-US-SaraNeural"),
    voice("en-US-NancyNeural"),
]


# --- What may be spent on --------------------------------------------------------------------


def test_a_child_voice_is_refused_even_though_the_api_calls_it_an_adult_woman() -> None:
    """The SDK reports no age. Verified against 1.51.1: Ana comes back plain `Female`.

    Formants scale with vocal tract length, so a child's sit well above an adult woman's.
    Dropped into the women's set she pulls its mean up and inflates its between-voice SD,
    producing a reference that is wrong in a way that still draws as a plausible vowel chart.
    Hillenbrand's own table is adult men and women only, for this exact reason.
    """
    assert not native_model.eligible(voice("en-US-AnaNeural", "Female"))
    chosen = native_model.select_voices([*ROSTER, voice("en-US-AnaNeural")], per_set=8)
    assert "en-US-AnaNeural" not in chosen[WOMEN]


@pytest.mark.parametrize(
    "name",
    [
        "en-US-AndrewMultilingualNeural",
        "en-US-Andrew3DragonHDLatestNeural",
        "en-US-AvaTurboMultilingualNeural",
    ],
)
def test_newer_voice_families_are_refused_as_an_unverified_pricing_class(name: str) -> None:
    """A cost rule inherited from `perception_trainer`, and worth sixteen times more here.

    BrianNeural is the one voice this project has actually seen billed as neural on F0. A
    capture run touches sixteen voices at once, so guessing wrong about the pricing class is
    sixteen times as expensive as guessing wrong anywhere else in the app.
    """
    assert not native_model.eligible(voice(name))


def test_a_non_neural_voice_is_refused() -> None:
    assert not native_model.eligible(voice("en-US-AriaNeural", kind="OnlineStandard"))


def test_a_voice_of_no_stated_sex_is_left_out_of_both_sets() -> None:
    """ "Neutral" has no vocal-tract-length reading, so it would tilt whichever set took it."""
    chosen = native_model.select_voices([*ROSTER, voice("en-US-NeutralNeural", "Neutral")])
    assert "en-US-NeutralNeural" not in chosen[MEN] + chosen[WOMEN]


# --- The stratification ------------------------------------------------------------------------


def test_the_two_sets_are_kept_apart_and_never_pooled() -> None:
    """Formants scale with vocal tract length; a mean of the two describes nobody."""
    chosen = native_model.select_voices(ROSTER, per_set=3)
    assert set(chosen) == {MEN, WOMEN}
    assert not set(chosen[MEN]) & set(chosen[WOMEN])
    assert len(chosen[MEN]) == 3
    assert len(chosen[WOMEN]) == 3


def test_the_curated_voices_are_taken_first() -> None:
    """Spread across voice generations is the property a reference wants, same as HVPT.

    Eight voices of one generation are one recording character wearing eight names, and the
    between-voice SD they produce understates how much real speakers differ.
    """
    preferred = ("en-US-TonyNeural", "en-US-JennyNeural")
    chosen = native_model.select_voices(ROSTER, per_set=1, preferred=preferred)
    assert chosen[MEN] == ("en-US-TonyNeural",)
    assert chosen[WOMEN] == ("en-US-JennyNeural",)


def test_the_selection_is_stable_across_runs() -> None:
    """A re-capture must not silently move the reference every stored reading is drawn against."""
    first = native_model.select_voices(ROSTER, per_set=4)
    second = native_model.select_voices(list(reversed(ROSTER)), per_set=4)
    assert first == second


def test_a_thin_roster_yields_a_thin_set_rather_than_a_padded_one() -> None:
    """The caller refuses below MIN_VOICES_PER_SET; selection never invents a voice to reach it."""
    chosen = native_model.select_voices(ROSTER[:2], per_set=8)
    assert len(chosen[MEN]) == 2
    assert chosen[WOMEN] == ()


# --- Cost, stated before it is spent -----------------------------------------------------------


def test_the_estimate_scales_with_the_text_and_the_voice_count() -> None:
    characters, seconds = native_model.estimate("x" * 975, ["a", "b"])
    assert characters == 1950
    assert seconds == pytest.approx(130.0, rel=0.01)


def test_nothing_is_captured_offline() -> None:
    """OFFLINE_MODE is the only thing standing between a capture script and a real charge."""
    conn = db.connect(":memory:")
    with pytest.raises(native_model.CaptureRefused, match="OFFLINE_MODE"):
        native_model.capture(conn, "Hello there.", "en-US-BrianNeural")


def test_an_empty_text_is_refused_before_anything_is_spent() -> None:
    conn = db.connect(":memory:")
    with pytest.raises(native_model.CaptureRefused, match="nothing to synthesise"):
        native_model.capture(conn, "   ", "en-US-BrianNeural")


# --- Storage --------------------------------------------------------------------------------


def test_a_rendering_round_trips_and_is_keyed_on_voice_and_text(tmp_path) -> None:
    conn = db.connect(":memory:")
    text = "Each morning I read these same words out loud."
    wav = tmp_path / "one.wav"
    wav.write_bytes(b"RIFF")

    db.record_native_rendering(
        conn,
        voice="en-US-BrianNeural",
        text_key=native_model.text_key(text),
        reference_text=text,
        wav_path=str(wav),
        payloads=[{"NBest": []}],
        seconds=62.0,
        characters=975,
    )
    found = native_model.rendering_for(conn, text, "en-US-BrianNeural")
    assert found is not None
    assert found.voice == "en-US-BrianNeural"
    assert found.seconds == 62.0
    assert found.payloads == [{"NBest": []}]
    assert native_model.rendering_for(conn, text, "en-US-AriaNeural") is None
    assert native_model.rendering_for(conn, "different text", "en-US-BrianNeural") is None


def test_re_capturing_a_voice_replaces_it_rather_than_storing_two(tmp_path) -> None:
    """Two readings that disagree about what the model does is worse than one that is stale."""
    conn = db.connect(":memory:")
    text = "Nothing here is clever."
    for seconds in (60.0, 61.5):
        db.record_native_rendering(
            conn,
            voice="en-US-BrianNeural",
            text_key=native_model.text_key(text),
            reference_text=text,
            wav_path=str(tmp_path / "a.wav"),
            payloads=[],
            seconds=seconds,
            characters=100,
        )
    stored = native_model.renderings_for(conn, text)
    assert len(stored) == 1
    assert stored[0].seconds == 61.5


def test_captured_voices_is_what_makes_a_run_resumable(tmp_path) -> None:
    conn = db.connect(":memory:")
    text = "The whole value is that the passage never changes."
    assert native_model.captured_voices(conn, text) == set()
    for name in ("en-US-BrianNeural", "en-US-AriaNeural"):
        db.record_native_rendering(
            conn,
            voice=name,
            text_key=native_model.text_key(text),
            reference_text=text,
            wav_path=str(tmp_path / f"{name}.wav"),
            payloads=[],
            seconds=1.0,
            characters=1,
        )
    assert native_model.captured_voices(conn, text) == {"en-US-BrianNeural", "en-US-AriaNeural"}


def test_the_text_key_groups_voices_and_separates_texts() -> None:
    """A text_key is the population a between-voice band is drawn from, so it ignores voice."""
    assert native_model.text_key("one") == native_model.text_key("one")
    assert native_model.text_key("one") != native_model.text_key("two")


# --- Which set a captured voice belongs to -----------------------------------------------------


def test_the_roster_is_the_authority_on_a_voices_sex_not_the_capture_row() -> None:
    """`native_renderings` stores no reference-set column, deliberately.

    Which set a voice belongs to is a fact about the voice. Copying it onto every rendering
    would be a second copy that can disagree with the first — and the SDK is right there.
    """
    grouped = native_model.group_by_set(
        ["en-US-BrianNeural", "en-US-AriaNeural", "en-US-GuyNeural"], ROSTER
    )
    assert grouped[MEN] == ["en-US-BrianNeural", "en-US-GuyNeural"]
    assert grouped[WOMEN] == ["en-US-AriaNeural"]


def test_a_voice_that_left_the_roster_keeps_its_rendering_and_loses_its_set() -> None:
    """Reported, never guessed. A retired voice must not be filed under a plausible set."""
    grouped = native_model.group_by_set(["en-US-RetiredNeural", "en-US-GuyNeural"], ROSTER)
    assert grouped[MEN] == ["en-US-GuyNeural"]
    assert "en-US-RetiredNeural" not in grouped[MEN] + grouped[WOMEN]
