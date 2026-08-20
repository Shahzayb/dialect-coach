"""The accent measurement: slicing, rejection, Lobanov, the instruments, the noise floor."""

from __future__ import annotations

import numpy as np
import pytest
from conftest import SAMPLE_RATE, synth_noise, synth_vowel, to_wav_bytes

import acoustics
import speech_analyzer
import vowel_measure
import vowel_reference
from acoustics import FormantPoint
from vowel_measure import Token

TICKS = speech_analyzer.TICKS_PER_SECOND

# F1/F2/F3 to synthesise for each vowel. Hillenbrand's adult-male means, used here as test
# INPUT so the measurement has a known answer to be checked against.
TRUTH = {
    "i": (340.0, 2338.0, 2994.0),
    "ɪ": (459.0, 1941.0, 2641.0),
    "ɛ": (592.0, 1774.0, 2602.0),
    "æ": (613.0, 1863.0, 2566.0),
    "ɑ": (757.0, 1326.0, 2523.0),
    "ɔ": (670.0, 1046.0, 2509.0),
    "ʌ": (618.0, 1243.0, 2549.0),
    "ʊ": (483.0, 1208.0, 2438.0),
    "u": (375.0, 971.0, 2359.0),
    "ɝ": (460.0, 1406.0, 1704.0),
    "eɪ": (437.0, 2181.0, 2727.0),
    "oʊ": (465.0, 866.0, 2479.0),
    "ə": (600.0, 1400.0, 2500.0),
    # Not in the published table — that is the point of the vowel this one is used for. The
    # values are a plausible steady stand-in, because the test is about what the REFERENCE
    # LOOKUP does with an uncovered vowel, not about MOUTH's real trajectory.
    "aʊ": (700.0, 1200.0, 2500.0),
}

# A silent lead-in, so the test exercises the thing that actually matters about the offsets:
# they are absolute positions into the file, not offsets from the first word. The committed
# drill fixture carries 1.69 s of exactly this.
LEAD_IN_S = 1.0


def build_recording(spec, *, drift: float = 1.0):
    """Synthesise audio and the matching normalised Azure words from one spec.

    `spec` is [(word, [(phoneme, milliseconds), ...]), ...]. Vowels become synthesised vowels
    with the formants in `TRUTH`; anything else becomes low-level noise, standing in for a
    consonant. Offsets are laid out on Azure's 10 ms tick grid from the start of the file,
    after the lead-in.

    `drift` scales every formant, standing in for the same speaker on a different day.
    """
    pieces = [synth_noise(LEAD_IN_S)]
    cursor = LEAD_IN_S
    words = []
    for text, phonemes in spec:
        entries = []
        word_start = cursor
        for symbol, milliseconds in phonemes:
            seconds = milliseconds / 1000.0
            if symbol in TRUTH:
                f1, f2, f3 = TRUTH[symbol]
                pieces.append(synth_vowel((f1 * drift, f2 * drift, f3 * drift), seconds))
            else:
                pieces.append(synth_noise(seconds))
            entries.append(
                {
                    "phoneme": symbol,
                    "score": 95.0,
                    "nbest": [{"phoneme": symbol, "score": 95.0}],
                    "offset_ticks": int(round(cursor * TICKS)),
                    "duration_ticks": int(round(seconds * TICKS)),
                    "start_s": cursor,
                    "end_s": cursor + seconds,
                }
            )
            cursor += seconds
        words.append(
            {
                "word": text,
                "accuracy": 95.0,
                "error_type": "None",
                "phonemes": entries,
                "syllables": [],
                "offset_ticks": int(round(word_start * TICKS)),
                "duration_ticks": int(round((cursor - word_start) * TICKS)),
                "start_s": word_start,
                "end_s": cursor,
            }
        )
    pieces.append(synth_noise(0.2))
    return to_wav_bytes(np.concatenate(pieces)), words


# A spec covering all twelve reference categories several times over, in real words so CMUdict
# can align them. Enough tokens per category to clear MIN_TOKENS_PER_CATEGORY.
INVENTORY_SPEC = [
    ("heed", [("h", 60), ("i", 200), ("d", 70)]),
    ("hid", [("h", 60), ("ɪ", 180), ("d", 70)]),
    ("head", [("h", 60), ("ɛ", 180), ("d", 70)]),
    ("had", [("h", 60), ("æ", 220), ("d", 70)]),
    ("hod", [("h", 60), ("ɑ", 220), ("d", 70)]),
    ("hawed", [("h", 60), ("ɔ", 220), ("d", 70)]),
    ("hud", [("h", 60), ("ʌ", 180), ("d", 70)]),
    ("hood", [("h", 60), ("ʊ", 180), ("d", 70)]),
    ("whod", [("h", 60), ("u", 200), ("d", 70)]),
    ("heard", [("h", 60), ("ɝ", 220), ("d", 70)]),
    ("hayed", [("h", 60), ("eɪ", 220), ("d", 70)]),
    ("hoed", [("h", 60), ("oʊ", 220), ("d", 70)]),
]


@pytest.fixture(scope="module")
def inventory():
    """One synthetic reading covering the whole reference inventory, three times over."""
    return build_recording(INVENTORY_SPEC * 3)


@pytest.fixture(scope="module")
def measured(inventory):
    wav, words = inventory
    return vowel_measure.extract(words, wav, ceiling_hz=5000.0, snr_db_min=30.0)


# --- Slicing: the offsets really point where they are believed to ----------------------------


def test_the_vowel_slices_land_on_the_vowels(measured) -> None:
    """The check that settles the stream-versus-file offset question, on real audio.

    `speech_analyzer._timing` warns that offsets are ticks from the start of the audio stream
    and that a slicing consumer must not simply assume they are file positions. This recording
    has a full second of silence before the first word, exactly as the committed fixture does,
    so reading the offsets relatively would put every slice a second early — in the silence.
    Vowels are the loudest thing in speech, so if the slices land right this is comfortably
    positive.
    """
    assert measured.alignment_db is not None
    assert measured.alignment_db > 10.0, (
        f"claimed vowel spans are only {measured.alignment_db:.1f} dB above everything that "
        f"is not a vowel — the phoneme offsets are not landing where they are believed to"
    )


def test_the_measurement_recovers_the_formants_that_were_synthesised(measured) -> None:
    """End to end: audio in, per-vowel means out, checked against what was put in."""
    normaliser = vowel_measure.lobanov(measured.accepted)
    found = vowel_measure.positions(measured.accepted, normaliser)
    for vowel, (f1, f2, _f3) in TRUTH.items():
        if vowel not in found:
            continue
        position = found[vowel]
        assert position.f1_hz == pytest.approx(f1, rel=0.10), vowel
        assert position.f2_hz == pytest.approx(f2, rel=0.10), vowel
        assert position.n >= vowel_measure.MIN_TOKENS_PER_CATEGORY


def test_a_segment_past_the_end_of_the_audio_is_rejected_not_clamped() -> None:
    wav, words = build_recording([("had", [("h", 60), ("æ", 200), ("d", 70)])])
    words[0]["phonemes"][1]["offset_ticks"] = int(999 * TICKS)
    result = vowel_measure.extract(words, wav, ceiling_hz=5000.0)
    assert [token.rejected_reason for token in result.tokens] == [
        vowel_measure.REJECT_OUT_OF_RANGE
    ]


# --- Refusing rather than guessing -----------------------------------------------------------


def test_a_short_vowel_is_rejected_with_its_reason() -> None:
    wav, words = build_recording([("had", [("h", 60), ("æ", 30), ("d", 70)])])
    result = vowel_measure.extract(words, wav, ceiling_hz=5000.0)
    assert result.tokens[0].rejected_reason == vowel_measure.REJECT_SHORT
    assert not result.accepted


def test_an_unvoiced_span_is_rejected() -> None:
    """A vowel slot filled with noise measures the room, not the speaker."""
    wav, words = build_recording([("had", [("h", 60), ("silence-æ", 200), ("d", 70)])])
    words[0]["phonemes"][1]["phoneme"] = "æ"  # claimed a vowel; the audio is noise
    result = vowel_measure.extract(words, wav, ceiling_hz=5000.0)
    assert result.tokens[0].rejected_reason == vowel_measure.REJECT_UNVOICED


def test_a_different_vowel_entirely_is_kept_out_of_the_baseline() -> None:
    """Its formants are a valid measurement of the WRONG target.

    Left in, it poisons the category mean it lands in while looking like ordinary evidence.
    It belongs in the phoneme diagnosis, which already reports it, and not here.
    """
    wav, words = build_recording([("had", [("h", 60), ("æ", 200), ("d", 70)])])
    words[0]["phonemes"][1]["nbest"] = [
        {"phoneme": "ɛ", "score": 80.0},
        {"phoneme": "æ", "score": 20.0},
    ]
    result = vowel_measure.extract(words, wav, ceiling_hz=5000.0)
    assert vowel_measure.REJECT_WRONG_VOWEL in result.tokens[0].rejected_reason
    assert "/ɛ/" in result.tokens[0].rejected_reason


def test_a_consonant_confusion_does_not_reject_the_vowel() -> None:
    """Only a VOWEL alternate means a different vowel was produced."""
    wav, words = build_recording([("had", [("h", 60), ("æ", 200), ("d", 70)])])
    words[0]["phonemes"][1]["nbest"] = [{"phoneme": "s", "score": 80.0}]
    result = vowel_measure.extract(words, wav, ceiling_hz=5000.0)
    assert result.tokens[0].accepted


def test_normalising_is_refused_below_the_floor_rather_than_approximated() -> None:
    wav, words = build_recording(INVENTORY_SPEC[:4] * 3)
    result = vowel_measure.extract(words, wav, ceiling_hz=5000.0)
    with pytest.raises(vowel_measure.TooFewTokens, match="refusal, not a zero"):
        vowel_measure.lobanov(result.accepted)


# --- Lobanov: the obvious implementation is the wrong one -------------------------------------


def _token(vowel: str, f1: float, f2: float) -> Token:
    point = FormantPoint(f1=f1, f2=f2, f3=2500.0, b1=50.0, b2=50.0, b3=50.0)
    return Token(
        vowel=vowel, word=vowel, word_index=0, start_s=0.0, end_s=0.1, duration_ms=100.0,
        at20=point, at50=point, at80=point, rms_dbfs=-20.0, f0_hz=120.0, stress=1,
        azure_score=95.0, coda_voiceless=None, accepted=True,
    )


def test_lobanov_averages_category_means_not_the_raw_token_pool() -> None:
    """The error this guards is invisible on inspection — the chart still looks like a chart.

    A natural passage over-samples some vowels badly: the benchmark passage yields 50 tokens
    of one vowel and 5 of another. Take the mean over the raw token pool and the speaker's
    centroid is dragged toward whichever vowel happened to occur most, tilting every z-score
    in the inventory.

    Here /ɑ/ appears thirty times and the other seven vowels three times each. The
    token-weighted centroid is dragged toward /ɑ/'s high F1; the category-mean centroid is
    not. They must differ, and the implementation must produce the second.
    """
    vowels = {
        "i": (340.0, 2338.0), "ɪ": (459.0, 1941.0), "ɛ": (592.0, 1774.0),
        "æ": (613.0, 1863.0), "ɔ": (670.0, 1046.0), "u": (375.0, 971.0),
        "ʊ": (483.0, 1208.0), "ɑ": (757.0, 1326.0),
    }
    tokens = [_token(v, *hz) for v, hz in vowels.items() for _ in range(3)]
    tokens += [_token("ɑ", *vowels["ɑ"]) for _ in range(27)]  # /ɑ/ ends up with 30

    normaliser = vowel_measure.lobanov(tokens, min_categories=8)

    category_mean = sum(hz[0] for hz in vowels.values()) / len(vowels)
    token_mean = sum(token.at50.f1 or 0.0 for token in tokens) / len(tokens)

    assert token_mean != pytest.approx(category_mean, abs=1.0), "the fixture is not unbalanced"
    assert normaliser.f1_mean == pytest.approx(category_mean, abs=0.01)
    assert normaliser.f1_mean != pytest.approx(token_mean, abs=1.0)
    assert len(normaliser.categories) == len(vowels)


def test_the_reference_is_normalised_over_its_own_twelve_categories() -> None:
    """A z-score is relative to whatever inventory produced it.

    A speaker normalised over 22 categories and a reference normalised over 12 sit in
    different spaces and their numbers are not comparable — which is why `lobanov` takes a
    category set at all.
    """
    normaliser = vowel_measure.reference_normaliser("men")
    assert len(normaliser.categories) == 12
    assert set(normaliser.categories) == vowel_measure.REFERENCE_CATEGORIES

    positions = vowel_measure.reference_positions("men")
    assert positions["i"].f2_z > 1.0, "FLEECE should sit far front in z-space"
    assert positions["u"].f2_z < -1.0, "GOOSE should sit far back"
    assert positions["ɑ"].f1_z > 1.0, "LOT should sit far open"


def test_an_unknown_reference_set_is_refused_rather_than_averaged() -> None:
    with pytest.raises(vowel_measure.TooFewTokens, match="never"):
        vowel_measure.reference_normaliser("everyone")


# --- The four instruments ---------------------------------------------------------------------


def test_rhoticity_is_measured_and_separates_nurse_from_the_rest(measured) -> None:
    normaliser = vowel_measure.lobanov(measured.accepted)
    found = vowel_measure.positions(measured.accepted, normaliser)
    nurse = found["ɝ"].f3_minus_f2_hz
    assert nurse is not None and nurse < 450.0
    for vowel in ("i", "ɑ", "u"):
        assert (found[vowel].f3_minus_f2_hz or 0.0) > nurse + 200.0


def test_trajectory_is_measured_from_the_same_three_samples(measured) -> None:
    normaliser = vowel_measure.lobanov(measured.accepted)
    found = vowel_measure.positions(measured.accepted, normaliser)
    # These are synthesised as steady vowels, so travel is near zero — which is exactly what a
    # monophthongised diphthong looks like, and what the finding should therefore report.
    assert found["eɪ"].f2_travel_hz == pytest.approx(0.0, abs=150.0)


def test_duration_ratios_are_ratios_and_never_absolute_milliseconds(measured) -> None:
    """Hillenbrand's durations are citation-form /hVd/; connected speech is far shorter."""
    ratios = vowel_measure.tense_lax_ratios(measured.accepted, "men")
    assert [ratio.label for ratio in ratios] == ["/i/ : /ɪ/", "/u/ : /ʊ/", "/eɪ/ : /ɛ/"]
    for ratio in ratios:
        assert ratio.target is not None
        assert 1.0 < ratio.target < 2.0, "a tense/lax target should be a modest ratio"
    # Synthesised at 200 ms against 180 ms, so the measured ratio follows what was built.
    assert ratios[0].ratio == pytest.approx(200.0 / 180.0, rel=0.15)


def test_pre_fortis_clipping_has_no_published_target_and_says_so() -> None:
    """Every Hillenbrand stimulus is an /hVd/ word, so all twelve end in a voiced /d/."""
    for table in vowel_reference.REFERENCE_SETS.values():
        assert len(table) == 12
    wav, words = build_recording(
        [("bad", [("b", 50), ("æ", 200), ("d", 70)])] * 3
        + [("bat", [("b", 50), ("æ", 120), ("t", 70)])] * 3
    )
    result = vowel_measure.extract(words, wav, ceiling_hz=5000.0)
    ratios = vowel_measure.pre_fortis_ratios(result.accepted)
    assert len(ratios) == 1
    assert ratios[0].target is None
    assert ratios[0].target_source == "TTS voice, same pipeline"
    assert ratios[0].ratio == pytest.approx(200.0 / 120.0, rel=0.2)


def test_reduction_is_measured_against_the_speaker_s_own_schwa(measured) -> None:
    normaliser = vowel_measure.lobanov(measured.accepted)
    result = vowel_measure.reduction(measured.accepted, normaliser)
    # The synthetic inventory is all monosyllables, so nothing is unstressed and the honest
    # answer is that reduction was not measured — not a zero.
    assert not result.measured
    assert result.mean_distance_z is None


def test_reduction_finds_the_centroid_when_unstressed_vowels_exist() -> None:
    spec = [("about", [("ə", 90), ("b", 50), ("aʊ", 200), ("t", 60)])] * 3
    spec += [(word, phonemes) for word, phonemes in INVENTORY_SPEC * 3]
    wav, words = build_recording(spec)
    result = vowel_measure.extract(words, wav, ceiling_hz=5000.0)
    normaliser = vowel_measure.lobanov(result.accepted)
    reduction = vowel_measure.reduction(result.accepted, normaliser)
    assert reduction.n_unstressed >= 3
    assert reduction.measured
    assert reduction.centroid_f1_z is not None


# --- The noise floor ---------------------------------------------------------------------------


def test_the_noise_floor_is_the_displacement_between_two_reads() -> None:
    """Two reads of the same passage with no learning in between. The gap IS the floor."""
    first_wav, first_words = build_recording(INVENTORY_SPEC * 3, drift=1.00)
    second_wav, second_words = build_recording(INVENTORY_SPEC * 3, drift=1.03)

    first = vowel_measure.extract(first_words, first_wav, ceiling_hz=5000.0)
    second = vowel_measure.extract(second_words, second_wav, ceiling_hz=5000.0)
    normaliser = vowel_measure.lobanov(first.accepted)

    floor = vowel_measure.noise_floor(
        vowel_measure.positions(first.accepted, normaliser),
        vowel_measure.positions(second.accepted, normaliser),
    )
    assert floor.vowels >= 8
    assert floor.median_z is not None and floor.median_z > 0.0


def test_movement_smaller_than_the_band_is_never_reported_as_change() -> None:
    """Including — especially — when it is in the flattering direction."""
    floor = vowel_measure.NoiseFloor(per_vowel={"i": 0.30, "ɑ": 0.20}, median_z=0.25, vowels=2)
    assert floor.within_noise("i", 0.29)
    assert floor.within_noise("i", -0.29)
    assert not floor.within_noise("i", 0.31)
    # A vowel with no band of its own falls back to the median rather than to "unlimited".
    assert floor.band_for("u") == 0.25
    assert floor.within_noise("u", 0.20)
    # No measurement at all is not evidence of change.
    assert floor.within_noise("i", None)


# --- The output contract -----------------------------------------------------------------------


def test_the_table_has_exactly_the_four_columns_in_order() -> None:
    assert vowel_measure.COLUMNS == (
        "Acoustic Feature",
        "User Realization",
        "Target Realization",
        "Delta / Adjustment Needed",
    )


def test_every_finding_names_the_phoneme_the_keyword_and_the_metric(measured) -> None:
    normaliser = vowel_measure.lobanov(measured.accepted)
    rows = vowel_measure.findings(measured, normaliser, reference_set="men")
    assert rows

    vowel_rows = [row for row in rows if row.feature.startswith("/")]
    assert vowel_rows
    for row in vowel_rows:
        assert "—" in row.feature, f"no metric named: {row.feature}"
        assert row.user, row.feature
        assert row.target, row.feature
        assert row.delta, row.feature

    fleece = [row for row in rows if row.feature.startswith("/i/ FLEECE — F2")]
    assert fleece, "FLEECE F2 row missing"
    assert "n=" in fleece[0].user, "the token count must travel with the number"
    assert "z" in fleece[0].target


def test_a_vowel_with_no_published_reference_says_so_rather_than_inventing_a_target() -> None:
    spec = INVENTORY_SPEC * 3 + [("mouth", [("m", 50), ("aʊ", 200), ("θ", 70)])] * 3
    wav, words = build_recording(spec)
    result = vowel_measure.extract(words, wav, ceiling_hz=5000.0)
    normaliser = vowel_measure.lobanov(result.accepted)
    rows = vowel_measure.findings(result, normaliser, reference_set="men")
    mouth = [row for row in rows if row.feature.startswith("/aʊ/ MOUTH")]
    assert mouth
    assert mouth[0].target == "no published GA reference"
    assert "12 vowels" in mouth[0].delta


def test_rejected_tokens_get_rows_so_a_thin_table_is_visibly_thin() -> None:
    wav, words = build_recording(INVENTORY_SPEC * 3 + [("hid", [("h", 60), ("ɪ", 20), ("d", 70)])])
    result = vowel_measure.extract(words, wav, ceiling_hz=5000.0)
    normaliser = vowel_measure.lobanov(result.accepted)
    rows = vowel_measure.findings(result, normaliser, reference_set="men")
    rejected = [row for row in rows if "rejected token" in row.feature]
    assert rejected
    assert vowel_measure.MIN_VOWEL_MS == 45.0
    assert any(vowel_measure.REJECT_SHORT in row.delta for row in rejected)
    assert all("Refused rather than guessed" in row.delta for row in rejected)


def test_a_movement_inside_the_noise_band_renders_as_measurement_noise(measured) -> None:
    normaliser = vowel_measure.lobanov(measured.accepted)
    # A deliberately enormous band, so every position lands inside it.
    floor = vowel_measure.NoiseFloor(per_vowel={}, median_z=99.0, vowels=0)
    rows = vowel_measure.findings(measured, normaliser, reference_set="men", noise=floor)
    scored = [row for row in rows if "Lobanov z" in row.feature and "no published" not in row.target]
    assert scored
    assert all(vowel_measure.WITHIN_NOISE in row.delta for row in scored)


def test_the_deltas_carry_a_sign_and_an_instruction(measured) -> None:
    """A delta with no instruction is a measurement; an instruction with no delta is advice."""
    normaliser = vowel_measure.lobanov(measured.accepted)
    rows = vowel_measure.findings(measured, normaliser, reference_set="men")
    scored = [
        row for row in rows
        if "Lobanov z" in row.feature and "no published" not in row.target
    ]
    assert scored
    for row in scored:
        assert "→" in row.delta, row.delta
        assert ("+" in row.delta or "−" in row.delta), row.delta


# --- Quality gating ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("snr", "reliable", "phrase"),
    [
        (30.0, True, ""),
        (18.0, True, "usable but not clean"),
        (9.0, False, "measuring the room"),
        (None, False, "no way to say"),
    ],
)
def test_quality_is_gated_on_the_worst_utterance(snr, reliable, phrase) -> None:
    """Azure's own `snr_db_min`, read from `overall_scores` — the payload is not re-parsed."""
    result = vowel_measure.Measurement(tokens=(), ceiling_hz=5000.0, snr_db_min=snr, style="read")
    assert result.reliable is reliable
    assert phrase in result.quality_note()


def test_the_ceiling_is_recorded_on_every_measurement(measured) -> None:
    """So a row stays interpretable after a re-calibration moves the ceiling."""
    assert measured.ceiling_hz == 5000.0
