"""The four rungs a problem is practised on: sound → word → sentence → paragraph.

The brief's rule, and #42's: **a thing is only resolved once it survives at the rung above.**
In isolation a speaker hyperarticulates; in context they reduce, link and subordinate stress.
Those are different productions of the same sound, and only the one in context is the one that
matters. So a sound is checked inside its word, a word inside its sentence, and a sentence
inside its paragraph — the paragraph is the top and is checked against itself.

Streamlit-free and network-free by design, like `vowel_measure` and `rhythm`: it reads the
normalised word shape `speech_analyzer.normalise` produces plus the audio those words were
measured from, and returns spans. That is what lets the same functions serve a user attempt and
a `native_model.Rendering` — `Rendering.words()` returns the identical shape.

## Slicing is done here with `wave`, not with Praat

Praat's `Extract part` with an empty range returns the WHOLE sound rather than nothing, which
has already spliced an entire recording back into a clip once in this project. `accent_resynth`
has to live with that (its corrections splice a middle back between two ends, and it guards the
empty parts explicitly via `_MIN_PART_S`). Plain playback does not: a span for listening is a
byte range in PCM, so it is cut here with the standard library and the trap never arises.
"""

from __future__ import annotations

import difflib
import io
import statistics
import wave
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

import accent_charts
import phoneme_reference
import rhythm
import shadowing
import utils


class Rung(str, Enum):
    """The four units, smallest first. The order is the ladder."""

    SOUND = "sound"
    WORD = "word"
    SENTENCE = "sentence"
    PARAGRAPH = "paragraph"


# Smallest to largest. `above` reads this and nothing else, so the ladder has one definition.
ORDER: tuple[Rung, ...] = (Rung.SOUND, Rung.WORD, Rung.SENTENCE, Rung.PARAGRAPH)

RUNG_LABELS: Mapping[Rung, str] = {
    Rung.SOUND: "sound",
    Rung.WORD: "word",
    Rung.SENTENCE: "sentence",
    Rung.PARAGRAPH: "paragraph",
}

# What the surface says when a rung has nothing above it to be checked against.
TOP_RUNG_NOTE = (
    "The paragraph is the top of the ladder — there is no larger unit to survive inside, so it "
    "is checked against itself."
)


def above(rung: Rung) -> Rung | None:
    """The rung a problem at `rung` has to survive inside. None at the top.

    None is not "no check" — it is "checked against itself", which the caller states rather
    than silently skipping. See `TOP_RUNG_NOTE`.
    """
    position = ORDER.index(rung)
    return ORDER[position + 1] if position + 1 < len(ORDER) else None


# The rungs whose spans are played back as audio. The SOUND rung is measured and diagnosed but
# never played on its own: a phoneme lifted out of its word is 60 ms of formant transition that
# teaches nothing, and the speaker hears it inside the word instead. `audible_span_for` is what
# acts on this.
AUDIBLE: frozenset[Rung] = frozenset({Rung.WORD, Rung.SENTENCE, Rung.PARAGRAPH})

# How closely the spoken tokens must align with the script before sentence boundaries mean
# anything. Well below what a stumbling read scores and well above what an unrelated text does,
# so a bad day still gets practice and a wrong reference still refuses.
MIN_ALIGNMENT = 0.6


@dataclass(frozen=True)
class Span:
    """One unit of one recording: what it is, where it is, and which words it covers.

    `start_s`/`end_s` are seconds into the recording the words were measured from. Both are
    always real numbers — a span is never built from a word that carries no timing, because a
    word that was never spoken has nowhere to point.
    """

    rung: Rung
    label: str  # the phoneme, the word, or the text of the sentence/paragraph
    start_s: float
    end_s: float
    word_indices: tuple[int, ...]
    # Only on a SOUND span: which phoneme within `word_indices[0]`. Positional rather than
    # the symbol, because a word can carry the same vowel twice and they are different tokens.
    phoneme_index: int | None = None

    @property
    def seconds(self) -> float:
        return self.end_s - self.start_s

    def contains(self, other: Span) -> bool:
        """Whether `other` sits inside this span, by word coverage rather than by time.

        Word indices, not timestamps: a rung above is defined by containing the same WORDS,
        and comparing floats at clip boundaries would make containment turn on rounding.
        """
        return bool(other.word_indices) and set(other.word_indices) <= set(self.word_indices)


# --- Building spans from a measured recording --------------------------------------------------


def _timing(item: Mapping[str, object]) -> tuple[float, float] | None:
    """(start, end) in seconds, or None when this word or phoneme carries no timing.

    A word that was never spoken carries the timing keys as None rather than not at all
    (`speech_analyzer.NO_TIMING`), so this reads them directly and needs no `.get` guard for
    one construction path and a different one for the other.
    """
    start, end = item.get("start_s"), item.get("end_s")
    if not isinstance(start, int | float) or not isinstance(end, int | float):
        return None
    if end <= start:
        return None
    return float(start), float(end)


def word_spans(words: Sequence[Mapping[str, object]]) -> list[Span]:
    """One span per spoken word, in time order. Words with no timing are dropped."""
    found: list[Span] = []
    for index, word in enumerate(words):
        times = _timing(word)
        if times is None:
            continue
        start, end = times
        found.append(
            Span(
                rung=Rung.WORD,
                label=str(word.get("word") or ""),
                start_s=start,
                end_s=end,
                word_indices=(index,),
            )
        )
    return found


def sound_spans(words: Sequence[Mapping[str, object]]) -> list[Span]:
    """One span per timed phoneme, in time order.

    Every phoneme, not only the vocalic ones: the sound rung inherits the existing
    `CONTRAST` and `VOWEL` targets, and a contrast target is routinely a consonant
    (`/θ/ → /t/`). Restricting this to vowels would leave the rung unable to represent
    half of what is already on the practice queue.
    """
    found: list[Span] = []
    for index, word in enumerate(words):
        phonemes = word.get("phonemes")
        if not isinstance(phonemes, list):
            continue
        for position, phoneme in enumerate(phonemes):
            if not isinstance(phoneme, dict):
                continue
            times = _timing(phoneme)
            if times is None:
                continue
            start, end = times
            found.append(
                Span(
                    rung=Rung.SOUND,
                    label=phoneme_reference.normalise(phoneme.get("phoneme")),
                    start_s=start,
                    end_s=end,
                    word_indices=(index,),
                    phoneme_index=position,
                )
            )
    return found


def _token_map(words: Sequence[Mapping[str, object]]) -> tuple[list[str], list[int]]:
    """(tokens, word index per token) for the whole word list.

    The same technique `speech_analyzer._diff_miscue` uses, and for the same reason: joining
    the words into one string and re-splitting it desynchronises the moment a word does not
    yield exactly one token. "well-known" yields two and shifts every later index; a
    punctuation-only word yields none and silently drops a real word.
    """
    tokens: list[str] = []
    index_of_token: list[int] = []
    for position, word in enumerate(words):
        for token in utils.normalise_words(str(word.get("word") or "")):
            tokens.append(token)
            index_of_token.append(position)
    return tokens, index_of_token


def sentence_spans(words: Sequence[Mapping[str, object]], reference_text: str | None) -> list[Span]:
    """One span per sentence of `reference_text`, mapped onto the words that were spoken.

    The split itself is `shadowing.phrases`, which already merges one-word fragments and
    returns a single phrase for a passage that does not split cleanly. Reusing it means the
    echo track and the practice ladder can never disagree about where a sentence ends.

    **Aligned, not matched position for position.** Requiring the spoken tokens to equal the
    reference tokens looks safe and is not: one inserted word anywhere in the recording would
    refuse every sentence in it. That is not hypothetical — it cost a reference voice on the
    first real run, and a human stumbles far more than a synthesiser does. So this aligns the
    two with `difflib`, exactly as `speech_analyzer._diff_miscue` does, and lets an insertion
    join the sentence it fell inside rather than invalidating the passage.

    It still refuses outright when the two texts are not the same text at all — below
    `MIN_ALIGNMENT`, a mapping would put boundaries in meaningless places. Mode C is the
    ordinary case for that: a prompt is not a script, and nothing was scored against it.
    """
    phrases = shadowing.phrases(reference_text)
    if not phrases:
        return []

    # Reference tokens, each tagged with the sentence it belongs to.
    reference: list[str] = []
    sentence_of_token: list[int] = []
    for index, phrase in enumerate(phrases):
        for token in utils.normalise_words(phrase):
            reference.append(token)
            sentence_of_token.append(index)
    if not reference:
        return []

    heard, index_of_token = _token_map(words)
    if not heard:
        return []

    matcher = difflib.SequenceMatcher(None, reference, heard, autojunk=False)
    if matcher.ratio() < MIN_ALIGNMENT:
        return []

    # Which sentence each SPOKEN token belongs to. An insertion has no reference token of its
    # own, so it inherits the sentence of whatever was last aligned — the sentence it was
    # actually spoken inside.
    sentence_of_word: dict[int, int] = {}
    current = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("equal", "replace"):
            for offset, position in enumerate(range(j1, j2)):
                source = min(i1 + offset, i2 - 1) if i2 > i1 else None
                current = sentence_of_token[source] if source is not None else current
                sentence_of_word.setdefault(index_of_token[position], current)
        elif tag == "insert":
            for position in range(j1, j2):
                sentence_of_word.setdefault(index_of_token[position], current)
        elif tag == "delete" and i2 > i1:
            # Words in the script that were never spoken. Nothing to place, but the cursor
            # moves so a later insertion lands in the right sentence.
            current = sentence_of_token[i2 - 1]

    grouped: dict[int, list[int]] = {}
    for word_index, sentence in sorted(sentence_of_word.items()):
        grouped.setdefault(sentence, []).append(word_index)

    found: list[Span] = []
    for sentence in sorted(grouped):
        covered = grouped[sentence]
        timed = [t for t in (_timing(words[i]) for i in covered) if t is not None]
        if not timed:
            # Every word in this sentence was omitted. There is no audio to play and nothing
            # to measure, so the sentence is not a practisable unit on this recording.
            continue
        found.append(
            Span(
                rung=Rung.SENTENCE,
                label=phrases[sentence],
                start_s=min(start for start, _ in timed),
                end_s=max(end for _, end in timed),
                word_indices=tuple(covered),
            )
        )
    return found


def paragraph_span(
    words: Sequence[Mapping[str, object]], reference_text: str | None = None
) -> Span | None:
    """The whole recording as one span, or None when nothing in it was timed."""
    timed = [t for t in (_timing(word) for word in words) if t is not None]
    if not timed:
        return None
    covered = tuple(index for index, word in enumerate(words) if _timing(word) is not None)
    label = " ".join((reference_text or "").split())
    return Span(
        rung=Rung.PARAGRAPH,
        label=label or " ".join(str(words[i].get("word") or "") for i in covered),
        start_s=min(start for start, _ in timed),
        end_s=max(end for _, end in timed),
        word_indices=covered,
    )


def spans(
    words: Sequence[Mapping[str, object]], rung: Rung, reference_text: str | None = None
) -> list[Span]:
    """Every practisable span at `rung`, in time order. Empty when the rung is unavailable."""
    if rung is Rung.SOUND:
        return sound_spans(words)
    if rung is Rung.WORD:
        return word_spans(words)
    if rung is Rung.SENTENCE:
        return sentence_spans(words, reference_text)
    whole = paragraph_span(words, reference_text)
    return [whole] if whole is not None else []


def enclosing(span: Span, candidates: Sequence[Span]) -> Span | None:
    """The span among `candidates` that `span` sits inside. None when nothing contains it.

    This is what "checked one level up" resolves to in practice: build the rung above with
    `spans(...)` and ask which of them the problem actually lives in.
    """
    for candidate in candidates:
        if candidate.contains(span):
            return candidate
    return None


def audible_span_for(span: Span, words: Sequence[Mapping[str, object]]) -> Span | None:
    """The span actually played for `span` — itself, unless it is a sound.

    A sound is heard inside its word (`AUDIBLE`), so this walks a SOUND span up one rung.
    Returns None when the rung is audible but its own span cannot be built, which a caller
    should treat as "nothing to play" rather than as an error.
    """
    if span.rung in AUDIBLE:
        return span
    return enclosing(span, word_spans(words))


# --- Cutting a span out of the audio ------------------------------------------------------------


class LadderError(ValueError):
    """A span that cannot be cut, or a recording that cannot be read."""


# A cut never starts or ends exactly on a phoneme boundary in practice, and a formant
# transition carries the identity of the sound before it. This much audio is kept either side
# so a sliced word does not begin mid-burst — small enough not to pull in a neighbouring
# vowel at connected-speech rates, where a short word runs about 200 ms.
PAD_S = 0.02


def _framerate(wav_bytes: bytes) -> int:
    """The recording's sample rate, read from its own header rather than assumed."""
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as source:
            return int(source.getframerate())
    except (wave.Error, EOFError) as exc:
        raise LadderError("That recording could not be read as WAV audio.") from exc


def slice_wav(wav_bytes: bytes, start_s: float, end_s: float, *, pad_s: float = PAD_S) -> bytes:
    """The audio between `start_s` and `end_s`, as its own WAV.

    Plain PCM frame arithmetic through the standard library, deliberately: this is the
    operation Praat's `Extract part` gets wrong at an empty range, and a span for LISTENING
    does not need Praat at all. `accent_resynth` still uses Praat because its corrections
    splice a modified middle back between two ends; that path guards the empty parts itself.

    Clamped to the recording rather than raising at the edges — a first-word span padded
    backwards starts before zero, and that is ordinary, not an error.
    """
    if end_s <= start_s:
        raise LadderError("That span has no duration.")
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as source:
            params = source.getparams()
            frames = source.readframes(params.nframes)
    except (wave.Error, EOFError) as exc:
        raise LadderError("That recording could not be read as WAV audio.") from exc

    rate = params.framerate
    width = params.sampwidth * params.nchannels
    total = len(frames) // width

    first = max(0, int((start_s - pad_s) * rate))
    last = min(total, int((end_s + pad_s) * rate))
    if last <= first:
        raise LadderError("That span falls outside the recording.")

    cut = frames[first * width : last * width]
    out = io.BytesIO()
    with wave.open(out, "wb") as sink:
        sink.setnchannels(params.nchannels)
        sink.setsampwidth(params.sampwidth)
        sink.setframerate(rate)
        sink.writeframes(cut)
    return out.getvalue()


def cut_with_offset(wav_bytes: bytes, span: Span, *, pad_s: float = PAD_S) -> tuple[bytes, float]:
    """The span's audio, and where its t=0 sits on the full recording's clock.

    The offset is frame-exact rather than `span.start_s - pad_s`: `slice_wav` truncates to a
    whole frame, and a correction rebased on the un-truncated number would sit up to one frame
    out. Sub-millisecond, and exactly the kind of drift that is invisible until a formant shift
    lands on the wrong side of a boundary — so the arithmetic has one definition and both the
    cut and the rebase read it from here.
    """
    rate = _framerate(wav_bytes)
    offset = max(0, int((span.start_s - pad_s) * rate)) / rate
    return slice_wav(wav_bytes, span.start_s, span.end_s, pad_s=pad_s), offset


def cut(wav_bytes: bytes, span: Span, *, pad_s: float = PAD_S) -> bytes:
    """`slice_wav` for a span. The form callers actually want."""
    return slice_wav(wav_bytes, span.start_s, span.end_s, pad_s=pad_s)


def rebase(
    times: Sequence[tuple[float, ...]], offset: float, duration: float
) -> list[tuple[float, ...]]:
    """Shift times from the full recording's clock onto a cut span's, dropping what falls out.

    Every correction in `accent_resynth` takes times on the clock of the audio it is handed.
    Feeding it full-recording times alongside a one-second cut would place every point past
    the end, which reads as "the model contour does not overlap this recording" rather than as
    the arithmetic error it is.
    """
    moved: list[tuple[float, ...]] = []
    for row in times:
        shifted = (*(value - offset for value in row[:-1]), row[-1])
        if all(0.0 <= value <= duration for value in shifted[:-1]):
            moved.append(shifted)
    return moved


# --- Arrival bands ------------------------------------------------------------------------------
# The dataclass lives here rather than in the generated module, the same way `ReferenceVowel`
# lives in `vowel_reference` and `model_reference` reuses it: a generated file should carry
# numbers, not type definitions, or regenerating it can silently change a contract.


@dataclass(frozen=True)
class Band:
    """Where native talkers actually sit on one metric, and how far apart they sit.

    `sd` is a **between-talker** spread — how far the reference voices are from each other,
    not the variation within one voice's tokens. Those answer different questions, and `voices`
    is how a surface can tell which it is holding. Same distinction `ReferenceVowel.voices`
    carries for the vowel tables.
    """

    metric: str
    mean: float
    sd: float
    voices: int

    def contains(self, value: float | None, *, width: float = 1.0) -> bool:
        """Whether `value` sits inside the band — arrival, in one call.

        `width` is in standard deviations. One SD is the default because it is the honest
        reading of "where native talkers sit": widening it to two would call almost anything
        arrived, which is the flattering direction this project refuses everywhere else.

        A band with no spread (every voice identical, or a single voice) cannot answer this
        and returns False rather than accepting everything.
        """
        if value is None or self.sd <= 0.0:
            return False
        return abs(value - self.mean) <= self.sd * width

    def distance(self, value: float | None) -> float | None:
        """How far outside the band `value` sits, in SDs. Zero when inside it."""
        if value is None or self.sd <= 0.0:
            return None
        return max(0.0, (abs(value - self.mean) - self.sd) / self.sd)


# --- Measuring a span ----------------------------------------------------------------------------
# The user's re-attempt and the reference renderings go through THESE functions, both of them.
# `scripts/build_ladder_reference.py` builds the bands by calling them over the model voices,
# and the practice surface judges an attempt by calling them over the speaker. One definition,
# so a band and the comparison against it cannot end up measuring different quantities.


# The metrics each rung is judged on. The sound rung is absent on purpose: `model_reference`
# already carries a between-voice formant spread per vowel, and publishing a second one would
# be two sources of truth for one claim.
METRICS: Mapping[Rung, tuple[str, ...]] = {
    Rung.WORD: ("relative_duration",),
    Rung.SENTENCE: ("pitch_range_st", "terminal_slope_st", "npvi"),
    Rung.PARAGRAPH: ("pitch_range_st", "terminal_slope_st", "npvi"),
}

# **Azure's own prosody score is deliberately not a metric here**, though it is measured, stored
# and shown everywhere else in the app. Across the sixteen reference voices it spans 89.50 to
# 90.73 — an SD of 0.40 on a 0-100 scale, against 4% for rhythm and 20% for pitch range. That
# spread is not a model of how far native TALKERS sit from each other; it is a measure of how
# uniform one synthesiser is, and a black-box score gives no way to separate the two.
#
# Using it would have been worse than useless rather than merely weak. `Verdict.resolved`
# requires every judgeable metric to clear, so a band a real speaker cannot land inside would
# permanently block the paragraph rung — and every sentence beneath it, since a sentence is not
# resolved until its paragraph survives. The whole ladder would jam above the word rung.
#
# The acoustic metrics do not have this problem: they are measured from the signal by this
# project's own code, so a spread across voices is a spread in something real. Keeping prosody
# out also removes a seam the plan had accepted — every rung is now judged by one instrument.

METRIC_LABELS: Mapping[str, str] = {
    "relative_duration": "length relative to your own average word",
    "pitch_range_st": "pitch range",
    "terminal_slope_st": "terminal fall",
    "npvi": "rhythm (nPVI)",
}


def track_within(track: Sequence[tuple[float, float]], span: Span) -> list[tuple[float, float]]:
    """The pitch points inside one span. Unpadded — a unit is measured on itself.

    The playback cut carries `PAD_S` either side so a word does not begin mid-burst; a
    measurement must not, or every span would be judged partly on its neighbours.
    """
    return [(time_s, hz) for time_s, hz in track if span.start_s <= time_s <= span.end_s]


def mean_word_seconds(words: Sequence[Mapping[str, object]]) -> float | None:
    """The reading's own average spoken word length. The divisor for `relative_duration`."""
    lengths = [span.seconds for span in word_spans(words) if span.seconds > 0]
    if not lengths:
        return None
    return sum(lengths) / len(lengths)


def scalars(
    span: Span,
    words: Sequence[Mapping[str, object]],
    track: Sequence[tuple[float, float]],
    *,
    divisor: float | None = None,
) -> dict[str, float]:
    """Every metric measurable for `span`, keyed as `METRICS` names them.

    `divisor` is the reading's mean word length, passed in rather than recomputed per span —
    it is a property of the whole reading, and at word rung this would otherwise be computed
    once per word.
    """
    found: dict[str, float] = {}

    if span.rung is Rung.WORD:
        if divisor and span.seconds > 0:
            found["relative_duration"] = span.seconds / divisor
        return found

    if span.rung is Rung.SOUND:
        # Judged on vowel position against `model_reference.sd50`, not here.
        return found

    inside = track_within(track, span)
    spread = accent_charts.pitch_range_semitones(inside)
    if spread is not None:
        found["pitch_range_st"] = float(spread)
    slope = accent_charts.terminal_slope_semitones(inside)
    if slope is not None:
        found["terminal_slope_st"] = float(slope)

    measured = rhythm.npvi([words[index] for index in span.word_indices])
    if measured.npvi is not None:
        found["npvi"] = float(measured.npvi)

    return found


# --- The movement half: a noise floor for these metrics -------------------------------------------
# `vowel_measure.NoiseFloor` is per-vowel and in formant z-units, so it answers for the SOUND
# rung and nothing else. The metrics the other three rungs are judged on — pitch range, terminal
# fall, rhythm, relative length — have no floor there, and measured resolution needs one for
# each: arrival alone would let a lucky reading graduate something.
#
# The construction is deliberately the same one, for the same reason: the displacement between
# two reads of the same passage in one sitting, at least `CALIBRATION_GAP_MINUTES` apart, IS how
# much these numbers wander with no learning in between.


@dataclass(frozen=True)
class MetricFloor:
    """How far each metric moves between two readings with no learning in between.

    Pooled across units rather than held per unit: one sentence contributes exactly one pair,
    which is not enough to characterise anything. Every sentence in the passage contributing
    to one per-metric floor is both more robust and the more conservative reading — the median
    absolute displacement across units is larger than the smallest of them, so it can only
    refuse to call something progress until the change is bigger.
    """

    per_metric: Mapping[str, float]
    units: int

    def band_for(self, metric: str) -> float | None:
        return self.per_metric.get(metric)

    def within_noise(self, metric: str, movement: float | None) -> bool:
        """Whether a change is too small to be called movement.

        Unmeasurable counts as within noise, and a metric with no floor at all counts as
        within noise too — both are 'this cannot be called progress', which is the direction
        that refuses rather than flatters.
        """
        if movement is None:
            return True
        band = self.band_for(metric)
        return band is None or abs(movement) < band


def metric_noise_floor(
    first: Mapping[int, Mapping[str, float]], second: Mapping[int, Mapping[str, float]]
) -> MetricFloor:
    """Per-metric displacement between two calibration reads, pooled across units.

    `first` and `second` are `{unit index: {metric: value}}` for the same passage read twice.
    A unit measured in only one of the two reads contributes nothing — there is no
    displacement to take.
    """
    gathered: dict[str, list[float]] = {}
    units = 0
    for index, values in first.items():
        other = second.get(index)
        if other is None:
            continue
        counted = False
        for metric, value in values.items():
            if metric not in other:
                continue
            gathered.setdefault(metric, []).append(abs(float(other[metric]) - float(value)))
            counted = True
        units += 1 if counted else 0
    return MetricFloor(
        per_metric={
            metric: statistics.median(values) for metric, values in gathered.items() if values
        },
        units=units,
    )


# --- The verdict ----------------------------------------------------------------------------------


# Both bars have to clear, and neither is enough alone. Arrival without movement is a reading
# that happened to land — it says nothing about whether the speaker changed. Movement without
# arrival is real progress that has not got there yet, which is worth showing and is not
# resolution. The brief's rule, kept as an `and` rather than a preference.
RESOLVED_NOTE = (
    "Resolved needs both: inside the range native talkers occupy, AND a change bigger than "
    "your own session-to-session variation. Either one alone is not enough."
)


@dataclass(frozen=True)
class MetricVerdict:
    """One metric on one attempt: where it landed, and whether that counts."""

    metric: str
    value: float | None
    band: Band | None
    arrived: bool
    # How far outside the band, in SDs. 0.0 when inside; None when it cannot be judged.
    distance_sd: float | None
    # None when there is no earlier attempt to compare against — not False, because
    # "first attempt" and "did not move" are different states and only one is a failure.
    moved: bool | None

    @property
    def resolved(self) -> bool:
        return self.arrived and self.moved is True

    @property
    def judgeable(self) -> bool:
        """Whether this metric can be judged at all on this recording."""
        return self.value is not None and self.band is not None


@dataclass(frozen=True)
class Verdict:
    """Every metric for one span, and whether the problem on it is resolved."""

    rung: Rung
    metrics: tuple[MetricVerdict, ...]

    def for_metric(self, metric: str) -> MetricVerdict | None:
        return next((m for m in self.metrics if m.metric == metric), None)

    @property
    def judgeable(self) -> tuple[MetricVerdict, ...]:
        return tuple(m for m in self.metrics if m.judgeable)

    @property
    def resolved(self) -> bool:
        """Resolved when every judgeable metric has cleared both bars.

        A rung with nothing judgeable on this recording is NOT resolved: `all(())` is True and
        would silently graduate a problem the instruments could not see. That is the one
        failure mode this property exists to refuse.
        """
        judgeable = self.judgeable
        return bool(judgeable) and all(m.resolved for m in judgeable)


def verdict(
    span: Span,
    values: Mapping[str, float],
    bands: Mapping[str, Band],
    *,
    previous: Mapping[str, float] | None = None,
    floor: MetricFloor | None = None,
) -> Verdict:
    """Judge one attempt at one span against both bars.

    `previous` is the attempt this one is compared against — the first attempt on this problem,
    not the one before it, so ten repetitions are ten measurements of the same gap rather than
    a random walk that can drift into looking like progress.
    """
    judged: list[MetricVerdict] = []
    for metric in METRICS.get(span.rung, ()):
        value = values.get(metric)
        band = bands.get(metric)
        arrived = band.contains(value) if band is not None else False
        distance = band.distance(value) if band is not None else None

        moved: bool | None = None
        if previous is not None and metric in previous and value is not None:
            movement = value - float(previous[metric])
            moved = floor is not None and not floor.within_noise(metric, movement)

        judged.append(
            MetricVerdict(
                metric=metric,
                value=value,
                band=band,
                arrived=arrived,
                distance_sd=distance,
                moved=moved,
            )
        )
    return Verdict(rung=span.rung, metrics=tuple(judged))
