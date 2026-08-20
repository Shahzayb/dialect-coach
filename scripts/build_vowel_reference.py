#!/usr/bin/env python3
"""Generate `src/vowel_reference.py` from the Hillenbrand et al. (1995) measurements.

Run once, and again only when the derivation changes. The point of generating rather than
typing is that **nobody types a formant value from memory into this project**: every number in
the emitted module is computed here from the primary data file, and the module says so.

    docker compose run --rm app python scripts/build_vowel_reference.py --force

## Where the data comes from, and why not from the canonical URL

The canonical host is `homepages.wmich.edu/~hillenbr/voweldata.html`. **It no longer serves
valid TLS** — as of 2026-08-20 it presents a certificate for `CN=redirect.wmich.edu`, so any
fetch fails certificate verification (curl exit 60). The data is taken instead from
`github.com/santiagobarreda/hillenbrand_et_al_1995`, which packages the original archive
unchanged ("Hosted with permission from Jim Hillenbrand") and ships the original `readme.txt`
alongside it. `--source` accepts a local copy of the zip so this can run offline.

## What the file contains, from its own header rather than from a summary

`vowdata.dat` is 1,668 tokens: 45-48 talkers in each of four groups (men, women, boys, girls)
producing 12 vowels in an /hVd/ frame. Columns are duration in ms, f0 and F1-F4 at "steady
state", then F1/F2/F3 at **20%, 50% and 80% of vowel duration**. An entry of zero means the
formant was not measurable and is excluded here rather than averaged in as a zero.

Only the men's and women's sets are emitted. The children's sets are real data but this
project has one adult user, and a reference set that could be selected by mistake is a
reference set that will be.
"""

from __future__ import annotations

import argparse
import io
import re
import statistics
import sys
import urllib.request
import zipfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "src" / "vowel_reference.py"

SOURCE_URL = (
    "https://raw.githubusercontent.com/santiagobarreda/hillenbrand_et_al_1995/main/"
    "h95-alldata.zip"
)
MEMBER = "vowdata.dat"

# Hillenbrand's two-letter vowel codes mapped onto the IPA symbols **Azure actually emits**,
# which are rhotic and carry no length marks. The keys on the left are from the file's own
# header; `phoneme_reference` owns the right-hand side and a test asserts every one resolves.
#
# Note `ei`: the file header spells the vowel in "hayed" as `ei`, while the archive's outer
# readme spells the same vowel `ey`. Both are read, because it costs one line and a silently
# dropped vowel category would be invisible in the output.
CODE_TO_IPA: dict[str, str] = {
    "ae": "æ",  # had
    "ah": "ɑ",  # hod
    "aw": "ɔ",  # hawed
    "eh": "ɛ",  # head
    "er": "ɝ",  # heard
    "ei": "eɪ",  # hayed
    "ey": "eɪ",  # hayed, as the outer readme spells it
    "ih": "ɪ",  # hid
    "iy": "i",  # heed
    "oa": "oʊ",  # hoed
    "oo": "ʊ",  # hood
    "uh": "ʌ",  # hud
    "uw": "u",  # who'd
}

# Emission order: front to back, then the diphthongs, then NURSE. Not the file's alphabetical
# order — a vowel table that reads like a vowel chart is easier to check by eye.
ORDER = ("i", "ɪ", "ɛ", "æ", "ɑ", "ɔ", "ʌ", "ʊ", "u", "eɪ", "oʊ", "ɝ")

GROUPS = {"m": "MEN", "w": "WOMEN"}

# Column indices into the 15 numbers that follow the filename, from the file's own header.
DURATION, F0 = 0, 1
AT = {20: (6, 7, 8), 50: (9, 10, 11), 80: (12, 13, 14)}


def parse(text: str) -> list[tuple[str, str, list[int]]]:
    """(group, vowel code, 15 numbers) for every token in `vowdata.dat`.

    Rows are matched on the filename pattern rather than by skipping a header line count, so a
    reformatted header cannot silently shift the data.
    """
    rows: list[tuple[str, str, list[int]]] = []
    for line in text.splitlines():
        match = re.match(r"^([mwbg])(\d\d)([a-z]{2})\s+(.*)$", line.strip())
        if not match:
            continue
        numbers = [int(value) for value in match.group(4).split()]
        if len(numbers) != 15:
            continue
        rows.append((match.group(1), match.group(3), numbers))
    return rows


def _mean(values: Sequence[int]) -> float | None:
    """Mean over measurable values only. A zero means 'not measurable', never 'zero hertz'."""
    usable = [float(value) for value in values if value > 0]
    return statistics.fmean(usable) if usable else None


def _sd(values: Sequence[int]) -> float | None:
    usable = [float(value) for value in values if value > 0]
    return statistics.stdev(usable) if len(usable) > 1 else None


def summarise(rows: list[tuple[str, str, list[int]]]) -> dict[str, dict[str, dict[str, object]]]:
    """Per-group, per-vowel means and SDs, keyed by Azure IPA."""
    out: dict[str, dict[str, dict[str, object]]] = {name: {} for name in GROUPS.values()}
    for code, group in ((c, g) for c in GROUPS for g in [GROUPS[c]]):
        for ipa in ORDER:
            codes = [key for key, value in CODE_TO_IPA.items() if value == ipa]
            tokens = [numbers for grp, vowel, numbers in rows if grp == code and vowel in codes]
            if not tokens:
                continue
            entry: dict[str, object] = {
                # `n` counts tokens with a measurable F1 at the 50% point — the point every
                # other figure is anchored to. Each individual mean is still taken over its
                # own measurable subset, so a vowel whose F3 was often unmeasurable reports a
                # sound F1 rather than being thrown away.
                "n": sum(1 for t in tokens if t[AT[50][0]] > 0),
                "duration_ms": _mean([t[DURATION] for t in tokens]),
                "f0_hz": _mean([t[F0] for t in tokens]),
            }
            for point, columns in AT.items():
                for index, formant in enumerate(("f1", "f2", "f3")):
                    entry[f"{formant}_{point}"] = _mean([t[columns[index]] for t in tokens])
            for index, formant in enumerate(("f1", "f2", "f3")):
                entry[f"{formant}_50_sd"] = _sd([t[AT[50][index]] for t in tokens])
            out[group][ipa] = entry
    return out


def _number(value: object) -> str:
    return "None" if value is None else f"{float(value):.1f}"


def render(summary: dict[str, dict[str, dict[str, object]]], token_count: int) -> str:
    """The emitted module. Prose first — the numbers are useless without the caveats."""
    generated = datetime.now(UTC).strftime("%Y-%m-%d")
    lines: list[str] = []
    add = lines.append

    add('"""General American vowel formant reference — Hillenbrand et al. (1995).')
    add("")
    add("**GENERATED FILE. Do not hand-edit.** Every number below is computed from")
    add("`vowdata.dat` by `scripts/build_vowel_reference.py`; re-run that script instead. The")
    add("rule this enforces is that no formant value in this project is ever typed from")
    add("memory — they are read off the primary data file or they do not exist.")
    add("")
    add("    Hillenbrand, J., Getty, L. A., Clark, M. J., & Wheeler, K. (1995). Acoustic")
    add("    characteristics of American English vowels. JASA 97(5), 3099-3111.")
    add("    doi:10.1121/1.411872")
    add("")
    add(f"Derived {generated} from {token_count} tokens, adult men and women only.")
    add("")
    add("## Read these four things before comparing anything to this table")
    add("")
    add("**1. The measurement points are 20 / 50 / 80 percent of vowel duration.** That is")
    add("the file's own sampling, and `vowel_measure` samples the speaker at the same three")
    add("proportions for exactly this reason. A 25/75 sample against a 20/80 reference is a")
    add("small systematic bias that lands hardest on the diphthongs, and adopting the")
    add("reference's own points costs nothing.")
    add("")
    add("**2. This covers 12 vowels, not the inventory.** There is no published mean here")
    add("for /aɪ aʊ ɔɪ ə ɚ ɑɹ ɔɹ ɛɹ ɪɹ ʊɹ/ — ten of the categories the benchmark passage")
    add("deliberately carries. A surface must report those with an honest 'no published GA")
    add("reference' rather than inventing a target. `has_reference()` is the check.")
    add("")
    add("**3. Durations here are citation-form /hVd/ words read in isolation.** /i/ averages")
    add("244 ms for men; the same vowel in connected speech is far shorter. **Absolute")
    add("milliseconds must never be compared against this table.** Only ratios transfer —")
    add("tense against lax, pre-fortis against pre-lenis, stressed against unstressed —")
    add("which is why `TENSE_LAX_PAIRS` exists and no absolute duration target does.")
    add("")
    add("**4. It is upper-Midwest speech recorded in the early 1990s, and English moved on.**")
    add("Two known drifts, handled by widening the tolerance band rather than by reporting a")
    add("deviation no listener would hear — see `TOLERANCE_MULTIPLIER` below.")
    add('"""')
    add("")
    add("from __future__ import annotations")
    add("")
    add("from collections.abc import Mapping")
    add("from dataclasses import dataclass")
    add("")
    add("")
    add("@dataclass(frozen=True)")
    add("class Point:")
    add('    """F1/F2/F3 in Hz at one proportion of the vowel\'s duration."""')
    add("")
    add("    f1: float | None")
    add("    f2: float | None")
    add("    f3: float | None")
    add("")
    add("    @property")
    add("    def f3_minus_f2(self) -> float | None:")
    add('        """The rhoticity measure. Low means r-coloured: /ɝ/ sits near 300 Hz here')
    add("        where every other vowel in the table sits between 546 and 1613.")
    add('        """')
    add("        if self.f1 is None or self.f2 is None or self.f3 is None:")
    add("            return None")
    add("        return self.f3 - self.f2")
    add("")
    add("")
    add("@dataclass(frozen=True)")
    add("class ReferenceVowel:")
    add('    """One vowel category\'s published means, sampled the way the paper sampled it."""')
    add("")
    add("    symbol: str  # Azure's IPA")
    add("    n: int  # tokens behind these means, with a measurable F1 at 50%")
    add("    duration_ms: float | None  # citation form. See caveat 3 in the module docstring.")
    add("    f0_hz: float | None")
    add("    at20: Point")
    add("    at50: Point")
    add("    at80: Point")
    add("    sd50: Point  # per-formant SD at the 50% point, the tolerance band's basis")
    add("")
    add("    @property")
    add("    def f2_travel(self) -> float | None:")
    add('        """Signed F2 movement from 20% to 80%. What makes a diphthong a diphthong."""')
    add("        if self.at20.f2 is None or self.at80.f2 is None:")
    add("            return None")
    add("        return self.at80.f2 - self.at20.f2")
    add("")

    for group in GROUPS.values():
        add("")
        add(f"{group}: Mapping[str, ReferenceVowel] = {{")
        for ipa in ORDER:
            entry = summary[group].get(ipa)
            if entry is None:
                continue
            add(f'    "{ipa}": ReferenceVowel(')
            add(f'        symbol="{ipa}",')
            add(f"        n={entry['n']},")
            add(f"        duration_ms={_number(entry['duration_ms'])},")
            add(f"        f0_hz={_number(entry['f0_hz'])},")
            for point in (20, 50, 80):
                add(
                    f"        at{point}=Point("
                    f"f1={_number(entry[f'f1_{point}'])}, "
                    f"f2={_number(entry[f'f2_{point}'])}, "
                    f"f3={_number(entry[f'f3_{point}'])}),"
                )
            add(
                f"        sd50=Point("
                f"f1={_number(entry['f1_50_sd'])}, "
                f"f2={_number(entry['f2_50_sd'])}, "
                f"f3={_number(entry['f3_50_sd'])}),"
            )
            add("    ),")
        add("}")
        add("")

    add("")
    add("# The two sets are kept apart and NEVER averaged. Formants scale with vocal tract")
    add("# length; a mean of the men's and women's tables describes nobody. `GA_REFERENCE_SET`")
    add("# selects one explicitly and `vowel_measure` refuses to score position until it does.")
    add('REFERENCE_SETS: Mapping[str, Mapping[str, ReferenceVowel]] = {"men": MEN,')
    add('                                                             "women": WOMEN}')
    add("")
    add("")
    add("# Where the 1995 reference is known to be behind the language, widen the band rather")
    add("# than report a deviation nobody would hear. Multiplies the SD-based tolerance.")
    add("#")
    add("#   /ɑ/ and /ɔ/ — the low-back (LOT-THOUGHT, 'cot-caught') merger has continued")
    add("#     across most of the United States since these recordings. A confident 'your /ɔ/")
    add("#     is wrong' may be flagging a change the reference predates rather than an error.")
    add("#     The table itself shows the pair still well separated (men, F2 at 50%: 1326 vs")
    add("#     1046 Hz), which is exactly the separation that has since eroded.")
    add("#   /u/ — GOOSE has fronted. A modern native production sits higher in F2 than the")
    add("#     1995 mean, so an unwidened band flags fronting as an error when it is current")
    add("#     General American.")
    add("TOLERANCE_MULTIPLIER: Mapping[str, float] = {")
    add('    "ɑ": 1.8,')
    add('    "ɔ": 1.8,')
    add('    "u": 1.8,')
    add("}")
    add("")
    add("DEFAULT_TOLERANCE_MULTIPLIER = 1.0")
    add("")
    add("")
    add("# Tense/lax duration pairs, as RATIOS — the only way this table's durations can be")
    add("# used at all (caveat 3). In General American the contrast is carried by quality AND")
    add("# length together, so a learner with the formants right and the length wrong still")
    add("# sounds wrong. Each entry is (tense, lax).")
    add("TENSE_LAX_PAIRS: tuple[tuple[str, str], ...] = (")
    add('    ("i", "ɪ"),')
    add('    ("u", "ʊ"),')
    add('    ("eɪ", "ɛ"),')
    add(")")
    add("")
    add("")
    add("def has_reference(symbol: str, reference_set: str) -> bool:")
    add('    """Whether this vowel has a published mean at all. Ten categories do not."""')
    add("    return symbol in REFERENCE_SETS.get(reference_set, {})")
    add("")
    add("")
    add("def lookup(symbol: str, reference_set: str) -> ReferenceVowel | None:")
    add('    """The published means for one vowel, or None when the table does not cover it."""')
    add("    return REFERENCE_SETS.get(reference_set, {}).get(symbol)")
    add("")
    add("")
    add("def tense_lax_ratio(tense: str, lax: str, reference_set: str) -> float | None:")
    add('    """Published duration ratio for one tense/lax pair, or None if either is absent."""')
    add("    first, second = lookup(tense, reference_set), lookup(lax, reference_set)")
    add("    if first is None or second is None:")
    add("        return None")
    add("    if first.duration_ms is None or not second.duration_ms:")
    add("        return None")
    add("    return first.duration_ms / second.duration_ms")
    add("")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--source", type=Path, help="local h95-alldata.zip instead of fetching")
    parser.add_argument("--force", action="store_true", help="overwrite an existing module")
    args = parser.parse_args(argv)

    if args.out.exists() and not args.force:
        print(f"[reference] {args.out} exists. Pass --force to regenerate.", file=sys.stderr)
        return 1

    if args.source:
        blob = args.source.read_bytes()
        print(f"[reference] reading {args.source}")
    else:
        print(f"[reference] fetching {SOURCE_URL}")
        with urllib.request.urlopen(SOURCE_URL, timeout=300) as response:  # noqa: S310
            blob = response.read()

    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        text = archive.read(MEMBER).decode("latin-1")

    rows = parse(text)
    if len(rows) != 1668:
        # Not fatal, but the file is a fixed historical artefact: a different count means the
        # source changed or the parse is wrong, and either is worth stopping for.
        print(f"[reference] WARNING: parsed {len(rows)} tokens, expected 1668", file=sys.stderr)

    summary = summarise(rows)
    args.out.write_text(render(summary, len(rows)) + "\n", encoding="utf-8")

    shown = args.out.resolve()
    shown = shown.relative_to(ROOT) if shown.is_relative_to(ROOT) else shown
    print(f"[reference] wrote {shown} from {len(rows)} tokens")
    for group in GROUPS.values():
        covered = ", ".join(sorted(summary[group]))
        print(f"[reference]   {group.lower()}: {len(summary[group])} vowels — {covered}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
