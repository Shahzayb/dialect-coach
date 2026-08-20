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
244 ms for men; the same vowel in connected speech is far shorter. **A bare
comparison in absolute milliseconds is a comparison of two registers**, and it
will show any connected-speech reading as roughly three times too short — an
artefact of this table, not a fact about the speaker. Ratios transfer cleanly —
tense against lax, pre-fortis against pre-lenis, stressed against unstressed —
which is why `TENSE_LAX_PAIRS` exists and no absolute duration target does.
Since v0.11.0 the duration chart does plot these milliseconds, deliberately, but
never alone: `model_reference.py` carries the same vowels in connected speech
through the identical pipeline, and that is the bar a difference may be read
from.

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
    # How many distinct TALKERS are behind the means. Zero here and non-zero in
    # `model_reference.py`, which reuses this dataclass: Hillenbrand's per-vowel token counts
    # are already in `n`, and its per-talker counts are not recoverable from `vowdata.dat`.
    # A reference whose SD is a between-talker spread and one whose SD is within-corpus are
    # different claims, and this field is how a surface can tell which it is holding.
    voices: int = 0

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
REFERENCE_SETS: Mapping[str, Mapping[str, ReferenceVowel]] = {"men": MEN, "women": WOMEN}


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


# --- What a formant delta MEANS, per vowel ------------------------------------------------
# The one place this module can produce confidently wrong advice, which is worse than none.
#
# **F1 maps cleanly and F2 does not.** A higher F1 means a lower tongue body and a more open
# jaw, in every vowel, with no exceptions worth encoding. F2 has two independent causes:
# tongue advancement AND lip posture. Rounding the lips lengthens the front cavity and drops
# F2 without the tongue moving at all — so the same signed F2 delta means "move your tongue"
# in one vowel and "change your lips" in another, and the instructions are not merely
# different, they are opposite. Telling a learner to retract the tongue when the real error
# is unrounded lips makes the vowel worse and teaches them to distrust the tool.
#
# So the text is **looked up, never generated**. A generated instruction is a template with a
# sign in it, and a template cannot know that /u/ and /i/ need different sentences for the
# same number. One entry per vowel, every entry written by hand, and a test asserting that no
# back-rounded vowel's F2 text mentions the tongue and no rhotic's mentions height.

FRONT_UNROUNDED = "front-unrounded"
BACK_ROUNDED = "back-rounded"
BACK_UNROUNDED = "back-unrounded"
CENTRAL = "central"
RHOTIC = "rhotic"

# Every symbol in `phoneme_reference.LEXICAL_SET`, classified by what moves its F2.
# Diphthongs are classified by their NUCLEUS plus their lip posture through the glide:
# /aɪ/ is unrounded throughout, /aʊ/ and /ɔɪ/ both carry rounding.
VOWEL_CLASS: Mapping[str, str] = {
    "i": FRONT_UNROUNDED,
    "ɪ": FRONT_UNROUNDED,
    "ɛ": FRONT_UNROUNDED,
    "æ": FRONT_UNROUNDED,
    "eɪ": FRONT_UNROUNDED,
    "aɪ": FRONT_UNROUNDED,
    "u": BACK_ROUNDED,
    "ʊ": BACK_ROUNDED,
    "oʊ": BACK_ROUNDED,
    "ɔ": BACK_ROUNDED,
    "aʊ": BACK_ROUNDED,
    "ɔɪ": BACK_ROUNDED,
    "ɑ": BACK_UNROUNDED,
    "ʌ": CENTRAL,
    "ə": CENTRAL,
    "ɝ": RHOTIC,
    "ɚ": RHOTIC,
    "ɑɹ": RHOTIC,
    "ɔɹ": RHOTIC,
    "ɛɹ": RHOTIC,
    "ɪɹ": RHOTIC,
    "ʊɹ": RHOTIC,
}


# The low-back pair. Not an error class — a change in progress. The LOT-THOUGHT ('cot-caught')
# merger has spread across most of the United States since 1995, so a confident "your /ɔ/ is
# wrong" may be flagging a merger the reference predates. `TOLERANCE_MULTIPLIER` already
# widens their bands; this names them so a surface can say WHY rather than only measuring
# loosely.
MERGING: frozenset[str] = frozenset({"ɑ", "ɔ"})

MERGING_NOTE = (
    "/ɑ/ and /ɔ/ (LOT and THOUGHT) are merged or merging for most General American speakers, "
    "and the 1995 reference predates that. Treat a gap here as merged-or-merging, not as an "
    "error to drill."
)

# What to say about F1 and F2 for an r-coloured vowel, which is: not much. F3 is the measure.
RHOTIC_SECONDARY = (
    "F1/F2 are secondary for an r-coloured vowel — the measure is F3, and the instruction is "
    "about tongue bunching and lip rounding, never about height or frontness. See the "
    "rhoticity row."
)


@dataclass(frozen=True)
class Instruction:
    """What to DO about a signed formant delta, for one vowel.

    Each field answers one sign of one formant, where the delta is `target − produced`:
    `f1_raise` is what to say when the target sits ABOVE the speaker, `f1_lower` when it sits
    below. `f3_*` is filled for the rhotics and empty everywhere else, because F3 is only a
    finding where r-colouring is the thing being measured.
    """

    f1_raise: str
    f1_lower: str
    f2_raise: str
    f2_lower: str
    f3_raise: str = ""
    f3_lower: str = ""


# Shared wording for the two directions of jaw opening. The tongue-height half of the
# instruction is identical across every vowel — it is F2 that needs per-vowel text — so
# repeating it twenty-two times by hand would invite twenty-two chances to write it
# differently.
_OPEN = "open the jaw further and let the tongue body drop"
_CLOSE = "close the jaw a little and raise the tongue body"


ARTICULATION: Mapping[str, Instruction] = {
    # --- Front unrounded: F2 is tongue advancement, and the lips stay SPREAD ---------------
    # Never "round the lips" to lower F2 here. Rounding a front vowel does lower F2, but it
    # produces a vowel English does not use, and it sounds like a different error entirely.
    "i": Instruction(
        f1_raise=f"{_OPEN} — FLEECE is being said too close, nearer a tense /ɪ/",
        f1_lower=f"{_CLOSE} — FLEECE wants the tongue high and the jaw nearly shut",
        f2_raise="push the tongue further forward and spread the lips into a smile",
        f2_lower="let the tongue sit slightly further back — keep the lips unrounded",
    ),
    "ɪ": Instruction(
        f1_raise=f"{_OPEN} — KIT is lax and sits lower than FLEECE, not at the same height",
        f1_lower=f"{_CLOSE}, but stop short of FLEECE — KIT is not a shortened /i/",
        f2_raise="tongue a little further forward, lips relaxed and slightly spread",
        f2_lower="let the tongue relax back toward centre — keep the lips unrounded",
    ),
    "ɛ": Instruction(
        f1_raise=f"{_OPEN} — DRESS is more open than KIT",
        f1_lower=f"{_CLOSE} — DRESS is closer than TRAP",
        f2_raise="tongue further forward, lips neutral to slightly spread",
        f2_lower="let the tongue settle back toward centre — lips stay unrounded",
    ),
    "æ": Instruction(
        f1_raise=f"{_OPEN} — TRAP is the most open of the front vowels",
        f1_lower=f"{_CLOSE} slightly — TRAP is drifting toward a fully open /a/",
        f2_raise="keep the tongue forward as the jaw opens; do not let TRAP retract to /ɑ/",
        f2_lower="the tongue is too far forward for TRAP — let it ease back, lips unrounded",
    ),
    "eɪ": Instruction(
        f1_raise=f"{_OPEN} at the START of FACE — the glide still closes toward /ɪ/",
        f1_lower=f"{_CLOSE} — FACE begins close and mid, not open",
        f2_raise="carry the tongue forward across the glide and spread the lips as it closes",
        f2_lower="begin FACE a little further back before gliding forward — lips unrounded",
    ),
    "aɪ": Instruction(
        f1_raise=f"{_OPEN} on the first half of PRICE — it starts genuinely open",
        f1_lower=f"{_CLOSE} the start of PRICE a little; it is not a fully open /ɑ/",
        f2_raise="finish the glide further forward, lips spreading toward /ɪ/",
        f2_lower="start PRICE further back and centre before gliding — lips stay unrounded",
    ),
    # --- Back rounded: F2 is LIP POSTURE. Never a tongue instruction. ----------------------
    # This is the case the whole table exists for. A learner whose /u/ has too high an F2 has
    # almost always under-rounded rather than fronted the tongue, and "move your tongue back"
    # sends them to fix the wrong articulator.
    "u": Instruction(
        f1_raise=f"{_OPEN} — GOOSE is being said too close and tense",
        f1_lower=f"{_CLOSE} — GOOSE is a high vowel",
        f2_raise="relax the lip rounding — GOOSE is fronter in current American than in 1995",
        f2_lower="round the lips harder and push them forward into a tight circle",
    ),
    "ʊ": Instruction(
        f1_raise=f"{_OPEN} — FOOT sits lower and laxer than GOOSE",
        f1_lower=f"{_CLOSE} a little — FOOT is still a high vowel, just a lax one",
        f2_raise="loosen the lip rounding — FOOT is only lightly rounded, not pursed",
        f2_lower="round the lips a little more, but keep them loose — FOOT is not GOOSE",
    ),
    "oʊ": Instruction(
        f1_raise=f"{_OPEN} at the start of GOAT — it begins mid, not high",
        f1_lower=f"{_CLOSE} through the glide as GOAT closes toward /ʊ/",
        f2_raise="ease the rounding at the start of GOAT; let it tighten across the glide",
        f2_lower="round the lips more firmly and keep rounding all the way through the glide",
    ),
    "ɔ": Instruction(
        f1_raise=f"{_OPEN} — THOUGHT is an open vowel",
        f1_lower=f"{_CLOSE} slightly — THOUGHT is not as open as LOT",
        f2_raise="loosen the lip rounding — THOUGHT is only weakly rounded in American",
        f2_lower="round the lips more — but see the merger note before drilling this",
    ),
    "aʊ": Instruction(
        f1_raise=f"{_OPEN} on the first half of MOUTH — it starts genuinely open",
        f1_lower=f"{_CLOSE} the start of MOUTH slightly before the glide",
        f2_raise="round the lips later and less — MOUTH starts unrounded and rounds as it glides",
        f2_lower="round the lips harder through the second half, closing toward /ʊ/",
    ),
    "ɔɪ": Instruction(
        f1_raise=f"{_OPEN} at the start of CHOICE — the nucleus is open before it glides up",
        f1_lower=f"{_CLOSE} through the glide as CHOICE closes toward /ɪ/",
        f2_raise="drop the rounding sooner — CHOICE unrounds and spreads as it glides to /ɪ/",
        f2_lower="round the lips more firmly on the nucleus before the glide begins",
    ),
    # --- Back unrounded: tongue, and explicitly NOT lips ----------------------------------
    "ɑ": Instruction(
        f1_raise=f"{_OPEN} — LOT is the most open vowel in the inventory",
        f1_lower=f"{_CLOSE} a little — LOT is drifting below the vowel space",
        f2_raise="let the tongue come forward — keep the lips unrounded, LOT is not THOUGHT",
        f2_lower="draw the tongue back and down, lips relaxed and unrounded",
    ),
    # --- Central --------------------------------------------------------------------------
    "ʌ": Instruction(
        f1_raise=f"{_OPEN} — STRUT is more open than schwa, and it is a full stressed vowel",
        f1_lower=f"{_CLOSE} slightly — STRUT is not as open as LOT",
        f2_raise="let the tongue come forward toward centre, lips neutral",
        f2_lower="let the tongue settle back toward centre, lips neutral and unrounded",
    ),
    "ə": Instruction(
        f1_raise=f"{_OPEN} slightly — but schwa's real measure is how far it REDUCES",
        f1_lower=f"{_CLOSE} slightly — but schwa's real measure is how far it REDUCES",
        f2_raise="let the tongue rest nearer centre — schwa is the vowel of no effort",
        f2_lower="let the tongue rest nearer centre — schwa is the vowel of no effort",
    ),
    # --- Rhotic: F3 is the measure, and F1/F2 say so rather than instructing --------------
    "ɝ": Instruction(
        f1_raise=RHOTIC_SECONDARY,
        f1_lower=RHOTIC_SECONDARY,
        f2_raise=RHOTIC_SECONDARY,
        f2_lower=RHOTIC_SECONDARY,
        f3_raise="less r-colouring: release the tongue bunching and relax the lips",
        f3_lower="more r-colouring: bunch the tongue body up and back (or curl the tip "
        "back), and round the lips slightly — NURSE is r-coloured all the way through",
    ),
    "ɚ": Instruction(
        f1_raise=RHOTIC_SECONDARY,
        f1_lower=RHOTIC_SECONDARY,
        f2_raise=RHOTIC_SECONDARY,
        f2_lower=RHOTIC_SECONDARY,
        f3_raise="less r-colouring: this is the unstressed lettER vowel, not a full NURSE",
        f3_lower="more r-colouring: bunch or curl the tongue on the unstressed syllable too "
        "— dropping it here is what makes 'letter' sound non-rhotic",
    ),
    "ɑɹ": Instruction(
        f1_raise=RHOTIC_SECONDARY,
        f1_lower=RHOTIC_SECONDARY,
        f2_raise=RHOTIC_SECONDARY,
        f2_lower=RHOTIC_SECONDARY,
        f3_raise="less r-colouring: START opens fully before the r-colouring arrives",
        f3_lower="more r-colouring: bunch the tongue back and up into the /ɹ/ and hold it "
        "— do not let START end as a plain open vowel",
    ),
    "ɔɹ": Instruction(
        f1_raise=RHOTIC_SECONDARY,
        f1_lower=RHOTIC_SECONDARY,
        f2_raise=RHOTIC_SECONDARY,
        f2_lower=RHOTIC_SECONDARY,
        f3_raise="less r-colouring: NORTH keeps its rounded nucleus before the /ɹ/",
        f3_lower="more r-colouring: keep the lips rounded and bunch the tongue back into "
        "the /ɹ/ without releasing",
    ),
    "ɛɹ": Instruction(
        f1_raise=RHOTIC_SECONDARY,
        f1_lower=RHOTIC_SECONDARY,
        f2_raise=RHOTIC_SECONDARY,
        f2_lower=RHOTIC_SECONDARY,
        f3_raise="less r-colouring: SQUARE begins as a clear front vowel",
        f3_lower="more r-colouring: bunch the tongue back into the /ɹ/ rather than ending "
        "SQUARE on the vowel alone",
    ),
    "ɪɹ": Instruction(
        f1_raise=RHOTIC_SECONDARY,
        f1_lower=RHOTIC_SECONDARY,
        f2_raise=RHOTIC_SECONDARY,
        f2_lower=RHOTIC_SECONDARY,
        f3_raise="less r-colouring: NEAR begins high and front before the /ɹ/",
        f3_lower="more r-colouring: carry the bunching through to the end of NEAR",
    ),
    "ʊɹ": Instruction(
        f1_raise=RHOTIC_SECONDARY,
        f1_lower=RHOTIC_SECONDARY,
        f2_raise=RHOTIC_SECONDARY,
        f2_lower=RHOTIC_SECONDARY,
        f3_raise="less r-colouring: CURE keeps its rounded nucleus before the /ɹ/",
        f3_lower="more r-colouring: round, then bunch back into the /ɹ/. CURE is the rarest "
        "en-US vowel and is actively merging into NORTH — treat a gap here as best-effort",
    ),
}


def vowel_class(symbol: str) -> str:
    """Which articulator moves this vowel's F2, or "" for an unknown symbol."""
    return VOWEL_CLASS.get(symbol, "")


def is_merging(symbol: str) -> bool:
    """Whether a gap here is a sound change in progress rather than an error to drill."""
    return symbol in MERGING


def instruction_for(symbol: str, formant: str, delta: float | None) -> str:
    """What to DO about `delta` — signed `target − produced` — on this vowel's `formant`.

    Looked up from `ARTICULATION`, never composed. `formant` is "F1", "F2" or "F3"; a delta
    of exactly zero, a None, an unknown vowel or an F3 delta on a non-rhotic all return "",
    which the caller renders as "no instruction" rather than as an empty claim.
    """
    entry = ARTICULATION.get(symbol)
    if entry is None or delta is None or delta == 0.0:
        return ""
    rising = delta > 0
    if formant == "F1":
        return entry.f1_raise if rising else entry.f1_lower
    if formant == "F2":
        return entry.f2_raise if rising else entry.f2_lower
    if formant == "F3":
        return entry.f3_raise if rising else entry.f3_lower
    return ""


# --- Bridging phrases ------------------------------------------------------------------------
# A sentence that forces one vowel repeatedly in VARIED consonant contexts — not a word list.
# The value is the co-articulation: a vowel is easy to hit in isolation and hard to hold
# through a following /l/, a preceding /s/, or a stressed-to-unstressed transition, and a list
# of citation-form words never exercises that.
#
# Written by hand rather than generated, which makes them free, offline and permanent: the
# fallback coach can offer a bridging phrase with no API key and no network, forever. The
# Gemini coach writes fresher ones when it is available; these are what exists when it is not.

BRIDGING_PHRASES: Mapping[str, tuple[str, ...]] = {
    "i": (
        "Each evening she reads three easy pieces and repeats the key scenes.",
        "We believe these three teachers each need a clean sheet.",
    ),
    "ɪ": (
        "Bill fixed the little tin lid with quick thin strips.",
        "Six kids in this village still finish it quickly.",
    ),
    "ɛ": (
        "Ten friends left the wet bench and went ahead.",
        "Every guest said the bread and the eggs were fresh.",
    ),
    "æ": (
        "Pat's cat grabbed a bad hat and ran back past the lab.",
        "Sam had a fast lap and a happy family after that.",
    ),
    "ɑ": (
        "Tom got a lot of hot stock from the odd shop.",
        "The doctor from the college watched the modern rock concert.",
    ),
    "ɔ": (
        "Paul bought a tall lawn chair and thought long about the cost.",
        "The author walked across the broad hall and called for water.",
    ),
    "ʌ": (
        "Some of us must cut the rough bunch up front.",
        "My brother's summer money runs out once a month.",
    ),
    "ʊ": (
        "The cook took a good look and put the wool book back.",
        "Would you pull the full wooden hood off the bush?",
    ),
    "u": (
        "Sue moved two blue spoons through the soup room.",
        "The rude student knew the new tune too soon.",
    ),
    "ɝ": (
        "The nurse heard the first word and turned to search the third shirt.",
        "Her worst nerves were certain to disturb the early service.",
    ),
    "ə": (
        "The purpose of a lesson is a chance to listen again.",
        "The company began a campaign of support around the region.",
    ),
    "ɚ": (
        "Another teacher answered better after her father's letter.",
        "The other reader remembered a longer summer under cover.",
    ),
    "eɪ": (
        "They came late and made the same eight plates today.",
        "Wait — they may take the train straight to the same place.",
    ),
    "aɪ": (
        "Five nights I tried to find my white bike outside.",
        "I like the quiet time by the wide river at night.",
    ),
    "oʊ": (
        "Joe rode home alone over the cold slow road.",
        "Both boats float close to the old stone coast.",
    ),
    "aʊ": (
        "The loud crowd found out how the brown house sounds.",
        "Now the mountain town is proud of its powerful sound.",
    ),
    "ɔɪ": (
        "Roy's choice of noise spoiled the boiling oil.",
        "The boy avoided the noisy point and enjoyed the quiet.",
    ),
    "ɑɹ": (
        "Mark parked the large dark car in the hard yard.",
        "Charlie's garden is far apart from the sharp barn.",
    ),
    "ɔɹ": (
        "George forced the short cord more before morning.",
        "Four important reports were ordered before the storm.",
    ),
    "ɛɹ": (
        "They shared a pair of chairs where the air was fair.",
        "Mary's parents were aware of the various careful repairs.",
    ),
    "ɪɹ": (
        "We hear the clear cheer here every year, near the pier.",
        "The engineer's career is nearly clear this year.",
    ),
    "ʊɹ": (
        "During the tour he was sure the cure was pure.",
        "Tourists in Europe are curious about the rural detour.",
    ),
}


def bridging_phrases(symbol: str) -> tuple[str, ...]:
    """Hand-written phrases forcing one vowel in varied contexts. Empty for an unknown symbol."""
    return BRIDGING_PHRASES.get(symbol, ())


# --- What the phoneme-keyed table has no slot for -------------------------------------------
# `phoneme_reference.minimal_pairs` is keyed on a SUBSTITUTION — an expected/produced pair —
# because that is what Azure reports. Neither of the two contrasts below is a substitution:
# both are the same phoneme said with the wrong LENGTH or the wrong STRESS, which Azure scores
# as correct. They have nowhere to live in that table and they live here instead.


@dataclass(frozen=True)
class PreFortisPair:
    """Two words whose vowels differ mainly in LENGTH, not in quality.

    In American English the vowel is markedly shorter before a voiceless coda than before a
    voiced one, and **that length difference, not the final consonant's own voicing, is the
    main cue** separating "bad" from "bat" — the /d/ and /t/ are often both weakly released
    or flapped. A learner who produces no clipping produces minimal pairs that do not land,
    however cleanly they articulate the consonant.
    """

    vowel: str
    long: str  # before a voiced coda
    short: str  # before a voiceless coda


PRE_FORTIS_PAIRS: tuple[PreFortisPair, ...] = (
    PreFortisPair("i", "bead", "beat"),
    PreFortisPair("i", "league", "leak"),
    PreFortisPair("ɪ", "bid", "bit"),
    PreFortisPair("ɛ", "bed", "bet"),
    PreFortisPair("æ", "bad", "bat"),
    PreFortisPair("æ", "bag", "back"),
    PreFortisPair("ɑ", "cod", "cot"),
    PreFortisPair("ʌ", "bud", "but"),
    PreFortisPair("u", "rude", "root"),
    PreFortisPair("eɪ", "made", "mate"),
    PreFortisPair("eɪ", "save", "safe"),
    PreFortisPair("aɪ", "ride", "write"),
    PreFortisPair("aɪ", "prize", "price"),
    PreFortisPair("oʊ", "robe", "rope"),
    PreFortisPair("aʊ", "loud", "lout"),
    PreFortisPair("ɝ", "heard", "hurt"),
)


@dataclass(frozen=True)
class StressPair:
    """One spelling, two stress patterns, two different reduction patterns.

    The vowel that carries stress is full; the one that loses it reduces toward schwa. So a
    stress-shift pair drills PLACEMENT and REDUCTION at once, which is exactly the pair of
    things `vowel_measure.reduction` and `vowel_measure.stress_contrasts` measure and Azure
    does not score.
    """

    word: str
    noun: str  # capitals mark the stressed syllable
    verb: str
    sentence: str


STRESS_SHIFT_PAIRS: tuple[StressPair, ...] = (
    StressPair("record", "REcord", "reCORD", "They reCORD every REcord they release."),
    StressPair("present", "PREsent", "preSENT", "I will preSENT the PREsent myself."),
    StressPair("contract", "CONtract", "conTRACT", "Muscles conTRACT; a CONtract does not."),
    StressPair("object", "OBject", "obJECT", "I obJECT to that OBject being here."),
    StressPair("produce", "PROduce", "proDUCE", "Farms proDUCE the PROduce we buy."),
    StressPair("permit", "PERmit", "perMIT", "They perMIT it once you hold a PERmit."),
    StressPair("rebel", "REbel", "reBEL", "The REbel will reBEL again tomorrow."),
    StressPair("increase", "INcrease", "inCREASE", "Prices inCREASE, so we expect an INcrease."),
    StressPair("conduct", "CONduct", "conDUCT", "She will conDUCT herself with good CONduct."),
    StressPair("suspect", "SUSpect", "susPECT", "I susPECT the SUSpect is lying."),
)
