# Tech Context

## Architecture

A single-page Streamlit app. `app.py` holds UI only and makes no API calls; it orchestrates
`speech_analyzer` (Azure STT), `tts` (Azure neural TTS), `audio_utils`, `budget`, `db` and
`utils`. Still uncreated: `ai_coach.py`, `fallback_coach.py`, `phoneme_reference.py`.

`app.py` also holds the pure rendering helpers — `colour_coded_html`, `reference_vs_heard`,
`phoneme_pairs`, `delivery_summary`, `severity_key`, `is_flagged` — kept free of Streamlit
so what the user sees is testable directly rather than only through a headless app run.

`db.py` and `budget.py` are additions to the master plan's §8 file list — it predates both
the SQLite decision and the decision to derive the meter from it.

Local SQLite history, no accounts, no persistent audio storage. One row per attempt holding
**both raw API responses verbatim** (`azure_raw_json`, and `gemini_raw_json` created NULL
for the coaching chunk), the normalised scores, and a SHA-256 of the audio — never the
audio itself. Storing responses whole means changing what the UI shows is a re-parse, not a
re-recording that spends quota.

## Technologies

- **Python 3.12**, pinned in `.python-version` and in the Docker base image.
- **Streamlit 1.61.1** — UI. `st.audio_input` (needs ≥ 1.41) is the intended capture widget;
  `streamlit-audiorecorder` is deliberately not used.
- **azure-cognitiveservices-speech 1.51.1** — pronunciation assessment and neural TTS.
  Native library.
- **google-genai 2.18.1** — current Google GenAI SDK (`from google import genai`). The old
  `google-generativeai` package is end-of-life and must not be used.
- **Gemini model ID: `gemini-3.6-flash`**, verified live 2026-08-17 via `scripts/smoke_test.py`.
  `gemini-2.5-flash` (used in the original design) now 404s — the API's own error says it's
  "no longer available to new users" and points at `gemini-3.6-flash`. Re-verify against a
  live call rather than recalling a model ID; these retire without much notice.
- **pydub 0.25.1** + system `ffmpeg` — audio conversion.
- **python-dotenv 1.2.3**, **pydantic 2.13.4**.
- **SQLite** via the stdlib `sqlite3` — built 2026-08-18. No dependency to pin and no second
  service. It persists for free under the existing bind mount, since the project directory
  is the host's; a database file under it survives `docker compose down`. Schema version is
  tracked with `PRAGMA user_version`; `DB_PATH` defaults to `./data/coach.db`.
- **pytest 9.1.1** — verified against PyPI 2026-08-18. In `requirements.txt` rather than a
  separate dev manifest, since the image is a local dev container and one manifest cannot
  drift. Collection is scoped to `tests/` in `pytest.ini`, because `scripts/smoke_test.py`
  and `scripts/pronunciation_test.py` have `test_`-prefixed functions that make real,
  billable API calls and pytest would otherwise collect them.

All pins in `requirements.txt` are exact `==`, verified against PyPI on 2026-08-17. Ranges
are rejected: an unattended free-tier rebuild must produce the same image next month.

## Running and testing

`make setup` (creates `.env` from `.env.example` if missing, checks `docker compose` is on
PATH — logic lives in `scripts/setup.py` since it's conditional, not a one-liner), then
`make up` (`docker compose up --build`, serving http://localhost:8501) and `make down`.
The source directory is bind-mounted, so edits reload without a rebuild; rebuild only when
`requirements.txt` changes. The image sets `STREAMLIT_SERVER_RUN_ON_SAVE=true`, because
Streamlit's default only shows a "Rerun" prompt on a file change instead of applying it.

A project-local `.venv` built from Python 3.12 is supported as an optional alternative
(`python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt`). Nothing is ever
installed globally.

Verified in the container: Python 3.12.14, and `streamlit`, `pydub`, `pydantic`, `dotenv`,
`audioop`, `azure.cognitiveservices.speech`, `google.genai` all import.

`make test` runs the suite in the container. It runs fully offline: `tests/conftest.py`
forces `OFFLINE_MODE=true`, clears the API keys, and marks dotenv as already-loaded — the
last one matters because `.env` is bind-mounted, so without it a real `.env` would silently
re-supply the keys the fixture just cleared and the suite would behave differently on a
machine with credentials. Rebuild (`docker compose build`) after a `requirements.txt`
change or the image has no pytest.

`tests/fixtures/` holds two verbatim Azure responses captured once from a real recording,
which is what lets parsing, colouring, and coaching be developed without spending quota.
No lint or type-check setup yet.

## Technical constraints

- **Python must stay at 3.12.** `pydub` depends on the stdlib `audioop` module, removed in
  3.13. Moving to 3.13+ requires adding `audioop-lts`.
- **Azure Speech SDK native prerequisites.** A missing `libasound2` shows up as an opaque
  `ImportError` on `import azure.cognitiveservices.speech`, not as an install failure. The
  `python:3.12-slim` base is Debian trixie, which renamed the packages to `libasound2t64`
  and `libssl3t64`, so the Dockerfile tries both names.
- Free tiers only: Azure Speech **F0** (5 audio hours/month) and the Gemini free tier.
  Running locally does not change this — the APIs are remote either way. Creating an Azure
  **S0** resource by mistake is the only way this project costs money.
- Target locale is **en-US** — prosody assessment supports nothing else.
- **`enable_content_assessment_with_topic` does not exist in SDK 1.51.1.** The master plan
  says it arrived in 1.33; it is on neither `PronunciationAssessmentConfig` nor the module.
  Mode C's content scoring has to find another route — verify before planning that chunk.
- **The SDK's JSON nests `ErrorType` and `Feedback` inside each word's
  `PronunciationAssessment`**, not at the word's top level as the docs' flat REST example
  shows. Reading `word["ErrorType"]` returns nothing and every word parses as clean — a
  silent failure that looks like a perfect score. The parser reads both shapes.
- **Delivery problems are not `ErrorType` values.** `UnexpectedBreak` / `MissingBreak` /
  `Monotone` live under `Feedback.Prosody` in `Break.ErrorTypes` and
  `Intonation.ErrorTypes`; `ErrorType` carries only the miscue kinds. Master plan §5 is
  wrong on this.
- **`enableMiscue` is ignored in continuous mode**, so Omission/Insertion for paragraphs are
  diffed locally and marked `error_source: "local_diff"` rather than passed off as Azure's.
- **`SpeechSynthesizer`'s `audio_config` does not default to `None`.** It defaults to an
  `AudioOutputConfig` bound to the default speaker, so omitting it makes the container
  synthesise to a sound device it does not have: no exception, no `audio_data`, and a call
  that consumed allowance for nothing. `audio_config=None` is what asks for the bytes back.
  This is the TTS twin of the `apply_to` trap and is just as easy to leave out. Confirmed
  against live Azure on 2026-08-18: with it, `Riff24Khz16BitMonoPcm` bytes come back and
  play directly in `st.audio`.
- **TTS character billing is an estimate that deliberately rounds up.** `tts.payload_for`
  returns exactly what is sent, and the meter is charged for all of it, SSML markup
  included. Whether Azure excludes markup is not confirmed; over-counting is the correct
  direction for a spend guard.
- Secrets come from environment variables only; never hardcoded, logged, or surfaced in a
  UI error or traceback. `.env` is gitignored and never copied into the image.
- Required: `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION`, `GEMINI_API_KEY`. The full annotated
  set, including duration guards and the budget guard, lives in `.env.example`.
  `GEMINI_API_KEY` and `GEMINI_MODEL` are still unread; `AZURE_TTS_VOICE` is now live.

## Hosting

**This runs locally. Deployment is not a goal** — it is left possible for anyone who forks
the project, and nothing in the app may assume a host. Practical consequences:

- The usage counter can just be a local file. No Hugging Face Dataset persistence, and no
  design work around a Space's ephemeral filesystem.
- No cold-start budget to design against, so no wake-time requirement.
- `packages.txt` and the README's optional frontmatter block are kept for a would-be
  deployer and are **unverified** — package names against the Space's Ubuntu release, and
  `sdk_version` against Hugging Face's supported list. Neither blocks local work.

## Decisions

- **Exact pins over ranges**, for the rebuild reason above.
- **Docker as the primary run path**, on request. It installs nothing on the host and fixes
  both the Python version and the Azure SDK's native libraries — the two things most likely
  to break setup on another machine. The local `.venv` path is kept but secondary.
- **`requirements.txt` stays the single dependency manifest**, not a lockfile or
  `pyproject.toml`. One manifest that both the Dockerfile and the optional local `.venv`
  read cannot drift; it also happens to be what a Space would read.
- **No module stubs.** The other modules the design calls for were left uncreated rather
  than committed empty — dead files that later work would have to clean up.
- **SQLite over Postgres or DuckDB.** Single user, single machine: Postgres would mean a
  second container and a named volume for nothing, and DuckDB's analytical edge does not
  show up at a few hundred rows. Revisit only if the data ever needs to be reachable from
  outside the container.
- Both Streamlit persistence traps are handled: the connection lives behind
  `@st.cache_resource` in `app.py` (the script re-runs on every widget interaction) and
  `db.connect` sets `check_same_thread=False`. `st.connection("sql")` was rejected — it
  would add SQLAlchemy, a dependency SQLite does not otherwise need. `db.py` never imports
  Streamlit, so tests and scripts can use it.
- **The usage meter is derived from the attempts table**, not a `.usage.json` file;
  `BUDGET_STATE_PATH` was dropped. One store cannot disagree with itself.
- **Mode B scores are duration-weighted, never naively averaged**, and completeness is
  recomputed globally from the local omission diff — Azure scores each utterance against
  the *whole* reference, so averaging would report a five-utterance paragraph as ~20%
  complete. `pron_score` as a weighted average is an approximation: Azure's composite
  weighting is not published.
- **`OFFLINE_MODE` bypasses the budget guard and the F0 acknowledgement.** Gating the
  zero-cost path behind a tier confirmation would make it harder to use than the paid one.
- **Retries count against the meter.** A retry re-uploads the audio and can consume
  allowance even when it fails, so recorded seconds are multiplied by attempts made. The
  same rule applies to TTS characters.
- **The TTS cache is checked before the meter is touched, never after.** Streamlit re-runs
  the whole script on every widget interaction, so pricing a call ahead of the cache lookup
  would charge again on each unrelated click and the meter would climb while nothing was
  synthesised. Both session caches share one LRU (`lru_get` / `lru_put`) for the same
  reason the assessment cache is LRU: the drill loop re-uses one entry over and over.
- **"Hear it" is disabled under `OFFLINE_MODE`, not faked.** Synthesis is a live call by
  definition and there is no audio fixture to replay the way there is for an assessment;
  a silent placeholder would be a confusing thing to build in. `OFFLINE_MODE` keeps its
  absolute meaning — no network call, ever — and `tts.synthesise` refuses independently of
  the UI so no code path can slip past it.
- **The voice comes from `AZURE_TTS_VOICE` only, with no UI picker.** The cache key is
  already `(voice, text, slow)`, so adding a picker later changes no stored shape.
- **Nothing renders an alert from inside an `st.columns` entry.** A function called within
  `with column:` appends its output to that column, so an `st.error` emitted there is laid
  out at the button's width — measured live at 124 px inside a 672 px row, a couple of
  hundred characters of message at one word per line. `play()` therefore *returns* an
  (icon, message) pair and the caller renders it after the columns close. Any future
  helper called from inside a column needs the same treatment.
- **A paid call that fails after retries is still metered.** `tts.synthesise` takes an
  `on_attempt` hook precisely because the exception carries no attempt count: when every
  attempt fails there is no `Synthesis` to read `attempts` from, but the text reached Azure
  each time and may have been charged. The pre-flight prices
  `payload × MAX_SYNTHESIS_ATTEMPTS` for the same reason — pricing one attempt would let
  the guard approve a call whose real charge lands past the budget.
- **Colour-coded text is HTML, not Streamlit's `:red[…]` markdown.** Only an attribute can
  carry the score on hover, which §11 asks for. Both the word and the title are escaped —
  they come from the reference textarea. Colours are set as text and border colours, never
  as a background, so they survive both Streamlit themes.
- **The colour-coded block is built from the aligned word list, not the reference string**,
  because that is what carries the scores — so punctuation and capitalisation are not
  reproduced there. The verbatim text stays visible in the diff panel above it.
