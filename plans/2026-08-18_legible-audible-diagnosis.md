# Make the diagnosis legible and audible

## Context

The brief's problem statement is: *"I can't hear the difference between my pronunciation and
a native speaker's, so I don't know what to practice."* Assessment now works end to end —
real Azure scores down to the phoneme, stored verbatim in SQLite — but the UI renders all of
it as a plain `st.dataframe`. The two core requirements that actually answer the brief are
**"show expected vs. actual sound for every flagged word"** and **"let me hear the correct
pronunciation next to my own recording"**, and both are presentation, not analysis. The data
for the first is already parsed and sitting in `assessment.words`; the second needs only
Azure neural TTS, whose meter (`budget.preflight_tts`) and table (`db.tts_usage`) were
already built for it and are currently dead code.

This is the chunk agreed in `memory-bank/progress.md` on 2026-08-18: master plan §11's UI
plus §6's "Hear it" playback, as **one** chunk. They are inseparable because a "Hear it"
button lives *next to* each flagged word — splitting them means building the same render
path twice.

**Explicitly out, on the user's standing instruction:** `ai_coach.py`, `fallback_coach.py`,
`phoneme_reference.py` — the whole coaching layer. `attach_coaching` and the
`gemini_raw_json` column already exist, so deferring it costs no migration. Mode C
(unscripted) is also out.

### Decisions taken with the user before planning

| Question | Decision |
| --- | --- |
| "Hear it" under `OFFLINE_MODE` | Buttons render **disabled** with a caption saying why. `OFFLINE_MODE` means *no network call, ever* — that contract stays absolute. |
| Voice selection | `AZURE_TTS_VOICE` env var only (defaults to `en-US-BrianNeural`). No picker. |
| Slow playback | **In scope.** Each flagged word gets "Hear it" and "Hear it slowly" (SSML `prosody rate="-35%"`). |

### Verified against the installed SDK, not recalled

Run in the project image (`azure-cognitiveservices-speech` 1.51.1, streamlit 1.61.1):

- `SpeechSynthesizer.__init__(speech_config, audio_config=<AudioOutputConfig object>, …)` —
  **`audio_config` defaults to a default-speaker output object, not `None`.** Omitting it
  makes the container synthesise to a sound device that does not exist; `audio_data` never
  comes back. This is the TTS equivalent of the `apply_to` trap and must be commented as such.
- `SpeechSynthesisResult` exposes `audio_data`, `audio_duration`, `cancellation_details`,
  `reason`. `ResultReason.SynthesizingAudioCompleted` is the success value.
- `SpeechSynthesisCancellationDetails` has `error_code` / `error_details` / `reason` — the
  *same* shape the existing `speech_analyzer._classify_cancellation` already handles.
- `SpeechConfig.speech_synthesis_voice_name` and `set_speech_synthesis_output_format` exist;
  `SpeechSynthesisOutputFormat.Riff24Khz16BitMonoPcm` is available.
- `st.audio(data, format="audio/wav", …, autoplay=False)` — `autoplay` exists in 1.61.1, so a
  click on "Hear it" can play immediately rather than needing a second click on the player.

---

## Implementation

### 1. `tts.py` — new module

```python
synthesise(text: str, *, voice: str | None = None, slow: bool = False) -> Synthesis
```

- `@dataclass Synthesis: audio: bytes, characters: int, voice: str, attempts: int` —
  mirrors `speech_analyzer.Assessment`, which also reports `attempts` so the meter can
  charge per attempt rather than per success.
- Refuses empty text, and refuses outright when `utils.offline_mode()` — the UI disables the
  button, this is defence in depth so no code path can sneak a network call into the
  zero-cost mode.
- Sets `speech_synthesis_voice_name` and `Riff24Khz16BitMonoPcm` (WAV out, so `st.audio`'s
  default `format="audio/wav"` is correct and there is no ffmpeg round trip).
- **Passes `audio_config=None` explicitly**, with the comment explaining the default-speaker
  trap above.
- Normal speed → `speak_text_async(text).get()`; slow → `speak_ssml_async(ssml).get()`.
- Wrapped in `utils.retry_transient(...)` with `on_attempt=` counting, exactly as
  `speech_analyzer.recognise` does — a retry re-sends the text and can consume allowance
  even when it ultimately fails.
- `characters` bills the **full payload string sent to Azure**, SSML markup included. If
  Azure excludes markup from billing this over-counts; over-counting is the correct
  direction for a guard, and `budget.py` already establishes "always round the estimate up".
  State the uncertainty in the docstring rather than asserting Azure's rule.

```python
slow_ssml(text: str, voice: str, rate: str = SLOW_RATE) -> str
```

Pure function, separated so a test can assert its structure without the SDK. Escapes the
text with `xml.sax.saxutils.escape` — the reference text is user input, and an unescaped
`&` or `<` makes Azure reject the whole request.

### 2. `speech_analyzer.py` — promote one helper, no behaviour change

`_classify_cancellation` → **`classify_cancellation(details, *, bad_request_hint: str)`**.
Synthesis cancellations carry the identical attribute shape and the same
`CancellationErrorCode` enum, so `tts.py` reuses it rather than duplicating ~40 lines of
error mapping. Keeping one classifier also keeps `QuotaExhausted` a single type, so the
existing `is_quota_exhausted()` → `budget.mark_quota_exhausted()` path in `app.py` works for
TTS 403s too. The `bad_request_hint` parameter exists because the current BadRequest message
("check the reference text and the audio format") is STT-specific; for TTS a BadRequest
means bad SSML or an unknown voice name. Two call sites to update, both in
`speech_analyzer.py` (`_assess_single_shot` :211 and the continuous `on_canceled` :242) —
behaviour is unchanged, so the existing error-mapping tests should pass untouched.

### 3. `utils.py` — banding beside the thresholds

Add a `Band` enum (`RED` / `AMBER` / `GREEN` / `NONE`) and `score_band(score, red, amber)`,
plus `word_band(word)` and `phoneme_band(phoneme)` wrappers. These live next to
`WORD_RED` / `WORD_AMBER` / `PHONEME_RED` / `PHONEME_AMBER` so there is still exactly one
place threshold logic exists. Hex colours stay in `app.py` — they are presentation.

### 4. `app.py` — the render layer (the bulk of the work)

Pure helpers first, so the rendering logic is testable without a Streamlit runtime — the
precedent is the existing `cache_fetch` / `cache_store`:

| Helper | Does |
| --- | --- |
| `colour_coded_html(words)` | One `<span>` per word, `title=` carrying the score for hover (master plan §11). Theme-safe styling: coloured text + `border-bottom`, **no** hardcoded background, so it survives Streamlit's light and dark themes. Rendered via `st.markdown(unsafe_allow_html=True)` — HTML is required because native `:red[…]` markdown cannot carry a hover title. `html.escape(..., quote=True)` on **both** the word text and the title attribute: the words come from the reference text, which is arbitrary user input being interpolated into markup. |
| `reference_vs_heard(reference_text, recognised_text)` | Inline diff via `difflib.SequenceMatcher` over `utils.normalise_words` (reused, not reimplemented). Marks omitted words struck through and extra words underlined. |
| `phoneme_pairs(word)` | `[(expected_ipa, produced_ipa, score)]` from the already-parsed `nbest` field. The existing `_weakest_phoneme` is reimplemented on top of this for the one-line card header. |
| `delivery_summary(words)` | Aggregates `w["delivery_error_types"]` into `{UnexpectedBreak: [words…], MissingBreak: […], Monotone: […]}`. |
| `severity_key(word)` | Sort order for flagged words — worst first. |

`render_result` becomes, in order: metric row (unchanged) → colour-coded reference text with
a legend and the "these thresholds are heuristics, not Azure-defined" caption → the
reference-vs-heard diff (replacing the current plain "What Azure heard" write) →
**whole-text "Hear it" / "Hear it slowly" for the full reference text** (master plan §6 asks
for playback per flagged word *and* for the full paragraph) → one bordered card per flagged
word, worst first → the delivery panel → the user's own recording, so the native rendering
and their own are back to back without leaving the page.

Each word card carries: the word and its score, the error type and `error_source` (so a
locally-diffed omission is never presented as Azure's judgement), the expected → produced
IPA rows coloured against the phoneme thresholds, the syllable/stress line (master plan §4:
misplaced lexical stress is invisible at the phoneme level), and the two playback buttons.
Note in the UI that an isolated word is synthesised in citation form — that is the right
model for a drill, but it is not how the word sounds inside the sentence, which is what the
whole-text playback is for.

`severity_key` orders Omissions first (they have `accuracy: None` — a word never spoken is a
worse outcome than a badly scored one, and `None` must not sort as zero or crash the sort),
then ascending accuracy, then delivery-only flags.

**Replaces** the current `st.dataframe` of flagged words and the "still to come" caption.

### 5. `app.py` — TTS orchestration and the metering trap

`app.py` orchestrates; `tts.py` makes no spend decisions. This matches `run_assessment`,
where `budget.preflight_stt` and `db.record_attempt` bracket the `speech_analyzer` call.

```
play(conn, text, *, slow):
    key = (voice, text, slow)
    audio = tts_cache_get(key)
    if audio is None:                      # <-- ORDER IS THE POINT
        budget.preflight_tts(conn, characters)   # BudgetError -> st.error, return
        result = tts.synthesise(text, slow=slow) # errors -> st.error, return
        db.record_tts_usage(conn, characters=result.characters * max(result.attempts, 1), ...)
        tts_cache_put(key, result.audio)
    st.session_state["now_playing"] = key
```

**The cache lookup must precede the preflight and the record.** Streamlit re-runs the entire
script on every widget interaction, so metering before the cache check would re-charge the
TTS meter on every unrelated click — the meter would inflate continuously while nothing was
synthesised. This is the same reasoning as `CACHE_LIMIT` on the assessment cache and needs
the same explicit comment.

A single `st.audio(..., autoplay=True)` renders whatever `now_playing` points at, because
the click itself is lost on the rerun. `now_playing` is cleared when a new assessment lands,
so a fresh result never opens with the previous attempt's word still queued in the player.

Generalise the existing `cache_fetch` / `cache_store` into `lru_get(cache, key)` /
`lru_put(cache, key, value, limit)` and use them for both caches, rather than writing a
second near-identical LRU for audio. Audio bytes are far larger than the assessment JSON, so
the TTS cache gets its own smaller limit. `tests/test_app.py`'s three existing cache tests
move to the new signature.

Offline: both buttons render with `disabled=True` and a caption naming `OFFLINE_MODE` as the
reason.

Every button in the flagged-word loop needs an explicit `key=` derived from the word's
**index**, not its text. A paragraph repeats words constantly ("the", "that"), and two
buttons sharing a key is a hard `StreamlitDuplicateElementKey` crash, not a cosmetic bug.

### Known approximation to state in the UI, not discover later

The colour-coded block is built from `assessment.words` (the aligned list that carries the
scores), not from the raw reference string — so the original punctuation and capitalisation
are not reproduced. The verbatim reference text stays visible in the diff panel directly
above it.

### 6. Tests — all offline, no keys

New `tests/test_tts.py`: `slow_ssml` escaping and structure; `synthesise` refuses empty text
and refuses under `OFFLINE_MODE`; retry/attempt counting and character counting, by
monkeypatching the module-level SDK-call seam in `tts.py`; `classify_cancellation` mapping
for the TTS BadRequest hint, driven by a stub details object carrying real
`CancellationErrorCode` members (no network needed).

New `tests/test_render.py`: `colour_coded_html` escapes `<` and bands against the thresholds;
`reference_vs_heard` marks an omission and an insertion; `delivery_summary` aggregates. The
captured fixture contains **no** `UnexpectedBreak` / `MissingBreak` / `Monotone` (see
`memory-bank/progress.md`), so the delivery test uses a hand-built payload explicitly marked
synthetic — the established pattern in `tests/test_merge.py`.

Extend `tests/test_app.py`: the Hear-it buttons are disabled offline and carry the
explanatory caption; the delivery panel and colour-coded block render; and a repeat click on
an already-synthesised phrase does **not** charge `db.monthly_tts_characters` twice.

Five existing tests in `tests/test_app.py` assert on surfaces this chunk replaces and must be
rewritten rather than left to rot — verified by reading them, not assumed:

- `test_the_result_shows_what_azure_heard` (:155) — the "What Azure heard" subheader becomes
  the diff panel.
- `test_flagged_words_are_listed` (:160) — indexes `app.dataframe[0]`, which silently becomes
  the *history* table once the word table is gone, so the assertion has to move to the cards.
- the three cache tests (:188, :203, :212) — moving to the generalised `lru_get` / `lru_put`.

The 108-test baseline is a verified count, not an estimate.

### 7. Documentation

`.env.example` (`AZURE_TTS_VOICE` is no longer "not read yet"), the README status paragraph,
and `memory-bank/` — `techContext.md` (tts.py exists, the `audio_config=None` trap as a
constraint), `progress.md` (what works / what is next), and a `history.md` row moved from
`planned` to `implemented`.

Per `CLAUDE.md`, the plan file is copied to `plans/2026-08-18_legible-audible-diagnosis.md`
and a `planned` row appended to `memory-bank/history.md` **before** any code is written.

---

## Verification

1. `make test` — the suite stays fully offline with no keys set (`tests/conftest.py` forces
   `OFFLINE_MODE` and clears the environment). 108 tests today; this chunk adds to that.
2. `make up` with `OFFLINE_MODE=true` — drive the whole diagnosis UI from the committed
   fixture at zero cost: colour-coded text, the diff, the word cards with expected → produced
   IPA, the delivery panel, and both Hear-it buttons correctly disabled with their caption.
3. **One online run, which needs input from the user:** the real `.env` (which I cannot read
   — it is gitignored and outside my access) and an audio file to assess. This is the only
   way to confirm real synthesised audio comes back and plays, that the WAV format is right
   for `st.audio`, and that `tts_usage` is charged once per distinct phrase rather than on
   every rerun. I will ask for both at that point, and say what it will spend before
   spending it — a handful of words against a 500,000-character monthly allowance.
4. `sqlite3 data/coach.db "select * from tts_usage"` — confirm one row per distinct
   `(voice, text, slow)`, not one per click.

## Commits, PR, review

Commit in chunks as work lands, per `CLAUDE.md` — plan file + history row, then `tts.py` +
its tests, then the render layer + its tests, then docs. No `Co-Authored-By` lines and no
watermarks in commits or the PR. Then open the PR against `main`, run `/code-review` over
the branch, and push fixes for anything it finds.
