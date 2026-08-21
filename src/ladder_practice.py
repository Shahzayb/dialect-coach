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
        # A sound is heard inside its word, so walk up one rung before matching.
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
