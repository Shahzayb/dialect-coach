#!/usr/bin/env python3
"""Measure the captured renderings and generate `src/model_reference.py`.

Costs nothing. `scripts/capture_model_reference.py` bought the audio; this is local signal
processing over files already on disk, and it can be re-run as often as the measurement code
changes. That is the whole reason the renderings are kept: **re-deriving a reference must
never mean re-spending**, exactly as re-deriving a measurement must never mean re-recording.

    docker compose run --rm app python scripts/build_model_reference.py

## Three decisions worth knowing before reading a number out of the output

**1. The ceiling is swept per voice.** The LPC ceiling has to match vocal tract length, and
sixteen voices are sixteen different tracts. Holding one ceiling across all of them would
measure the shorter tracts through a filter built for the longer ones.

**2. Per-voice category means first, then the mean ACROSS voices.** Never a pooled token
average. The benchmark passage yields far more /ə/ than /ɔɪ/, and it yields different counts
per voice — a token-weighted mean would let whichever voice happened to produce the most
tokens drag the category. Same error `vowel_measure.Normaliser` documents for Lobanov, one
level up: there it is vowels within a speaker, here it is speakers within a reference.

**3. The SD is a BETWEEN-TALKER spread.** Hillenbrand's `sd50` is the variation within one
corpus's tokens; this one is how far sixteen General American talkers sit from each other.
They answer different questions and a surface must say which it is holding, which is what
`ReferenceVowel.voices` is for — zero for Hillenbrand, non-zero here.

A category is published only when enough voices produced it cleanly. One that does not clear
the bar is emitted as an **explicit absence with its voice count**, never as a mean computed
from two talkers — a thin reference has to look thin, the same rule the four-column table
follows for a thin measurement.
"""

from __future__ import annotations

import argparse
import logging
import statistics
import sys
import textwrap
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import acoustics  # noqa: E402
import db  # noqa: E402
import native_model  # noqa: E402
import phoneme_reference  # noqa: E402
import progress_view  # noqa: E402
import utils  # noqa: E402
import vowel_measure  # noqa: E402
from native_model import MEN, WOMEN  # noqa: E402

OUT = ROOT / "src" / "model_reference.py"

# The full en-US inventory the benchmark passage was written to carry — all 22, against the
# twelve Hillenbrand covers. Closing that gap is the point of the whole exercise.
INVENTORY: tuple[str, ...] = tuple(sorted(phoneme_reference.LEXICAL_SET))


# **Why this table publishes no diphthong trajectory**, established by measuring rather than
# by reasoning about it. The first build had FACE gliding -225 Hz where General American glides
# about +140, so the per-token numbers were dumped and they said why:
#
#   - Across all eight men's voices only TWELVE /eI/ tokens clear `MIN_TRAJECTORY_MS`, and they
#     come from three word types: "pace" (median -136 Hz), "way" (-446 Hz) and "same" (-813 Hz).
#     Two of the twelve are positive.
#   - Every one of those three has a contaminating right context. "same" ends in a nasal, and
#     its 80% sample reads F1 240 Hz / F2 1285 Hz — a nasal murmur, not a vowel. "way" is
#     followed by the word "I", so the sample runs into the next vowel.
#   - It is not an amplitude artefact and cannot be gated on one: the 80% window of "same"
#     measures -17.4 dB against -16.4 dB at the midpoint. The velum is open at full voicing.
#
# So the number exists and does not mean what the label "F2 travel 20->80%" says: it describes
# what FOLLOWS each diphthong in this passage. Published as a target it would produce a
# confident "widen the glide" instruction derived from a following /m/, which is exactly the
# failure this project exists to eliminate.
#
# The 50% window sits in the middle of the vowel and is unaffected, so position, rhoticity,
# duration and reduction are all published normally. Only the edges are withheld, and only for
# THIS reference — the speaker's own trajectory is still measured and still charted.
#
# Nothing is lost permanently. The renderings are stored, so a passage written with clean
# diphthong contexts, or a better source of boundaries, is a re-derivation and not a re-spend.


class VowelMeans:
    """One voice's mean for one vowel category, at all three sampling points."""

    def __init__(self, symbol: str, tokens: Sequence[vowel_measure.Token]) -> None:
        self.symbol = symbol
        self.n = len(tokens)
        # **The 50% point uses every token; the edges use only the long ones.** A 60 ms vowel
        # gives a perfectly good midpoint — the 25 ms window fits — and a useless 80% point,
        # because that window reaches 12.5 ms past the sample and lands in the next consonant.
        # Averaging the edges over every token is how the first build of this table came out
        # with FACE gliding backwards. See `vowel_measure.MIN_TRAJECTORY_MS`.
        gliding = [token for token in tokens if token.trajectory_usable]
        self.n_trajectory = len(gliding)
        self.at20 = _mean_point([token.at20 for token in gliding])
        self.at50 = _mean_point([token.at50 for token in tokens])
        self.at80 = _mean_point([token.at80 for token in gliding])
        self.duration_ms = _mean([token.duration_ms for token in tokens])
        self.f0_hz = _mean([token.f0_hz for token in tokens])


class VoiceMeans:
    """One voice's whole reading, reduced to per-category means. The averaging unit."""

    def __init__(self, voice: str) -> None:
        self.voice = voice
        self.vowels: dict[str, VowelMeans] = {}
        self.tokens = 0
        self.ceiling_hz = 0.0


def _mean_point(points: Sequence[acoustics.FormantPoint]) -> tuple[float | None, ...]:
    return tuple(_mean([getattr(point, part) for point in points]) for part in ("f1", "f2", "f3"))


def measure(rendering: native_model.Rendering) -> VoiceMeans | None:
    """Slice one rendering into vowel tokens and reduce it to per-category means.

    The ceiling is **swept per voice**, not held: it has to match vocal tract length, and
    sixteen voices are sixteen different tracts. Held at one value, the shorter tracts would
    be measured through a filter built for the longer ones.
    """
    audio = rendering.audio()
    if audio is None:
        return None
    measurement = vowel_measure.extract(
        rendering.words(), audio, ceiling_hz=None, snr_db_min=None, style="read"
    )
    grouped: dict[str, list[vowel_measure.Token]] = {}
    for token in measurement.accepted:
        if token.at50.usable:
            grouped.setdefault(token.vowel, []).append(token)

    found = VoiceMeans(rendering.voice)
    found.ceiling_hz = measurement.ceiling_hz
    found.tokens = len(measurement.accepted)
    found.vowels = {
        symbol: VowelMeans(symbol, tokens)
        for symbol, tokens in grouped.items()
        if len(tokens) >= vowel_measure.MIN_TOKENS_PER_CATEGORY
    }
    return found


def _mean(values: Sequence[float | None]) -> float | None:
    real = [value for value in values if value is not None]
    return statistics.fmean(real) if real else None


def _sd(values: Sequence[float | None]) -> float | None:
    real = [value for value in values if value is not None]
    return statistics.stdev(real) if len(real) > 1 else None


def _num(value: float | None) -> str:
    return "None" if value is None else f"{value:.1f}"


def _point(rows: Sequence[tuple[float | None, ...]]) -> str:
    """One sampling point, averaged ACROSS voices — never across pooled tokens."""
    f1, f2, f3 = (_mean([row[index] for row in rows]) for index in range(3))
    return f"Point(f1={_num(f1)}, f2={_num(f2)}, f3={_num(f3)})"


def build_set(voices: Sequence[VoiceMeans]) -> tuple[dict[str, str], dict[str, int]]:
    """Emit one reference set's entries, plus the voice count behind every inventory member."""
    entries: dict[str, str] = {}
    counts: dict[str, int] = {}

    for symbol in INVENTORY:
        having = [found.vowels[symbol] for found in voices if symbol in found.vowels]
        counts[symbol] = len(having)
        if len(having) < native_model.MIN_VOICES_PER_SET:
            continue
        entries[symbol] = (
            f'    "{symbol}": ReferenceVowel(\n'
            f'        symbol="{symbol}",\n'
            f"        n={sum(one.n for one in having)},\n"
            f"        voices={len(having)},\n"
            f"        duration_ms={_num(_mean([one.duration_ms for one in having]))},\n"
            f"        f0_hz={_num(_mean([one.f0_hz for one in having]))},\n"
            # at20/at80 are deliberately NOT emitted — see the note above `VowelMeans`. The
            # 50% point is the only one this passage supports, and it is the only one written.
            f"        at20=Point(f1=None, f2=None, f3=None),\n"
            f"        at50={_point([one.at50 for one in having])},\n"
            f"        at80=Point(f1=None, f2=None, f3=None),\n"
            # The SD is the spread BETWEEN talkers at the 50% point, not within a token pool.
            f"        sd50=Point(\n"
            f"            f1={_num(_sd([one.at50[0] for one in having]))},\n"
            f"            f2={_num(_sd([one.at50[1] for one in having]))},\n"
            f"            f3={_num(_sd([one.at50[2] for one in having]))},\n"
            f"        ),\n"
            f"    ),"
        )
    return entries, counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    utils.configure_logging(logging.WARNING)

    out = Path(args.out) if args.out else OUT
    conn = db.connect()
    text = progress_view.BENCHMARK_PASSAGE
    stored = {r.voice: r for r in native_model.renderings_for(conn, text)}
    if not stored:
        print("[build] no captured renderings. Run scripts/capture_model_reference.py first.")
        return 1

    print(f"[build] measuring {len(stored)} rendering(s) …")
    measured: dict[str, VoiceMeans] = {}
    for voice, rendering in sorted(stored.items()):
        found = measure(rendering)
        if found is None:
            print(f"[build]   {voice}: audio missing, skipped")
            continue
        measured[voice] = found
        print(
            f"[build]   {voice}: {found.tokens} tokens, {len(found.vowels)} categories, "
            f"ceiling {found.ceiling_hz:.0f} Hz"
        )

    # Built from what was actually CAPTURED, not from what would be selected today: a
    # roster change must not silently drop a voice out of a reference already in use.
    sets: dict[str, list[VoiceMeans]] = {MEN: [], WOMEN: []}
    roster = native_model.stored_sets(conn, text)
    for reference_set, names in roster.items():
        sets[reference_set] = [measured[n] for n in names if n in measured]

    blocks: dict[str, dict[str, str]] = {}
    coverage: dict[str, dict[str, int]] = {}
    for reference_set, voices in sets.items():
        if len(voices) < native_model.MIN_VOICES_PER_SET:
            print(
                f"[build] only {len(voices)} usable {reference_set}'s voice(s); a set needs "
                f"{native_model.MIN_VOICES_PER_SET}. Refusing to emit it."
            )
            return 1
        blocks[reference_set], coverage[reference_set] = build_set(voices)
        print(
            f"[build] {reference_set}: {len(voices)} voices, "
            f"{len(blocks[reference_set])}/{len(INVENTORY)} categories published"
        )

    out.write_text(render_module(blocks, coverage, sets), encoding="utf-8")
    print(f"[build] wrote {out.relative_to(ROOT)}")
    for reference_set, counts in coverage.items():
        thin = [s for s, c in counts.items() if c < native_model.MIN_VOICES_PER_SET]
        if thin:
            print(f"[build] {reference_set}: no published mean for {' '.join(thin)}")
    return 0


def render_module(
    blocks: dict[str, dict[str, str]],
    coverage: dict[str, dict[str, int]],
    sets: dict[str, list[VoiceMeans]],
) -> str:
    """The generated module's text. Reuses `vowel_reference`'s dataclasses, never its data."""
    when = datetime.now(UTC).strftime("%Y-%m-%d")
    lines = [
        '"""General American vowel reference, measured by this project through its own pipeline.',
        "",
        "**GENERATED FILE. Do not hand-edit.** Every number below is produced by",
        "`scripts/build_model_reference.py` from renderings captured by",
        "`scripts/capture_model_reference.py`; re-run those instead. Same rule as",
        "`vowel_reference.py`: no formant value in this project is ever typed from memory.",
        "",
        f"Derived {when} from the benchmark passage v{progress_view.BENCHMARK_VERSION}, read by",
    ]
    for reference_set in (MEN, WOMEN):
        names = ", ".join(found.voice for found in sets[reference_set])
        # Wrapped, because a generated file still has to pass the same line-length lint as a
        # hand-written one — a module nobody can run `ruff check` over is a module nobody
        # notices has gone stale.
        lines.append(f"{reference_set}:")
        lines.extend(textwrap.wrap(names + ".", width=94))
    lines += [
        "",
        "## What this is, and what it is not",
        "",
        "It is **not** a corpus of native speakers. It is a set of en-US neural voices reading",
        "one passage, measured by the same segmenter and the same Burg analysis that measures",
        "the user. What earns it its place is not that a synthesiser is a person — it is that",
        "the comparison holds everything but the talker still, and that across sixteen voices",
        "it is a distribution rather than one voice's idiosyncrasy.",
        "",
        "It complements `vowel_reference.py` and never replaces or averages with it. Hillenbrand",
        "1995 is real humans, peer-reviewed, and covers 12 vowels of citation-form /hVd/ speech.",
        "This covers the whole inventory the passage carries, in connected speech, at today's",
        "vowel qualities — including the ten categories that have no published mean at all, of",
        "which six are r-coloured, on the single most correctable marker for a GA target.",
        "",
        "**Durations here CAN be compared in milliseconds**, unlike Hillenbrand's, because these",
        "are connected speech through the identical pipeline. That is the one caveat this table",
        "lifts and the reason it was worth real allowance.",
        "",
        "**There are no at20/at80 points, deliberately.** Azure's phoneme boundaries in",
        "connected speech are not accurate enough to measure a glide against: across the eight",
        "men's voices only twelve FACE tokens are long enough to sample at all, from three word",
        "types, and each one's 80% window lands in the following nasal or in the next word's",
        "vowel. The number that falls out describes what FOLLOWS each diphthong in this passage,",
        "not the diphthong itself. The 50% point sits in the middle of the vowel and is",
        "unaffected — it is what every entry below carries.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from collections.abc import Mapping",
        "",
        "from vowel_reference import Point, ReferenceVowel",
        "",
    ]
    for reference_set, constant in ((MEN, "MEN"), (WOMEN, "WOMEN")):
        lines.append(f"{constant}: Mapping[str, ReferenceVowel] = {{")
        for symbol in INVENTORY:
            if symbol in blocks[reference_set]:
                lines.append(blocks[reference_set][symbol])
        lines += ["}", ""]

    lines += [
        "",
        "REFERENCE_SETS: Mapping[str, Mapping[str, ReferenceVowel]] = {",
        '    "men": MEN,',
        '    "women": WOMEN,',
        "}",
        "",
        "",
        "# How many voices produced each category cleanly, INCLUDING the ones below the",
        "# publication floor. A thin category has to look thin rather than be silently absent:",
        "# a surface asked for /ʊɹ/ can then say 'two voices produced it, and a reference needs",
        "# four' instead of the same blank a typo would produce.",
        "VOICE_COVERAGE: Mapping[str, Mapping[str, int]] = {",
    ]
    for reference_set in (MEN, WOMEN):
        lines.append(f'    "{reference_set}": {{')
        for symbol in INVENTORY:
            lines.append(f'        "{symbol}": {coverage[reference_set][symbol]},')
        lines.append("    },")
    lines += [
        "}",
        "",
        "",
        "def has_reference(symbol: str, reference_set: str) -> bool:",
        '    """Whether this table carries a mean for this vowel. Mirrors `vowel_reference`."""',
        "    return symbol in REFERENCE_SETS.get(reference_set, {})",
        "",
        "",
        "def lookup(symbol: str, reference_set: str) -> ReferenceVowel | None:",
        '    """The measured means for one vowel, or None when too few voices produced it."""',
        "    return REFERENCE_SETS.get(reference_set, {}).get(symbol)",
        "",
        "",
        "def voices_behind(symbol: str, reference_set: str) -> int:",
        '    """How many voices produced this category, published or not."""',
        "    return VOICE_COVERAGE.get(reference_set, {}).get(symbol, 0)",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
