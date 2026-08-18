# 2026-08-18 — Azure analysis core: Modes A & B, offline-testable, persisted to SQLite

## Context

The repo is a runnable scaffold and nothing more: `app.py` renders a placeholder page, and
every feature module the master plan calls for (`speech_analyzer.py`, `audio_utils.py`,
`utils.py`, …) was deliberately left uncreated. `memory-bank/progress.md` names the next
concrete step — commit a realistic Azure pronunciation-assessment fixture and build the
parsing/normalisation layer against it, so the whole downstream stack (colouring, coaching,
fallback) can be developed without spending the 5-hour monthly Azure quota.

This chunk does that, and closes the loop so the result is actually usable: audio in →
Azure assessment → normalised result → stored in SQLite → shown on a bare Drill page.
`memory-bank/techContext.md` records SQLite as chosen but unbuilt with "what gets stored"
open; this chunk answers it — **both raw API responses are stored verbatim**, Azure's now
and Gemini's when the coaching chunk lands.

Deliberately **not** in this chunk: Gemini coaching, the offline fallback coach,
`phoneme_reference.py`, TTS/"Hear it", Mode C (unscripted), and the rich §11 UI
(colour-coded text, reference-vs-heard diff, delivery panel). Each gets its own chunk.

## Scope decisions taken with the user

- Backend + a **minimal** Mode A UI, so the chunk is demonstrable end to end.
- Modes **A (Drill, single-shot)** and **B (Paragraph, continuous + merge)**. Mode C later.
- The monthly usage meter is **derived from SQLite**, not from `.usage.json` — one store,
  no drift. `BUDGET_STATE_PATH` gets dropped from `.env.example`.

---

## Files

New, all at repo root unless noted:

| File | Responsibility |
|---|---|
| `utils.py` | Config loading, `Mode` enum, thresholds, SHA-256 hashing, logging, retry policy |
| `audio_utils.py` | Convert to 16 kHz/16-bit/mono PCM WAV, duration measurement + validation, temp-file lifecycle |
| `speech_analyzer.py` | Azure recognition (single-shot + continuous), raw-JSON capture, normalisation, utterance merge |
| `db.py` | SQLite schema, connection, `record_attempt`, monthly usage queries |
| `budget.py` | Pre-flight STT/TTS guard over `db`'s meters; F0 tier acknowledgement check |
| `tests/fixtures/sample_azure_response.json` | Real captured single-shot payload |
| `tests/fixtures/sample_azure_continuous.json` | Real captured array of per-utterance payloads |
| `tests/test_parsing.py`, `tests/test_audio_utils.py`, `tests/test_db.py`, `tests/test_budget.py` | Offline coverage |
| `tests/conftest.py` | Puts the repo root on `sys.path` so root-level modules import, and forces `OFFLINE_MODE=true` for the whole suite |
| `scripts/capture_fixture.py` | One-off: run a real assessment and write the raw payload to `tests/fixtures/` |

Modified: `app.py`, `Makefile`, `requirements.txt`, `.env.example`, `README.md`, `memory-bank/*`.

`db.py` and `budget.py` are **additions to master plan §8's file list**, not renames of
anything in it. §8 predates both the SQLite decision and the "meter lives in the DB"
decision; `utils.py` would otherwise become the place everything unrelated lands.

`scripts/pronunciation_test.py` stays as-is — it is the working reference for the SDK call
shape (`speech_config.speech_recognition_language`, `enable_prosody_assessment()`,
`pron_config.apply_to(recognizer)`) and `speech_analyzer.py` should follow it rather than
re-derive it. `scripts/setup.py` is the model for "Python, not shell, once there's branching".

---

## Design

### `utils.py`

- `load_config()` — tries `os.environ`, then `st.secrets` (import guarded in `try/except`
  so tests run outside a Streamlit runtime), then `.env` via `python-dotenv`. Fails with a
  message naming the missing key and **never** echoing a value.
- `Mode` enum: `DRILL`, `PARAGRAPH`, `UNSCRIPTED` (C declared, not implemented).
- Threshold constants with the comment the master plan requires — heuristics, not
  Azure-defined: `WORD_RED < 80 ≤ WORD_AMBER < 95 ≤ WORD_GREEN`,
  `PHONEME_RED < 60 ≤ PHONEME_AMBER < 85 ≤ PHONEME_GREEN`. Unused by the minimal UI;
  defined here now so the colouring chunk has one home for them.
- `attempt_hash(reference_text, audio_bytes) -> str` — SHA-256, used as the session cache key.
- `retry_transient(fn, attempts, ...)` — exponential backoff with jitter. Retries
  `ServiceUnavailable` / `ServiceTimeout` / `429` / connection errors only. `401`, `403`,
  `BadRequest` propagate immediately, wrapped so the message distinguishes **bad key** from
  **quota exhausted**.
- `configure_logging()` with a filter that redacts anything matching a configured secret's
  value, so a key can never reach a log line or a surfaced traceback.

### `audio_utils.py`

- `to_pcm_wav(data: bytes) -> bytes` — pydub → 16 kHz, 16-bit, mono. Handles wav/mp3/m4a/
  webm/ogg (system `ffmpeg` is already in the image).
- `duration_seconds(wav_bytes) -> float`.
- `validate_duration(seconds, mode)` — `MIN_DURATION_SECONDS` globally, per-mode maximum
  from the existing `.env.example` vars. Raises with an actionable message.
- `temp_wav(wav_bytes)` — context manager writing a temp file and deleting it in `finally`,
  so it goes even when Azure raises mid-call (acceptance criterion 8). Azure's
  `AudioConfig(filename=…)` needs a real path, which is the only reason a temp file exists.

### `speech_analyzer.py`

Assessment config is built **from JSON**, per master plan §4 — not via constructor kwargs
(`phoneme_alphabet=` raises `TypeError`; `grading_system=`/`granularity=` take enum members):

```python
{"referenceText": ..., "gradingSystem": "HundredMark", "granularity": "Phoneme",
 "phonemeAlphabet": "IPA", "nBestPhonemeCount": 5,
 "enableMiscue": mode is Mode.DRILL, "enableProsodyAssessment": True}
```

`pron_config.apply_to(recognizer)` is mandatory and gets its own test assertion.

- **Mode A** — `recognize_once_async()`, `enableMiscue: True`. Returns one raw payload.
- **Mode B** — `start_continuous_recognition_async()`, accumulate `recognized` events, stop
  on `session_stopped` / `canceled` via a `threading.Event`. Returns a **list** of payloads.
  `enableMiscue` is `False` (Azure does not honour it in continuous mode), so **omissions
  and insertions are computed locally** by diffing normalised reference words against
  recognised words with `difflib.SequenceMatcher`. Locally-derived error types carry a
  `source: "local_diff"` marker so the UI never presents them as Azure's own judgement.
- Raw JSON comes from `result.properties[PropertyId.SpeechServiceResponse_JsonResult]` and
  is kept **verbatim** for storage before any normalisation touches it.
- **`NoMatch` and `Canceled` are handled distinctly**, per §10 — `NoMatch` means the call
  succeeded but no speech was detected (actionable: check the mic, speak louder, check the
  clip isn't silence); `Canceled` carries `cancellation_details.reason` and gets a message
  that separates a bad key from an exhausted quota from a network failure. Neither is
  allowed to surface as a raw traceback.
- `recognised_text` for Mode B is the per-utterance display texts joined in order; for
  Mode A it is the single result's text.
- Word-level delivery error types (`UnexpectedBreak`, `MissingBreak`, `Monotone`) are
  **carried through normalisation** even though the delivery *panel* (§5/§11) is a later
  chunk — dropping them here would mean re-touching the parser then.

**Merge (Mode B).** Weight by each utterance's `Duration` (100-ns ticks), never a naive mean:

- `accuracy`, `fluency`, `prosody`, `pron_score` → duration-weighted averages.
- `completeness` → **recomputed globally** as `(reference words − omitted) / reference words`.
  Azure's per-utterance completeness is scored against the whole reference text and is
  meaningless to average.
- `pron_score` as a duration-weighted average is an **approximation** — Azure's composite
  weighting is not published. Noted in a comment and in `techContext.md`.
- `prosody` is weighted over only the utterances that actually carry a prosody score, and is
  `None` — never `0.0` — when no utterance has one.

`normalise(payloads) -> dict` emits exactly the §4 shape, including `nbest` per phoneme
(the field that makes the tool diagnostic rather than a scoreboard) and `syllables`.

**`OFFLINE_MODE=true`** short-circuits before any network call and replays the committed
fixture. This is the default path for tests and UI work.

### `db.py`

SQLite via stdlib `sqlite3`. `connect(path)` returns a plain connection with
`check_same_thread=False` and WAL enabled; `app.py` wraps it in `@st.cache_resource` (the
Streamlit-specific traps already recorded in `techContext.md`). `db.py` itself never imports
Streamlit, so tests and scripts can use it. Path from `DB_PATH`, default `./data/coach.db`
— the project directory is bind-mounted, so it survives `docker compose down`; `connect()`
creates the parent directory if it is missing. Schema version tracked with
`PRAGMA user_version`. `.gitignore` already covers `*.db` and the WAL sidecars.

```sql
CREATE TABLE attempts (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at       TEXT    NOT NULL,   -- UTC ISO-8601
  mode             TEXT    NOT NULL,   -- drill | paragraph | unscripted
  reference_text   TEXT,               -- NULL for unscripted
  recognised_text  TEXT,
  audio_seconds    REAL    NOT NULL,   -- what the STT meter is charged
  audio_sha256     TEXT    NOT NULL,   -- dedupe key; no audio is stored
  pron_score       REAL, accuracy REAL, fluency REAL, completeness REAL, prosody REAL,
  azure_raw_json   TEXT    NOT NULL,   -- verbatim; a JSON array for continuous mode
  gemini_raw_json  TEXT,               -- NULL until the coaching chunk fills it
  coach_source     TEXT,               -- 'gemini' | 'fallback' | NULL
  offline          INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_attempts_created_at ON attempts(created_at);

CREATE TABLE tts_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL, characters INTEGER NOT NULL, voice TEXT
);
```

`gemini_raw_json` / `coach_source` are created now and left NULL — the columns exist so the
coaching chunk is an `UPDATE`, not a migration. `tts_usage` likewise exists so the TTS
chunk has a meter to write to. No audio bytes are ever stored, per the brief.

Queries: `monthly_stt_seconds(now_utc)`, `monthly_tts_characters(now_utc)`,
`record_attempt(...) -> id`, `recent_attempts(limit)`.

### `budget.py`

- `preflight_stt(seconds, mode)` — projects the cost of the **next** call from the DB-derived
  meter and refuses it if cumulative spend would exceed `MONTHLY_BUDGET_USD`. Rounds **up**,
  counts attempts rather than successes, and knows that Mode C two-pass charges twice (the
  multiplier is in place even though C is not implemented).
- Two independent meters (STT seconds, TTS characters) — never combined.
- `require_f0_acknowledgement()` — when `MONTHLY_BUDGET_USD` is `0.00`, refuse to start
  unless `AZURE_TIER_CONFIRMED_F0=true`. The SDK cannot read the resource SKU. "Refuse" is
  `st.error(...)` + `st.stop()` in the app and a non-zero exit in scripts — never a bare
  exception the user has to decode.
- **`OFFLINE_MODE=true` bypasses both the tier check and the pre-flight guard entirely.**
  Nothing is being spent, so gating UI work behind an F0 acknowledgement would make the
  zero-cost path harder to use than the paid one — exactly backwards.
- On a `403` from Azure, mark the month exhausted locally regardless of the meter — the
  provider is authoritative, the local estimate never overrides it.
- Month boundary is UTC and the meter is presented as an estimate; the Azure portal is
  authoritative.

### `app.py` — minimal Drill page

Mode selector (Drill / Paragraph; Unscripted disabled with a "next chunk" note), reference
textarea with 3–4 presets **chosen to load the phonemes in master plan §7** — the
Urdu/Punjabi L1 interference set (/θ/ /ð/ /v/ /w/ /æ/ /ɛ/, dental vs alveolar /t/ /d/, dark
/l/, final consonant clusters) — so a practice attempt actually exercises the sounds the
coaching chunk will diagnose. `st.audio_input` plus an `st.file_uploader` fallback
for wav/mp3/m4a/webm/ogg, a Run button, then: recognised text next to the reference, the
metric row (pron / accuracy / fluency / completeness / prosody, rendering `—` not `0` when
prosody is unavailable), and a plain word table with error types. Plus the usage line —
"≈ X of Y seconds used this month (local estimate — the Azure portal is authoritative)".

Reference-text validation before any call: non-empty, ≤ 1000 characters, and a warning when
it contains digits (Azure normalises "333" and "three thirty-three" differently, which
breaks word alignment).

Results cached in `st.session_state` keyed by `attempt_hash`, bounded to the 10 most recent.
The comment states the real reason: Streamlit re-runs the whole script on every widget
interaction, so without the cache one button click would re-run the entire Azure pipeline.
Cross-session dedupe is a side benefit, not the requirement.

No API call is made anywhere in `app.py` — it calls `speech_analyzer` and `db` only.

---

## Fixtures — how they get captured

Real payloads, not hand-written, per master plan §8 and `progress.md`.

**You are supplying a real recording and its reference text, plus a filled `.env`.** That is
the ideal input — a genuine human attempt produces the real substitutions, `nbest`
alternates, and prosody error types that this whole layer exists to surface. Two clips would
cover both modes: one drill-length sentence (under ~15 s, the single-shot ceiling) and one
100–200 word paragraph. If only one arrives, the drill clip is the one to prioritise.

1. `scripts/capture_fixture.py <audio> <reference.txt> --mode drill|paragraph --out <path>`
   converts the audio, runs the real assessment through `speech_analyzer`, and writes the
   verbatim payload(s) to `tests/fixtures/`.
2. Cost: well under a minute of the 18,000-second monthly allowance.
3. Fallback if no recording arrives: generate WAVs host-side with macOS
   `say --file-format=WAVE --data-format=LEI16@16000` (the container has no TTS) and
   **deliberately mismatch reference and spoken text** — e.g. speak "I think the weather is
   fine" against reference "I sink the whether was fine". Clean TTS scored against its own
   text returns near-perfect results with no `nbest` alternates and no error types, which
   would make the fixture useless for testing exactly the paths that matter. This is
   strictly the worse option; real speech is preferred.

The supplied audio is used for capture and then deleted — it is never committed, and no
audio is written to the database (the brief rules out stored audio).

Ordering consequence: the thin recognition code is written **before** the fixture exists;
normalisation, merge, and every test are written **after**, against the captured payload.

Fixtures are inspected before committing — an Azure payload contains no key, but this is
checked rather than assumed.

---

## Config changes

`.env.example`: drop `BUDGET_STATE_PATH`, add `DB_PATH=./data/coach.db`. Everything else the
file already documents is now actually read by code, so the "nothing reads these yet" header
comment goes.

`requirements.txt`: add `pytest`, version verified against PyPI at implementation time (the
standing preference is to verify pins, not recall them). It goes in the one manifest rather
than a new `requirements-dev.txt` — `techContext.md` records "one manifest" as a deliberate
decision, deployment has since been demoted from a goal, and a test runner in a local image
costs nothing.

`Makefile`: add `test:` running `docker compose run --rm app python -m pytest -q`. Adding a
dependency means the image is stale, so the same chunk documents that `requirements.txt`
changes need a `docker compose build` (or `make up`, which builds) before `make test` sees
`pytest`. This stays a Makefile one-liner — no branching, so it does not earn a
`scripts/*.py` file under the standing preference.

---

## Steps

Committed as each piece lands, not one commit at the end (per `CLAUDE.md`).

1. Write `plans/2026-08-18_azure-analysis-core.md` in the repo and append its `planned` row
   to `memory-bank/history.md`. Plan mode cannot write repo files, so this is step one.
2. `utils.py`, `audio_utils.py` + their tests.
3. `db.py`, `budget.py` + their tests (pure SQLite, no network).
4. `speech_analyzer.py` recognition paths (Modes A and B).
5. Write `scripts/capture_fixture.py`, capture both fixtures with it, commit the fixtures.
   Needs the filled `.env` and the recording; everything before this point does not.
6. `speech_analyzer.py` normalisation + merge, written against the captured fixtures;
   `tests/test_parsing.py`.
7. `app.py` minimal Drill/Paragraph page.
8. `README.md`: the §2 budget table, the storage note (both raw API responses kept in a
   local SQLite file, no audio), and the §13 disclosures — audio is transmitted to Azure.
9. Update `memory-bank/`. `techContext.md`'s Architecture section currently says "No
   database, no accounts, no persistent audio storage" and lists `app.py` as the only
   source file — both now false. `progress.md`'s current focus and next step move on.
   Move the `history.md` row to `implemented`. Verified facts written directly; judgement
   calls proposed first.
10. Run a review pass over the diff (`/code-review`) and fix what it surfaces before the PR
    goes up, rather than after.
11. Open the PR, with a bullet summary of what shipped.

## Verification

- `make test` passes with **no API keys set and no network** — this is acceptance
  criterion 5, and the tests must fail rather than skip themselves when keys are absent.
- `tests/test_parsing.py` asserts the parsed fixture contains a `PronunciationAssessment`
  block (criterion 3 — proves `apply_to` was actually called), that prosody is populated,
  and that every flagged phoneme reports a produced `nbest` alternate, not only an expected
  one (criterion 4).
- Merge test: a hand-built two-utterance case with unequal durations, asserting the
  duration-weighted result differs from the naive mean and matches the expected value.
- `test_audio_utils.py` asserts the temp file is gone after the context manager exits
  **when the body raises** (criterion 8).
- A test asserting a fake key never appears in a log record or a formatted error
  (criterion 7).
- `OFFLINE_MODE=true make up` → record anything → a full result renders with zero network
  calls.
- Live check, once, Mode A: `make up`, record a drill sentence, confirm real scores render
  and prosody is not blank. Then Mode B with a short paragraph, confirming the merge path
  produces one coherent set of scores.
- After both: query the DB and confirm one row per attempt with `azure_raw_json` populated
  verbatim, `gemini_raw_json` NULL, and no audio anywhere:
  ```bash
  docker compose run --rm app python -c "import db,json; c=db.connect(); [print(r) for r in db.recent_attempts(c,5)]"
  ```
- Confirm the app refuses to start with `MONTHLY_BUDGET_USD=0.00` and
  `AZURE_TIER_CONFIRMED_F0=false`, with a message that explains why — and that the same
  config with `OFFLINE_MODE=true` starts fine.

Acceptance criteria **not** in scope here, so review shouldn't flag them: 1 and 2 (Hugging
Face deployment and warm-processing time — hosting is no longer a goal) and 6 (a complete
report with `GEMINI_API_KEY` unset — there is no coaching layer yet to degrade).

## Known unknowns

- Whether Azure's continuous-mode per-utterance payload carries `Duration` in every case;
  if an utterance lacks it, the merge falls back to equal weighting and logs a warning.
- Whether the supplied recording produces enough `UnexpectedBreak` / `MissingBreak` /
  `Monotone` error types to exercise the delivery aggregation. If not, that aggregation is
  written against a hand-extended fixture and the extension is marked as synthetic in the
  file rather than passed off as captured.
- Timing: steps 2–4 don't need the recording or the keys, so they proceed immediately;
  step 5 waits on the audio arriving.
- `pytest` version — verified at implementation time, not recalled.
