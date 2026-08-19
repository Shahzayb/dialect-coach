# Progress

## Current focus

Building the app one chunk at a time. The diagnosis is legible and audible, the coaching
layer turns it into something to practise, the record-and-assess surface now behaves under
repeated and impatient clicking, the v0.1.0 code-review findings are fixed, the scores
and error metrics for milestone v0.3.0 (#11, #13, #10, pronunciation half of #12) are built
and verified live, the prosody score now comes with a drill attached (#9, v0.4.0), and the
stored history is finally shown back over time on a fixed benchmark passage (v0.5.0).
**Milestone v0.3.0 is closed** — #10, #11, #13 closed with comments
pointing at what implemented them; #12 split, its content-score half (vocabulary/grammar/
topic) retitled and moved to v0.12.0 since scripted assessment never returns it. What
remains is Mode C (unscripted), which is also what unblocks #12's remaining half.

## Releases

**v0.5.0 — built 2026-08-19, not yet tagged.** The progress view: the first feature that
reads the SQLite history back, on a fixed benchmark passage. On the branch and verified;
the PR, the tag, the GitHub release and closing milestone v0.5.0 are still to do.

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

**Read the benchmark passage.** The progress view ships with its headline series empty —
that is not a defect, it is the first day. Four or five reads spread over a month are what
make the chart worth looking at, and the first one also settles whether 196 words really
lands inside 60-90 seconds at an actual reading pace (the attempt's own `audio_seconds`
says). Until then the only thing on screen is the free-practice cloud, which is exactly the
thing that cannot be read as progress.

**Then Mode C (unscripted speech)** — free speech scored on vocabulary, grammar and
topic, not just a script. Blocked on a real question, not busywork:
`enable_content_assessment_with_topic` does not exist in SDK 1.51.1 despite the master plan
citing it (see Dead ends below), so Mode C's content scoring needs another route found and
verified before it can be planned. `UNSCRIPTED_TWO_PASS` is defined and priced by
`budget.passes_for` but unread by any recognition code yet.

## Active plan

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
  fixtures, and every guard now also applies to the capture script.
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
