"""Shadowing — speaking along with a synthesised model, in real time.

Every chunk before this one measures. The perception trainer trains, but it trains
*listening*. Rhythm, linking and intonation are not learned from a score; they are learned by
speaking along with a model, and the app already synthesises one for any text. Shadowing is
the one place in this design where the user practises against a model **while speaking**
rather than reading a report afterwards.

Two modes, and only one of them is ever assessed. That split is a finding, not a preference:

- **Simultaneous** — one continuous clip of the whole passage, normal or slow. Press record,
  press play, speak along. The recording is continuous, so its fluency and prosody are
  directly comparable to a cold read of the same passage. That comparability is the whole
  acceptance test, so this is the mode that gets assessed.
- **Echo** — the passage split into sentences, each clip followed by a silence matched to
  that clip's own duration, concatenated into one track. Press play, repeat in the gaps.
  **Never assessed.** Its recording would carry a structural pause between every phrase, so
  Azure would depress its fluency score by construction and the delta against a cold read
  would measure the format rather than the speaker. Offering it as a warm-up is honest;
  scoring it would not be.

Both modes play a voice while the microphone is open, so **headphones are a requirement**, not
a suggestion — on speakers, Azure hears the model as well as the speaker and assesses a
mixture.

The assessed read goes through the ordinary Mode B path and is stored as an ordinary attempt.
Nothing in the analysis pipeline changes: no new parsing branch, no new normalised shape, no
new merge rule. The only thing added to the row is a tag, so the comparison below is a query
rather than a re-run.

This module is **pure**: no Streamlit, no database, no network, no clock. Passages are passed
in rather than restated here (`app.PRESETS[Mode.PARAGRAPH]` is already exactly the set), on
the same boundary `progress_view.py`, `rhythm.py`, `perception_trainer.py` and
`practice_queue.py` all hold.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Mapping, Sequence

import utils

logger = logging.getLogger(__name__)

# --- Identity ---------------------------------------------------------------------------------

# The tag written on an assessed shadowed attempt. One string, read by the writer, by the
# progress readers that must keep shadowed reads off the cold trajectory, and by the
# comparison that pairs the two — so there is no spelling for them to drift apart on.
SHADOW_TAG = "shadowed"

SIMULTANEOUS = "simultaneous"
ECHO = "echo"

MODE_LABELS: Mapping[str, str] = {
    SIMULTANEOUS: "Speak along with it",
    ECHO: "Repeat after each phrase",
}


def passage_key(text: str | None) -> str:
    """The normalised identity of a passage.

    The same tokeniser `progress_view.benchmark_key` and the miscue diff run on, so a passage
    can never split into two series over casing, punctuation or a line break — which matters
    more here than anywhere else, since the comparison pairs a shadowed read to a cold read
    **by matching this key**. That is also why the baseline capture's trick of prefixing a
    marker onto `reference_text` is not reusable for the shadow tag: it would break exactly
    the match this feature depends on.
    """
    return " ".join(utils.normalise_words(text or ""))


def title_for(passages: Mapping[str, str], text: str | None) -> str | None:
    """The on-screen name of a passage, matched by key rather than by string equality."""
    key = passage_key(text)
    for title, passage in passages.items():
        if passage_key(passage) == key:
            return title
    return None


# --- The echo track ---------------------------------------------------------------------------


# Sentence-final punctuation followed by whitespace. Deliberately not a sentence tokeniser: the
# shadowable passages are the app's own presets, written in plain declarative prose with no
# abbreviations, so a regex is the honest amount of machinery. A passage that does not split
# cleanly still works — it comes back as one phrase and the echo track is one long gap.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")

# Below this many characters a "sentence" is a fragment — a stray initial, or the tail of a
# split that went wrong. Merged into its neighbour rather than played as its own phrase, since
# a one-word clip followed by a one-word silence is not shadowing practice.
MIN_PHRASE_CHARS = 12


def phrases(text: str) -> list[str]:
    """Split a passage into the phrases the echo track plays one at a time.

    Paragraph breaks are collapsed first: a blank line is a visual convenience in the passage
    text, not a spoken boundary, and splitting on it would put a silence where the reader does
    not pause.
    """
    flat = " ".join((text or "").split())
    if not flat:
        return []

    found: list[str] = []
    for piece in _SENTENCE_END.split(flat):
        piece = piece.strip()
        if not piece:
            continue
        if found and len(piece) < MIN_PHRASE_CHARS:
            found[-1] = f"{found[-1]} {piece}"
        else:
            found.append(piece)

    # A leading fragment has no previous phrase to join, so it merges forward instead.
    if len(found) > 1 and len(found[0]) < MIN_PHRASE_CHARS:
        found[1] = f"{found[0]} {found[1]}"
        found.pop(0)
    return found


# How much longer the silence is than the phrase that precedes it. A gap exactly as long as the
# model took leaves no room to start, and a shadower who is a beat behind runs into the next
# phrase; this is that beat.
ECHO_TAIL_MS = 400


def echo_seconds(clip_seconds: Sequence[float], *, tail_ms: int = ECHO_TAIL_MS) -> float:
    """How long the finished echo track runs, from its clip durations.

    Exposed so the UI can say the number before building the track — an unassessed warm-up
    that turns out to be three minutes long is worth knowing about in advance.
    """
    return sum(clip_seconds) * 2 + len(clip_seconds) * tail_ms / 1000.0


# --- Scheduling ------------------------------------------------------------------------------
# The cadence itself lives in `practice_queue`, with the other kinds. What is here is only what
# a shadow item *is*: a standing practice against a passage, which never graduates.


@dataclass(frozen=True)
class Session:
    """One planned shadowing session. Carries no audio and no scores — see the module docstring."""

    title: str
    passage: str
    mode: str
    slow: bool

    @property
    def key(self) -> str:
        return passage_key(self.passage)

    @property
    def assessable(self) -> bool:
        """Only a simultaneous read is assessed, and only ever as an ordinary Mode B attempt."""
        return self.mode == SIMULTANEOUS


def evidence_for(session: Session) -> dict[str, object]:
    """What a shadow target stores about itself.

    A shadow item is not promoted from flagged evidence the way a contrast is, so "why is this
    here" has a different and shorter answer: because it was started. Recorded as a fact rather
    than dressed up as a diagnosis.
    """
    return {
        "source": "shadowing",
        "title": session.title,
        "passage_key": session.key,
        "why": (
            f"You started shadowing \"{session.title}\". It is a standing practice rather "
            f"than a flagged sound, so nothing promoted it and nothing takes it off."
        ),
    }


# --- The copy the surface renders -------------------------------------------------------------
# Kept here rather than inline in `app.py` for the same reason `fallback_coach`'s drill
# templates are: it is the substance of the feature, it has to be assertable, and it must not
# differ between the two places it appears.

HEADPHONES = (
    "**Headphones, not speakers.** The model plays while your microphone is open, so on "
    "speakers Azure hears the model as well as you and scores the mixture — the result would "
    "be partly measuring the synthesiser."
)

SIMULTANEOUS_STEPS = (
    "1. Listen once without speaking, so the shape is in your ear.\n"
    "2. Press **Record**, then press play on the model, and speak **with** it — not after it. "
    "Match the timing before you worry about the sounds.\n"
    "3. Stop recording, then assess it. It is stored as an ordinary paragraph attempt."
)

ECHO_STEPS = (
    "Press play and repeat each phrase in the silence after it. Each gap is as long as the "
    "phrase that came before it, so there is room to say the whole thing.\n\n"
    "**This one is not assessed, and that is deliberate.** A recording made of phrases "
    "separated by silences carries a pause between every one of them, so Azure would mark the "
    "delivery down for a gap the format put there. Use it as a warm-up, then do a "
    "speak-along read for the one that counts."
)

SLOW_NOTE = (
    "Slow playback is the on-ramp, not a lesser version: at 35% slower a sound you cannot "
    "catch at conversational speed separates out from its neighbours. Move to the normal rate "
    "once you can stay with it."
)

NOT_A_MEASUREMENT = (
    "Nothing here is scored while you shadow. This is practice against a model, not another "
    "reading you get marked on — the numbers come afterwards, from the ordinary assessment, "
    "and the comparison against a cold read lives on the Progress tab."
)
