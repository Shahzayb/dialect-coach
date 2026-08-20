"""General American vowel reference, measured by this project through its own pipeline.

**GENERATED FILE. Do not hand-edit.** Every number below is produced by
`scripts/build_model_reference.py` from renderings captured by
`scripts/capture_model_reference.py`; re-run those instead. Same rule as
`vowel_reference.py`: no formant value in this project is ever typed from memory.

Derived 2026-08-20 from the benchmark passage v1, read by
men:
en-US-AndrewNeural, en-US-BrandonNeural, en-US-BrianNeural, en-US-ChristopherNeural, en-US-
DavisNeural, en-US-EricNeural, en-US-GuyNeural, en-US-TonyNeural.
women:
en-US-AmberNeural, en-US-AriaNeural, en-US-AshleyNeural, en-US-AvaNeural, en-US-CoraNeural,
en-US-ElizabethNeural, en-US-EmmaNeural, en-US-JennyNeural.

## What this is, and what it is not

It is **not** a corpus of native speakers. It is a set of en-US neural voices reading
one passage, measured by the same segmenter and the same Burg analysis that measures
the user. What earns it its place is not that a synthesiser is a person — it is that
the comparison holds everything but the talker still, and that across sixteen voices
it is a distribution rather than one voice's idiosyncrasy.

It complements `vowel_reference.py` and never replaces or averages with it. Hillenbrand
1995 is real humans, peer-reviewed, and covers 12 vowels of citation-form /hVd/ speech.
This covers the whole inventory the passage carries, in connected speech, at today's
vowel qualities — including the ten categories that have no published mean at all, of
which six are r-coloured, on the single most correctable marker for a GA target.

**Durations here CAN be compared in milliseconds**, unlike Hillenbrand's, because these
are connected speech through the identical pipeline. That is the one caveat this table
lifts and the reason it was worth real allowance.

**There are no at20/at80 points, deliberately.** Azure's phoneme boundaries in
connected speech are not accurate enough to measure a glide against: across the eight
men's voices only twelve FACE tokens are long enough to sample at all, from three word
types, and each one's 80% window lands in the following nasal or in the next word's
vowel. The number that falls out describes what FOLLOWS each diphthong in this passage,
not the diphthong itself. The 50% point sits in the middle of the vowel and is
unaffected — it is what every entry below carries.
"""

from __future__ import annotations

from collections.abc import Mapping

from vowel_reference import Point, ReferenceVowel

MEN: Mapping[str, ReferenceVowel] = {
    "aɪ": ReferenceVowel(
        symbol="aɪ",
        n=146,
        voices=8,
        duration_ms=79.1,
        f0_hz=113.0,
        at20=Point(f1=None, f2=None, f3=None),
        at50=Point(f1=452.7, f2=1655.7, f3=2460.9),
        at80=Point(f1=None, f2=None, f3=None),
        sd50=Point(
            f1=50.9,
            f2=131.9,
            f3=158.1,
        ),
    ),
    "aʊ": ReferenceVowel(
        symbol="aʊ",
        n=52,
        voices=8,
        duration_ms=105.6,
        f0_hz=118.5,
        at20=Point(f1=None, f2=None, f3=None),
        at50=Point(f1=636.5, f2=1185.9, f3=2684.3),
        at80=Point(f1=None, f2=None, f3=None),
        sd50=Point(
            f1=72.0,
            f2=121.3,
            f3=215.9,
        ),
    ),
    "eɪ": ReferenceVowel(
        symbol="eɪ",
        n=45,
        voices=8,
        duration_ms=90.4,
        f0_hz=122.8,
        at20=Point(f1=None, f2=None, f3=None),
        at50=Point(f1=363.6, f2=2207.8, f3=2643.3),
        at80=Point(f1=None, f2=None, f3=None),
        sd50=Point(
            f1=48.7,
            f2=162.9,
            f3=203.8,
        ),
    ),
    "i": ReferenceVowel(
        symbol="i",
        n=63,
        voices=8,
        duration_ms=100.9,
        f0_hz=129.3,
        at20=Point(f1=None, f2=None, f3=None),
        at50=Point(f1=268.4, f2=2079.8, f3=2719.8),
        at80=Point(f1=None, f2=None, f3=None),
        sd50=Point(
            f1=23.0,
            f2=120.4,
            f3=170.5,
        ),
    ),
    "oʊ": ReferenceVowel(
        symbol="oʊ",
        n=73,
        voices=8,
        duration_ms=81.8,
        f0_hz=133.3,
        at20=Point(f1=None, f2=None, f3=None),
        at50=Point(f1=413.0, f2=926.8, f3=2682.0),
        at80=Point(f1=None, f2=None, f3=None),
        sd50=Point(
            f1=44.8,
            f2=52.1,
            f3=165.2,
        ),
    ),
    "u": ReferenceVowel(
        symbol="u",
        n=21,
        voices=6,
        duration_ms=86.5,
        f0_hz=132.7,
        at20=Point(f1=None, f2=None, f3=None),
        at50=Point(f1=321.5, f2=970.1, f3=2593.9),
        at80=Point(f1=None, f2=None, f3=None),
        sd50=Point(
            f1=49.3,
            f2=155.3,
            f3=207.0,
        ),
    ),
    "æ": ReferenceVowel(
        symbol="æ",
        n=54,
        voices=8,
        duration_ms=90.8,
        f0_hz=124.9,
        at20=Point(f1=None, f2=None, f3=None),
        at50=Point(f1=602.1, f2=1568.7, f3=2575.2),
        at80=Point(f1=None, f2=None, f3=None),
        sd50=Point(
            f1=77.7,
            f2=71.6,
            f3=156.2,
        ),
    ),
    "ɑ": ReferenceVowel(
        symbol="ɑ",
        n=18,
        voices=5,
        duration_ms=82.2,
        f0_hz=138.5,
        at20=Point(f1=None, f2=None, f3=None),
        at50=Point(f1=702.6, f2=1257.4, f3=2693.7),
        at80=Point(f1=None, f2=None, f3=None),
        sd50=Point(
            f1=150.3,
            f2=52.2,
            f3=241.8,
        ),
    ),
    "ɑɹ": ReferenceVowel(
        symbol="ɑɹ",
        n=12,
        voices=4,
        duration_ms=145.0,
        f0_hz=122.5,
        at20=Point(f1=None, f2=None, f3=None),
        at50=Point(f1=542.3, f2=1395.0, f3=1881.6),
        at80=Point(f1=None, f2=None, f3=None),
        sd50=Point(
            f1=28.7,
            f2=65.5,
            f3=141.0,
        ),
    ),
    "ɔ": ReferenceVowel(
        symbol="ɔ",
        n=39,
        voices=8,
        duration_ms=130.4,
        f0_hz=119.1,
        at20=Point(f1=None, f2=None, f3=None),
        at50=Point(f1=661.3, f2=1085.5, f3=2632.7),
        at80=Point(f1=None, f2=None, f3=None),
        sd50=Point(
            f1=55.5,
            f2=52.3,
            f3=217.1,
        ),
    ),
    "ɔɪ": ReferenceVowel(
        symbol="ɔɪ",
        n=36,
        voices=8,
        duration_ms=153.0,
        f0_hz=117.7,
        at20=Point(f1=None, f2=None, f3=None),
        at50=Point(f1=400.0, f2=1763.4, f3=2405.9),
        at80=Point(f1=None, f2=None, f3=None),
        sd50=Point(
            f1=31.0,
            f2=180.5,
            f3=168.4,
        ),
    ),
    "ɔɹ": ReferenceVowel(
        symbol="ɔɹ",
        n=31,
        voices=8,
        duration_ms=140.7,
        f0_hz=142.8,
        at20=Point(f1=None, f2=None, f3=None),
        at50=Point(f1=437.2, f2=1158.6, f3=1994.4),
        at80=Point(f1=None, f2=None, f3=None),
        sd50=Point(
            f1=34.6,
            f2=149.0,
            f3=324.8,
        ),
    ),
    "ə": ReferenceVowel(
        symbol="ə",
        n=135,
        voices=8,
        duration_ms=72.1,
        f0_hz=113.8,
        at20=Point(f1=None, f2=None, f3=None),
        at50=Point(f1=413.7, f2=1443.5, f3=2667.7),
        at80=Point(f1=None, f2=None, f3=None),
        sd50=Point(
            f1=110.3,
            f2=182.0,
            f3=198.2,
        ),
    ),
    "ɚ": ReferenceVowel(
        symbol="ɚ",
        n=38,
        voices=8,
        duration_ms=105.1,
        f0_hz=125.9,
        at20=Point(f1=None, f2=None, f3=None),
        at50=Point(f1=345.0, f2=1421.3, f3=2120.8),
        at80=Point(f1=None, f2=None, f3=None),
        sd50=Point(
            f1=74.9,
            f2=140.8,
            f3=247.5,
        ),
    ),
    "ɛ": ReferenceVowel(
        symbol="ɛ",
        n=89,
        voices=8,
        duration_ms=65.8,
        f0_hz=129.6,
        at20=Point(f1=None, f2=None, f3=None),
        at50=Point(f1=472.4, f2=1578.7, f3=2558.8),
        at80=Point(f1=None, f2=None, f3=None),
        sd50=Point(
            f1=53.0,
            f2=71.5,
            f3=182.4,
        ),
    ),
    "ɛɹ": ReferenceVowel(
        symbol="ɛɹ",
        n=21,
        voices=6,
        duration_ms=140.4,
        f0_hz=127.1,
        at20=Point(f1=None, f2=None, f3=None),
        at50=Point(f1=472.5, f2=1373.3, f3=1880.5),
        at80=Point(f1=None, f2=None, f3=None),
        sd50=Point(
            f1=29.7,
            f2=86.4,
            f3=263.8,
        ),
    ),
    "ɝ": ReferenceVowel(
        symbol="ɝ",
        n=36,
        voices=8,
        duration_ms=105.1,
        f0_hz=149.9,
        at20=Point(f1=None, f2=None, f3=None),
        at50=Point(f1=414.0, f2=1326.7, f3=1824.3),
        at80=Point(f1=None, f2=None, f3=None),
        sd50=Point(
            f1=24.9,
            f2=88.7,
            f3=232.6,
        ),
    ),
    "ɪ": ReferenceVowel(
        symbol="ɪ",
        n=96,
        voices=8,
        duration_ms=77.9,
        f0_hz=121.0,
        at20=Point(f1=None, f2=None, f3=None),
        at50=Point(f1=392.7, f2=1706.1, f3=2619.8),
        at80=Point(f1=None, f2=None, f3=None),
        sd50=Point(
            f1=90.0,
            f2=141.1,
            f3=186.5,
        ),
    ),
    "ɪɹ": ReferenceVowel(
        symbol="ɪɹ",
        n=21,
        voices=7,
        duration_ms=183.8,
        f0_hz=120.9,
        at20=Point(f1=None, f2=None, f3=None),
        at50=Point(f1=427.9, f2=1355.5, f3=1809.0),
        at80=Point(f1=None, f2=None, f3=None),
        sd50=Point(
            f1=41.4,
            f2=92.5,
            f3=169.5,
        ),
    ),
    "ʊ": ReferenceVowel(
        symbol="ʊ",
        n=28,
        voices=8,
        duration_ms=87.6,
        f0_hz=145.6,
        at20=Point(f1=None, f2=None, f3=None),
        at50=Point(f1=385.7, f2=1357.7, f3=2601.1),
        at80=Point(f1=None, f2=None, f3=None),
        sd50=Point(
            f1=25.9,
            f2=81.7,
            f3=153.9,
        ),
    ),
    "ʌ": ReferenceVowel(
        symbol="ʌ",
        n=39,
        voices=8,
        duration_ms=75.5,
        f0_hz=126.8,
        at20=Point(f1=None, f2=None, f3=None),
        at50=Point(f1=436.8, f2=1193.8, f3=2531.5),
        at80=Point(f1=None, f2=None, f3=None),
        sd50=Point(
            f1=81.7,
            f2=102.3,
            f3=287.0,
        ),
    ),
}

WOMEN: Mapping[str, ReferenceVowel] = {
    "aɪ": ReferenceVowel(
        symbol="aɪ",
        n=133,
        voices=8,
        duration_ms=82.2,
        f0_hz=187.3,
        at20=Point(f1=None, f2=None, f3=None),
        at50=Point(f1=573.5, f2=1958.0, f3=2856.3),
        at80=Point(f1=None, f2=None, f3=None),
        sd50=Point(
            f1=53.3,
            f2=83.1,
            f3=231.5,
        ),
    ),
    "aʊ": ReferenceVowel(
        symbol="aʊ",
        n=42,
        voices=8,
        duration_ms=118.6,
        f0_hz=194.0,
        at20=Point(f1=None, f2=None, f3=None),
        at50=Point(f1=834.3, f2=1408.2, f3=2983.8),
        at80=Point(f1=None, f2=None, f3=None),
        sd50=Point(
            f1=95.9,
            f2=127.6,
            f3=276.7,
        ),
    ),
    "eɪ": ReferenceVowel(
        symbol="eɪ",
        n=30,
        voices=7,
        duration_ms=97.8,
        f0_hz=206.3,
        at20=Point(f1=None, f2=None, f3=None),
        at50=Point(f1=468.7, f2=2521.2, f3=3088.6),
        at80=Point(f1=None, f2=None, f3=None),
        sd50=Point(
            f1=65.9,
            f2=162.6,
            f3=229.1,
        ),
    ),
    "i": ReferenceVowel(
        symbol="i",
        n=50,
        voices=8,
        duration_ms=94.9,
        f0_hz=204.5,
        at20=Point(f1=None, f2=None, f3=None),
        at50=Point(f1=338.5, f2=2311.8, f3=3059.3),
        at80=Point(f1=None, f2=None, f3=None),
        sd50=Point(
            f1=46.9,
            f2=224.9,
            f3=214.5,
        ),
    ),
    "oʊ": ReferenceVowel(
        symbol="oʊ",
        n=71,
        voices=8,
        duration_ms=87.5,
        f0_hz=210.9,
        at20=Point(f1=None, f2=None, f3=None),
        at50=Point(f1=488.1, f2=1175.2, f3=2991.7),
        at80=Point(f1=None, f2=None, f3=None),
        sd50=Point(
            f1=47.4,
            f2=75.0,
            f3=247.3,
        ),
    ),
    "u": ReferenceVowel(
        symbol="u",
        n=13,
        voices=4,
        duration_ms=104.6,
        f0_hz=222.0,
        at20=Point(f1=None, f2=None, f3=None),
        at50=Point(f1=392.2, f2=1277.1, f3=2930.6),
        at80=Point(f1=None, f2=None, f3=None),
        sd50=Point(
            f1=45.9,
            f2=150.0,
            f3=13.9,
        ),
    ),
    "æ": ReferenceVowel(
        symbol="æ",
        n=60,
        voices=8,
        duration_ms=102.0,
        f0_hz=198.5,
        at20=Point(f1=None, f2=None, f3=None),
        at50=Point(f1=808.2, f2=1836.4, f3=2937.2),
        at80=Point(f1=None, f2=None, f3=None),
        sd50=Point(
            f1=84.2,
            f2=153.5,
            f3=177.1,
        ),
    ),
    "ɑ": ReferenceVowel(
        symbol="ɑ",
        n=29,
        voices=8,
        duration_ms=93.1,
        f0_hz=228.9,
        at20=Point(f1=None, f2=None, f3=None),
        at50=Point(f1=850.1, f2=1449.6, f3=3077.5),
        at80=Point(f1=None, f2=None, f3=None),
        sd50=Point(
            f1=111.5,
            f2=127.2,
            f3=220.2,
        ),
    ),
    "ɑɹ": ReferenceVowel(
        symbol="ɑɹ",
        n=18,
        voices=6,
        duration_ms=155.6,
        f0_hz=208.3,
        at20=Point(f1=None, f2=None, f3=None),
        at50=Point(f1=668.0, f2=1710.8, f3=2295.7),
        at80=Point(f1=None, f2=None, f3=None),
        sd50=Point(
            f1=46.0,
            f2=96.6,
            f3=233.3,
        ),
    ),
    "ɔ": ReferenceVowel(
        symbol="ɔ",
        n=37,
        voices=8,
        duration_ms=130.2,
        f0_hz=181.2,
        at20=Point(f1=None, f2=None, f3=None),
        at50=Point(f1=844.0, f2=1314.5, f3=3047.9),
        at80=Point(f1=None, f2=None, f3=None),
        sd50=Point(
            f1=71.7,
            f2=108.3,
            f3=264.0,
        ),
    ),
    "ɔɪ": ReferenceVowel(
        symbol="ɔɪ",
        n=36,
        voices=8,
        duration_ms=152.8,
        f0_hz=189.9,
        at20=Point(f1=None, f2=None, f3=None),
        at50=Point(f1=513.6, f2=2079.7, f3=2750.2),
        at80=Point(f1=None, f2=None, f3=None),
        sd50=Point(
            f1=34.7,
            f2=157.2,
            f3=231.8,
        ),
    ),
    "ɔɹ": ReferenceVowel(
        symbol="ɔɹ",
        n=31,
        voices=8,
        duration_ms=133.4,
        f0_hz=229.2,
        at20=Point(f1=None, f2=None, f3=None),
        at50=Point(f1=559.4, f2=1460.2, f3=2231.5),
        at80=Point(f1=None, f2=None, f3=None),
        sd50=Point(
            f1=35.4,
            f2=104.5,
            f3=354.0,
        ),
    ),
    "ə": ReferenceVowel(
        symbol="ə",
        n=112,
        voices=8,
        duration_ms=67.3,
        f0_hz=194.6,
        at20=Point(f1=None, f2=None, f3=None),
        at50=Point(f1=429.2, f2=1576.1, f3=2965.8),
        at80=Point(f1=None, f2=None, f3=None),
        sd50=Point(
            f1=79.0,
            f2=142.8,
            f3=157.0,
        ),
    ),
    "ɚ": ReferenceVowel(
        symbol="ɚ",
        n=41,
        voices=8,
        duration_ms=97.5,
        f0_hz=213.7,
        at20=Point(f1=None, f2=None, f3=None),
        at50=Point(f1=376.5, f2=1719.3, f3=2493.3),
        at80=Point(f1=None, f2=None, f3=None),
        sd50=Point(
            f1=51.5,
            f2=149.7,
            f3=247.3,
        ),
    ),
    "ɛ": ReferenceVowel(
        symbol="ɛ",
        n=87,
        voices=8,
        duration_ms=68.4,
        f0_hz=196.5,
        at20=Point(f1=None, f2=None, f3=None),
        at50=Point(f1=636.0, f2=1802.2, f3=2915.3),
        at80=Point(f1=None, f2=None, f3=None),
        sd50=Point(
            f1=42.0,
            f2=181.3,
            f3=278.5,
        ),
    ),
    "ɛɹ": ReferenceVowel(
        symbol="ɛɹ",
        n=25,
        voices=7,
        duration_ms=138.0,
        f0_hz=213.4,
        at20=Point(f1=None, f2=None, f3=None),
        at50=Point(f1=572.6, f2=1622.4, f3=2071.3),
        at80=Point(f1=None, f2=None, f3=None),
        sd50=Point(
            f1=50.2,
            f2=127.0,
            f3=150.2,
        ),
    ),
    "ɝ": ReferenceVowel(
        symbol="ɝ",
        n=37,
        voices=8,
        duration_ms=144.1,
        f0_hz=234.8,
        at20=Point(f1=None, f2=None, f3=None),
        at50=Point(f1=543.9, f2=1614.2, f3=2203.1),
        at80=Point(f1=None, f2=None, f3=None),
        sd50=Point(
            f1=50.2,
            f2=111.6,
            f3=245.3,
        ),
    ),
    "ɪ": ReferenceVowel(
        symbol="ɪ",
        n=89,
        voices=8,
        duration_ms=81.0,
        f0_hz=195.4,
        at20=Point(f1=None, f2=None, f3=None),
        at50=Point(f1=463.8, f2=1894.9, f3=2980.7),
        at80=Point(f1=None, f2=None, f3=None),
        sd50=Point(
            f1=62.1,
            f2=237.7,
            f3=216.9,
        ),
    ),
    "ɪɹ": ReferenceVowel(
        symbol="ɪɹ",
        n=18,
        voices=6,
        duration_ms=177.2,
        f0_hz=194.2,
        at20=Point(f1=None, f2=None, f3=None),
        at50=Point(f1=568.4, f2=1681.4, f3=2152.6),
        at80=Point(f1=None, f2=None, f3=None),
        sd50=Point(
            f1=47.4,
            f2=115.1,
            f3=270.6,
        ),
    ),
    "ʊ": ReferenceVowel(
        symbol="ʊ",
        n=25,
        voices=7,
        duration_ms=90.2,
        f0_hz=219.3,
        at20=Point(f1=None, f2=None, f3=None),
        at50=Point(f1=488.5, f2=1716.9, f3=2939.3),
        at80=Point(f1=None, f2=None, f3=None),
        sd50=Point(
            f1=78.4,
            f2=226.8,
            f3=221.6,
        ),
    ),
    "ʌ": ReferenceVowel(
        symbol="ʌ",
        n=46,
        voices=8,
        duration_ms=85.9,
        f0_hz=201.1,
        at20=Point(f1=None, f2=None, f3=None),
        at50=Point(f1=541.9, f2=1452.5, f3=2825.1),
        at80=Point(f1=None, f2=None, f3=None),
        sd50=Point(
            f1=75.2,
            f2=147.4,
            f3=326.4,
        ),
    ),
}


REFERENCE_SETS: Mapping[str, Mapping[str, ReferenceVowel]] = {
    "men": MEN,
    "women": WOMEN,
}


# How many voices produced each category cleanly, INCLUDING the ones below the
# publication floor. A thin category has to look thin rather than be silently absent:
# a surface asked for /ʊɹ/ can then say 'two voices produced it, and a reference needs
# four' instead of the same blank a typo would produce.
VOICE_COVERAGE: Mapping[str, Mapping[str, int]] = {
    "men": {
        "aɪ": 8,
        "aʊ": 8,
        "eɪ": 8,
        "i": 8,
        "oʊ": 8,
        "u": 6,
        "æ": 8,
        "ɑ": 5,
        "ɑɹ": 4,
        "ɔ": 8,
        "ɔɪ": 8,
        "ɔɹ": 8,
        "ə": 8,
        "ɚ": 8,
        "ɛ": 8,
        "ɛɹ": 6,
        "ɝ": 8,
        "ɪ": 8,
        "ɪɹ": 7,
        "ʊ": 8,
        "ʊɹ": 0,
        "ʌ": 8,
    },
    "women": {
        "aɪ": 8,
        "aʊ": 8,
        "eɪ": 7,
        "i": 8,
        "oʊ": 8,
        "u": 4,
        "æ": 8,
        "ɑ": 8,
        "ɑɹ": 6,
        "ɔ": 8,
        "ɔɪ": 8,
        "ɔɹ": 8,
        "ə": 8,
        "ɚ": 8,
        "ɛ": 8,
        "ɛɹ": 7,
        "ɝ": 8,
        "ɪ": 8,
        "ɪɹ": 6,
        "ʊ": 7,
        "ʊɹ": 0,
        "ʌ": 8,
    },
}


def has_reference(symbol: str, reference_set: str) -> bool:
    """Whether this table carries a mean for this vowel. Mirrors `vowel_reference`."""
    return symbol in REFERENCE_SETS.get(reference_set, {})


def lookup(symbol: str, reference_set: str) -> ReferenceVowel | None:
    """The measured means for one vowel, or None when too few voices produced it."""
    return REFERENCE_SETS.get(reference_set, {}).get(symbol)


def voices_behind(symbol: str, reference_set: str) -> int:
    """How many voices produced this category, published or not."""
    return VOICE_COVERAGE.get(reference_set, {}).get(symbol, 0)
