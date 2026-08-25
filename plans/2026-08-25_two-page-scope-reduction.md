# Scope reduction: two pages, Azure-first

## Context

The project grew to 23,362 lines across 28 `src/` modules and four tabs (Today, Practice,
Progress, Accent) — a perception trainer, a shadowing flow, a four-rung practice ladder, an
accent measurement engine with Praat formant analysis, resynthesis, progress charts and a
benchmark trajectory. `memory-bank/projectbrief.md` describes two pages. The code no longer
matches the brief, and most of the extra surface never accumulated enough real evidence to be
trustworthy (`progress.md` lists five separate "live evidence still needed" items against it).

`progress.md` already records this cull as the next concrete work. This plan executes it:
strip to **Analyze** and **History**, keep every Azure result, and delete the rest behind a
git tag so the code is recoverable but out of the way.

## Decisions (from the planning conversation)

| Question | Decision |
| --- | --- |
| Archive mechanics | Tag `v0.12.0-full` at the current commit, then **delete**. `plans/` + `history.md` keep intent; the tag keeps the code. |
| Scripted path | **Continuous only.** `Mode.DRILL` (single-shot) is removed. Omissions/insertions come from the local aligned diff — Azure's native miscue detection is given up deliberately, because unlimited scripted length requires continuous mode. |
| Limits | Delete `MAX_DURATION_SECONDS_*` and `validate_duration`. **Keep `budget.py`** and the meter — F0 cannot bill, and the guard is the second line of defence against an S0 misconfiguration. |
| Azure results | Every documented field rendered as human UI. **No** raw-JSON expander (the payload stays in the DB for re-parsing, unshown). |
| Gemini | **Only** prosody markup: given the reference text (scripted) or the transcript (unscripted) plus the Azure evidence, return the same words annotated with stress, pauses and linking. Not coaching, not content scoring. |
| Native audio | **Plain TTS playback** (whole text + per-flagged-word citation form), cached in `native_renderings`. `native_model.py` is deleted. |
| "How I said it" | All three: audio sliced from the recording at Azure word offsets, expected→produced IPA, and the syllable-stress line. |
| Page shape | **Two tabs, one script.** Preserves the `AppTest.from_file` pattern that every surviving test uses. |
| `app.py` | **Surgical strip in place.** Survivors keep their comments verbatim — they encode Streamlit and Azure facts a rewrite would silently drop. |
| Version / branch | **v0.13.0** on `claude/feature-scope-architecture-87ce30`. |

## Step 0 — archive point

Tag the current commit `v0.12.0-full` and push the tag **before the first deletion**. This is
the only recovery path for the deleted modules and must exist first.

## Step 1 — delete modules

Delete outright:

`perception_trainer.py`, `practice_queue.py`, `shadowing.py`, `ladder.py`,
`ladder_practice.py`, `ladder_reference.py`, `progress_view.py`, `accent_charts.py`,
`accent_view.py`, `accent_resynth.py`, `vowel_measure.py`, `vowel_reference.py`,
`model_reference.py`, `native_model.py`, `acoustics.py`, `rhythm.py`, `content_score.py`.

`rhythm.py` goes because nPVI is only honest against the same passage rendered through
Azure TTS and re-assessed by the same pipeline (`techContext.md`) — and that pipeline is
`native_model.py`, which the plain-TTS decision removes. A standalone nPVI number has nothing
to compare against.

**Before deleting `ladder.py`**, lift `slice_wav` ([src/ladder.py:470](src/ladder.py:470)) into
`audio_utils.py`. It is pure-stdlib `wave` frame arithmetic with no Praat dependency and
`audio_utils.py` is its natural home — this is the "how I said it" audio path. Take
`_framerate` with it; leave `cut_with_offset`/`rebase`/`Span`/`Alignment` behind.

Survivors: `app.py`, `speech_analyzer.py`, `db.py`, `utils.py`, `audio_utils.py`, `tts.py`,
`budget.py`, `fallback_coach.py`, `ai_coach.py` (rewritten), `phoneme_reference.py`,
`stress_lexicon.py`.

## Step 2 — `speech_analyzer.py`: two modes

- Remove `Mode.DRILL` from `utils.Mode`, `_assess_single_shot`, and the `enableMiscue` branch
  at [src/speech_analyzer.py:184](src/speech_analyzer.py:184) (always `False` now).
- `FIXTURES` keys on the two surviving modes; the `FIXTURES[Mode.DRILL]` fallback at
  [src/speech_analyzer.py:438](src/speech_analyzer.py:438) must point at the continuous fixture.
  Delete `sample_azure_response.json` (single-shot shape) if nothing else reads it.
- **Compatibility:** existing DB rows carry `mode = 'drill'`. History must render them. Read
  the column as a string and map unknown/legacy values onto the scripted path rather than
  round-tripping through the enum — do not migrate or rewrite stored rows.
- Keep `UNSCRIPTED_TWO_PASS` two-pass Mode C behaviour and its billing exactly as it is.

## Step 3 — limits off

Delete `validate_duration` ([src/audio_utils.py:87](src/audio_utils.py:87)) and its call in
`prepare`, plus `MAX_DURATION_SECONDS_*` from `utils.py` defaults and `.env.example`. Keep
`MIN_DURATION_SECONDS` — a 0.2 s recording is a misclick, not a long read. Keep `budget.py`,
its meter line, and the tier acknowledgement untouched. Raise or drop
`CONTINUOUS_TIMEOUT_SECONDS` so a long read cannot hit the backstop before Azure stops.

## Step 4 — `ai_coach.py` becomes the prosody annotator

Rewrite around one call. Keep the three structural rules the module header names — enforced
JSON schema, compacted evidence, learner text delimited and treated as data — and keep the
"never invent a phoneme not in `observed_pairs`" filter, applied now to the annotation.

- Input: the reference text (scripted) or `assessment.scored_against`/`recognised_text`
  (unscripted), plus the compacted Azure evidence from `fallback_coach.compact`.
- Output: the **same word sequence**, with per-word stress emphasis, break positions, and
  linking. Validate that the returned words match the input words before rendering; a model
  that changed the words is dropped, not shown.
- Failure of any kind renders the plain text. No error reaches the user.
- Delete the Gemini coaching path. `fallback_coach.py` is now the sole source of "what's
  wrong + recommended exercises" — it already owns the report schema and drill templates, and
  already guarantees a delivery fault gets a drill.
- Audio still never goes to Gemini.

## Step 5 — `app.py` strip

Delete every render function belonging to a deleted module: the accent surface
(`render_accent*`, `render_calibration`, `render_baseline`, `render_room_check`,
`render_pitch_overlay`, `render_resynthesis`, `render_rhythm_chart`, `_correct_worst_vowel`
and friends), the ladder block (~`open_ladder` through `render_ladder_bank`), shadowing,
perception blocks, the practice queue, `render_progress`, `render_today`, `render_content*`,
and `render_rhythm`.

Rebuild `render()` as two tabs:

**Analyze** — lifted from `render_practice`/`render_result`:
- `st.radio` over the two surviving modes; preset picker and text area (the same widget holds
  a *prompt* in unscripted mode — keep that comment).
- `st.audio_input` behind the existing generation key, worker-thread assessment via
  `start_assessment`/`collect_finished_job`, Stop and cancel semantics unchanged.
- Results: transcript, all Azure scores, error counts, coaching (`fallback_coach`), the
  Gemini prosody annotation, the reference-vs-heard diff (scripted only), colour-coded text,
  whole-text native playback, your recording, flagged word cards, delivery faults.
- Flagged word cards gain **your own audio for that word**, sliced with the relocated
  `slice_wav` at the word's Azure offsets, next to the existing native citation-form playback,
  the expected→produced IPA row, and the syllable-stress line.
- New: render the remaining documented Azure fields that today's UI drops — per-word and
  per-phoneme offsets/durations, `NBestPhonemes` alternates, syllable offsets, and SNR.

**History** — replaces `render_history` ([src/app.py:4630](src/app.py:4630)):
- Paginated over `attempts`, newest first, **including `offline = 1` rows** (labelled as
  fixture replays).
- A scripted/unscripted filter, reading the raw `mode` string so legacy `drill` rows appear.
- Per-row delete: removes the attempt, its `attempt_audio` file on disk, and cascaded rows.
  Confirm before deleting — nothing in the app can currently destroy history.
- Opening a row renders the Analyze result body with the recording and input controls hidden,
  from stored `azure_raw_json` + `coaching`. If the native TTS was never cached for that text,
  **synthesise it on open** (it meters and writes to `native_renderings` like any other buy).

Keep `_DB_LOCK`, the generation-key reset, the "never render an alert inside a narrow
`st.columns` cell" rule, and the TTS-cache-before-metering ordering.

## Step 6 — DB

**No schema changes and no drops.** `practice_targets`, `perception_trials`, `attempt_tags`,
`speaker_baseline`, `vowel_measurements` hold real recorded data (calibration reads, trial
answers) and dropping them is unrecoverable. Leave the DDL in `_SCHEMA` and stop reading and
writing them. `user_version` does not move.

Delete only the now-unreferenced *readers* in `db.py` (`upsert_target`, `record_trial`,
`save_baseline`, `record_vowel_measurements`, `queue_fingerprint`, and siblings). Keep
`attempt_audio`, `native_renderings`, `tts_usage`, `attempt_content_scores` (rows stay; the
writer goes). Add the paginated reader and a count alongside `recent_attempts`
([src/db.py:517](src/db.py:517)), plus a `delete_attempt`.

## Step 7 — tests, scripts, dependencies

- Delete the test files for deleted modules: `test_accent*`, `test_vowel_*`, `test_ladder*`,
  `test_model_reference`, `test_native_model`, `test_perception_trainer`, `test_practice_queue`,
  `test_progress_view`, `test_shadowing`, `test_rhythm`, `test_acoustics`, `test_content_score`,
  `test_coach_geometry`. Roughly 22 of 42 go.
- Adapt the survivors: `test_app`, `test_render`, `test_unscripted_app`, `test_parsing`,
  `test_merge`, `test_db`, `test_budget`, `test_audio_utils`, `test_tts`, `test_cancellation`,
  `test_offline_guard`, `test_fallback_coach`, `test_phoneme_reference`, `test_stress_lexicon`,
  `test_utils`, `conftest`. Rewrite `test_ai_coach` against the annotator.
- New coverage: History pagination and delete, legacy `drill`-row rendering, the relocated
  `slice_wav`, and the annotator's word-sequence validation rejecting a changed sequence.
- Delete scripts for deleted features: `build_ladder_reference`, `build_model_reference`,
  `build_vowel_reference`, `capture_baseline`, `capture_model_reference`, `content_probe`,
  `seed_accent_demo`, `seed_progress_history`, `rederive`. Keep `setup`, `smoke_test`,
  `capture_fixture`, `list_voices`, `pronunciation_test`, `coach_test`.
- Drop `praat-parselmouth` and `numpy` from `requirements.txt` **only after**
  `grep -rn "parselmouth\|numpy" src/ scripts/ tests/` comes back clean — `db.py` mentions
  parselmouth and needs checking before the pin is pulled. `pandas`/`altair` go with
  `progress_view.py` unless something else imports them.

## Step 8 — records

- `memory-bank/projectbrief.md`: already correct; confirm no edit needed.
- `memory-bank/techContext.md`: delete the Learning surfaces and Accent measurement sections
  and the Mode A contract. Add one **Archived** section mapping each removed feature to its
  plan file and the `v0.12.0-full` tag.
- `memory-bank/progress.md`: replace "Next concrete work" and the accent/perception/shadowing
  evidence items with the new two-page state.
- `memory-bank/history.md`: append this plan's row.
- Bump `pyproject.toml` to `0.13.0`.

## Verification

1. `make check` (ruff + strict mypy + pytest) in the container — this is the gate.
2. `make up`, then drive with Playwright MCP: record scripted, assert scores, flagged word
   cards, own-word audio, native playback, and the Gemini annotation all render; repeat
   unscripted and assert no reference-vs-heard diff appears.
   **Never `browser_navigate` mid-session** — a reload wipes Streamlit session state silently.
3. History: paginate, filter by mode, open a legacy `drill` row and confirm it renders,
   open a row whose native audio was never bought and confirm it synthesises, delete a row and
   confirm the attempt, the audio file and the cascaded rows are gone.
4. `OFFLINE_MODE=1` end-to-end: fixture replays, no network, TTS reports disabled rather than
   faking audio.
5. Confirm a long recording (well past the old 180 s cap) completes without a duration refusal
   and without hitting the continuous backstop.

## Commit shape

One commit per step on `claude/feature-scope-architecture-87ce30`, conventional messages, no
watermarks. Step 0's tag lands first.
