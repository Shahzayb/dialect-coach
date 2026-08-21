# Progress

## What works

- **The review pass on v0.11.0, and what it changed.** Eight findings, five of them wrong
  advice or dead wiring rather than style. All are fixed and each carries a test that fails
  against the bug it covers:
  - The rhoticity instruction was inverted — a speaker whose r-colouring had not arrived was
    told to release the bunching. The 150 Hz threshold beside it is now
    `RHOTICITY_TOLERANCE_HZ`, applied symmetrically.
  - `ranked_gaps` read a missing `f2_travel_hz` as a 0 Hz glide, manufacturing a top-ranked
    "monophthongised" drill out of a measurement `_trajectory_findings` had refused.
  - `corrected_vowel` spliced the whole recording back in for a vowel at either edge of the
    clip, because Praat reads an empty `Extract part` range as the whole sound.
  - `corrected_pitch` re-ran the pitch tracker per contour point: 109 s on a 60 s paragraph,
    now 0.37 s.
  - `pitch_frame` never quantised the warped model times, so the "averaged across voices"
    line aggregated nothing and zigzagged between them.
  - **Rhoticity is now scored against `model_reference` and only in hertz.** `_hertz_reference`
    is the rule: z-comparisons stay on the published table because a z-score is relative to
    the inventory that produced it, and hertz comparisons may use the table that actually
    covers all seven r-coloured categories. `ranked_gaps` also stopped skipping every vowel
    with no published mean, which had limited the rhoticity ranking to NURSE.
  - **The vowel geometry reaches both coaches.** `app.geometry_gaps` derives the ranking once,
    inside the session-cache guard and before the coach branch, and hands it to
    `ai_coach.coach` and `fallback_coach.build` alike. Verified end to end against a seeded
    baseline: two trajectory gaps in, two hand-written bridging phrases out.
  - `_correct_worst_vowel` ranks in z and maps the target back through `Normaliser.hz`, so the
    vowel it picks is not whichever one carries the largest vocal-tract-length offset.

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
(vocabulary/grammar/topic) arrives with Mode C, from Gemini rather than Azure — see below.

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

**Why that pair went the wrong way — reported 2026-08-20.** The speaker cannot listen and
speak at the same time, and said so unprompted the first time they used the surface. Slowing
playback to 35% did not rescue it: echo is workable where simultaneous is not, at either
speed. The read was on headphones, so Azure heard no synthesiser and the −5.4 prosody is a
clean measurement of a degraded delivery rather than a mixture. Simultaneous shadowing is
therefore harder for this speaker than a cold read — a mechanism for the sign being negative,
not an excuse for it. Still one pair: `shadow_summary`'s 3-pair bar stands, and the
pre-registered claim is on notice rather than refuted.

**Third-read fatigue is ruled out as the cause, by the two reads before it.** Attempt 10 was
the third read of the benchmark inside 23 minutes, so the obvious rival explanation is that
the voice was simply tiring. The data rejects it — the two cold reads either side of that
window are flat, and the drop arrives only with the shadowing:

| attempt | time | pron | accuracy | fluency | prosody |
|---|---|---|---|---|---|
| 8 (cold) | 11:58 | 91.2 | 98.2 | 91.1 | 83.4 |
| 9 (cold) | 12:10 | 91.1 | 98.0 | 91.4 | 83.5 |
| 10 (**shadowed**) | 12:21 | 88.2 | 95.9 | 90.7 | **78.1** |

Fatigue predicts a gradual decline across all three; prosody instead held at 83.4 → 83.5 and
then fell 5.4. Note also that **accuracy fell 2.1 on the shadowed read** (98.0 → 95.9). The
Progress surface cannot show that: `SHADOW_METRICS` compares fluency and prosody only, on the
stated grounds that "shadowing trains delivery, not articulation". That rule assumes shadowing
is neutral on articulation, and this pair is evidence it can be negative — worth revisiting if
later pairs repeat it.

**The ramp exists and nothing puts you on it.** The mode radio defaults to `SIMULTANEOUS`, so
a first-time shadower meets the hardest combination — speak-along, full speed — with no ramp,
though `SLOW_NOTE` already calls slow playback "the on-ramp" and echo is the gentler format.
The deeper bind: **echo mode has no recorder at all** (`render_shadow` calls `st.audio_input`
only in the simultaneous branch), so the mode this speaker can sustain captures nothing, while
the mode that produces data is the one they have now failed at twice. The echo track is built
clip-by-clip with each gap sized to its own phrase, so per-phrase segmentation of an echo
recording is feasible if it is ever wanted. Direction deliberately left open on 2026-08-20 —
the candidates were re-specifying the acceptance test around cold-read transfer, per-phrase
assessment, or publishing only the metrics the format does not corrupt.

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

**Confirmed by ear on 2026-08-20.** In a manual session the six chart-and-table pairs rendered
against attempt #10 (a real read, 138 tokens) with no console errors, the model's reading of
that passage was captured live, and corrected pitch played back. The open question — whether a
corrected-pitch clip still *sounds* like the speaker — is answered yes, by the speaker. That
was the one claim the formant-preservation check could not settle.

**The loop closes.** Ranked gaps travel to the coach as a `vowel_geometry` section alongside the
phoneme payload, both coaches answer with a bridging phrase (a sentence forcing the vowel in
varied consonant contexts, never a word list), and one click fills the practice textarea while a
second promotes it to the queue as a `vowel` target with its evidence.

**Mode C — unscripted speech, two-pass diagnosis, and content scores (v0.12.0).** The third
recording mode, and the only one that measures the register this project is about: generating
language and monitoring pronunciation at the same time. Pick a prompt — an interview answer, a
call, explaining something technical — talk for three or four minutes, and get accuracy, fluency
and prosody down to the phoneme, plus vocabulary, grammar and topic.

- **It sends the audio twice, on purpose.** Unscripted assessment (an empty `referenceText`)
  runs on a weaker speech-to-text model than standard Azure STT, so this follows Microsoft's own
  recommendation: transcribe with standard STT first, then run a *scripted* assessment against
  that transcript. A phoneme diagnosis against a wrong transcript is worse than no diagnosis,
  because it confidently blames the wrong sounds. `UNSCRIPTED_TWO_PASS` turns it off; the surface
  then says out loud that the transcript came from the weaker recogniser.
- **The transcript is shown above everything derived from it.** Every sound named below it was
  scored against those words, and the reader is the only one who can notice a wrong one.
- **No completeness score, ever.** There is nothing to be complete against, and the real capture
  proves the suppression is load-bearing rather than cosmetic: pass 2 IS a scripted assessment,
  so Azure does return a `CompletenessScore` — computed against the machine's own transcript, so
  ~100 by construction. It is a number measuring the recogniser agreeing with itself.
- **No miscue diff either**, for the same reason: a diff against the machine's transcript reports
  one recogniser disagreeing with another as a speaker error. Repetitions are still caught, from
  adjacency alone — free speech is where stumbles actually happen.
- **Content scores come from Gemini, not Azure, and every surface says so.** Vocabulary, grammar
  and topic on Azure's own 0-59/60-79/80-89/90-100 banding, with the headline stated as the plain
  mean of the three because Azure never published its weighting. Behind a button, like the Gemini
  coach, so the spend stays deliberate; the verdict is stored so re-rendering never means
  re-asking. Every unavailability renders **with its reason** — offline, no key, 429, a transcript
  under Azure's own 50-word / 3-sentence floor — never a blank and never a scripted number
  standing in.

**Verified live on 2026-08-21, and the budget claim came out exact.** A 35.7 s synthesised
monologue through the real two-pass flow: `passes_for(Mode C)` returned 2 and the pre-flight
priced **71 s before the first pass was sent**; the run reported `attempts=2`; the meter moved
0 → 71 s, **exactly 2.00× the clip**. Pre-flight and post-hoc agree. Scores came back
93.2/95.2/92.6/89.7 with `completeness: None`, and Gemini scored the content 88 vocabulary /
85 grammar / 95 topic, quoting the speaker's own words in its note. `tests/fixtures/
sample_azure_unscripted.json` is that capture, verbatim.

**Read and spontaneous speech are kept apart, structurally.** Spontaneous speech is not read
speech measured under harder conditions — it is a systematically different population: speakers
hyperarticulate when reading and reduce far more when generating language, so vowels centralise,
durations shorten and unstressed syllables collapse further toward schwa. Every one of those is
something v0.10.0 measures. The `read`/`spontaneous` tag has been written on every attempt since
then; this is what made it load-bearing.

- **One current baseline per style.** `save_baseline` supersedes only within its own style, so a
  spontaneous calibration no longer silently retires the read baseline every Mode B reading is
  normalised against. `current_baseline(conn, style=...)` requires the style and never guesses.
- **The LPC ceiling stays style-agnostic** (`any_current_baseline`): it tracks vocal tract length,
  not register, and a second sweep would produce formants incomparable with everything stored.
- **`plot_gate` refuses a style mismatch rather than warning above the chart.** It used to draw
  the numbers with a caveat over them, which is not enough — the numbers still looked comparable
  against last month's, and a caveat is the first thing a reader skips.
- **A spontaneous baseline is built from the same PROMPT twice**, ≥ `CALIBRATION_GAP_MINUTES`
  apart. The content is not identical, so the floor carries content variation on top of
  measurement noise and comes out **wider than the read floor** — an upper bound, which is the
  conservative direction: it can only refuse to call something progress until the change is
  bigger. The surface says so.
- **Mode-aware per-vowel token floor.** A drill token at n=1 is a deliberate probe of a sound the
  speaker chose to work on; a lone free-speech token is whichever vowel the sentence happened to
  reach for. Read speech keeps `minimum=1` against a stored baseline (that is what makes the
  measure-drill-remeasure loop possible); spontaneous readings are held to
  `MIN_TOKENS_PER_CATEGORY`, and a category below it is refused rather than drawn as a lonely
  confident dot.
- **The four-column table carries the style beside the token count** — `873 Hz (n=6, read)`. The
  one addition the v0.10.0 contract takes, because in this mode the number cannot be interpreted
  without knowing which population produced it.

**The four defects the 2026-08-20 manual session found are fixed, and one of them was
diagnosed wrong when it was filed.** Each carries a test that fails against the bug it covers.
Shadowing (the fifth) is untouched and still open.

- **The Accent tab's reading/label mismatch was never about "the render where `options`
  grows".** Verified against the installed streamlit 1.61.1 rather than recalled: `st.rerun()`
  builds `RerunData(...)` with **no `widget_states`**, and the runner only restores the
  browser's values `if rerun_data.widget_states is not None`; a `RerunException` is **not** a
  premature stop (`exec_code.py` documents `premature_stop` as "False for RerunExceptions"), so
  `_remove_stale_widgets` still runs after one. Any `st.rerun()` in Today or Practice ends the
  script before the Accent tab is reached — tabs render in order — so its selector is stale on
  that pass and its value is deleted from `_new_widget_state` and `_old_state` alike. The next
  full run re-registers the widget from scratch, and with no `index=` the positional default is
  `options[0]`, whichever reading is newest. Being a first registration it sets neither
  `value_changed` nor `value_needs_reset`, so `set_value` never reaches the browser and it goes
  on painting the label it already had. **It had been firing on every early-terminated rerun
  all along** — no new attempt is needed, which the reproduction shows (`assert 2 == 1` on a
  bare truncating rerun). It only became visible when the newest attempt stopped being the one
  already selected. The default is now resolved by identity from a plain session key that
  stale-widget cleanup does not touch, the same shape `render_shadow_offer`'s passage picker
  already used; the chosen reading sticks, and a new reading arrives at the top of the list
  without taking the selection.
- **`vowel_measure.label_matches_measurement` is the invariant as a refusal.** A label claiming
  138 tokens above a table reporting n=2 is arithmetically impossible, and the panel now
  refuses to draw rather than leaving it to be noticed. It is a **tripwire, not the detector**:
  for one attempt id the two counts agree by construction, since `measured_attempts`' SQL
  filters the same `accepted = 1` flag `Measurement.accepted` reads back. It fires if the label
  and the measurement are ever resolved from different ids or different snapshots again.
  Beside it, a caption built from the loaded measurement — `Plotting #1 · 36 accepted tokens ·
  2026-08-20 11:58` — states what was drawn independently of the widget.
- **The practice queue rotates on `last_seen`.** It was written on every finished block and
  read by nothing; `due()` sorted on `next_due`, which reads "now" for every active target by
  design and is never rewritten by a block that leaves the target active, so the sort key was a
  constant and a stable sort pinned index 0 forever. Never practised now sorts first, then
  oldest; `next_due` stays the gate on what is due at all and drops to a tiebreak, which is
  what still orders graduated reviews against each other. The preferred fix of the two
  recorded, for the stated reason: dropping the write-back condition would have left ordering
  on a timestamp meaning two things at once.
- **A repeated word is a stumble, not a substitution.** This was wiring, not detection — one of
  the pair is already an Insertion, labelled by `enableMiscue` on the drill path and derived by
  `_diff_miscue` on the paragraph path. `_mark_repetitions` marks **both** occurrences, because
  which one difflib calls the insertion is arbitrary (it tags the first) while the phonemes that
  get misread belong to whichever one Azure scored badly. Suppressed on both surfaces:
  `weakest_phoneme` makes no substitution claim and the card says the word was said twice
  instead, and `fallback_coach._substitutions` returns nothing so the pair never enters
  `observed_pairs` — the only list a report may discuss. The word still reaches the coach as a
  flagged word with its score: the stumble is worth reporting, the invented substitution is not.
- **The headline row counts monotone stretches, not the words inside them.** `1 · Monotone
  stretch (28 words)`, from `delivery_faults`' own `runs`, so the row and the Delivery panel
  below it cannot disagree about how many stretches there were. A break stays a word count on
  purpose — it is a point event at a word, not a span. The badge row moved into a
  Streamlit-free `error_count_badges` so the units are assertable directly.
- Also: the intonation overlay's `1 model voice(s)` hedge is pluralised properly.

**Verified live in a browser on 2026-08-21**, against two seeded readings in an otherwise
empty database: an explicitly chosen reading (#1) survived an `st.rerun()` raised from the
Today tab, with the selector label, the `Plotting #1` caption and the rhoticity table all
naming the same reading, and no console or server errors. The two defects that need a real
voice — the mismatch fired by a live assessment, and a real stumbled word — are covered by
`AppTest` and by hand-built payloads rather than by a recording, since neither can be produced
from this side of the microphone.

**The four-rung practice ladder (#39, in progress).** `sound → word → sentence → paragraph`,
with a thing resolved only once it survives at the rung above (#42). Resolution is **measured
only** — inside the native band AND past the speaker's own noise floor — so the three exits are
measured, bailed and reopened, and there is no way to mark something done by hand.
`src/ladder.py` holds the rungs, spans, both bars and the verdict; `src/ladder_practice.py`
assembles a unit; `src/ladder_reference.py` is generated.

- **The 16-voice reference had been lost and was re-captured on 2026-08-21.** Only 3 renderings
  (one voice) remained in the database and only its generated `model_reference.py` survived —
  which is exactly why the artefact is committed and the audio is gitignored. The re-capture
  cost 14,625 TTS characters (6,118 → 20,743) and 961 STT seconds (1,403 → 2,364), landing on
  its own estimate. The captures carry the `[tts rhythm baseline capture]` marker, so
  `spoken_attempts` already keeps them off the Progress cloud.
- **Arrival bands are derived locally, no Azure**: 196 word bands, 14 sentence bands, 3
  paragraph metrics, from stored payloads and stored WAVs.
- **Azure's `ProsodyScore` is deliberately not a metric.** Across the sixteen voices it spans
  89.50-90.73 — SD 0.40 on a 0-100 scale, against 3.7% for nPVI and 19.9% for pitch range. That
  measures how uniform the TTS is, not how far native talkers sit apart. It would also have
  jammed the ladder: a span resolves only when every judgeable metric clears, so a band no real
  speaker can reach would block the paragraph rung and every sentence beneath it.
- **nPVI is a passage-scale measure.** `rhythm.MIN_PAIRS` is 20, so only 5 of 14 benchmark
  sentences carry a rhythm band; the rest are judged on pitch range and terminal fall alone,
  which `Verdict.resolved` handles by requiring only the judgeable metrics to clear.
- **The native leg's voice is matched on median F0, not sex.** Sex-matching needs the live
  roster; the stored medians are cleanly bimodal (104-150 Hz against 179-224 Hz), so pitch
  matching sex-matches on its own and discriminates within a sex too.
- **Two defects the real data found, neither visible against fixtures.** Strict
  position-for-position sentence mapping refused an entire recording over one inserted word
  (GuyNeural) — now aligned with `difflib`, since a human stumbles far more than a synthesiser.
  And `bands_for` keyed on index alone handed sentence 0 of ANY text the benchmark's sentence-0
  numbers; it now verifies the passage against the stored sentence text.
- **Verified live in a browser on 2026-08-21** against the real database: all three legs played
  for a real sentence — mine 6.10 s, Brian 4.59 s, corrected 6.10 s — the cap notice fired, the
  corrected clip was the sentence and not the 62 s recording, dropping the unit returned to
  Today with the queue intact, and no console errors.

**Banking, keeping and the queue are wired.** A take is banked on an explicit button that
prices the spend first and stores the attempt tagged `rep`; keeping a unit writes a ladder
target that Today renders through `grade_ladder`, so the card and the rule cannot disagree.
Ladder kinds are not `promotable`, so they never consume one of the three perception slots.
Corrected vowel lengths are reachable at rung scale against the MODEL duration table.

**Still open on the ladder.** **No human has spoken into the "say it again" path** — the local
verdict is proven against synthetic clips and the real stored reads, never against a live
repetition, and nothing has been banked. The automatic reopen is tested but has never fired on
real data, because that needs a target resolved at one rung and then failing at the rung above.
`corrected_vowel_in` exists and is tested but has no button. And the **by-ear check at word
scale is unanswered**: a corrected word was produced from a real read on 2026-08-21 ("these",
0.41 s, uncapped, containment verified against the 82.1 s recording) and handed to the speaker,
but whether it still sounds like them at that length is theirs to settle. Pitch correction was
confirmed by ear over a whole paragraph on 2026-08-20; a word is a much shorter span and is
where a manipulation is most likely to sound artefactual.

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
  **Third failed attempt on 2026-08-20** (attempt 12, "Workplace explanation", a passage
  chosen for its long subordinate chain, read with a real stumble in it): both still 0, while
  `Monotone` came back 28. Three deliberate attempts is enough to stop treating this as bad
  luck and start treating it as a property of Azure's scoring — the synthetic fixture may be
  the only thing that will ever exercise these two branches.

- **`Monotone` is now confirmed twice from real captures.** Attempt 12 returned one flat
  stretch of 28 words at `SyllablePitchDeltaConfidence` 0.25 — about a third of the passage —
  and `fallback_coach` wrote a coherent drill quoting the real span. The first live capture
  was attempt 9. Both were second-or-later reads in a sitting.

- **No human has spoken into Mode C yet.** The two-pass flow, the meter arithmetic, the content
  scoring and the fixture were all verified against a **synthesised** monologue on 2026-08-21 —
  a neural voice pushed back through the pipeline, which is the same trick `model_reference`
  uses. That proves the plumbing, the billing and the parsing. It does not prove the thing Mode C
  exists to measure: a synthesiser does not hesitate, does not reduce under cognitive load and
  does not centralise its vowels while deciding what to say next. The register confound is
  precisely what a synthetic voice cannot exercise.
- **No spontaneous baseline exists**, so every Mode C accent surface currently refuses. Building
  one needs the same prompt recorded twice, at least `CALIBRATION_GAP_MINUTES` apart — which is
  partly what the first real Mode C session is for.
- **The one-click drill has not been used by a human.** The charts and the resynthesis
  players now have (see below); the drill is still proven only against synthetic audio and an
  `AppTest` run.
- **The trajectory chart has never seen a real monophthongised diphthong.** The exit condition
  is proven on synthesised FACE vowels at 240 ms, where the distinction is 0.1 Hz against 72.
  Whether a learner's monophthongal /eɪ/ is distinguishable at real connected-speech durations,
  where boundary contamination is large, is **not** established — see the dead end below.

## Known issues

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
- **Azure's content assessment, by any route.** `enable_content_assessment_with_topic` is not in
  SDK 1.51.1 — and the reason is not that it was renamed. **Microsoft retired content assessment
  at Speech SDK 1.46.0** and this project pins 1.51.1. Established four ways on 2026-08-21, none
  of them from memory:
  - `dir(PronunciationAssessmentConfig)` in the built image returns exactly `apply_to`,
    `enable_prosody_assessment`, `nbest_phoneme_count`, `phoneme_alphabet`, `reference_text`,
    `to_json`. `PronunciationAssessmentResult` exposes no vocabulary/grammar/topic;
    `PropertyId` carries no content entry.
  - A string scan of the native `.so` files finds only `referenceText`, `gradingSystem`,
    `granularity`, `enableMiscue`, `enableProsodyAssessment`, `phonemeAlphabet`,
    `nbestPhonemeCount` — no `contentAssessment`, no `contentTopic`, at all.
  - Microsoft Learn says so in as many words: *"Content assessment (preview) is retired from
    Speech SDK versions 1.46.0 and later."* Their documented replacement is a chat model given a
    grading rubric that returns `{"vocabulary","grammar","topic"}` 0-100.
  - **And the JSON route was tried live, once, and answered no.** The config DOES still carry
    unknown keys — `PronunciationAssessmentConfig(json_string=...)` round-trips
    `enableContentAssessment` / `contentTopic` back out of `to_json()` untouched, so the client
    can still send them. A real unscripted call on 2026-08-21 sent exactly
    `{"referenceText":"", ..., "enableContentAssessment":true, "contentTopic":"my hobby"}` and
    came back with `PronunciationAssessment` keys `AccuracyScore, CompletenessScore,
    FluencyScore, PronScore, ProsodyScore` and nothing else. **The service ignores them.**
  The flag that sends them (`UNSCRIPTED_CONTENT_PROBE`) and the script that uses it
  (`scripts/content_probe.py`) are kept so the question can be re-asked cheaply if Azure ever
  changes its mind, and `speech_analyzer.azure_content_scores` still reads the fields if they
  ever appear. Content scores in this project come from Gemini against Microsoft's own
  published rubric, and every surface says so rather than presenting them as Azure's.
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
