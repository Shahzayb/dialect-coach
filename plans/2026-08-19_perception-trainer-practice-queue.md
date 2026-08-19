# Perception trainer + practice queue (milestone v0.7.0)

## Context

The brief's stated problem is *"I can't hear the difference between my pronunciation and a
native speaker's."* Everything shipped so far diagnoses and explains; the closest thing to
training is "Hear it", which plays a target next to an attempt. That is **exposure**, and
exposure is the weakest intervention available. The established one is High Variability
Phonetic Training: forced-choice identification of minimal pairs, spoken by several
different talkers, scored immediately, in short daily blocks. Two findings drive the whole
design — perception gains transfer to production without production practice, and
**multiple talkers** are what makes the gain generalise to new words and new speakers. An
adult forms a new L2 sound category only once the sound stops being heard as a variant of
the nearest native one, so drilling production of a contrast you cannot hear is practising
without a target.

The second half is what makes daily use compound. Today the app has no memory between
sessions: every session opens on an empty textarea and ends with a routine that is
discarded. Thirty days of that is thirty separate first sessions. The queue persists a
small target set, schedules it, and answers "what am I doing today?" the moment the app
opens.

Two components, one chunk, because they are one surface: the queue decides what is trained
and the trainer is the first thing it schedules.

**No STT spend at all. TTS only.**

## Decisions taken before planning

- **Entry point**: a new **first tab, `Today`** — `Today | Practice | Progress`. The
  textarea stays exactly one tab click away.
- **Cold start**: no L1 hint. With no flagged history the queue says so and offers nothing,
  pointing at the benchmark read. Keeps *"the queue must never invent a target"* absolute
  and keeps `projectbrief.md`'s no-hardcoded-L1 non-goal intact.
- **All three item kinds ship now** (chosen by the user over my narrower recommendation).
  This is affordable because of a reframing found while exploring: a **vowel gap needs no
  formant work** — it is a flagged substitution whose expected phoneme is a vowel,
  diphthong or r-coloured vowel in `phoneme_reference`, so it trains through the same
  perception block as a consonant contrast. Only **stress** is a genuinely different kind;
  see the concern below.
- **"Unseen item" = an unheard `(word, voice)` combination.** Voice variety is the active
  ingredient, so a familiar word in a voice never heard for that contrast is a genuinely new
  stimulus. That gives 24–40 novel stimuli per contrast against 3–5 minimal pairs.

## One concern, stated plainly, then built

**A stress target has no scored check that costs no STT.** Azure emits no stress marks —
verified against the committed fixtures: `unpredictable` comes back as
`ʌn / pɹə / dɪk / tə / bəl` with accuracy scores and nothing else — so there is no way to
ask "which syllable was stressed?" and know the right answer without either a pronouncing
dictionary (a new dependency and a 130k-word data file) or re-recording (STT spend, which
this chunk excludes).

The brief already names the resolution: *"the due **block**, the due **drill**"*. So:

- **contrast** and **vowel** items → a **due block**: scored perception trials, graduating
  on accuracy.
- **stress** items → a **due drill**: the existing `fallback_coach` stress exercise on the
  word, plus the word played in each of the block voices. It graduates on **evidence drying
  up** — the word stops being flagged in new attempts — not on a self-reported score.

Two kinds of item, two graduation rules, both visible on screen with their actual numbers.
This is honest and needs no new dependency; a CMUdict-backed stress-location task is the
later upgrade and is recorded as such, not built here.

## Files

New: `perception_trainer.py`, `practice_queue.py`, `scripts/list_voices.py`.
Changed: [db.py](db.py), [tts.py](tts.py), [utils.py](utils.py),
[progress_view.py](progress_view.py), [app.py](app.py), [.gitignore](.gitignore).
Tests: `tests/test_perception_trainer.py`, `tests/test_practice_queue.py`, plus additions to
`tests/test_db.py`, `tests/test_tts.py`, `tests/test_progress_view.py`, `tests/test_app.py`.

`perception_trainer.py` and `practice_queue.py` are **pure and never import Streamlit** —
the same boundary [progress_view.py](progress_view.py) and [rhythm.py](rhythm.py) already
sit on, which is what makes a block plan and a graduation decision assertable in a test.
`app.py` owns every `st.*` call.

### Step 0 — repo bookkeeping (CLAUDE.md §2)

Copy this plan to `plans/2026-08-19_perception-trainer-practice-queue.md` and append a
`planned` row to `memory-bank/history.md` **before** writing any code.

### Step 1 — verify the voice roster by introspection

`scripts/list_voices.py`: build a `SpeechSynthesizer` and call `get_voices_async("en-US")`,
printing name, gender and voice type. This lists voices; it synthesises nothing, so it
charges no characters — assert that against `db.monthly_tts_characters` before and after.

```bash
docker compose run --rm app python scripts/list_voices.py
```

Only after that run, hardcode `perception_trainer.VOICES` — **at least four en-US neural
voices varied in sex and timbre**. Do not guess names; the roster changes without notice.
The list is a module constant, not an env var: the sex/timbre balance is a design property
of the intervention, not a config knob someone should be able to flatten to one name.
`AZURE_TTS_VOICE` is untouched and stays the single voice for "Hear it" elsewhere —
imitation wants one consistent model, identification wants variety, and the two settings
pull in opposite directions on purpose.

**If fewer than `MIN_VOICES` (4) are usable at runtime, refuse to start the block** rather
than degrade to fewer. A one-voice block trains talker-specific listening, which is the
exact thing HVPT exists to avoid.

### Step 2 — conventions in `utils.py`

Added under a comment block labelled the way `WORD_RED` already is — *heuristics this
project chose, not values Azure or any published protocol fixes*:

```
PERCEPTION_BLOCK_TRIALS = 20        PERCEPTION_REVIEW_TRIALS = 10
PERCEPTION_GRADUATE_ACCURACY = 0.90 PERCEPTION_GRADUATE_BLOCKS = 2
PERCEPTION_REGRESS_ACCURACY = 0.75  MAX_ACTIVE_TARGETS = 3
REVIEW_INTERVAL_DAYS = (3, 7, 21, 60)
MIN_PAIRS_FOR_BLOCK = 3             RECUR_ATTEMPTS = 2
```

**The chance floor is not one of them.** It is arithmetic —
`perception_trainer.chance_floor(alternatives) -> 1.0 / alternatives` — with a comment
saying so, because a two-alternative forced choice scores 50% by guessing and showing "62%"
without that anchor reports near-noise as progress.

### Step 3 — schema (`db.py`), additive, `SCHEMA_VERSION` stays 1

Both tables go into `_SCHEMA` as `CREATE TABLE IF NOT EXISTS`, so an existing v1 database
gains them on the next `connect()` and `user_version` never moves. Same precedent as the v1
coaching columns.

```sql
practice_targets(
  id, item, kind,          -- kind: contrast | vowel | stress
  added, last_seen, next_due,
  state,                   -- active | graduated
  reviews_passed INTEGER NOT NULL DEFAULT 0,
  evidence TEXT NOT NULL)  -- JSON; the counts it was promoted on, verbatim
UNIQUE INDEX (item, kind)

perception_trials(
  id, block_id, target_id REFERENCES practice_targets(id), created_at,
  item, word, voice,       -- item denormalised: a deleted target keeps its history
  novel INTEGER,           -- 1 when this (word, voice) had never been presented
  alternatives INTEGER,    -- 2 today
  answered TEXT, correct INTEGER, review INTEGER DEFAULT 0)
INDEX (block_id)
```

Two deliberate details:

- **`reviews_passed` is one column beyond the brief's list.** The alternative is stuffing a
  schedule pointer into `evidence`, which is for evidence.
- **`alternatives` is stored per trial**, so the chance floor is a *stored fact* rather than
  an assumption baked into the reader. If a three-alternative task ever ships, old rows keep
  reporting their own floor.

New readers/writers in `db.py`: `upsert_target`, `set_target_state`, `targets`,
`record_trial`, `trial_rows`, `block_summaries`. All SQL stays here; all policy stays in
`practice_queue.py`.

### Step 4 — TTS disk cache (`tts.py`)

`cache_key(voice, text, rate)` = SHA-256 of `voice \0 text \0 rate`; `cache_path`,
`cached_audio`, `store_audio`. Directory `./data/tts_cache` (`TTS_CACHE_DIR`).

- **Plain text at normal rate, never SSML.** The meter charges the payload actually sent and
  an SSML payload bills its full markup: one 8-character word wrapped in SSML measured 167
  characters. `rate` stays in the key so a slow variant is a different entry, not a
  collision.
- **The docstring states the privacy boundary explicitly**: this caches *synthesised* audio,
  which carries no personal data, and the no-stored-audio rule covers the user's recordings.
  Never a byte of recorded input.
- **`data/` goes into `.gitignore` in the same commit that writes the first cache file.**
  `.gitignore` currently matches `*.db` and its sidecars but not `data/` itself.
- **The disk lookup happens before the pre-flight**, exactly as `app.play()`'s session-cache
  lookup does today — metering ahead of a cache check would charge the meter again on every
  unrelated Streamlit rerun.

Deliberately **not** extended to `app.play()`'s "Hear it" buttons in this chunk: that path is
single-voice and session-cached, and changing its spend behaviour is a separate call.

### Step 5 — `perception_trainer.py` (pure)

- `trainable(expected, produced)` — a contrast exists in `phoneme_reference` with
  `>= MIN_PAIRS_FOR_BLOCK` minimal pairs. Reuses `phoneme_reference.contrast` /
  `minimal_pairs` / `lookup`; the `kind` field on `Phoneme` is what splits *contrast* from
  *vowel gap*. Contrasts with no pairs (11 of 88, all honest empties) and
  `progress_view.UNCLEAR` labels are excluded.
- `build_block(contrast, voices, seen, trials, rng)` → a list of `Trial(word, pair, voice,
  alternatives, novel)`.
  - Stimuli are `(word, voice)` over both words of every pair × every voice.
  - **Unseen preferred**, then least-recently-heard. The block's novel fraction is reported.
  - No two consecutive trials share a voice or a pair where the stimulus pool allows it.
  - The two on-screen alternatives are shuffled per trial so button position carries no
    information.
  - `rng` is injected and seeded in tests, so a block plan is asserted rather than sampled.
- `stimuli_needed(block)` — every `(word, voice)` in the block **plus the other word of each
  pair in the same voice**, since "replay both" is on demand.
- `score(trials)` → `BlockResult(correct, total, accuracy, chance_floor, novel_fraction)`.

### Step 6 — `practice_queue.py` (pure policy)

Evidence comes from the same aggregates the Progress tab already draws, so the queue and the
chart can never disagree about what recurs:

- `progress_view.flagged_phonemes(parsed)` — already exists.
- **`progress_view.weak_syllables(parsed)`** — new, added beside it and sharing the existing
  private `_tally`, so "recurs across attempts" has one definition. A multi-syllable word
  whose weakest syllable is below `fallback_coach.SYLLABLE_RED`, which is the heuristic
  already shipped and rendered in the coaching report.

Functions:

- `candidates(phoneme_frame, syllable_frame)` → ranked `Candidate(item, kind, evidence)`.
  Kind is `contrast` / `vowel` from `Phoneme.kind`, or `stress`.
- `promote(existing, candidates, now)` → fills to `MAX_ACTIVE_TARGETS`, **preferring one of
  each kind before doubling up on any kind**, so three consonant contrasts cannot crowd out
  a vowel gap. Nothing is promoted that is not in `candidates` — the queue orders and
  schedules what the analysis found and never invents a target.
- `grade(target, blocks)` → `Decision(state, reason)`. A **contrast/vowel** graduates at
  `>= 90%` across two blocks; regresses below 75% on a review. A **stress** item graduates
  when its word stops appearing in the flagged aggregate. `reason` is prose carrying the
  real numbers and is rendered verbatim — the brief requires the rules be visible, not
  implicit.
- `schedule(target, now)` → `next_due` from `REVIEW_INTERVAL_DAYS[reviews_passed]`.
- `due(targets, now)` → what is due, ordered.

### Step 7 — `app.py`: the `Today` tab

`st.tabs(["Today", "Practice", "Progress"])`. **Streamlit executes every tab body on every
rerun**, including the 0.4 s poll reruns during an assessment, so `Today` reuses the existing
`@st.cache_data` `parsed_attempts` and caches its own candidate ranking on the same
`db.attempt_fingerprint` key. Nothing here re-parses payloads per rerun.

The tab, in order:

1. **The one due thing**, stated as an answer: the due block, or the due drill, with a Start
   button.
2. **The ≤3 active targets.** Each shows the kind, the item, *why it is here* (the verbatim
   evidence counts), *what removes it* (the graduation rule with its actual numbers),
   accuracy so far **always beside its chance floor**, and the next due date.
3. **Graduated items** with their next review date — a graduated contrast that is never
   re-tested is an unverified claim.
4. Nothing due → says so. No history at all → explains that targets are promoted from
   assessed attempts and points at Practice.

**Block UI**: one trial per rerun. Play the word (autoplay), two buttons, score immediately,
reveal the answer with `phoneme_reference.why_it_matters` for the pair, offer replay of both
words, then Next. State in `st.session_state["block"]`.

- **All of the block's audio is synthesised up front**, before trial 1, as one batch: check
  the disk cache, `budget.preflight_tts` the uncached remainder priced at
  `chars × MAX_SYNTHESIS_ATTEMPTS`, synthesise, `db.record_tts_usage` each, write to disk.
  No lag mid-block and one visible charge.
- **Trials are written per answer, not at block end** — "store the evidence, not only the
  verdict". A block only *counts toward graduation* when its trial count is complete;
  completeness is derived from the rows, so an abandoned block keeps its evidence without
  earning a verdict.
- Alerts never render inside an `st.columns` entry; the existing `play()`
  return-an-(icon, message)-pair convention applies to every new helper called from a column.

### Step 8 — the chart (`progress_view.py`)

`perception_frame(trial_rows)` → per `(item, block)` accuracy with the block date and the
chance floor derived from the stored `alternatives`. `perception_chart(frame)` draws it
faceted by item, y pinned 0–100, with a **dashed rule at the chance floor** — the same
device `rhythm_chart` uses for the TTS baseline. Rendered on the Progress tab under the
existing charts.

## Verification

1. `make test` — offline, no keys, no network. New coverage: a block cycles ≥4 distinct
   voices and never repeats a voice or pair back-to-back; unseen stimuli are preferred; a
   seeded RNG produces a deterministic plan; `build_block` refuses under 4 voices; the chance
   floor comes from `alternatives`; promotion caps at 3, prefers one per kind, excludes
   `(unclear)` and sub-3-pair contrasts, and promotes nothing absent from the flagged
   aggregate; graduation, regression and widening intervals; both new tables appear on a v1
   database with `user_version` unchanged and survive a reconnect; the chance rule is present
   in `perception_chart(...).to_dict()`.
2. `docker compose run --rm app python scripts/list_voices.py` — the roster, and
   `monthly_tts_characters` unchanged across the call.
3. `python scripts/seed_progress_history.py`, then the browser: `Today` promotes real items
   from the seeded flagged history (`/θ/ → /t/`, `/v/ → /w/`, `/ð/ → /d/`, `/l/ → /ɹ/`), each
   showing its evidence and its graduation rule.
4. **A live block end to end, asserted against the meter.** Record
   `db.monthly_tts_characters` before and after: the delta must equal the summed payload
   lengths of exactly the uncached words, and a **second block on the same contrast must
   charge 0** — the disk cache. Expected first-block cost is ~200 characters against a
   500,000/month allowance.
5. **Restart the container.** `Today` shows the same targets, states and due dates.
6. Progress tab: per-contrast accuracy plotted against the 50% chance line.

## Cost

The brief's ~2,000-character estimate assumed ~40 pairs; `phoneme_reference` actually holds
**88 contrasts and 280 pairs over 282 distinct words (1,152 characters)**. At four voices
the entire corpus is ~4,600 characters if every contrast were ever trained — still under 1%
of the monthly allowance, and it is never paid at once: synthesis is lazy, per block, and
cached to disk permanently.

## Explicitly not in this chunk

- Extending the disk cache to `app.play()`'s "Hear it" buttons.
- A CMUdict-backed stress-*location* identification task (the upgrade path for stress).
- Any new minimal pairs in `phoneme_reference`.
- Any STT call, any recording, and any change to `AZURE_TTS_VOICE`.

## On landing

Update `memory-bank/progress.md` and `techContext.md` per CLAUDE.md §3 — verified facts
written directly, judgment calls proposed first — and move the `history.md` row to
`implemented`. Milestone v0.7.0.
