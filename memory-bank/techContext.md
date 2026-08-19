# Tech Context

## Architecture

**Every source module lives in `src/`, as flat modules — not a package.** The imports are
plain (`import utils`), and each runner is told where `src/` is by exactly one mechanism:
Streamlit takes the entry script's own directory (`streamlit run src/app.py`), pytest takes
`pythonpath = src` in `pytest.ini`, `scripts/` keep a two-line `sys.path` shim, mypy takes
`mypy_path`, and ruff takes `src = ["src"]` so isort classifies them as first-party. The
repo root is deliberately no longer a source root; `tests/` and `scripts/` sit beside `src/`,
not under it.

A single-page Streamlit app. `app.py` holds UI only and makes no API calls; it orchestrates
`speech_analyzer` (Azure STT), `tts` (Azure neural TTS), `audio_utils`, `budget`, `db`,
`utils`, and the coaching layer — `phoneme_reference`, `fallback_coach`, `ai_coach`.

`app.py` holds the rendering helpers that are specific to the UI — `colour_coded_html`,
`reference_vs_heard`, `severity_key` — kept free of Streamlit so what the user sees is
testable directly rather than only through a headless app run. The readers that answer
"what did you actually produce" — `phoneme_pairs`, `is_flagged`, `delivery_summary` — moved
into `speech_analyzer` when the coaching layer landed, because that layer needs them too and
cannot import a module that pulls in Streamlit; one definition means the word card and the
coaching report can never disagree about a substitution.

`progress_view.py` follows the same rule as those helpers and is the strictest case of it:
it builds pandas frames and altair chart specs and **never imports Streamlit**, so `app.py`
owns every `st.altair_chart` call and the caching around them. That is what makes the chart
spec assertable in a test — including the rule that Mode A and Mode B never share a line.

`db.py` and `budget.py` are additions to the master plan's §8 file list — it predates both
the SQLite decision and the decision to derive the meter from it.

Local SQLite history, no accounts, no persistent audio storage. One row per attempt holding
**both raw API responses verbatim** (`azure_raw_json`, and `gemini_raw_json` — the whole
Gemini response on the model path, or the report itself on the offline path, told apart by
`coach_source`), the normalised scores, and a SHA-256 of the audio — never the audio itself.
Storing responses whole means changing what the UI shows is a re-parse, not a re-recording
or a re-spend of quota.

### The coaching layer

Three modules, landed 2026-08-18, built in this order deliberately:

- **`phoneme_reference.py`** — static en-US IPA data keyed **by phoneme**, never by first
  language: the lookup is expected → produced, and the produced side comes from Azure's
  `NBestPhonemes`, from what the speaker actually did. 46 entries (28 consonants +
  monophthongs, 5 diphthongs, 5 r-coloured vowels), each with a concrete articulation note
  and, per observed substitution, minimal word pairs. Keyed on **Azure's own symbols** —
  rhotic, no length marks (`ɝ ɚ ɹ ɔɹ ɪɹ oʊ eɪ`, never `iː ɑː ɜː`) — verified against both
  fixtures rather than assumed; `normalise()` maps textbook/keyboard spellings (`ɡ`, `r`,
  `ʧ`, `ɜː`, …) onto them. A pair with no entry degrades to `NO_NOTE` and an empty pair
  list, never to invented advice. Written for three consumers so it is written once: both
  coaches read `articulation`/`minimal_pairs`; a later perception trainer can read
  `Phoneme.contrasts` directly; a later accent feature adds fields to the same dataclasses.
- **`fallback_coach.py`** — **the primary path, not the degraded one**: deterministic,
  no key, no network, no clock, same bytes out for the same bytes in. Holds the shared
  report schema too (`CoachingReport` and friends, pydantic, no `Optional`/defaults, so the
  GenAI SDK's schema conversion stays clean) — kept on this side rather than in `ai_coach`
  so the free path never imports the Google SDK. `compact()` reduces a fixture-sized Azure
  response (~39 kB) to ~2 kB of evidence — only flagged words, their substitutions, weak
  syllables, the delivery faults with their measurements as their own section, and the
  distinct `observed_pairs` — and is shared with
  `ai_coach`, so both coaches read exactly the same facts. Ranking (`_groups`) is worth
  remembering: adjacent phonemes claiming the *same* produced sound are collapsed to the
  worse of the run (Azure's aligner smears one produced sound across two targets — the
  fixture's "thursday" reports /tʃ/ at 100 for both its /z/ and its /d/), and a pair the
  reference table has written up outranks an unwritten one at the same word-spread, because
  an unwritten pair can only be named while a written one can be practised — Azure's
  alternates for a mangled word include alignment noise, and that is exactly what has no
  entry. Without both rules the flagship `/θ/ → /s/` on "thursday" ranked behind two
  artefacts.
- **`ai_coach.py`** — the model path, falling through to `fallback_coach` on any failure.
  `response_mime_type="application/json"` **and** `response_schema=CoachingReport` are both
  set; the mime type alone does not guarantee shape. `max_output_tokens` is deliberately
  left unset — capping it on a thinking model truncates the JSON mid-object, which shows up
  as a parse failure and a silent fall-through rather than a readable error.
  `automatic_function_calling` is explicitly disabled (no tools are declared, so it had
  nothing to do but warn on every call). The model is not trusted about phonemes:
  `validated()` drops any fix whose `(expected, produced)` pair is absent from
  `observed_pairs`, after the prompt has already told it not to invent one — the prompt
  constraint is a request, the validation is what makes it true. `reference_text` and
  `recognised_text` are wrapped in `<reference_text>`/`<recognised_text>` delimiters with
  the delimiter tokens stripped from the text first (both are free-form user input) and an
  explicit system-instruction line to analyse their contents and never obey them. A 429 is
  terminal without a retry (a free-tier 429 is the day's or month's allowance, not
  congestion); 5xx and transport failures get one call plus two retries
  (`MAX_COACH_ATTEMPTS = 3`). `coach()` always returns a `CoachingResult`, whatever the
  network did — `sdk_http_response` (raw transport headers) is excluded from what gets
  stored. `report_from_raw()` re-reads a stored payload back into a `CoachingReport` for
  either source, so a change to what the UI shows is a re-parse of a stored row, not a
  fresh call. `OFFLINE_MODE` is refused inside `coach()` itself, before any client is
  built — even an injected one — the same absolute contract `tts.synthesise` enforces on
  its own rather than trusting the caller.

### Delivery coaching — making the prosody score actionable

Landed 2026-08-19 (#9, milestone v0.4.0). Prosody was scored and displayed but could not
be practised; this turns it into a span plus something to perform, and it is a **section of
the existing coaching payload**, not a second model call.

- **Where the data is.** Delivery faults are not `ErrorType` values — that field carries
  only None/Mispronunciation/Omission/Insertion. They live in each word's
  `PronunciationAssessment.Feedback.Prosody`, under `Break.ErrorTypes` and
  `Intonation.ErrorTypes`. Beside them sit the measurements
  `Break.BreakLength` and `Intonation.Monotone.SyllablePitchDeltaConfidence`, which
  `speech_analyzer._prosody_detail` now reads into every normalised word.
- **`BreakLength` is in 100-ns ticks, and that was derived, not looked up.** SDK 1.51.1
  never mentions the field — not in its Python layer, not in the strings of its native
  libraries. The committed capture holds 0, 200000 and 2000000 in a 9.79-second utterance,
  so milliseconds would make the largest a 2000-second pause; the tick divisor gives 200 ms
  and also gives the word `Duration`s in the same payload their sane 0.27–0.41 s. The
  parser exposes `break_length_ms`, so the coach can say "about 420 ms" rather than quoting
  a raw field. **An earlier reading of the fixture that said every `BreakLength` is 0 was
  wrong** and is contradicted by the file; two commit messages on the branch still repeat
  it.
- **Azure reports the measurements whether or not it flagged anything.** "thursday" in the
  capture carries a 200 ms break with `Break.ErrorTypes: ["None"]` — an ordinary pause at a
  sentence boundary — and the pitch-delta confidence is the same 0.17783079 on every word
  of the recording. So `speech_analyzer.delivery_faults` averages **only over the words
  carrying that fault**; averaging across the attempt would give every clean reading a
  monotone number.
- **The measurements are deliberately not a sort key.** Faults are ordered by span size and
  then by `FAULT_PRECEDENCE` (`UnexpectedBreak`, `MissingBreak`, `Monotone`). A longer pause
  is not automatically the worse fault, and the pitch confidence is constant across the
  capture, so it would order nothing.
- **The report shape.** `CoachingReport` gains `delivery_drills: list[DeliveryDrill]`
  (fault, span, `what_happened`, `drill`), and the delivery sentences moved *out* of
  `stress_and_rhythm`, which keeps misplaced syllable stress and the overall score. The two
  render inches apart, so saying it in both read as padding.
- **The templates live in `fallback_coach`, not the prompt** (`_DELIVERY_DRILLS`,
  `measurement_note`). That is what makes the feature work with no key at all — the
  complaint in #9 was a score with nothing to do about it, and a prompt-only answer would
  have left the keyless path exactly there. `measurement_note` is also what `app.render_delivery`
  uses, so the coaching section and the evidence panel cannot quote different numbers.
- **The model is checked differently here than on the fixes.** `ai_coach._checked_drills`
  drops a drill for a fault Azure never reported, rewrites the span from the payload, and
  **backfills from the templates** for any reported fault the model skipped or left blank —
  rather than rejecting the whole report the way an invented *fix* does. An invented fix
  means the answer is about the wrong recording; a missing drill costs nothing to replace.
  The result is that "a fault in the data always produces advice" holds on both paths.
- **`report_from_raw` fills the new section in** when re-reading rows written before it
  existed (v0.1.0–v0.3.0). Absent means the coach of the day had no delivery section, not
  that the row is corrupt.
- **Spans are cut into contiguous `runs`, and the coaching quotes the longest.** This is
  the one thing the synthetic payload could not have taught: its spans were three words
  long, where a real Monotone is an unbroken passage. Since a span is in reading order, its
  head is whichever function words happened to start it — the captured bad reading turned
  into "Say i, i, need, once, i, get three times" before this existed. Quoting is capped at
  `MAX_QUOTED_WORDS`, and a run stops at a gap so a quote never joins words the speaker did
  not say next to each other.
- **`OFFLINE_FIXTURE`** names which file in `tests/fixtures/` `OFFLINE_MODE` replays,
  resolved inside that directory and refused if it escapes. It exists for one narrow
  reason: both captures are clean on Break and Intonation, so without it the delivery
  coaching could be seen in the test suite and nowhere else.
  `tests/fixtures/synthetic_delivery_faults.json` is the hand-built payload it selects, and
  it says so in a `_synthetic` key inside the file — everything else in that directory is
  verbatim Azure and must stay distinguishable from it.
  `tests/fixtures/bad_delivery_capture.json` is verbatim: a 38.5 s reading done badly on
  purpose, which Azure flagged `Monotone` on and nothing else. **No capture has ever
  produced `UnexpectedBreak` or `MissingBreak`**, so those two remain synthetic-only.

**Deliberately no Gemini budget guard.** `budget.py` is shaped around Azure's paid tiers; a
free-tier Gemini key returns 429 rather than billing, which `ai_coach` already treats as
terminal. The usage metadata (`prompt_token_count`, etc.) is still stored verbatim in
`gemini_raw_json`, so a token meter is a later re-parse if the free tier ever needs one.

### Running an assessment without freezing the page

The assessment runs on a background `threading.Thread`, not inline, and the script polls it
(`time.sleep(JOB_POLL_SECONDS)` then `st.rerun()`) until it finishes. This is not
over-engineering — it is forced by three Streamlit facts, all verified rather than assumed:

- **A widget interaction cannot interrupt a blocking call already in progress.** Streamlit's
  rerun-kills-the-current-run behaviour only fires when the script calls back into
  Streamlit; a blocking SDK call never yields to it. An inline Azure call therefore leaves
  the page frozen with no way to draw a Stop button, let alone act on one.
- **Streamlit does not support calling its API from a custom thread.** The documented
  pattern is a thread that touches no `st.*` at all, with the main script collecting the
  result. That is why the worker returns an `AssessOutcome` carrying an (icon, message) pair
  instead of rendering its own error — the same reason `play()` returns rather than renders.
- **A widget's `disabled=` reflects the *previous* completed run.** A click is handled in
  the pass that drew the button, so the button on screen stays enabled until the next rerun.
  Every guard here is therefore state-based and checked before acting (`if clicked and not
  running`), never left to the flag — the same lesson the Gemini button already taught.

`AssessJob.outcome` is written once by the worker just before it returns and read only after
`thread.is_alive()` is False, so the two threads never race over it. `_DB_LOCK` guards the
worker's `record_attempt` against the meter reads that run at the foot of every rerun.

**Cancelling: a stopped run is never recorded and never metered**, whether or not it reached
Azure — the cancel check sits before `db.record_attempt`, and the STT meter is derived from
the attempts table, so writing no row *is* charging nothing. This is a different question
from the standing rule that a *completed* run counts every attempt it made (retries and
failures included): those re-uploaded the audio for a result the user kept. Only continuous
recognition can actually be stopped mid-call, via `stop_continuous_recognition_async`;
`recognize_once_async` is one blocking round trip with no SDK-exposed abort, so a stop
during a drill discards the result when it arrives instead. The UI says which of the two
happened rather than implying every Stop is instant.

Neither `st.audio_input` nor `st.file_uploader` can be cleared by writing to
`st.session_state` (an open upstream request), so both are keyed on a generation counter;
bumping it hands Streamlit a key it has never seen, which builds a fresh empty widget. The
textarea and preset select are cleared the documented way instead — explicit `key=` plus an
`on_click`/`on_change` callback, which runs before the next render rather than fighting it.

**UI contract**: the offline report renders on every assessment, for free, directly under
the scores — what to do about them before the evidence for them. "Improve this with
Gemini" is a button, not a side effect of assessing, and its caption states what a click
sends (the compacted analysis and the reference text, never the audio) before it is
clicked. The session `coaching` cache is checked before anything is produced, and
`coaching_for`'s `already_asked` guard exists because a button click is handled in the same
Streamlit rerun that renders the button — the on-screen button still shows enabled until
the *next* rerun, so the spend guard has to live where the spend happens, not on the
widget's `disabled` flag. `db.attach_coaching` fires once per (attempt, source), keyed off
`CachedAttempt.attempt_id`.

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

### Formatting, linting and types

`make lint` (ruff format --check + ruff check), `make typecheck` (mypy), `make check` (both
plus `make test`). All three run in the container against the pinned binaries in
`requirements.txt`, which is the same thing CI runs — a green `make check` is a green CI run.

Both tools are configured in `pyproject.toml`, and **every rule choice there carries its
reason in a comment beside it**. The ones worth knowing without opening the file:

- **`line-length = 100`, not ruff's default 88.** The prose comments here are hand-wrapped
  near 96: 2,035 lines exceed 88 and only 23 exceed 100.
- **`include = ["*.py", "*.pyi"]`.** Ruff also formats Python fenced inside Markdown, which
  rewrote the code blocks in five `plans/` files on the first run. Those are records of what
  was intended at the time, not source, and reformatting them falsifies them.
- **`BLE` is selected because the codebase already wrote for it.** Twelve `except Exception`
  handlers carried a `# noqa: BLE001` and a sentence saying why that one is deliberate. With
  BLE unselected, `RUF100` deletes all twelve explanations as dead noqa. Ruff flags only four
  of the twelve sites, so five keep the noqa and seven keep the sentence as a plain comment.
- **`ANN` is not selected** — annotations are mypy's job, and mypy says something truer about
  them. Nor are `T20` (scripts print legitimately), `ARG` (Streamlit callbacks), `TRY`, `N`
  (`N818` would rename `TierNotAcknowledged`, a public API change), `PTH`, or `D`.
- **mypy is strict on every module under `src/`, `app.py` included.** The plan budgeted for
  leaving the UI layer loose; with the dependencies installed it cost ten signature
  annotations. `tests/` and `scripts/` are checked but not strict — at global strictness they
  raise 284 findings, almost all of them annotating a test that takes a pytest fixture.
- **`utils.RowLike`** is a Protocol saying what a row reader actually needs: subscript by
  column name. `sqlite3.Row` is not a `Mapping` — no `.get`, and it raises `IndexError` where
  a dict raises `KeyError`, which is why `progress_view.is_shadowed` already caught both.
  Readers that were annotated `Mapping[str, Any]` were being handed a `Row` anyway.
- **The Azure and Google SDK boundaries are annotated `Any` explicitly.** Neither ships type
  information, so `Any` is the whole truth there, and saying it is what lets everything
  around them be strict.
- **`warn_return_any` is off for `progress_view` alone.** Altair's builders compose into a
  `LayerChart | FacetChart` union whose chained methods mypy widens to `Any`; it fires on the
  six chart builders and nothing else. Altair itself is *not* in the missing-stubs ignore
  list — it ships `py.typed`. `azure.*`, `pydub.*` and `pandas.*` are.

### Continuous integration

`.github/workflows/ci.yml` on every push and pull request: build the image, then
`ruff format --check`, `ruff check`, `mypy`, `pytest`. It runs inside the project's own image
rather than on the runner, so CI and `make check` are the same commands against the same
pinned dependencies, and the apt list the Azure SDK's native libraries need stays in the
Dockerfile in one place instead of being duplicated in YAML.

**A CI run is structurally unable to spend money — four independent layers, any one of which
would be enough on its own:**

1. `ci.yml` names no repository secret anywhere, deliberately not even in a comment, so that
   `grep -n 'secrets\.' .github/workflows/ci.yml` returning nothing is a real check rather
   than one the file defeats by describing itself. The trigger is `pull_request`, never
   `pull_request_target`, so a fork's PR could not reach repository secrets even if one were
   added later.
2. A CI checkout has no `.env` — it is gitignored — and `compose.yaml` declares it
   `required: false`, so the container starts with none of the three credentials.
3. `tests/conftest.py` deletes those three and forces `OFFLINE_MODE` for every test.
4. `tests/conftest.py`'s `no_network` fixture patches `socket.socket.connect` to raise on any
   non-loopback address. This is the layer below `OFFLINE_MODE`: the flag is a decision some
   future path could forget to make, and a socket that refuses is not a decision. Loopback
   stays open on purpose — a guard that cries wolf gets switched off.
   `tests/test_offline_guard.py` asserts the guard fires, because a monkeypatch that stops
   being applied leaves no trace in a passing run.

`permissions: contents: read` at the workflow level. `.github/workflows/release.yml` is a
separate file for exactly that reason: publishing needs `contents: write`, and merging them
would hand every push a permission only a tag push needs. It is the one place a secret is
referenced, and `GITHUB_TOKEN` is Actions' own repository token — it cannot authenticate to
Azure or Google, so it cannot spend the quota this project guards.

**There is no Python version matrix, and that is a decision.** #16 asked for 3.11 + 3.12.
`.python-version`, the Dockerfile and `requirements.txt` all pin 3.12 deliberately (`pydub`
needs the stdlib `audioop`, removed in 3.13) and nothing here has ever been run on 3.11. A
matrix would assert support that does not exist.

**Do not verify row counts by opening the database from a second process.** SQLite's WAL
needs shared-memory coordination that the macOS bind mount does not provide across
processes, so a `docker exec … sqlite3` reader sees only checkpointed rows while the app
holds its connection — measured live at 1 row visible against the app's own 3, with no
`-wal` file present at all. The app is single-connection, so this never affects it. Read the
app's own History panel, or its `Recorded attempt` log lines, instead.

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
- Required: `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION`. `GEMINI_API_KEY` is optional — its
  absence, or `OFFLINE_MODE`, still produces a complete report via `fallback_coach`, with a
  visible note saying which coach wrote it. The full annotated set, including duration
  guards and the budget guard, lives in `.env.example`. `GEMINI_API_KEY`, `GEMINI_MODEL`
  and `AZURE_TTS_VOICE` are all live and read.

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
- **A cancelled assessment does not.** Stopping a run writes no `attempts` row, which is
  also what keeps it off the meter. The two rules answer different questions: how many times
  a *kept* result re-uploaded the audio, versus whether a *discarded* attempt should be
  recorded at all. The second is always no — including the paragraph case where some audio
  may already have reached Azure before the abort landed, which the message says plainly
  rather than claiming nothing was sent.
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
- **Streamlit 1.61.1 exposes no CSS custom properties for its theme anywhere in the DOM.**
  Checked live: `getComputedStyle(el).getPropertyValue('--text-color')` (and
  `--secondary-background-color`) return `""` on `body`, `.stApp`, and every
  `[data-testid]` container tried. A `var(--secondary-background-color, #fallback)` in
  injected HTML therefore always resolves to the fallback — verified, not assumed, after the
  per-word phoneme tooltip (`word_tooltip_html`/`colour_coded_html`, #13) needed an opaque
  background and the obvious "read the live theme" approach silently never engaged. It uses
  one fixed light card instead, checked to read on both the light and dark theme.


### The progress view and the benchmark passage

Landed 2026-08-19 (milestone v0.5.0). The first feature that reads the stored history back.

- **Why a fixed passage at all.** Plotting scores across arbitrary self-chosen texts measures
  **text difficulty, not the speaker** — an easy paragraph scores higher and reads as
  progress. So `progress_view.BENCHMARK_PASSAGE` is frozen, read on a schedule, and its
  series is the headline; free practice is drawn behind it as unconnected points, context
  only.
- **One passage, two instruments.** It was chosen once for this chart *and* for the vowel
  measurement a later chunk needs a calibration read for, because two different passages
  would mean two different recordings of the same 80 seconds. 196 words. It carries the
  commonly substituted consonants (/θ/ /ð/ /v/ /w/, non-flapped /t/ /d/, dark /l/, /ʃ/ /s/,
  /z/ /dʒ/, fourteen final clusters) and the full en-US vowel inventory including FACE and
  GOAT, in stressed unreduced positions.
- **`BENCHMARK_COVERAGE` is that claim as data, and a test asserts every token it lists
  really appears in the passage.** This is not decoration — it caught a token ("which") that
  was listed but had been edited out. A prose justification would have drifted silently.
- **/t/ and /d/ are placed where General American does not flap them** — word-initial, after
  /s/, and word-final or in a cluster. Never *better/water/city*. `phoneme_reference` maps
  `ɾ → t`, so a flapped token scores as /t/ and says nothing about the dental-versus-alveolar
  contrast the passage exists to measure.
- **Three honest gaps, recorded so they are not rediscovered.** ʊɹ (CURE) gets two tokens and
  cannot naturally get more — it is the rarest en-US vowel and is merging into ɔɹ, so treat it
  as best-effort. ɑ and ɔ are subject to the cot–caught merger, so a merged speaker's tokens
  will measure alike; that is a finding, not a defect. Stressed /ð/ lives almost entirely in
  function words, so six tokens is close to the ceiling for natural prose.
- **A benchmark attempt is identified by matching the normalised `reference_text`, not by a
  new column.** `db._migrate` has no upgrade path and `SCHEMA_VERSION` is still 1; the v1
  precedent (coaching columns created NULL so coaching was an UPDATE) says not to add one.
  `progress_view.benchmark_key` reuses `utils.normalise_words`, the same tokeniser the miscue
  diff runs on, so casing and whitespace never split the series — and it works retroactively
  on rows already stored. The consequence: **editing the passage starts a new series**, which
  is what `BENCHMARK_VERSION` records.
- **Mode A and Mode B never share a line, enforced structurally.** Only the benchmark subset
  gets a line mark, and it is single-mode by construction (a 196-word passage is always read
  in paragraph mode); free practice is points shaped by mode, so there is no line for two
  modes to share. A test asserts against `chart.to_dict()` that no `line` layer encodes
  `mode`.
- **A NULL prosody produces no row, never a zero.** The y scale is pinned to 0-100 for the
  same reason: an auto-scaled axis magnifies noise into a trend, which is the exact failure
  the benchmark exists to prevent.
- **Offline replays are excluded from both readers.** An `OFFLINE_MODE` run replays the same
  fixture, so its scores are a constant; thirty identical points is not a trajectory.
- **Rankings count attempts, not tokens.** "Flagged most often" is a question about
  recurrence across sessions; counting raw occurrences would let one long paragraph dominate.
  `benchmark_attempts` is carried beside the total, because on the fixed passage that count
  is comparable read to read.
- **`pandas==3.0.5` and `altair==6.2.2` are pinned explicitly**, read out of the built image
  rather than recalled. Both arrive transitively with streamlit, but `progress_view.py`
  imports them directly and a direct import should not depend on a transitive gift. `numpy`
  is deliberately not pinned — nothing imports it directly.
- **The re-parse is cached, and has to be.** Streamlit executes *both* tab bodies on every
  rerun, including the 0.4 s poll reruns during an assessment, and each pass would otherwise
  re-parse tens of 45-170 kB payloads. `app.parsed_attempts` is `@st.cache_data` keyed on
  `db.attempt_fingerprint` — `(max id, row count)` — with the connection passed as `_conn`
  so Streamlit does not try to hash it.

### The perception trainer and the practice queue

Landed 2026-08-19 (milestone v0.7.0). The first feature that **trains** rather than diagnoses,
and the first that remembers anything between sessions.

- **Why identification and not exposure.** Playing a target next to an attempt is exposure,
  which is the weakest intervention available. The established one is High Variability
  Phonetic Training: forced-choice identification of minimal pairs across several talkers,
  scored immediately, in short daily blocks. Perception gains transfer to production without
  production practice, and **multiple talkers are what make the gain generalise** to new words
  and speakers. `perception_trainer.py` and `practice_queue.py` are pure — no Streamlit, no
  database, no clock — on the same boundary as `progress_view.py` and `rhythm.py`.
- **Six voices, verified by introspection, spanning two generations.**
  `scripts/list_voices.py` calls `get_voices_async("en-US")` and prints the live roster; the
  run on 2026-08-19 confirmed all names and that the listing charges nothing (meter 0 before,
  0 after). The first four names picked — Andrew, Ava, Brian, Emma — all exist, but they are
  all the *same* conversational generation and share a recording character, so the roster is
  now Andrew and Ava plus Aria, Guy, Jenny and Tony from the older set: three male, three
  female, two generations. **A block refuses to run under `MIN_VOICES` (4) rather than
  degrading** — a one-voice block still looks like training on screen. Only plain `…Neural`
  voices; the DragonHD and MAI families are an unverified pricing class on F0.
- **`AZURE_TTS_VOICE` and `perception_trainer.VOICES` must never be collapsed.** The first is
  one consistent model for imitation ("Hear it"); the second is variety for identification.
  They pull in opposite directions on purpose, and `en-US-BrianNeural` is left out of the
  roster precisely because it is the former's default.
- **"Unseen item" means an unheard `(word, voice)` combination**, not an unheard word. That
  is what makes the rule workable at all: `phoneme_reference` holds three to five pairs per
  contrast, and six voices turn those into a 36-60 stimulus pool. It is also the honest
  reading — a familiar word in a voice never heard for that contrast is new information about
  the category.
- **Round-robin selection, not selection-then-ordering.** Picking the best twenty stimuli and
  then trying to order them cannot guarantee voice rotation: the selection can return eight
  of one voice, and no ordering of eight-in-twenty avoids a consecutive repeat. The pool is
  dealt one voice at a time instead, so rotation is a property of the *selection*. The
  pair-avoidance preference searches only within one novelty tier, or it quietly returns a
  stimulus already heard while an unheard one waits.
- **The chance floor is arithmetic, not a threshold, and it is stored per trial.** A
  two-alternative forced choice scores 50% by guessing, so `perception_trainer.chance_floor`
  derives it from the trial's `alternatives` count and `chance_caption` builds the sentence
  that has to sit beside every accuracy figure — one definition, so it cannot be dropped from
  one of the several places accuracy appears. `perception_trials.alternatives` makes it a fact
  on the row rather than an assumption in whatever reads it later.
- **Three item kinds, two graduation rules, and the split is forced by the data.** A **vowel
  gap needs no formant work** — it is a flagged substitution whose expected phoneme is a
  vowel, diphthong or r-coloured in `phoneme_reference`, so it trains through the same block
  as a consonant contrast and only the label and the reason differ. **Azure emits no stress
  marks**: the fixtures return `unpredictable` as `ʌn/pɹə/dɪk/tə/bəl` with accuracy scores and
  nothing else, so a stress target has no scored check that costs no STT. It gets *the due
  drill* rather than *the due block* (the brief names both) and graduates on the evidence
  drying up — the word stops appearing in the flagged aggregate. A CMUdict-backed
  stress-*location* task is the upgrade path, deliberately not built.
- **Evidence comes from the aggregates the Progress tab already draws**, so the queue and the
  chart cannot disagree about what recurs. `progress_view.weak_syllables` was added beside
  `flagged_phonemes`/`flagged_words`, sharing the same `_tally`, and it reads
  `fallback_coach.SYLLABLE_RED` rather than restating the cut.
- **The queue never invents a target.** `promote` can only choose from `candidates`, which can
  only come from stored attempts. With no history it offers nothing and says so; **no L1 hint
  was built**, which keeps `projectbrief.md`'s no-hardcoded-L1 non-goal intact. Three slots at
  most, one of each kind before a second of any, so three consonant contrasts cannot crowd out
  a vowel gap flagged just as often.
- **A `Decision.reason` deliberately does not restate the graduation rule.** `render_target_card`
  renders `graduation_rule` on the line directly above it, and the first browser check showed
  the whole rule printed twice three inches apart — the same padding the delivery drills
  already taught to avoid.
- **Trials are written as they are answered, not at block end.** An abandoned block keeps its
  evidence; whether it earns a *verdict* is a separate question `practice_queue` decides from
  the trial count. Store the evidence, not only the verdict.
- **Two new tables, `SCHEMA_VERSION` still 1.** `practice_targets` and `perception_trials` are
  additive `CREATE TABLE IF NOT EXISTS`, so an existing v1 database gains them on the next
  `connect()` and `user_version` never moves — the v1 coaching-column precedent.
  `reviews_passed` is one column beyond the brief's list; the alternative was a schedule
  pointer inside `evidence`, which is for evidence.
- **The TTS disk cache holds synthesised audio only** (`data/tts_cache`, keyed by
  `(voice, text, rate)`, `data/` now gitignored in full). This does not touch the
  no-stored-audio rule: that rule covers the *user's* recordings and nothing writes one to
  disk. Plain text at the normal rate, never SSML — the meter charges the payload sent and
  SSML bills its markup. **The disk lookup happens before the pre-flight and before the
  meter**, the same ordering `play()` depends on.
- **Measured live against Azure on 2026-08-19.** A fresh 20-trial block on `/θ/ → /t/` needed
  **38 clips and charged 167 characters** — one `tts_usage` row per clip, none double-charged.
  A second block on the same contrast drew different stimuli from the same pool and charged
  **9 characters for the 2 clips it had never played**, with the other 36 served from disk.
  The whole corpus is ~4,600 characters at six voices against 500,000 a month.
- **Synthesis is sequential, at roughly one second per clip**, so a fresh block takes about
  40 seconds to prepare before the first trial. The progress bar names the count while it
  runs. Repeat blocks on a warm cache are instant; this cost is paid once per contrast.

### Shadowing practice

Landed 2026-08-19 (milestone v0.8.0). The first feature where practice happens **while
speaking** rather than afterwards, and the first that measures itself.

- **A flow wrapped around the existing path, not a new analysis path.** A shadowed read goes
  through the same `prepare_audio` → `start_assessment` → `speech_analyzer.analyse` as any
  Mode B attempt and is stored as an ordinary row. No parsing branch, no new normalised shape,
  no new merge rule. The only addition is a tag.
- **Two modes, and only one is assessed — the split is a finding, not a preference.**
  *Simultaneous* is one continuous clip of the whole passage; the recording is continuous, so
  its fluency and prosody are directly comparable to a cold read, and that comparability is
  the whole acceptance test. *Echo* is per-sentence clips concatenated with a silence matched
  to each clip's own duration; it is **never assessed**, because a recording made of phrases
  separated by silences carries a structural pause between every one of them and Azure would
  mark the delivery down for a gap the format put there. Offering it as a warm-up is honest;
  scoring it would not be.
- **Headphones are a requirement, not a suggestion.** Both modes play a voice while the
  microphone is open, so on speakers Azure hears the model as well as the speaker and assesses
  a mixture. `shadowing.HEADPHONES` says so above the recorder. This is also the first thing to
  suspect if accuracy ever moves: shadowing trains delivery, and a large accuracy delta is more
  likely the model bleeding into the take than a result.
- **No rerun may happen between "record" and "play".** `st.audio_input` holds a live
  `MediaRecorder` in the browser and a Streamlit rerun re-renders that component, so the model
  player and the recorder are both on screen *before* recording starts, on a plain `st.audio`
  with native controls and no `autoplay`, with no button between them. A "fetch the model now"
  button sitting there would cut the take in half.
- **Tagging is a new additive `attempt_tags` table; `SCHEMA_VERSION` is still 1.** `attempts`
  is created with `CREATE TABLE IF NOT EXISTS`, so a column would need a real `ALTER TABLE` and
  `db._migrate` has no upgrade path — the v0.7.0 precedent (`practice_targets` /
  `perception_trials` added additively) applies directly. `attempt_series` and
  `attempt_payloads` join the flag in, so no reader needs a second query.
  **`rhythm.BASELINE_CAPTURE_MARKER`'s reference-text prefix is deliberately NOT reused here**:
  the comparison pairs a shadowed read to a cold one *by matching that text*, so a marker in it
  would break the very match the feature depends on.
- **Keeping shadowed reads off the cold trajectory is the correctness crux.**
  `progress_view.is_benchmark` identifies a benchmark read by matching its reference text, so
  an untagged shadowed read would land on the headline line and the nPVI series as though it
  were cold — inflating the exact line the benchmark design exists to keep honest.
  `progress_view.cold_attempts()` is applied at the score trajectory, the rhythm chart and
  `days_since_benchmark`. Shadowed benchmark reads get their **own dashed series** on the score
  chart rather than being dropped, because two lines converging is the acceptance test
  rendered; the rhythm chart excludes them outright, since a shadowed read's nPVI is largely
  the synthesiser's and would be `benchmark_tts_baseline.json` taking a detour through a human.
- **The flagged aggregates deliberately keep including shadowed reads.**
  `progress_view._tally` counts the attempts a thing appeared in and
  `practice_queue.candidates` thresholds on that cumulative count, so an assisted read can only
  ever *raise* a count, never retire a target early. A sound still flagged while a model is
  carrying the read is stronger evidence, not weaker.
- **A fourth `shadow` kind in `practice_targets`, which never graduates.** It is kept out of
  `KIND_ORDER` (that tuple drives promotion) and `practice_queue.promotable()` is what every
  other rule keys on — without it a standing practice would eat one of the three
  `MAX_ACTIVE_TARGETS` slots and silently retire a sound the recordings were still flagging.
  `grade()` returns its state unchanged so `apply_decisions` writes nothing for it; `next_due`
  is a fixed `utils.SHADOW_INTERVAL_DAYS` gap that never widens, because there is no graduation
  for a widening schedule to grow confident about. **The row is created on first use, never by
  `promote()`** — the "queue never invents a target" rule is about claims made from the user's
  own flagged history, and a standing practice makes no such claim.
- **`MAX_DURATION_SECONDS_PARAGRAPH` is 180, not 120.** The benchmark passage is 61.8 s through
  Azure TTS at the normal rate and ~95 s at `rate="-35%"`, and a shadowed read starts recording
  before the model and stops after it. A read is billed for its own seconds either way, so the
  higher ceiling costs nothing unspent.
- **The disk cache already took a `rate`,** so `tts.py` needed no change: a slow rendering is
  cached separately from the normal one under the existing `(voice, text, rate)` key.
  `app.synthesise_clip` was extracted from `buy_block_audio` so the perception block and the
  shadow model charge the meter by exactly the same rule; the batch pre-flight stays with each
  caller, which is what stops a guard approving a run whose real charge lands mid-batch.
- **Two surfaces can start an assessment, so one of them has to own the result.** `last_key` is
  a single session slot and Streamlit executes *every* tab body on every rerun, so once the
  shadow surface could also start a job, the Practice tab rendered the same result underneath
  it — and `render_result` builds its widget keys from the attempt, so the second render raised
  `StreamlitDuplicateElementKey` rather than merely duplicating the panel. `RESULT_OWNER_KEY`
  records which surface produced it; each renders only its own, and leaving a shadow session
  clears the result it owned. **Found on the first real read, not by any test** — no test that
  drives one surface at a time can reach it.
- **A shadow row must not make the queue look non-empty.** It is a standing practice rather
  than something the recordings promoted, so `render_today`'s empty-state check keys on
  `practice_queue.promotable` — otherwise a database with one attempt and nothing promoted
  reports "nothing due, they are all on the review schedule" about targets that never existed.
  Same predicate as the `MAX_ACTIVE_TARGETS` fix, and for the same reason.
- **Measured live on 2026-08-19.** The model clip is 975 characters and 61.775 s (the committed
  TTS baseline says 61.8 s); the echo track is 14 clips, 959 characters, and 129.15 s. Both are
  bought once per `(passage, rate)` and served from disk after that — a second preparation
  charged nothing. The whole live exercise cost **1,934 TTS characters and no STT**.
- **What the comparison is, and what a failure of it looks like.** `progress_view.shadow_pairs`
  sets each shadowed read against the **nearest cold read of the same passage by time, either
  side** — requiring the cold read to come first would throw away every pair from the first
  weeks, which are exactly the weeks the narrowing question is about — and carries the distance
  in days on the row, so a pair straddling two months looks like the weak evidence it is. Only
  fluency and prosody are compared. `shadow_summary` never states a delta without the number of
  pairs beside it, the same discipline as the perception trainer's chance floor.

### Timing data and nPVI

Landed 2026-08-19 (milestone v0.6.0). The de-risking chunk for all later accent work — rhythm,
vowel-space drift, an F0 track, slicing audio to play a sound back — and a measurement in its
own right.

**What the parser now carries.** Azure sends `Offset` and `Duration` on every word, syllable
*and* phoneme; `_normalise_word` discarded all of it. Every level now carries `offset_ticks`,
`duration_ticks`, `start_s` and `end_s`, always present, `None` on an `_omission` (a word never
spoken has no extent). The top-level `SNR` goes into `overall_scores` as `snr_db` and
`snr_db_min` — no column, no migration, because `db.record_attempt` reads five named keys and
ignores the rest. Continuous mode returns **one SNR per utterance, not one per recording**, so
both a duration-weighted figure and the worst utterance are kept: measurement quality is
governed by the worst segment, and averaging hides the utterance that ruins a reading.

**Purely additive, and retroactive.** `progress_view.parse_attempts` re-parses `azure_raw_json`
through `speech_analyzer.normalise` on every Progress render, so every attempt already stored
gained timing and an nPVI with no migration and no backfill. This is the second time storing
responses verbatim has paid for itself.

**Three facts about Azure's timing, established from the payloads rather than the docs:**

- **Offsets are ticks from the start of the AUDIO STREAM, not the start of the file.** In the
  drill fixture the whole response carries `Offset: 16900000` and the first word begins at
  exactly that tick — 1.69 s in. Nothing yet depends on it. **The chunk that slices audio must
  read the payload's own top-level `Offset` first** rather than treating a word offset as a file
  position.
- **Everything lands on a 10 ms grid.** Every `Offset` and `Duration` at every level is a whole
  multiple of `speech_analyzer.FRAME_TICKS`. Asserted across all fixtures, not trusted.
- **Segments tile their parent with a systematic one-frame seam.** A word's first phoneme starts
  exactly at the word's `Offset` and its last ends exactly at `Offset + Duration` (20/20 words),
  yet consecutive phonemes are separated by exactly 10 ms (62/62; syllables 9/9). So
  `sum(durations) + 10 ms x (n-1) == parent duration`, and the self-consistent reading is that
  Azure reports `Duration` as `(frames - 1) * 10 ms`. **`Duration` is used raw anyway** — the
  correction is an inference, the reported value is a fact — and the resulting upward bias on
  nPVI (~5 points) is documented rather than silently applied.

**nPVI lives in `rhythm.py`**, a pure reader of the normalised shape on the same boundary as
`progress_view.py`. The vowel predicate reuses `phoneme_reference`'s existing
`consonant | vowel | diphthong | r-coloured` classification rather than restating an inventory
that would then have two places to drift from; a test asserts all 39 symbols across all fixtures
resolve through it. Three segmentation decisions, each measured:

- adjacent vocalic phonemes **merge** into one interval, because a vocalic interval is
  contiguous vowel and not a phoneme (it happens for real: "rather" /ɚ/ into "unpredictable"
  /ʌ/);
- an interval's length is its **span**, not the sum of its phonemes' durations, so the
  frame-grid bias is one frame per *interval* rather than one per *phoneme*;
- **pauses over 100 ms end the run** and no pair spans one — the fixture's gaps are bimodal
  (one frame, a few at 30 ms, then real pauses at 210 ms) and the score is flat at 55.85 for any
  threshold from 50 to 200 ms, so 100 sits mid-plateau.

**Which comparison is primary — and the intuitive answer is wrong.** Published General American
bands come from hand-segmented corpora reading different material, so scoring Azure-derived
durations against one compares three things at once. Measured on the committed fixture, four
defensible policies give **50.3 / 54.75 / 55.85 / 56.25 on the same unchanged recording** — a
5.4-point spread from policy alone, wider than several published cross-language contrasts. So
the primary comparison is `tests/fixtures/benchmark_tts_baseline.json`: the same passage through
Azure TTS and the same pipeline, one variable. **Published bands get no chart ink anywhere** —
with a same-pipeline baseline on the same axes, drawing a band that cannot be compared to it
would only invite the comparison. The baseline is a fixed point, *not* a native speaker; a
synthesiser's rhythm is its own, and the UI says so.

- Captured once by `scripts/capture_baseline.py`: en-US-BrianNeural, **nPVI 58.45 over 180
  pairs**, 61.8 s, SNR ~47 dB (synthesised audio is far cleaner than the ~25 dB of a real
  recording). Synthesised at the plain rate, never `slow_ssml`, which would stretch exactly the
  durations being measured. The script refuses to overwrite without `--force`, since re-capturing
  moves the fixed point every stored reading is plotted against.
- **The capture writes an attempts row, and that row must be marked.** It really was billable
  seconds and the meter derives from that table, so skipping it would under-report the spend —
  but its reference text *is* the benchmark passage, so unmarked it puts a point nobody spoke on
  the trajectory, reports the benchmark as read today by a machine, and lets the synthesiser's
  weak sounds into "what keeps going wrong". `rhythm.BASELINE_CAPTURE_MARKER` prefixes the row
  and `progress_view.spoken_attempts` filters it at every entry point, so the trajectory, the
  rankings and the last-read date cannot disagree about which attempts exist.
- `MIN_PAIRS = 20` gates on **how much connected speech there was, not on the mode label** —
  which is why the two-sentence drill fixture does produce a figure and a real three-word drill
  does not.
- The seeder borrows phoneme timings from the baseline (its 196 words are the same passage in
  the same order), so the demo shows a real trend rather than a random walk. Without that fixture
  the seeded rows carry no timing and the rhythm chart renders its empty state.

**Two things measured live against Azure on 2026-08-19, after the chunk was built:**

- **Azure's segmentation is exactly reproducible.** The same bytes assessed twice returned
  identical offsets and durations to the tick, and a fresh live call on the recording the
  fixtures came from reproduced the committed `sample_azure_continuous.json` figure of
  **55.26** to two decimal places. There is no run-to-run noise to average out.
- **The audio codec moves nPVI by more than the seam bias does.** The same unchanged take
  scores **55.26 as a WAV and 53.10 as an m4a** — same duration, same words, same 25 pairs.
  2.16 points from lossy compression alone. So a reading uploaded from a phone is not
  comparable to one recorded in the browser, and the number is trustworthy over time only if
  the recording format is held still. Stated in `rhythm.py` and in the UI caption.

**Audio on disk is now permitted but not built.** The user lifted the "no stored audio" rule on
2026-08-19: recordings may be kept on disk, never committed, with the path and hash in the
database. Nothing in v0.6.0 needed it, so no column was added and `SCHEMA_VERSION` stays 1 — a
schema v2 would be this project's first real migration and belongs to the chunk that needs it.
The only audio landing on disk today is the baseline WAV under the gitignored `audio/`.
