# Surface the scores and metrics — milestone v0.3.0

## Context

Milestone v0.3.0 bundles four GitHub issues around making Azure's scores and error counts
visible on screen: #11 (score breakdown), #13 (per-word breakdown layout), #10
(mispronunciations/breaks/monotone tracking), and the pronunciation half of #12 (banded
pron/accuracy/fluency/prosody scores + mispronunciation counts — the content-score half of
#12 is vocabulary/grammar/topic, which only unscripted assessment returns, so it cannot be
built in this milestone at all).

This is pure render-layer work: every number involved already exists in
`assessment.overall_scores` or `assessment.words` (produced by `speech_analyzer.normalise`),
and the committed drill fixture (`tests/fixtures/sample_azure_response.json`) carries real
`Mispronunciation` words to render against. No Azure/Gemini calls, no schema changes to the
normalised shape, no new API cost.

Verified directly against the four issue images (fetched via `gh issue view`) and the
current code before writing this plan:

- **#10 is already three-quarters built.** `render_delivery` in `app.py` already aggregates
  `UnexpectedBreak`/`MissingBreak`/`Monotone` into counts + the specific words, via
  `speech_analyzer.delivery_summary`. The only thing #10 asks for that is genuinely missing
  is a **mispronunciation count**, surfaced nowhere on screen before this chunk.
- **#11 and the pronunciation half of #12 are the same ask.** Both issue images show the
  identical "Score breakdown" widget shape (label, `N / 100`, a coloured bar) — issue 11's
  screenshot happens to be of the *content*-score breakdown (Vocabulary/Grammar/Topic),
  which is out of scope, but the *component* it asks for is exactly what #12 asks for
  applied to Accuracy/Fluency/Prosody, which scripted assessment does return today.
- **#13's image is a hover tooltip on the existing colour-coded running text**, not the
  flagged-word cards further down the page: a word inside a sentence, coloured/struck the
  way `colour_coded_html` already renders words, with a tooltip on hover showing a
  `word : score` header, then the word's phonemes as one row and their scores as an aligned
  row underneath. The previous tooltip was a plain single-line `title=` attribute, which
  cannot lay out two aligned rows.
- Azure's own 4-band score convention (0-59 / 60-79 / 80-89 / 90-100) is distinct from this
  project's `WORD_RED`/`WORD_AMBER`/`PHONEME_RED`/`PHONEME_AMBER` heuristics in `utils.py`,
  and CLAUDE.md is explicit that the two must not be merged.

## Approach taken

1. **`utils.py`** — added `AzureBand` and `azure_score_band()`, cut at 60/80/90, with a
   comment distinguishing them from the `WORD_RED`/`WORD_AMBER`/`PHONEME_RED`/`PHONEME_AMBER`
   heuristics directly above. Banding stays presentation-only: `overall_scores` keeps its
   raw floats; bands are computed at render time only.
2. **`speech_analyzer.py`** — added `mispronounced_words()`, a pure reader mirroring
   `delivery_summary`, returning words with `error_type == "Mispronunciation"`.
3. **`app.py` — `render_scores`** rewritten: a colour-banded Pronunciation headline number
   plus Completeness as a plain metric, then a "Score breakdown" section with
   Accuracy/Fluency/Prosody as banded bars. A `None` score renders "—" and an unfilled bar,
   never `0 / 100`.
4. **`app.py` — new `render_error_counts`**: four count badges (Mispronunciations,
   Unexpected break, Missing break, Monotone), sourced from `mispronounced_words` and
   `delivery_summary`, placed right after the score breakdown. Count-only — the existing
   `render_delivery` panel (untouched) still gives the per-word detail for the three
   delivery faults, and the flagged-word cards still give it for mispronunciations, so this
   does not duplicate that detail.
5. **`app.py` — `colour_coded_html`**: the old `title=` attribute (`hover_text`) is replaced
   with a real CSS `:hover` tooltip (`word_tooltip_html`), built from
   `speech_analyzer.phoneme_pairs` — the same reader `render_word_card` already uses, so the
   two views can never disagree about a phoneme's score. The tooltip needs an opaque
   background to be legible, unlike the inline word colours elsewhere (kept as text/border
   colours so they need no background). Checked live in the browser: Streamlit 1.61.1 does
   not expose its theme as CSS custom properties anywhere in the DOM
   (`getComputedStyle(...).getPropertyValue('--text-color')` returns `""` on `body`,
   `.stApp`, and every Streamlit container tried), so the tooltip deliberately uses one fixed
   light card rather than chasing a variable that would never resolve — verified to read on
   both the light and dark Streamlit themes.

## Out of scope, explicitly

- Content score (Vocabulary/Grammar/Topic) — not returned by scripted assessment at all.
  Deferred to v0.12.0 (to be recorded on GitHub issue #12, pending the user's go-ahead).
- Any change to `speech_analyzer.normalise`'s output shape, `db.py`, or anything that
  touches Azure/Gemini.

## Verification

- `make test`: 311 passed (up from 293), all offline, no keys, no network. New/updated
  coverage: `azure_score_band` cut points including `None`; `mispronounced_words` against a
  synthetic word list; the tooltip builder (phoneme rows, escaping, "not spoken", "—" for a
  missing phoneme score); the new score-breakdown/error-count shape in `test_app.py`,
  including a case proving a missing pron/accuracy/fluency/prosody score renders "—" and
  never `0`.
- Live in the browser (`OFFLINE_MODE=true`, real drill fixture — 3 real `Mispronunciation`
  words, `pron_score` 83.0/accuracy 89.0/fluency 88.0/prosody 76.4): confirmed the score
  breakdown bars, the banded headline colour (green for 83/89/88 = "good", amber for 76.4 =
  "fair"), and the "3 Mispronunciations" badge all render and match the issue images, on
  both the light and dark Streamlit themes. The fixture carries no
  `UnexpectedBreak`/`MissingBreak`/`Monotone` (pre-existing, documented gap in
  `memory-bank/progress.md`), so those three badges were confirmed at 0 in the browser, with
  their non-zero behaviour covered by the `test_app.py`/`test_render.py` cases instead —
  consistent with how the rest of the delivery-fault code was already tested.
- Confirmed the new hover tooltip renders the `word : score` header, phoneme row, and score
  row (e.g. "rather : 97" over "ɹ æ ð ɚ" / "100 100 100 92") on hover, matching the issue-13
  image's layout, on both themes.
- A live (online, real-Azure) check was attempted but abandoned as not worth the effort for
  this chunk — the offline/fixture verification above, plus 311 passing tests, is the
  verification of record for this change.
