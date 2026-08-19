# Shadowing practice — milestone v0.8.0

## Context

Every chunk so far measures. `v0.7.0` was the first that *trains*, but it trains perception —
listening, not speaking. Rhythm, linking and intonation are not learned from a score; they are
learned by speaking along with a model in real time. The app already synthesises a native
rendering of any text, already has a slow path (`tts.slow_ssml`, `rate="-35%"`, measured at
1.54× normal duration), and already has a Mode B assessment pipeline. Shadowing is those three
things wired into a practice loop — **a flow wrapped around the existing path, not a new
analysis path.**

It also carries its own acceptance test, and the test is free: a shadowed read and a cold read
of the same passage are both ordinary stored attempts, so the comparison costs nothing beyond
the reads themselves.

**Exit criterion.** A shadowed read and a cold read of the same passage render side by side
with their fluency and prosody delta named. Milestone v0.8.0 closed.

## The pre-registered finding

Written down *before* the data exists, so the outcome is a finding rather than a surprise or a
retrofit.

1. **A shadowed attempt should score higher on fluency and prosody than a cold read of the
   same passage.** If it does not, shadowing is not doing what the literature says it does —
   under these conditions, with this synthesiser, on this passage — and that is the result.
2. **That gap should NARROW over weeks**, as the shadowed pattern becomes the cold-read
   pattern. That is transfer, and it is the only thing that makes the practice worth the
   minutes.
3. **If the gap never narrows, the practice is not transferring and the design is wrong.**
   A stable gap means the model is a crutch that carries the read and puts nothing down.
   That reading is to be stated on the comparison surface, not explained away.
4. Accuracy and pronunciation are **not** expected to move. Shadowing trains delivery, not
   articulation. A large accuracy delta is more likely a measurement artefact (see the
   headphone constraint) than a result.

Neither 2 nor 3 is a merge gate. They are calendar items, the same shape as the benchmark's
30-day check and the perception trainer's "has never been *used*, only run".

## Scope, and what this deliberately is not

Shadowing is the one place in this design where the user practises against a model in real time
instead of reading a report afterwards. **No scoring surface is added to the practice flow
itself** — no live meter, no per-phrase feedback, no accuracy read-out while shadowing. When
the read is finished it is assessed as an ordinary Mode B attempt and rendered by the existing
`render_result`; no new scoring UI is written. The shadowed-vs-cold comparison lives on the
Progress tab, where every other measurement lives.

Nothing in the analysis pipeline changes: no new parsing branch, no new normalised shape, no
new merge rule.

## Decisions, and the findings behind them

### Two shadowing modes, only one of them assessed

- **Simultaneous (the assessed flow).** One continuous clip of the whole passage, normal or
  slow. The user presses record, presses play, and speaks along. The recording is continuous,
  so its fluency and prosody are directly comparable to a cold read — which is precisely what
  makes the free acceptance test valid.
- **Echo (an unassessed warm-up).** The passage split into sentences, each clip followed by a
  silence matched to that clip's own duration, concatenated into one track. Press play, repeat
  in the gaps. **This one is deliberately never assessed**: its recording would carry
  structural pauses between every phrase, so Azure would depress its fluency score by design
  and the delta against a cold read would be uninterpretable. Offering it as a warm-up is
  honest; scoring it would not be.

### Headphones are a requirement, not a suggestion

Both modes play a model voice while the microphone is open. On speakers, Azure hears the model
as well as the speaker and assesses a mixture — the scores would then partly measure the
synthesiser. The shadow surface says this plainly before the record widget, and the caption
stays on screen while shadowing.

### No rerun may happen between "record" and "play"

`st.audio_input` holds an in-progress `MediaRecorder` in the browser; a Streamlit rerun
re-renders that component mid-recording. So the model player and the record widget are both on
screen **before** recording starts, with the model on a plain `st.audio` (native controls, no
`autoplay`), and no button sits between them. This is a constraint on the layout, and it is on
the live verification list because it is exactly the kind of thing that only fails in a
browser.

### Tagging: a new additive table, `SCHEMA_VERSION` stays 1

`attempts` is created with `CREATE TABLE IF NOT EXISTS`, so a new column would need a real
`ALTER TABLE` migration and `db._migrate` has no upgrade path. The v0.7.0 precedent
(`practice_targets` / `perception_trials` added additively, `user_version` never moved) applies
directly: a new `attempt_tags(attempt_id, tag, created_at)` table with a unique
`(attempt_id, tag)` index. One tag value is defined today, `shadowed`; a second practice mode
later reuses the table rather than needing another one.

`rhythm.BASELINE_CAPTURE_MARKER`'s reference-text-prefix trick is **not** reusable here: the
comparison pairs a shadowed read to a cold read by matching normalised reference text, and a
marker in that text would break exactly the match the feature depends on.

### Shadowed reads must be kept off the benchmark trajectory — this is the correctness crux

`progress_view.is_benchmark` identifies a benchmark read by matching its reference text, so a
shadowed read of the benchmark passage would land on the headline progress line and on the
nPVI series **as if it were a cold read**. That inflates the exact line the whole benchmark
design exists to keep honest, and it corrupts the rhythm series doubly, since shadowing changes
rhythm by construction. So:

- `progress_view` gains `cold_attempts()` (spoken, minus shadowed) beside the existing
  `spoken_attempts()`. The score trajectory, the rhythm chart and `days_since_benchmark` all
  read `cold_attempts`. The shadow comparison is the one reader that wants both.
- Shadowed benchmark reads are drawn on the score chart as their **own dashed series**, not
  dropped — two lines converging is the acceptance test rendered.

**The flagged-phoneme, flagged-word and weak-syllable aggregates deliberately keep including
shadowed reads.** `_tally` counts the attempts a sound appeared in and `practice_queue.candidates`
thresholds on that cumulative count, so an assisted read can only ever *raise* a count, never
retire a target early. A sound still flagged while a model is carrying the read is stronger
evidence, not weaker.

### Scheduling: a fourth `shadow` kind in `practice_targets`

- `practice_queue.SHADOW` is added to `KIND_LABELS` but **not** to `KIND_ORDER` — `KIND_ORDER`
  drives promotion balance, and a shadow item is never promoted from evidence.
- `promote()` must count only promotable kinds toward `MAX_ACTIVE_TARGETS`, or a shadow row
  silently eats one of the three slots. One-line fix, and a test that pins it.
- `grade()` gets a `shadow` branch returning the state unchanged with `regressed=False`, so
  `apply_decisions` skips it; `graduation_rule(SHADOW)` says plainly that **nothing takes it
  off the list** — shadowing is a standing practice, not a target to clear.
- `next_due()` gets a `shadow` branch: `now + utils.SHADOW_INTERVAL_DAYS`, rather than the
  "active means due immediately" rule that the other kinds use.
- **The row is created when the user first shadows a passage, never by `promote()`.** The
  brief's "the queue never invents a target" rule is about claims made from the user's own
  evidence, and a standing practice makes no such claim. Until then Today simply offers
  shadowing as an available action.

### Passages: the benchmark plus the paragraph presets

Three shadowable passages, benchmark first. The benchmark is the one that already gets read
cold on a schedule, so its pairing has data from day one; the shorter presets are there for a
day when 196 words is too much, and the comparison surface says *"no cold read of this passage
yet"* per passage rather than silently showing nothing.

`PRESETS[Mode.PARAGRAPH]` is already exactly this set. `shadowing.py` stays pure and takes the
passage mapping as an argument rather than restating it — the same boundary `progress_view.py`,
`rhythm.py`, `perception_trainer.py` and `practice_queue.py` all hold.

### `MAX_DURATION_SECONDS_PARAGRAPH` moves from 120 to 180

The benchmark passage is 61.8 s through Azure TTS at the normal rate (measured, in
`tests/fixtures/benchmark_tts_baseline.json`). At `rate="-35%"` that is ~95 s, and a shadowed
read starts recording before the model and stops after it — ~105 s against a 120 s limit, with
no room for a shadower who trails the model. 180 s costs nothing that is not already spent:
a read is billed for its own seconds either way, and 18,000 s a month is the budget.
Default changes in `utils._DEFAULTS` and `.env.example`; the guard itself is untouched.

## Files

**New: `shadowing.py`** — pure, no Streamlit, no database, no clock. Holds `SHADOW_TAG`,
sentence splitting for the echo track, the per-passage identity (reusing
`progress_view.benchmark_key` as the normaliser so a passage cannot split into two series),
the instruction and headphone copy, and the scheduling helpers that `practice_queue` calls into.

**`audio_utils.py`** — `echo_track(clips, *, tail_ms)`: concatenate WAV clips with a silence
after each equal to that clip's own duration plus a short tail, using the `AudioSegment` import
already present. Returns WAV bytes. Pure and directly assertable on output duration and format.

**`db.py`** — the `attempt_tags` table in `_SCHEMA` (additive, `SCHEMA_VERSION` stays 1);
`tag_attempt()`; a `shadowed` column joined into `attempt_series` and `attempt_payloads` so
the progress readers can see it without a second query.

**`tts.py`** — no change. `cache_key`/`store_audio`/`cached_audio` already take a `rate`, and
`synthesise(slow=True)` already exists; the shadow clips use them as they are.

**`practice_queue.py`** — the `SHADOW` kind: `KIND_LABELS`, the `promote()` slot-count fix, the
`grade()` and `next_due()` branches, `graduation_rule(SHADOW)`.

**`progress_view.py`** — `is_shadowed(row)` (tolerant of a row without the key, in the style of
`evidence_of`), `ParsedAttempt.shadowed`, `cold_attempts()`, and the comparison:
`shadow_pairs()` (a frame pairing each shadowed read with the nearest cold read of the same
passage by time, carrying the fluency and prosody deltas and the days between them),
`shadow_summary()` (the delta named in a sentence, **always beside its n**), and
`shadow_gap_chart()` (the gap over time — the narrowing, or its absence). The score chart gains
the dashed shadowed series.

**`utils.py`** — `SHADOW_INTERVAL_DAYS` (3, a tuning value, commented as one), and the
`MAX_DURATION_SECONDS_PARAGRAPH` default.

**`app.py`** — `tags` threaded through `AssessJob` → `start_assessment` → `run_assessment_job`,
tagged inside the same `_DB_LOCK` block that records the attempt. A `SHADOW_KEY` session state
and `render_shadow()`, rendered in place inside `render_today` exactly as `render_block` already
is (`if st.session_state.get(BLOCK_KEY): render_block(conn); return`) — Streamlit cannot switch
tabs programmatically, and this is the pattern the app already uses for it. `render_target_card`
gets a shadow branch so it stops asking `db.trials_for` for perception trials a shadow item
never has. A `render_shadow_comparison()` section on the Progress tab.

**`.env.example`** — the new paragraph maximum, with the reason.

**Tests** — `tests/test_shadowing.py` (splitting, scheduling, passage identity),
plus additions to `test_audio_utils.py` (echo track duration and format), `test_db.py`
(tagging, the join, `user_version` still 1), `test_practice_queue.py` (the shadow kind, and
that a shadow row does not consume a promotion slot), `test_progress_view.py` (a shadowed read
is absent from the benchmark line and from the rhythm frame, present in the pairing; a pairing
with no cold read reports that rather than inventing one), and `test_app.py` (`AppTest` over
the Today shadow flow and the tag reaching the database).

## Verification

**Offline, no keys, no network** — `make test`. Current baseline is 556 tests.

**Browser, offline** (`make up`, `OFFLINE_MODE=true`): the shadow card appears on Today; the
model and echo buttons are **disabled** with the caption that synthesis is a live call by
definition and there is no audio fixture to replay — the same rule "Hear it" already follows.
Both themes.

**Live, one deliberate spend**, with the meter as the assertion — the v0.7.0 discipline:

1. Read the meter. Prepare the benchmark model clip at the normal rate (~975 characters) and
   confirm exactly one `tts_usage` row; press it again and confirm the disk cache charges
   nothing.
2. Build the echo track (14 sentence clips, ~975 characters at the normal rate) and confirm the
   per-clip rows and that a rebuild charges zero.
3. **Do one shadowed read of the benchmark passage on headphones** (~70 s of the 18,000 s
   allowance). Confirm: the recording survives the record → play → stop sequence with no rerun
   between (the constraint above); the attempt is stored; `attempt_tags` carries `shadowed`.
4. On Progress: the delta is named with its n, the shadowed point is on the dashed series and
   **not** on the benchmark line, and the rhythm chart is unchanged by it.
5. Report the total spend in characters and seconds, as every previous live run has.

If no cold read of the benchmark exists at that point, step 4 must render the *"no cold read
yet"* state rather than a number — and that state is itself worth seeing once, since it is what
the surface shows on day one.

## First step, before any code

`CLAUDE.md` requires the approved plan to live in the repo. Write this file to
`plans/2026-08-19_shadowing-practice-flow.md` and append its `planned` row to
`memory-bank/history.md` before the first edit to a source file.

## After it lands

`memory-bank/techContext.md` gains a "Shadowing practice" section (the two modes and why only
one is assessed, the headphone constraint, the no-rerun-during-recording constraint, the
`attempt_tags` precedent, and the cold-versus-shadowed split across the progress readers).
`memory-bank/progress.md` gains the release and the pre-registered finding above under what
this does **not** yet prove. `memory-bank/history.md` gets its `planned` row now and moves to
`implemented` when it lands.
