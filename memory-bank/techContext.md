# Tech Context

## Architecture

The app is a local, single-script Streamlit application with two tabs, Analyze and History.
Source is a flat `src/` module set; `app.py` orchestrates UI only and delegates Azure speech
analysis, Azure TTS, audio handling, budget checks, SQLite persistence, and coaching to sibling
modules. `pytest.ini`, mypy, ruff, and scripts each configure `src/` as their one import root.

- `speech_analyzer.py` normalises Azure output. It owns shared readers such as phoneme pairs,
  flagged-word detection, delivery faults, and timings. Azure SDK values are isolated at this
  boundary; retain raw responses so later normalisers can re-derive data.
  `assessment_from_payloads` re-runs the normaliser over stored bytes — that is what makes
  re-opening a History row a re-parse rather than another call.
- `app.py` owns Streamlit calls and session state. Render helpers stay independently testable;
  modules that build data do not import Streamlit.
- `fallback_coach.py` is the only coach. It is deterministic, needs no key and no network, and
  runs on every attempt; it owns the shared report schema.
- `ai_coach.py` is the Gemini **prosody annotator** and nothing else: given the passage and the
  delivery evidence, it returns the same words marked up with stress, phrase boundaries and
  linking. It never writes coaching. Audio is never sent to Gemini.
- `db.py` is the source of truth for attempts, coaching, annotations, and recordings. Metering
  derives from completed attempt rows rather than a second state file.

### Assessment and persistence contracts

- Two modes. Scripted is **continuous recognition at any length**; unscripted transcribes first,
  then assesses against that transcript, costing two STT passes and using no completeness.
- Azure ignores `enableMiscue` in continuous mode, so **omissions and insertions always come
  from the local aligned diff**, never from Azure. Completeness is likewise always recomputed.
  That is a deliberate trade for unlimited length, made when single-shot was removed.
- `ErrorType` and prosody feedback are nested in each word's `PronunciationAssessment`.
  Delivery faults (`UnexpectedBreak`, `MissingBreak`, `Monotone`) live in `Feedback.Prosody`,
  not `ErrorType`. Azure content assessment is retired; do not re-attempt it.
- Score merges are duration-weighted. Averages of Azure composite pronunciation scores are
  approximate because Azure does not document the underlying weighting.
- A completed retry or failed paid call is metered. A user-cancelled assessment writes no
  attempt row and is not metered.
- Store raw Azure and coaching JSON, normalised values, and recording metadata. Audio files are
  local under the gitignored attempt store; never commit or upload them.
- `OFFLINE_MODE` performs no network calls, bypasses paid guards, and replays a fixture.
  TTS is disabled rather than faked. Synthetic fixtures must declare themselves.
- **There is no duration ceiling.** A recording is billed for its own seconds either way, and
  `budget.py` meters what was spent. Only a minimum survives, because Azure returns confusing
  errors on sub-second audio. Continuous timeouts are hung-callback insurance sized far past
  any real read, never a processing budget.

### Legacy rows

Rows written before 2026-08-25 carry values the code no longer produces, and History must
render them rather than raising:

- `mode = 'drill'` was scripted single-shot. Read the column through `utils.mode_of`, never
  `Mode(value)`, which raises and takes the page down. `db._mode_group` derives the History
  filter from the same function so the two cannot drift.
- `coach_source = 'gemini'` means Gemini wrote that row's coaching. Both shapes are the same
  `CoachingReport` and both still re-read; the column is what tells the page which it holds.
- Stored rows are never rewritten or migrated.

### UI and concurrency contracts

Azure assessment runs on a worker thread because a blocking SDK call prevents Streamlit from
processing Stop. The worker never calls `st.*`; the main script polls and renders its outcome.
State guards, not only disabled widgets, prevent duplicate assessment/annotation spending.

Streamlit executes BOTH tab bodies on every rerun, and `render_result` derives its widget keys
from the attempt — so two of them in one pass is a hard duplicate-key error, not a cosmetic
one. Analyze and History coordinate over `HISTORY_OPEN_KEY`: opening a stored attempt clears
`last_key`, and Analyze stands down.

`audio_input` and `file_uploader` reset through a generation key; they cannot be cleared by
writing session state. Never render alerts inside a narrow `st.columns` cell. TTS cache lookup
precedes metering and keys on the rendered payload, voice, and rate. `SpeechSynthesizer` needs
an explicit no-output audio configuration to return bytes instead of trying a default speaker.

## Learning surfaces

- **Analyze** shows every documented field Azure returned: scores and bands, aligned
  reference/heard text, colour-coded words, per-word phoneme evidence with `NBestPhonemes`
  alternates, syllable and phoneme offsets in both seconds and 100-ns ticks, prosody faults,
  and SNR. The two collapsed detail panels are what makes "everything Azure said" literally
  true of the page. Azure's 0–59/60–79/80–89/90–100 bands are presentation-only; preserve raw
  values, and name a score Azure did not return rather than omitting the row.
- A flagged word carries four things: the native rendering in citation form (plain TTS), **your
  own audio for that word** sliced by `audio_utils.slice_wav` at Azure's offsets, the
  expected → produced IPA row, and the syllable-stress line. Every reason a clip is absent is
  ordinary — no stored recording, an omitted word, a span outside the audio — so it renders
  nothing rather than an error.
- Coaching prioritises observed substitutions and delivery faults. Advice must be tied to
  evidence, never invented from a low score alone. A reported delivery fault must receive a
  drill.
- The annotation is all-or-nothing: the model may not add, drop, reorder or respell a word, and
  a returned sequence that disagrees is dropped unread. A partial repair would produce a page
  that looks annotated and is silently misaligned from the third word onwards.
- **History** paginates every attempt newest first, `offline = 1` rows included and labelled —
  a fixture replay is a real row a real click produced. Opening one renders the Analyze result
  body with the inputs hidden, from stored JSON. Deleting one is confirmed first, and removes
  the row, its cascaded rows and its recording file.

## Stack and operations

- Python 3.12 is mandatory: `pydub` depends on stdlib `audioop`, removed in Python 3.13.
- Exact pins live in `requirements.txt`; Docker is the primary run path, single-stage since the
  parselmouth compile went. The Azure native SDK needs the image's ALSA compatibility package;
  a missing library presents as an opaque import failure.
- Key dependencies: Streamlit, Azure Speech SDK, Google GenAI, pydub/ffmpeg, pydantic, cmudict,
  pytest, ruff, and mypy. Inspect the installed package before relying on an SDK surface or
  model ID.
- `make setup`, `make up`, `make down`, `make test`, `make lint`, `make typecheck`, and
  `make check` are the supported commands. Run them in the container. Tests force offline mode
  and clear credentials before dotenv can restore them.
- `.claude/launch.json` has `coach` (normal) and `coach-offline` (fixture replay, its own
  database). Verify UI work through `coach-offline`: a real assessment spends Azure quota.
- CI uses the same pinned container commands. Never install dependencies globally; a local
  Python 3.12 virtual environment is the supported host alternative.

## Durable constraints and decisions

- Exact dependency pins and one manifest prevent free-tier rebuild drift. A pin follows a
  direct import; a transitive arrival is not pinned.
- SQLite is sufficient for one local user; do not add a server database or a parallel usage
  file. SQLite WAL is unreliable for cross-process reads over the macOS bind mount, so verify
  rows through the app process rather than a second host reader.
- Secrets belong only in `.env`; no audio, credentials, or private recordings enter Git.
- Build parsers against captured payloads and verify SDK behaviour through the installed
  package. Documentation and old plans are leads, not runtime truth.
- Prefer an existing module and a narrow conditional path over new wrappers or service layers.
- Hosting is not a project goal. The app is local-first and cloud storage/sync are non-goals.

## Archived on 2026-08-25

Tag **`v0.12.0-full`** holds the code; `plans/` holds the intent. Nothing below exists in the
tree any more, and re-adding any of it needs a new plan file rather than a revert.

| Feature | Modules | Plan |
| --- | --- | --- |
| Perception trainer | `perception_trainer` | `plans/2026-08-19_perception-trainer-practice-queue.md` |
| Practice queue and targets | `practice_queue` | `plans/2026-08-19_perception-trainer-practice-queue.md` |
| Shadowing | `shadowing` | `plans/2026-08-19_shadowing-practice-flow.md` |
| Progress charts and the benchmark | `progress_view` | `plans/2026-08-19_progress-view-benchmark.md` |
| Rhythm and nPVI | `rhythm` | `plans/2026-08-19_timing-data-and-npvi.md` |
| Accent measurement engine | `vowel_measure`, `vowel_reference`, `acoustics` | `plans/2026-08-20_accent-measurement-engine.md` |
| Accent charts and resynthesis | `accent_charts`, `accent_view`, `accent_resynth` | `plans/2026-08-20_accent-visualisation-and-resynthesis.md` |
| Assessed native reference | `native_model`, `model_reference` | `plans/2026-08-20_accent-visualisation-and-resynthesis.md` |
| Gemini content scores | `content_score` | `plans/2026-08-21_unscripted-and-content-scores.md` |
| Four-rung practice ladder | `ladder`, `ladder_practice`, `ladder_reference` | `plans/2026-08-21_four-rung-practice-ladder.md` |
| Gemini-authored coaching | (rewritten `ai_coach`) | `plans/2026-08-18_coaching-layer.md` |

`praat-parselmouth`, `pandas`, `altair` and `numpy` went with them, and the Dockerfile's
parselmouth builder stage with those.

**Their database tables stay.** `practice_targets`, `perception_trials`, `attempt_tags`,
`attempt_content_scores`, `speaker_baseline`, `vowel_measurements` and `native_renderings` keep
their DDL and their rows: they hold real recorded evidence — calibration reads, answered
perception trials — and dropping them is unrecoverable in a way that deleting code is not.
`user_version` did not move.

For chronology, experimental results, and detailed rejected alternatives, use the linked plan
in `history.md`; do not copy them here.
