# Progress

## Current focus

The measurement engine behind the project's headline goal: sounding less accented. Azure's
diagnosis is CATEGORICAL — this phoneme is /θ/ or /t/, scored 0-100 — and accent is CONTINUOUS. A
vowel scoring 78 while drifting toward the target and one scoring 78 while drifting away look
identical to Azure. This chunk measures the gradient part: where vowels actually sit, how they
move, how long they last, how loud they are, and how far the unstressed ones reduce. Needs the
timing data from v0.6.0. Costs two calibration recordings, roughly 90 seconds each — two, not one,
and the reason is in THE NOISE FLOOR below.

SETTLE THE DEPENDENCY FIRST, BEFORE ANYTHING ELSE IN THE CHUNK, and record the measured build
time in the plan file whichever wins:
- praat-parselmouth 0.4.7 ships NO linux-aarch64 wheel. cp312 wheels exist for manylinux x86_64
  and i686 and for macOS arm64, but not linux arm64. This project's container is aarch64 Linux on
  Apple Silicon, so pip install there downloads the source tarball and compiles Praat from C++:
  a large build, a C++ toolchain in the image, a much bigger layer. Route one is a multi-stage
  Docker build compiling in a builder stage so the toolchain never ships in the final image.
  Praat's Burg formant analysis is the reference implementation the published tables were built
  with. Note the inversion: on an Apple Silicon host .venv, parselmouth installs from a macOS
  arm64 wheel instantly, so for this one feature the venv path is easier than Docker.
- Route two is scipy plus hand-rolled LPC: pre-emphasis 0.97, Hamming window, resample to twice
  the formant ceiling, LPC order about 2 + fs/1000 AT THE RESAMPLED RATE, roots -> angles ->
  frequencies, filtered by bandwidth and range. About forty lines, noisier than Burg, adds one
  wheel instead of a compiler. F0 needs an autocorrelation or cepstral tracker on top.
- librosa is NOT recommended: good pyin F0 out of the box but no formants at all, so it solves the
  easy half, leaves the hard half, and pulls the largest dependency tree.

ROUTE ONE IS RECOMMENDED, AND THE REASON IS NOT ACCURACY — IT IS v0.11.0. Parselmouth is not only
an analyser. It exposes Praat's Manipulation object: PSOLA resynthesis, PitchTier replacement,
DurationTier time-scaling, and formant shifting. v0.11.0's modified-self-voice surface — playing
the user their OWN recording with the corrected pitch contour or the corrected vowel — is built
directly on those calls and is nearly free once parselmouth is present. Route two analyses and
cannot resynthesise, so choosing it to save a Docker layer silently deletes half of the next
milestone. If route two wins anyway, SAY IN THE PLAN FILE that v0.11.0's manipulation surfaces
are deferred and what would be needed to restore them.
Verify all of the above against PyPI and the running container rather than trusting this summary.

SETTLE THE STRESS LEXICON TOO, SAME PLACE, SAME RIGOUR. Azure returns syllable spans and scores
but NO LEXICAL STRESS MARKS — there is no field that says which syllable of "computer" is
stressed. Reduction and stress placement cannot be measured without that, so it is a dependency,
not a detail. Options: a CMU Pronouncing Dictionary wheel (ARPABET with 0/1/2 stress digits, pure
data, no compiler, needs an ARPABET -> Azure-IPA mapping table written once into
phoneme_reference); a g2p package (heavier, usually drags nltk and a model download); or hand-
annotating stress only for the fixed calibration passage and the fixed drill inventory (free,
offline, permanent, and covers every scripted surface — but returns nothing for Mode C). Verify
what is actually on PyPI. If the third option wins, the reduction measures are SCRIPTED-ONLY and
that limit must be stated on the surface, not discovered later.

CRITICAL ARCHITECTURAL FACT, do not design around the wrong model: the user's audio is DELETED at
the end of each assessment request — audio_utils.temp_wav removes the temp file in a finally
block, by design, and no recording is ever persisted. So this measurement CANNOT be a later pass
over stored history. It must run INSIDE the assessment request, while the audio is still in
memory. Two consequences to state in the plan file: every attempt recorded before this chunk ships
is permanently unmeasurable, and the calibration passage must therefore be read AFTER this lands.

FIRST, RESOLVE A CONTRADICTION IN THE MEMORY BANK, because this chunk and v1.0.0's privacy
disclosure both rest on it. memory-bank/projectbrief.md — which declares itself the tiebreaker —
says "audio kept on disk but never committed" and "Recordings may be kept locally, with the path
and hash in the database". The code says otherwise and matches the paragraph above. One of the two
is stale. Decide which, fix that file, and note the decision in the plan file BEFORE writing any
measurement code: if audio were in fact retainable, the whole "must run inside the request"
constraint dissolves and this chunk is designed wrong.

PIPELINE: slice each vowel from the WAV using the phoneme offsets -> measure F1/F2/F3 at the 25%,
50% and 75% points, the segment's duration in ms, and its RMS intensity in dB, plus an F0 track
over the utterance -> Lobanov z-score normalise across the speaker's own vowel inventory ->
compare against published General American formant means.

FOUR INSTRUMENTS, NOT ONE. Formant position is the one everybody builds and it is a quarter of an
accent. Build all four in this chunk, because all four fall out of the same slice-and-measure loop
and retro-fitting any of them means re-recording that this project's deleted-audio design makes
impossible:
- POSITION — F1/F2 in Lobanov space against the GA reference. Where the vowel sits.
- TRAJECTORY — the 25% -> 75% movement. Whether a diphthong is a diphthong.
- RHOTICITY — F3, and specifically F3 minus F2. THE HIGHEST-VALUE SINGLE NUMBER IN THIS CHUNK for
  a General American target, and the one the original pipeline measured and then never used.
  American /ɹ ɝ ɚ ɔɹ ɑɹ ɪɹ ɛɹ ʊɹ/ are defined acoustically by a steeply lowered F3 approaching F2;
  a non-rhotic or weakly rhotic production leaves F3 high and separated. It is the loudest,
  cleanest, most correctable accent marker available and it costs one extra column.
- DURATION AND REDUCTION — vowel length in ms and how far unstressed vowels collapse toward the
  speaker's own schwa. Three sub-measures, all from data already in the row:
  * TENSE/LAX RATIO: /i/ against /ɪ/, /u/ against /ʊ/, /eɪ/ against /ɛ/. In GA the contrast is
    carried by quality AND length together; a learner who gets the formants right and the length
    wrong still sounds wrong.
  * PRE-FORTIS CLIPPING: the same vowel is markedly shorter before a voiceless coda than before a
    voiced one — this length difference, not the consonant's own voicing, is the main cue that
    separates "bat" from "bad" in American English. Measure the ratio; a learner producing no
    clipping produces minimal pairs that do not land.
  * REDUCTION: compute the speaker's OWN schwa centroid from unstressed syllables, then measure
    the mean Lobanov distance of unstressed vowels from it. Under-reduction — unstressed vowels
    held too peripheral, too long, too loud — is one of the strongest and most trainable accent
    markers in English, and it is invisible to every phoneme-level score Azure returns.

STRESS PLACEMENT is the composite of the last two plus F0 and intensity: an English stressed
syllable is longer, louder, higher or more pitch-moved, and has an unreduced vowel. Score all four
components separately and report them separately. A stress error reported as one number is exactly
the vague advice this project exists to delete — "your stress is off" is useless, "the second
syllable is 40 ms longer and 3 dB louder than the first, and GA puts it the other way round" is
an instruction.

THE TRAPS THAT DECIDE WHETHER THE NUMBERS MEAN ANYTHING:
- MEASURE AT POINTS, NEVER AVERAGE ACROSS THE SEGMENT. The edges are contaminated by
  coarticulation with neighbouring consonants and an averaged diphthong lands in the middle of
  nowhere. The three-point sample also makes trajectory fall out for free: a monophthong is the
  case where the 25% and 75% points coincide.
- MATCH THE MEASUREMENT POINTS TO THE REFERENCE'S OWN POINTS. Hillenbrand et al. report steady-
  state values and a separate trajectory measurement taken at fixed proportions of vowel duration
  that are NOT necessarily the 25/75 used here. Look up which proportions the table actually used
  when you look up the values, and either adopt them or record the offset. Comparing a 25/75
  sample against a 20/80 reference is a small systematic bias and it lands hardest on the
  diphthongs that v0.11.0's acceptance test depends on.
- THE LPC CEILING IS THE CLASSIC FORMANT ERROR: it must match vocal tract length, roughly 5000 Hz
  for a typical adult male voice and 5500 Hz for a typical adult female one. Getting it wrong
  shifts every value. Derive it from the measured median F0 rather than assuming, expose it as a
  setting, and RECORD WHICH VALUE PRODUCED EACH STORED MEASUREMENT so old rows stay interpretable.
  Treat the F0-derived guess as a WEAK estimator — F0 and vocal tract length correlate loosely,
  not tightly — so also sanity-check it by sweeping the ceiling across a range on the calibration
  audio and preferring the value that minimises within-vowel-category formant variance.
- THE CEILING AND THE LPC ORDER ARE ONE DECISION, NOT TWO. This is where route two goes wrong
  silently: "order 2 + fs/1000, 18 at 16 kHz" analyses the full 8 kHz band, which IS a ceiling of
  8000 Hz and quietly overrides the 5000/5500 setting above. The rule applies after resampling.
  Resample to twice the ceiling (10 kHz for 5000, 11 kHz for 5500), then the order is about 12 —
  roughly two coefficients per expected formant plus two for spectral tilt. An order-18 analysis
  at 16 kHz will invent extra poles inside the vowel's first two formants and split one formant
  into two, which looks like a plausible measurement and is not one. Assert the relationship in
  code rather than leaving both as independent settings.
- REJECT RATHER THAN GUESS. Discard any segment shorter than about 40-50 ms (too few pitch periods
  for a stable estimate), unvoiced or with no reliable F0 in its middle, or one where the speaker
  produced a DIFFERENT VOWEL ENTIRELY — its formants are a valid measurement of the wrong target
  and will quietly poison the cluster. That token belongs in the phoneme diagnosis, not the
  baseline.
- NORMALISATION IS NOT OPTIONAL. Formants scale with vocal tract length and raw Hz cannot be
  compared between speakers; comparing a male speaker's F1/F2 to a female synthetic voice's
  without normalising produces a chart that is confidently and entirely wrong. Use Lobanov
  z-scores (per-speaker mean and SD across the vowel inventory).
- LOBANOV IS SENSITIVE TO INVENTORY BALANCE, AND THE OBVIOUS IMPLEMENTATION IS THE WRONG ONE.
  Take the mean and SD over PER-VOWEL-CATEGORY MEANS, never over the raw token pool. Any natural
  passage over-samples some vowels, and a token-weighted centroid is dragged toward whichever
  vowel happened to occur most, tilting every z-score in the inventory. The error is invisible on
  inspection — the chart still looks like a vowel chart — so assert it in a test with a
  deliberately unbalanced token set.
- GATE ON RECORDING QUALITY. Formant estimation degrades badly with room reverb and a poor mic.
  The Azure payload's top-level SNR is ALREADY parsed into overall_scores as snr_db and
  snr_db_min by speech_analyzer._snr — read those, do not re-read the payload, and gate on
  snr_db_min because quality is governed by the worst segment. When it is low say the measurement
  is unreliable instead of drawing a confident dot. Before the first calibration read, have the
  user record five seconds, report the measured SNR, and say plainly whether this room and mic can
  support a vowel measurement at all.
- REPORT HOW MANY TOKENS each vowel's position is based on. A point built from two tokens and one
  built from twenty must not look the same.
- TAG THE SPEECH STYLE ON EVERY ATTEMPT FROM DAY ONE. Read speech is hyperarticulated;
  spontaneous speech is systematically more reduced and more centralised. They are different
  measurement populations and pooling them makes a register change look like a regression toward
  the middle of the vowel space. v0.12.0 adds spontaneous speech, but the tag has to exist BEFORE
  it, because audio is deleted and an untagged token can never be reclassified. attempt_tags
  already takes a free-text tag with no migration — write 'read' or 'spontaneous' there beside
  'shadowed', and make every baseline and every trend query filter on it.

THE NOISE FLOOR, AND THIS IS WHY CALIBRATION IS RECORDED TWICE. A vowel centroid moves between
sessions from mic placement, room, posture, time of day and vocal warm-up, with no learning
whatsoever. Without knowing how big that movement is, the progress view will render noise as
progress — against a brief whose entire goal is "see that drilling it worked". So: record the
calibration passage TWICE in one sitting, at least ten minutes apart, same mic, same room. The
per-vowel displacement between the two runs IS the measurement noise floor. Store it beside the
baseline. THEREAFTER NO MOVEMENT SMALLER THAN THAT BAND MAY BE REPORTED AS CHANGE — render it as
"within measurement noise", every time, including when it is in the flattering direction.

REFERENCE TARGETS — pick one per surface and label it; never average them. Published General
American formant means (Hillenbrand et al. 1995, adult male and adult female sets), Lobanov
normalised the same way as the speaker's data, are what the NUMBERS are measured against — look
the table up when writing it, do NOT type the values from memory. The Azure TTS voice measured
through the same pipeline is what the EAR is trained on. The two do not coincide, and imitating
the voice can move a token AWAY from the published mean while sounding better. Say which reference
each surface uses, on that surface.

KNOW WHAT THE PUBLISHED TABLE IS AND IS NOT. It is upper-Midwest speakers recorded in the early
1990s. Two consequences worth writing into vowel_reference.py as comments rather than discovering
later: the low-back /ɑ/-/ɔ/ distinction has continued to merge across most of the US since, so a
confident "your /ɔ/ is wrong" may be flagging a change the reference predates; and GA /u/ has
fronted, so a modern native production sits higher in F2 than the 1995 mean. Where the reference
is known to be behind the language, WIDEN THE TOLERANCE BAND rather than reporting a deviation
that no listener would hear.

THE OUTPUT CONTRACT — every accent surface in this project, here and in every later chunk, renders
its findings as a Markdown table with EXACTLY these four column headers, in this order:

| Acoustic Feature | User Realization | Target Realization | Delta / Adjustment Needed |

Rules that make the table worth having:
- The feature column NAMES THE PHONEME IN AZURE'S IPA, plus the Wells lexical-set keyword, plus
  the metric: "/eɪ/ FACE — F2 travel 25%->75%", "/ɝ/ NURSE — F3-F2 distance". IPA alone is
  unreadable at a glance; the keyword alone is imprecise; the metric alone is not a sound.
- The two middle columns carry NUMBERS WITH UNITS — Hz, z-units, ms, dB, semitones — and the user
  column carries its token count. Never a score, never a percentage, never a verdict.
- The fourth column carries the SIGNED delta AND the articulatory instruction it implies. A delta
  with no instruction is a measurement; an instruction with no delta is the vague advice this
  project exists to delete. Both, in every row.
- One row per measured feature. A rejected token gets a row too, with the rejection reason in the
  fourth column, so a thin table is visibly thin rather than silently short.

Worked shape, for the fixture:

| Acoustic Feature | User Realization | Target Realization | Delta / Adjustment Needed |
|---|---|---|---|
| /i/ FLEECE — F2 (Lobanov z) | +1.12 (n=14) | +1.94 | −0.82 → tongue further front, lips spread |
| /eɪ/ FACE — F2 travel 25→75% | 180 Hz | 620 Hz | Monophthongised; glide, do not hold |
| /ɝ/ NURSE — F3−F2 | 980 Hz | 310 Hz | +670 Hz → no r-colouring; bunch the tongue |
| /æ/ TRAP — duration before voiced coda | 118 ms | 205 ms | +87 ms → lengthen before /d z g/ |
| /ə/ unstressed — distance from schwa centroid | 0.94 z (n=31) | 0.30 z | −0.64 → under-reduced |

STORAGE, additive, no migration: speaker_baseline (per-vowel means and SDs from the calibration
run, the noise-floor band from the paired run, the LPC ceiling used, the speech-style tag, when it
was measured; one current row, history kept) and vowel_measurements (one row per token: attempt
id, vowel, three-point formants F1/F2/F3, duration ms, RMS dB, stressed/unstressed, the token's
Azure score, the recording's snr_db_min, the LPC ceiling used, the speech-style tag, and whether
it was accepted or rejected and why). Store RAW measurements, never only derived positions —
normalisation schemes and reference tables will change, and re-deriving must never require
re-recording. This is also why F3, intensity and the stress flag are columns from day one even
though nothing reads them until v0.11.0: a column costs nothing, a re-recording is impossible.

EXIT: two calibration reads produce a stored baseline AND a stored noise floor; every measurement
is reproducible from stored rows; every accent surface renders the four-column table with IPA-
labelled features and signed deltas; F3-based rhoticity, vowel duration and unstressed reduction
each produce a number, not a placeholder; the pipeline refuses rather than guesses when tokens are
too few to normalise; a movement smaller than the noise floor renders as "within measurement
noise". Milestone v0.10.0 closed.

## Releases

**v0.9.0 — 2026-08-20.** The tooling chunk: source moved under `src/` (#17), ruff format and
lint (#15), mypy (#18), and GitHub Actions (#16), as one chunk because all four touch the same
9,582 lines. The `src/` layout is **bare-root**, so no import statement changed. **mypy is
strict on every module under `src/`, `app.py` included** — the plan budgeted for leaving the UI
layer loose, but with the dependencies installed it cost ten signature annotations. The
controlling constraint was that **a CI run must be structurally unable to spend quota**, and it
is, in four independent layers: the workflow names no repository secret, a CI checkout has no
`.env`, `conftest` clears the credentials and forces `OFFLINE_MODE`, and a new `no_network`
fixture refuses any non-loopback socket. `release.yml` is a separate workflow so `ci.yml` keeps
`permissions: contents: read`, and this release is its own first run — published by the tag
push, not by hand. No Python version matrix, contrary to #16, because the project pins 3.12
deliberately and has never been run on 3.11; said on the issue rather than left implied.
641 tests, up from 637 by exactly the four that guard the socket guard, and the formatting
commit's count is identical to the commit before it. Merged via
[PR #29](https://github.com/Shahzayb/dialect-coach/pull/29). Milestone v0.9.0 closed with all
four issues.

**v0.8.0 — 2026-08-19.** Shadowing: practise against a synthesised model in real time, then
assess that read as an ordinary Mode B attempt — a flow wrapped around the existing path, with
nothing in the analysis pipeline changed. Two modes, only the speak-along one assessed. Merged
via [PR #27](https://github.com/Shahzayb/dialect-coach/pull/27), tagged, and released
(`gh release create v0.8.0`). Milestone v0.8.0 closed; it carried no issues — this chunk came
from the brief rather than from the tracker, so the release closes none. **The exit criterion is
met structurally but not yet with data**: a shadowed read and a cold read of the same passage
render side by side with their fluency and prosody delta named, and the surface currently and
correctly renders its day-one empty state, because only the shadowed half exists.

**v0.7.0 — 2026-08-19.** The perception trainer and the practice queue: forced-choice
minimal-pair identification across six en-US voices, and a persisted target set of at most
three items promoted from the user's own flagged history. A new **Today** tab is the app's
entry point. Merged via [PR #26](https://github.com/Shahzayb/dialect-coach/pull/26), tagged,
and released (`gh release create v0.7.0`). Milestone v0.7.0 closed; it carried no issues —
this chunk came from the brief rather than from the tracker, so the release closes none.

**v0.6.0 — 2026-08-19.** Azure's `Offset`/`Duration` (word, syllable and phoneme) and the
top-level `SNR` carried through the parser, plus nPVI over vocalic intervals in a new
`rhythm.py`, measured against a captured Azure TTS baseline of the benchmark passage. Merged
via [PR #25](https://github.com/Shahzayb/dialect-coach/pull/25), tagged, and released
(`gh release create v0.6.0`).

**v0.5.0 — 2026-08-19.** The progress view: the first feature that reads the SQLite history
back, on a fixed benchmark passage. Merged via
[PR #24](https://github.com/Shahzayb/dialect-coach/pull/24), tagged, and released
(`gh release create v0.5.0`).

**v0.3.0 — 2026-08-19.** Score breakdown, headline mispronunciation/delivery-fault counts,
and the per-word phoneme hover tooltip, merged via
[PR #21](https://github.com/Shahzayb/dialect-coach/pull/21), tagged, and released
(`gh release create v0.3.0`). Closes #10, #11, #13. Splits #12 — its content-score half
(vocabulary/grammar/topic) retitled and moved to milestone v0.12.0, since that data only
comes from unscripted assessment, not built yet. Milestone v0.3.0 closed.

**v0.2.0 — 2026-08-19.** Stop, reset and delete controls on the record-and-assess surface,
merged via [PR #20](https://github.com/Shahzayb/dialect-coach/pull/20), tagged, and
released (`gh release create v0.2.0`). Closes #4, #5, #6, #7, #8. Milestone v0.2.0 closed.
Also fixed the 8 code-review findings left open at v0.1.0 (see below — no longer "open").

**v0.1.0 — 2026-08-18.** The coaching layer, merged via
[PR #19](https://github.com/Shahzayb/dialect-coach/pull/19), tagged, and released
(`gh release create v0.1.0`). References #9 (prosody feedback) in the release notes without
closing it — `stress_and_rhythm` addresses the "no way to get feedback" complaint, but the
issue is left for the user to close once satisfied. Released with the 8 code-review findings
below still open, on the user's explicit instruction (fix-after rather than fix-before).

## Next concrete step

**Read the benchmark passage — cold, and then shadowed.** The progress view ships with its
headline series empty — that is not a defect, it is the first day. Four or five reads spread
over a month are what make the chart worth looking at, and the first one also settles whether
196 words really lands inside 60-90 seconds at an actual reading pace (the attempt's own
`audio_seconds` says; the TTS baseline took 61.8 s, and a human reads it slower). Until then
the only thing on screen is the free-practice cloud, which is exactly the thing that cannot be
read as progress.

Four things now depend on it rather than one. The rhythm chart plots benchmark reads only,
because nPVI moves with the text as much as with the speaker, and the first read is the first
number that can be set against the TTS baseline's 58.45. **The practice queue is the third,
and it is the one that changes what a read is for**: targets are promoted from sounds flagged
across separate attempts, so until there are real attempts the queue has nothing to schedule
and the Today tab correctly offers nothing. Reading the passage is now what starts the whole
loop, not only what fills a chart.

**Shadowing is the fourth, and it needs the cold read specifically.** A shadowed read with
nothing to sit against says nothing at all — the comparison surface correctly reports "no cold
read of this passage yet" rather than inventing a partner. So the order matters: read it cold
first, then shadow it on headphones, and the first honest pair exists. That pair is also the
first real test of the pre-registered finding above, and of whether the recording survives the
press-record-then-press-play sequence in a real browser, which no test can answer.

**Then do some blocks.** The trainer has been run, not used. The graduation rule (90% across
two completed blocks) has never fired on real listening, the spaced-review schedule has never
come round, and whether a perception gain shows up in the Azure scores is the question the
whole chunk is a bet on. Same shape as the benchmark's 30-day check: only weeks of real use
answer it, and neither is a merge gate.

**Then Mode C (unscripted speech)** — free speech scored on vocabulary, grammar and
topic, not just a script. Blocked on a real question, not busywork:
`enable_content_assessment_with_topic` does not exist in SDK 1.51.1 despite the master plan
citing it (see Dead ends below), so Mode C's content scoring needs another route found and
verified before it can be planned. `UNSCRIPTED_TWO_PASS` is defined and priced by
`budget.passes_for` but unread by any recognition code yet.

## Active plan

`plans/2026-08-19_shadowing-practice-flow.md` — **built and complete offline; the live half is
outstanding and cannot be closed without a human at a microphone.** Open as
[PR #27](https://github.com/Shahzayb/dialect-coach/pull/27) with the v0.8.0 milestone attached,
version bumped to 0.8.0 — **not yet merged, tagged or released.** 632 tests and the whole
surface driven in the browser, but no real shadowed read exists yet, so the acceptance test it
carries has no data. Unlike every earlier chunk's deferred check, this one is not just a
calendar item — nothing in the repository can answer it.

`plans/2026-08-19_perception-trainer-practice-queue.md` — complete. The live block was run
against real Azure and asserted against the meter, so the exit criteria are met rather than
deferred.

`plans/2026-08-19_timing-data-and-npvi.md` — complete. The TTS baseline was captured, so the
nPVI figure ships with the comparison that makes it mean something rather than with a caveat.

`plans/2026-08-19_progress-view-benchmark.md` — complete; the 30-day check is a
calendar item, not a merge gate (see below).
`plans/2026-08-19_prosody-coaching-payload.md` — complete, live recording included.
`plans/2026-08-19_record-assess-defects.md` — complete.
`plans/2026-08-18_coaching-layer.md` — complete.
`plans/2026-08-18_legible-audible-diagnosis.md` — complete.
`plans/2026-08-18_azure-analysis-core.md` — complete.
`plans/2026-08-17_project-scaffold.md` — complete.

## What works

Record or upload a drill sentence or a paragraph and get real Azure scores down to the
phoneme, rendered as: the metric row, a script-versus-heard diff, colour-coded reference
text with the score on hover, a card per flagged word naming the sound actually produced in
place of the target (`/θ/ → /t/`, not "your /θ/ scored 41"), the syllable/stress line, and
the delivery panel. "Hear it" and "Hear it slowly" synthesise a native rendering — per word
and for the whole text — with your own recording directly beneath for back-to-back
comparison. Every attempt is stored in local SQLite with both raw API responses kept
verbatim.

Verified end to end, offline and online. `make test` is 161 tests with no keys and no
network. The online run on 2026-08-18 used the real `.env` and the 12.8 s weather recording:

- The F0 guard refused to start at `AZURE_TIER_CONFIRMED_F0=false`, as designed, and the
  acknowledgement was given by the user rather than assumed. It was passed to the container
  as an environment variable rather than written into `.env`, so the file still says false.
- Live assessment returned `pron_score` 83.0, accuracy 89.0, **prosody 76.4** — prosody is
  genuinely populated, not blank.
- Live TTS returned real audio: RIFF WAV, 24 kHz mono, 1.04 s for one word, 7.9 s for the
  whole text. `audio_config=None` is confirmed necessary and sufficient.
- The slow path returned 1.6 s against 1.04 s for the same word — the 1.54× that
  `rate="-35%"` predicts, so the SSML reaches Azure intact.
- **The meter charged once per distinct phrase, not once per click.** Four clicks produced
  three `tts_usage` rows (8 chars for "thursday", 167 for its SSML, 135 for the whole text);
  the repeat click was served from the session cache and charged nothing.
- Exactly one synthesised player renders at a time, and the two offline replays sitting in
  the table are correctly excluded from the STT meter — 12.82 s charged, not 16.82 s.

A second review pass driven against the running app found four more, all fixed and
re-verified live. Failure paths were exercised by starting the app with a deliberately
invalid `AZURE_TTS_VOICE`, which is a cheap way to reach the error branches without
waiting for a real outage — worth reusing. Omissions were exercised by adding a word to
the reference text that the recording does not contain; Azure marked it `Omission` itself,
confirming `enableMiscue` really is honoured in drill mode.

Total spend across all live testing: 64 s of 18,000 STT seconds and 339 of 500,000 TTS
characters.

**The coaching layer** turns the diagnosis into a report: 2-3 sentences on the attempt, up
to three priority fixes (expected → produced, affected words, why it matters, articulation,
minimal pairs), stress-and-rhythm issues with a drill, and a five-minute practice plan
naming specific words from the attempt. Rendered directly under the metric row — what to do
before the evidence for it — with the top fixes as bordered cards, never a raw model text
blob. The offline coach (`fallback_coach`) writes it for free on every assessment, with no
key and no network; "✨ Improve this with Gemini" is a button that spends one free-tier call
and replaces it in place, with a caption stating up front that a click sends the compacted
analysis and the reference text to Google, never the audio. A visible caption always says
which coach wrote the report on screen.

Verified live on 2026-08-18 with `scripts/coach_test.py`, which spends no Azure quota (it
replays the committed fixture the way `OFFLINE_MODE` does) and one real Gemini call: the
schema was honoured, no phoneme absent from the Azure data survived into the report, the
~39 kB raw response compacted to ~1.8 kB sent, and the stored payload re-parsed back into
the same report. The exit criterion — a complete, useful report with `GEMINI_API_KEY`
deliberately unset — was verified in a running container via the browser tool: uploading
the captured recording and assessing it against its own reference text produced the full
report, correctly naming `/θ/ → /s/` on "thursday" as the flagship fix, entirely offline.

One thing the browser check caught that the offline test suite could not: a click on
"Improve this with Gemini" is handled in the same Streamlit rerun that renders the button,
so the on-screen button still shows as enabled until the *next* rerun — a second click
before then would have bought a second call. Fixed by moving the spend guard into
`coaching_for` itself (`already_asked`), not left on the button's `disabled` flag alone.

**The prosody score is actionable (milestone v0.4.0, #9).** Delivery faults —
`UnexpectedBreak`, `MissingBreak`, `Monotone`, which live under `Feedback.Prosody` and not
in `ErrorType` — travel to the coach as their own payload section, carrying the span of
words each one damaged plus what Azure measured there. The report answers with a
`Delivery` block: the fault in words, the span, what happened, and a drill to perform.
`fallback_coach` writes those drills from templates, so the feature works with no API key —
which is the whole point, since "Prosody 76.4" with nothing to do about it was the
complaint. `ai_coach` asks the model for the same section and backfills from the templates
for any fault it skips, so a fault in the data always produces advice on both paths.

Verified offline in the browser on 2026-08-19 **against the real captured bad reading**
(`OFFLINE_FIXTURE=bad_delivery_capture.json`, paragraph mode, no `GEMINI_API_KEY`):
prosody 81, and a Delivery block quoting the flat stretch back — *"once i get back to my
desk i'll call the team to …"* — with a drill on it, and a note that it went flat in two
separate stretches. Also verified earlier against the synthetic payload with
`OFFLINE_FIXTURE=synthetic_delivery_faults.json`: prosody 54, and a Delivery block naming
the Monotone span ("stayed, warm, clear") with a drill for it, an UnexpectedBreak span
("unpredictable, thursday", longest about 420 ms) and a MissingBreak span ("clouds,
while"). The delivery panel further down quoted the same spans and the same numbers,
because both read `fallback_coach.measurement_note`.

The model path was verified with **one free-tier Gemini call** the same day, through
`scripts/coach_test.py` with `OFFLINE_FIXTURE=synthetic_delivery_faults.json` — no Azure
quota, since the script replays a fixture. `gemini-3.6-flash` returned all three faults
drilled with the spans Azure reported, nothing invented, 3298 tokens in and 742 out, and
the stored payload re-parsed. Nothing had to be backfilled on that run, so the backfill
path itself is covered by tests rather than by observation.

**A deliberately bad reading was captured on 2026-08-19** (38.5 s, 39 s of the 18,000 s
allowance) and committed as `tests/fixtures/bad_delivery_capture.json` — the first payload
in the repo carrying a real delivery fault. Azure flagged **Monotone on 30 words across 7
utterances and nothing else**, so the `UnexpectedBreak` / `MissingBreak` paths are still
covered only by the synthetic payload. Reading three sentences haltingly, with pauses run
together, did not produce a break fault; whatever provokes one, that was not it.

**The real capture broke the coaching immediately, which is what it was for.** The
synthetic payload's spans were three words long, so naming the first few of them read
fine. A real Monotone is a long unbroken passage, and its span is in reading order — so
the coach produced *"Say i, i, need, once, i, get three times"*. `delivery_faults` now
cuts a span into contiguous `runs` and the coach quotes the longest one back as the phrase
it is, capped at 12 words. Runs stop at a gap, so a quote can never join words the speaker
never said next to each other. **The lesson worth keeping: a synthetic payload sized like
a unit test hides everything that only shows up at real length.**

**`BreakLength` is in 100-ns ticks.** Derived from the committed captures, not from docs —
SDK 1.51.1 never mentions the field anywhere. The bad reading confirms it independently:
31100000 in a 38.5-second take is over eight hours as milliseconds and 3.1 seconds as
ticks. See `techContext.md`; an earlier reading that called every value 0 was wrong.

**The record-and-assess surface** survives being used impatiently. `Assess` is disabled
while a request is in flight and a `Stop` button appears beside it for the duration; a
`↺ Reset` clears the recording, the upload, the text, the preset and the on-screen result;
a `🗑️ Delete recording` discards just the take, keeping the text, so a bad take costs
nothing typed. Words that scored 100 but were flagged anyway — a delivery fault on an
otherwise perfect word — are collapsed behind an expander instead of burying the words that
need work. Omitted words are never collapsed there: they carry no score at all, which is the
opposite of a perfect one.

Verified live in the browser, entirely offline: **ten rapid clicks on `Assess` produced
exactly one attempt row**, a re-assess of an identical attempt stayed instant on the session
cache without ever spawning a job, and Reset cleared the uploaded file along with everything
else. The in-flight controls and the cancellation paths are covered headlessly instead —
offline replay returns too fast for a human to click Stop during it, so those are driven by
`AppTest` against a job whose thread is held open, and by a fake recognizer whose events fire
under the test's control. No sleeps, no races, no cost.

**The scores and error metrics (milestone v0.3.0, #11/#13/#10/#12-pronunciation)** are
banded and surfaced. `render_scores` shows a colour-banded Pronunciation headline plus
Completeness, then a "Score breakdown" section (Accuracy/Fluency/Prosody as banded bars) —
banded against Azure's own 0-59/60-79/80-89/90-100 convention (`utils.AzureBand`), not the
word/phoneme heuristics. `render_error_counts` adds a headline count row (Mispronunciations,
Unexpected break, Missing break, Monotone) right under it — counts only, since
`render_delivery` and the flagged-word cards already give the per-word detail. Hovering a
word in the "Word by word" running text now shows a real tooltip (`word_tooltip_html`) with
the word's score, then its phoneme symbols and their scores as two aligned rows, replacing
the old single-line `title=` attribute. Content score (vocabulary/grammar/topic, #12's other
half) is out of scope — scripted assessment never returns it.

`make test` is 352 tests, all offline with no keys and no network.

Not built: Mode C (unscripted).

**Progress over time, on a fixed benchmark passage (milestone v0.5.0).** The Progress tab
charts pronunciation, accuracy, fluency and prosody across every stored attempt, plus the
substitutions and words that keep getting flagged. The whole design turns on one decision:
plotting scores across arbitrary self-chosen texts measures **text difficulty, not the
speaker**, so a fixed passage is the headline series and free practice is a faint cloud of
unconnected points behind it. The passage was chosen once for two consumers — this chart and
the vowel-measurement calibration read a later chunk needs — and `techContext.md` holds why
it covers both, along with the three vowels it honestly cannot guarantee.

Two things worth keeping from building it:

- **The coverage table earns its keep.** `BENCHMARK_COVERAGE` ships the "it covers both
  instruments" claim as data with a test asserting every token really appears in the
  passage, and it immediately caught one ("which") that had been edited out during drafting.
  A prose justification would have drifted silently and nobody would ever have checked.
- **A synthetic payload has to match the text it claims to be.** The seed script's first
  version replayed the committed weather fixture against the benchmark reference; the Mode B
  miscue diff duly marked two hundred words omitted on every benchmark read and "the" and
  "i" headed the flagged-word ranking. Seeded benchmark rows now carry a payload built from
  the passage itself, and the ranking shows what it should: /θ/ → /t/, /v/ → /w/, /ð/ → /d/,
  /l/ → /ɹ/ — the sounds the passage was written to catch.

Verified in the browser against `scripts/seed_progress_history.py`'s 30 days, on both the
light and the dark theme: the benchmark line rises 72 → 87 across four faceted metrics and
is plainly distinct from the grey cloud, the two free-practice modes carry different shapes
and are joined by nothing, a seeded NULL prosody leaves a gap rather than a dip to zero, and
both rankings label every bar. Zero spend — nothing in the chunk calls Azure or Gemini.
`make test` is 392 tests (up from 352), all offline.

**What shipping this does not prove, stated plainly: the real 30-day check is a calendar
item, not a merge gate.** What was verified is a seeded history — the plumbing, the shapes
and the chart. The benchmark series starts **empty** on the day this ships, and only four or
five real reads spread over a month make it worth looking at. The first real read is also
the only way to confirm that 196 words lands inside 60-90 seconds at an actual reading pace.

**Training, not only diagnosis (milestone v0.7.0).** A **Today** tab is now the first thing
the app opens on, and it answers "what am I doing today?" instead of presenting a blank
textarea — the textarea is one tab click away. It carries at most three targets, each
promoted out of the user's own flagged history, each showing the counts it was promoted on
and the rule that takes it off. The due one is either a **listening block** (a contrast or a
vowel gap) or a **stress drill**.

A block is High Variability Phonetic Training: 20 forced-choice trials on minimal pairs from
`phoneme_reference`, cycling **six en-US voices** — three male, three female, across two
voice generations — scored immediately, with the answer revealed and both words replayable.
Every accuracy figure on screen sits beside its chance floor, and the Progress tab plots
per-contrast accuracy against a dashed 50% rule.

Verified live against Azure on 2026-08-19, and the meter is the assertion:

- A fresh 20-trial block on `/θ/ → /t/` needed **38 clips and charged 167 characters** —
  one `tts_usage` row per clip, none double-charged.
- A **second block charged 9 characters**, for the 2 clips it had never played; the other 36
  came off the disk cache. Nothing already on disk is ever bought again.
- `scripts/list_voices.py` listed the roster and **charged nothing** (meter 0 before, 0
  after), which is what it exists to prove.
- Total spend for the whole exercise: **176 TTS characters** of 500,000, and no STT at all.

Verified in the browser on the seeded demo, both themes: Today promotes one consonant
contrast, one vowel gap and one stress item; a trial autoplays, scores, reveals `tick` against
`thick` with the contrast note, and offers both words back; the perception chart draws the
chance line under a rising trajectory. `make test` is **556 tests**, all offline with no keys.

**What this does not yet prove.** The trainer has been run, not *used*. Whether twenty trials
a day is a habit anyone keeps, and whether the perception gain shows up in the Azure scores at
all, are questions only weeks of real blocks answer — the same shape as the benchmark's 30-day
check. The graduation rule (90% across two blocks) has never fired on real listening.

**Practice against a model, in real time (milestone v0.8.0).** Shadowing: press record,
press play, and speak **with** a synthesised reading of the passage. The read is then assessed
as an ordinary Mode B attempt — nothing in the analysis pipeline changed — and tagged
`shadowed`, so a shadowed read and a cold read of the same passage can be set side by side
with their fluency and prosody delta named. A second mode, **echo** (per-sentence clips with a
silence matched to each clip's own duration), is a warm-up and is deliberately never assessed:
its recording would pause between every phrase, so Azure would mark the delivery down for a gap
the format put there.

The feature carries its own acceptance test and the test is free, because both reads are
already stored attempts. **It is pre-registered**, in `progress_view`'s section header and in
the plan file, so the outcome is a finding rather than a retrofit:

1. A shadowed read should score higher on fluency and prosody than a cold read of the same
   passage.
2. **That gap should narrow over weeks**, as the shadowed pattern becomes the cold-read
   pattern. The narrowing is transfer, and transfer is the only thing that makes the practice
   worth the minutes.
3. **If the gap never narrows, the practice is not transferring and the design is wrong** — the
   model is a crutch that carries the read and puts nothing down. The comparison surface says
   so in as many words rather than leaving it to be explained away later.

Verified offline, no keys and no network: `make test` is **632 tests** (up from 556). The whole
surface was driven in the browser on the seeded demo, both themes — the shadow card is offered
on Today with no history at all (it is the one practice here that needs none), the session
renders in place the way a listening block does, "Prepare the model" is disabled under
`OFFLINE_MODE` with the reason, echo mode names its 14 phrases and offers no recorder at all,
and the comparison names `Fluency +7.7` and `Prosody +7.3` **beside its four pairs** with the
gap chart converging on zero. The browser check caught one thing the tests could not: a dashed
orange line with no legend entry reads as a second trajectory, which is the one thing it must
not be taken for, so a caption now says what it is.

**Measured live against Azure on 2026-08-19, and the meter is the assertion** — same
discipline as v0.7.0, on a database and a month that both started at zero, so every charge is
attributable:

- The benchmark model clip charged **exactly one `tts_usage` row of 975 characters**, the
  passage's own length, and came back as **61.775 s of audio** — the committed TTS baseline
  says 61.8 s, so the arithmetic the 180-second duration ceiling was chosen on is right.
- Leaving the session and preparing again charged **nothing**: *"Shadow audio served entirely
  from the disk cache; nothing charged."*
- The echo track needed **14 clips and charged 959 characters**, which is
  `sum(len(p) for p in phrases(BENCHMARK_PASSAGE))` exactly — one row per clip, none
  double-charged — and produced a **129.15 s** track against the 129.2 s the gap rule predicts.
- Switching back to speak-along charged nothing; 15 clips on disk, 15 rows, meter unmoved.
- **The layout constraint holds in a real browser**: the model player has `autoplay=false` and
  native controls, the recorder sits after it, and there are **zero buttons between them**, so
  nothing can trigger a rerun between pressing record and pressing play.
- **Echo mode renders no recorder at all** — the Today tab panel held 0 recorders and 1 audio
  element, so an echo take cannot be submitted even by accident.
- On a completely empty database the queue correctly offers nothing while **shadowing is still
  offered**, which is the branch that matters: it is the one practice here that needs no history.
- Total spend for the whole exercise: **1,934 TTS characters** of 500,000, and **no STT at all**.

**The first real shadowed read was done on 2026-08-19, and it found two defects the whole
offline suite had missed.** Both came from the same root: **two surfaces can now start an
assessment, and Streamlit executes every tab body on every rerun.**

- **`StreamlitDuplicateElementKey`, on the real read.** `last_key` is a single slot, so the
  Practice tab rendered the same result underneath the shadow surface — and `render_result`
  derives its widget keys from the attempt, so the second render *collided with the first* and
  blew up the page rather than merely looking odd. A `result_owner` slot now says which surface
  produced the result; each renders only its own. The regression test reproduces the original
  error with the guard removed, which is the only way to know a regression test works.
- **A queue holding nothing but a shadowing passage read as a full one.** With one attempt and
  nothing promoted, the shadow row made `targets` non-empty, so Today answered "what am I doing
  today?" with *"nothing due, they are all on the review schedule"* and captioned the empty list
  *"everything promoted so far has graduated"*. Both false. The empty-state check now keys on
  `practice_queue.promotable`, the same predicate that stops a shadow row eating one of the
  three slots.

**The lesson worth keeping: a second surface that can start the same job is not a UI change, it
is a change to who owns the single result slot.** Neither defect is reachable by any test that
exercises one surface at a time, and both appeared on the first real use.

The read itself: 66.5 s, pronunciation 84.1, accuracy 95.3, fluency 87.2, prosody 69.9, stored
`offline = 0` and tagged `shadowed`, with the shadow target created and due again three days
later — so `record_shadow_session` fired exactly once, as designed. Live spend for everything
above: **66 s of 18,000 STT seconds and 6,283 of 500,000 TTS characters.**

**What this still does not prove.** *There is no cold read of the benchmark passage yet*, so
the comparison correctly renders its day-one state — "no shadowed read has a cold read of the
same passage to sit against yet" — and the pre-registered finding above has **no data at all**.
One shadowed read is not a trend and cannot be: the whole claim is about a gap narrowing over
weeks. The seeded demo's gap was *written* to narrow because that is the shape the design predicts —
seeding the failure shape would have demoed a broken feature, but it means the demo is an
illustration and not evidence. Whether a real shadowed read beats a real cold one, and whether
the gap really closes, needs weeks of real reads on headphones. Same shape as the benchmark's
30-day check and the perception trainer's "has been run, not used".

## Known issues

**The 8 code-review findings from 2026-08-18 are all fixed** (2026-08-19, in the
record-and-assess chunk). Each has a regression test in `tests/test_review_findings.py`.
Worth keeping from that pass:

- The httpx one was the most misleading: `isinstance(exc, (TimeoutError, ConnectionError))`
  looked correct and matched nothing. **No httpx transport exception subclasses either
  builtin** — verified by introspecting all six in the container, not from docs — so every
  real network failure was classified permanent and skipped its retry. Now keyed on
  `httpx.TransportError`, which is the common base for timeouts and connect errors alike.
- The Gemini re-spend guard now keys off *whether a call was bought*
  (`gemini_attempted`), never off which source came back. An outcome that spent a real call
  and still fell back is exactly the one not worth buying twice.
- `validated()` now checks the prose (`overall_comment`, `practice_plan`, the
  stress-and-rhythm lines) as well as the fixes, and rejects the whole report rather than
  editing a fabricated sound out of a sentence — there is no way to cut a clause and be
  left with English, and the offline report that replaces it is complete.

**A real `.env` can silently undo the 180-second paragraph ceiling.** The default moved from
120 to 180 for shadowing, but `.env.example` and any existing `.env` set the variable
explicitly, so a file written before this chunk still says 120 and wins. A slow shadowed read of
the benchmark is 61.775 s x 1.54 = **~95 s of model audio** plus the lead-in and tail, so ~105 s
lands inside 120 with almost no margin for a shadower who trails the model. Check the live
`.env`, not just `utils._DEFAULTS`.

**A shadowed read is only as clean as the headphones.** The model plays while the microphone
is open, so on speakers Azure assesses a mixture of the speaker and the synthesiser. The UI
says so above the recorder, but nothing enforces it and nothing in the stored row records
which was used — an unexplained jump in *accuracy* on a shadowed read is the symptom to
suspect, since shadowing trains delivery and should not move articulation.

**Preparing the model for the benchmark passage is one 975-character synthesis** (about
1,150 as SSML at the slow rate), and echo mode is 14 separate clips of the same text. Both are
cached on disk and bought once per (passage, rate), so the cost is paid the first time and
never again — but the first slow echo track on a fresh cache is 14 sequential calls before
anything plays.

**Preparing a fresh block takes about 40 seconds.** Synthesis is sequential at roughly one
second per clip and a block needs ~38 of them, so the first block on a new contrast has a real
wait before trial one. The progress bar names the count while it runs, and every later block on
that contrast is instant because the clips are on disk. Parallelising the batch is the obvious
fix and was deliberately not attempted in this chunk.

**A stress item cannot be checked, only drilled.** Azure returns per-syllable accuracy but no
stress marks, so there is no way to ask "which syllable was stressed?" and know the right
answer without a pronouncing dictionary or another recording. Stress items therefore graduate
on the evidence drying up rather than on a score. A CMUdict-backed stress-location task would
close it and is the recorded upgrade path.

Still open, lower severity, from the same pass: `app.py`'s word card shows the raw unsmeared
duplicate phoneme that the coaching report collapses — the two views can disagree on how
many things went wrong in one word (directly observed live). `ai_coach.py`'s
`_client`/`_config`/`_call` lack type hints (violates CLAUDE.md's "enforce type hints"
rule). `ai_coach.report_from_raw` is still unreachable from the running app — nothing in
`app.py` calls it, so a Gemini report evicted from the session cache or lost to a restart
cannot be recovered from the database despite being stored for exactly that (the function
itself now re-reads both stored shapes, so wiring it up is all that is left). `app.py`'s
`if entry.attempt_id:` treats an id of `0` as absent rather than checking `is not None`
(PLAUSIBLE, low-probability trigger).

- **SQLite WAL is not readable across processes over the macOS bind mount.** A second
  process (`docker exec … sqlite3`) reading `DB_PATH` while the app holds its connection
  sees only checkpointed rows — during live verification the app's own History panel showed
  3 attempts while an outside reader saw 1, and no `-wal` file was visible at all. The app
  is single-connection so this never affects it; it means **verify row counts through the
  app's own History panel or its logs, not by opening the file from another process.**
- `pydub` 0.25.1 emits `SyntaxWarning: invalid escape sequence` on import under 3.12.
  Cosmetic, upstream, no action. The `audioop` DeprecationWarning is filtered in
  `pytest.ini` for the same reason.
- The multi-utterance merge is only covered by synthetic payloads. The captured 12.8 s
  recording came back as a single utterance in continuous mode, so the real multi-utterance
  path has never run against live data. A longer paragraph recording would close this.
- **`UnexpectedBreak` and `MissingBreak` have still never been seen from Azure.** The
  deliberately bad reading closed the gap for `Monotone` only
  (`tests/fixtures/bad_delivery_capture.json`); the two break faults are covered by
  `tests/fixtures/synthetic_delivery_faults.json`, which is hand-built and says so inside
  the file. `OFFLINE_FIXTURE` selects either. A reading that actually provokes a break
  fault would close the rest — halting delivery with sentences run together did not.
- The reference text sent to TTS is the *script*, not what was heard, so whole-text
  playback always renders the intended reading. That is the point, but it means a
  paragraph's playback does not line up word-for-word with a recording that omitted words.

## Dead ends

- **Reading `word["ErrorType"]` from the Azure payload.** It sits inside the word's
  `PronunciationAssessment`, so the top-level read silently returns nothing and every word
  parses as clean. Not worth retrying — the docs' flat REST example is what misleads here.
- **`enable_content_assessment_with_topic`** is not in SDK 1.51.1 despite the master plan
  citing it for Mode C. Do not plan Mode C's content scoring around it without checking
  first.

## Standing preferences

- Project memory lives in this repo's `memory-bank/`, per `.claude/skills/memory-bank/SKILL.md`.
- Take one chunk of work at a time, plan it in its own dated file, then implement only that.
- **Never install anything globally.** Docker is the preferred run path; a project-local
  `.venv` is the acceptable alternative.
- Commit in chunks as work lands, not one commit at the end.
- **Python over `.sh` for anything with branching/conditionals.** Trivial one-liners (a
  single `docker compose up --build`) can stay as a Makefile recipe; put real logic in a
  `scripts/*.py` file instead, as `scripts/setup.py` does.
- Verify library versions and API surfaces against current sources rather than recalling
  them — the pins in the original design were already stale.
- **Build parsers against a captured payload, not documentation.** The real Azure response
  differs from the documented shape in ways that fail silently rather than loudly.
- **Verify SDK surfaces by introspecting the installed package**, not from docs or memory.
  The `SpeechSynthesizer` default-speaker trap was found by printing the constructor
  signature in the project image, and it would not have been found by reading a sample.
- Spend API quota deliberately and say so, not incidentally: two calls captured both
  fixtures, and every guard now also applies to the capture script. **`OFFLINE_MODE` is the
  only thing standing between a capture script and a real charge** — `.env` is bind-mounted
  and `compose.yaml` loads it, so running a capture script with `OFFLINE_MODE=false` bills
  immediately, with no further prompt. Treat unsetting it as the decision to spend.
- **The app runs locally. Deploying it is not a goal** — treat hosting as an option left
  open for someone else, never as a requirement to design around. See `techContext.md`.

## How the direction has evolved

- 2026-08-17 — Docker became the primary run path mid-implementation, to keep the host
  clean and to pin the Azure SDK's native dependencies alongside the Python version.
- 2026-08-17 — A local database is now in scope. The brief previously ruled out stored
  history entirely; SQLite is the chosen engine, and `projectbrief.md` was updated on the
  user's instruction. What gets stored is still open.
- 2026-08-18 — What the database stores is settled: **both raw API responses, verbatim**,
  on the user's instruction. The monthly usage meter is derived from that same table, so
  `.usage.json` and `BUDGET_STATE_PATH` were dropped rather than kept as a second store
  that could disagree with it.
- 2026-08-17 — Hosting dropped as a goal: the tool is for local use. The original design
  treated a Hugging Face Space as the target and derived real requirements from it
  (ephemeral-filesystem handling for the usage meter, cold-start wake time, a private
  Space). Those requirements are gone; the deploy artefacts stay only as an option.
