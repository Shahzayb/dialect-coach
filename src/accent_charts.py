"""The accent charts: one frame builder and one chart spec per instrument.

pandas and altair, **never Streamlit** — the same boundary `progress_view.py` and
`accent_view.py` sit on, so every frame and every chart spec is assertable in a test without
driving a page. `app.py` calls `st.altair_chart` and nothing here knows that.

**Every chart ships with its table.** The rows beside a chart come from
`vowel_measure.findings_by_instrument`, keyed by the same instrument name, so the picture and
the numbers are two renderings of one computation rather than two computations that might
disagree. The chart carries the shape; the table carries the numbers; the table is the half
that survives being pasted into a plan file or a commit message.

## The arrow is the deliverable

On the quadrant, a produced vowel and its target are two dots and the line between them is the
finding: its **direction** is the instruction and its **length** is the priority ranking. A
scatter of dots leaves the reader to work out which way to move and which one matters most,
which is the work this project exists to do for them.

An arrow shorter than the measurement noise floor **is not a finding**. The floor is drawn as
a faint ring around each produced point, in real data units, so "shorter than the ring" is
something the eye resolves without arithmetic.

## Why the two pitch contours are aligned on WORD OFFSETS and not with DTW

This is a decision, not an omission.

Dynamic time warping finds the alignment that minimises distance between two contours. Applied
here it would happily warp the time axis until a timing error disappeared — and timing error is
one of the things being measured. A tool that can hide the finding is not a neutral choice of
alignment; it is the wrong instrument.

Anchoring on Azure's word offsets and interpolating linearly between them is a piecewise warp
**constrained to linguistic units**. It can line "the same word" up with "the same word", which
is what makes two contours comparable at all, and it cannot line up two syllables that took
different amounts of time — so a word the speaker held twice as long stays visibly twice as
long. Both sides carry offsets from the same segmenter (see `native_model`), which is what
makes the anchors trustworthy.

Uniform whole-clip stretching is the other wrong answer, for the same reason in reverse: it
smears one global timing ratio across every word, so a single slow word makes every other word
look mistimed.

If a later chunk wants DTW it wants it for a different question — global similarity scoring —
and it should say so.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

import altair as alt
import pandas as pd

import phoneme_reference
import vowel_measure
from vowel_measure import NoiseFloor, Token, Trajectory, VowelPosition

# Kept in one place so the quadrant, the trajectories and the rings cannot drift apart on the
# thing that makes a vowel chart readable.
F2_TITLE = "F2 (Lobanov z) — front ← → back"
F1_TITLE = "F1 (Lobanov z) — close ↑ ↓ open"

WITHIN_NOISE = vowel_measure.WITHIN_NOISE

# What every accent surface says instead of a verdict. Native-likeness is a direction with a
# distance, not a checkbox: a surface that can only say "not yet native" is uninformative for
# the entire period during which it is true.
POST_HOC = (
    "**Post-hoc.** Drawn after the assessment returned, not live while you spoke — Streamlit "
    "re-runs the whole script on every interaction and there is no streaming audio path."
)


def _label(vowel: str) -> str:
    """Azure IPA plus the Wells keyword. IPA alone is unreadable at a glance."""
    keyword = phoneme_reference.keyword_for(vowel)
    return f"{vowel} {keyword}" if keyword else vowel


# --- The vowel quadrant ------------------------------------------------------------------------


def screen_angle(f1_delta: float, f2_delta: float) -> float:
    """Compass bearing, in degrees, for an arrow pointing along (f1_delta, f2_delta).

    **Both axes are reversed on a vowel chart**, and that has to be undone here or every arrow
    points the wrong way. A vowel chart is a schematic of the mouth: high-F2 front vowels sit
    on the LEFT and low-F1 close vowels on TOP. So a rising F2 moves an arrow left on screen,
    and a rising F1 moves it down.

    Vega's `angle` channel measures clockwise from straight up, which is what this returns —
    so a target that is purely fronter than the speaker comes back as 270°, pointing left.
    """
    screen_x = -f2_delta
    screen_y = f1_delta  # larger F1 is lower on screen, which is where "open" belongs
    return math.degrees(math.atan2(screen_x, -screen_y)) % 360.0


QUADRANT_COLUMNS: tuple[str, ...] = (
    "vowel",
    "label",
    "f1_z",
    "f2_z",
    "n",
    "target_f1_z",
    "target_f2_z",
    "arrow_z",
    "band_z",
    "angle",
    "real",
    "has_target",
    "verdict",
)


def quadrant_frame(
    speaker: Mapping[str, VowelPosition],
    reference: Mapping[str, VowelPosition],
    noise: NoiseFloor | None = None,
) -> pd.DataFrame:
    """One row per produced vowel, carrying its target and the arrow between them.

    A vowel with no published target keeps its row — its position is recorded even when it
    cannot be scored — with `has_target` False, so the chart plots the point and draws no
    arrow rather than dropping the vowel and silently under-reporting the inventory.
    """
    rows: list[dict[str, object]] = []
    for vowel, position in sorted(speaker.items()):
        if position.f1_z is None or position.f2_z is None:
            continue
        target = reference.get(vowel)
        band = (noise.band_for(vowel) if noise else None) or 0.0
        row: dict[str, object] = {
            "vowel": vowel,
            "label": _label(vowel),
            "f1_z": position.f1_z,
            "f2_z": position.f2_z,
            "n": position.n,
            "target_f1_z": None,
            "target_f2_z": None,
            "arrow_z": None,
            "band_z": band,
            "angle": 0.0,
            "real": False,
            "has_target": False,
            "verdict": "No General American reference for this vowel — position recorded, "
            "not scored.",
        }
        if target is not None and target.f1_z is not None and target.f2_z is not None:
            f1_delta = target.f1_z - position.f1_z
            f2_delta = target.f2_z - position.f2_z
            arrow = math.hypot(f1_delta, f2_delta)
            real = arrow > band
            row.update(
                {
                    "target_f1_z": target.f1_z,
                    "target_f2_z": target.f2_z,
                    "arrow_z": arrow,
                    "angle": screen_angle(f1_delta, f2_delta),
                    "real": real,
                    "has_target": True,
                    "verdict": (
                        f"{arrow:.2f} z from the target, past the {band:.2f} z noise band."
                        if real
                        else f"{WITHIN_NOISE} — {arrow:.2f} z against a {band:.2f} z band."
                    ),
                }
            )
        rows.append(row)
    return pd.DataFrame(rows, columns=list(QUADRANT_COLUMNS))


def noise_ring_frame(frame: pd.DataFrame, *, points: int = 48) -> pd.DataFrame:
    """The noise floor as an actual circle per vowel, in data units.

    Drawn as a polygon rather than encoded as a mark size, and the difference matters: `size`
    is measured in screen area, so a resized chart would silently rescale the band relative to
    the arrows it is supposed to be compared against. A ring in z-units stays the same size
    relative to the thing it is judging, which is the only way "shorter than the ring" is a
    reading anyone can trust.
    """
    rows: list[dict[str, object]] = []
    for _, row in frame.iterrows():
        band = float(row["band_z"] or 0.0)
        if band <= 0:
            continue
        for step in range(points + 1):
            theta = 2 * math.pi * step / points
            rows.append(
                {
                    "vowel": row["vowel"],
                    "order": step,
                    "f1_z": float(row["f1_z"]) + band * math.sin(theta),
                    "f2_z": float(row["f2_z"]) + band * math.cos(theta),
                }
            )
    return pd.DataFrame(rows, columns=["vowel", "order", "f1_z", "f2_z"])


def _x(field: str = "f2_z:Q", title: str | None = F2_TITLE) -> alt.X:
    return alt.X(field, title=title, scale=alt.Scale(reverse=True, zero=False))


def _y(field: str = "f1_z:Q", title: str | None = F1_TITLE) -> alt.Y:
    return alt.Y(field, title=title, scale=alt.Scale(reverse=True, zero=False))


def quadrant_chart(
    frame: pd.DataFrame, rings: pd.DataFrame | None = None
) -> alt.LayerChart | alt.FacetChart:
    """The vowel space, with an arrow per vowel from produced toward target.

    Both axes reversed, because a vowel chart is a schematic of the mouth and an unreversed
    scatter of the same numbers is upside down and back to front. Point size encodes the token
    count, so thin evidence looks thin.
    """
    base = alt.Chart(frame)
    layers: list[alt.Chart] = []

    if rings is not None and not rings.empty:
        layers.append(
            alt.Chart(rings)
            .mark_line(strokeWidth=1, opacity=0.30, strokeDash=[2, 2], color="#888")
            .encode(x=_x(), y=_y(), order="order:Q", detail="vowel:N")
        )

    # The arrow's shaft. Only where the gap clears the noise band: drawing a confident stroke
    # for movement that is not real is exactly what the band exists to prevent.
    real = frame[frame["real"]] if "real" in frame else frame.iloc[0:0]
    if not real.empty:
        layers.append(
            alt.Chart(real)
            .mark_rule(strokeWidth=1.5, opacity=0.65, color="#d1495b")
            .encode(x=_x(), y=_y(), x2="target_f2_z:Q", y2="target_f1_z:Q")
        )
        layers.append(
            alt.Chart(real)
            .mark_point(shape="triangle", filled=True, size=70, opacity=0.9, color="#d1495b")
            .encode(
                x=_x("target_f2_z:Q", None),
                y=_y("target_f1_z:Q", None),
                angle=alt.Angle(
                    "angle:Q", scale=alt.Scale(domain=[0, 360], range=[0, 360]), legend=None
                ),
            )
        )

    layers.append(
        base.mark_point(filled=True, opacity=0.85, color="#1f77b4").encode(
            x=_x(),
            y=_y(),
            size=alt.Size("n:Q", title="tokens", scale=alt.Scale(range=[40, 400])),
            tooltip=[
                alt.Tooltip("label:N", title="vowel"),
                alt.Tooltip("n:Q", title="tokens"),
                alt.Tooltip("f1_z:Q", title="F1 (z)", format="+.2f"),
                alt.Tooltip("f2_z:Q", title="F2 (z)", format="+.2f"),
                alt.Tooltip("verdict:N", title="gap"),
            ],
        )
    )
    layers.append(base.mark_text(dy=-14, fontSize=11).encode(x=_x(), y=_y(), text="vowel:N"))
    return alt.layer(*layers).properties(height=460)


# --- Diphthong trajectories ----------------------------------------------------------------------

TRAJECTORY_COLUMNS: tuple[str, ...] = (
    "series",
    "vowel",
    "label",
    "kind",
    "order",
    "f1_z",
    "f2_z",
    "length_z",
    "travel_hz",
    "n",
)


def trajectory_frame(
    speaker: Mapping[str, Trajectory],
    reference: Mapping[str, Trajectory] | None = None,
    *,
    speaker_label: str = "You",
    reference_label: str = "General American",
    diphthongs_only: bool = False,
) -> pd.DataFrame:
    """Two rows per vowel — the 20% point and the 80% point — so each renders as a stroke.

    **This is the chart the whole feature is judged on.** A monophthongised FACE vowel renders
    as a dot where a native rendering renders as a stroke, and no table of signed hertz makes
    that difference as immediate.

    Monophthongs are included by default. A chart showing only the diphthongs cannot show that
    the diphthongs are the ones that move; seeing /i/ sit still beside a flattened /eɪ/ is what
    makes the flattening legible rather than merely stated.
    """
    rows: list[dict[str, object]] = []
    for series, found in ((speaker_label, speaker), (reference_label, reference or {})):
        for vowel, trajectory in sorted(found.items()):
            entry = phoneme_reference.lookup(vowel)
            kind = entry.kind if entry is not None else ""
            if diphthongs_only and kind != "diphthong":
                continue
            if None in (
                trajectory.start_f1_z,
                trajectory.start_f2_z,
                trajectory.end_f1_z,
                trajectory.end_f2_z,
            ):
                continue
            for order, (f1_z, f2_z) in enumerate(
                (
                    (trajectory.start_f1_z, trajectory.start_f2_z),
                    (trajectory.end_f1_z, trajectory.end_f2_z),
                )
            ):
                rows.append(
                    {
                        "series": series,
                        "vowel": vowel,
                        "label": _label(vowel),
                        "kind": kind,
                        "order": order,
                        "f1_z": f1_z,
                        "f2_z": f2_z,
                        "length_z": trajectory.length_z,
                        "travel_hz": trajectory.travel_hz,
                        "n": trajectory.n,
                    }
                )
    return pd.DataFrame(rows, columns=list(TRAJECTORY_COLUMNS))


def trajectory_chart(frame: pd.DataFrame) -> alt.LayerChart | alt.FacetChart:
    """Each vowel as the gesture it is: a stroke from 20% of its duration to 80%.

    The stroke is what carries the finding, so it is drawn thick and the start point is marked
    — without a start marker a stroke has no direction, and /eɪ/ and a reversed /eɪ/ would
    render identically.
    """
    base = alt.Chart(frame)
    strokes = base.mark_line(strokeWidth=2.5, opacity=0.85).encode(
        x=_x(),
        y=_y(),
        order="order:Q",
        detail="vowel:N",
        color=alt.Color("series:N", title=None),
        strokeDash=alt.StrokeDash("series:N", title=None),
        tooltip=[
            alt.Tooltip("label:N", title="vowel"),
            alt.Tooltip("series:N", title="series"),
            alt.Tooltip("length_z:Q", title="stroke (z)", format=".2f"),
            alt.Tooltip("travel_hz:Q", title="F2 travel (Hz)", format="+.0f"),
            alt.Tooltip("n:Q", title="tokens"),
        ],
    )
    starts = (
        base.transform_filter(alt.datum.order == 0)
        .mark_point(filled=True, size=45, opacity=0.9)
        .encode(x=_x(), y=_y(), color=alt.Color("series:N", title=None))
    )
    labels = (
        base.transform_filter(alt.datum.order == 0)
        .mark_text(dy=-13, fontSize=11)
        .encode(x=_x(), y=_y(), text="vowel:N", color=alt.Color("series:N", title=None))
    )
    return alt.layer(strokes, starts, labels).properties(height=460)


# --- Rhoticity ------------------------------------------------------------------------------------

RHOTICITY_COLUMNS: tuple[str, ...] = (
    "vowel",
    "label",
    "word",
    "f3_hz",
    "f3_minus_f2_hz",
    "target_hz",
    "band_hz",
)


def is_rhotic(vowel: str) -> bool:
    """The r-coloured vowels, by the same test `vowel_measure` uses."""
    entry = phoneme_reference.lookup(vowel)
    return vowel in {"ɝ", "ɚ"} or (entry is not None and entry.kind == "r-coloured")


def rhoticity_frame(
    tokens: Sequence[Token],
    reference: Mapping[str, VowelPosition],
    bands: Mapping[str, float] | None = None,
) -> pd.DataFrame:
    """One row per r-coloured TOKEN, not per category.

    A strip plot rather than a mean, because r-colouring is the marker most likely to be
    produced inconsistently — arriving on the stressed NURSE and vanishing on the unstressed
    lettER — and a category mean is precisely the statistic that hides that. Twelve tokens
    scattered across the reference line say something a single averaged dot cannot.

    This chart goes ABOVE the quadrant. For a General American target it is routinely the
    largest single gap on the page, and it is the most correctable.
    """
    nurse = reference.get("ɝ")
    rows: list[dict[str, object]] = []
    for token in tokens:
        if not token.accepted or not is_rhotic(token.vowel):
            continue
        if token.f3_minus_f2 is None:
            continue
        target = reference.get(token.vowel)
        target_value = target.f3_minus_f2_hz if target is not None else None
        if target_value is None and nurse is not None:
            # /ɝ/ stands in for the categories with no mean of their own. Defensible because
            # r-colouring is one articulatory gesture whatever vowel carries it — and said out
            # loud on the surface rather than implied.
            target_value = nurse.f3_minus_f2_hz
        rows.append(
            {
                "vowel": token.vowel,
                "label": _label(token.vowel),
                "word": token.word,
                "f3_hz": token.at50.f3,
                "f3_minus_f2_hz": token.f3_minus_f2,
                "target_hz": target_value,
                "band_hz": (bands or {}).get(token.vowel),
            }
        )
    return pd.DataFrame(rows, columns=list(RHOTICITY_COLUMNS))


def rhoticity_chart(frame: pd.DataFrame) -> alt.LayerChart | alt.FacetChart:
    """Every r-coloured token against its reference, with the target marked per vowel.

    **Lower is more r-coloured.** F3 dropping toward F2 IS the acoustic signature of American
    /ɹ/, so the axis is labelled with that rather than leaving the reader to infer a direction
    from a number.
    """
    base = alt.Chart(frame)
    ticks = base.mark_tick(thickness=2, size=26, opacity=0.75).encode(
        x=alt.X(
            "f3_minus_f2_hz:Q",
            title="F3 − F2 (Hz) — more r-coloured ← → less r-coloured",
            scale=alt.Scale(zero=False),
        ),
        y=alt.Y("label:N", title=None, sort="-x"),
        tooltip=[
            alt.Tooltip("word:N", title="word"),
            alt.Tooltip("label:N", title="vowel"),
            alt.Tooltip("f3_minus_f2_hz:Q", title="F3 − F2 (Hz)", format=".0f"),
            alt.Tooltip("f3_hz:Q", title="F3 (Hz)", format=".0f"),
            alt.Tooltip("target_hz:Q", title="target (Hz)", format=".0f"),
        ],
    )
    targets = base.mark_point(shape="diamond", filled=True, size=110, color="#d1495b").encode(
        x=alt.X("target_hz:Q", scale=alt.Scale(zero=False)),
        y=alt.Y("label:N", sort="-x"),
    )
    return alt.layer(ticks, targets).properties(height=alt.Step(30))


# --- Pitch: two contours on one time axis ---------------------------------------------------------


@dataclass(frozen=True)
class Anchor:
    """One word, found in both readings, with where it starts in each."""

    word: str
    user_s: float
    model_s: float


def word_anchors(
    user_words: Sequence[Mapping[str, object]], model_words: Sequence[Mapping[str, object]]
) -> list[Anchor]:
    """Match the two readings word by word, in order, keeping only words both actually spoke.

    A plain ordered walk rather than a similarity search: both sides are readings of the SAME
    reference text, so the n-th spoken word is the n-th spoken word. Words that one side
    omitted carry no timing (`speech_analyzer.NO_TIMING`) and drop out on their own, which is
    the correct behaviour — an omitted word cannot anchor anything.
    """

    def spoken(words: Sequence[Mapping[str, object]]) -> list[tuple[str, float]]:
        found: list[tuple[str, float]] = []
        for word in words:
            start = word.get("start_s")
            text = str(word.get("word") or "").strip().lower()
            if isinstance(start, (int, float)) and text:
                found.append((text, float(start)))
        return found

    anchors: list[Anchor] = []
    for (user_text, user_s), (model_text, model_s) in zip(spoken(user_words), spoken(model_words)):
        if user_text != model_text:
            # The readings have diverged — a substitution or an insertion. Stop rather than
            # anchoring the rest of the sentence onto the wrong words, which would misalign
            # everything after it while still looking like an alignment.
            break
        anchors.append(Anchor(word=user_text, user_s=user_s, model_s=model_s))
    return anchors


def to_user_clock(model_time_s: float, anchors: Sequence[Anchor]) -> float | None:
    """Map a model timestamp onto the user's clock, piecewise-linearly between word starts.

    **This is the alignment decision, in eight lines.** Between two anchors the map is a
    straight line, so a word the user held twice as long stays twice as long — the warp is
    constrained to linguistic units and cannot absorb a timing error. See the module docstring
    for why DTW and uniform stretching are both the wrong instrument here.
    """
    if len(anchors) < 2:
        return None
    if model_time_s <= anchors[0].model_s:
        return anchors[0].user_s + (model_time_s - anchors[0].model_s)
    if model_time_s >= anchors[-1].model_s:
        return anchors[-1].user_s + (model_time_s - anchors[-1].model_s)
    for first, second in pairwise(anchors):
        if first.model_s <= model_time_s <= second.model_s:
            span = second.model_s - first.model_s
            if span <= 0:
                return first.user_s
            share = (model_time_s - first.model_s) / span
            return first.user_s + share * (second.user_s - first.user_s)
    return None


PITCH_COLUMNS: tuple[str, ...] = ("series", "time_s", "semitones", "hz")


def pitch_frame(
    user_track: Sequence[tuple[float, float]],
    model_tracks: Sequence[Sequence[tuple[float, float]]],
    anchors: Sequence[Anchor],
    *,
    user_label: str = "You",
    model_label: str = "General American model",
) -> pd.DataFrame:
    """Both contours on the USER's time axis, in semitones relative to each speaker's median.

    **Semitones, never hertz.** A low voice and a synthetic voice overlaid in hertz show the
    trivial fact that two people have different larynxes and hide the contour entirely. Each
    track is re-expressed against its OWN median, so what is left on the chart is the shape —
    which is the thing that can be imitated.

    Several model voices are averaged at each instant rather than one being picked, so the
    reference line is a General American tendency instead of one synthesiser's habit.
    """
    rows: list[dict[str, object]] = []
    if user_track:
        user_median = float(pd.Series([hz for _, hz in user_track]).median())
        rows += [
            {
                "series": user_label,
                "time_s": time_s,
                "semitones": _semitones(hz, user_median),
                "hz": hz,
            }
            for time_s, hz in user_track
        ]

    for track in model_tracks:
        if not track:
            continue
        model_median = float(pd.Series([hz for _, hz in track]).median())
        for model_time, hz in track:
            mapped = to_user_clock(model_time, anchors)
            if mapped is None:
                continue
            rows.append(
                {
                    "series": model_label,
                    "time_s": mapped,
                    "semitones": _semitones(hz, model_median),
                    "hz": hz,
                }
            )
    return pd.DataFrame(rows, columns=list(PITCH_COLUMNS))


def _semitones(hz: float, reference_hz: float) -> float:
    if hz <= 0 or reference_hz <= 0:
        return 0.0
    return 12.0 * math.log2(hz / reference_hz)


def word_boundary_frame(anchors: Sequence[Anchor]) -> pd.DataFrame:
    """Where each word starts on the user's clock. What makes the overlay readable at all."""
    return pd.DataFrame(
        [{"word": anchor.word, "time_s": anchor.user_s} for anchor in anchors],
        columns=["word", "time_s"],
    )


def pitch_chart(
    frame: pd.DataFrame, boundaries: pd.DataFrame | None = None
) -> alt.LayerChart | alt.FacetChart:
    """Two contours, one time axis, word boundaries marked.

    The model line is averaged across voices at each instant, so it reads as a tendency rather
    than as one voice to copy exactly. Zero on the y-axis is each speaker's own median, which
    is why the two are comparable at all.
    """
    base = alt.Chart(frame)
    layers: list[alt.Chart] = []
    if boundaries is not None and not boundaries.empty:
        layers.append(
            alt.Chart(boundaries)
            .mark_rule(strokeDash=[2, 3], opacity=0.35, color="#999")
            .encode(x=alt.X("time_s:Q", title="seconds"))
        )
        layers.append(
            alt.Chart(boundaries)
            .mark_text(angle=270, dy=-4, fontSize=9, align="left", baseline="middle", opacity=0.7)
            .encode(x="time_s:Q", y=alt.value(6), text="word:N")
        )
    layers.append(
        base.mark_line(strokeWidth=2, opacity=0.85, interpolate="monotone").encode(
            x=alt.X("time_s:Q", title="seconds"),
            y=alt.Y(
                "median(semitones):Q",
                title="semitones from each speaker's own median",
                scale=alt.Scale(zero=False),
            ),
            color=alt.Color("series:N", title=None),
            tooltip=[
                alt.Tooltip("series:N", title="series"),
                alt.Tooltip("time_s:Q", title="seconds", format=".2f"),
                alt.Tooltip("median(semitones):Q", title="semitones", format="+.1f"),
            ],
        )
    )
    return alt.layer(*layers).properties(height=280)


def pitch_range_semitones(track: Sequence[tuple[float, float]]) -> float | None:
    """The 10th-to-90th percentile spread, in semitones. Robust to one creaky frame."""
    if len(track) < 5:
        return None
    values = pd.Series([hz for _, hz in track])
    median = float(values.median())
    low, high = values.quantile(0.10), values.quantile(0.90)
    return _semitones(float(high), median) - _semitones(float(low), median)


def terminal_slope_semitones(
    track: Sequence[tuple[float, float]], *, window_s: float = 0.35
) -> float | None:
    """How far the pitch moves across the last `window_s` of voicing.

    Reported because a falling terminal is what marks a statement in General American, and a
    reader who keeps a level or rising terminal sounds permanently uncertain — an impression
    no segmental score ever picks up.
    """
    if len(track) < 5:
        return None
    end = track[-1][0]
    tail = [(time_s, hz) for time_s, hz in track if time_s >= end - window_s]
    if len(tail) < 3:
        return None
    return _semitones(tail[-1][1], tail[0][1])


# --- Duration -----------------------------------------------------------------------------------

DURATION_COLUMNS: tuple[str, ...] = ("vowel", "label", "series", "duration_ms", "n")


def duration_frame(
    speaker: Mapping[str, VowelPosition],
    published: Mapping[str, VowelPosition],
    measured: Mapping[str, VowelPosition],
    *,
    speaker_label: str = "You",
    published_label: str = "Hillenbrand 1995 (citation form)",
    measured_label: str = "Model voices (connected speech)",
) -> pd.DataFrame:
    """Paired bars per vowel: your mean, the published mean, and the measured-model mean.

    **Three bars, not two, and the third is what makes the other two interpretable.**
    Hillenbrand's durations are citation-form /hVd/ words read in isolation — /i/ averages
    244 ms there — so a learner's connected-speech vowel is bound to look "too short" against
    it, by an amount that is the reference's artefact and not their accent. The model bar is
    the same passage in connected speech through the identical pipeline, and it is the one a
    difference can honestly be read from.
    """
    rows: list[dict[str, object]] = []
    for series, found in (
        (speaker_label, speaker),
        (published_label, published),
        (measured_label, measured),
    ):
        for vowel, position in sorted(found.items()):
            if position.duration_ms is None:
                continue
            rows.append(
                {
                    "vowel": vowel,
                    "label": _label(vowel),
                    "series": series,
                    "duration_ms": position.duration_ms,
                    "n": position.n,
                }
            )
    frame = pd.DataFrame(rows, columns=list(DURATION_COLUMNS))
    if frame.empty:
        return frame
    # Only vowels the speaker actually produced. The reference tables cover more, and a bar
    # chart of targets with no measurement beside them is a chart of the reference.
    return frame[frame["vowel"].isin(set(speaker))].reset_index(drop=True)


def duration_chart(frame: pd.DataFrame) -> Any:
    """Grouped bars, one group per vowel."""
    return (
        alt.Chart(frame)
        .mark_bar()
        .encode(
            x=alt.X("label:N", title=None, sort="-y"),
            y=alt.Y("duration_ms:Q", title="mean duration (ms)"),
            xOffset="series:N",
            color=alt.Color("series:N", title=None),
            tooltip=[
                alt.Tooltip("label:N", title="vowel"),
                alt.Tooltip("series:N", title="series"),
                alt.Tooltip("duration_ms:Q", title="ms", format=".0f"),
                alt.Tooltip("n:Q", title="tokens"),
            ],
        )
        .properties(height=300)
    )


# --- Rhythm --------------------------------------------------------------------------------------

RHYTHM_COLUMNS: tuple[str, ...] = ("index", "duration_ms", "run")


def rhythm_frame(runs: Sequence[Sequence[float]]) -> pd.DataFrame:
    """One bar per vocalic interval, in the order they were spoken.

    Takes `rhythm.vocalic_intervals`' runs directly, keeping the run boundaries: nPVI is
    computed within uninterrupted stretches of speech and never across a pause, so a chart that
    ran the bars together would imply a comparison the number itself refuses to make.
    """
    rows: list[dict[str, object]] = []
    position = 0
    for run_index, run in enumerate(runs):
        for duration in run:
            rows.append({"index": position, "duration_ms": duration, "run": run_index})
            position += 1
    return pd.DataFrame(rows, columns=list(RHYTHM_COLUMNS))


def rhythm_chart(frame: pd.DataFrame) -> Any:
    """The shape of the reading's rhythm: alternating long and short, or flat.

    **The picture is the point, not the index.** A syllable-timed rhythm carried into English
    renders as a row of near-equal bars; a stress-timed one renders as a jagged alternation.
    The nPVI figure summarises exactly that into one number, and the number is what the table
    beside this chart carries.
    """
    return (
        alt.Chart(frame)
        .mark_bar()
        .encode(
            x=alt.X("index:O", title="vocalic interval, in order spoken", axis=None),
            y=alt.Y("duration_ms:Q", title="vowel duration (ms)"),
            color=alt.Color("run:N", title="unbroken stretch"),
            tooltip=[
                alt.Tooltip("duration_ms:Q", title="ms", format=".0f"),
                alt.Tooltip("run:N", title="stretch"),
            ],
        )
        .properties(height=220)
    )
