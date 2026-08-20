# v0.11.0 — Accent visualisation, resynthesis, and a General American reference this project actually measured

Issue #14, milestone v0.11.0. To be copied to
`plans/2026-08-20_accent-visualisation-and-resynthesis.md` with a `planned` row in
`memory-bank/history.md` before any code is written.

## Context

v0.10.0 landed the measurement core — `vowel_measure.py` slices vowels out of a recording,
`acoustics.py` measures F1/F2/F3 at 20/50/80 %, `vowel_reference.py` holds the Hillenbrand
General American means, and every finding renders through one four-column Markdown table. It
was calibrated live on 2026-08-20: a 0.20 z noise floor across 18 vowels, from two benchmark
reads 13 minutes apart.

What it cannot do is *show* any of that, or let the user **hear** it. `projectbrief.md`'s
founding problem is "I can't hear the difference between my pronunciation and a native
speaker's", and a table of z-scores does not solve it. Two things close the gap: an arrow
from produced to target carries a direction and a priority ranking a signed number does not,
and changing **one** dimension of the user's own recording and playing it back isolates the
variable that a side-by-side TTS clip never can.

But there is a third thing, and it is the reason this plan is longer than the issue.

### The reference is the bottleneck, and it is fixable with free-tier quota

Every awkward compromise in v0.10.0 traces to one root cause: the only General American
reference in the project is Hillenbrand et al. (1995), and `vowel_reference.py`'s own
docstring lists what that costs.

- **It covers 12 vowels, not the inventory.** There is no published mean for
  `/aɪ aʊ ɔɪ ə ɚ ɑɹ ɔɹ ɛɹ ɪɹ ʊɹ/` — ten categories the benchmark passage deliberately
  carries. Today those render "no published GA reference" and are recorded, not scored. The
  quadrant would draw ten arrowless points. **Rhoticity — which the brief calls the loudest,
  cleanest, most correctable marker for a GA target — has a published mean for `/ɝ/` alone.**
- **Its durations are citation-form `/hVd/` words.** `/i/` averages 244 ms there against far
  less in connected speech, which is why caveat 3 forbids comparing absolute ms at all — and
  why the duration chart had to choose between being honest and being what was asked for.
- **It is upper-Midwest speech from the early 1990s**, handled by widening tolerance bands
  for the low-back merger and GOOSE-fronting rather than by having current data.

The fix costs almost nothing. **The benchmark passage already carries all 22 vowel categories
by design** (`progress_view.BENCHMARK_COVERAGE`). Synthesising it in a set of en-US neural
voices and pushing each rendering through *this project's own measurement pipeline* produces a
reference that is connected speech, complete across the inventory, current, and measured by
the same segmenter and the same Burg analysis that measures the user — so absolute
milliseconds finally compare like with like.

**Sixteen voices costs 15,600 TTS characters (3.1 % of the monthly 500,000) and about 992
seconds of STT (5.5 % of the monthly 18,000).** Once. `scripts/capture_baseline.py` already
spends 975 characters and 62 seconds for a single voice and calls it a fixed point worth
having; this is the same trade sixteen times over, for the measurement this project exists to
make.

`vowel_measure.py:1057` already declares `REFERENCE_PUBLISHED = "Hillenbrand 1995"` and
`REFERENCE_VOICE = "TTS voice, same pipeline"` as two references that "do NOT coincide" and
"are never averaged together". `REFERENCE_VOICE` has never had anything behind it. This chunk
populates it.

**Neither reference replaces the other.** Hillenbrand is real humans, peer-reviewed, and stays.
The model reference is real connected speech through the identical pipeline, from
synthesisers. Both are shown, both are labelled, and — exactly as the existing constants
promise — they are never averaged.

## Exit condition

**A monophthongised FACE vowel and a diphthongised one are visibly different on the trajectory
chart.** If that fails the pipeline is wrong regardless of what else works.

## Decisions taken before writing this

Three points where the brief collides with a v0.10.0 decision. Recorded so they are not
re-litigated mid-implementation.

1. **The native contour comes from synthesise-then-assess through Azure STT**, not from the
   cheaper `synthesis_word_boundary` events, so the model's word offsets come from the *same
   segmenter* that timed the user's recording. Captured for a **set** of voices, not one.
2. **The duration chart plots absolute ms against Hillenbrand's `duration_ms`, as briefed** —
   and beside it a third bar, the model-voice mean measured through this pipeline, which is
   what makes the first two interpretable. `vowel_reference.py`'s caveat 3 ("absolute
   milliseconds must never be compared against this table") becomes wrong about what the
   project does and gets amended; a judgment call, so propose the exact lines before writing
   per `CLAUDE.md` §3.
3. **Trajectories are sampled 20 %→80 %, not the brief's 25/75.** `acoustics.SAMPLE_POINTS`
   is `(0.20, 0.50, 0.80)` because that is Hillenbrand's own sampling, and a 25/75 sample
   against a 20/80 reference is a systematic bias landing hardest on exactly the diphthongs
   this chart exists to show. No code change; one line in the docstring saying so.

Also settled: **all six chart+table pairs live on the Accent tab only.** The four-column table
under each assessment result stays as it is. Streamlit runs every tab body on every rerun, and
six frames re-derived on each 0.4 s job poll is not worth it.

### Verified, not assumed

Run in the pinned container (`dialect-coach-app:local`, parselmouth 0.4.7, Speech SDK 1.51.1).
`progress.md` records "planning v0.11.0 against a typed `parselmouth.Manipulation` class" as a
dead end — everything below goes through the untyped `parselmouth.praat.call`.

- `call(snd, "To Manipulation", 0.01, 60, 500)` → `parselmouth.Data`.
- `Extract pitch tier` / `Create PitchTier` + `Add point` / `Replace pitch tier` →
  `Get resynthesis (overlap-add)` (PSOLA). Confirmed the output's median F0 follows the
  replaced contour.
- `Extract duration tier` → `Add point` → `Replace duration tier` → resynthesis: 1.0 s became
  1.4 s.
- `Change gender` (floor, ceiling, formantShiftRatio, **0** = keep pitch median, 1, 1) for
  formant shifting; `Extract part` / `Concatenate` for splicing one vowel back into an
  otherwise untouched utterance — duration and sample rate survive.
- `Sound.save(path, parselmouth.SoundFileFormat.WAV)` works but **warns on clipping**;
  amplitudes must be scaled before writing.
- `SpeechSynthesizer.synthesis_word_boundary` exists with `audio_offset` / `duration` /
  `text` / `boundary_type` — **not** used (decision 1), but worth recording as available, and
  it is the free fallback if the STT half ever has to be dropped.

## What gets built

### 1. The model reference — `src/model_reference.py` (generated) and its capture

Mirrors the pattern `scripts/build_vowel_reference.py` → `src/vowel_reference.py` already
establishes: a script derives a module, the module is marked **GENERATED FILE, do not
hand-edit**, and no formant value in this project is ever typed from memory.

- **`scripts/capture_model_reference.py`** — for each voice in a curated en-US list:
  synthesise `BENCHMARK_PASSAGE`, convert, assess, and store the payload plus the WAV. Meters
  both halves through `db.record_tts_usage` and the attempts table so the app's
  remaining-allowance figure stays honest, and refuses through `budget.preflight_*` like
  every other call. Resumable — a voice already captured is skipped, so a mid-run failure
  costs only the remainder.
- **Voices are sex-stratified and never pooled.** `GA_REFERENCE_SET = men | women` exists
  because formants scale with vocal tract length and an average of the two describes nobody;
  the captured set carries the same split, built from `perception_trainer.VOICES` (six
  already curated as plain `…Neural`, never DragonHD or MAI) plus `en-US-BrianNeural` and
  enough more from `scripts/list_voices.py` to reach roughly eight per set. `MIN_VOICES = 4`
  per set is the refusal floor.
- **`scripts/build_model_reference.py`** — runs every captured rendering through
  `vowel_measure.extract` with the ceiling swept per voice, then emits `src/model_reference.py`:
  per vowel, per reference set, the F1/F2/F3 means at 20/50/80 %, the **between-voice SD**
  (a real speaker-to-speaker spread, not a 1995 within-corpus SD), mean duration in ms, mean
  F3−F2, mean F2 travel, and the voice count behind each number. **All 22 categories**, or an
  honest absence for any that too few voices produced cleanly.
- The generated module is **committed**: it is numbers, it carries no key and no audio, and
  committing it means a fresh clone has the reference without re-spending. The WAVs go to the
  gitignored `audio/`, like every recording in this project.
- `vowel_measure.reference_positions(reference_set)` gains a `source` argument
  (`REFERENCE_PUBLISHED` | `REFERENCE_VOICE`), so every consumer states which table it used
  and the two can never be silently mixed.

**What this unlocks, concretely:** rhoticity gets a target for all seven r-coloured
categories instead of `/ɝ/` alone; the quadrant draws arrows for 22 vowels instead of 12; the
duration chart gets a connected-speech target; the reduction measure gets a model schwa
centroid to sit beside the speaker's own; and `TOLERANCE_MULTIPLIER`'s hand-widened bands for
`/ɑ ɔ u/` acquire a check — if current neural voices show the low-back merger and
GOOSE-fronting, the model table will say so in numbers.

### 2. `src/native_model.py` — the model's rendering of an arbitrary text

The reference above is the benchmark passage. The pitch overlay and the resynthesis need the
model's reading of **whatever the user just read**.

- `rendering_for(conn, text, voice)` → stored WAV bytes plus normalised words with Azure's
  word offsets, or `None`.
- `capture(conn, text, voices)` → synthesise, convert, assess, persist, for each voice.
  Captured **on demand when a surface needs it**, not hidden behind a warning: a paragraph is
  62 seconds of an 18,000-second monthly allowance. The surface states what it spent and what
  remains, afterwards, rather than asking permission first — `budget.preflight_*` still
  guards the ceiling and `OFFLINE_MODE` still refuses.
- `seed_from_fixtures(conn)` — the benchmark passage is **already paid for**;
  `tests/fixtures/benchmark_tts_baseline.json` carries the payloads and the voice and
  `audio/benchmark_tts_baseline.wav` the audio. The model-reference capture seeds the rest.
- New table `native_renderings(voice, text_key, reference_text, wav_path, payloads_json,
  created_at)`, unique on `(voice, text_key)`, beside `speaker_baseline` in `db.SCHEMA`.
  `text_key` reuses `tts.cache_key` so "the same text" has one definition.

A fourth module beyond the three the brief names, because it touches TTS *and* STT *and* the
database: folding it into `tts.py` (which has never seen STT or `db`) or `accent_charts.py`
(which must stay network-free) would break a boundary that is currently clean.

### 3. `src/vowel_reference.py` — the arrow-to-instruction mapping, as data

The one place this chunk can produce confidently wrong advice, which is worse than none. F1
maps cleanly — higher F1 means a lower tongue and a more open jaw. **F2 does not.** It
responds to lip posture as strongly as to tongue advancement, so the same delta has two causes
and two opposite instructions.

- `VOWEL_CLASS: Mapping[str, str]` — one entry for **every** symbol in
  `phoneme_reference.LEXICAL_SET` (22), classifying into `front-unrounded` / `back-rounded` /
  `rhotic` / `central` / `merging`.
- `ARTICULATION: Mapping[str, Instruction]` — a frozen dataclass of looked-up strings
  (`f1_raise`, `f1_lower`, `f2_raise`, `f2_lower`, optional `f3`). **Looked up, never
  generated.**
  - `/i ɪ eɪ ɛ æ/` — F2 is tongue advancement: "further front, spread the lips".
  - `/u ʊ oʊ ɔ/` — F2 is **lip posture**: "round your lips more" / "less". Never "move your
    tongue back". Telling a learner to retract the tongue when the error is unrounded lips
    makes the vowel worse.
  - `/ɝ ɚ ɔɹ ɑɹ ɪɹ ɛɹ ʊɹ/` — F3 is the measure, F1/F2 secondary; the instruction is tongue
    bunching or retraction plus rounding, **never** height or frontness.
  - `/ɑ ɔ/` — a merged-or-merging note, not an error, per the v0.10.0 note already encoded in
    `TOLERANCE_MULTIPLIER`.
- `instruction_for(vowel, formant, delta) -> str` — the single lookup.
- `BRIDGING_PHRASES: Mapping[str, tuple[str, ...]]` — hand-written sentences per vowel forcing
  the transition repeatedly in varied consonant contexts. Not word lists: the value is the
  co-articulation. Plus `PRE_FORTIS_PAIRS` and `STRESS_SHIFT_PAIRS`, which the phoneme-keyed
  table in `phoneme_reference.py` has no slot for. Free, offline, permanent.

Then **rewire `vowel_measure._position_instruction`** (`src/vowel_measure.py:1129`), which
today emits a generic "tongue further back, lips rounder" for every negative F2 delta — the
exact wrong instruction for a back-rounded vowel.

### 4. `src/vowel_measure.py` — gating, slicing, ranking

- **`plot_gate(baseline_row, measurement) -> PlotGate`** — gate on *is there a stored
  baseline*, **never on the mode**. With one stored, a three-word drill **is** plottable as a
  single point with its token count shown; without one, refuse with a reason naming what is
  missing. Wrong in either direction is costly: refusing drills throws away the
  measure-drill-remeasure loop that is this feature's whole purpose, and plotting before a
  baseline exists draws a confident dot from a normalisation that does not exist. `PlotGate`
  also carries `style_warning` — a read-speech baseline normalises read speech, so a
  `spontaneous` token plotted against it says so rather than silently mixing two populations.
  v0.12.0 depends on this being right.
- **Normalise through the stored baseline.** `app.render_accent_table`
  (`src/app.py:1571`) calls `lobanov(measurement.accepted, …)`, which needs
  `MIN_CATEGORIES = 8` and therefore refuses every drill. Where a baseline exists, use
  `normaliser_from_json(baseline_row["normaliser_json"])`; the `lobanov` path stays as the
  no-baseline case. This one change is what makes short-token plotting possible.
- **`findings_by_instrument(...) -> dict[str, list[Finding]]`**, keyed `position` /
  `trajectory` / `rhoticity` / `duration` / `reduction` / `stress` / `pitch` / `rhythm` /
  `rejected`. `findings()` becomes its concatenation — existing output unchanged — and each
  chart renders its own slice from the same data. New builders for `pitch` (range in
  semitones, terminal slope per phrase) and `rhythm` (nPVI against the model band), lifting
  the numbers `app.render_rhythm` (`src/app.py:1391`) already computes.
- **`ranked_gaps(...) -> list[Gap]`** — longest arrows *net of the noise floor*, shortest
  trajectories, largest F3−F2 excess, worst reduction distance, worst nPVI deviation, each
  carrying vowel, metric, magnitude and token count. **An arrow shorter than the noise band
  is not a finding** and never enters the ranking.

### 5. `src/accent_charts.py` — six frames, six charts (new)

pandas and altair, **never Streamlit** — the boundary `progress_view.py` and `accent_view.py`
already hold, so every frame and chart spec is assertable without driving a page. One
`*_frame()` and one `*_chart()` per instrument. `altair==6.2.2` is already pinned; no new
dependency.

The module docstring carries the alignment decision in full: **the two contours are aligned on
Azure's word offsets and linearly interpolated between them — not with DTW, and not by
stretching the whole clip uniformly.** DTW minimises distance between two contours, which
means it will happily warp the time axis until a timing error disappears, and timing error is
one of the things being measured. Anchoring on word offsets is a piecewise warp constrained to
linguistic units: it aligns the same word to the same word without being able to hide that one
of them took twice as long. A later chunk wanting DTW wants it for global similarity scoring,
which is a different question.

Order on the page — **rhoticity first**, because it is the chart most likely to show the
largest single gap:

1. **Rhoticity** — F3 and F3−F2 per rhotic token as a strip plot with the reference band
   marked. Now covers all seven r-coloured categories, not just `/ɝ/`.
2. **Vowel quadrant** — F2 on x **decreasing left to right**, F1 on y **decreasing bottom to
   top**; `accent_view.vowel_chart` already reverses both and that reasoning stays. New here:
   **an arrow per vowel from produced to target** — the arrow is the deliverable, its
   direction is the instruction and its length is the priority ranking. Every point labelled
   Azure IPA plus the Wells keyword (`accent_view._label` already does this). The noise floor
   draws as a faint circle around each produced point, and **an arrow shorter than that circle
   is not a finding** — it renders "within measurement noise".
3. **Diphthong trajectories** — a stroke from the 20 % point to the 80 % point. A
   monophthongised FACE vowel renders as a dot where a native rendering renders as a stroke.
   The exit condition.
4. **Pitch overlay** — the user's contour against the model's, on a shared time axis with word
   boundaries marked, plus the **between-voice envelope** as a band. F0 in **semitones
   relative to each speaker's own median**, never Hz, so a low voice and a synthetic voice
   overlay meaningfully and the chart shows contour *shape* rather than the trivial fact that
   two people have different voices. `vowel_measure._semitones` (`src/vowel_measure.py:928`)
   already exists — reuse it. Report pitch range in semitones and each phrase's terminal slope.
5. **Duration** — bars per vowel: the user's mean, Hillenbrand's `duration_ms` (as briefed),
   and the model-voice mean through this pipeline. Plus the tense/lax and pre-fortis ratios as
   single numbers against their targets. The caption carries the citation-form caveat and says
   plainly which of the two targets the pipeline actually supports.
6. **Rhythm** — one bar per vocalic interval from `rhythm.vocalic_intervals`, plus nPVI
   against the model band. With N voices the reference stops being one synthesiser's
   idiosyncratic reading and becomes a distribution — which is the objection `rhythm.py` and
   `capture_baseline.py` both raise about themselves.

**Every chart ships with the table.** Each renders with
`accent_view.to_markdown(findings_by_instrument(...)[key])` beside it, from the same
measurement. The chart carries the shape and the table carries the numbers; neither
substitutes for the other, and the table is the half that survives being pasted into a plan
file or a commit message.

**Never a percentage or a verdict.** Every surface reports distance and direction with the
token count behind it. Report the gap even when it is small — especially then — but never
smaller than the noise floor. Say **POST-HOC** on the surface: issue #14 sketches real-time
overlapping pitch curves, and this is drawn after the assessment returns.

### 6. `src/accent_resynth.py` — the part that makes the arrow audible (new)

Pure parselmouth: no Streamlit, no network, no database. Each function returns a `Resynthesis`
— `audio: bytes`, `changed: str` (the one variable moved), `capped: bool`, `note: str`.

- **`corrected_pitch(...)`** — extract the user's F0 track, replace it with the model contour
  (the median across the captured voices of the matching set) scaled to the user's own median
  and range in semitones, resynthesise with PSOLA. The user hears their own voice with native
  intonation. Nothing else in this project demonstrates a prosody error so immediately, so it
  ships first.
- **`corrected_timing(...)`** — a DurationTier stretching the under-long vowels and compressing
  the over-long ones toward the model durations. What makes under-reduction and missing
  pre-fortis clipping audible rather than tabular.
- **`corrected_vowel(...)`** — `Extract part` around one flagged vowel, `Change gender` with a
  formant shift ratio and pitch median 0, `Concatenate` back. The narrowest and most
  convincing, and the most fragile.

**The rules, in code and asserted in tests:**

- **Cap every manipulation** — no shift past roughly a third of the distance to the target, no
  duration scale outside ~0.67–1.5×. Beyond that the ear reads "robot" rather than "native",
  which teaches the wrong lesson. `capped=True` reaches the surface.
- **Always play the ORIGINAL immediately before the modified version, in that order,
  labelled.** A modified clip heard alone teaches nothing. This gets its own `AppTest`
  assertion.
- **State on the surface that this is the user's own voice, modified.** A synthetic-sounding
  clip the user believes is a native model is actively misleading.
- **Transient** — generated in the request, played, never written to disk, like every other
  synthesised clip here.
- **Button-gated and cached in session state**: a Manipulation over a 90-second paragraph is
  expensive and Streamlit re-runs the script on every widget interaction.

### 7. Closing the loop — the point of the ranking is the next drill

- `fallback_coach.compact()` gains a **`"vowel_geometry"` section** carrying `ranked_gaps`,
  **alongside** the existing phoneme payload, never instead of it. A new section, not a new
  prompt.
- `ai_coach.SYSTEM_INSTRUCTION` asks for a **bridging phrase** — a sentence forcing the
  specific transition repeatedly in varied consonant contexts, not a word list.
  `CoachingReport` gains `bridging_phrases`, and `ai_coach.validated()` must check them the
  way `_checked_drills` (`src/ai_coach.py:287`) already checks drills: a phrase naming a vowel
  that was never measured is a fabrication and rejects the report.
- `fallback_coach` writes the same section from `vowel_reference.BRIDGING_PHRASES` — offline,
  free, permanent.
- **One click turns a bridging phrase into a drill**, pre-filled, no retyping, through session
  state the way `_apply_preset` (`src/app.py:635`) already does. A second button promotes it
  into the practice queue via `db.upsert_target` with `kind=practice_queue.VOWEL` and the
  gap's evidence attached — a rhoticity or reduction target **is that kind with its
  evidence**, not a new one.

### 8. `scripts/rederive.py` — the promise v0.10.0 made

v0.10.0 keeps recordings specifically so "a changed normalisation scheme is a re-derivation
rather than a request that the passage be read again". This chunk changes the reference, which
makes that script owed. Re-measures every stored attempt's kept WAV against its stored payload
and rewrites `vowel_measurements`. **Costs no Azure quota at all** — it is local signal
processing over audio and payloads already on disk.

## Files

| File | Change |
|---|---|
| `src/model_reference.py` | **new, generated** — 22 vowels × 2 sets, measured through this pipeline |
| `scripts/capture_model_reference.py` | **new** — synthesise + assess the benchmark in ~16 voices, resumable |
| `scripts/build_model_reference.py` | **new** — measure the captures, emit the module |
| `src/native_model.py` | **new** — the model's reading of an arbitrary text; capture, store, seed |
| `src/accent_charts.py` | **new** — six frame/chart pairs; the alignment decision in the docstring |
| `src/accent_resynth.py` | **new** — three PSOLA surfaces, capped |
| `scripts/rederive.py` | **new** — re-measure stored audio against the new reference |
| `src/vowel_reference.py` | `VOWEL_CLASS`, `ARTICULATION`, `instruction_for`, `BRIDGING_PHRASES`, `PRE_FORTIS_PAIRS`, `STRESS_SHIFT_PAIRS`; amend caveat 3 |
| `src/vowel_measure.py` | `plot_gate`, `findings_by_instrument`, `ranked_gaps`, pitch/rhythm builders; `reference_positions(source=…)`; `_position_instruction` becomes a lookup |
| `src/accent_view.py` | quadrant gains arrows and noise circles; keeps the one table renderer |
| `src/app.py` | Accent tab grows the six chart+table pairs, resynthesis players, one-click drill; `render_accent_table` normalises through the stored baseline |
| `src/db.py` | `native_renderings` table |
| `src/fallback_coach.py`, `src/ai_coach.py` | `vowel_geometry` payload section, bridging phrases, validation |
| `tests/test_model_reference.py`, `tests/test_accent_charts.py`, `tests/test_accent_resynth.py` | **new** |
| `tests/test_accent.py`, `tests/test_vowel_measure.py` | extend — gating, instruction mapping, coach payload |

## Order of work

Commit at each step — `CLAUDE.md`: commit chunks as they land, never one commit at the end.

1. `vowel_reference` instruction mapping + bridging phrases + tests; rewire
   `_position_instruction`. No Azure cost.
2. `vowel_measure`: `plot_gate`, `findings_by_instrument`, `ranked_gaps`,
   baseline-normaliser path in `render_accent_table`. No Azure cost.
3. **The capture run.** `capture_model_reference.py` + `build_model_reference.py` +
   `model_reference.py` + `reference_positions(source=…)`. **~15,600 TTS characters and ~992
   STT seconds, once.** Verify the generated table against Hillenbrand where the two overlap
   before trusting it anywhere — the twelve shared categories should agree in *shape* after
   normalisation, and a set that does not is a bug in the capture, not a finding about English.
4. `native_model` + `db` table + fixture seeding.
5. `accent_charts`: rhoticity, quadrant with arrows, trajectories. Wire the Accent tab.
   **The exit condition is testable at the end of this step — do not go further until it
   passes.**
6. `accent_charts`: pitch overlay, duration, rhythm. Wire.
7. `accent_resynth`: corrected pitch, then timing, then vowel. Wire with original-then-modified
   playback and the "your own voice, modified" label.
8. Coach payload, bridging phrases in both coaches, one-click drill, queue promotion.
9. `scripts/rederive.py`; memory bank update; `pyproject.toml` to 0.11.0; tag; close #14 and
   the milestone.

## Explicitly out of scope

- **A calibration built from more than two reads.** A two-read noise floor is a sample of
  size one, and N reads would give it a real distribution — a genuine improvement, and not
  this issue. Note it in `memory-bank/progress.md` as a follow-up.
- **Mode C / spontaneous speech.** `plot_gate` carries the style tag so v0.12.0 can rely on it;
  nothing here reads spontaneous speech.
- **Real-time overlapping pitch curves**, as issue #14 sketches. Streamlit re-runs the whole
  script on every interaction and there is no streaming audio path. The surface says POST-HOC.

## Verification

Everything runs in the container; `make check` is lint + typecheck + tests, and green there is
green in CI.

**The exit condition, as a test.** Build two synthetic recordings with `conftest.synth_vowel` —
a steady `/eɪ/` and one with a real F2 glide (a new `synth_glide` helper concatenating two
segments) — and assert the trajectory frame's stroke lengths differ by a stated factor and that
the chart encodes it.

**Resynthesis, without a committed recording.** The brief asks for "a committed sample WAV";
`.gitignore` and `tests/conftest.py` both say no recording is ever committed, and the conftest
comment argues the synthetic path is *better* here — a synthesised signal has a **known** F0
and known formants, so "did PSOLA move the pitch to the target and leave the formants alone" is
a real assertion rather than a comparison against another estimate of the same unknown. So:
synthesise a flat-F0 signal, apply `corrected_pitch` with a rising target, re-measure — assert
the contour rose, the formants did not, and the duration is unchanged. Assert an absurd target
returns `capped=True`.

**The model reference, as tests.** The generated module has an entry for every symbol in
`LEXICAL_SET` or an explicit absence with the voice count that produced it; the men's and
women's sets are never pooled; where the twelve Hillenbrand categories overlap, the
normalised positions correlate — a check that the capture measured speech and not an artefact.

**The rest, as tests.** No back-rounded vowel produces a tongue-advancement instruction. No
rhotic instruction mentions height or frontness. With no baseline the charts refuse with a
readable reason; with a baseline and three tokens they plot, with the count shown. A
`spontaneous` token against a `read` baseline carries the style caveat. No caption or `Finding`
renders a percentage of nativeness or a pass/fail verdict. The four-column headers are
unchanged — `tests/test_accent.py:27` already asserts this and must stay green.

**End to end, by hand** (`make up`, Accent tab) — the half tests cannot reach:

- Read the benchmark passage; confirm all six charts render with their tables beside them,
  rhoticity above the quadrant, and that the ten previously unreferenced vowels now carry
  arrows.
- Confirm a vowel inside the noise band says "within measurement noise" rather than drawing a
  confident arrow.
- Play corrected-pitch and confirm it **sounds like your own voice** with a different contour,
  that the original played first, and that the surface says it is modified.
- Record a three-word drill and confirm it plots as a single point with `n=` shown — the
  measure-drill-remeasure loop, and the thing the current code refuses.
- Click a bridging phrase through to a pre-filled drill, then into the practice queue.
- Confirm the remaining-allowance figure moved by roughly the predicted amount after the
  capture run, and not by more.
- Per the standing memory note: **do not reload the page mid-session during Playwright
  checks** — a full navigate silently wipes session-local state such as the room-check verdict.
