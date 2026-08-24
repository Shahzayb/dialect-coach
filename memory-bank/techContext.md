# Tech Context

## Architecture

The app is a local, single-page Streamlit application. Source is a flat `src/` module set;
`app.py` orchestrates UI only and delegates Azure speech analysis, Azure TTS, audio handling,
budget checks, SQLite persistence, coaching, content scoring, progress charts, practice, and
accent measurement to sibling modules. `pytest.ini`, mypy, ruff, and scripts each configure
`src/` as their one import root.

- `speech_analyzer.py` normalises Azure output. It owns shared readers such as phoneme pairs,
  flagged-word detection, delivery faults, timings, and Mode A/B/C behaviour. Azure SDK values
  are isolated at this boundary; retain raw responses so later normalisers can re-derive data.
- `app.py` owns Streamlit calls and session state. Render helpers stay independently testable;
  modules that build data or charts do not import Streamlit.
- `progress_view.py` produces pandas data and Altair specs only. Benchmark lines must never
  connect Mode A and Mode B; arbitrary free practice is not a comparable trajectory.
- `fallback_coach.py` is the deterministic primary coach and owns the shared report schema.
  `ai_coach.py` may improve the report but must fall back cleanly. Audio is never sent to
  Gemini; only compacted evidence and reference text are eligible.
- `content_score.py` is separate from coaching: it evaluates a Mode C transcript, not speech
  acoustics, and must be labelled as Gemini-derived.
- `db.py` is the source of truth for attempts, coaching, practice, tags, recordings, and
  baselines. Metering derives from completed attempt rows rather than a second state file.

### Assessment and persistence contracts

- Mode A is scripted single-shot assessment. Mode B is scripted continuous assessment.
  Azure ignores `enableMiscue` in continuous mode, so Mode B omissions/insertions come from a
  local aligned diff. Mode C first transcribes unscripted speech, then assesses against that
  transcript; it costs two STT passes and does not use completeness.
- `ErrorType` and prosody feedback are nested in each word's `PronunciationAssessment`.
  Delivery faults (`UnexpectedBreak`, `MissingBreak`, `Monotone`) live in `Feedback.Prosody`,
  not `ErrorType`. Azure content assessment is retired; do not re-attempt it.
- Mode B score merges are duration-weighted; completeness is recomputed globally. Averages of
  Azure composite pronunciation scores are approximate because Azure does not document the
  underlying weighting.
- A completed retry or failed paid call is metered. A user-cancelled assessment writes no
  attempt row and is not metered. Continuous recognition can stop; a single-shot result is
  discarded when it arrives after cancellation.
- Store raw Azure and coaching JSON, normalised values, tags, and recording metadata. Audio
  files are local under the gitignored attempt store; never commit or upload them.
- `OFFLINE_MODE` performs no network calls, bypasses paid guards, and replays a fixture.
  TTS is disabled rather than faked. Synthetic fixtures must declare themselves.

### UI and concurrency contracts

Azure assessment runs on a worker thread because a blocking SDK call prevents Streamlit from
processing Stop. The worker never calls `st.*`; the main script polls and renders its outcome.
State guards, not only disabled widgets, prevent duplicate assessment/coaching spending.

`audio_input` and `file_uploader` reset through a generation key; they cannot be cleared by
writing session state. Never render alerts inside a narrow `st.columns` cell. TTS cache lookup
precedes metering and keys on the rendered payload, voice, and rate. `SpeechSynthesizer` needs
an explicit no-output audio configuration to return bytes instead of trying a default speaker.

## Learning surfaces

- The diagnosis shows Azure scores, aligned reference/heard text, per-word phoneme evidence,
  prosody faults, and normal/slow native playback. Azure's 0–59/60–79/80–89/90–100 bands are
  presentation-only; preserve raw values.
- Coaching prioritises observed substitutions and delivery faults. Advice must be tied to
  evidence, never invented from a low score alone. A reported delivery fault must receive a
  fallback drill even if Gemini omits it.
- The benchmark passage is the sole comparable read-speech series and the calibration passage.
  Its coverage claim lives as tested data in `progress_view.py`; identification is by
  normalised reference text, not a schema migration.
- The perception trainer is forced-choice high-variability training. It needs its configured
  voice variety; do not silently degrade to fewer voices. Practice targets are promoted only
  from observed evidence, rotate by `last_seen`, and record trial answers immediately.
- Shadowing is tagged separately. Cold and shadowed reads may be compared only by the declared
  metrics and require enough pairs before the view calls a pattern. Simultaneous shadowing is
  presently unsuitable for this speaker; echo is usable but not an assessed capture.
- The practice ladder is sound → word → sentence → paragraph. Resolution is measured, not
  manually declared; corrections alter one acoustic dimension at a time. Banked reps are
  tagged attempts and excluded from the ordinary progress cloud.

## Accent measurement

Accent measurement is a guarded diagnostic, not a truth machine. It uses a room check, two
separated calibration reads, token-quality gates, per-style baselines, and a noise floor.
Read and spontaneous speech are separate populations and never share a baseline or chart.
The four instruments are vowel position, diphthong trajectory, rhoticity (F3−F2), and
duration/reduction; rhythm and pitch have their own whole-reading measures.

- Azure offsets/durations are 100-ns ticks from the audio stream and lie on a 10-ms grid.
  Preserve them, but validate phoneme alignment before treating a token as acoustic evidence.
- nPVI is passage-scale. Compare the benchmark against the same passage rendered through Azure
  TTS and the same pipeline, not against published hand-segmented bands.
- Lobanov normalisation needs adequate vowel-category coverage; a z-score is relative to the
  inventory used to create it. Do not compare published normalised coordinates with a generated
  reference in hertz as if they were interchangeable.
- Published Hillenbrand data and the project-measured multi-voice reference complement one
  another and must never be averaged. The measured reference supplies connected-speech timing
  and broader rhotic coverage; it excludes known child voices manually because the SDK lacks
  age metadata.
- Diphthong advice requires a sufficiently long, voiced, uncontaminated token and a reference
  with an agreeing travel direction. A missing travel is unknown, never zero.
- Praat has no typed `Manipulation` class in its Python binding. Use supported `praat.call`
  surfaces and test clip boundaries: an empty `Extract part` range returns the whole sound.

## Stack and operations

- Python 3.12 is mandatory: `pydub` depends on stdlib `audioop`, removed in Python 3.13.
- Exact pins live in `requirements.txt`; Docker is the primary run path. The Azure native SDK
  needs the image's ALSA compatibility package; a missing library presents as an opaque import
  failure.
- Key dependencies: Streamlit, Azure Speech SDK, Google GenAI, pydub/ffmpeg, pydantic,
  pandas/Altair, praat-parselmouth, pytest, ruff, and mypy. Inspect the installed package
  before relying on an SDK surface or model ID.
- `make setup`, `make up`, `make down`, `make test`, `make lint`, `make typecheck`, and
  `make check` are the supported commands. Run them in the container. Tests force offline mode
  and clear credentials before dotenv can restore them.
- CI uses the same pinned container commands. Never install dependencies globally; a local
  Python 3.12 virtual environment is the supported host alternative.

## Durable constraints and decisions

- Exact dependency pins and one manifest prevent free-tier rebuild drift.
- SQLite is sufficient for one local user; do not add a server database or a parallel usage
  file. SQLite WAL is unreliable for cross-process reads over the macOS bind mount, so verify
  rows through the app process rather than a second host reader.
- Secrets belong only in `.env`; no audio, credentials, or private recordings enter Git.
- Build parsers against captured payloads and verify SDK behaviour through the installed
  package. Documentation and old plans are leads, not runtime truth.
- Prefer an existing module and a narrow conditional path over new wrappers or service layers.
- Hosting is not a project goal. The app is local-first and cloud storage/sync are non-goals.

For chronology, experimental results, and detailed rejected alternatives, use the linked plan
in `history.md`; do not copy them here.
