"""Assembling one practice unit: what you said, a native saying it, and the bands to judge it.

Streamlit-free and network-free, like `ladder` itself. It takes readings that have already been
loaded — the speaker's words and audio, and the stored model renderings — and answers the three
questions the practice surface asks of every unit:

1. **Which units are practisable on this recording?** (`units`)
2. **Which stored voice should play the native leg, and what does it sound like for this unit?**
   (`native_leg`)
3. **What does native-like mean here?** (`bands_for`)

The surface itself does the Streamlit and the database; this does the arithmetic, so it can be
tested without either.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import accent_resynth
import acoustics
import ladder
import ladder_reference
import shadowing
from ladder import Alignment, Band, Rung, Span


@dataclass(frozen=True)
class NativeLeg:
    """One stored voice's rendering of the same unit, cut to the unit."""

    voice: str
    audio: bytes
    span: Span


@dataclass(frozen=True)
class Unit:
    """One practisable thing on one recording, with what it is judged against.

    `script_index` is the unit's position in the SCRIPT — the sentence index, or the reference
    word index — never its position in the recognised word list. That is what the bands are
    keyed on, and what survives a stumble.
    """

    span: Span
    script_index: int | None
    bands: Mapping[str, Band]

    @property
    def judgeable(self) -> bool:
        """Whether anything here can be resolved. A unit with no band never can."""
        return bool(self.bands)


def nearest_voice(median_f0_hz: float | None) -> str | None:
    """The reference voice closest to this speaker's own pitch, or None with nothing to go on.

    **Matched on pitch rather than on sex.** Sex-matching needs the live voice roster, which is
    a network call the practice surface should not depend on, and it is a proxy for what
    actually matters anyway: how comparable the two voices sound. The stored medians come out
    cleanly bimodal (104-150 Hz against 179-224 Hz), so this sex-matches on its own and also
    discriminates within a sex, which sex-matching cannot.

    The native leg is one voice because audio cannot be averaged. The pitch TARGET stays the
    sixteen-voice mean, since what should transfer is the General American tendency rather than
    one synthesiser's habits — the two are different jobs and only one of them has to be heard.
    """
    if median_f0_hz is None or not ladder_reference.MEDIAN_F0_HZ:
        return None
    return min(
        ladder_reference.MEDIAN_F0_HZ,
        key=lambda voice: abs(ladder_reference.MEDIAN_F0_HZ[voice] - median_f0_hz),
    )


def covers(reference_text: str | None) -> bool:
    """Whether the arrival bands describe THIS passage.

    The bands are keyed by position — sentence index, script word index — so an index lookup
    alone would hand sentence 0 of any text the numbers measured from sentence 0 of the
    benchmark passage. That is not a thin reference or a missing band; it is a confident wrong
    answer, and the speaker would be judged against sentences they never read.

    Checked against the stored sentence TEXT rather than a passage identity, so editing a word
    of the benchmark invalidates the bands the same way it invalidates the progress series.
    """
    if not reference_text:
        return False
    return tuple(shadowing.phrases(reference_text)) == tuple(
        ladder_reference.SENTENCE_TEXT[index] for index in sorted(ladder_reference.SENTENCE_TEXT)
    )


def bands_for(
    rung: Rung, script_index: int | None, reference_text: str | None
) -> Mapping[str, Band]:
    """What native-like means for this unit. Empty when the reference has nothing for it."""
    if not covers(reference_text):
        return {}
    if rung is Rung.PARAGRAPH:
        return ladder_reference.PARAGRAPH
    if script_index is None:
        return {}
    if rung is Rung.SENTENCE:
        return ladder_reference.SENTENCE.get(script_index, {})
    if rung is Rung.WORD:
        return ladder_reference.WORD.get(script_index, {})
    # The sound rung is judged on vowel position against `model_reference.sd50`, not here.
    return {}


def _script_index(span: Span, alignment: Alignment) -> int | None:
    """Where this unit sits in the script, or None when it stands for nothing in it."""
    if span.rung is Rung.PARAGRAPH:
        return None
    if span.rung is Rung.SENTENCE:
        found = {
            alignment.sentence_of_word[i]
            for i in span.word_indices
            if i in alignment.sentence_of_word
        }
        return found.pop() if len(found) == 1 else None
    return alignment.reference_word_of_word.get(span.word_indices[0])


def units(
    words: Sequence[Mapping[str, object]], reference_text: str | None, rung: Rung
) -> list[Unit]:
    """Every practisable unit at `rung` on this recording, in the order it was spoken."""
    alignment = ladder.align(words, reference_text)
    found: list[Unit] = []
    for span in ladder.spans(words, rung, reference_text):
        index = _script_index(span, alignment)
        found.append(
            Unit(
                span=span,
                script_index=index,
                bands=bands_for(rung, index, reference_text),
            )
        )
    return found


def native_leg(
    span: Span,
    mine: Alignment,
    theirs: Alignment,
    their_words: Sequence[Mapping[str, object]],
    their_audio: bytes,
    voice: str,
) -> NativeLeg | None:
    """The same unit, cut out of one model voice's reading. None when it has no counterpart.

    A unit with no counterpart is an honest None rather than the nearest thing: an inserted
    word stands for nothing in the script, so there is no native version of it to play, and
    playing a neighbouring word as if it were the same one would be worse than playing nothing.
    """
    playable = span if span.rung in ladder.AUDIBLE else None
    if playable is None:
        # A sound has no audio worth playing on its own — it is heard inside its word. Walking
        # it up needs the speaker's own word list, which this function is not given, so the
        # caller does it with `ladder.audible_span_for` and passes the word span in. Returning
        # None here rather than guessing is the honest half of that split.
        return None
    their_spans = (
        ladder.spans(their_words, span.rung, None)
        if span.rung is Rung.PARAGRAPH
        else ladder.spans(their_words, span.rung, " ".join(theirs.phrases))
    )
    other = ladder.matching_span(playable, mine, theirs, their_words, their_spans)
    if other is None:
        return None
    try:
        audio = ladder.cut(their_audio, other)
    except ladder.LadderError:
        return None
    return NativeLeg(voice=voice, audio=audio, span=other)


# --- Corrections at rung scale --------------------------------------------------------------------
# Each of the three above corrects a whole recording. On the ladder the unit is a word, a
# sentence or a paragraph, so the correction has to act on a span of one.
#
# **The span is cut first, then corrected as a clip in its own right.** The alternative —
# correcting in place and splicing the result back between two untouched ends — is what
# `corrected_vowel` has to do, and it is the path where Praat's empty-range `Extract part`
# returns the whole recording. Cutting first with `ladder.slice_wav` (plain PCM frame
# arithmetic, no Praat) means pitch and timing never touch that path at all. `corrected_vowel`
# still splices, inside the cut, and still guards it with `_MIN_PART_S`.
#
# **No stacking.** Three separate corrections, each changing exactly one thing, so "your voice,
# one thing changed" stays literally true and no clip compounds three manipulations into
# something that has stopped sounding like the speaker.


def corrected_pitch_in(
    wav_bytes: bytes,
    span: Span,
    target: Sequence[tuple[float, float]],
    *,
    max_shift: float = accent_resynth.MAX_PITCH_SHIFT_SEMITONES,
) -> accent_resynth.Resynthesis:
    """`corrected_pitch`, applied to one rung's span. `target` is on the full clock."""
    audio, offset = ladder.cut_with_offset(wav_bytes, span)
    duration = acoustics.load(audio).duration
    moved = [(time_s, value) for time_s, value in ladder.rebase(target, offset, duration)]
    if not moved:
        raise accent_resynth.ResynthesisError(
            "The model contour does not reach this part of the recording, so there is nothing "
            "to correct toward here."
        )
    return accent_resynth.corrected_pitch(audio, moved, max_shift=max_shift)


def corrected_timing_in(
    wav_bytes: bytes,
    span: Span,
    stretches: Sequence[tuple[float, float, float]],
) -> accent_resynth.Resynthesis:
    """`corrected_timing`, applied to one rung's span. `stretches` are on the full clock.

    A vowel straddling the cut edge is dropped rather than clipped: stretching half a vowel
    would report a timing correction that was never applied to the sound the listener hears.
    """
    audio, offset = ladder.cut_with_offset(wav_bytes, span)
    duration = acoustics.load(audio).duration
    moved = [
        (start, end, ratio) for start, end, ratio in ladder.rebase(stretches, offset, duration)
    ]
    if not moved:
        raise accent_resynth.ResynthesisError(
            "No vowel inside this unit has both a measurement and a target length to move toward."
        )
    return accent_resynth.corrected_timing(audio, moved)


def corrected_vowel_in(
    wav_bytes: bytes,
    span: Span,
    start_s: float,
    end_s: float,
    produced_f2_hz: float,
    target_f2_hz: float,
    *,
    fraction: float = accent_resynth.MAX_FORMANT_FRACTION,
) -> accent_resynth.Resynthesis:
    """`corrected_vowel`, applied to one vowel inside one rung's span."""
    audio, offset = ladder.cut_with_offset(wav_bytes, span)
    duration = acoustics.load(audio).duration
    first, last = start_s - offset, end_s - offset
    if first < 0.0 or last > duration or last <= first:
        raise accent_resynth.ResynthesisError(
            "That vowel does not sit inside the unit being practised."
        )
    return accent_resynth.corrected_vowel(
        audio, first, last, produced_f2_hz, target_f2_hz, fraction=fraction
    )
