"""The progress view: the first thing in this project that reads the stored history back.

Every attempt has been in SQLite since the first chunk — `azure_raw_json` verbatim, the
normalised scores beside it — and nothing has ever shown it to the user. This module turns
those rows into the frames and chart specs `app.py` renders.

**The benchmark passage is what makes the chart mean anything.** Plotting scores across
arbitrary self-chosen texts measures text difficulty, not the speaker: an easy paragraph
scores higher and reads as progress. So one passage is fixed here, read on a schedule and
scored identically every time, and that series is the headline; free-practice attempts sit
behind it as an unconnected cloud for context only.

Like the pure render helpers in `app.py`, this module never imports Streamlit — the frames
and the chart specs are testable directly rather than through a headless app run. `app.py`
owns the `st.altair_chart` call and the caching.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

import altair as alt
import pandas as pd

import rhythm
import speech_analyzer
import utils
from utils import Mode

logger = logging.getLogger(__name__)


# --- The benchmark passage ------------------------------------------------------------------
# Chosen ONCE, for two consumers: this chart, and the vowel-measurement calibration read a
# later chunk needs. One 60-90 second read serves both, which is why it carries every
# commonly substituted consonant AND the full en-US vowel inventory in stressed, unreduced
# contexts. `BENCHMARK_COVERAGE` below is that claim as data, and a test asserts every token
# listed there really appears in the text, so the justification cannot drift from the passage.
#
# Constraints inherited from the codebase rather than invented:
#   - No digits. Azure normalises "33" and "thirty-three" differently and that breaks word
#     alignment (see PRESETS in app.py).
#   - Commas and periods only — no dashes, colons or hyphens. Mode B's local miscue diff
#     re-tokenises the reference; the punctuation and hyphen indexing bugs there are fixed,
#     but a text that will be read a hundred times is no place to re-enter that territory.
#   - Real punctuation, real prose. Prosody is one of the four charted metrics and is scored
#     on connected speech, so this stays a paragraph and not a word list.
#
# FROZEN. The benchmark series is identified by matching `reference_text`, so editing a word
# starts a new series. That is what BENCHMARK_VERSION records.
BENCHMARK_VERSION = 1

BENCHMARK_TITLE = "Benchmark — the same words each morning"

BENCHMARK_PASSAGE = (
    "Each morning I read these same words out loud, the way I said them last week. "
    "Nothing here is clever. The whole value is that the passage never changes, so "
    "whatever moves is my own voice, not the writing.\n\n"
    "Three things go through my mind while I read. The first is breath, where a short "
    "pause helps the listener, and where I join two thoughts that should have stayed "
    "apart. The second is the end of every word, the hard sounds I let go soft when I am "
    "tired, in asked and helped, in world, month and next. The third is the choice I make "
    "on each vowel, whether to hold it full and clear or to let it slide.\n\n"
    "A few of them still catch me. Brother and breathe. Believe and above. School, "
    "careful and cold. During a long, honest answer the joy goes out of it, I am not sure "
    "of my own voice, and I judge it more than I should.\n\n"
    "So I stop, sit up, take a fair pace, and finish the thought I began. In a good year "
    "I would like to measure how far this went, without the usual excuses."
)

# What the passage is for, as data. Keys are Azure's own IPA symbols (rhotic, no length
# marks — the same spellings `phoneme_reference` keys on), plus two named positional groups
# that are not single symbols. Values are the passage words carrying that target.
#
# Two things designed in rather than fallen into:
#   - /t/ and /d/ sit where General American does NOT flap them: word-initial, after /s/,
#     and word-final or in a cluster. No "better", "water" or "city". `phoneme_reference`
#     maps ɾ → t, so a flapped token is scored as /t/ and says nothing about the
#     dental-versus-alveolar contrast this passage exists to measure.
#   - The passage carries its own θ/ð minimal pair — "breath" and "breathe", one sentence
#     apart — and neighbours for /s/~/ʃ/ and /v/~/w/.
BENCHMARK_COVERAGE: Mapping[str, tuple[str, ...]] = {
    # --- The consonants this project was built to catch ---------------------------------
    "θ": ("three", "things", "through", "thoughts", "third", "month", "breath", "thought"),
    "ð": ("these", "them", "that", "whether", "brother", "breathe"),
    "v": ("value", "voice", "never", "every", "vowel", "believe", "above", "moves"),
    "w": ("words", "word", "way", "week", "whatever", "where", "while", "when", "world",
          "would"),
    "t": ("two", "tired", "take", "still", "sit", "stop", "next", "apart", "last", "first"),
    "d": ("said", "read", "world", "cold", "would", "third", "during", "end", "mind",
          "hard", "hold", "slide", "loud"),
    "l (dark, coda)": ("whole", "value", "helps", "world", "vowel", "full", "still",
                       "school", "careful", "cold", "while"),
    "l (clear, onset)": ("loud", "listener", "let", "like", "long", "last"),
    "ʃ": ("short", "should", "sure", "finish"),
    "s": ("same", "said", "so", "sit", "sounds", "still", "second", "slide", "school",
          "stayed", "pace", "voice", "soft"),
    "z": ("these", "changes", "words", "moves", "sounds", "pause", "things", "goes",
          "excuses"),
    "dʒ": ("join", "joy", "judge"),
    "final clusters": ("asked", "helped", "next", "world", "month", "first", "words",
                       "sounds", "thoughts", "helps", "cold", "hold", "mind", "end"),

    # --- The full en-US vowel inventory, for the calibration read ------------------------
    "æ": ("passage", "asked", "last", "answer", "catch"),
    "ɛ": ("breath", "end", "every", "let", "helps", "helped", "second", "next", "said",
          "went"),
    "ɪ": ("this", "things", "still", "sit", "finish", "listener"),
    "i": ("these", "read", "week", "breathe", "believe", "three", "each", "me"),
    "ɑ": ("not", "honest", "stop"),
    "ʌ": ("nothing", "up", "month", "judge", "above"),
    "ɝ": ("words", "word", "world", "first", "third"),
    "ʊ": ("should", "would", "full", "good"),
    "u": ("two", "through", "school", "moves", "usual", "excuses"),
    "ɔ": ("thoughts", "thought", "soft", "long", "pause"),
    "ə": ("passage", "listener", "second", "answer", "apart", "above", "began", "usual"),
    "ɚ": ("listener", "never", "clever", "whatever", "brother", "whether", "answer"),
    "eɪ": ("same", "way", "stayed", "make", "take", "pace", "began"),
    "aɪ": ("my", "while", "mind", "tired", "slide", "like", "writing"),
    "oʊ": ("whole", "so", "own", "go", "goes", "hold", "cold"),
    "aʊ": ("out", "loud", "sounds", "how"),
    "ɔɪ": ("voice", "join", "joy", "choice"),
    "ɑɹ": ("hard", "apart", "far"),
    "ɔɹ": ("morning", "short", "more"),
    "ɛɹ": ("where", "careful", "fair"),
    "ɪɹ": ("here", "clear", "year"),
    # The one inventory member the passage cannot guarantee: CURE is the rarest en-US vowel
    # and is actively merging into ɔɹ ("sure" is commonly /ʃɔɹ/). "during" holds it better.
    # A measurement consumer should treat ʊɹ as best-effort, not as covered.
    "ʊɹ": ("during", "sure"),
}


def benchmark_key(text: str | None) -> str:
    """The normalised identity of a reference text.

    Reuses `utils.normalise_words` — the same tokeniser the miscue diff runs on — so casing,
    whitespace and punctuation differences never split the benchmark into two series.
    """
    return " ".join(utils.normalise_words(text or ""))


_BENCHMARK_KEY = benchmark_key(BENCHMARK_PASSAGE)


def is_benchmark(text: str | None) -> bool:
    """Whether a stored `reference_text` is the benchmark passage.

    Identity comes from the text itself rather than a column: `db._migrate` has no upgrade
    path, and the schema-v1 precedent (coaching columns created NULL so the coaching chunk
    was an UPDATE, not a migration) says not to add one. Matching the text also works
    retroactively on rows already stored.
    """
    return bool(text) and benchmark_key(text) == _BENCHMARK_KEY


def spoken_attempts(rows: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Every stored attempt that is somebody actually speaking.

    Filters out the TTS rhythm baseline capture. That row is a real, really-billed assessment
    and has to stay in the table or the usage meter under-reports what was spent — but it is a
    synthesiser reading the benchmark passage, and none of this view's questions have an
    honest answer for it. Left in, it would put a point nobody spoke on the trajectory and let
    the voice's own weak sounds into "what keeps going wrong".

    Applied at every entry point rather than inside one chart, so the trajectory, the rankings
    and the last-read date cannot disagree about which attempts exist.
    """
    return [row for row in rows if not rhythm.is_baseline_capture(row["reference_text"])]


# --- Series and metrics ---------------------------------------------------------------------
# Mode A and Mode B scores are NOT comparable and must never share a line. Mode B's overall
# scores come from a duration-weighted merge across utterances (`speech_analyzer._merge_overall`)
# which approximates an unpublished Azure composite; Mode A's are Azure's own single-shot
# numbers. The separation is enforced structurally rather than by convention:
#   - only the benchmark subset gets a line mark, and it is single-mode by construction
#     (the passage is ~200 words, so it is always read in paragraph mode);
#   - free practice is drawn as unconnected points, shaped by mode, so there is no line for
#     two modes to share.
BENCHMARK_SERIES = "Benchmark passage"
FREE_SERIES = "Free practice"

MODE_LABELS: Mapping[str, str] = {
    Mode.DRILL.value: "Drill (Mode A)",
    Mode.PARAGRAPH.value: "Paragraph (Mode B)",
    Mode.UNSCRIPTED.value: "Unscripted (Mode C)",
}

# (column in `attempts`, label on the chart). Completeness is deliberately absent: it is a
# function of how much of the script was read, not of how well it was pronounced, and on the
# benchmark it should be 100 every time.
METRICS: tuple[tuple[str, str], ...] = (
    ("pron_score", "Pronunciation"),
    ("accuracy", "Accuracy"),
    ("fluency", "Fluency"),
    ("prosody", "Prosody"),
)

METRIC_ORDER: tuple[str, ...] = tuple(label for _, label in METRICS)

FRAME_COLUMNS: tuple[str, ...] = (
    "when", "attempt_id", "metric", "value", "series", "mode", "label",
)

# How much of the reference text the tooltip shows. Enough to tell two free-practice texts
# apart, short enough not to unroll a whole paragraph under the cursor.
_LABEL_CHARS = 48


def _parse_when(created_at: str | None) -> datetime | None:
    """`created_at` back into a datetime. Stored UTC, second precision, always 'Z'-suffixed."""
    if not created_at:
        return None
    try:
        return datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        logger.warning("Unparseable created_at %r — attempt left out of the chart", created_at)
        return None


def _label(reference_text: str | None, benchmark: bool) -> str:
    """What the tooltip calls this attempt's text."""
    if benchmark:
        return BENCHMARK_TITLE
    text = " ".join((reference_text or "").split())
    if not text:
        return "(no reference text)"
    return text if len(text) <= _LABEL_CHARS else text[:_LABEL_CHARS - 1] + "…"


def score_frame(rows: Iterable[Mapping[str, Any]]) -> pd.DataFrame:
    """Long-form scores over time: one row per (attempt × metric).

    A NULL score produces **no row at all** — never a zero. `prosody` is NULL, not 0.0, when
    Azure did not return one (`db.py`'s schema says so and `tests/test_db.py` pins it), and
    plotting that as zero would invent a collapse the speaker never had. A gap in the line is
    the honest rendering.
    """
    records: list[dict[str, Any]] = []
    for row in spoken_attempts(rows):
        when = _parse_when(row["created_at"])
        if when is None:
            continue
        benchmark = is_benchmark(row["reference_text"])
        shared = {
            "when": when,
            "attempt_id": int(row["id"]),
            "series": BENCHMARK_SERIES if benchmark else FREE_SERIES,
            "mode": MODE_LABELS.get(str(row["mode"]), str(row["mode"])),
            "label": _label(row["reference_text"], benchmark),
        }
        for column, metric in METRICS:
            value = row[column]
            if value is None:
                continue
            records.append({**shared, "metric": metric, "value": float(value)})

    if not records:
        return pd.DataFrame({name: pd.Series(dtype="object") for name in FRAME_COLUMNS})
    return pd.DataFrame.from_records(records)[list(FRAME_COLUMNS)]


def score_chart(frame: pd.DataFrame) -> alt.Chart:
    """The trajectory: benchmark as a line, free practice as a faint unconnected cloud.

    The y scale is pinned to 0-100 on purpose. An auto-scaled axis magnifies noise into a
    trend, which is exactly the failure this whole benchmark design exists to prevent, and
    the brief measures progress as distance from native-like rather than as a pass mark.
    """
    base = alt.Chart(frame)
    x = alt.X("when:T", title=None)
    y = alt.Y("value:Q", title=None, scale=alt.Scale(domain=[0, 100]),
              axis=alt.Axis(tickCount=5))

    # Only the modes actually present get a shape. Mode C is declared in `utils.Mode` but is
    # not built, and a legend entry for it would advertise something that cannot happen.
    modes = [label for label in MODE_LABELS.values() if label in set(frame.get("mode", []))]

    # Free practice: points only, never joined. Shape carries the mode, so Mode A and Mode B
    # are distinguishable and there is no line for them to share.
    cloud = base.transform_filter(
        alt.datum.series == FREE_SERIES
    ).mark_point(size=45, filled=True, opacity=0.28).encode(
        x=x,
        y=y,
        shape=alt.Shape("mode:N", title="Free practice",
                        scale=alt.Scale(domain=modes,
                                        range=["triangle-up", "circle", "square"][:len(modes)])),
        color=alt.value("#8a8a8a"),
        tooltip=[alt.Tooltip("when:T", title="When"), alt.Tooltip("mode:N", title="Mode"),
                 alt.Tooltip("metric:N", title="Metric"),
                 alt.Tooltip("value:Q", title="Score", format=".1f"),
                 alt.Tooltip("label:N", title="Text")],
    )

    # The benchmark: the only layer with a line mark, and it encodes no mode at all — it
    # cannot, because the passage is only ever read in one mode.
    benchmark = base.transform_filter(
        alt.datum.series == BENCHMARK_SERIES
    ).mark_line(point=True, strokeWidth=2, color="#2f6fd0").encode(
        x=x,
        y=y,
        tooltip=[alt.Tooltip("when:T", title="When"),
                 alt.Tooltip("metric:N", title="Metric"),
                 alt.Tooltip("value:Q", title="Score", format=".1f"),
                 alt.Tooltip("label:N", title="Text")],
    )

    return alt.layer(cloud, benchmark).properties(height=130).facet(
        row=alt.Row("metric:N", title=None, sort=list(METRIC_ORDER),
                    header=alt.Header(labelFontWeight="bold", labelAnchor="start")),
    ).properties(title="Scores over time").resolve_scale(y="shared")


# --- Rhythm over time -------------------------------------------------------------------------
# nPVI is text-sensitive, so only benchmark reads are plotted. A free-practice point would be
# measuring the passage as much as the speaker, which is the same reason the benchmark passage
# exists at all. See `rhythm.py` for why the TTS baseline is the only comparison this supports,
# and why published General American bands get no ink here.

RHYTHM_COLUMNS: tuple[str, ...] = ("when", "attempt_id", "npvi", "pairs", "runs")


def rhythm_frame(parsed: Sequence[ParsedAttempt]) -> pd.DataFrame:
    """nPVI per benchmark attempt, oldest first.

    Reads the re-parsed word lists rather than a stored column: nPVI is derived from phoneme
    durations inside `azure_raw_json`, which is exactly what storing the response verbatim was
    for. Every attempt already in the database therefore gains a rhythm figure with no
    migration and no backfill.

    An attempt with too little connected speech to measure produces no row — never a zero.
    Same rule as `score_frame` and for the same reason: a gap is honest, a zero invents a
    collapse into perfectly even syllables that nobody has ever produced.
    """
    records: list[dict[str, Any]] = []
    for attempt in parsed:
        if not attempt.benchmark:
            continue
        when = _parse_when(attempt.created_at)
        if when is None:
            continue
        measured = rhythm.npvi(attempt.words)
        if not measured.measured:
            continue
        records.append({
            "when": when,
            "attempt_id": attempt.attempt_id,
            "npvi": float(measured.npvi),
            "pairs": measured.pairs,
            "runs": measured.runs,
        })

    if not records:
        return pd.DataFrame({name: pd.Series(dtype="object") for name in RHYTHM_COLUMNS})
    return pd.DataFrame.from_records(records)[list(RHYTHM_COLUMNS)]


def rhythm_chart(frame: pd.DataFrame, baseline: float | None = None) -> alt.Chart:
    """Benchmark nPVI over time, with the TTS baseline as a rule.

    Unlike `score_chart` the y axis is **not** pinned to a fixed domain. nPVI is not a 0-100
    score and has no meaningful ceiling; readings cluster in a narrow band, and forcing a wide
    axis would flatten the only thing worth seeing into a straight line. `zero=False` for the
    same reason.

    The baseline is drawn as a rule rather than a second series because it does not move — it
    is one capture, not a history, and drawing it as a line over time would imply otherwise.
    """
    points = alt.Chart(frame).mark_line(point=True, strokeWidth=2, color="#2f6fd0").encode(
        x=alt.X("when:T", title=None),
        y=alt.Y("npvi:Q", title="nPVI", scale=alt.Scale(zero=False)),
        tooltip=[alt.Tooltip("when:T", title="When"),
                 alt.Tooltip("npvi:Q", title="nPVI", format=".1f"),
                 alt.Tooltip("pairs:Q", title="Vowel pairs"),
                 alt.Tooltip("runs:Q", title="Unbroken stretches")],
    )
    if baseline is None:
        return points.properties(height=170, title="Rhythm (nPVI) over time")

    rule = alt.Chart(pd.DataFrame({"baseline": [baseline]})).mark_rule(
        color="#8a8a8a", strokeDash=[6, 4], strokeWidth=2,
    ).encode(y=alt.Y("baseline:Q", scale=alt.Scale(zero=False)))

    return alt.layer(points, rule).properties(
        height=170, title="Rhythm (nPVI) over time"
    ).resolve_scale(y="shared")


def days_since_benchmark(rows: Iterable[Mapping[str, Any]], *, now: datetime | None = None
                         ) -> int | None:
    """Whole days since the benchmark passage was last read, or None if it never has been.

    Stated as a fact, not as a nudge: nothing here decides the passage is due. The cadence
    is the user's discipline, and an app that nags about it would be gamification.
    """
    moments = [
        when for when in (
            _parse_when(row["created_at"]) for row in spoken_attempts(rows)
            if is_benchmark(row["reference_text"])
        ) if when is not None
    ]
    if not moments:
        return None
    return max(0, ((now or datetime.now(timezone.utc)) - max(moments)).days)


# --- What keeps going wrong -----------------------------------------------------------------
# The per-phoneme and per-word detail is not stored as columns — it lives inside
# `azure_raw_json`, in Azure's own shape. Recovering it is a re-parse of the stored row, which
# is exactly what storing the response verbatim was for (see `db.py`'s module docstring). The
# re-parse is expensive enough that `app.py` caches it; nothing here caches.


@dataclass(frozen=True)
class ParsedAttempt:
    """One stored attempt, re-parsed back into the normalised word shape."""

    attempt_id: int
    created_at: str
    mode: Mode
    reference_text: str
    benchmark: bool
    words: list[dict[str, Any]]


def parse_attempts(rows: Iterable[Mapping[str, Any]]) -> list[ParsedAttempt]:
    """Re-parse stored payloads through the same normaliser the live path uses.

    Two shapes arrive: a drill stores a JSON **object** and a paragraph a JSON **array** of
    per-utterance payloads (`app.py` writes `raw if len(raw) > 1 else raw[0]`), so a
    non-list is wrapped. The `Mode` has to be passed through as well, or Mode B's local
    miscue diff never runs and omissions vanish from the aggregate.

    A row that cannot be parsed is logged and skipped: one bad payload must not blank the
    whole view.
    """
    parsed: list[ParsedAttempt] = []
    for row in spoken_attempts(rows):
        try:
            payload = json.loads(row["azure_raw_json"])
            payloads = payload if isinstance(payload, list) else [payload]
            mode = Mode(str(row["mode"]))
            reference_text = row["reference_text"] or ""
            _, _, words = speech_analyzer.normalise(payloads, reference_text, mode)
        except Exception:  # noqa: BLE001 — a corrupt row is a skip, not a broken page
            logger.warning("Attempt %s could not be re-parsed; left out of the totals",
                           row["id"], exc_info=True)
            continue
        parsed.append(ParsedAttempt(
            attempt_id=int(row["id"]),
            created_at=str(row["created_at"]),
            mode=mode,
            reference_text=reference_text,
            benchmark=is_benchmark(reference_text),
            words=words,
        ))
    return parsed


# What a low-scoring phoneme with no better alternate is called. Azure names no substitute
# when the sound was weakened or dropped rather than swapped — which is what final-cluster
# simplification looks like in the data (see `phoneme_reference.FINAL_CLUSTER_NOTE`). Folding
# it in with the substitutions would misreport it; dropping it would make the benchmark
# passage's fourteen final clusters invisible.
UNCLEAR = "(unclear)"


def _tally(per_attempt: Sequence[tuple[bool, Sequence[str]]]) -> list[dict[str, Any]]:
    """Turn per-attempt occurrence lists into attempt counts and token counts.

    Ranking is by **how many attempts a thing appeared in**, not by raw occurrences: "flagged
    most often" is a question about recurrence across sessions, and counting tokens alone
    would let a single paragraph that repeats one word dominate the list.
    """
    attempts: dict[str, int] = {}
    benchmark_attempts: dict[str, int] = {}
    tokens: dict[str, int] = {}
    for benchmark, occurrences in per_attempt:
        for name in occurrences:
            tokens[name] = tokens.get(name, 0) + 1
        for name in set(occurrences):
            attempts[name] = attempts.get(name, 0) + 1
            if benchmark:
                benchmark_attempts[name] = benchmark_attempts.get(name, 0) + 1
    rows = [
        {"attempts": count, "benchmark_attempts": benchmark_attempts.get(name, 0),
         "tokens": tokens[name], "_name": name}
        for name, count in attempts.items()
    ]
    rows.sort(key=lambda r: (-r["attempts"], -r["tokens"], r["_name"]))
    return rows


def flagged_phonemes(parsed: Sequence[ParsedAttempt]) -> pd.DataFrame:
    """Which expected → produced substitutions recur across stored attempts.

    Reads `speech_analyzer.is_flagged` and `speech_analyzer.phoneme_pairs` — the single
    definition of "what you actually produced" that the word cards and the coaching report
    also read, so this view can never disagree with them about a substitution.
    """
    per_attempt: list[tuple[bool, list[str]]] = []
    detail: dict[str, tuple[str, str]] = {}
    for attempt in parsed:
        occurrences: list[str] = []
        for word in attempt.words:
            if not speech_analyzer.is_flagged(word):
                continue
            for expected, produced, score in speech_analyzer.phoneme_pairs(word):
                if not expected:
                    continue
                if produced:
                    label = f"/{expected}/ → /{produced}/"
                elif score is not None and score < utils.PHONEME_RED:
                    label, produced = f"/{expected}/ → {UNCLEAR}", UNCLEAR
                else:
                    continue
                detail[label] = (expected, produced)
                occurrences.append(label)
        per_attempt.append((attempt.benchmark, occurrences))

    rows = _tally(per_attempt)
    if not rows:
        return pd.DataFrame({name: pd.Series(dtype="object") for name in
                             ("label", "expected", "produced", "attempts",
                              "benchmark_attempts", "tokens")})
    return pd.DataFrame([
        {"label": row["_name"], "expected": detail[row["_name"]][0],
         "produced": detail[row["_name"]][1], "attempts": row["attempts"],
         "benchmark_attempts": row["benchmark_attempts"], "tokens": row["tokens"]}
        for row in rows
    ])


def flagged_words(parsed: Sequence[ParsedAttempt]) -> pd.DataFrame:
    """Which words recur in the flagged list across stored attempts."""
    per_attempt: list[tuple[bool, list[str]]] = []
    for attempt in parsed:
        occurrences = [
            str(word.get("word") or "").lower()
            for word in attempt.words if speech_analyzer.is_flagged(word)
        ]
        per_attempt.append((attempt.benchmark, [w for w in occurrences if w]))

    rows = _tally(per_attempt)
    if not rows:
        return pd.DataFrame({name: pd.Series(dtype="object") for name in
                             ("word", "attempts", "benchmark_attempts", "tokens")})
    return pd.DataFrame([
        {"word": row["_name"], "attempts": row["attempts"],
         "benchmark_attempts": row["benchmark_attempts"], "tokens": row["tokens"]}
        for row in rows
    ])


# How many bars each ranking shows. Long enough to see a pattern, short enough that the bar
# at the bottom still means something.
TOP_PHONEMES = 12
TOP_WORDS = 15


def _ranking_chart(frame: pd.DataFrame, field: str, title: str, colour: str,
                   limit: int) -> alt.Chart:
    """One horizontal bar ranking, ordered worst-first by attempts."""
    data = frame.head(limit)
    return alt.Chart(data).mark_bar(color=colour, cornerRadiusEnd=2).encode(
        x=alt.X("attempts:Q", title="Attempts it was flagged in",
                axis=alt.Axis(tickMinStep=1)),
        # labelOverlap=False: Vega drops every other label when the band is tight, and a
        # ranking whose bars are half unlabelled says nothing at all.
        y=alt.Y(f"{field}:N", title=None, sort="-x",
                axis=alt.Axis(labelOverlap=False, labelLimit=180)),
        tooltip=[alt.Tooltip(f"{field}:N", title=title),
                 alt.Tooltip("attempts:Q", title="Attempts"),
                 alt.Tooltip("benchmark_attempts:Q", title="of those, benchmark reads"),
                 alt.Tooltip("tokens:Q", title="Times in total")],
    ).properties(title=title, height=alt.Step(20))


def phoneme_chart(frame: pd.DataFrame, limit: int = TOP_PHONEMES) -> alt.Chart:
    """The substitutions flagged in the most attempts."""
    return _ranking_chart(frame, "label", "Sounds flagged most often", "#c07f16", limit)


def word_chart(frame: pd.DataFrame, limit: int = TOP_WORDS) -> alt.Chart:
    """The words that recur in the flagged list."""
    return _ranking_chart(frame, "word", "Words that keep coming back", "#6a4fa0", limit)
