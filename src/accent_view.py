"""Rendering for the accent measurement: the four-column table, and the vowel chart.

Same boundary as `progress_view.py` — pandas and altair, never Streamlit — so the frames and
the Markdown can be asserted in a test without driving a page.

**The table contract is the whole point of this module.** Every accent surface in this
project, here and in every later chunk, renders its findings as a Markdown table with exactly
these four column headers, in this order:

    | Acoustic Feature | User Realization | Target Realization | Delta / Adjustment Needed |

One renderer, so no surface can quietly grow a fifth column or reorder them, and one test
asserting the headers.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence

import altair as alt
import pandas as pd

import phoneme_reference
import vowel_measure
from vowel_measure import Finding, NoiseFloor, VowelPosition

logger = logging.getLogger(__name__)

# What a surface says about which reference it used. The two do NOT coincide — imitating the
# synthesised voice can move a token away from the published mean while sounding better — so
# naming one on the surface is not decoration.
PUBLISHED_CAPTION = (
    "Measured against **published General American means** (Hillenbrand et al. 1995, "
    "{set} set), Lobanov-normalised the same way as your own vowels. That is what the "
    "NUMBERS are measured against; the synthesised voice you practise with is what your EAR "
    "is trained on, and the two do not coincide."
)

EMPTY_TABLE = "_Nothing measurable in this recording._"


def to_markdown(findings: Sequence[Finding]) -> str:
    """The four-column table. The one renderer every accent surface goes through."""
    if not findings:
        return EMPTY_TABLE
    header = "| " + " | ".join(vowel_measure.COLUMNS) + " |"
    rule = "|" + "---|" * len(vowel_measure.COLUMNS)
    rows = [
        "| "
        + " | ".join(_escape(value) for value in (row.feature, row.user, row.target, row.delta))
        + " |"
        for row in findings
    ]
    return "\n".join([header, rule, *rows])


def _escape(value: str) -> str:
    """Pipes would split a cell, and newlines would end the table mid-row."""
    return value.replace("|", "\\|").replace("\n", " ")


def findings_frame(findings: Sequence[Finding]) -> pd.DataFrame:
    """The same rows as a frame, for anything that wants to filter or count them."""
    return pd.DataFrame(
        [
            dict(zip(vowel_measure.COLUMNS, (row.feature, row.user, row.target, row.delta)))
            for row in findings
        ],
        columns=list(vowel_measure.COLUMNS),
    )


# --- The vowel chart ---------------------------------------------------------------------


def vowel_frame(
    speaker: Mapping[str, VowelPosition],
    reference: Mapping[str, VowelPosition],
    *,
    reference_label: str = "General American (1995)",
    speaker_label: str = "You",
) -> pd.DataFrame:
    """Long-form frame for the vowel chart: one row per vowel per series.

    `n` travels on every speaker row. A point built from two tokens and one built from twenty
    must not look the same, and the chart encodes the count as size for exactly that reason.
    """
    rows: list[dict[str, object]] = []
    for series, found, is_speaker in (
        (speaker_label, speaker, True),
        (reference_label, reference, False),
    ):
        for vowel, position in found.items():
            if position.f1_z is None or position.f2_z is None:
                continue
            rows.append(
                {
                    "series": series,
                    "vowel": vowel,
                    "label": _label(vowel),
                    "f1_z": position.f1_z,
                    "f2_z": position.f2_z,
                    "n": position.n,
                    "speaker": is_speaker,
                }
            )
    return pd.DataFrame(rows, columns=["series", "vowel", "label", "f1_z", "f2_z", "n", "speaker"])


def _label(vowel: str) -> str:
    keyword = phoneme_reference.keyword_for(vowel)
    return f"{vowel} {keyword}" if keyword else vowel


def vowel_chart(frame: pd.DataFrame) -> alt.LayerChart | alt.FacetChart:
    """The vowel space, drawn the way a vowel chart is drawn.

    Both axes are **reversed**, which is not a style choice: a vowel chart is a schematic of
    the mouth, so high-F2 front vowels belong on the left and low-F1 close vowels on top. An
    unreversed scatter of the same numbers is upside down and back to front, and unreadable to
    anyone who has seen one before.

    Point size encodes the token count, so thin evidence looks thin.
    """
    base = alt.Chart(frame)
    points = base.mark_point(filled=True, opacity=0.85).encode(
        x=alt.X(
            "f2_z:Q",
            title="F2 (Lobanov z) — front ← → back",
            scale=alt.Scale(reverse=True, zero=False),
        ),
        y=alt.Y(
            "f1_z:Q",
            title="F1 (Lobanov z) — close ↑ ↓ open",
            scale=alt.Scale(reverse=True, zero=False),
        ),
        color=alt.Color("series:N", title=None),
        size=alt.Size("n:Q", title="tokens", scale=alt.Scale(range=[40, 400])),
        tooltip=[
            alt.Tooltip("label:N", title="vowel"),
            alt.Tooltip("series:N", title="series"),
            alt.Tooltip("f1_z:Q", title="F1 (z)", format=".2f"),
            alt.Tooltip("f2_z:Q", title="F2 (z)", format=".2f"),
            alt.Tooltip("n:Q", title="tokens"),
        ],
    )
    text = base.mark_text(dy=-12, fontSize=11).encode(
        x=alt.X("f2_z:Q", scale=alt.Scale(reverse=True, zero=False)),
        y=alt.Y("f1_z:Q", scale=alt.Scale(reverse=True, zero=False)),
        text="vowel:N",
        color=alt.Color("series:N", legend=None),
    )
    return alt.layer(points, text).properties(height=420)


# --- The noise floor, said in words -----------------------------------------------------------


def noise_caption(noise: NoiseFloor | None) -> str:
    """What the noise floor is, and what it forbids. Rendered wherever movement is shown."""
    if noise is None or noise.median_z is None:
        return (
            "**No noise floor yet.** Until the calibration passage has been read twice, there "
            "is no way to tell a real change from a different microphone position — so no "
            "movement on this page can honestly be called progress."
        )
    return (
        f"**Measurement noise floor: {noise.median_z:.2f} z** (median across "
        f"{noise.vowels} vowels, from two reads of the same passage in one sitting). A vowel "
        f"centroid moves this much between sessions from microphone placement, room, posture "
        f"and warm-up, with no learning at all. **Nothing smaller than this band is reported "
        f"as change** — including when it moves the flattering way."
    )


def movement_rows(
    before: Mapping[str, VowelPosition],
    after: Mapping[str, VowelPosition],
    noise: NoiseFloor | None,
) -> list[Finding]:
    """Vowel-by-vowel movement between two readings, with the noise band applied.

    Produces the same four-column shape as everything else, so the trend surface and the
    per-attempt surface cannot render their findings differently.
    """
    import math

    rows: list[Finding] = []
    for vowel in sorted(set(before) & set(after)):
        first, second = before[vowel], after[vowel]
        if None in (first.f1_z, first.f2_z, second.f1_z, second.f2_z):
            continue
        moved = math.hypot(
            float(second.f1_z) - float(first.f1_z),  # type: ignore[arg-type]
            float(second.f2_z) - float(first.f2_z),  # type: ignore[arg-type]
        )
        band = noise.band_for(vowel) if noise else None
        if noise is not None and noise.within_noise(vowel, moved):
            verdict = (
                f"{vowel_measure.WITHIN_NOISE} — moved {moved:.2f} z against a {band:.2f} z band."
                if band is not None
                else f"{vowel_measure.WITHIN_NOISE} — moved {moved:.2f} z."
            )
        else:
            verdict = (
                f"Moved {moved:.2f} z, past the {band:.2f} z noise band. Real movement."
                if band is not None
                else f"Moved {moved:.2f} z. No noise band measured, so this cannot be "
                f"called progress yet."
            )
        rows.append(
            Finding(
                feature=f"/{vowel}/ {phoneme_reference.keyword_for(vowel)} — position shift",
                user=f"F1 {second.f1_z:+.2f} z, F2 {second.f2_z:+.2f} z (n={second.n})",
                target=f"was F1 {first.f1_z:+.2f} z, F2 {first.f2_z:+.2f} z (n={first.n})",
                delta=verdict,
            )
        )
    return rows
