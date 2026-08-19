# Timing data through the parser, and nPVI

Milestone v0.6.0.

## Context

Azure's pronunciation-assessment payload carries `Offset` and `Duration` on every word,
syllable and phoneme, in 100-ns ticks, plus a top-level `SNR`. `speech_analyzer._normalise_word`
discards all of it — the normalised word keeps `word`, `accuracy`, `error_type`, `error_source`,
`delivery_error_types`, `prosody_detail`, `syllables` and `phonemes`, and no timing at all.

Every later accent measurement — rhythm, vowel-space drift, an F0 track, slicing the audio to
play a sound back — needs those numbers. Carrying them through is the de-risking step for all of
it. The chunk earns its place on its own, though: with phoneme durations in hand, **nPVI** (the
normalised Pairwise Variability Index over vowel durations) is computable today against the
committed fixture. nPVI measures how much successive vowel durations differ. Stress-timed English
varies a lot; syllable-timed languages vary much less, and carrying a syllable-timed rhythm into
English is one of the most recognisable prosodic markers of second-language speech.

The intended outcome is that a benchmark read produces a rhythm number, and that number is
compared against something the comparison can actually support.

Purely additive. No existing consumer reads a normalised word by position, no stored shape
changes, and `progress_view.parse_attempts` re-parses `azure_raw_json` through
`speech_analyzer.normalise` on every Progress render — so **every attempt already in the database
gains timing and an nPVI retroactively, with no migration.**

## Which comparison is primary

Published General American nPVI bands come from hand-segmented corpora reading different
material. nPVI is sensitive both to the segmentation method and to the text, so scoring
Azure-derived durations against a published band compares three things at once and the absolute
number is close to meaningless.

Measured, not asserted. Four defensible methodology choices on the *same* committed recording:

| Variant | nPVI |
| --- | --- |
| raw `Duration`, merged intervals, 100 ms pause break | **55.72** (25 pairs) |
| raw `Duration`, unmerged phones | 56.25 |
| raw `Duration`, no pause break at all | 54.75 |
| `Duration + 1 frame` (see the seam below), merged | ~50.3 |

A 5.4-point spread from segmentation policy alone, on one unchanged recording — wider than
several published cross-language contrasts within stress-timed varieties. That is the whole
argument.

**The primary comparison is therefore the same benchmark passage rendered by Azure TTS and pushed
through the same assessment pipeline** — same segmenter, same text, one variable — tracked over
weeks. Decided (confirmed with the user): **capture that baseline in this chunk.** It costs 975
TTS characters and ~85 s of STT, once, against free tiers of 500,000 characters and 18,000
seconds. Reusable forever.

Consequence: the published GA band gets **no chart ink at all**. With a same-pipeline baseline on
the chart, a band that cannot be compared to it would only invite the comparison. Its provenance
and why it is not plotted go in a caption and in `rhythm.py`'s module docstring.

The baseline is Azure TTS speaking, not a native human. It is a **fixed reference point through
an identical pipeline**, not ground truth for "native". The UI must say so.

## Findings from the committed fixture that shape the design

All verified against `tests/fixtures/`, not recalled.

1. **Top-level `Offset` is 16,900,000 and `SNR` is 25.035732** in `sample_azure_response.json` —
   the two values the exit criteria name. The first phoneme /ð/ of "the" sits at offset
   16,900,000 for 1,900,000 ticks: 1.69 s in, 190 ms long.

2. **Offsets are ticks from the start of the AUDIO STREAM, which is not necessarily the start of
   the file.** In this fixture the first word begins exactly at the top-level `Offset` (1.69 s),
   i.e. there is 1.69 s of something before it. **Check the top-level `Offset` before trusting the
   arithmetic when this data is eventually used to slice audio.** Nothing in this chunk slices
   audio, so nothing here depends on it — but the next chunk that does will, and this is where it
   is written down.

3. **Everything sits on a 10 ms grid** — every `Offset` and every `Duration` at every level is a
   multiple of 100,000 ticks. Checked across all four fixtures: no exceptions.

4. **There is a systematic 10 ms seam between consecutive segments.** Within a word, the first
   phoneme starts exactly at the word's `Offset` and the last ends exactly at
   `Offset + Duration` (20/20 words), yet every consecutive phoneme pair has exactly 100,000
   ticks of gap (62/62 pairs; same for syllables, 9/9). So
   `sum(phoneme durations) + 10 ms × (n−1) = word duration`. The self-consistent reading is that
   Azure reports `Duration` as `(frames − 1) × 10 ms`, i.e. each segment's true extent is
   `Duration + 10 ms`.

   **Decision: use `Duration` as reported, raw.** The +1-frame correction is an inference; the
   reported value is a fact. It does bias nPVI *upward* (a 10 ms shortfall costs a 40 ms vowel 25%
   and a 320 ms vowel 3%, so short intervals shrink more) — which is one more reason the published
   band is not comparable. Both sides of the primary comparison carry the identical bias, so it
   cancels there. The delta is measured (≈5.4 points) and documented, so the decision is
   reversible with a known cost.

5. **Continuous mode returns one `SNR` per utterance**, not one per recording
   (`bad_delivery_capture.json` has seven payloads with SNRs 20.6–23.2).

6. **Adjacent vowel phonemes occur** across word boundaries (…"rather" /ɚ/ + "unpredictable" /ʌ/).
   Classic nPVI measures *vocalic intervals* — contiguous vocalic material — not phones, so these
   merge into one interval.

7. **The pause threshold barely matters here.** The fixture's phoneme-stream gaps are bimodal:
   1 frame (the seam, ×75), 3 frames (×4), then 21 frames / 210 ms (×2). nPVI is flat at 55.72
   for any threshold from 50 ms to 200 ms. 100 ms sits in the middle of that plateau.

## Part 1 — carry the timing through

### `speech_analyzer.py`

`TICKS_PER_MS = 10_000` already exists (line 43) and already documents ticks. Reuse it; add
`TICKS_PER_SECOND = 10_000_000` beside it.

One helper, used at all three levels:

```python
def _timing(node: dict[str, Any]) -> dict[str, Any]:
    """Offset/Duration in Azure's 100-ns ticks, plus derived seconds."""
```

Returns `offset_ticks`, `duration_ticks` (ints or `None`) and `start_s`, `end_s` (floats or
`None`). Keys always present, never absent — the same contract `prosody_detail` already holds in
`_omission`.

- `_normalise_word` (line 490): merge `_timing(word)` into the returned dict; add the same to
  each syllable entry (currently `{"syllable", "score"}`) and each phoneme entry.
- `_omission` (line 584): all timing keys present and `None`. A word that was never spoken has no
  extent, and every consumer must see one shape from both construction paths.
- Insertions built in `_diff_miscue` come from `_normalise_word`, so they carry real timing
  already — no change.

### SNR

`normalise()` returns a 3-tuple `(overall, recognised_text, words)` consumed in exactly two
places (`analyse`, and `progress_view.parse_attempts` as `_, _, words = ...`). **Do not change
the arity** — put SNR into the `overall_scores` dict, which is free-form: `db.record_attempt`
reads five named keys out of it and ignores the rest, so extra keys are already harmless and this
needs no column and no migration.

Two keys, both set in the single-shot branch and in `_merge_overall`:

- `snr_db` — duration-weighted across payloads via the existing `_weighted` helper, matching how
  every other merged score is combined.
- `snr_db_min` — the worst utterance. Later accent work gates *measurement quality* on SNR, and
  quality is governed by the worst segment, not the average.

`None` never `0.0`, per the existing prosody precedent.

## Part 2 — nPVI

### New module: `rhythm.py`

A pure reader of the normalised word shape, in the same family as `speech_analyzer.is_flagged` /
`phoneme_pairs` / `delivery_faults`, but in its own file rather than growing the already-38 KB
`speech_analyzer.py`. It gives later accent work an obvious home. No Streamlit, no network, no
SDK — the same boundary `progress_view.py` sits on.

**The vowel predicate reuses `phoneme_reference`, it does not restate it.** `phoneme_reference`
already classifies every symbol as `consonant | vowel | diphthong | r-coloured`, and its
`normalise()` maps aliases onto Azure's spellings. Vocalic is `kind in {vowel, diphthong,
r-coloured}`. Verified: all 39 distinct phoneme symbols across all four fixtures resolve, none
missing.

```python
VOCALIC_KINDS = frozenset({"vowel", "diphthong", "r-coloured"})
PAUSE_BREAK_MS = 100.0
MIN_PAIRS = 20

@dataclass(frozen=True)
class Rhythm:
    npvi: float | None
    pairs: int          # differences actually averaged
    intervals: int
    runs: int           # stretches the pauses cut the speech into
```

- `vocalic_intervals(words) -> list[list[float]]` — walks the phoneme stream in time order,
  merging temporally contiguous vocalic phonemes into one interval (gap ≤ one 10 ms frame), and
  starting a new run wherever the phoneme stream gaps by more than `PAUSE_BREAK_MS`. Runs are the
  unit; nPVI never pairs across a pause or across an utterance boundary.
- `npvi(words) -> Rhythm` — `100/(m−1) · Σ |d_k − d_{k+1}| / ((d_k + d_{k+1})/2)`, summed over
  every within-run adjacent pair and divided by the total pair count. Returns `npvi=None` below
  `MIN_PAIRS`: nPVI over a handful of vowels is noise, and a drill read yields far too few.
  Zero-length and `None` intervals are dropped (the mean would be a division by zero); none occur
  in any committed fixture, so this is a guard, not a code path.

`MIN_PAIRS = 20` is chosen against the data: the ~13 s drill fixture yields 25 pairs, so a normal
short read clears it and anything shorter does not. The 196-word benchmark passage yields several
hundred.

### The baseline

- **`scripts/capture_baseline.py`** — follows `scripts/capture_fixture.py` exactly (same
  `sys.path` bootstrap, same `OFFLINE_MODE`/`check_required` guards, same budget pre-flight,
  writes verbatim JSON). It synthesises `progress_view.BENCHMARK_PASSAGE` via `tts.synthesise`,
  writes the WAV to a gitignored path, feeds it back through `speech_analyzer.analyse` in
  paragraph mode, and writes the payload to
  `tests/fixtures/benchmark_tts_baseline.json`. Metered through `db.record_tts_usage` and the
  attempts table like any other call, so the meters stay honest.
- The **JSON is committed** (precedent: four fixtures already are). The **WAV is not** — a
  `audio/` line goes in `.gitignore`. Keeping the WAV means the baseline can be re-assessed later
  without re-spending TTS.
- `rhythm.py` loads the baseline lazily and exposes `baseline_npvi() -> Rhythm | None`, returning
  `None` when the fixture is absent so every consumer renders a real no-baseline state.

Two things the script must do that `capture_fixture.py` does not:

1. Synthesise at the **plain rate**, never `slow_ssml` — `tts.payload_for(text, slow=False)`.
   Slowed synthesis would change exactly the durations being measured.
2. Record the voice name (`tts.voice_name()`) into the fixture alongside the payload. A different
   `AZURE_TTS_VOICE` is a different baseline, and a baseline whose provenance is unrecorded is
   not a baseline.

### Where the number surfaces

**Practice tab** — inside `app.py:render_delivery` (line 1208), which already owns the
break/monotone panel. One line: the attempt's nPVI, the baseline's, and the difference, with a
caption naming what the baseline is (Azure TTS through the same pipeline, a fixed reference point
and not a native human). Renders the below-`MIN_PAIRS` state as "not enough connected speech to
measure rhythm" rather than a number, so a drill never shows one.

**Progress tab** — `progress_view.py` gains `rhythm_frame(parsed)` and `rhythm_chart(frame)`
following `score_frame`/`score_chart` exactly (pure, pandas + altair, no Streamlit), plotting
nPVI over time for benchmark attempts only, with the baseline as a horizontal rule. `app.py:
render_progress` (line 1305) renders it beneath the existing charts. Free-practice attempts are
left out entirely: nPVI is text-sensitive, which is the same reason the benchmark passage exists.
No `use_container_width` — `width="stretch"`, as the last two chunks already established.

## Files

| File | Change |
| --- | --- |
| `speech_analyzer.py` | `_timing` helper; `_normalise_word`, `_omission` carry it; SNR into `overall_scores` in both branches |
| `rhythm.py` | **new** — vocalic intervals, nPVI, baseline loader |
| `progress_view.py` | `rhythm_frame`, `rhythm_chart` |
| `app.py` | rhythm line in `render_delivery`; rhythm chart in `render_progress` |
| `scripts/capture_baseline.py` | **new** — one-off, billable, run manually |
| `tests/fixtures/benchmark_tts_baseline.json` | **new**, committed |
| `.gitignore` | gitignored `audio/` for the baseline WAV |
| `tests/test_parsing.py` | timing + SNR assertions |
| `tests/test_rhythm.py` | **new** |
| `tests/test_progress_view.py` | rhythm frame/chart |
| `pyproject.toml` | version → 0.6.0 |

## Not in this chunk

- **Audio storage.** The user overrode the no-audio rule (files on disk, path + hash in the DB).
  Confirmed: **record the permission, do not build it.** `memory-bank/projectbrief.md`'s
  "no stored audio" non-goal and `techContext.md` get updated to record the override and the
  intended mechanism; no column is added, no user recording is stored, `db.SCHEMA_VERSION` stays
  1. The only file landing on disk is the gitignored baseline WAV. A schema v2 would be this
  project's first real migration and belongs to the chunk that actually needs it.
- Any other rhythm metric (%V, ΔC, varco), the F0 track, vowel-space measurement, coaching text
  generated from nPVI. This chunk produces a number and shows it; turning it into advice is later
  work that this de-risks.

## Verification

Offline, no keys — everything below runs under the existing autouse `offline_env` fixture:

```bash
docker compose run --rm app python -m pytest -q
```

New assertions, all against committed fixtures:

- Top-level `Offset == 16_900_000` and `SNR == 25.035732` in `sample_azure_response.json`, and
  both survive `normalise()` into `overall_scores["snr_db"]`.
- The /ð/ of "the" normalises to `offset_ticks == 16_900_000`, `duration_ticks == 1_900_000`,
  `start_s == 1.69`, `end_s == 1.88`.
- Timing keys present at word, syllable and phoneme level on every word of both fixtures; present
  and `None` on an `_omission`.
- The 10 ms grid and the 10 ms seam hold across every fixture — these are the assumptions
  `vocalic_intervals` merges on, so they are asserted rather than trusted.
- `snr_db_min` ≤ `snr_db` on the seven-payload `bad_delivery_capture.json`.
- `rhythm.npvi` on `sample_azure_response.json` is **55.72 ± 0.01 over 25 pairs**.
- A run of identical durations gives nPVI 0; an alternating run gives the hand-computed value.
- Below `MIN_PAIRS`, `npvi is None` and `pairs` still reports the true count.
- No pair spans a pause: a hand-built payload with a 500 ms gap yields two runs and one fewer
  pair than the interval count minus one.
- `baseline_npvi()` returns `None` cleanly when the fixture file is absent, and both UI surfaces
  render their no-baseline state.

Then, live and manual — this is the step that needs the user's keys:

```bash
docker compose run --rm app python scripts/capture_baseline.py
```

Confirm: the WAV is ~80–90 s, the meters moved by ~975 characters and ~85 s, the fixture lands,
`baseline_npvi()` returns a number, and both the Practice and Progress surfaces render it against
a real read. Then read the benchmark passage yourself once in the browser and check the Practice
tab shows your nPVI beside the baseline's.

## Bookkeeping

Per `CLAUDE.md`:

1. Copy this file to `plans/2026-08-19_timing-data-and-npvi.md` and append a `planned` row to
   `memory-bank/history.md` **before** writing any code.
2. Commit in chunks on this branch (Part 1, then `rhythm.py`, then the UI, then the baseline).
   No `Co-Authored-By`, no watermarks.
3. On landing: flip the history row to `implemented`, write the verified facts into
   `memory-bank/techContext.md` and `progress.md` directly, and propose the exact lines for the
   `projectbrief.md` non-goal change rather than writing it unilaterally.
4. Release v0.6.0: bump `pyproject.toml`, tag, push tags, `gh release create v0.6.0
   --generate-notes`, link resolved issues, close the v0.6.0 milestone, record it in the memory
   bank. GitHub bookkeeping is confirmed with the user first, per the precedent set in the
   surface-scores chunk.
