"""Arrival bands per rung, measured by this project through its own pipeline.

**GENERATED FILE. Do not hand-edit.** Every number below is produced by
`scripts/build_ladder_reference.py` from renderings captured by
`scripts/capture_model_reference.py`; re-run those instead. Same rule as
`model_reference.py`: no reference number in this project is ever typed from memory.

Derived 2026-08-21 from benchmark passage
v1, read by 16 en-US neural voices:
en-US-AmberNeural, en-US-AndrewNeural, en-US-AriaNeural, en-US-AshleyNeural, en-US-AvaNeural,
en-US-BrandonNeural, en-US-BrianNeural, en-US-ChristopherNeural, en-US-CoraNeural, en-US-
DavisNeural, en-US-ElizabethNeural, en-US-EmmaNeural, en-US-EricNeural, en-US-GuyNeural, en-
US-JennyNeural, en-US-TonyNeural

## What this answers

"Am I inside the range native talkers occupy for this unit" — the arrival half of `#39`'s
measured resolution. The other half, whether the change cleared the speaker's own
session-to-session variation, is `vowel_measure.NoiseFloor` and is not here.

## What is deliberately absent

**The sound rung.** `model_reference.sd50` already carries a between-voice formant spread per
vowel. A second sound band would be two sources of truth for one claim.

**Milliseconds.** Word bands are duration relative to the reading's own mean word, so they
measure reduction rather than speaking rate.

**Anything split by sex.** Every scalar here is a ratio or a log ratio and carries no vocal
tract length, unlike the formants `model_reference` has to split.

Keyed by index into the benchmark passage — word index for `WORD`, sentence index for
`SENTENCE`. A changed passage invalidates those keys, which is what `BENCHMARK_VERSION`
records.
"""

from __future__ import annotations

from collections.abc import Mapping

from ladder import Band

BENCHMARK_VERSION = 1
VOICES = 16

# The sentence each index refers to, so a band can be read without re-splitting the passage.
SENTENCE_TEXT: Mapping[int, str] = {
    0: "Each morning I read these same words out loud, the way I said them last week.",
    1: "Nothing here is clever.",
    2: (
        "The whole value is that the passage never changes, so whatever moves is my own"
        " voice, not the writing."
    ),
    3: "Three things go through my mind while I read.",
    4: (
        "The first is breath, where a short pause helps the listener, and where I join"
        " two thoughts that should have stayed apart."
    ),
    5: (
        "The second is the end of every word, the hard sounds I let go soft when I am"
        " tired, in asked and helped, in world, month and next."
    ),
    6: (
        "The third is the choice I make on each vowel, whether to hold it full and clear"
        " or to let it slide."
    ),
    7: "A few of them still catch me.",
    8: "Brother and breathe.",
    9: "Believe and above.",
    10: "School, careful and cold.",
    11: (
        "During a long, honest answer the joy goes out of it, I am not sure of my own"
        " voice, and I judge it more than I should."
    ),
    12: "So I stop, sit up, take a fair pace, and finish the thought I began.",
    13: ("In a good year I would like to measure how far this went, without the usual excuses."),
}

WORD: Mapping[int, Mapping[str, Band]] = {
    0: {
        "relative_duration": Band(metric="relative_duration", mean=1.1304, sd=0.0848, voices=16),
    },
    1: {
        "relative_duration": Band(metric="relative_duration", mean=1.4947, sd=0.0845, voices=16),
    },
    2: {
        "relative_duration": Band(metric="relative_duration", mean=0.2942, sd=0.0460, voices=16),
    },
    3: {
        "relative_duration": Band(metric="relative_duration", mean=0.9739, sd=0.1341, voices=16),
    },
    4: {
        "relative_duration": Band(metric="relative_duration", mean=0.8049, sd=0.0494, voices=16),
    },
    5: {
        "relative_duration": Band(metric="relative_duration", mean=1.0163, sd=0.1032, voices=16),
    },
    6: {
        "relative_duration": Band(metric="relative_duration", mean=1.1342, sd=0.0606, voices=16),
    },
    7: {
        "relative_duration": Band(metric="relative_duration", mean=0.6845, sd=0.0736, voices=16),
    },
    8: {
        "relative_duration": Band(metric="relative_duration", mean=1.9073, sd=0.1629, voices=16),
    },
    9: {
        "relative_duration": Band(metric="relative_duration", mean=0.6085, sd=0.0452, voices=16),
    },
    10: {
        "relative_duration": Band(metric="relative_duration", mean=0.7261, sd=0.0695, voices=16),
    },
    11: {
        "relative_duration": Band(metric="relative_duration", mean=0.3372, sd=0.0435, voices=16),
    },
    12: {
        "relative_duration": Band(metric="relative_duration", mean=0.9067, sd=0.0621, voices=16),
    },
    13: {
        "relative_duration": Band(metric="relative_duration", mean=0.5335, sd=0.0393, voices=16),
    },
    14: {
        "relative_duration": Band(metric="relative_duration", mean=1.2500, sd=0.0678, voices=16),
    },
    15: {
        "relative_duration": Band(metric="relative_duration", mean=1.5010, sd=0.0790, voices=16),
    },
    16: {
        "relative_duration": Band(metric="relative_duration", mean=1.7884, sd=0.1130, voices=16),
    },
    17: {
        "relative_duration": Band(metric="relative_duration", mean=0.8487, sd=0.1488, voices=16),
    },
    18: {
        "relative_duration": Band(metric="relative_duration", mean=0.4708, sd=0.0740, voices=16),
    },
    19: {
        "relative_duration": Band(metric="relative_duration", mean=2.0897, sd=0.1571, voices=16),
    },
    20: {
        "relative_duration": Band(metric="relative_duration", mean=0.6847, sd=0.1022, voices=16),
    },
    21: {
        "relative_duration": Band(metric="relative_duration", mean=0.8537, sd=0.0603, voices=16),
    },
    22: {
        "relative_duration": Band(metric="relative_duration", mean=1.7068, sd=0.1138, voices=16),
    },
    23: {
        "relative_duration": Band(metric="relative_duration", mean=0.5450, sd=0.0874, voices=16),
    },
    24: {
        "relative_duration": Band(metric="relative_duration", mean=0.5297, sd=0.0696, voices=16),
    },
    25: {
        "relative_duration": Band(metric="relative_duration", mean=0.3552, sd=0.0462, voices=16),
    },
    26: {
        "relative_duration": Band(metric="relative_duration", mean=1.7948, sd=0.1212, voices=16),
    },
    27: {
        "relative_duration": Band(metric="relative_duration", mean=1.1388, sd=0.0967, voices=16),
    },
    28: {
        "relative_duration": Band(metric="relative_duration", mean=2.4861, sd=0.2328, voices=16),
    },
    29: {
        "relative_duration": Band(metric="relative_duration", mean=0.9765, sd=0.1938, voices=16),
    },
    30: {
        "relative_duration": Band(metric="relative_duration", mean=1.3336, sd=0.0877, voices=16),
    },
    31: {
        "relative_duration": Band(metric="relative_duration", mean=1.2531, sd=0.1343, voices=16),
    },
    32: {
        "relative_duration": Band(metric="relative_duration", mean=0.4710, sd=0.0528, voices=16),
    },
    33: {
        "relative_duration": Band(metric="relative_duration", mean=0.7471, sd=0.0538, voices=16),
    },
    34: {
        "relative_duration": Band(metric="relative_duration", mean=0.6974, sd=0.0874, voices=16),
    },
    35: {
        "relative_duration": Band(metric="relative_duration", mean=2.1023, sd=0.1276, voices=16),
    },
    36: {
        "relative_duration": Band(metric="relative_duration", mean=1.0968, sd=0.0803, voices=16),
    },
    37: {
        "relative_duration": Band(metric="relative_duration", mean=0.4210, sd=0.0349, voices=16),
    },
    38: {
        "relative_duration": Band(metric="relative_duration", mean=1.8022, sd=0.1385, voices=16),
    },
    39: {
        "relative_duration": Band(metric="relative_duration", mean=1.3378, sd=0.1301, voices=16),
    },
    40: {
        "relative_duration": Band(metric="relative_duration", mean=1.0770, sd=0.1066, voices=16),
    },
    41: {
        "relative_duration": Band(metric="relative_duration", mean=0.5695, sd=0.0422, voices=16),
    },
    42: {
        "relative_duration": Band(metric="relative_duration", mean=0.8192, sd=0.0974, voices=16),
    },
    43: {
        "relative_duration": Band(metric="relative_duration", mean=0.5021, sd=0.0632, voices=16),
    },
    44: {
        "relative_duration": Band(metric="relative_duration", mean=1.4313, sd=0.1486, voices=16),
    },
    45: {
        "relative_duration": Band(metric="relative_duration", mean=0.6734, sd=0.0941, voices=16),
    },
    46: {
        "relative_duration": Band(metric="relative_duration", mean=0.3165, sd=0.0520, voices=16),
    },
    47: {
        "relative_duration": Band(metric="relative_duration", mean=1.7279, sd=0.1428, voices=16),
    },
    48: {
        "relative_duration": Band(metric="relative_duration", mean=0.7536, sd=0.2197, voices=16),
    },
    49: {
        "relative_duration": Band(metric="relative_duration", mean=1.2981, sd=0.0391, voices=16),
    },
    50: {
        "relative_duration": Band(metric="relative_duration", mean=0.4283, sd=0.0422, voices=16),
    },
    51: {
        "relative_duration": Band(metric="relative_duration", mean=1.8762, sd=0.1275, voices=16),
    },
    52: {
        "relative_duration": Band(metric="relative_duration", mean=0.8342, sd=0.1098, voices=16),
    },
    53: {
        "relative_duration": Band(metric="relative_duration", mean=0.2756, sd=0.0470, voices=15),
    },
    54: {
        "relative_duration": Band(metric="relative_duration", mean=1.2035, sd=0.2571, voices=16),
    },
    55: {
        "relative_duration": Band(metric="relative_duration", mean=1.3569, sd=0.1213, voices=16),
    },
    56: {
        "relative_duration": Band(metric="relative_duration", mean=1.0103, sd=0.0808, voices=16),
    },
    57: {
        "relative_duration": Band(metric="relative_duration", mean=0.4413, sd=0.1649, voices=16),
    },
    58: {
        "relative_duration": Band(metric="relative_duration", mean=1.9877, sd=0.4291, voices=16),
    },
    59: {
        "relative_duration": Band(metric="relative_duration", mean=0.8716, sd=0.3495, voices=16),
    },
    60: {
        "relative_duration": Band(metric="relative_duration", mean=0.5925, sd=0.1271, voices=16),
    },
    61: {
        "relative_duration": Band(metric="relative_duration", mean=0.2796, sd=0.1195, voices=16),
    },
    62: {
        "relative_duration": Band(metric="relative_duration", mean=1.1212, sd=0.2559, voices=16),
    },
    63: {
        "relative_duration": Band(metric="relative_duration", mean=0.9471, sd=0.1234, voices=16),
    },
    64: {
        "relative_duration": Band(metric="relative_duration", mean=1.3226, sd=0.1521, voices=16),
    },
    65: {
        "relative_duration": Band(metric="relative_duration", mean=0.6255, sd=0.1656, voices=16),
    },
    66: {
        "relative_duration": Band(metric="relative_duration", mean=0.6092, sd=0.0834, voices=16),
    },
    67: {
        "relative_duration": Band(metric="relative_duration", mean=0.4384, sd=0.0763, voices=16),
    },
    68: {
        "relative_duration": Band(metric="relative_duration", mean=0.9453, sd=0.1456, voices=16),
    },
    69: {
        "relative_duration": Band(metric="relative_duration", mean=1.8462, sd=0.2684, voices=16),
    },
    70: {
        "relative_duration": Band(metric="relative_duration", mean=0.8276, sd=0.3379, voices=16),
    },
    71: {
        "relative_duration": Band(metric="relative_duration", mean=1.5030, sd=0.2266, voices=16),
    },
    72: {
        "relative_duration": Band(metric="relative_duration", mean=0.4711, sd=0.2943, voices=16),
    },
    73: {
        "relative_duration": Band(metric="relative_duration", mean=0.5776, sd=0.0444, voices=16),
    },
    74: {
        "relative_duration": Band(metric="relative_duration", mean=0.6577, sd=0.1573, voices=16),
    },
    75: {
        "relative_duration": Band(metric="relative_duration", mean=0.4334, sd=0.1155, voices=16),
    },
    76: {
        "relative_duration": Band(metric="relative_duration", mean=0.9351, sd=0.1757, voices=16),
    },
    77: {
        "relative_duration": Band(metric="relative_duration", mean=1.8377, sd=0.2292, voices=16),
    },
    78: {
        "relative_duration": Band(metric="relative_duration", mean=0.7662, sd=0.2703, voices=16),
    },
    79: {
        "relative_duration": Band(metric="relative_duration", mean=1.0934, sd=0.1503, voices=16),
    },
    80: {
        "relative_duration": Band(metric="relative_duration", mean=1.3354, sd=0.1060, voices=16),
    },
    81: {
        "relative_duration": Band(metric="relative_duration", mean=0.2573, sd=0.2780, voices=16),
    },
    82: {
        "relative_duration": Band(metric="relative_duration", mean=0.8150, sd=0.1745, voices=16),
    },
    83: {
        "relative_duration": Band(metric="relative_duration", mean=0.6181, sd=0.0954, voices=16),
    },
    84: {
        "relative_duration": Band(metric="relative_duration", mean=1.6540, sd=0.2911, voices=16),
    },
    85: {
        "relative_duration": Band(metric="relative_duration", mean=0.5594, sd=0.2667, voices=16),
    },
    86: {
        "relative_duration": Band(metric="relative_duration", mean=0.2470, sd=0.0887, voices=16),
    },
    87: {
        "relative_duration": Band(metric="relative_duration", mean=0.5222, sd=0.1018, voices=16),
    },
    88: {
        "relative_duration": Band(metric="relative_duration", mean=2.1278, sd=0.4604, voices=16),
    },
    89: {
        "relative_duration": Band(metric="relative_duration", mean=0.9960, sd=0.2677, voices=16),
    },
    90: {
        "relative_duration": Band(metric="relative_duration", mean=1.2532, sd=0.1686, voices=16),
    },
    91: {
        "relative_duration": Band(metric="relative_duration", mean=0.4665, sd=0.2176, voices=16),
    },
    92: {
        "relative_duration": Band(metric="relative_duration", mean=1.6888, sd=0.3640, voices=16),
    },
    93: {
        "relative_duration": Band(metric="relative_duration", mean=0.8883, sd=0.2258, voices=16),
    },
    94: {
        "relative_duration": Band(metric="relative_duration", mean=1.7470, sd=0.3277, voices=16),
    },
    95: {
        "relative_duration": Band(metric="relative_duration", mean=1.3479, sd=0.2499, voices=16),
    },
    96: {
        "relative_duration": Band(metric="relative_duration", mean=0.5284, sd=0.1769, voices=16),
    },
    97: {
        "relative_duration": Band(metric="relative_duration", mean=1.9457, sd=0.4294, voices=16),
    },
    98: {
        "relative_duration": Band(metric="relative_duration", mean=0.7865, sd=0.2895, voices=16),
    },
    99: {
        "relative_duration": Band(metric="relative_duration", mean=1.2475, sd=0.1779, voices=16),
    },
    100: {
        "relative_duration": Band(metric="relative_duration", mean=0.4612, sd=0.1745, voices=16),
    },
    101: {
        "relative_duration": Band(metric="relative_duration", mean=0.4115, sd=0.0480, voices=16),
    },
    102: {
        "relative_duration": Band(metric="relative_duration", mean=1.3848, sd=0.2923, voices=16),
    },
    103: {
        "relative_duration": Band(metric="relative_duration", mean=0.2597, sd=0.2783, voices=16),
    },
    104: {
        "relative_duration": Band(metric="relative_duration", mean=0.9127, sd=0.2024, voices=16),
    },
    105: {
        "relative_duration": Band(metric="relative_duration", mean=0.6363, sd=0.1530, voices=16),
    },
    106: {
        "relative_duration": Band(metric="relative_duration", mean=0.7253, sd=0.1088, voices=16),
    },
    107: {
        "relative_duration": Band(metric="relative_duration", mean=1.8169, sd=0.3287, voices=16),
    },
    108: {
        "relative_duration": Band(metric="relative_duration", mean=1.2678, sd=0.1909, voices=16),
    },
    109: {
        "relative_duration": Band(metric="relative_duration", mean=0.4245, sd=0.2391, voices=16),
    },
    110: {
        "relative_duration": Band(metric="relative_duration", mean=0.9226, sd=0.1542, voices=16),
    },
    111: {
        "relative_duration": Band(metric="relative_duration", mean=0.4921, sd=0.1304, voices=16),
    },
    112: {
        "relative_duration": Band(metric="relative_duration", mean=1.1288, sd=0.2162, voices=16),
    },
    113: {
        "relative_duration": Band(metric="relative_duration", mean=0.5434, sd=0.1530, voices=16),
    },
    114: {
        "relative_duration": Band(metric="relative_duration", mean=1.4536, sd=0.3423, voices=16),
    },
    115: {
        "relative_duration": Band(metric="relative_duration", mean=0.7205, sd=0.2507, voices=16),
    },
    116: {
        "relative_duration": Band(metric="relative_duration", mean=0.4401, sd=0.0909, voices=16),
    },
    117: {
        "relative_duration": Band(metric="relative_duration", mean=0.5761, sd=0.0387, voices=16),
    },
    118: {
        "relative_duration": Band(metric="relative_duration", mean=0.4239, sd=0.0762, voices=16),
    },
    119: {
        "relative_duration": Band(metric="relative_duration", mean=2.0146, sd=0.4576, voices=16),
    },
    120: {
        "relative_duration": Band(metric="relative_duration", mean=0.8064, sd=0.3616, voices=16),
    },
    121: {
        "relative_duration": Band(metric="relative_duration", mean=0.8388, sd=0.0613, voices=16),
    },
    122: {
        "relative_duration": Band(metric="relative_duration", mean=0.3904, sd=0.1519, voices=16),
    },
    123: {
        "relative_duration": Band(metric="relative_duration", mean=0.6941, sd=0.1256, voices=16),
    },
    124: {
        "relative_duration": Band(metric="relative_duration", mean=1.0265, sd=0.1490, voices=16),
    },
    125: {
        "relative_duration": Band(metric="relative_duration", mean=1.2585, sd=0.0851, voices=16),
    },
    126: {
        "relative_duration": Band(metric="relative_duration", mean=1.3351, sd=0.0806, voices=16),
    },
    127: {
        "relative_duration": Band(metric="relative_duration", mean=1.6193, sd=0.1490, voices=16),
    },
    128: {
        "relative_duration": Band(metric="relative_duration", mean=0.5547, sd=0.3808, voices=16),
    },
    129: {
        "relative_duration": Band(metric="relative_duration", mean=1.9172, sd=0.4392, voices=16),
    },
    130: {
        "relative_duration": Band(metric="relative_duration", mean=1.9164, sd=0.2402, voices=16),
    },
    131: {
        "relative_duration": Band(metric="relative_duration", mean=0.5507, sd=0.4295, voices=16),
    },
    132: {
        "relative_duration": Band(metric="relative_duration", mean=1.9973, sd=0.3936, voices=16),
    },
    133: {
        "relative_duration": Band(metric="relative_duration", mean=2.5529, sd=0.3716, voices=16),
    },
    134: {
        "relative_duration": Band(metric="relative_duration", mean=1.9122, sd=0.2193, voices=16),
    },
    135: {
        "relative_duration": Band(metric="relative_duration", mean=0.5863, sd=0.4232, voices=16),
    },
    136: {
        "relative_duration": Band(metric="relative_duration", mean=1.9108, sd=0.4383, voices=16),
    },
    137: {
        "relative_duration": Band(metric="relative_duration", mean=1.4062, sd=0.2274, voices=16),
    },
    138: {
        "relative_duration": Band(metric="relative_duration", mean=0.3047, sd=0.2901, voices=16),
    },
    139: {
        "relative_duration": Band(metric="relative_duration", mean=1.7563, sd=0.4125, voices=16),
    },
    140: {
        "relative_duration": Band(metric="relative_duration", mean=1.6813, sd=0.1434, voices=16),
    },
    141: {
        "relative_duration": Band(metric="relative_duration", mean=1.0769, sd=0.1404, voices=16),
    },
    142: {
        "relative_duration": Band(metric="relative_duration", mean=0.4299, sd=0.1799, voices=16),
    },
    143: {
        "relative_duration": Band(metric="relative_duration", mean=0.9801, sd=0.1762, voices=16),
    },
    144: {
        "relative_duration": Band(metric="relative_duration", mean=0.9644, sd=0.0820, voices=16),
    },
    145: {
        "relative_duration": Band(metric="relative_duration", mean=0.5958, sd=0.1017, voices=16),
    },
    146: {
        "relative_duration": Band(metric="relative_duration", mean=0.3708, sd=0.0875, voices=16),
    },
    147: {
        "relative_duration": Band(metric="relative_duration", mean=1.0094, sd=0.2104, voices=16),
    },
    148: {
        "relative_duration": Band(metric="relative_duration", mean=0.6470, sd=0.1347, voices=16),
    },
    149: {
        "relative_duration": Band(metric="relative_duration", mean=0.4990, sd=0.0616, voices=16),
    },
    150: {
        "relative_duration": Band(metric="relative_duration", mean=0.8059, sd=0.1184, voices=16),
    },
    151: {
        "relative_duration": Band(metric="relative_duration", mean=0.8308, sd=0.0896, voices=16),
    },
    152: {
        "relative_duration": Band(metric="relative_duration", mean=0.3861, sd=0.1270, voices=16),
    },
    153: {
        "relative_duration": Band(metric="relative_duration", mean=0.6856, sd=0.0967, voices=16),
    },
    154: {
        "relative_duration": Band(metric="relative_duration", mean=0.6638, sd=0.0803, voices=16),
    },
    155: {
        "relative_duration": Band(metric="relative_duration", mean=2.0641, sd=0.4602, voices=16),
    },
    156: {
        "relative_duration": Band(metric="relative_duration", mean=0.8519, sd=0.3016, voices=16),
    },
    157: {
        "relative_duration": Band(metric="relative_duration", mean=0.3075, sd=0.1225, voices=16),
    },
    158: {
        "relative_duration": Band(metric="relative_duration", mean=1.0658, sd=0.2415, voices=16),
    },
    159: {
        "relative_duration": Band(metric="relative_duration", mean=0.4473, sd=0.1435, voices=16),
    },
    160: {
        "relative_duration": Band(metric="relative_duration", mean=0.8424, sd=0.1169, voices=16),
    },
    161: {
        "relative_duration": Band(metric="relative_duration", mean=0.5267, sd=0.0851, voices=16),
    },
    162: {
        "relative_duration": Band(metric="relative_duration", mean=0.2903, sd=0.0883, voices=16),
    },
    163: {
        "relative_duration": Band(metric="relative_duration", mean=1.7534, sd=0.4449, voices=16),
    },
    164: {
        "relative_duration": Band(metric="relative_duration", mean=1.1459, sd=0.2415, voices=16),
    },
    165: {
        "relative_duration": Band(metric="relative_duration", mean=0.3722, sd=0.1849, voices=16),
    },
    166: {
        "relative_duration": Band(metric="relative_duration", mean=1.9451, sd=0.5176, voices=16),
    },
    167: {
        "relative_duration": Band(metric="relative_duration", mean=1.2130, sd=0.1965, voices=16),
    },
    168: {
        "relative_duration": Band(metric="relative_duration", mean=1.2048, sd=0.1998, voices=16),
    },
    169: {
        "relative_duration": Band(metric="relative_duration", mean=0.9474, sd=0.0916, voices=16),
    },
    170: {
        "relative_duration": Band(metric="relative_duration", mean=0.3109, sd=0.1789, voices=16),
    },
    171: {
        "relative_duration": Band(metric="relative_duration", mean=0.9848, sd=0.1776, voices=16),
    },
    172: {
        "relative_duration": Band(metric="relative_duration", mean=1.9547, sd=0.4302, voices=16),
    },
    173: {
        "relative_duration": Band(metric="relative_duration", mean=0.8516, sd=0.3230, voices=16),
    },
    174: {
        "relative_duration": Band(metric="relative_duration", mean=1.2581, sd=0.1248, voices=16),
    },
    175: {
        "relative_duration": Band(metric="relative_duration", mean=0.4787, sd=0.2675, voices=16),
    },
    176: {
        "relative_duration": Band(metric="relative_duration", mean=1.0313, sd=0.1621, voices=16),
    },
    177: {
        "relative_duration": Band(metric="relative_duration", mean=0.2992, sd=0.1703, voices=16),
    },
    178: {
        "relative_duration": Band(metric="relative_duration", mean=2.0100, sd=0.4758, voices=16),
    },
    179: {
        "relative_duration": Band(metric="relative_duration", mean=0.8653, sd=0.3671, voices=16),
    },
    180: {
        "relative_duration": Band(metric="relative_duration", mean=0.2728, sd=0.1461, voices=16),
    },
    181: {
        "relative_duration": Band(metric="relative_duration", mean=0.8420, sd=0.1637, voices=16),
    },
    182: {
        "relative_duration": Band(metric="relative_duration", mean=1.0100, sd=0.1745, voices=16),
    },
    183: {
        "relative_duration": Band(metric="relative_duration", mean=0.3067, sd=0.1902, voices=16),
    },
    184: {
        "relative_duration": Band(metric="relative_duration", mean=0.4942, sd=0.0850, voices=16),
    },
    185: {
        "relative_duration": Band(metric="relative_duration", mean=0.7183, sd=0.0712, voices=16),
    },
    186: {
        "relative_duration": Band(metric="relative_duration", mean=0.4089, sd=0.1046, voices=16),
    },
    187: {
        "relative_duration": Band(metric="relative_duration", mean=1.2058, sd=0.2365, voices=16),
    },
    188: {
        "relative_duration": Band(metric="relative_duration", mean=0.7310, sd=0.1621, voices=16),
    },
    189: {
        "relative_duration": Band(metric="relative_duration", mean=1.1088, sd=0.1594, voices=16),
    },
    190: {
        "relative_duration": Band(metric="relative_duration", mean=0.7563, sd=0.1379, voices=16),
    },
    191: {
        "relative_duration": Band(metric="relative_duration", mean=1.4760, sd=0.2147, voices=16),
    },
    192: {
        "relative_duration": Band(metric="relative_duration", mean=1.5606, sd=0.1219, voices=16),
    },
    193: {
        "relative_duration": Band(metric="relative_duration", mean=0.5146, sd=0.2889, voices=16),
    },
    194: {
        "relative_duration": Band(metric="relative_duration", mean=1.2915, sd=0.2216, voices=16),
    },
    195: {
        "relative_duration": Band(metric="relative_duration", mean=2.9768, sd=0.4006, voices=16),
    },
}


SENTENCE: Mapping[int, Mapping[str, Band]] = {
    0: {
        "pitch_range_st": Band(metric="pitch_range_st", mean=7.6649, sd=2.0564, voices=16),
        "terminal_slope_st": Band(metric="terminal_slope_st", mean=-4.2771, sd=4.2708, voices=16),
    },
    1: {
        "pitch_range_st": Band(metric="pitch_range_st", mean=10.9161, sd=3.5202, voices=16),
        "terminal_slope_st": Band(metric="terminal_slope_st", mean=-10.7145, sd=5.2740, voices=16),
    },
    2: {
        "npvi": Band(metric="npvi", mean=45.9028, sd=4.4626, voices=16),
        "pitch_range_st": Band(metric="pitch_range_st", mean=8.6594, sd=1.9520, voices=16),
        "terminal_slope_st": Band(metric="terminal_slope_st", mean=-4.4070, sd=9.0569, voices=16),
    },
    3: {
        "pitch_range_st": Band(metric="pitch_range_st", mean=8.9137, sd=2.8931, voices=16),
        "terminal_slope_st": Band(metric="terminal_slope_st", mean=-6.0379, sd=4.5731, voices=16),
    },
    4: {
        "npvi": Band(metric="npvi", mean=57.5292, sd=6.4600, voices=16),
        "pitch_range_st": Band(metric="pitch_range_st", mean=8.9390, sd=1.5057, voices=16),
        "terminal_slope_st": Band(metric="terminal_slope_st", mean=-5.8957, sd=3.9591, voices=16),
    },
    5: {
        "npvi": Band(metric="npvi", mean=57.5817, sd=4.2050, voices=16),
        "pitch_range_st": Band(metric="pitch_range_st", mean=6.9614, sd=1.3643, voices=16),
        "terminal_slope_st": Band(metric="terminal_slope_st", mean=-6.0329, sd=5.5711, voices=16),
    },
    6: {
        "npvi": Band(metric="npvi", mean=68.4819, sd=5.4672, voices=16),
        "pitch_range_st": Band(metric="pitch_range_st", mean=8.5994, sd=1.7748, voices=16),
        "terminal_slope_st": Band(metric="terminal_slope_st", mean=-8.2097, sd=4.5028, voices=16),
    },
    7: {
        "pitch_range_st": Band(metric="pitch_range_st", mean=12.5502, sd=4.0731, voices=16),
        "terminal_slope_st": Band(metric="terminal_slope_st", mean=-6.7084, sd=4.8800, voices=16),
    },
    8: {
        "pitch_range_st": Band(metric="pitch_range_st", mean=9.6197, sd=3.5622, voices=16),
        "terminal_slope_st": Band(metric="terminal_slope_st", mean=-7.0961, sd=4.1753, voices=16),
    },
    9: {
        "pitch_range_st": Band(metric="pitch_range_st", mean=9.2054, sd=2.5149, voices=16),
        "terminal_slope_st": Band(metric="terminal_slope_st", mean=-8.0997, sd=4.9816, voices=16),
    },
    10: {
        "pitch_range_st": Band(metric="pitch_range_st", mean=11.2216, sd=2.9391, voices=16),
        "terminal_slope_st": Band(metric="terminal_slope_st", mean=-9.1633, sd=4.9715, voices=16),
    },
    11: {
        "npvi": Band(metric="npvi", mean=61.6241, sd=4.1181, voices=16),
        "pitch_range_st": Band(metric="pitch_range_st", mean=8.0735, sd=1.7453, voices=16),
        "terminal_slope_st": Band(metric="terminal_slope_st", mean=-5.7886, sd=4.3494, voices=16),
    },
    12: {
        "pitch_range_st": Band(metric="pitch_range_st", mean=9.4240, sd=1.8974, voices=16),
        "terminal_slope_st": Band(metric="terminal_slope_st", mean=-6.4760, sd=4.5306, voices=16),
    },
    13: {
        "pitch_range_st": Band(metric="pitch_range_st", mean=7.9587, sd=1.6522, voices=16),
        "terminal_slope_st": Band(metric="terminal_slope_st", mean=-9.7099, sd=5.2401, voices=16),
    },
}


PARAGRAPH: Mapping[str, Band] = {
    "npvi": Band(metric="npvi", mean=58.2746, sd=2.1496, voices=16),
    "pitch_range_st": Band(metric="pitch_range_st", mean=8.6184, sd=1.7160, voices=16),
    "terminal_slope_st": Band(metric="terminal_slope_st", mean=-9.7099, sd=5.2401, voices=16),
}

# Each reference voice's own median pitch. The practice surface picks the voice nearest the
# speaker's own to play as the native leg — matching on what actually makes two voices
# comparable, and needing no live voice roster to do it.
MEDIAN_F0_HZ: Mapping[str, float] = {
    "en-US-AmberNeural": 223.9,
    "en-US-AndrewNeural": 105.5,
    "en-US-AriaNeural": 200.9,
    "en-US-AshleyNeural": 213.5,
    "en-US-AvaNeural": 196.8,
    "en-US-BrandonNeural": 145.3,
    "en-US-BrianNeural": 122.7,
    "en-US-ChristopherNeural": 108.6,
    "en-US-CoraNeural": 206.8,
    "en-US-DavisNeural": 122.1,
    "en-US-ElizabethNeural": 180.9,
    "en-US-EmmaNeural": 178.8,
    "en-US-EricNeural": 104.4,
    "en-US-GuyNeural": 149.6,
    "en-US-JennyNeural": 179.2,
    "en-US-TonyNeural": 115.5,
}
