"""General American vowel formant reference — Hillenbrand et al. (1995).

**GENERATED FILE. Do not hand-edit.** Every number below is computed from
`vowdata.dat` by `scripts/build_vowel_reference.py`; re-run that script instead. The
rule this enforces is that no formant value in this project is ever typed from
memory — they are read off the primary data file or they do not exist.

    Hillenbrand, J., Getty, L. A., Clark, M. J., & Wheeler, K. (1995). Acoustic
    characteristics of American English vowels. JASA 97(5), 3099-3111.
    doi:10.1121/1.411872

Derived 2026-08-20 from 1668 tokens, adult men and women only.

## Read these four things before comparing anything to this table

**1. The measurement points are 20 / 50 / 80 percent of vowel duration.** That is
the file's own sampling, and `vowel_measure` samples the speaker at the same three
proportions for exactly this reason. A 25/75 sample against a 20/80 reference is a
small systematic bias that lands hardest on the diphthongs, and adopting the
reference's own points costs nothing.

**2. This covers 12 vowels, not the inventory.** There is no published mean here
for /aɪ aʊ ɔɪ ə ɚ ɑɹ ɔɹ ɛɹ ɪɹ ʊɹ/ — ten of the categories the benchmark passage
deliberately carries. A surface must report those with an honest 'no published GA
reference' rather than inventing a target. `has_reference()` is the check.

**3. Durations here are citation-form /hVd/ words read in isolation.** /i/ averages
244 ms for men; the same vowel in connected speech is far shorter. **Absolute
milliseconds must never be compared against this table.** Only ratios transfer —
tense against lax, pre-fortis against pre-lenis, stressed against unstressed —
which is why `TENSE_LAX_PAIRS` exists and no absolute duration target does.

**4. It is upper-Midwest speech recorded in the early 1990s, and English moved on.**
Two known drifts, handled by widening the tolerance band rather than by reporting a
deviation no listener would hear — see `TOLERANCE_MULTIPLIER` below.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Point:
    """F1/F2/F3 in Hz at one proportion of the vowel's duration."""

    f1: float | None
    f2: float | None
    f3: float | None

    @property
    def f3_minus_f2(self) -> float | None:
        """The rhoticity measure. Low means r-coloured: /ɝ/ sits near 300 Hz here
        where every other vowel in the table sits between 546 and 1613.
        """
        if self.f1 is None or self.f2 is None or self.f3 is None:
            return None
        return self.f3 - self.f2


@dataclass(frozen=True)
class ReferenceVowel:
    """One vowel category's published means, sampled the way the paper sampled it."""

    symbol: str  # Azure's IPA
    n: int  # tokens behind these means, with a measurable F1 at 50%
    duration_ms: float | None  # citation form. See caveat 3 in the module docstring.
    f0_hz: float | None
    at20: Point
    at50: Point
    at80: Point
    sd50: Point  # per-formant SD at the 50% point, the tolerance band's basis

    @property
    def f2_travel(self) -> float | None:
        """Signed F2 movement from 20% to 80%. What makes a diphthong a diphthong."""
        if self.at20.f2 is None or self.at80.f2 is None:
            return None
        return self.at80.f2 - self.at20.f2


MEN: Mapping[str, ReferenceVowel] = {
    "i": ReferenceVowel(
        symbol="i",
        n=45,
        duration_ms=243.5,
        f0_hz=138.6,
        at20=Point(f1=346.4, f2=2311.6, f3=3009.7),
        at50=Point(f1=340.0, f2=2338.2, f3=2993.7),
        at80=Point(f1=342.9, f2=2317.4, f3=2946.6),
        sd50=Point(f1=28.8, f2=135.4, f3=210.0),
    ),
    "ɪ": ReferenceVowel(
        symbol="ɪ",
        n=45,
        duration_ms=192.9,
        f0_hz=135.6,
        at20=Point(f1=433.8, f2=2032.3, f3=2683.5),
        at50=Point(f1=458.8, f2=1940.9, f3=2641.4),
        at80=Point(f1=466.5, f2=1841.1, f3=2661.7),
        sd50=Point(f1=33.5, f2=115.8, f3=116.6),
    ),
    "ɛ": ReferenceVowel(
        symbol="ɛ",
        n=45,
        duration_ms=195.6,
        f0_hz=126.9,
        at20=Point(f1=584.2, f2=1817.3, f3=2603.2),
        at50=Point(f1=592.4, f2=1773.8, f3=2602.1),
        at80=Point(f1=560.8, f2=1745.1, f3=2644.6),
        sd50=Point(f1=37.7, f2=111.0, f3=130.2),
    ),
    "æ": ReferenceVowel(
        symbol="æ",
        n=45,
        duration_ms=271.6,
        f0_hz=125.8,
        at20=Point(f1=591.7, f2=1923.2, f3=2597.3),
        at50=Point(f1=613.0, f2=1863.1, f3=2565.8),
        at80=Point(f1=632.1, f2=1720.4, f3=2597.9),
        sd50=Point(f1=42.6, f2=118.5, f3=148.4),
    ),
    "ɑ": ReferenceVowel(
        symbol="ɑ",
        n=45,
        duration_ms=260.9,
        f0_hz=126.6,
        at20=Point(f1=754.1, f2=1303.8, f3=2527.8),
        at50=Point(f1=756.9, f2=1326.3, f3=2523.3),
        at80=Point(f1=705.6, f2=1460.6, f3=2537.3),
        sd50=Point(f1=69.0, f2=116.4, f3=158.9),
    ),
    "ɔ": ReferenceVowel(
        symbol="ɔ",
        n=45,
        duration_ms=274.8,
        f0_hz=125.3,
        at20=Point(f1=654.4, f2=1019.9, f3=2516.2),
        at50=Point(f1=670.5, f2=1046.4, f3=2508.7),
        at80=Point(f1=658.0, f2=1265.0, f3=2491.8),
        sd50=Point(f1=46.2, f2=66.2, f3=172.0),
    ),
    "ʌ": ReferenceVowel(
        symbol="ʌ",
        n=45,
        duration_ms=194.2,
        f0_hz=129.0,
        at20=Point(f1=622.2, f2=1181.3, f3=2547.7),
        at50=Point(f1=617.9, f2=1243.1, f3=2548.5),
        at80=Point(f1=573.9, f2=1493.3, f3=2549.8),
        sd50=Point(f1=31.2, f2=88.9, f3=134.5),
    ),
    "ʊ": ReferenceVowel(
        symbol="ʊ",
        n=45,
        duration_ms=192.9,
        f0_hz=133.3,
        at20=Point(f1=467.5, f2=1121.6, f3=2439.9),
        at50=Point(f1=483.4, f2=1208.5, f3=2438.3),
        at80=Point(f1=483.0, f2=1489.9, f3=2445.2),
        sd50=Point(f1=26.9, f2=105.2, f3=120.4),
    ),
    "u": ReferenceVowel(
        symbol="u",
        n=45,
        duration_ms=236.7,
        f0_hz=143.6,
        at20=Point(f1=384.8, f2=1015.0, f3=2346.1),
        at50=Point(f1=374.7, f2=971.0, f3=2359.0),
        at80=Point(f1=368.5, f2=1027.8, f3=2337.6),
        sd50=Point(f1=31.6, f2=102.2, f3=138.6),
    ),
    "eɪ": ReferenceVowel(
        symbol="eɪ",
        n=45,
        duration_ms=267.2,
        f0_hz=129.5,
        at20=Point(f1=478.8, f2=2089.0, f3=2698.5),
        at50=Point(f1=437.2, f2=2180.6, f3=2727.0),
        at80=Point(f1=399.6, f2=2229.2, f3=2741.7),
        sd50=Point(f1=29.8, f2=119.3, f3=151.5),
    ),
    "oʊ": ReferenceVowel(
        symbol="oʊ",
        n=45,
        duration_ms=266.0,
        f0_hz=129.8,
        at20=Point(f1=511.1, f2=936.1, f3=2454.9),
        at50=Point(f1=464.7, f2=865.8, f3=2478.7),
        at80=Point(f1=434.6, f2=897.5, f3=2447.9),
        sd50=Point(f1=29.3, f2=76.5, f3=144.5),
    ),
    "ɝ": ReferenceVowel(
        symbol="ɝ",
        n=45,
        duration_ms=263.5,
        f0_hz=130.6,
        at20=Point(f1=484.7, f2=1371.3, f3=1720.4),
        at50=Point(f1=460.2, f2=1405.8, f3=1703.9),
        at80=Point(f1=442.3, f2=1484.0, f3=1752.3),
        sd50=Point(f1=29.1, f2=73.9, f3=88.4),
    ),
}


WOMEN: Mapping[str, ReferenceVowel] = {
    "i": ReferenceVowel(
        symbol="i",
        n=48,
        duration_ms=306.8,
        f0_hz=227.3,
        at20=Point(f1=446.2, f2=2755.3, f3=3381.2),
        at50=Point(f1=436.1, f2=2766.9, f3=3368.1),
        at80=Point(f1=429.2, f2=2759.8, f3=3322.9),
        sd50=Point(f1=40.3, f2=146.6, f3=249.4),
    ),
    "ɪ": ReferenceVowel(
        symbol="ɪ",
        n=48,
        duration_ms=241.4,
        f0_hz=224.7,
        at20=Point(f1=482.2, f2=2377.8, f3=3050.5),
        at50=Point(f1=520.7, f2=2267.8, f3=3019.6),
        at80=Point(f1=532.9, f2=2167.0, f3=3040.2),
        sd50=Point(f1=42.2, f2=135.5, f3=193.2),
    ),
    "ɛ": ReferenceVowel(
        symbol="ɛ",
        n=48,
        duration_ms=251.6,
        f0_hz=213.0,
        at20=Point(f1=727.6, f2=2089.1, f3=2956.2),
        at50=Point(f1=728.4, f2=2031.7, f3=2955.7),
        at80=Point(f1=671.9, f2=2038.3, f3=2994.5),
        sd50=Point(f1=61.1, f2=128.2, f3=193.1),
    ),
    "æ": ReferenceVowel(
        symbol="æ",
        n=48,
        duration_ms=334.2,
        f0_hz=214.2,
        at20=Point(f1=676.5, f2=2335.3, f3=2960.0),
        at50=Point(f1=756.1, f2=2139.9, f3=2902.7),
        at80=Point(f1=807.5, f2=1924.2, f3=2902.9),
        sd50=Point(f1=65.0, f2=155.9, f3=204.8),
    ),
    "ɑ": ReferenceVowel(
        symbol="ɑ",
        n=48,
        duration_ms=324.3,
        f0_hz=212.3,
        at20=Point(f1=929.8, f2=1521.4, f3=2832.0),
        at50=Point(f1=918.9, f2=1558.3, f3=2839.7),
        at80=Point(f1=853.0, f2=1734.9, f3=2864.8),
        sd50=Point(f1=86.3, f2=128.2, f3=180.5),
    ),
    "ɔ": ReferenceVowel(
        symbol="ɔ",
        n=48,
        duration_ms=350.5,
        f0_hz=214.0,
        at20=Point(f1=804.1, f2=1179.6, f3=2824.8),
        at50=Point(f1=817.5, f2=1260.8, f3=2835.7),
        at80=Point(f1=811.8, f2=1575.9, f3=2828.2),
        sd50=Point(f1=68.0, f2=135.1, f3=216.8),
    ),
    "ʌ": ReferenceVowel(
        symbol="ʌ",
        n=48,
        duration_ms=236.5,
        f0_hz=218.5,
        at20=Point(f1=761.9, f2=1401.6, f3=2893.1),
        at50=Point(f1=751.5, f2=1510.1, f3=2912.8),
        at80=Point(f1=662.8, f2=1831.0, f3=2926.2),
        sd50=Point(f1=56.1, f2=137.2, f3=173.6),
    ),
    "ʊ": ReferenceVowel(
        symbol="ʊ",
        n=48,
        duration_ms=249.3,
        f0_hz=230.0,
        at20=Point(f1=516.8, f2=1225.5, f3=2826.0),
        at50=Point(f1=562.0, f2=1383.2, f3=2822.1),
        at80=Point(f1=567.1, f2=1752.8, f3=2819.7),
        sd50=Point(f1=40.0, f2=158.1, f3=166.5),
    ),
    "u": ReferenceVowel(
        symbol="u",
        n=48,
        duration_ms=303.8,
        f0_hz=235.6,
        at20=Point(f1=465.0, f2=1139.2, f3=2737.1),
        at50=Point(f1=455.1, f2=1090.3, f3=2747.0),
        at80=Point(f1=447.0, f2=1116.0, f3=2754.6),
        sd50=Point(f1=43.2, f2=174.8, f3=138.0),
    ),
    "eɪ": ReferenceVowel(
        symbol="eɪ",
        n=48,
        duration_ms=320.9,
        f0_hz=219.6,
        at20=Point(f1=534.1, f2=2514.3, f3=3053.3),
        at50=Point(f1=476.4, f2=2611.4, f3=3042.7),
        at80=Point(f1=446.9, f2=2692.8, f3=3075.8),
        sd50=Point(f1=41.5, f2=173.8, f3=169.8),
    ),
    "oʊ": ReferenceVowel(
        symbol="oʊ",
        n=48,
        duration_ms=326.7,
        f0_hz=217.7,
        at20=Point(f1=603.0, f2=1078.1, f3=2819.0),
        at50=Point(f1=524.6, f2=1005.6, f3=2852.6),
        at80=Point(f1=471.7, f2=996.4, f3=2808.8),
        sd50=Point(f1=51.3, f2=92.9, f3=180.3),
    ),
    "ɝ": ReferenceVowel(
        symbol="ɝ",
        n=48,
        duration_ms=321.6,
        f0_hz=217.9,
        at20=Point(f1=536.0, f2=1585.9, f3=1950.0),
        at50=Point(f1=511.0, f2=1594.6, f3=1933.2),
        at80=Point(f1=495.3, f2=1690.0, f3=2008.8),
        sd50=Point(f1=43.3, f2=124.1, f3=134.0),
    ),
}


# The two sets are kept apart and NEVER averaged. Formants scale with vocal tract
# length; a mean of the men's and women's tables describes nobody. `GA_REFERENCE_SET`
# selects one explicitly and `vowel_measure` refuses to score position until it does.
REFERENCE_SETS: Mapping[str, Mapping[str, ReferenceVowel]] = {"men": MEN,
                                                             "women": WOMEN}


# Where the 1995 reference is known to be behind the language, widen the band rather
# than report a deviation nobody would hear. Multiplies the SD-based tolerance.
#
#   /ɑ/ and /ɔ/ — the low-back (LOT-THOUGHT, 'cot-caught') merger has continued
#     across most of the United States since these recordings. A confident 'your /ɔ/
#     is wrong' may be flagging a change the reference predates rather than an error.
#     The table itself shows the pair still well separated (men, F2 at 50%: 1326 vs
#     1046 Hz), which is exactly the separation that has since eroded.
#   /u/ — GOOSE has fronted. A modern native production sits higher in F2 than the
#     1995 mean, so an unwidened band flags fronting as an error when it is current
#     General American.
TOLERANCE_MULTIPLIER: Mapping[str, float] = {
    "ɑ": 1.8,
    "ɔ": 1.8,
    "u": 1.8,
}

DEFAULT_TOLERANCE_MULTIPLIER = 1.0


# Tense/lax duration pairs, as RATIOS — the only way this table's durations can be
# used at all (caveat 3). In General American the contrast is carried by quality AND
# length together, so a learner with the formants right and the length wrong still
# sounds wrong. Each entry is (tense, lax).
TENSE_LAX_PAIRS: tuple[tuple[str, str], ...] = (
    ("i", "ɪ"),
    ("u", "ʊ"),
    ("eɪ", "ɛ"),
)


def has_reference(symbol: str, reference_set: str) -> bool:
    """Whether this vowel has a published mean at all. Ten categories do not."""
    return symbol in REFERENCE_SETS.get(reference_set, {})


def lookup(symbol: str, reference_set: str) -> ReferenceVowel | None:
    """The published means for one vowel, or None when the table does not cover it."""
    return REFERENCE_SETS.get(reference_set, {}).get(symbol)


def tense_lax_ratio(tense: str, lax: str, reference_set: str) -> float | None:
    """Published duration ratio for one tense/lax pair, or None if either is absent."""
    first, second = lookup(tense, reference_set), lookup(lax, reference_set)
    if first is None or second is None:
        return None
    if first.duration_ms is None or not second.duration_ms:
        return None
    return first.duration_ms / second.duration_ms

