# Mode C — unscripted assessment, two-pass diagnosis, and content scores

Target: milestone **v0.12.0**, closing **issue #12** in full.

## Context

Modes A and B measure reading aloud. Reading aloud is not the register this project exists
for: interviews, calls, explaining technical things. Mode C is the only mode that measures
generating language and monitoring pronunciation at the same time.

Two things had to be settled before this could be planned, and both now are.

### 1. The content-assessment route — verified by introspection, before this file existed

An earlier design routed content scoring through `enable_content_assessment_with_topic(topic)`.
**That method does not exist in `azure-cognitiveservices-speech` 1.51.1, and it was not
renamed — Microsoft retired the feature.** Established three ways, none of them from memory:

- Introspection of the installed package in `dialect-coach-app:local`:
  `dir(PronunciationAssessmentConfig)` returns exactly `apply_to`, `enable_prosody_assessment`,
  `nbest_phoneme_count`, `phoneme_alphabet`, `reference_text`, `to_json`.
  `PronunciationAssessmentResult` exposes no vocabulary/grammar/topic. `PropertyId` carries no
  content entry. A string scan of the native `.so` files finds only
  `referenceText`, `gradingSystem`, `granularity`, `enableMiscue`, `enableProsodyAssessment`,
  `phonemeAlphabet`, `nbestPhonemeCount` — no `contentAssessment`, no `contentTopic`.
- Microsoft Learn, verbatim: *"Content assessment (preview) is retired from Speech SDK versions
  1.46.0 and later."* Their stated replacement is a chat model given a published grading rubric
  that returns `{"vocabulary": 0-100, "grammar": 0-100, "topic": 0-100}`.
- **The JSON route still carries unknown keys.** `PronunciationAssessmentConfig(json_string=…)`
  round-trips `enableContentAssessment` and `contentTopic` back out of `to_json()` untouched
  (and appends `"dimension":"Comprehensive"` of its own). So the client can still *send* the
  retired fields; whether the service still *answers* them is only knowable from a live call.

**Decision:** one deliberate probe call sends the retired fields and records the answer as fact
in the memory bank, either as a live route or as a dead end. The standing implementation is
Gemini against Microsoft's own published rubric, labelled on the surface as Gemini-derived and
never as an Azure number. Issue #12 closes fully.

### 2. The budget guard — verified, not rebuilt

The spec's claim held on inspection, with one gap:

- `UNSCRIPTED_TWO_PASS=true` is in `.env.example`, `utils._DEFAULTS` (line 193), and
  `budget.passes_for()` returns 2 for `Mode.UNSCRIPTED` ([budget.py:77](src/budget.py:77)).
- `preflight_stt` multiplies `seconds * passes_for(mode)` into `billed_seconds` **before** the
  refusal, and appends `"(Mode C sends the audio twice)"` ([budget.py:167](src/budget.py:167)).
  `prepare_audio` calls it before anything is dispatched, so **the projected cost shown before
  pass 1 already covers both passes.** Nothing to write here — only to confirm against a real call.
- `tests/test_budget.py:132-139` covers both states of the flag.
- **The gap is post-hoc, not pre-flight.** `db.attempts.audio_seconds` is documented as "what the
  STT meter is charged for this attempt" and `db.monthly_stt_seconds` sums it, but
  [app.py:900](src/app.py:900) writes `seconds * max(assessment.attempts, 1)` — the *retry*
  count. A two-pass Mode C recording would be metered once while Azure was charged twice, and
  every later pre-flight would compute against an understated `meter.used`. The fix is not a new
  multiplier: **the two-pass runner returns a single `Assessment` whose `attempts` is the sum of
  both passes' attempts**, and the existing line is then already correct.
  (Pass 1 is plain STT with no prosody add-on, so `stt_cost_usd` over-charges it by the add-on
  rate. Left alone deliberately — the module already states that erring high is the correct
  direction for a guard.)

### 3. The register confound

Spontaneous speech is a systematically different population from read speech, not read speech
under harder conditions: vowels centralise, durations shorten, unstressed syllables collapse
further toward schwa. Every one of those is something v0.10.0 measures. The infrastructure to
keep them apart already exists and is mostly unused — `app.style_for()`
([app.py:770](src/app.py:770)) already tags every attempt `read`/`spontaneous`,
`speaker_baseline.style_tag` and `vowel_measurements.style_tag` are columns from day one, and
`vowel_measure.STYLE_MISMATCH` already exists as a *warning*. This chunk turns that warning into
a refusal and makes the baseline store per-style.

---

## Decisions taken (from the clarifying round)

| Question | Decision |
| --- | --- |
| Content scores | Probe the retired Azure fields **once**, record the answer; standing route is Gemini on Microsoft's rubric |
| Spontaneous baseline | Two Mode C recordings on the **same prompt**, ≥ `CALIBRATION_GAP_MINUTES` apart; floor labelled as including content variation and therefore an upper bound |
| Per-vowel token floor | Mode-aware: A/B keep `minimum=1` against a stored baseline; Mode C uses `MIN_TOKENS_PER_CATEGORY` (3) |
| Miscue / completeness | Mode C reports **no completeness**, runs **no `_diff_miscue`**, and keeps repetition detection via a new adjacency path |

---

## Work

### A. Two-pass recognition — `src/speech_analyzer.py`

Today `recognise()` raises `AssessmentError` for `Mode.UNSCRIPTED`
([speech_analyzer.py:385](src/speech_analyzer.py:385)). Replace that branch with a two-pass runner.

- `transcribe(wav_path, cancel_event, on_attempt) -> tuple[str, int]` — **pass 1**. A plain
  `SpeechRecognizer` with **no** `pron_config.apply_to()`, continuous, reusing the existing
  callback/timeout/cancellation scaffolding of `_assess_continuous` (factor the shared session
  loop out rather than copying it). Returns the joined `DisplayText` and the attempts made.
  This is the accurate transcript Microsoft's own note recommends, because the unscripted
  assessment model is weaker than standard STT and a phoneme diagnosis against a wrong
  transcript confidently blames the wrong sounds.
- **Pass 2** is `_assess_continuous(wav_path, transcript)` — an ordinary *scripted* assessment
  whose reference text is pass 1's transcript.
- `assessment_config_json` gains a `Mode.UNSCRIPTED` shape: `enableMiscue: False`, prosody on,
  IPA, nbest 5 — and, only when `UNSCRIPTED_CONTENT_PROBE=true`, the retired
  `enableContentAssessment` / `contentTopic` keys. Default **false**, so the standing path is
  clean and the probe is a deliberate act.
- When `UNSCRIPTED_TWO_PASS=false`, run a **single** unscripted pass (empty `referenceText`) and
  say on the surface that the transcript is from the weaker model.
- `Assessment.attempts` = pass 1 attempts + pass 2 attempts. This is what makes the meter right
  (see Context §2) and it is the one number to get right in this file.
- Carry the pass-1 transcript on `Assessment` (new field, e.g. `stt_transcript`) so the surface
  can show what pass 2 was scored against, and so the content scorer has its text.

**Normalisation** (`normalise`): for `Mode.UNSCRIPTED`, skip `_diff_miscue` entirely and set
`completeness` to `None` — Azure's own unscripted results table has no completeness score and
there is nothing to compare against. `_merge_overall`'s reference-words branch must not fire.

**Repetitions**: `_mark_repetitions` currently requires one of the pair to carry `Insertion`
([speech_analyzer.py:668](src/speech_analyzer.py:668)), which no longer happens without the
diff. Add an adjacency path used for Mode C: two neighbouring words whose normalised tokens are
equal are both marked `REPETITION`, no Insertion label needed. Free speech is where stumbles
actually happen, and the existing suppression in `weakest_phoneme` and
`fallback_coach._substitutions` then does the rest unchanged.

**Timeouts**: `CONTINUOUS_TIMEOUT_SECONDS` is 300 s and Mode C's ceiling is 300 s of audio, run
twice. Give Mode C its own, more generous backstop rather than sharing the paragraph one.

**Offline**: add a `Mode.UNSCRIPTED` entry to `FIXTURES`, captured from the first real run and
committed (payload only — no audio).

### B. Content scores — new `src/content_score.py`

A sibling of `ai_coach.py`, same shape and the same rules: lazy `google.genai` import,
`response_mime_type` **and** `response_schema` both set, a pydantic model
(`ContentScores{vocabulary, grammar, topic, notes}`), user text wrapped as delimited data with
the delimiters stripped, and a system instruction saying the transcript is material to analyse
and never instructions. The rubric is Microsoft's published one, quoted in the module docstring
as the provenance of the numbers.

Every unavailability is **explicit, with its reason** — never a blank, and never a
scripted-mode number in its place:

| Condition | Rendered as |
| --- | --- |
| `OFFLINE_MODE` | "unavailable — offline" |
| No `GEMINI_API_KEY` | "unavailable — no Gemini key" |
| 429 / call failed | "unavailable — Gemini returned 429 (free-tier allowance)" |
| Transcript under ~50 words / fewer than 3 sentences | "unavailable — Azure's own guidance is 15 s (50+ words) minimum, and topic scoring needs at least three sentences" |
| Probe returned Azure content scores | rendered as Azure's, sourced accordingly |

Reuse `ai_coach._classify`, `_is_transport_failure` and `_as_data` rather than re-deriving them;
lift them to shared helpers if that reads better than importing across.

Storage: a `content_score_json` column on `attempts`, created `IF NOT EXISTS`-style like the
v1 coaching columns (additive, `user_version` unmoved), holding the verbatim response plus its
source (`gemini` | `azure` | `unavailable:<reason>`).

### C. Surface — `src/app.py`

- `MODE_LABELS` gains `"Unscripted — speak freely on a prompt": Mode.UNSCRIPTED`, and the
  "not built yet" caption at [app.py:3948](src/app.py:3948) goes.
- Mode C branch in `render_practice`: a **topic/prompt** picker plus a free-text topic box
  instead of the reference textarea. Ship a `PROMPTS` set aimed at the register the project is
  about — an interview answer, a call, explaining something technical. `validate_reference` is
  not called (there is no reference); a topic-specific validator checks the topic is non-empty
  and warns below the 15 s / 50-word guidance. Feed the topic into `utils.attempt_hash` in the
  reference-text slot so two topics never share a cache entry.
- Live guidance under the recorder: Azure's 15 s–10 min band, the 3–4 minute target, and — when
  `UNSCRIPTED_TWO_PASS` is on — that this recording is sent twice and the pre-flight figure
  already covers both.
- `render_scores`: for Mode C, Completeness renders as "—  not applicable to unscripted speech"
  rather than an empty bar. Add a **Content score** panel below the pronunciation breakdown:
  Vocabulary / Grammar / Topic as banded bars through the existing `_score_bar_html` and
  `AzureBand`, matching the 0-59/60-79/80-89/90-100 convention #12 asks for, with the headline
  Content figure stated as the **plain mean of the three** and captioned as such (Azure's own
  weighting is unpublished, so it is not reconstructed).
- Show the pass-1 transcript above the diff, captioned as what pass 2 was scored against.
- `render_error_counts`: the Mispronunciation badge stays; the break/monotone badges are
  unchanged. No badge invents a completeness figure.

### D. Register separation — `src/db.py`, `src/vowel_measure.py`, `src/app.py`

This is the scientific half and the part most worth getting exactly right.

1. **Baselines become per-style.** `db.save_baseline` currently supersedes *every* current row
   ([db.py:825](src/db.py:825)), so a spontaneous baseline would retire the read one. Scope the
   `UPDATE` to `WHERE superseded_at IS NULL AND style_tag = ?`, and give
   `db.current_baseline(conn, *, style)` a required style. `app.baseline_context(conn, style)`
   follows. Two current baselines — one per style — is the correct state, and neither is ever
   averaged into the other.
2. **`plot_gate` refuses on a style mismatch instead of warning.** `STYLE_MISMATCH`
   ([vowel_measure.py:1842](src/vowel_measure.py:1842)) becomes a `reason`, not a
   `style_warning`, and `PlotGate.ok` goes False. A spontaneous read is normalised against a
   spontaneous baseline or it is not normalised at all. Until one exists, the Mode C accent
   surfaces say exactly that — including that establishing one is partly the job of the first
   Mode C session.
   *(Keep `style_warning` on the dataclass only if something still needs a non-fatal note;
   otherwise delete it so there is one path, not two.)*
3. **`calibrate` takes the real style.** It defaults `style="read"`
   ([vowel_measure.py:2381](src/vowel_measure.py:2381)) and `app.build_baseline` never passes
   it, so a spontaneous calibration would be stored mislabelled. Thread it through, and refuse
   outright if the two calibration attempts carry different style tags.
4. **A spontaneous calibration flow** beside `render_calibration`: two Mode C recordings on the
   **same prompt**, ≥ `CALIBRATION_GAP_MINUTES` apart. `calibration_reads` currently filters on
   `progress_view.is_benchmark(reference_text)` ([app.py:1866](src/app.py:1866)); the
   spontaneous selector filters on the `spontaneous` tag and equality of topic instead. The
   stored noise floor is captioned as **including content variation and therefore an upper
   bound** — wider than the read floor, which is the conservative direction for a guard.
5. **Mode-aware token floor.** `findings_by_instrument`'s `minimum`
   ([vowel_measure.py:1764](src/vowel_measure.py:1764)) already does the refusing via
   `positions(..., minimum=...)`; the change is what `render_accent_table` passes. A/B against a
   stored baseline keep `minimum=1` (a drill token is a deliberate probe, and v0.11.0's
   measure-drill-remeasure loop depends on it). Mode C passes `MIN_TOKENS_PER_CATEGORY`. Free
   speech does not sample the vowel space evenly — token counts per vowel will be wildly uneven
   and some vowels will have none — so a lone accidental token is refused rather than plotted as
   a confident dot.
6. **The style tag joins the token count in the four-column table.** `_with_count`
   ([vowel_measure.py:1305](src/vowel_measure.py:1305), 9 call sites) becomes
   `_with_count(text, count, style)` rendering `(n=14, spontaneous)`; `findings_by_instrument`
   threads `measurement.style` down. The columns and their order are unchanged — this is the one
   addition the v0.10.0 contract takes, because in this mode the reader cannot otherwise tell
   which population the number came from. Existing assertions on `(n=14)` in
   `tests/test_accent.py:36,45` and `tests/test_accent_gating.py:94` update with it.
7. **Progress view.** `progress_view.MODE_LABELS` already has a Mode C entry and free practice is
   drawn as unconnected points shaped by mode, so no line is pooled. **One real defect Mode C
   exposes:** the shape range is positional —
   `range=["triangle-up","circle","square"][: len(modes)]` — so with paragraph absent, Mode C
   silently inherits paragraph's circle. Key shapes to the mode instead of to list position.

### E. Config — `.env.example`, `src/utils.py`

- Drop the "Not read yet — Mode C is not implemented" comment above `UNSCRIPTED_TWO_PASS`.
- Add `UNSCRIPTED_CONTENT_PROBE=false` with a comment saying what it sends, that Azure retired
  the feature at SDK 1.46.0, and that turning it on is the decision to spend one call finding
  out whether the service still answers.
- `MAX_DURATION_SECONDS_UNSCRIPTED=300` is already correct for a 3–4 minute target; leave it.
  Note in `techContext.md` that a real `.env` written earlier may not carry it, the same trap
  already recorded for the 180 s paragraph ceiling.

## Files

`src/speech_analyzer.py`, `src/app.py`, `src/vowel_measure.py`, `src/db.py`,
`src/progress_view.py`, `src/utils.py`, new `src/content_score.py`, `.env.example`.
Tests: `tests/test_parsing.py` (two-pass, no-diff, repetition adjacency), new
`tests/test_content_score.py`, `tests/test_accent_gating.py` (style refusal, mode-aware floor),
`tests/test_accent.py` (style tag in the table), `tests/test_budget.py` (combined `attempts`
reaches the meter), `tests/test_app.py` (Mode C surface, content panel, unavailable states),
new `tests/fixtures/sample_azure_unscripted.json`.

## Verification

Offline first, then a budgeted live session.

1. `make check` — lint, mypy, full suite, all offline via `tests/conftest.py`.
2. **Offline surface pass**: `OFFLINE_MODE=true` with the committed Mode C fixture. Confirm the
   mode appears, the prompt picker replaces the textarea, completeness reads "not applicable",
   and the content panel renders "unavailable — offline" rather than a blank.
3. **Live, budgeted.** ~7 minutes of the 18,000 monthly seconds per hour of testing, doubled by
   the second pass. Planned spend for this chunk: one ~3.5-min Mode C run (≈420 billed s), the
   content probe on a ~20 s clip (≈40 s), and a spontaneous calibration pair (≈840 s) — roughly
   1,300 s, under 8% of the month.
   - **The budget assertion the spec asks for**: before pass 1, record the pre-flight figure and
     the meter's `used`; after the run, confirm `attempts.audio_seconds` for that row is ≈ 2 ×
     clip duration and that `budget.summary_line` moved by that amount. That is the end-to-end
     proof that the cost shown before the first pass covered both passes.
   - Confirm the pass-1 transcript is visibly better than an unscripted-model transcript
     (compare against a `UNSCRIPTED_TWO_PASS=false` run if quota allows), and that the phoneme
     diagnosis is against the pass-1 text.
   - Run the probe once with `UNSCRIPTED_CONTENT_PROBE=true`; record in `memory-bank/progress.md`
     under *Dead ends* or *What works* depending on the answer, either way as a verified fact.
   - Confirm the Mode C accent surface **refuses** while only a read baseline exists, then build
     the spontaneous baseline from the pair and confirm it plots with `(n=…, spontaneous)` and
     that the read baseline is still current for Mode B.
4. Drive it in the browser via the Playwright MCP tools. **Do not reload a live Streamlit session
   mid-check** — a full navigate wipes session-local state silently.
5. Commit in chunks as each lands (recognition, content score, surface, register separation),
   per the repo rule.

## Closing out

- `memory-bank/progress.md`: the retirement of Azure content assessment (with the SDK version and
  how it was established), the probe's answer, the two-pass meter wiring, and the per-style
  baseline rule. Verified facts written directly; judgement calls proposed first.
- `memory-bank/techContext.md`: the two-pass flow and the per-style baseline as architecture.
- `memory-bank/history.md`: the plan row moves `planned` → `implemented`.
- Close issue #12, close milestone v0.12.0, record the milestone in the memory bank.

## Stated risk

`referenceText` has **no documented length limit** — Microsoft's docs and quota pages do not
publish one, checked rather than assumed. A 3–4 minute pass-1 transcript is ~500–600 words
(~3,500 chars), against the ~200-word benchmark paragraph that works today. If pass 2 comes back
`BadRequest`, the failure is already classified by `classify_cancellation` as a permanent error;
Mode C then falls back to the single unscripted pass with the reason stated on the surface, and
the recommended recording length is capped in `.env.example`. This is a first-live-call unknown,
not a blocker.
