"""Forced-choice minimal-pair identification — High Variability Phonetic Training.

Why this exists, from the brief: *"I can't hear the difference between my pronunciation and
a native speaker's."* Playing a target next to an attempt is **exposure**, and exposure is
the weakest intervention available. The established one is HVPT: identify which of a minimal
pair you just heard, spoken by several different talkers, scored immediately, in short daily
blocks. Two findings shape everything below:

1. Perception gains transfer to production, measurably, without any production practice.
2. **Multiple talkers are what make the gain generalise** to new words and new speakers.
   That is not a garnish — a single-voice block trains you to hear one synthesiser.

An adult forms a new second-language sound category only once the sound stops being heard as
a variant of the nearest native one. Drilling production of a contrast you cannot hear is
practising without a target, which is why this comes before any production drill.

This module is **pure**: no Streamlit, no database, no network, no clock. It plans a block
and scores one; `app.py` plays the audio and stores the answers. The randomness is injected
(`rng`), so a block plan is asserted in a test rather than sampled and hoped over — the same
boundary `progress_view.py` and `rhythm.py` already sit on.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from typing import Mapping, Sequence

import phoneme_reference
import utils

logger = logging.getLogger(__name__)

# --- Voices ---------------------------------------------------------------------------------
# VERIFIED BY INTROSPECTION on 2026-08-19, not recalled: `scripts/list_voices.py` calls the
# SDK's `get_voices_async("en-US")` and prints the live roster, which changes without notice.
# Do not add a name here without seeing it in that output. That run also confirmed the listing
# itself charges nothing — the TTS meter read 0 before and 0 after.
#
# Chosen for SPREAD rather than for quality, because the variability IS the intervention. Six
# talkers, three male and three female, deliberately drawn from **two different voice
# generations**: Andrew and Ava are the newer conversational voices, while Aria, Guy, Jenny and
# Tony are the older ones and carry an audibly different recording character. Four voices of
# the same generation would be a single-talker block wearing a disguise, which is the failure
# HVPT exists to avoid — a listener who only ever hears one voice type learns that voice type.
#
# Only plain `…Neural` voices, never the DragonHD or MAI families: `en-US-BrianNeural` is the
# one voice this project has actually seen billed as neural on F0, and the newer families are
# an unverified pricing class.
#
# `en-US-BrianNeural` is deliberately ABSENT even though it is a fine voice: it is the default
# for `AZURE_TTS_VOICE`, the single consistent model "Hear it" plays for imitation everywhere
# else in the app. These two settings must never be collapsed — imitation wants one model,
# identification wants variety, and they pull in opposite directions on purpose.
VOICES: tuple[str, ...] = (
    "en-US-AndrewNeural",
    "en-US-AriaNeural",
    "en-US-GuyNeural",
    "en-US-AvaNeural",
    "en-US-JennyNeural",
    "en-US-TonyNeural",
)

# Below this, refuse to run rather than degrade. Falling back to one voice would still look
# like training on screen while training the wrong thing.
MIN_VOICES = 4


class BlockError(RuntimeError):
    """A block cannot be built. Always says which precondition failed."""


# --- The chance floor -------------------------------------------------------------------------
# Deliberately NOT a tunable in `utils.py` beside WORD_RED and friends. It is arithmetic, not a
# threshold anyone chose: a two-alternative forced choice scores 50% by guessing, full stop.
# It is derived here from the number of alternatives actually on the trial, and stored on every
# trial row, so an accuracy figure can never be reported without the anchor that makes it mean
# something. "62%" against an unstated floor of 50% is noise reported as progress.


def chance_floor(alternatives: int) -> float:
    """What guessing scores on a trial with this many choices, as a 0-1 fraction."""
    if alternatives < 2:
        raise ValueError("A forced choice needs at least two alternatives.")
    return 1.0 / alternatives


def chance_caption(alternatives: int) -> str:
    """The sentence that has to sit beside every accuracy figure. One definition, so it
    cannot be left off one of the places accuracy is shown."""
    floor = chance_floor(alternatives) * 100.0
    return (
        f"{floor:.0f}% is what guessing scores — it is a {alternatives}-way choice, so read "
        f"every figure against that line, not against zero."
    )


# --- What can be trained ------------------------------------------------------------------------

CONTRAST = "contrast"   # a consonant substitution
VOWEL = "vowel"         # a vowel, diphthong or r-coloured substitution — a "vowel gap"
STRESS = "stress"       # not trained here: a stress item is a drill, not a block

# `Phoneme.kind` values that make a substitution a vowel gap rather than a consonant contrast.
# The mechanics of the block are identical; the label and the reason differ, and the queue
# balances one of each rather than letting three consonants crowd the vowels out.
_VOWEL_KINDS = frozenset({"vowel", "diphthong", "r-coloured"})


def kind_for(expected: str) -> str:
    """Whether a substitution of `expected` is a consonant contrast or a vowel gap."""
    entry = phoneme_reference.lookup(expected)
    if entry is None:
        return CONTRAST
    return VOWEL if entry.kind in _VOWEL_KINDS else CONTRAST


def pairs_for(expected: str, produced: str) -> list[tuple[str, str]]:
    """The minimal pairs written up for this substitution, `(expected word, produced word)`.

    Straight through to `phoneme_reference`, which is the single table three consumers read.
    An unwritten pair returns an empty list rather than an invented word.
    """
    return phoneme_reference.minimal_pairs(expected, produced)


def trainable(expected: str, produced: str) -> bool:
    """Whether a block can be built for this substitution at all.

    Two ways to fail, both honest: the pair has no entry in the reference table (eleven
    contrasts carry no pairs, because some substitutions have no minimal pair in English), or
    it has too few to fill a block with enough item variety to be worth calling training.
    """
    return len(pairs_for(expected, produced)) >= utils.MIN_PAIRS_FOR_BLOCK


# --- A block ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Trial:
    """One forced choice: a word, played in one voice, against the other half of its pair."""

    word: str                          # what is actually played
    other: str                         # the other word of the pair — replayable on demand
    alternatives: tuple[str, ...]      # on-screen order, shuffled per trial
    voice: str
    novel: bool                        # this (word, voice) had never been presented before


@dataclass(frozen=True)
class Block:
    """A planned block. Nothing here has been played or answered yet."""

    item: str
    kind: str
    expected: str
    produced: str
    voices: tuple[str, ...]
    trials: tuple[Trial, ...]
    review: bool = False

    @property
    def alternatives(self) -> int:
        return len(self.trials[0].alternatives) if self.trials else 2

    @property
    def novel_count(self) -> int:
        return sum(1 for trial in self.trials if trial.novel)


def build_block(
    *,
    item: str,
    expected: str,
    produced: str,
    heard: Mapping[tuple[str, str], str] | None = None,
    voices: Sequence[str] = VOICES,
    trials: int | None = None,
    rng: random.Random | None = None,
    review: bool = False,
) -> Block:
    """Plan a block for one substitution.

    `heard` maps an already-presented `(word, voice)` to when it was last played, straight
    from `db.heard_stimuli`. It drives the "unseen item" rule: **an unheard (word, voice)
    combination is a genuinely new stimulus**, because voice variety is the active ingredient
    — a familiar word in a voice you have never heard it in is new information about the
    category, not a repeat. Four voices over three to five pairs gives 24-40 novel stimuli,
    which is what makes that definition workable against a reference table that holds three
    to five pairs per contrast.
    """
    rng = rng or random.Random()
    heard = dict(heard or {})
    voice_list = list(dict.fromkeys(voices))  # de-duplicated, order kept

    if len(voice_list) < MIN_VOICES:
        raise BlockError(
            f"A block needs at least {MIN_VOICES} distinct voices and got "
            f"{len(voice_list)}. Voice variety is the part of this that works, so running "
            f"with fewer would look like training while training the wrong thing."
        )

    pair_list = pairs_for(expected, produced)
    if len(pair_list) < utils.MIN_PAIRS_FOR_BLOCK:
        raise BlockError(
            f"/{expected}/ → /{produced}/ has {len(pair_list)} minimal pairs written up and "
            f"needs {utils.MIN_PAIRS_FOR_BLOCK}. There is nothing to drill it with yet."
        )

    wanted = trials or (
        utils.PERCEPTION_REVIEW_TRIALS if review else utils.PERCEPTION_BLOCK_TRIALS
    )

    # Every (word, voice) the pairs allow. Both words of each pair are playable targets — the
    # task is "which did you hear", so the expected word and the produced word each have to
    # come up, or the answer becomes guessable from the question.
    pool: list[tuple[str, str, str, str]] = []  # (word, other, voice, last heard or "")
    for first, second in pair_list:
        for word, other in ((first, second), (second, first)):
            for voice in voice_list:
                pool.append((word, other, voice, heard.get((word, voice), "")))

    # Shuffle first, then stable-sort: unseen combinations lead, and within the seen ones the
    # least recently heard leads. The shuffle is what breaks ties without a bias toward
    # whichever pair happens to be written first in the reference table.
    rng.shuffle(pool)
    pool.sort(key=lambda entry: (entry[3] != "", entry[3]))

    ordered = _interleave(pool, voice_list, min(wanted, len(pool)), rng)

    planned: list[Trial] = []
    for word, other, voice, last in ordered:
        options = [word, other]
        # Shuffled per trial, so the button position never carries the answer. Without this a
        # block teaches "the target is on the left", which scores well and trains nothing.
        rng.shuffle(options)
        planned.append(Trial(
            word=word, other=other, alternatives=tuple(options), voice=voice,
            novel=last == "",
        ))

    return Block(
        item=item, kind=kind_for(expected), expected=expected, produced=produced,
        voices=tuple(voice_list), trials=tuple(planned), review=review,
    )


def _interleave(
    pool: Sequence[tuple[str, str, str, str]],
    voices: Sequence[str],
    wanted: int,
    rng: random.Random,
) -> list[tuple[str, str, str, str]]:
    """Deal the pool out one voice at a time, in rotation, taking the best of each.

    Selecting the twenty best stimuli and *then* trying to order them does not work: the
    selection can hand back eight of one voice, and no ordering of eight-in-twenty avoids a
    consecutive repeat. Dealing round-robin instead makes the rotation a property of the
    selection, so every voice gets within one trial of an equal share and no two neighbouring
    trials share a talker — which is the point. Variability that arrives in clumps is not
    variability: five trials of one voice followed by five of the next is closer to four
    single-talker blocks than to a mixed one.

    Within a voice the order is already novelty-first, so the round-robin never trades away
    the preference for stimuli that have not been heard.
    """
    queues: dict[str, list[tuple[str, str, str, str]]] = {voice: [] for voice in voices}
    for entry in pool:
        queues[entry[2]].append(entry)

    rotation = list(voices)
    rng.shuffle(rotation)
    ordered: list[tuple[str, str, str, str]] = []
    last_pair: frozenset[str] | None = None

    while len(ordered) < wanted:
        progressed = False
        for voice in rotation:
            if len(ordered) >= wanted:
                break
            queue = queues[voice]
            if not queue:
                continue
            # Take the best entry that does not repeat the previous trial's pair — but only
            # from among the entries that are equally good on novelty. Searching the whole
            # queue would let a cosmetic preference about presentation order quietly hand
            # back a stimulus already heard while an unheard one waited, and novelty is the
            # thing graduation is claimed on. With a small pool the constraint sometimes
            # cannot be met at all, and failing a block over presentation order would be the
            # wrong trade, so it falls back to the head.
            tier = queue[0][3]
            index = next(
                (i for i, entry in enumerate(queue)
                 if entry[3] == tier and frozenset({entry[0], entry[1]}) != last_pair),
                0,
            )
            entry = queue.pop(index)
            ordered.append(entry)
            last_pair = frozenset({entry[0], entry[1]})
            progressed = True
        if not progressed:  # every queue is empty
            break

    return ordered


def stimuli(block: Block) -> list[tuple[str, str]]:
    """Every `(text, voice)` the block needs synthesised, in a stable order.

    Both words of the pair in the trial's voice, not just the one that gets played: "replay
    both" is offered on every reveal, and discovering mid-block that the other half was never
    synthesised means a stall and a second charge.
    """
    seen: dict[tuple[str, str], None] = {}
    for trial in block.trials:
        seen.setdefault((trial.word, trial.voice), None)
        seen.setdefault((trial.other, trial.voice), None)
    return list(seen)


# --- Scoring ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BlockResult:
    """What a block scored. Every accuracy here travels with its own chance floor."""

    correct: int
    total: int
    alternatives: int
    novel: int
    planned: int

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    @property
    def chance(self) -> float:
        return chance_floor(self.alternatives)

    @property
    def novel_fraction(self) -> float:
        return self.novel / self.total if self.total else 0.0

    @property
    def complete(self) -> bool:
        """Whether every planned trial was answered.

        An abandoned block keeps its evidence — the answered trials are stored as they are
        given — but it does not earn a verdict. Graduation is a claim about a full block.
        """
        return self.total >= self.planned and self.planned > 0

    @property
    def above_chance(self) -> bool:
        return self.accuracy > self.chance


def score(answers: Sequence[bool], *, alternatives: int, novel: int, planned: int
          ) -> BlockResult:
    """Turn a list of right/wrong into a result. Pure arithmetic, no interpretation."""
    return BlockResult(
        correct=sum(1 for answer in answers if answer),
        total=len(answers),
        alternatives=alternatives,
        novel=novel,
        planned=planned,
    )
