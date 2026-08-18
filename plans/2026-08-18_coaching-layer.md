# The coaching layer — `phoneme_reference.py`, `fallback_coach.py`, `ai_coach.py`

## Context

The app is a scorer, not a coach. It shows what went wrong down to the phoneme (`/θ/ → /s/`
on "thursday"), renders it legibly, and plays the native rendering — but it never says what
to *do* about it. `memory-bank/progress.md` names this as the next concrete step, and
`db.attach_coaching` plus the `gemini_raw_json` / `coach_source` columns have existed since
schema version 1, so storage is an UPDATE and not a migration.

The fallback is the primary path, not degradation: the free Gemini tier runs out, and a
deterministic report from Azure's own data is fully testable offline with no key. So the
build order is `phoneme_reference.py` → `fallback_coach.py` → `ai_coach.py`, and the report
shape is defined **once** and emitted by both coaches, so the UI has one renderer.

## Already verified (offline, no quota spent)

- **google-genai 2.18.1** — `types.GenerateContentConfig` carries `response_mime_type`,
  `response_schema` (accepts a pydantic class), `response_json_schema`, `system_instruction`,
  `temperature`, `max_output_tokens`. `genai.Client(api_key=…)` and
  `client.models.generate_content(model=…, contents=…, config=…)` match the intended calls.
  `_transformers.t_schema` converts the exact required schema — nested `MinimalPair` list
  included — into a clean Gemini `Schema` with `required` and `property_ordering`. No
  unsupported keyword in the shape below.
- **Error surface** — `google.genai.errors` has `APIError` (carrying `.code`, `.status`,
  `.message`), `ClientError` (4xx), `ServerError` (5xx). That is what the 429 branch reads.
- **`GenerateContentResponse`** — `.text` is a property, `.parsed` is a field,
  `model_dump(mode="json")` yields the full response (candidates, `usage_metadata`) for
  verbatim storage. It also carries `sdk_http_response`, which holds `headers` and `body`:
  that field is excluded from what gets stored, because nothing in this project writes
  transport headers into a database it never inspects.
- **Azure's IPA is rhotic and length-mark-free.** Both committed fixtures use
  `ɝ ɚ ɹ ɔɹ ɪɹ oʊ eɪ æ ɛ ɪ ɔ ʌ ə θ ð …` — 28 distinct symbols — never `iː ɑː ɜː`. The seed
  list in the request is British-style transcription; the table must key on **Azure's**
  symbols or every lookup silently misses. (`tests/test_render.py:160` already uses `ɔː`,
  which is why an alias map is not optional.)

## Verify first, before any code (step 0)

1. **The Gemini model ID.** `client.models.list()` — free, not a generate call — inside the
   project image with the real `.env`, asserting `models/gemini-3.6-flash` is present. This
   was denied by permissions during planning. If it is gone, take the successor the list
   offers and update the `GEMINI_MODEL` default in `utils.py` and the note in
   `memory-bank/techContext.md`.
2. Copy this plan to `plans/2026-08-18_coaching-layer.md` and append a `planned` row to
   `memory-bank/history.md` **before** writing code.

## Decisions taken with the user

- **Fallback first, Gemini on click.** Every assessment renders the deterministic report
  immediately and for free. An "Improve this with Gemini" button spends one call and
  replaces it in place. No assessment, and no retry of the same drill, burns free-tier RPD
  unasked.
- **The three pure helpers move to `speech_analyzer.py`** — `phoneme_pairs`, `is_flagged`,
  `delivery_summary`. They read the normalised shape that module produces, they are
  Streamlit-free, and the coach cannot import `app.py`. One definition of "what you actually
  produced", so the word card and the coaching report can never disagree.
- **No minimal-pair playback in this chunk.** Hearing contrasts is the perception trainer
  that `phoneme_reference` is being written to feed later.

## Files

### 1. `phoneme_reference.py` (new) — static IPA data, keyed by phoneme

Two levels, because articulation belongs to a sound and a minimal pair belongs to a
*contrast*:

```python
@dataclass(frozen=True)
class Contrast:
    produced: str                     # what came out, an Azure symbol
    why_it_matters: str               # what a listener hears instead
    minimal_pairs: tuple[tuple[str, str], ...]

@dataclass(frozen=True)
class Phoneme:
    symbol: str                       # exactly as Azure emits it
    label: str                        # "voiceless th"
    kind: str                         # consonant | vowel | diphthong | r-coloured
    articulation: str                 # tongue / lip / airflow, concrete
    examples: tuple[str, ...]
    contrasts: Mapping[str, Contrast] # produced symbol -> contrast
```

Written for three consumers at once: the AI coach reads `articulation` and
`minimal_pairs`; a perception trainer reads `contrasts` alone; the later accent feature adds
`formants` to `Phoneme` and `bridging_drill` to `Contrast` without restructuring either.

Public API:

- `normalise(symbol) -> str` — alias map, so a British-style or ASCII symbol still resolves:
  strip `ː`, `ɜ`→`ɝ`, `ɡ`→`g`, `r`→`ɹ`, `iː`→`i`, `ɑː`→`ɑ`, `ɔː`→`ɔ`, `uː`→`u`.
- `lookup(expected) -> Phoneme | None`
- `contrast(expected, produced) -> Contrast | None`
- `articulation_for(expected, produced) -> str` — the phoneme's articulation when known,
  else the constant `NO_NOTE` ("No articulation note for this pair yet — the substitution
  itself is still what to drill."). **A missing entry never produces a wrong note.**
- `minimal_pairs(expected, produced) -> list[tuple[str, str]]` — `[]` when unknown.

Coverage: the full en-US inventory — consonants `p b t d k g tʃ dʒ f v θ ð s z ʃ ʒ h m n ŋ
l ɹ w j`, vowels `i ɪ ɛ æ ə ʌ ɑ ɔ ʊ u ɝ ɚ`, diphthongs `eɪ aɪ ɔɪ oʊ aʊ`, r-coloured
`ɑɹ ɔɹ ɛɹ ɪɹ ʊɹ`. Seeded in the requested order (`/θ/ /ð/ /v/ /w/ /æ/ /ɛ/ /ɪ/ /i/ /ɑ/ /ʌ/
/ɝ/ /ə/`, dental vs alveolar `/t/ /d/`, dark `/l/`, `/z/` vs `/dʒ/`, `/ʃ/` vs `/s/`, `/f/`
vs `/p/`, final clusters). Entries that never fire cost a few lines of static data.

### 2. `fallback_coach.py` (new) — the report shape *and* the deterministic coach

The pydantic models live here, not in `ai_coach.py`: the fallback is the primary path and
`ai_coach` imports it anyway for the fall-through, so this keeps the SDK out of the free
path. Exactly the required schema, no `Optional` and no defaults, so `t_schema` conversion
stays clean:

```python
class MinimalPair(BaseModel):   a: str; b: str
class PriorityFix(BaseModel):
    expected_phoneme: str; produced_phoneme: str; affected_words: list[str]
    why_it_matters: str; articulation: str; minimal_pairs: list[MinimalPair]
class StressAndRhythm(BaseModel): issues: list[str]; drill: str
class CoachingReport(BaseModel):
    overall_comment: str; priority_fixes: list[PriorityFix]
    stress_and_rhythm: StressAndRhythm; practice_plan: str
```

- `compact(assessment, mode) -> dict` — **shared** with `ai_coach`, so grouping exists once.
  Only flagged words (`speech_analyzer.is_flagged`), each with its substitutions from
  `phoneme_pairs` where the phoneme is `is_mispronounced`, its syllable scores, error type
  and source, plus the overall scores, `delivery_summary`, and the distinct
  `observed_pairs`. Raw Azure JSON for a paragraph is large, mostly noise, and measurably
  degrades output — a test asserts the compacted payload is a fraction of the raw fixture.
- `build(assessment, mode) -> CoachingReport` — group by `(expected, produced)`, rank by
  intelligibility impact (distinct affected words first, then mean score deficit, then the
  symbol, so ordering is deterministic and testable), take the top 3, fill articulation and
  minimal pairs from `phoneme_reference`. `stress_and_rhythm` comes from the delivery faults
  and the worst-scoring syllables; `practice_plan` is one 5-minute routine naming the actual
  words from this attempt. `overall_comment` is 2–3 sentences from the scores, no praise
  padding. No key, no network, no clock — same input, same bytes.
- **Flagged words with no phoneme-level substitution** — omissions, and words scored badly
  whose phonemes all sit above `PHONEME_RED` — carry no `(expected, produced)` pair and so
  can never become a `priority_fix` without inventing one. They are surfaced in
  `overall_comment` and `practice_plan` instead. Without this an all-omission attempt
  returns an empty report; a test covers exactly that case.

### 3. `ai_coach.py` (new) — the model path, falling back on any failure

- `client = genai.Client(api_key=utils.require("GEMINI_API_KEY"))`, then
  `client.models.generate_content(model=utils.get("GEMINI_MODEL"), contents=…,
  config=types.GenerateContentConfig(response_mime_type="application/json",
  response_schema=CoachingReport, system_instruction=…, temperature=0.2))` — **both** the
  mime type and the schema; the mime type alone does not guarantee shape. `max_output_tokens`
  is deliberately **not** set: the 450-word limit is a prompt constraint, and capping output
  on a thinking model truncates the JSON mid-object, which arrives as a parse failure and a
  silent fall-through rather than as an error anyone can read.
- A response whose `.text` is empty, or whose candidate stopped for any reason other than
  normal completion (`finish_reason`, `prompt_feedback`), is a failure and not a report: log
  the reason, fall through.
- Prompt: the compacted payload as JSON, plus `reference_text` and `recognised_text` inside
  explicit `<reference_text>` / `<recognised_text>` delimiters with an instruction to treat
  their contents as data and never as instructions. Both are user-supplied free text, so the
  delimiter tokens are stripped from the text before embedding. Constraints in the system
  instruction: name the substitution explicitly, never invent a phoneme absent from the
  data, skip anything above threshold, under 450 words.
- **Validation, not just prompting.** Drop any `priority_fix` whose `(expected, produced)`
  pair is not in `observed_pairs`; truncate to 3. If nothing usable survives while the
  fallback found fixes, treat it as a failure and fall through.
- Errors: `ServerError`/timeouts → `TransientError`, retried by `utils.retry_transient` with
  `MAX_COACH_ATTEMPTS = 3` — one call plus the two retries the brief allows; `ClientError`
  code 429 → terminal, no retry (a free-tier 429 is the day or the month, not a blip); any
  other `ClientError` → terminal. Every failure path logs `utils.redact(str(exc))` and
  returns the fallback report.
- `OFFLINE_MODE` refuses independently of the UI, exactly as `tts.synthesise` does, so no
  code path can turn the offline promise into a network call — and offline still gets a
  complete report.
- `coach(assessment, reference_text, mode) -> CoachingResult(report, source, raw)`.
  `source` is `"gemini"` or `"fallback"`; `raw` is
  `response.model_dump(mode="json", exclude={"sdk_http_response"})` on the model path (usage
  metadata included, so a token meter is a later re-parse) and the report dict on the
  fallback path. Stored verbatim through `db.attach_coaching`, so changing what
  the UI shows is a re-parse, never a re-spend. `report_from_raw(raw, source)` reads either
  back — `coach_source` is what disambiguates the two shapes.

### 4. `app.py` — render the structured report

- `run_assessment` returns the `attempt_id` alongside the `Assessment`; the session cache
  entry becomes `(assessment, reference_text, attempt_id)`.
- New `render_coaching` between `render_scores` and `render_diff`: the top three fixes as
  bordered containers with `/expected/ → /produced/` as the dominant heading, then affected
  words, why it matters, articulation, minimal pairs; then stress and rhythm, then the
  practice plan. Never a raw model text blob.
- A caption naming the coach: "Written from the Azure data alone (offline coach)" versus
  "Gemini". The button `✨ Improve this with Gemini` is disabled — with the reason shown —
  under `OFFLINE_MODE` or a missing `GEMINI_API_KEY`, and carries a caption saying what the
  click sends: the compacted analysis and the reference text, to Google, never the audio.
  README §"What leaves your machine" already warns that free-tier prompts may be used to
  improve Google's products; making the send a click rather than a side effect of every
  assessment is what turns that warning into a choice.
- The report is **resolved before it is rendered** — session cache, then the button, then the
  spend — and any failure message is returned to the caller and rendered outside the
  `st.columns` the button lives in, the lesson `play()` records in its docstring.
- A `coaching` session cache keyed by the attempt hash, checked **before** any spend, for
  the same reason the TTS cache is: Streamlit re-runs the script on every widget click.
  `db.attach_coaching` runs once per (attempt, source), not on every rerun.
- The "The coaching report is still to come." caption goes.
- No Gemini budget guard: the free tier returns 429 rather than billing, `budget.py` is
  Azure-shaped, and a token meter would need a schema change. Recorded in the memory bank as
  a deliberate omission, with the usage metadata already stored for a later re-parse.

## Order and commits

Conventional messages, one commit per landed chunk, on the current branch:

1. `feat: add en-US phoneme reference with articulation and minimal pairs` (+ tests)
2. `refactor: move the pure normalised-data readers into speech_analyzer` (+ test updates)
3. `feat: add the deterministic offline coach and the shared report schema` (+ tests)
4. `feat: add the Gemini coach with schema-enforced output and fallback` (+ tests)
5. `feat: render the coaching report` (+ tests)
6. `docs: record the coaching layer in the memory bank`

The docs commit is not only the memory bank. Four places currently state that coaching does
not exist and would become false the moment it does: `.env.example` (`GEMINI_API_KEY` and
`GEMINI_MODEL` are marked "not read yet"), `README.md` (the status paragraph, the "Not built
yet" list, the settings table row, and the "Once coaching lands" wording under *What leaves
your machine*), `app.py`'s module docstring, and `db.py`'s "unused until the coaching chunk
lands" comment on `attach_coaching`.

## Tests (offline, no keys, no network — `tests/`)

- `test_phoneme_reference.py` — **every phoneme symbol appearing in either committed fixture
  resolves** (the one coverage claim that is provable, not assumed); aliases resolve; an
  unknown pair returns `NO_NOTE` and `[]`; every entry has non-empty articulation; minimal
  pairs are genuine pairs (`a != b`).
- `test_fallback_coach.py` — against `tests/fixtures/sample_azure_response.json`: the
  `/θ/ → /s/` fix on "thursday" appears, at most 3 fixes, ranking is stable across runs, no
  phoneme absent from the payload, the result validates as `CoachingReport`, an attempt with
  nothing flagged still produces a usable report, and `compact` is a fraction of the raw
  payload's size.
- `test_ai_coach.py` — with a fake client injected: a valid response parses; an invented
  phoneme is dropped; a 429 falls back without retrying; 5xx retries then falls back;
  malformed JSON falls back; `OFFLINE_MODE` never constructs a client; the prompt contains
  both delimiter blocks and the data-not-instructions instruction; a delimiter typed into
  the reference text cannot close the block. Plus a public-API check that
  `types.GenerateContentConfig(response_schema=CoachingReport)` constructs.
- `report_from_raw` round-trips a stored payload back into a `CoachingReport` for **both**
  sources. That test is what makes "changing what the UI shows is a re-parse, not a
  re-spend" a property of the code rather than an intention.
- `tests/test_app.py` — the coaching section renders from the seeded cache, the coach-source
  note is visible, and the Gemini button is disabled offline.

## Verification

```bash
make test
```

Then, live and deliberate — the only spend in this chunk:

- `client.models.list()` — free — to confirm the model ID before anything else.
- **Exit criterion, offline path first:** run the app with `GEMINI_API_KEY` deliberately
  unset and confirm a complete, useful report renders with a visible note saying the offline
  coach wrote it.
- **Then two or three Gemini calls** (free tier, `$0`, one per click of "Improve this with
  Gemini") against the 12.8 s weather recording: confirm the schema is honoured, that the
  report names only substitutions Azure actually reported, that `gemini_raw_json` and
  `coach_source` land in SQLite, and that a repeat click is served from cache rather than
  re-spent.

## When it lands

Update `memory-bank/techContext.md` (the three new modules, the moved helpers, the verified
Gemini surfaces, the deliberate absence of a Gemini meter) and `progress.md` (what works,
next step). Move the history row to `implemented`. Bump `pyproject.toml` to `0.1.0`, tag
`v0.1.0`, push tags, `gh release create v0.1.0 --generate-notes`, link the milestone's
issues and close it.
