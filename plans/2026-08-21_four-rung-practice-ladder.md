# #39 — The three-way listen as the practice surface, on a four-rung ladder

## Context

The app listens extremely well and then stops. [#35](https://github.com/Shahzayb/dialect-coach/issues/35)
names [#39](https://github.com/Shahzayb/dialect-coach/issues/39) as the centre of the v1.0.0
round: hearing your own voice adjusted toward native is the most useful thing this project can
do, and today it is a three-button demo buried inside the Accent tab's pitch overlay, operating
on a whole recording, with no native rendering beside it.

This round promotes it to **the** practice surface, complete — and puts it on the ladder the
brief describes: **sound → word → sentence → paragraph**. A thing is only resolved once it
survives at the rung above ([#42](https://github.com/Shahzayb/dialect-coach/issues/42)), because
in isolation you hyperarticulate and in context you reduce.

Not in this round: deriving the shortlist automatically out of an analysis
([#37](https://github.com/Shahzayb/dialect-coach/issues/37)), the wider cross-session queue work
in [#40](https://github.com/Shahzayb/dialect-coach/issues/40), retiring shadowing
([#38](https://github.com/Shahzayb/dialect-coach/issues/38)).

## Decisions taken

| Decision | Choice |
| --- | --- |
| Units | Four rungs: sound → word → sentence → paragraph |
| Audible three-way listen | Word, sentence, paragraph. **Sound is measured-only** — you hear it inside its word |
| Repetition cost | Hybrid — free local verdict per rep; explicit "bank this" spends Azure |
| Resolution | Measured only: arrival **and** movement. Exits are measured / bailed / reopened. No manual resolve |
| Resolvable thing | One problem at one rung, not a whole unit |
| Arrival bands | **Derived locally at all four rungs** from stored payloads and stored WAVs. Zero Azure spend |
| Cascade | Enter at the lowest open rung; a failure above **automatically reopens** the rung below |
| Sound rung | Reuse `CONTRAST` / `VOWEL` targets and the perception trainer; add the survives-in-word check |
| Native leg audio | Stored renderings; capturing a new text is explicit and priced |
| Corrections | **One thing at a time — no stacking.** Pitch, timing and vowel stay separate, each changing exactly one thing |
| Banked reps | Ordinary `attempts` rows tagged `rep`, excluded from the Progress cloud |
| Placement | Opens in place on **Today**, the way a perception block already does |

Placement rationale: these problems are queue targets, `render_today` is already the entry point
that answers "what am I doing today?", and `start_block` / `render_block`
([src/app.py:3745](src/app.py:3745)) is the existing precedent for opening a practice surface in
place rather than adding a fifth tab.

## Work, in order

### 1. The ladder — `src/ladder.py` (new, Streamlit-free)

One module defining the four rungs and the spans that realise them on audio.

- Sentence split reuses `shadowing.phrases()` ([src/shadowing.py:101](src/shadowing.py:101)) —
  it already merges one-word fragments and survives a passage that does not split cleanly.
- Word and sound spans come from the `offset_ticks` / `duration_ticks` that
  `speech_analyzer.normalise` puts on every word and phoneme.
- The same functions serve a user attempt and a `native_model.Rendering`, since
  `Rendering.words()` returns the identical normalised shape
  ([src/native_model.py:248](src/native_model.py:248)).

**The edge-splice landmine, now on the critical path.** Praat's `Extract part` with an empty
range returns the *whole sound* — recorded in `progress.md`'s dead ends, and it once spliced a
full recording back in. The word rung is audible in this round, so word-span extraction runs at
exactly the fragility #39 warns about. Every extraction skips zero-length parts explicitly, with
tests that fail against a first-word span and a last-word span.

### 2. Arrival bands at all four rungs — `scripts/build_ladder_reference.py` → `src/ladder_reference.py`

**No capture, no Azure, no spend.** Everything needed is already stored:

| Rung | Band derived from |
| --- | --- |
| Sound | `ReferenceVowel.sd50` across 8 voices per set ([src/vowel_reference.py:79](src/vowel_reference.py:79)) — exists today |
| Word | Per-word scores and durations off the 16 stored payloads; formants off the stored WAVs via parselmouth |
| Sentence | Contour spread and nPVI across the 16 voices, from stored payload offsets and stored audio |
| Paragraph | The 16 renderings *are* whole-passage assessments already in the database |

`native_model.renderings_for` is documented as *"the population a between-voice band is drawn
from"* ([src/native_model.py:288](src/native_model.py:288)) — this is that, at four scales.

Generated file, never hand-edited, following `model_reference.py`'s header convention and the
project rule that no reference number is ever typed from memory.

**The one honest gap:** Azure's `ProsodyScore` does not decompose below the utterance it was
computed on, so the prosody arrival bar below paragraph level is **acoustic** (contour spread,
nPVI), not Azure's own number. The surface says which instrument judged it.

### 3. Free per-repetition verdict — `src/ladder.py`

Pure parselmouth, no network: distance from the target contour via `accent_resynth.pitch_track`
and `semitones`, plus interval durations and formants. Reports both bars separately per
instrument — inside the native band (arrival) **and** past the speaker's own noise floor
(movement, via `vowel_measure.NoiseFloor.within_noise` at [src/vowel_measure.py:1214](src/vowel_measure.py:1214)).

Azure's phoneme-level scores are **dark during free repetition** and the surface says so:
`vowel_measure.extract` needs Azure's phoneme offsets ([src/app.py:851](src/app.py:851)), so they
light up only on a banked attempt.

### 4. Corrections at rung scale — `src/accent_resynth.py`

**No stacking.** `corrected_pitch`, `corrected_timing` and `corrected_vowel` stay three separate
corrections, each changing exactly one thing, each individually capped and each declaring its
cap — so "your voice, one thing changed" stays literally true and a corrected clip is always one
controlled difference from the original.

The only change is scope: each takes a rung's span rather than the whole recording. `#39` asks
for a single button giving the full transformation; this round deliberately does not do that, and
the issue should be updated to say so rather than left implying otherwise.

**Still worth a by-ear check, in reduced form.** Corrected pitch was confirmed to still sound
like the speaker on 2026-08-20 — over a whole paragraph. At word scale it is unverified, and a
short span is where a manipulation is most likely to sound artefactual. One manual listen at word
and sentence length before the round closes.

### 5. The surface — `render_ladder_practice` in `src/app.py`, opened from Today

For the current rung: **1. Mine**, **2. Native** (one sex-matched voice sliced from a stored
rendering — the averaged contour still drives the correction, since audio cannot be averaged),
**3. Mine, one thing changed** — you pick which one. Original always first and labelled, keeping
`render_resynthesis`'s existing rule and its heading ([src/app.py:2913](src/app.py:2913)).

Then: record again → instant free verdict → repeat, with **Bank this attempt** (spends Azure,
priced through `budget.preflight_stt` first) and **Drop this one** always available. Bailing must
not stall the queue, must not read as failure, and must not re-offer the same thing next.

Where a text has no stored renderings the surface refuses, showing the capture price — the
`plot_gate` refusal pattern ([src/app.py:2088](src/app.py:2088)), never a caveat over a drawn
panel.

**Guard:** a word or short sentence will fall below `MIN_DURATION_SECONDS`, so banking would
raise from `audio_utils.validate_duration`. Check before offering the button, and say why when
the rung is too short to bank.

### 6. Queue state and the cascade — `src/practice_queue.py`, `src/db.py`

A target is one **problem at one rung**, which fits `practice_targets`' existing unique index on
`(item, kind)` ([src/db.py:79](src/db.py:79)).

- **Entry** is always the lowest open rung.
- **Resolution** is measured only — arrival and movement, on the same instrument, checked at the
  rung above.
- **Reopening is automatic** when a resolved rung stops surviving above it, and also available by
  hand. Measured reopening has precedent: a contrast target already returns to the list when a
  spaced review drops below `PERCEPTION_REGRESS_ACCURACY`. An automatic reopen must read as
  information, not punishment — it names what stopped surviving and where.
- **Sound rung keeps its machinery.** Perception trials and vowel drills are unchanged; the only
  addition is that graduating a sound now also requires it to survive inside its word. That turns
  the existing 90%-across-two-blocks rule into a first rung rather than a dead end — and it means
  the perception trainer's graduation rule, which has never yet fired on real listening, gains a
  second condition before it can.
- `graduation_rule` ([src/practice_queue.py:334](src/practice_queue.py:334)) is rendered verbatim
  beside every target, so each rung's rule sentence must state its real band, its level-above
  requirement, and that reopening can happen on its own — otherwise the text on screen
  contradicts the behaviour.

### 7. Keep reps out of the Progress cloud — `src/progress_view.py`

Banked reps carry a `rep` tag in `attempt_tags`. `progress_view` already filters on tags for
`shadowed` ([src/progress_view.py:227](src/progress_view.py:227)); reps use the same mechanism.
Demoted, not deleted — ten dots for ten repetitions of one word would measure text difficulty
rather than the speaker, which is #37's complaint arriving out of the fix for it.

## Verification

- **Unit tests**, each failing against the bug it covers: first-word and last-word span
  extraction (the `Extract part` edge case), span mapping agreeing between a user attempt and a
  rendering, both resolution bars independently, the cascade reopening a lower rung when the one
  above fails, a sound refusing to graduate on perception alone, and `rep`-tagged attempts absent
  from the progress frame.
- **`AppTest`** for the loop: open the lowest rung, repeat without banking (assert no Azure call),
  bail, confirm the queue offers something else and is not stuck.
- **Live in the browser** via `mcp__playwright__*` against a seeded database — three legs play in
  order at word and sentence scale, the verdict updates per repetition, banking produces a tagged
  attempt, the Progress cloud does not grow, and a forced failure at the rung above visibly
  reopens the one below. Do not reload the page mid-session: a full navigate wipes session-local
  state silently.
- **The by-ear check** (step 4) — a corrected word and a corrected sentence still sound like you.
  No test substitutes for it.
- **`make test`** and the lint/type gate before the branch is offered.

## Risks

- **Word-span splicing is the fragile part**, and this round makes it audible rather than
  avoiding it. The `Extract part` empty-range bug has bitten here before; tests at both clip edges
  are the mitigation, and a word that cannot be cleanly extracted must refuse rather than play
  something wrong.
- **A word-scale correction may sound artefactual.** Pitch correction was confirmed by ear over a
  paragraph, never over a single word, and a short span is where a manipulation is most likely to
  break. Checked by ear, not assumed.
- **#39 asks for one combined button and this round deliberately does not build it.** The issue
  needs updating to record that, so the gap is a decision on the record rather than something
  unfinished.
- **Prosody's arrival bar below paragraph level is acoustic, not Azure's number.** A real gap,
  stated on the surface rather than papered over.
- **Day one the surface works on texts with stored renderings** — the benchmark passage — so
  nothing unscripted, which the brief calls the register that matters most. Accepted cost of one
  coherent population behind the native leg, the pitch target and every band.
- **Four rungs is a lot of state.** If it proves too much mid-implementation, the honest move is
  to stop and say so rather than quietly collapsing rungs.

## Repo bookkeeping

Per `CLAUDE.md`, once approved: write this to `plans/2026-08-21_four-rung-practice-ladder.md` and
append a `planned` row to `memory-bank/history.md` **before** any code.
