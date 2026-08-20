"""The model's own reading of a text: synthesised, then assessed through this same pipeline.

Everything in this project that says "compared to a native speaker" ultimately compares
against something, and this module is what that something is made of. A neural voice reads a
text; the recording goes back through **pronunciation assessment**; and the result is a
reading with word offsets, phoneme offsets and audio, measurable by exactly the code that
measures the user.

## Why the model's reading is ASSESSED and not merely synthesised

Azure's synthesiser reports word boundaries for free during synthesis — `audio_offset`,
`duration` and `text` on `synthesis_word_boundary` — and using them would have cost nothing.
It is the wrong answer anyway. Those offsets come from the **synthesiser's** clock and the
user's come from the **recogniser's**. Anchoring two pitch contours on two different
segmenters produces an alignment that is approximately right, and timing error is one of the
things being measured: an approximation there is indistinguishable from the finding.

So the model's rendering pays for the assessment half too, and both sides of every comparison
carry offsets from one segmenter. It costs about 62 seconds of the monthly 18,000 per voice
per passage, once, and it is bought once and kept forever.

## A synthesiser is not a native speaker, and this module never claims otherwise

What earns a neural voice its place is not that it is a person. It is that it is **General
American, connected, current, and it does not move** — and that captured across a set of
voices it becomes a distribution rather than one talker's idiosyncrasy. `rhythm.py` and
`scripts/capture_baseline.py` both raise this objection about their own single-voice baseline;
capturing many voices is the answer to it.

The two references are kept apart and never averaged: `vowel_measure.REFERENCE_PUBLISHED` is
Hillenbrand's real humans and `REFERENCE_VOICE` is this. Every surface says which it used.

No Streamlit here. It touches TTS, STT and the database, which is why it is not folded into
`tts.py` (which has never seen either of the other two).
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import audio_utils
import budget
import db
import speech_analyzer
import tts
import utils
from utils import Mode

logger = logging.getLogger(__name__)

# The reference sets, spelled the way `vowel_reference.REFERENCE_SETS` spells them. Formants
# scale with vocal tract length, so the two are captured and kept apart — a mean of the men's
# and women's readings describes nobody, and `GA_REFERENCE_SET` exists to pick one.
MEN = "men"
WOMEN = "women"

# The only locale Azure pronunciation assessment supports for this project's target accent.
LOCALE = "en-US"

_GENDER_TO_SET: Mapping[str, str] = {"Male": MEN, "Female": WOMEN}

# Only plain `…Neural` voices. This is a COST rule, not a taste one, and it is inherited
# verbatim from `perception_trainer`: `en-US-BrianNeural` is the one voice this project has
# actually seen billed as neural on the F0 tier, and the DragonHD, MAI, Turbo and Multilingual
# families are an unverified pricing class. A capture run touches sixteen voices at once, so
# guessing wrong here is sixteen times as expensive as guessing wrong anywhere else.
_PLAIN_NEURAL = re.compile(r"^en-US-[A-Za-z]+Neural$")
_EXCLUDED_FAMILIES = ("Multilingual", "DragonHD", "MAI", "Turbo", "HD")

# **The SDK reports no age, and a child voice is a `Female` adult as far as it is concerned.**
# Verified by introspection against SDK 1.51.1, not assumed: `en-US-AnaNeural` — Microsoft's
# en-US child voice — comes back as `gender=Female, voice_type=OnlineNeural, style_list=['']`
# with nothing anywhere in the roster entry marking her as a child.
#
# That matters more here than anywhere else in the app. Formants scale with vocal tract length
# and a child's is far shorter, so Ana's F1/F2/F3 sit well above an adult woman's. Dropped into
# the women's reference set she would pull its mean upward and inflate its between-voice SD —
# producing a reference that is confidently wrong in a way that still looks like a vowel chart.
# Hillenbrand's own table is adult men and women only, for exactly this reason.
#
# So the exclusion is a hand-maintained list with a test, because there is no API to ask. If a
# future roster adds another child or teen voice it must be added here; the alternative is
# discovering it as a reference that quietly disagrees with every other measurement.
NON_ADULT_VOICES = frozenset({"en-US-AnaNeural"})

# Per reference set. Eight is chosen so each vowel category lands ~40 tokens per set from one
# reading of the benchmark passage, which is enough for a between-voice SD to mean something,
# at a cost of roughly 3% of the monthly TTS allowance and 5% of the STT allowance.
VOICES_PER_SET = 8

# Below this a set has no usable between-voice spread and the reference refuses rather than
# reporting an SD computed from three talkers as though it described a population.
MIN_VOICES_PER_SET = 4

# The passage is ~196 words. Single-shot cannot take it, so every rendering is captured in
# paragraph mode — the same mode the user reads it in, through the same merge.
CAPTURE_MODE = Mode.PARAGRAPH


class CaptureRefused(RuntimeError):
    """The rendering cannot be bought. Message is safe to show in the UI."""


def text_key(text: str) -> str:
    """The identity of a text, shared with the TTS clip cache so there is one definition.

    Keyed on the text alone, not on the voice: a text_key groups every voice's reading of the
    same passage, which is exactly the population a between-voice band is drawn from.
    """
    return tts.cache_key("", text)


# --- Choosing the voices ----------------------------------------------------------------------


@dataclass(frozen=True)
class RosterVoice:
    """One entry of the live en-US roster, as the SDK reports it."""

    short_name: str
    gender: str  # "Male" | "Female" | "Neutral", the SDK's own word
    voice_type: str  # "OnlineNeural" and friends


def eligible(voice: RosterVoice) -> bool:
    """Whether this voice may be spent on at all.

    Four filters, all refusals: it must be a plain `en-US-<Name>Neural`, it must not carry a
    newer-family marker, it must not be a child voice, and the SDK must call it a neural online
    voice. A name that no longer exists fails as a `BadRequest` at synthesis time, **after** the
    pre-flight has already approved the spend — which is why the roster is read rather than
    recalled.
    """
    if not _PLAIN_NEURAL.match(voice.short_name):
        return False
    if any(marker in voice.short_name for marker in _EXCLUDED_FAMILIES):
        return False
    if voice.short_name in NON_ADULT_VOICES:
        return False
    return voice.voice_type == "OnlineNeural"


def select_voices(
    roster: Iterable[RosterVoice],
    *,
    per_set: int = VOICES_PER_SET,
    preferred: Sequence[str] = (),
) -> dict[str, tuple[str, ...]]:
    """Pick a sex-stratified set of voices from the live roster. Pure, so it is testable.

    `preferred` goes first and the rest fill alphabetically. The preference is not cosmetic:
    `perception_trainer.VOICES` was curated for spread across two voice generations, which is
    the same property a reference wants — a set drawn from one generation is one recording
    character wearing eight names, and its between-voice SD would understate how much real
    speakers differ. Alphabetical after that, so a re-run picks the same set and a re-capture
    does not silently move the reference.
    """
    by_set: dict[str, list[str]] = {MEN: [], WOMEN: []}
    usable = [voice for voice in roster if eligible(voice)]
    order = {name: index for index, name in enumerate(preferred)}
    usable.sort(key=lambda voice: (order.get(voice.short_name, len(order)), voice.short_name))

    for voice in usable:
        reference_set = _GENDER_TO_SET.get(voice.gender)
        if reference_set is None:
            # "Neutral" has no vocal-tract-length interpretation, and putting it in either set
            # would tilt that set's Lobanov centroid by an unknown amount.
            continue
        if len(by_set[reference_set]) < per_set:
            by_set[reference_set].append(voice.short_name)

    return {name: tuple(names) for name, names in by_set.items()}


def fetch_roster() -> list[RosterVoice]:
    """The live en-US roster, read from Azure. Costs nothing — it synthesises no audio.

    Read rather than recalled, every time. Voices are added, renamed and retired without
    notice, and a name that no longer exists fails as a `BadRequest` at synthesis time — after
    the pre-flight has already approved the spend.
    """
    import azure.cognitiveservices.speech as speechsdk

    config = speechsdk.SpeechConfig(
        subscription=utils.require("AZURE_SPEECH_KEY"),
        region=utils.require("AZURE_SPEECH_REGION"),
    )
    # audio_config=None for the same reason `tts._speak` needs it: the default binds a speaker
    # the container does not have.
    synthesizer = speechsdk.SpeechSynthesizer(speech_config=config, audio_config=None)
    result = synthesizer.get_voices_async(LOCALE).get()
    if result.reason != speechsdk.ResultReason.VoicesListRetrieved:
        raise CaptureRefused(f"Could not list the en-US voices: {result.reason}")
    return [
        RosterVoice(
            short_name=entry.short_name,
            gender=getattr(entry.gender, "name", str(entry.gender)),
            voice_type=getattr(entry.voice_type, "name", str(entry.voice_type)),
        )
        for entry in result.voices
    ]


def group_by_set(names: Iterable[str], roster: Iterable[RosterVoice]) -> dict[str, list[str]]:
    """Split captured voice names into the two reference sets. Pure, so it is testable.

    **The roster is the authority on a voice's sex, not the capture row.** `native_renderings`
    deliberately stores no reference-set column: which set a voice belongs to is a fact about
    the voice, and duplicating it onto every rendering is a second copy that can disagree with
    the first. A voice that has since left the roster keeps its rendering and loses its set —
    reported, not guessed.
    """
    gender = {entry.short_name: entry.gender for entry in roster}
    grouped: dict[str, list[str]] = {MEN: [], WOMEN: []}
    for name in sorted(names):
        reference_set = _GENDER_TO_SET.get(gender.get(name, ""))
        if reference_set is not None:
            grouped[reference_set].append(name)
    return grouped


def stored_sets(conn: sqlite3.Connection, text: str) -> dict[str, list[str]]:
    """The captured voices for this text, grouped into the two reference sets."""
    return group_by_set(captured_voices(conn, text), fetch_roster())


# --- What a captured rendering is -------------------------------------------------------------


@dataclass(frozen=True)
class Rendering:
    """One voice's assessed reading of one text, ready to measure."""

    voice: str
    reference_text: str
    wav_path: Path
    payloads: list[dict[str, Any]]
    seconds: float
    characters: int

    def words(self) -> list[dict[str, Any]]:
        """The normalised word shape, with offsets — the same shape a user attempt carries."""
        _, _, words = speech_analyzer.normalise(self.payloads, self.reference_text, CAPTURE_MODE)
        return words

    def audio(self) -> bytes | None:
        """The WAV, or None when the file has gone. Gitignored, so a fresh clone has none."""
        try:
            return self.wav_path.read_bytes()
        except OSError:
            logger.warning("Native rendering audio missing at %s", self.wav_path)
            return None


def wav_path_for(voice: str, key: str) -> Path:
    """Where one rendering's audio lives. Under `audio/`, which is gitignored in full."""
    root = Path(utils.get("AUDIO_DIR") or "./audio/attempts").parent
    return root / "native" / f"{key[:16]}_{voice}.wav"


def _rendering_from_row(row: utils.RowLike) -> Rendering:
    payloads = json.loads(str(row["payloads_json"]))
    return Rendering(
        voice=str(row["voice"]),
        reference_text=str(row["reference_text"]),
        wav_path=Path(str(row["wav_path"])),
        payloads=payloads if isinstance(payloads, list) else [payloads],
        seconds=float(row["seconds"]),
        characters=int(row["characters"]),
    )


def rendering_for(conn: sqlite3.Connection, text: str, voice: str) -> Rendering | None:
    """One voice's stored reading of this text, or None when it has not been bought."""
    row = db.native_rendering(conn, voice, text_key(text))
    return None if row is None else _rendering_from_row(row)


def renderings_for(conn: sqlite3.Connection, text: str) -> list[Rendering]:
    """Every stored voice for this text. The population a between-voice band is drawn from."""
    return [_rendering_from_row(row) for row in db.native_renderings_for(conn, text_key(text))]


def captured_voices(conn: sqlite3.Connection, text: str) -> set[str]:
    """Which voices this text already has. What makes a capture run resumable."""
    return db.native_rendering_voices(conn, text_key(text))


# --- Buying one ---------------------------------------------------------------------------------


def estimate(text: str, voices: Sequence[str]) -> tuple[int, float]:
    """(characters, seconds) a capture would cost, before any of it is spent.

    The seconds figure is an estimate from the character count — a neural voice reads the
    benchmark passage in about 62 seconds for 975 characters — and it is deliberately rounded
    UP, because erring toward "less remaining than you think" is the rule the rest of
    `budget.py` already follows.
    """
    characters = len(text) * len(voices)
    seconds = (len(text) / 975.0) * 65.0 * len(voices)
    return characters, seconds


def capture(conn: sqlite3.Connection, text: str, voice: str) -> Rendering:
    """Synthesise `text` in `voice`, assess it, store it. Spends real allowance.

    Both halves are metered — the TTS characters through `db.record_tts_usage` and the STT
    seconds through an `attempts` row, which is where the app's remaining-allowance figure is
    derived from. A capture that did not meter itself would make every later estimate in the
    app wrong by exactly the amount this run cost.
    """
    text = (text or "").strip()
    if not text:
        raise CaptureRefused("There is nothing to synthesise — the text is empty.")
    if utils.offline_mode():
        raise CaptureRefused(
            "OFFLINE_MODE is on, so no model rendering can be captured. There is no fixture "
            "to replay for audio the way there is for an assessment."
        )

    budget.require_f0_acknowledgement()
    payload = tts.payload_for(text, slow=False, voice=voice)
    budget.preflight_tts(conn, len(payload))

    synthesis = tts.synthesise(text, voice=voice, slow=False)
    db.record_tts_usage(
        conn, characters=synthesis.characters * max(synthesis.attempts, 1), voice=synthesis.voice
    )

    wav_bytes, seconds = audio_utils.prepare(synthesis.audio, CAPTURE_MODE)
    budget.preflight_stt(conn, seconds, CAPTURE_MODE)

    with audio_utils.temp_wav(wav_bytes) as temp_path:
        payloads, _, attempts = speech_analyzer.recognise(temp_path, text, CAPTURE_MODE)

    key = text_key(text)
    path = wav_path_for(voice, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(wav_bytes)

    # Recorded as an attempt so the STT meter is honest about the money. Marked with the
    # capture marker `rhythm.is_baseline_capture` already understands, so the Progress tab can
    # never plot a synthesiser's reading as the user's own.
    db.record_attempt(
        conn,
        mode=CAPTURE_MODE,
        reference_text=f"{CAPTURE_MARKER} {voice}",
        recognised_text=speech_analyzer._display_text(payloads[0]),
        audio_seconds=seconds * max(attempts, 1),
        audio_sha256=utils.sha256_bytes(wav_bytes),
        overall_scores={},
        azure_raw=payloads if len(payloads) > 1 else payloads[0],
        offline=False,
    )

    db.record_native_rendering(
        conn,
        voice=voice,
        text_key=key,
        reference_text=text,
        wav_path=str(path),
        payloads=payloads,
        seconds=seconds * max(attempts, 1),
        characters=synthesis.characters * max(synthesis.attempts, 1),
    )
    logger.info("Captured %s: %.1fs, %d characters", voice, seconds, synthesis.characters)

    return Rendering(
        voice=voice,
        reference_text=text,
        wav_path=path,
        payloads=payloads,
        seconds=seconds,
        characters=synthesis.characters,
    )


# Reuses `rhythm.BASELINE_CAPTURE_MARKER`'s wording rather than inventing a second marker: the
# Progress tab already filters on it, and a second spelling would be a synthesiser's reading
# plotted as a human's the first time somebody forgot about it.
CAPTURE_MARKER = "[tts rhythm baseline capture]"


def seed_from_baseline_fixture(conn: sqlite3.Connection, fixture: Path, wav: Path) -> bool:
    """Adopt the already-paid-for benchmark rendering as a native rendering. Costs nothing.

    `scripts/capture_baseline.py` bought exactly this in v0.7.0 — the benchmark passage,
    synthesised and assessed — and committed the payload to `tests/fixtures/`. Re-buying it
    would be spending allowance on something already on disk.
    """
    if not fixture.exists() or not wav.exists():
        return False
    try:
        captured = json.loads(fixture.read_text(encoding="utf-8"))
        text = str(captured.get("reference_text") or "")
        voice = str(captured.get("voice") or "")
        payloads = captured["payloads"]
    except (OSError, ValueError, KeyError):
        logger.warning("Could not read the baseline fixture at %s", fixture, exc_info=True)
        return False
    if not text or not voice:
        return False

    key = text_key(text)
    destination = wav_path_for(voice, key)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        destination.write_bytes(wav.read_bytes())

    db.record_native_rendering(
        conn,
        voice=voice,
        text_key=key,
        reference_text=text,
        wav_path=str(destination),
        payloads=payloads,
        seconds=audio_utils.duration_seconds(destination.read_bytes()),
        characters=len(text),
    )
    logger.info("Seeded the native rendering for %s from the committed baseline fixture", voice)
    return True
