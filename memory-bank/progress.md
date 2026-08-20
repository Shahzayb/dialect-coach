# Progress

## What works

**Assessment and diagnosis.** Record or upload a drill sentence or paragraph and get real
Azure scores down to the phoneme: the metric row, a script-versus-heard diff, colour-coded
reference text with score-on-hover, a card per flagged word naming the sound actually
produced in place of the target (`/θ/ → /t/`, never "your /θ/ scored 41"), the
syllable/stress line, and the delivery panel. "Hear it" / "Hear it slowly" synthesise a
native rendering, per word and for the whole text, with the user's own recording directly
beneath for back-to-back comparison. Every attempt is stored in local SQLite with both raw
API responses kept verbatim, and — since v0.10.0 — the recording itself, under a gitignored
`audio/attempts/`, so a changed normalisation scheme is a re-derivation rather than a request
that the passage be read again.

**Coaching.** Turns the diagnosis into a report: 2-3 sentences on the attempt, up to three
priority fixes (expected → produced, affected words, why it matters, articulation, minimal
pairs), stress-and-rhythm issues with a drill, and a five-minute practice plan naming
specific words from the attempt. The offline coach (`fallback_coach`) writes it for free on
every assessment; "✨ Improve this with Gemini" spends one free-tier call and replaces it in
place, captioned with what gets sent (the compacted analysis and reference text, never the
audio). Delivery faults (`UnexpectedBreak`, `MissingBreak`, `Monotone`) travel to the coach
as their own payload section and the report answers with a `Delivery` block naming the fault,
the span of words it damaged, and a drill — `fallback_coach` writes those from templates, so
the feature works with no API key.

**Record-and-assess surface.** Survives impatient use: `Assess` disables while a request is
in flight with a `Stop` button beside it, `↺ Reset` clears everything, `🗑️ Delete recording`
discards just the take. Words that scored 100 but were flagged anyway (a delivery fault on an
otherwise perfect word) collapse behind an expander; omitted words never do, since they carry
no score at all.

**Scores and error metrics.** `render_scores` shows a colour-banded Pronunciation headline
plus Completeness, then Accuracy/Fluency/Prosody as banded bars, against Azure's own
0-59/60-79/80-89/90-100 convention. `render_error_counts` adds a headline count row
(Mispronunciations, Unexpected break, Missing break, Monotone). Hovering a word shows its
score plus its phoneme symbols and their scores as two aligned rows. Content score
(vocabulary/grammar/topic) is out of scope — scripted assessment never returns it; Mode C
(unscripted) is not built.

**Progress view.** The Progress tab charts pronunciation, accuracy, fluency and prosody
across every stored attempt, plus recurring substitutions and flagged words. A fixed
benchmark passage (`progress_view.BENCHMARK_PASSAGE`, chosen once to also serve the vowel
calibration read) is the headline series; free practice on arbitrary text renders as a faint
unconnected cloud behind it, because plotting arbitrary texts measures text difficulty, not
the speaker. `BENCHMARK_COVERAGE` ships the "the passage covers both instruments" claim as
data with a test.

**Perception trainer and practice queue.** A **Today** tab is the app's entry point, carrying
at most three targets promoted from the user's own flagged history, each showing the counts
it was promoted on and the rule that retires it. A block is High Variability Phonetic
Training — 20 forced-choice trials on minimal pairs, cycling six en-US voices — scored
immediately with both words replayable; every accuracy figure sits beside its chance floor.

**Shadowing.** Press record, press play, speak with a synthesised reading of the passage; the
take is assessed as an ordinary Mode B attempt and tagged `shadowed`, so it can be set beside
a cold read of the same passage with the fluency/prosody delta named. A second mode, echo
(per-sentence clips), is a warm-up and deliberately never assessed. The comparison carries a
pre-registered claim: a shadowed read should score higher than a cold one, and that gap
should narrow over weeks as the pattern transfers — if it never narrows, the practice isn't
transferring and the surface says so rather than explaining it away later.

**First real shadow/cold pair landed 2026-08-20.** One shadowed benchmark read against the
cold read from the same session: Fluency **-0.7**, Prosody **-5.4** — the opposite direction
from the pre-registered claim. `progress_view.shadow_summary` correctly refused to call this
a pattern ("That is an observation, not a result — 3 pairs is the least this view will call
a pattern"), which is the mechanism working as designed, not a defect. Too early to read
anything into the direction; needs more pairs.

**Accent measurement (v0.10.0).** The Accent tab holds a room check, a two-read calibration
flow, a vowel chart against published General American means, and a stated noise floor. Every
assessment also renders a four-column table — `Acoustic Feature | User Realization | Target
Realization | Delta / Adjustment Needed` — with the phoneme in Azure's IPA plus its Wells
keyword plus the metric, numbers with units and a token count, and a signed delta carrying the
articulatory instruction it implies. Four instruments: vowel **position** in Lobanov space,
**trajectory** (20%→80% movement), **rhoticity** (F3−F2), and **duration and reduction**
(tense/lax ratios, pre-fortis clipping, distance from the speaker's own schwa centroid). The
pipeline refuses rather than guesses: it discards a token that's too short, unvoiced, or the
wrong vowel (with its own row saying why), refuses to normalise below eight categories, and
refuses a calibration built from two back-to-back reads. The noise floor — displacement
between two reads of the same passage ten minutes apart — means no movement smaller than that
band is ever reported as change, including in the flattering direction.

**Calibrated live for the first time on 2026-08-20**, from a real room check plus two
benchmark-passage reads (attempts 8 and 9, 13 minutes apart, 140/136 usable vowel tokens,
276 combined). Noise floor came out at **0.20 z median across 18 vowels**, ranging from
0.05 z (/ə/ commA) to 0.56 z (/ɚ/ lettER) — the SNR gate, baseline computation and vowel
chart all render correctly against a real voice, not just synthetic fixtures. The second
read was also the first live capture of the `Monotone` delivery fault on a real recording
(93 words across 2 stretches) with segmental scores essentially unchanged from the first
read (91/98/91 pronunciation/accuracy/fluency both times) — consistent with second-read
vocal fatigue/flattening rather than measurement noise, and the coaching report produced a
coherent "Flat intonation across the span" drill quoting the real longest run.

**Accent charts, resynthesis and a measured reference (v0.11.0).** The Accent tab carries six
chart-and-table pairs for any stored reading — rhoticity first, then the vowel quadrant with an
arrow per vowel from produced to target, diphthong trajectories, a semitone pitch overlay,
duration, and rhythm. Each table is the SAME rows the whole four-column table carries
(`vowel_measure.findings_by_instrument`), so the picture and the numbers cannot disagree.
Charts gate on **whether a baseline is stored, never on the mode**: with one, a three-word
drill plots as a single point carrying `n=1`, which is what makes the measure-drill-remeasure
loop possible at all. The arrow-to-instruction mapping is **data** in `vowel_reference`, one
hand-written entry per vowel, because F2 responds to lip posture as strongly as to tongue
advancement — a back-rounded vowel can no longer reach the table with a tongue instruction, and
a test enforces it.

**A second General American reference, measured by this project.** `src/model_reference.py` is
generated from the benchmark passage read by 16 sex-stratified en-US neural voices, each pushed
back through pronunciation assessment so both sides of every comparison carry offsets from one
segmenter. **21 of 22 categories in both sets**, against Hillenbrand's 12 — including six
r-coloured categories that previously had no target at all. Durations are connected speech and
so can be compared in milliseconds, the one caveat this table lifts. It complements
`vowel_reference` and is never averaged with it; `REFERENCE_PUBLISHED` and `REFERENCE_VOICE`
have existed since v0.10.0 and the second finally has something behind it.

**Resynthesis: the user's own voice with one thing changed.** Corrected pitch (their contour
replaced with the model's, scaled to their own median and range in semitones), corrected timing
(a DurationTier toward the target lengths) and corrected vowel (one vowel's formants shifted, a
third of the way, everything else bit-identical). Built on `parselmouth.praat.call` — the typed
`Manipulation` class does not exist. Every manipulation is capped and says so; the original
always plays first, labelled; and the surface states it is the user's own voice modified.

**The loop closes.** Ranked gaps travel to the coach as a `vowel_geometry` section alongside the
phoneme payload, both coaches answer with a bridging phrase (a sentence forcing the vowel in
varied consonant contexts, never a word list), and one click fills the practice textarea while a
second promotes it to the queue as a `vowel` target with its evidence.

## Not yet proven live

- **The benchmark's 30-day trajectory has one real point, not several** — the first live
  benchmark read landed 2026-08-20 (pronunciation 91.2), but a trajectory needs the series to
  accumulate over weeks; one point proves the pipeline, not a trend.
- **The perception trainer's graduation rule (90% across two blocks) has never fired** on real
  listening — two real blocks now exist (75%, then 80% on `/w/ → /v/`, 2026-08-20), still short
  of the 90% bar, so it's been run twice, not yet used to graduate anything.
- **`UnexpectedBreak` and `MissingBreak` have never been seen from Azure.** Only `Monotone` is
  confirmed from a real capture (`tests/fixtures/bad_delivery_capture.json`); the other two
  are covered only by a hand-built synthetic fixture. A reading that actually provokes one
  would close this — halting delivery with run-together sentences did not.

- **No human has used the v0.11.0 surfaces.** The charts, the resynthesis players and the
  one-click drill are proven against synthetic audio and an `AppTest` run, not against a voice.
  In particular nobody has yet confirmed that a corrected-pitch clip *sounds* like their own
  voice — the formant-preservation check says it should, and that is not the same thing.
- **The trajectory chart has never seen a real monophthongised diphthong.** The exit condition
  is proven on synthesised FACE vowels at 240 ms, where the distinction is 0.1 Hz against 72.
  Whether a learner's monophthongal /eɪ/ is distinguishable at real connected-speech durations,
  where boundary contamination is large, is **not** established — see the dead end below.

## Known issues

- **`ranked_gaps` and `findings_by_instrument` still score against Hillenbrand only.** Both
  call `reference_positions(reference_set)` with the default `source=REFERENCE_PUBLISHED`,
  while the rhoticity chart draws its targets from `model_reference`. For the men's set the
  chart marks /ɝ/ at 498 Hz and the table under it quotes 298 Hz, and the six other rhotics
  get a real per-vowel target on the chart against Hillenbrand's /ɝ/ in every table row. The
  measured reference was bought precisely to fix that; wiring it through changes what every
  table on the page says, so it is a decision rather than a patch.
- **The vowel geometry never reaches either coach.** Nothing in `src/` calls
  `fallback_coach.with_geometry` or `vowel_measure.ranked_gaps` — `compact()`'s
  `"vowel_geometry": []` is what `ai_coach.coach` and `fallback_coach.build` actually see. So
  `_checked_bridging_phrases` returns early, `bridging_phrases` iterates nothing, and
  `app.render_bridging_phrases` never renders: no bridging phrase, no one-click drill, no queue
  promotion. `vowel_reference.PRE_FORTIS_PAIRS` and `STRESS_SHIFT_PAIRS` are likewise written
  and read by no consumer. All of it is covered by tests, which is why it stayed invisible.
- **`_correct_worst_vowel` picks and shifts in raw hertz.** It ranks tokens by the hertz gap
  between the speaker's F2 and a model voice's mean, so vocal-tract length dominates the
  choice: a speaker whose tract is longer than the model-set average has every F2 low, and the
  "worst" vowel is whichever carries the largest anatomical offset. Every other ranking in the
  chunk works in z-units for exactly this reason.

**A real `.env` can silently undo the 180-second paragraph ceiling.** The default moved from
120 to 180 for shadowing, but a `.env` written before that change still says 120 and wins —
check the live file, not just `utils._DEFAULTS`.

**A shadowed read is only as clean as the headphones.** The model plays while the mic is
open, so on speakers Azure assesses a mixture of speaker and synthesiser; nothing enforces
headphone use or records which was used.

**A stress item can only be drilled, not checked** — Azure returns no stress marks, so a
stress-location task needs a pronouncing dictionary or another recording to grade against. A
CMUdict-backed task would close it (partially addressed by v0.10.0's `stress_lexicon`, not
yet wired into the trainer).

`app.py`'s word card shows the raw unsmeared duplicate phoneme that the coaching report
collapses. `ai_coach.py`'s `_client`/`_config`/`_call` lack type hints. `ai_coach.report_from_raw`
is unreachable from the running app. `app.py`'s `if entry.attempt_id:` treats id `0` as absent.

- **SQLite WAL is not readable across processes over the macOS bind mount** — verify row
  counts through the app's own History panel or logs, not by opening the DB file from another
  process while the app holds its connection.
- The multi-utterance merge is only covered by synthetic payloads; a captured recording has
  always come back as one utterance.
- The reference text sent to TTS is the script, not what was heard, so paragraph playback
  never lines up word-for-word with a recording that omitted words.

## Dead ends

- **Reading `word["ErrorType"]` from the Azure payload.** It sits inside the word's
  `PronunciationAssessment`, so the top-level read silently returns nothing. The docs' flat
  REST example is what misleads here.
- **`enable_content_assessment_with_topic`** is not in SDK 1.51.1 despite the master plan
  citing it for Mode C. Do not plan Mode C's content scoring around it without checking first.
- **Planning v0.11.0 against a typed `parselmouth.Manipulation` class.** It does not exist —
  the bindings are Sound, Pitch, Formant, Intensity, Spectrum. PSOLA resynthesis, PitchTier
  replacement and DurationTier time-scaling are reachable only through the untyped
  `parselmouth.praat.call(...)`. Checked in the 0.4.7 source tree, not assumed.
- **Fetching the Hillenbrand vowel data from its canonical URL.** `homepages.wmich.edu` now
  presents a certificate for `CN=redirect.wmich.edu`, so every fetch fails verification. Use
  the `santiagobarreda/hillenbrand_et_al_1995` mirror, packaged with the author's permission.
- **Measuring a diphthong's glide from Azure's phoneme boundaries in connected speech.** The
  number exists and does not mean what "F2 travel 20→80%" says. Across the 16-voice reference
  capture, only twelve /eɪ/ tokens in the men's set clear a 90 ms floor, from three word types,
  and each one's 80% analysis window lands in a following nasal or the next word's vowel — on
  "same" it reads F1 240 Hz / F2 1285 Hz, a nasal murmur. **It cannot be gated on amplitude**:
  that window measures −17.4 dB against −16.4 dB at the vowel's midpoint. `model_reference`
  therefore publishes no at20/at80 at all. A passage written with clean diphthong contexts, or
  a better source of boundaries, would be a re-derivation over the stored renderings rather
  than a re-spend.
- **Asking the Speech SDK which voices are children.** It reports no age at all:
  `en-US-AnaNeural` comes back `gender=Female, voice_type=OnlineNeural`, identical in shape to
  an adult woman. The exclusion list in `native_model.NON_ADULT_VOICES` is hand-maintained
  because there is nothing to query.
- **Testing a formant tracker against a three-formant synthetic vowel.** It under-determines
  the five-pole model Praat fits below a 5 kHz ceiling, producing a spurious ~1700 Hz-wide
  peak between F1 and F2. The test signal needs as many resonances as real speech.
- **Praat's `Extract part` with an EMPTY time range.** `Extract part 0.0 0.0` returns the
  whole sound, not nothing — verified against the pinned 0.4.7. Any splice built by extracting
  a before/middle/after triple has to skip the parts that do not exist, or a span touching
  either edge of the clip silently concatenates the entire recording back in.
- **Negating a delta because the quantity is a difference rather than a formant.** F3−F2's
  delta is `target − produced` exactly like every other delta in `vowel_measure`, so it feeds
  `instruction_for` unchanged. The flip is easy to argue yourself into and it inverts the
  advice: it told a speaker whose r-colouring had not arrived to release the bunching. Both
  directions of both instruments now have a named test.

## Standing preferences

- Project memory lives in this repo's `memory-bank/`, per `.claude/skills/memory-bank/SKILL.md`.
- Take one chunk of work at a time, plan it in its own dated file, then implement only that.
- **Never install anything globally.** Docker is the preferred run path; a project-local
  `.venv` is the acceptable alternative.
- Commit in chunks as work lands, not one commit at the end.
- **Python over `.sh` for anything with branching/conditionals.** Trivial one-liners can stay
  as a Makefile recipe; real logic goes in a `scripts/*.py` file.
- Verify library versions and API surfaces against current sources rather than recalling them.
- **Build parsers against a captured payload, not documentation** — the real Azure response
  differs from the documented shape in ways that fail silently rather than loudly.
- **Verify SDK surfaces by introspecting the installed package**, not from docs or memory.
- Spend API quota deliberately and say so, not incidentally. **`OFFLINE_MODE` is the only
  thing standing between a capture script and a real charge** — treat unsetting it as the
  decision to spend.
- **The app runs locally. Deploying it is not a goal** — treat hosting as an option left open
  for someone else, never as a requirement to design around. See `techContext.md`.

## How the direction has evolved

- 2026-08-17 — Docker became the primary run path mid-implementation, to keep the host clean
  and pin the Azure SDK's native dependencies alongside the Python version.
- 2026-08-17 — A local database came into scope. The brief previously ruled out stored history
  entirely; SQLite is the chosen engine, on the user's instruction.
- 2026-08-18 — What the database stores is settled: both raw API responses, verbatim. The
  monthly usage meter is derived from that same table, so `.usage.json` and
  `BUDGET_STATE_PATH` were dropped rather than kept as a second store that could disagree.
- 2026-08-17 — Hosting dropped as a goal: the tool is for local use. The original Hugging Face
  Space target and its derived requirements (ephemeral-filesystem handling, cold-start wake
  time, a private Space) are gone; the deploy artefacts stay only as an option.
- 2026-08-20 — **Audio is kept on disk**, acting on permission granted 2026-08-19. Not so a
  measurement can be deferred — it still runs inside the assessment request — but so it can be
  **re-derived**: normalisation schemes and reference tables will change, and re-deriving must
  never require a re-recording. Two code docstrings still asserting the lifted "no stored
  audio" rule were corrected; `projectbrief.md` was right all along.
