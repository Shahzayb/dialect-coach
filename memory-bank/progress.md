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

- **The one-click drill has not been used by a human.** The charts and the resynthesis
  players now have (see below); the drill is still proven only against synthetic audio and an
  `AppTest` run.
- **The trajectory chart has never seen a real monophthongised diphthong.** The exit condition
  is proven on synthesised FACE vowels at 240 ms, where the distinction is 0.1 Hz against 72.
  Whether a learner's monophthongal /eɪ/ is distinguishable at real connected-speech durations,
  where boundary contamination is large, is **not** established — see the dead end below.

## Known issues

**The Accent tab can render one reading's acoustics under another reading's label.** Caught
live on 2026-08-20, minutes after attempt 12 was stored. The "Which reading?" selector showed
`#10 · 138 tokens · Each morning I read these same words out` while the rhoticity table below
it showed `/ɑɹ/ START 805 Hz (n=2)` and `/ɝ/ NURSE 866 Hz (n=1)` — **attempt 12's** figures,
identical to the ones its own bridging phrases quoted. Selecting each attempt explicitly
proves the pairing: #10 is 917 Hz (n=3) / 772 Hz (n=5), #12 is 805 Hz (n=2) / 866 Hz (n=1).
The mismatch **survived switching tabs away and back**; it corrected only once the selector
was actually operated, so this is not a one-frame paint glitch but a state that persists
silently for as long as the widget is left alone.

`measurement_for` is a clean query on `attempt_id` and is not at fault — whatever id the
backend resolved, it fetched correctly. The disagreement is between what
`st.selectbox("Which reading?", options=list(labels), key="accent_chart_attempt")`
(`app.py:2043`) displays and what it returns on the render where `options` has just grown.
There is no `index=`, so the default is positional, and a new attempt is prepended: the
newest reading takes position 0, which the previous render's position 0 held. Do not fix this
by reasoning about Streamlit's widget internals from memory — reproduce it first by storing a
new attempt with the Accent tab already rendered.

**Severity is high and the failure is silent.** This is the one surface whose entire purpose
is comparing readings over time, it misattributes acoustics between them, and it fires exactly
when a new attempt lands — the moment the user goes to look. Both halves of the screen look
plausible on their own. The one visible tell is arithmetic: the label carries the attempt's
`accepted` token count (138) while the table reported n=2 and n=1 per category, which a
138-token read cannot produce. That invariant is checkable — the panel could refuse to draw
when the selected label's token count and the loaded measurement's disagree, in the same
spirit as every other refusal in `vowel_measure`.

**A repeated word is reported as a phoneme substitution, and the advice is wrong.** Attempt
12 (2026-08-20): the speaker stumbled and said "Wednesday" twice. The script-versus-heard
diff handles it correctly, showing the second one italicised as heard-but-not-in-script. The
flagged-word card for the same word does not — it renders **`wednesday — 6`** with
**`/eɪ/ → sounded like /w/`**, because the `/eɪ/` ending the first "Wednes-day" aligned
against the `/w/` onset of the second. Azure's payload is being read faithfully; the card
turns it into "your /eɪ/ came out as /w/", which is a substitution that never happened, on
the lowest-scoring word of the attempt (6/100). Two surfaces then tell contradictory stories
about one word, and the card is the wrong one — against this project's own rule that a word
Azure heard *differently* matters more than a low phoneme score. A disfluency needs to be
recognised as a disfluency before `weakest_phoneme` is allowed to describe it; the diff
already knows, so the signal exists.

**The error-count badges put two different units side by side.** `render_error_counts`
(`app.py:1067`) counts words for every badge, but "2 Mispronunciations" means two
independently wrong words while "28 Monotone" means **one** flat stretch that spans 28 words
— which is exactly how the Delivery panel below it words the same fault ("across the span",
one confidence figure). Read together, the row implies the monotone problem is fourteen times
the size of the articulation problem. Because prose comes in spans, the monotone badge will
always be the largest number on the row and is structurally the least informative one.
`1 monotone stretch (28 words)` would say the true thing.

**The practice queue never rotates — one target monopolises the block slot forever.** Found
by hand on 2026-08-20, after the user said they were tired of being given `/w/ → /v/`. Today
renders a single block from `trainable[0]` (`app.py:3018`), `due()` sorts on
`(active?, next_due)` and never on `last_seen` (`practice_queue.py:519`), and `next_due` is
written back **only when the state changes or the item regressed** (`app.py:3458`). A block
that leaves the target active — anything under the 90% graduation bar — writes `last_seen`
alone. So `/w/ → /v/` still carries the `next_due` it was *added* with
(2026-08-19T18:05:45Z) after two completed blocks at 75% and 80%, and a stable sort pins it
at index 0 permanently. `/ɑ/ → /ɔ/` and `/i/ → /ɪ/` cannot be reached until it graduates,
even though both carry more evidence than it does (4 tokens each against 2).
**`last_seen` is written in two places and read in none** — it is the field this wants.
Two candidate fixes: drop the condition at `app.py:3458` so an active item's `next_due`
advances to now and it falls to the back; or order `due()` by `last_seen`, never-seen first.
The second is preferable — under the first, ordering still rests on a timestamp whose meaning
is "due now" for every active target. Left unfixed deliberately on 2026-08-20.

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
