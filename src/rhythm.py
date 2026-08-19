"""Rhythm measured from phoneme durations: nPVI over vocalic intervals.

nPVI — the normalised Pairwise Variability Index — asks how much each vowel differs in
length from the vowel that follows it, averaged over a reading and normalised so that
speaking faster or slower does not move it. Stress-timed English varies a lot: a stressed
vowel is held and the unstressed ones around it are crushed down to schwa, so successive
vowels are very unequal. Syllable-timed languages give each syllable closer to equal time,
and carrying that rhythm into English is one of the most recognisable prosodic markers of
second-language speech — audible as an accent long before any individual sound is.

This module is a pure reader of the normalised word shape `speech_analyzer.normalise`
produces, in the same family as `is_flagged` and `delivery_faults`. No Streamlit, no network,
no SDK — the same boundary `progress_view.py` sits on.

## Which comparison this number supports

**Not the published one.** General American nPVI bands in the literature come from
hand-segmented corpora reading different material, and nPVI is sensitive both to the
segmentation method and to the text. Scoring Azure-derived durations against a published band
compares three things at once, and the absolute number that falls out is close to meaningless.

That is measured here, not assumed. Four defensible segmentation choices applied to the *same*
unchanged recording (tests/fixtures/sample_azure_response.json) give:

    raw durations, merged intervals, pauses break pairs   55.85   <- what this module computes
    raw durations, one interval per phoneme               56.25
    raw durations, pairs allowed to span pauses           54.75
    durations read as one frame longer (see below)        ~50.3

A 5.4-point spread from policy alone, before any question of corpus, speaker or text — wider
than several published cross-language contrasts within stress-timed varieties.

**The comparison this supports is `baseline()`**: the same benchmark passage, rendered by
Azure TTS, pushed through this same assessment pipeline and this same code. Same segmenter,
same text, one variable. It is a fixed reference point, *not* ground truth for "native" — it
is a synthesiser, and a synthesiser's rhythm is its own. What makes it worth having is that it
does not move, so a change in the gap between it and a reading is a change in the reading.

Published bands are deliberately given no chart ink anywhere in this project. With a
same-pipeline baseline on the same axes, plotting a band that cannot be compared to it would
only invite the comparison.

## Three biases worth knowing about

`speech_analyzer._timing` establishes that Azure appears to report `Duration` as
`(frames - 1) * 10 ms`, so every segment's true extent is about 10 ms longer than stated. The
durations here are the raw reported values. That shortfall costs a 40 ms vowel 25% and a
320 ms vowel 3%, so short intervals shrink further than long ones and nPVI comes out high —
one more reason the published bands are not comparable. It cancels against the baseline, which
is measured through this identical code.

nPVI also rises with a slower, more deliberate reading and falls with a rushed one, on the
same speaker. The benchmark passage exists so that at least the text is held still.

**The audio codec is worth more than you would guess.** Measured against Azure live: one
unchanged take of the same passage scores 55.26 as a WAV and 53.10 as an m4a — same duration,
same words, same 25 pairs, a 2.16-point swing from lossy compression alone. That is larger
than the seam bias above. Azure's segmentation is otherwise **exactly reproducible**: the same
bytes assessed twice returned identical durations to the tick. So a change in this number is
trustworthy only when the recording format has not changed underneath it, and a reading
uploaded from a phone is not comparable to one recorded in the browser.
"""

from __future__ import annotations

import itertools
import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import phoneme_reference
import speech_analyzer

logger = logging.getLogger(__name__)

# What counts as vocalic. `phoneme_reference` already classifies every symbol Azure emits as
# consonant / vowel / diphthong / r-coloured, and its `normalise` maps alias spellings onto
# Azure's own — so this reuses that table rather than restating an inventory that would then
# have two places to drift from. Verified against every committed fixture: all 39 distinct
# symbols resolve, none missing.
VOCALIC_KINDS = frozenset({"vowel", "diphthong", "r-coloured"})

# A gap in the phoneme stream wider than this ends the run, and no pair spans it. A pause is
# not a short vowel, and pairing the vowel before a breath with the one after it measures the
# breath. The fixture's own gaps are cleanly bimodal — one frame (the seam), a handful at
# 30 ms, then real pauses at 210 ms — and the resulting nPVI is flat at 55.85 for any
# threshold from 50 to 200 ms, so this sits in the middle of a plateau rather than on an edge.
PAUSE_BREAK_MS = 100.0

# Below this many pairs the figure is noise and `npvi` is None rather than a number. Chosen
# against the data: the ~13 s drill fixture yields 25 pairs, so an ordinary short read clears
# it and anything shorter does not; the 196-word benchmark passage yields several hundred.
MIN_PAIRS = 20

_FRAME_MS = speech_analyzer.FRAME_TICKS / speech_analyzer.TICKS_PER_MS

BASELINE_FIXTURE = speech_analyzer.FIXTURE_DIR / "benchmark_tts_baseline.json"

# Prefixed onto the `reference_text` of the attempts row `scripts/capture_baseline.py` writes.
#
# The capture is a real assessment and really costs quota, so it must be recorded — the meter
# derives from the attempts table and skipping the row would silently under-report the spend.
# But the text it was assessed against IS the benchmark passage, so without a marker
# `progress_view.is_benchmark` matches it and the synthesiser's reading is plotted as the
# user's own: the trajectory gains a point nobody spoke, and "benchmark last read today" is
# true of a machine. The prefix keeps the row honest about the money and honest about whose
# voice it was.
BASELINE_CAPTURE_MARKER = "[tts rhythm baseline capture]"


def is_baseline_capture(reference_text: str | None) -> bool:
    """Whether a stored attempt is the TTS baseline capture rather than a spoken reading."""
    return bool(reference_text) and reference_text.startswith(BASELINE_CAPTURE_MARKER)


@dataclass(frozen=True)
class Rhythm:
    """One reading's rhythm measurement, with enough context to judge whether to trust it.

    `npvi` is None when there was not enough connected speech to measure. The counts are
    reported either way — "we measured nothing" and "we measured 4 pairs" are different
    answers, and a caller deciding what to render needs the second one.
    """

    npvi: float | None
    pairs: int  # adjacent differences actually averaged
    intervals: int  # vocalic intervals found
    runs: int  # stretches of speech the pauses cut the reading into

    @property
    def measured(self) -> bool:
        return self.npvi is not None


def is_vocalic(symbol: str | None) -> bool:
    """Whether an Azure phoneme symbol is a vowel, diphthong or r-coloured vowel."""
    entry = phoneme_reference.lookup(symbol)
    return entry is not None and entry.kind in VOCALIC_KINDS


def _phonemes_in_time_order(words: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Every timed phoneme across the reading, sorted by when it was spoken.

    Sorted rather than assumed in order. Words arrive in order from a single payload, but a
    paragraph is several payloads concatenated, and Mode B's local miscue diff splices
    omissions into that list at reference positions — an omission carries no timing at all and
    is dropped here rather than treated as a zero-length event at the start of the recording.
    """
    timed = [
        phoneme
        for word in words
        for phoneme in (word.get("phonemes") or [])
        if phoneme.get("offset_ticks") is not None and phoneme.get("duration_ticks") is not None
    ]
    return sorted(timed, key=lambda p: p["offset_ticks"])


def vocalic_intervals(words: Sequence[Mapping[str, Any]]) -> list[list[float]]:
    """Vocalic interval durations in ms, grouped into runs of uninterrupted speech.

    A **vocalic interval** is a contiguous stretch of vowel, which is not the same thing as a
    phoneme: adjacent vocalic phonemes are one interval, not two. This happens for real — the
    drill fixture has "rather" ending in /ɚ/ followed by "unpredictable" opening on /ʌ/, which
    is one continuous piece of vowel across a word boundary, and the classic measure is
    defined over exactly that. Splitting it in two would invent a pair of equal neighbours and
    pull the index down.

    Contiguity is judged against the one-frame seam `speech_analyzer._timing` documents, since
    consecutive segments never share an edge exactly.

    An interval's length is its **span** — last reported end minus first reported start — and
    not the sum of its phonemes' durations. For an interval of one phoneme the two are the
    same thing. For a merged one they are not, and the span is the consistent choice: on the
    frame-grid reading every interval then under-reports by exactly one frame whichever way it
    was built, where summing durations would under-report by one frame *per phoneme* and make
    the bias depend on how many phonemes happened to compose the interval. On the committed
    fixture the difference is 55.85 against 55.72 — small, but uneven bias is worth avoiding
    for free.

    A **run** ends wherever the phoneme stream gaps by more than `PAUSE_BREAK_MS`. Runs are
    the unit `npvi` pairs within, so no pair ever spans a pause or an utterance boundary.
    """
    runs: list[list[float]] = []
    current: list[float] = []
    open_interval: tuple[int, int] | None = None  # (first start, last end), in ticks
    previous_end: int | None = None

    def close() -> None:
        nonlocal open_interval
        if open_interval is not None:
            start, end = open_interval
            current.append((end - start) / speech_analyzer.TICKS_PER_MS)
            open_interval = None

    for phoneme in _phonemes_in_time_order(words):
        start, length = phoneme["offset_ticks"], phoneme["duration_ticks"]
        gap_ms = (
            None if previous_end is None else (start - previous_end) / speech_analyzer.TICKS_PER_MS
        )

        if gap_ms is not None and gap_ms > PAUSE_BREAK_MS:
            close()
            runs.append(current)
            current = []

        if is_vocalic(phoneme.get("phoneme")):
            if open_interval is not None and gap_ms is not None and gap_ms <= _FRAME_MS:
                open_interval = (open_interval[0], start + length)
            else:
                close()
                open_interval = (start, start + length)
        else:
            close()

        previous_end = start + length

    close()
    runs.append(current)
    return [run for run in runs if run]


def npvi(words: Sequence[Mapping[str, Any]]) -> Rhythm:
    """The normalised Pairwise Variability Index over this reading's vocalic intervals.

        nPVI = 100 / (m - 1) * SUM |d_k - d_k+1| / ((d_k + d_k+1) / 2)

    Each difference is divided by the mean of the pair it came from, which is what makes the
    index a statement about *variability* rather than about speaking rate: halve every
    duration and the number does not move.

    Summed over every within-run adjacent pair and divided by the total pair count, so a
    reading broken into several runs is one measurement rather than an average of averages
    that would over-weight a two-vowel fragment.
    """
    runs = vocalic_intervals(words)
    # A zero-length interval would divide by zero, and a pair of them has no meaningful
    # difference. None occur in any committed fixture — this is a guard, not a code path.
    runs = [[d for d in run if d > 0] for run in runs]
    runs = [run for run in runs if len(run) > 1]

    intervals = sum(len(run) for run in runs)
    pairs = sum(len(run) - 1 for run in runs)
    if pairs < MIN_PAIRS:
        return Rhythm(npvi=None, pairs=pairs, intervals=intervals, runs=len(runs))

    total = sum(
        abs(first - second) / ((first + second) / 2)
        for run in runs
        for first, second in itertools.pairwise(run)
    )
    return Rhythm(npvi=100.0 * total / pairs, pairs=pairs, intervals=intervals, runs=len(runs))


# --- The baseline ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Baseline:
    """The benchmark passage as Azure TTS reads it, measured through this same code.

    `voice` is not decoration. A different `AZURE_TTS_VOICE` is a different baseline, and one
    whose provenance is unrecorded cannot be told apart from the reading it is meant to
    anchor. The UI names it.
    """

    rhythm: Rhythm
    voice: str
    captured_at: str


_baseline: Baseline | None = None
_baseline_loaded = False


def baseline(path: Path | None = None) -> Baseline | None:
    """The captured TTS baseline, or None if it has not been captured on this machine.

    None is a state the UI renders, not an error: the fixture is produced by
    `scripts/capture_baseline.py`, which spends real quota and is run once by hand. Every
    consumer must therefore have something honest to say without it.

    Cached after the first read — the Progress tab renders on every Streamlit rerun.
    """
    global _baseline, _baseline_loaded
    if path is not None:
        return _read_baseline(path)
    if not _baseline_loaded:
        _baseline = _read_baseline(BASELINE_FIXTURE)
        _baseline_loaded = True
    return _baseline


def _read_baseline(path: Path) -> Baseline | None:
    if not path.exists():
        return None
    try:
        captured = json.loads(path.read_text(encoding="utf-8"))
        payloads = captured["payloads"]
        _, _, words = speech_analyzer.normalise(
            payloads, captured.get("reference_text") or "", speech_analyzer.Mode.PARAGRAPH
        )
        return Baseline(
            rhythm=npvi(words),
            voice=str(captured.get("voice") or "unknown"),
            captured_at=str(captured.get("captured_at") or "unknown"),
        )
    except Exception:  # a corrupt baseline is a missing one, not a crash
        logger.warning(
            "Baseline at %s could not be read; treating it as absent", path, exc_info=True
        )
        return None


def reset_baseline_cache() -> None:
    """Drop the memoised baseline. For tests, and for after a fresh capture."""
    global _baseline, _baseline_loaded
    _baseline, _baseline_loaded = None, False
