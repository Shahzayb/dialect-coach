# Accent measurement engine — v0.10.0

Plan file for the chunk. On approval this is copied to
`plans/2026-08-20_accent-measurement-engine.md` in the repo and gains a `planned` row in
`memory-bank/history.md`, per `CLAUDE.md` §2.

---

## Context

Azure's diagnosis is categorical — this phoneme is /θ/ or /t/, scored 0-100. Accent is
continuous. A vowel scoring 78 while drifting toward the target and one scoring 78 while
drifting away are identical to Azure. Nothing in the project measures the gradient part:
where a vowel actually sits, how it moves, how long it lasts, how loud it is, how far the
unstressed ones reduce.

This chunk builds that measurement. It depends on the phoneme offsets carried through the
parser in v0.6.0, and it costs two calibration recordings of the existing benchmark passage,
roughly 90 seconds each — two, because the displacement between them **is** the measurement
noise floor, and without it the progress view will render noise as progress.

---

## 1. Dependencies, settled first — verified, not recalled

### 1.1 Route one (praat-parselmouth) wins

**Verified against PyPI on 2026-08-20.** `praat-parselmouth` 0.4.7 publishes 100 files.
Enumerated in full: cp312 wheels exist for `macosx_11_0_arm64`,
`manylinux2014_x86_64`, `manylinux2014_i686`, `win32`, `win_amd64` — and **no
`linux_aarch64` wheel at any Python version**. `docker version` reports the daemon as
`arm64 linux`. So the container installs from `praat_parselmouth-0.4.7.tar.gz` (22.5 MB)
and compiles.

**The build, inspected from the sdist rather than guessed:**

| Fact | Value |
|---|---|
| Build backend | `setuptools.build_meta` via `scikit-build>=0.12` + `cmake>=3.18` |
| `scikit-build` latest | 0.19.1, classifiers list Python 3.12 |
| C/C++ sources in the tree | 2,865, of which 535 `.cpp` under `praat/` |
| Vendored deps | `extern/fmt`, pybind11 |

**Route one is taken, and the deciding reason is v0.11.0 — verified, with one correction to
the brief.** parselmouth's bindings directory contains `Formant.cpp`, `Pitch.cpp`,
`Intensity.cpp`, `Sound.cpp`, `Spectrum.cpp` and others — **there is no `Manipulation.cpp`.**
Manipulation, PitchTier and DurationTier are not exposed as typed Python classes. They are
still reachable, because `praat.cpp` binds `call`, `run` and `run_file`, and the Praat source
tree bundled in the sdist does contain `fon/DurationTier.cpp`, the PitchTier sources and the
Manipulation machinery. So v0.11.0's modified-self-voice surface is buildable on
`parselmouth.praat.call("To Manipulation...", ...)` — **untyped, string-dispatched, and not
the typed object API the brief describes.** The argument holds; the mechanism is worth
recording now so v0.11.0 is not planned against an API that does not exist.

What route one gives this chunk directly, all typed and confirmed from `Sound.cpp` /
`Formant.cpp`:

- `Sound.to_formant_burg(time_step, max_number_of_formants=5.0, maximum_formant=5500.0,
  window_length=0.025, pre_emphasis_from=50.0)` — Burg, the reference implementation.
- `Formant.get_value_at_time(n, t)` and `get_bandwidth_at_time(n, t)` — exactly the
  three-point sampling this pipeline needs, no frame arithmetic.
- `Sound.to_pitch_ac` / `to_pitch_cc` — the F0 track.
- `Sound.to_intensity(minimum_pitch=100.0, ...)` — intensity in dB.

**A bonus that settles one of the brief's traps structurally.** Praat's Burg API takes
`maximum_formant` and `max_number_of_formants` and derives the resampling rate and the LPC
order internally. The "ceiling and order are one decision, not two" failure is *impossible to
make* through this API and is entirely the caller's problem in route two. That is a second
independent argument for route one.

### MEASURED BUILD TIME — taken 2026-08-20, route one confirmed

Docker BuildKit, `docker compose build app`, cold (no layer cache), on the arm64 host:

| Stage | Time |
|---|---|
| `apt-get install build-essential cmake ninja-build` | **55.9 s** |
| **`pip wheel praat-parselmouth==0.4.7` — the Praat C++ compile** | **100.0 s** |
| Whole image, builder plus runtime, end to end | **265 s (4 m 25 s)** |

Output: `praat_parselmouth-0.4.7-cp312-cp312-linux_aarch64.whl`, 9.9 MB. Final image 1.68 GB
against 1.47 GB before, so parselmouth plus cmudict plus the numpy pin cost ~210 MB — and the
C++ toolchain is not in it, which was the point of the second stage.

**The concern was overblown.** 100 seconds, not the tens of minutes 535 `.cpp` files suggested;
Praat parallelises well and the layer is cached afterwards, so it is paid once. Verified inside
the built image: `parselmouth 0.4.7` wrapping `praat 6.1.38`, `cmudict` with 126,052 entries,
`Sound` constructible from a numpy array, and `parselmouth.praat.call` present — the untyped
door to the Manipulation machinery v0.11.0 needs.

One transient failure worth recording, because it looks like a build failure and is not: the
first attempt died after 62 s with `DeadlineExceeded` on `load metadata for
docker.io/library/python:3.12-slim`. That is a registry lookup timing out, not a compile
problem. Re-running succeeded with no change.

### 1.2 Stress lexicon: `cmudict` 1.1.3

**Verified against PyPI and then measured against this repo's own fixtures.**

- `cmudict` 1.1.3 ships `cmudict-1.1.3-py3-none-any.whl` — pure data, `py.typed`, requires
  only `importlib-metadata` and `importlib-resources`. **No nltk, no model download, no
  compiler.** 3.5 MB unpacked, 126,052 entries.
- `g2p-en` 2.1.0 drags `nltk`, `numpy`, `inflect`, `distance` — rejected.
- Hand-annotation was the third option and is **not needed**, which matters: it would have
  made every reduction measure scripted-only and returned nothing for Mode C.

**Coverage measured, not assumed:**

| Check | Result |
|---|---|
| Unique words of `BENCHMARK_PASSAGE` present in cmudict | **128 / 128** |
| Vowel tokens the passage yields | 230, of which **64 unstressed (28%)** |
| Word alignment across `sample_azure_response`, `sample_azure_continuous`, `bad_delivery_capture` | **100 / 101 words (99%)** |

The alignment is: count ARPABET vowel phones per word, count Azure vocalic phonemes per word
(via `phoneme_reference.lookup(...).kind in {vowel, diphthong, r-coloured}`, reusing
`rhythm.VOCALIC_KINDS`), and index-align. `R` is a consonant in ARPABET and is not counted, so
Azure's single `ɑɹ` still aligns against cmudict's `AA1` — which is why the rate is 99% and
not 70%. The one failure is real and is rejected honestly: *our* → Azure `ɑɹ` (one vocalic)
against cmudict `AW1 ER0` (two). A word whose counts disagree contributes no stress
information and gets a rejected row.

`AH0` is cmudict's schwa and `ER0` its `ɚ`, so the stress digit carries the reduction signal
directly. Mapping is `AA→ɑ AE→æ AH0→ə AH1/2→ʌ AO→ɔ AW→aʊ AY→aɪ EH→ɛ ER0→ɚ ER1/2→ɝ EY→eɪ
IH→ɪ IY→i OW→oʊ OY→ɔɪ UH→ʊ UW→u`, written once into `phoneme_reference.py` beside the
existing `_ALIASES`.

### 1.3 The memory-bank contradiction — resolved the other way

The brief asked me to decide which of `projectbrief.md` and the code is stale, on the premise
that the code is right. **The premise is wrong, and the evidence is in the memory bank
itself.** `memory-bank/techContext.md` records:

> **Audio on disk is now permitted but not built.** The user lifted the "no stored audio"
> rule on 2026-08-19: recordings may be kept on disk, never committed, with the path and hash
> in the database. Nothing in v0.6.0 needed it, so no column was added…

So `projectbrief.md` is **current**. The code implements the older, stricter behaviour and
never caught up. The stale text is in two docstrings that assert a constraint that no longer
exists:

- `src/audio_utils.py:99` — *"'no persistent audio storage' is a project constraint, not a
  nicety"*
- `src/db.py:6` — *"No audio is ever stored — the brief rules that out"*

**Both are corrected in this chunk. `projectbrief.md` is not edited.**

**Your decision: keep every recording.** Consequences, stated here because v1.0.0's privacy
disclosure rests on them:

- Every attempt's WAV is persisted under a gitignored `audio/attempts/`. `.gitignore` already
  ignores `audio/`; nothing new is needed there.
- Measurement still runs **inside the assessment request**, because the audio is already in
  memory at that point and a second pass would be pure cost. Persistence is for
  *re-derivation*, not for deferral. That distinction matters: normalisation schemes and
  reference tables will change, and re-deriving must never require re-recording.
- **Every attempt recorded before this chunk ships is permanently unmeasurable.** Nothing can
  recover them. The calibration passage must therefore be read *after* this lands — and it has
  not been read even once yet, so nothing is lost.
- Disk: 90 s of 16 kHz/16-bit/mono is ~2.9 MB. A hundred reads is ~290 MB. Stated in
  `.env.example` beside a `KEEP_AUDIO` switch that defaults on.
- "No audio leaves this machine" is untouched.

---

## 2. Findings that correct the brief's own numbers

Established by fetching and parsing the primary source, not by recall.

**The reference data.** `homepages.wmich.edu` — the canonical host — **no longer serves valid
TLS** (its certificate is `CN=redirect.wmich.edu`; `curl` fails with exit 60). The dataset is
taken instead from `github.com/santiagobarreda/hillenbrand_et_al_1995`, MIT-packaged and
"hosted with permission from Jim Hillenbrand", whose `h95-alldata.zip` contains the original
`vowdata.dat` (1,668 tokens), `vowdata.ds`, `timedata.dat` and the original `readme.txt`.

1. **The measurement points are 20% / 50% / 80%, not 25/50/75.** `vowdata.dat`'s own header
   states formants are sampled at steady state, 20%, 50% and 80% of vowel duration.
   **This pipeline adopts 20/50/80** rather than recording an offset — a 25/75 sample against
   a 20/80 reference is a systematic bias that lands hardest on the diphthongs v0.11.0's
   acceptance test depends on, and adopting costs nothing.
2. **The reference covers 12 vowels, not the inventory.** `ae ah aw eh er ei ih iy oa oo uh
   uw` → `æ ɑ ɔ ɛ ɝ eɪ ɪ i oʊ ʊ ʌ u`. There is **no published mean for `aɪ aʊ ɔɪ ə ɚ ɑɹ ɔɹ ɛɹ
   ɪɹ ʊɹ`** — ten of the categories `BENCHMARK_COVERAGE` deliberately covers. POSITION and
   TRAJECTORY are scoreable against GA for 12 categories; the other ten get a row whose Target
   column says *no published GA reference* rather than a number. This is a thin-table-is-
   visibly-thin case, not a defect.
3. **The brief's worked fixture numbers are illustrative and two are wrong.** Derived from
   `vowdata.dat`, adult male / adult female:
   - `/ɝ/` F3−F2 at 50%: **298 Hz / 339 Hz** (the brief says 310 — close).
   - `/eɪ/` F2 travel 20%→80%: **+140 Hz / +179 Hz** (the brief says 620 Hz — **not what the
     data says**). The fixture in §8 uses derived numbers.
   - `/ɝ/` F3−F2 against every other vowel: 298 Hz versus 546–1613 Hz. The rhoticity
     instrument is the cleanest discriminator in the table, confirmed before a line of code.
4. **Hillenbrand durations are citation-form `/hVd/` words read in isolation** — `/i/` is
   244 ms (men). Connected speech is far shorter. **Absolute ms must never be compared against
   this table.** Only ratios transfer: tense/lax, pre-fortis clipping, stressed/unstressed.
   The brief's "`/æ/` 118 ms against a 205 ms target" row is replaced by a ratio row. This trap
   is not in the brief and is written into `vowel_reference.py` as a comment.
5. **The low-back merger and `/u/`-fronting** are written in as comments *and as widened
   tolerance bands*, per the brief: `/ɑ/`–`/ɔ/` and `/u/` F2 get a wider band than the rest, so
   the tool does not confidently flag a change the 1995 upper-Midwest reference predates.

---

## 3. What gets built

New modules, all on the established boundary — no module below `app.py` imports Streamlit.

| File | Responsibility |
|---|---|
| `src/acoustics.py` | **The only module that imports parselmouth.** Sound loading, Burg formant track, F0 track, intensity, three-point sampling, the ceiling sweep. Knows nothing about English. |
| `src/vowel_reference.py` | Generated GA reference: per-vowel F1/F2/F3 at 20/50/80, duration, F0, SD and n, for the men and women sets, keyed by Azure IPA. Carries the merger/fronting comments and the widened bands. |
| `src/stress_lexicon.py` | cmudict adapter: ARPABET→Azure IPA, per-word vowel/stress sequence, alignment against Azure's vocalic phonemes. |
| `src/vowel_measure.py` | The pipeline: token extraction, rejection rules, Lobanov, the four instruments, the noise floor. Pure; the phonetics live here, mirroring how `rhythm.py` sits against `speech_analyzer.py`. |
| `src/accent_view.py` | The four-column table renderer and the chart frames. Mirrors `progress_view.py` — pandas/altair, no Streamlit. |
| `scripts/build_vowel_reference.py` | One-off generator that fetches the mirror, parses `vowdata.dat`, and writes `vowel_reference.py` with provenance. Values are **computed from the primary source, never typed.** |

Modified: `src/db.py` (three tables + readers), `src/app.py` (Accent tab, calibration flow,
room check, per-attempt table), `src/audio_utils.py` and `src/db.py` docstrings, `.env.example`,
`requirements.txt`, `Dockerfile`, `pyproject.toml` (mypy strict tier — a new module has to be
added there deliberately, which is the point).

### Storage — additive, `SCHEMA_VERSION` stays 1

Three `CREATE TABLE IF NOT EXISTS` tables, following the `practice_targets` /
`perception_trials` / `attempt_tags` precedent exactly.

```sql
attempt_audio      -- attempt_id, path, sha256, bytes, sample_rate, created_at
speaker_baseline   -- one current row, history kept: per-vowel means/SDs (JSON), the
                   -- noise-floor band (JSON), lpc_ceiling_hz, reference_set, style_tag,
                   -- measured_at, calibration attempt ids, superseded_at
vowel_measurements -- one row per token: attempt_id, vowel, f1/f2/f3 at 20/50/80,
                   -- duration_ms, rms_db, stressed, azure_score, snr_db_min,
                   -- lpc_ceiling_hz, style_tag, accepted, rejected_reason
```

**Raw measurements, never only derived positions.** F3, intensity and the stress flag are
columns from day one even though only v0.11.0 reads some of them — a column costs nothing and
a re-recording is impossible.

### Speech-style tag, from day one

`attempt_tags` already takes free text with no migration. Every attempt gains `read` or
`spontaneous` beside the existing `shadowed`. Read speech is hyperarticulated and spontaneous
speech is systematically more centralised; pooling them makes a register change look like a
regression. v0.12.0 adds spontaneous speech, but the tag has to exist **before** it, because
an untagged token can never be reclassified. Every baseline and trend query filters on it.

### Surface — a fourth tab

`st.tabs(["Today", "Practice", "Progress", "Accent"])`. The Accent tab holds the room check,
the calibration flow, the vowel chart, the baseline, the noise-floor band and the four-column
table for the baseline. The same four-column table also renders under each assessment result.

---

## 4. The pipeline

Runs inside `app.run_assessment_job`, immediately after `speech_analyzer.analyse` returns and
before `db.record_attempt`, where `wav_bytes` is still in memory. It never raises into the
worker thread — a measurement failure is recorded as a rejected row set, never as a lost
attempt.

1. **Gate on quality first.** Read `snr_db_min` from `assessment.overall_scores` — already
   parsed by `speech_analyzer._snr`; the payload is not re-read. Gate on the minimum, not the
   mean, because quality is governed by the worst segment. Below the floor the measurement is
   stored and marked unreliable, and the surface says so instead of drawing a confident dot.
2. **Slice each vowel** from the WAV using `offset_ticks` / `duration_ticks`.
   **`speech_analyzer._timing` warns that offsets are ticks from the start of the audio
   stream, not the file.** In the drill fixture the top-level `Offset` (16,900,000) equals the
   first word's offset, and `1.69 s + 9.79 s = 11.48 s` inside a 12.82 s recording — consistent
   with absolute file positions plus leading and trailing silence. **That reading is asserted,
   not assumed**: a test slices a known vowel and a known silence from a synthetic WAV built to
   the same offset structure and fails if the energy lands in the wrong place.
3. **Measure at 20%, 50% and 80%** of the segment — never an average. Edges are contaminated by
   coarticulation and an averaged diphthong lands in the middle of nowhere. Trajectory falls
   out for free: a monophthong is the case where 20% and 80% coincide.
4. **Per token**: F1/F2/F3 at the three points, duration in ms, RMS intensity in dB, plus an F0
   track over the whole utterance.
5. **Lobanov z-score** across the speaker's own inventory.
6. **Compare** against the reference chosen for that surface.

### The LPC ceiling

One setting, one stored value, one sweep.

- The F0-derived guess (~5000 Hz adult male, ~5500 Hz adult female) is treated as a **weak**
  estimator, because F0 and vocal tract length correlate loosely. It only bounds the sweep.
- The sweep runs 4500–6000 Hz in 100 Hz steps over the calibration audio and prefers the value
  that **minimises within-vowel-category formant variance** (per-category z-scored, so no
  category's scale dominates).
- The chosen value is stored on `speaker_baseline` **and on every `vowel_measurements` row**,
  so old rows stay interpretable after a re-calibration.
- `LPC_CEILING_HZ` exists as an override. `acoustics.burg_settings(ceiling)` returns the
  ceiling and `max_number_of_formants` together and a test asserts the relationship, so the
  two can never drift apart even though Praat already couples them.

### Rejection rules — refuse rather than guess

A token is rejected, with its reason stored and rendered, when it is:

- shorter than **45 ms** — too few pitch periods for a stable estimate (Azure's 10 ms grid
  means a 90 ms vowel already carries ±11% quantisation);
- unvoiced, or with no reliable F0 across its middle;
- a **different vowel entirely** — the highest-scoring vocalic nbest alternate disagrees with
  the target. Its formants are a valid measurement of the wrong target and would poison the
  cluster. That token belongs in the phoneme diagnosis, not the baseline;
- missing timing (an `_omission` carries `None`, by design);
- outside a plausible formant range, or with a bandwidth too wide to trust;
- in a word cmudict cannot align (stress only — the token still counts for position).

### Lobanov — the obvious implementation is the wrong one

Mean and SD are taken over **per-vowel-category means**, never over the raw token pool. Any
natural passage over-samples some vowels — the benchmark passage yields `AH` 50 times and `OY`
5 — and a token-weighted centroid is dragged toward whichever vowel happened to occur most,
tilting every z-score in the inventory. The error is invisible on inspection: the chart still
looks like a vowel chart. **A test asserts it with a deliberately unbalanced token set.**

The pipeline **refuses to normalise** below a floor (categories present, tokens per category)
and says so, rather than producing a chart from four vowels.

---

## 5. Four instruments, all in this chunk

All four fall out of the same slice-and-measure loop, and retro-fitting any of them means
re-recording.

- **POSITION** — F1/F2 in Lobanov space against the GA reference. Available for the 12
  referenced categories; the other ten report the speaker's own position with an honest
  "no published GA reference" target.
- **TRAJECTORY** — the 20%→80% movement. Whether a diphthong is a diphthong.
- **RHOTICITY** — F3, and specifically **F3−F2**. The highest-value single number here.
  American `/ɹ ɝ ɚ ɔɹ ɑɹ ɪɹ ɛɹ ʊɹ/` are defined acoustically by a steeply lowered F3
  approaching F2. Reference confirms it: `/ɝ/` sits at 298 Hz where every other vowel sits at
  546–1613 Hz. `/ɚ/` and the `/Vɹ/` sequences have no published mean of their own, so they are
  measured against the `/ɝ/` r-colouring target, and the surface says which.
- **DURATION AND REDUCTION** — three sub-measures, all from data already in the row:
  - **Tense/lax ratio**: `/i/`:`/ɪ/`, `/u/`:`/ʊ/`, `/eɪ/`:`/ɛ/`. GA reference ratios derived
    from `vowdata.dat` (men: 244/193 = 1.26, 237/193 = 1.23, 267/196 = 1.36). A learner who
    gets the formants right and the length wrong still sounds wrong.
  - **Pre-fortis clipping**: the same vowel before a voiceless coda against a voiced one.
    Coda voicing is read from the following Azure consonant via `phoneme_reference`. This ratio,
    not the consonant's own voicing, is the main cue separating *bat* from *bad*.
    **This one has no published target.** Hillenbrand's stimuli are all `/hVd/` — *had, hod,
    hawed, head, heard, haid, hid, heed, hoed, hood, hud, who'd* — every one a voiced coda, so
    the table contains no pre-fortis data at all. The target therefore comes from the **TTS
    voice measured through the same pipeline**, and that row names its reference explicitly.
    A ratio near 1.0 is the finding worth reporting regardless of target: it means the speaker
    produces no clipping, and their minimal pairs do not land.
  - **Reduction**: the speaker's **own** schwa centroid from unstressed syllables, then the
    mean Lobanov distance of unstressed vowels from it. 64 unstressed tokens per benchmark read
    is plenty. Under-reduction is invisible to every phoneme-level score Azure returns.

**STRESS PLACEMENT** is the composite of duration, intensity, F0 and vowel reduction. **All
four components are scored and reported separately.** A stress error reported as one number is
exactly the vague advice this project exists to delete.

---

## 6. The noise floor — why calibration is recorded twice

A vowel centroid moves between sessions from mic placement, room, posture, time of day and
warm-up, with no learning at all. Without knowing how big that movement is, the progress view
renders noise as progress — against a brief whose entire goal is *see that drilling it worked*.

The calibration passage is `progress_view.BENCHMARK_PASSAGE`, unchanged. It was chosen once
for exactly two consumers and its own comment says so: *"this chart, and the vowel-measurement
calibration read a later chunk needs."* 196 words, the full en-US vowel inventory in stressed
unreduced contexts, `BENCHMARK_COVERAGE` shipping as data with a test. Nothing new is written.

It is read **twice in one sitting, at least ten minutes apart, same mic, same room.** The app
enforces the gap from the two attempts' timestamps and refuses to compute a baseline from two
back-to-back reads. The per-vowel displacement between the two runs **is** the noise floor,
stored beside the baseline.

**Thereafter no movement smaller than that band may be reported as change.** It renders as
*within measurement noise*, every time — including when it is in the flattering direction.

**Before the first calibration read**, a five-second room check: record, assess through the
existing drill path, report the measured `snr_db_min`, and say plainly whether this room and
mic can support a vowel measurement at all. It costs ~5 s of the 18,000 s monthly allowance;
the spend is stated on screen before the button, per the project's standing preference.

---

## 7. Reference targets — one per surface, named on that surface, never averaged

- **The numbers** are measured against the published GA means (Hillenbrand et al. 1995, adult
  male and adult female sets kept separate), Lobanov-normalised the same way as the speaker's
  data — the reference's own 12 category means normalised by the reference's own mean and SD
  across those categories.
- **The ear** is trained on the Azure TTS voice measured through this same pipeline.
  `scripts/capture_baseline.py` gains a vowel mode and re-uses the synthesised passage.
- The two do not coincide, and imitating the voice can move a token *away* from the published
  mean while sounding better. Every surface says which reference it is using.
- `GA_REFERENCE_SET` is an explicit setting with **no default**. The pipeline offers the
  F0-derived suggestion and refuses to score position until it is confirmed — averaging the
  men's and women's sets, or guessing, would produce a chart that is confidently and entirely
  wrong.

---

## 8. The output contract

Every accent surface in this project — here and in every later chunk — renders findings as a
Markdown table with exactly these four headers, in this order. Implemented once in
`accent_view.py` as a row dataclass plus one renderer, with a test asserting the headers.

| Acoustic Feature | User Realization | Target Realization | Delta / Adjustment Needed |
|---|---|---|---|

- The feature column names the phoneme in **Azure's IPA**, plus the **Wells lexical-set
  keyword**, plus the metric. IPA alone is unreadable at a glance; the keyword alone is
  imprecise; the metric alone is not a sound.
- The two middle columns carry **numbers with units** — Hz, z-units, ms, dB, semitones — and
  the user column carries its **token count**. Never a score, never a percentage, never a
  verdict.
- The fourth column carries the **signed delta and the articulatory instruction it implies**.
  Both, in every row.
- **One row per measured feature, and a rejected token gets a row too**, with the rejection
  reason in the fourth column, so a thin table is visibly thin rather than silently short.

Worked shape for the fixture. Target values are the derived adult-male figures from §2, not
the brief's illustrative ones:

| Acoustic Feature | User Realization | Target Realization | Delta / Adjustment Needed |
|---|---|---|---|
| /i/ FLEECE — F2 (Lobanov z) | +1.12 (n=14) | +1.94 | −0.82 → tongue further front, lips spread |
| /eɪ/ FACE — F2 travel 20→80% | 18 Hz (n=7) | +140 Hz | −122 Hz → monothongised; glide, do not hold |
| /ɝ/ NURSE — F3−F2 | 980 Hz (n=15) | 298 Hz | +682 Hz → no r-colouring; bunch the tongue |
| /æ/ TRAP — duration before voiced vs voiceless coda | 1.04× (n=6/7) | TTS voice, same pipeline | Ratio ≈1 → no clipping; lengthen before /d z g/ |
| /ə/ unstressed — distance from own schwa centroid | 0.94 z (n=64) | 0.30 z | −0.64 → under-reduced |
| /ʊɹ/ CURE — F3−F2 | — (n=1) | /ɝ/ target 298 Hz | Rejected: one token, below the floor to normalise |

---

## 9. Order of work

Each step is its own commit on `claude/accent-measurement-engine-2c048c`, per `CLAUDE.md`.

0. **Multi-stage Dockerfile + parselmouth.** Builder stage compiles the wheel; the final image
   installs it, so no C++ toolchain ships. `make up` and `import parselmouth` verified in the
   container. **The measured build time is written into this plan file before step 1.** If it
   fails, stop and report — do not silently fall through to route two.
1. `scripts/build_vowel_reference.py` → generated `src/vowel_reference.py`, with provenance,
   the merger/fronting comments and the widened bands.
2. `src/acoustics.py` + tests against a **synthesised vowel of known F1/F2/F3** built in-process
   — the suite has no audio and must stay offline, so ground truth is generated, not committed.
3. `src/stress_lexicon.py` + the alignment test against the committed fixtures (99% expected).
4. `src/vowel_measure.py` — rejection rules, Lobanov (with the unbalanced-inventory test), the
   four instruments, the noise floor.
5. `src/db.py` — three tables, readers, writers; docstring correction.
6. `src/accent_view.py` — the four-column renderer, the chart frames.
7. `src/app.py` — Accent tab, room check, calibration flow, per-attempt table; audio
   persistence wired into `run_assessment_job`; `audio_utils` docstring correction.
8. `make check` green; `.env.example`, `README.md`, `pyproject.toml` mypy tier updated.
9. Memory bank: `techContext.md` gains an *Accent measurement* section, `history.md` gains the
   `implemented` row, and **`progress.md`'s `## Current focus` — which currently holds the
   pasted brief — is replaced with the real post-chunk focus.** Say if you want the section
   deleted outright instead of rewritten.

---

## 10. Verification

- `make check` — lint, mypy strict on every new module, and the offline suite. The suite must
  stay structurally unable to spend quota: no new module may open a socket, and `no_network`
  will catch it if one does.
- **Ground-truth test**: synthesise a source-filter vowel with known F1/F2/F3 and assert the
  Burg pipeline recovers them within tolerance. This is the only honest way to test a formant
  tracker offline.
- **Lobanov test**: a deliberately unbalanced token set, asserting the category-mean result
  differs from the token-pool result and that the pipeline produces the former.
- **Slicing test**: a synthetic WAV built to the fixture's offset structure, asserting energy
  lands in the sliced vowel and not in the silence — this is what settles the
  stream-versus-file offset question empirically.
- **Contract test**: the four headers, exactly, in order.
- **Live, in the browser, with a real microphone** — the part no test can answer: room check,
  two calibration reads ten minutes apart, a stored baseline, a stored noise floor, and a third
  read whose movement renders as *within measurement noise*. Cost: ~3 minutes of the 18,000 s
  allowance.

---

## 11. Stated limits, and what route two would have cost

- **The published reference covers 12 of 22 categories.** Ten report position and trajectory
  with no GA target. Said on the surface, not discovered later.
- **Absolute durations are never compared to the reference** — citation-form `/hVd/` against
  connected speech. Ratios only.
- **Pre-fortis clipping has no published target**, because every Hillenbrand stimulus ends in
  a voiced `/d/`. It is scored against the TTS voice through the same pipeline, and that is
  said on the row.
- **The reference is upper-Midwest, early 1990s.** `/ɑ/`–`/ɔ/` and `/u/` carry widened bands.
- **v0.11.0's manipulation surfaces are reachable but untyped** — `parselmouth.praat.call`,
  not a bound `Manipulation` class. Plan v0.11.0 accordingly.
- **Had route two won**, v0.11.0's modified-self-voice surface would have been deleted outright:
  scipy analyses and cannot resynthesise. Restoring it would have meant adding parselmouth
  anyway, or hand-writing PSOLA. Recorded here so the trade is legible if step 0 forces a
  reversal.
- **Every attempt recorded before this chunk is permanently unmeasurable.**
