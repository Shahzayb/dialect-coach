# Progress view + the benchmark passage (milestone v0.5.0)

## Context

Every attempt has been stored in SQLite since the first chunk — `azure_raw_json` verbatim,
the normalised scores beside it — and nothing has ever read it back. `projectbrief.md` asks
for two things this closes: *"Keep history in a local SQLite file, and show it back over
time"* and *"see that drilling it worked"*. This is the first feature in the project that
reads history at all.

The trap it has to avoid: plotting scores across arbitrary self-chosen texts measures **text
difficulty, not the speaker**. An easy paragraph scores higher and reads as progress. So the
headline is a **fixed benchmark passage**, read on a schedule and scored identically every
time; free-practice attempts sit behind it as a faint, unconnected cloud for context.

The passage is chosen **once, here**, for **two** consumers: this chart, and the vowel
measurement instrument a later chunk needs a calibration read for. One 60–90 second read
serves both.

---

## 1. The benchmark passage

**Name:** `Benchmark — the same words each morning`
**Mode:** B (paragraph / continuous). 198 words. **~79–85 s at a normal 140–150 wpm read.**

> Each morning I read these same words out loud, the way I said them last week. Nothing here
> is clever. The whole value is that the passage never changes, so whatever moves is my own
> voice, not the writing.
>
> Three things go through my mind while I read. The first is breath, where a short pause
> helps the listener, and where I join two thoughts that should have stayed apart. The second
> is the end of every word, the hard sounds I let go soft when I am tired, in asked and
> helped, in world, month and next. The third is the choice I make on each vowel, whether to
> hold it full and clear or to let it slide.
>
> A few of them still catch me. Brother and breathe. Believe and above. School, careful and
> cold. During a long, honest answer the joy goes out of it, I am not sure of my own voice,
> and I judge it more than I should.
>
> So I stop, sit up, take a fair pace, and finish the thought I began. In a good year I would
> like to measure how far this went, without the usual excuses.

### Why this passage covers both instruments

**It is about its own re-reading.** A passage read on a schedule for months has to survive
being read again. This one is first-person and about the practice itself, matching the voice
of the existing `PRESETS[Mode.PARAGRAPH]` entries.

**Constraints inherited from the codebase, not invented:**

- **No digits** — `app.py:113-115` records that Azure normalises `"33"` and `"thirty-three"`
  differently, which breaks word alignment. None appear.
- **Commas and periods only** — no em dashes, colons, or hyphens. The Mode B local miscue
  diff re-tokenises the reference (`speech_analyzer._diff_miscue`); the punctuation-token and
  hyphen indexing bugs were fixed in the Azure-core chunk, but there is no reason to re-enter
  that territory in a text that will be read a hundred times.
- **Punctuation is real, not decorative.** Prosody is one of the four charted metrics and is
  scored on connected speech, so the passage stays prose with genuine phrase boundaries. Word
  lists ("Brother and breathe.") are kept to three short fragments, deliberately placed where
  a list is natural.

**Consumer 1 — the trajectory chart** needs a text whose difficulty is constant and whose
faults are the speaker's known faults, so that a change in score is a change in the speaker.
Every sound this project was built to catch (`app.py:112-118`, `phoneme_reference._CONSONANTS`
seed order) appears several times, in stressed and unreduced positions:

| Target | Tokens in the passage | n |
| --- | --- | --- |
| /θ/ | three, things, through, thoughts, third, month, breath, thought | 8 |
| /ð/ | these, them, that, whether, brother, breathe | 6 |
| /v/ | value, voice, never, every, vowel, believe, above, moves | 8 |
| /w/ | words, word, way, week, whatever, where, while, when, world, would, which | 11 |
| /t/ **non-flapped** | two, tired, take, still, sit, stop, next, apart, last, first | 10 |
| /d/ | said, read, world, cold, would, third, during, end, mind, hard, hold, slide, loud | 13 |
| dark /l/ (coda) | whole, value, helps, world, vowel, full, still, school, careful, cold, while | 11 |
| clear /l/ (onset) | loud, listener, let, like, long, last | 6 |
| /ʃ/ | short, should, sure, finish | 4 |
| /s/ | same, said, so, sit, sounds, still, second, slide, school, stayed, pace, voice, soft | 13 |
| /z/ | these, changes, words, moves, sounds, pause, things, goes, excuses | 9 |
| /dʒ/ | join, joy, judge *(two /dʒ/ in one word)* | 3 |
| **final clusters** | asked /skt/, helped /lpt/, next /kst/, world /rld/, month /nθ/, first /rst/, words /rdz/, sounds /ndz/, thoughts /ts/, helps /lps/, cold, hold, mind, end | 14 |

Two things that were designed in rather than fallen into:

- **/t/ and /d/ are placed where General American does *not* flap them** — word-initial
  (*take, tired, two, down*), after /s/ (*still, stop, stayed, sit*), and word-final or in a
  cluster (*next, apart, last, first, world, cold*). No *better / water / city*. This matters
  because `phoneme_reference._ALIASES` maps `ɾ → t`: a flapped token is scored as /t/ and
  tells you nothing about the dental-vs-alveolar contrast the passage is supposed to measure.
- **The passage contains its own θ/ð minimal pair**: *breath* and *breathe*, one sentence
  apart. Same for the /s/–/ʃ/ neighbourhood (*short* beside *stayed*, *sure* beside *so*) and
  /v/–/w/ (*value* beside *whatever*, *vowel* beside *would*).

**Consumer 2 — the vowel calibration read** needs every vowel in the en-US inventory, in a
stressed unreduced syllable, several times, so formants can be measured and compared read to
read. The inventory is taken from `phoneme_reference.py` (46 entries, Azure's own rhotic,
length-mark-free symbols — `ɝ ɚ ɹ ɔɹ ɪɹ oʊ eɪ`, never `iː ɑː ɜː`), which is the project's
authoritative list:

| | Vowel | Tokens | n |
| --- | --- | --- | --- |
| Monophthong | æ | passage, asked, last, answer, catch | 5 |
| | ɛ | breath, end, every, let, helps, helped, second, next, said, went | 10 |
| | ɪ | this, things, still, sit, finish, listener, which | 7 |
| | i | these, read, week, breathe, believe, three, each, me | 8 |
| | ɑ | not ×2, honest, stop | 4 |
| | ʌ | nothing, up, month, judge, above | 5 |
| | ɝ | words, word, world, first, third | 5 |
| | ʊ | should ×2, would, full, good | 5 |
| | u | two, through, school, moves, usual, excuses | 6 |
| | ɔ | thoughts, thought, soft, long, pause | 5 |
| | ə *(reduced anchor)* | passage, listener, second, answer, apart, above, began, usual | 8 |
| | ɚ | listener, never, clever, whatever, brother, whether, answer | 7 |
| **Diphthong** | **eɪ (FACE)** | same, way, stayed, make, take, pace, began | **7** |
| | aɪ | my, while, mind, tired, slide, like, writing | 7 |
| | **oʊ (GOAT)** | whole, so, own ×2, go, goes, hold, cold | **7** |
| | aʊ | out ×2, loud, sounds, how | 5 |
| | ɔɪ | voice ×2, join, joy, choice | 5 |
| R-coloured | ɑɹ | hard, apart, far | 3 |
| | ɔɹ | morning, short, more | 3 |
| | ɛɹ | where ×2, careful, fair | 4 |
| | ɪɹ | here, clear, year | 3 |
| | ʊɹ | during, sure | **2 — see below** |

Three honest caveats, recorded now so a later measurement chunk does not rediscover them:

1. **ʊɹ (CURE) gets two tokens and cannot get more naturally.** It is the rarest vowel in
   General American and is actively merging into ɔɹ for most speakers — *sure* is commonly
   /ʃɔɹ/. It is the one inventory member this passage cannot guarantee; *during* holds it
   better than *sure* does. A measurement consumer should treat ʊɹ as best-effort.
2. **ɑ and ɔ are subject to the cot–caught merger.** Azure's en-US model still emits both
   symbols and `phoneme_reference` keys both, so tokens for each are present, but a merged
   speaker's ɑ and ɔ will measure alike. That is a finding, not a defect in the passage.
3. **Stressed /ð/ is intrinsically limited.** In English it lives almost entirely in function
   words. The passage takes three of the handful of content words that carry it — *brother*
   (medial), *breathe* (final), *whether* (medial) — plus the unstressed *the/them/that*
   tokens. Six is close to the ceiling for a natural text.

**Timing.** 198 words is 79–85 s at 140–150 wpm — inside the 60–90 s window, at the top of it
deliberately, because the binding constraint is token density for the vowel instrument, not
brevity. A read slower than ~132 wpm would exceed 90 s, and that is itself a fluency signal
worth capturing rather than a reason to shorten the text.

**The passage is frozen at merge.** The benchmark series is identified by matching
`reference_text`, so editing a word starts a new series. `BENCHMARK_VERSION = 1` is defined
alongside it to make that explicit.

### Where it lives

- `progress_view.BENCHMARK_PASSAGE` — the frozen text, plus `BENCHMARK_TITLE`,
  `BENCHMARK_VERSION`, and `BENCHMARK_COVERAGE: dict[str, tuple[str, ...]]` — the two tables
  above as data, so the justification cannot silently drift from the text (a test asserts
  every listed token actually appears in the passage).
- Added to `app.PRESETS[Mode.PARAGRAPH]` as the **first** entry, so it is selected rather than
  retyped — a hand-typed near-copy would not match and would silently start a second series.

---

## 2. Identifying a benchmark attempt without a migration

`db.SCHEMA_VERSION` is 1 and `_migrate` (`db.py:98-109`) has **no upgrade path** — adding an
`is_benchmark` column means writing the branch that does not exist. The v1 precedent
(`gemini_raw_json` created NULL so coaching was an UPDATE, not a migration) says: avoid it.

So a benchmark attempt is identified by **matching the stored `reference_text`**:

```python
def benchmark_key(text: str | None) -> str:      # pure, in progress_view.py
    """Normalised identity of a reference text: lowercase, punctuation-stripped words."""
    return " ".join(utils.normalise_words(text or ""))

def is_benchmark(text: str | None) -> bool:
    return bool(text) and benchmark_key(text) == _BENCHMARK_KEY
```

Reuses `utils.normalise_words` (`utils.py:275-277`) — the same normaliser the miscue diff
uses — so whitespace, casing and punctuation differences do not split the series. Zero schema
change, and it works retroactively on rows already stored.

---

## 3. Mode A and Mode B never share a line

Mode B's overall scores come from a duration-weighted merge across utterances
(`speech_analyzer._merge_overall`), an approximation of an unpublished Azure composite. They
are not comparable to Mode A's single-shot scores. Enforced **structurally**, not by
convention:

- **Only the benchmark subset gets a line mark.** It is single-mode by construction (the
  passage is 198 words — always `Mode.PARAGRAPH`), so a line can never span two modes.
- **Free practice is drawn with point marks only** — never connected. Mode is encoded as
  *shape* (drill = triangle, paragraph = circle), so the two modes are visually distinct and
  structurally unjoinable.
- A test asserts against `chart.to_dict()` that **no layer using `mark: line` encodes `mode`**
  and that the free-practice layer's mark is `point`. That is the rule as an assertion rather
  than a comment.

---

## 4. Files

### New: `progress_view.py`

Imports `pandas`, `altair`, `json`, `speech_analyzer`, `utils`, `db` types. **Never imports
Streamlit** — same rule as the pure render helpers in `app.py:263-267`, so the frames and the
chart specs are testable directly rather than through a headless app run.

| Function | Returns |
| --- | --- |
| `benchmark_key(text)` / `is_benchmark(text)` | passage identity (above) |
| `score_frame(rows) -> pd.DataFrame` | long form: `when, metric, value, series, mode, attempt_id, label` |
| `score_chart(frame) -> alt.Chart` | layered (cloud + benchmark line), faceted by metric |
| `parse_attempts(rows) -> list[ParsedAttempt]` | re-parse of `azure_raw_json` via `speech_analyzer.normalise` |
| `flagged_phonemes(parsed) -> pd.DataFrame` | `label, expected, produced, attempts, benchmark_attempts, tokens` |
| `flagged_words(parsed) -> pd.DataFrame` | `word, attempts, benchmark_attempts, tokens` |
| `phoneme_chart(df)` / `word_chart(df)` | horizontal bar specs |

**`score_frame` details.** One row per (attempt × metric), metrics
`Pronunciation / Accuracy / Fluency / Prosody` from `pron_score, accuracy, fluency, prosody`.

- **A NULL prosody produces no row** — never a zero. `db.py:43` and `tests/test_db.py:85-90`
  pin that NULL and 0.0 are different things; a gap in the line is the correct rendering.
- `offline = 1` rows are excluded in SQL. An `OFFLINE_MODE` replay returns the same fixture
  scores every time; thirty identical points is not a trajectory.
- **Y axis is fixed to `[0, 100]`.** An auto-scaled axis turns noise into a trend, which is
  precisely the failure this whole design exists to prevent, and the brief measures progress
  as *distance from native-like*, not as a pass mark.
- Tooltip: date, mode, metric, value, and the first ~40 chars of the reference text.

**`parse_attempts` details.** Feeds the stored blob back through
`speech_analyzer.normalise(payloads, reference_text, Mode(row["mode"]))` — the documented
re-parse route (`db.py:1-8`). Two traps to handle: a drill stores a JSON **object** and a
paragraph a JSON **array** (`app.py:727`), so wrap a non-list in a one-element list; and the
`Mode` must be passed so the Mode B local miscue diff runs.

**`flagged_phonemes` details.** Over words where `speech_analyzer.is_flagged(word)`, using
`speech_analyzer.phoneme_pairs(word)` — the single definition of "what you actually produced",
so this view can never disagree with the word cards or the coaching report.

- A pair with a named `produced` becomes `/θ/ → /s/`.
- A phoneme scoring below `utils.PHONEME_RED` with **no** differing alternate becomes
  `/t/ → (unclear)`, kept as its own bucket. That is what cluster simplification looks like in
  the data (`phoneme_reference.FINAL_CLUSTER_NOTE`), and dropping it would make the passage's
  fourteen final clusters invisible.
- Ranked by **attempts it appeared in**, then tokens — "flagged most often" across attempts,
  so one paragraph repeating a word cannot dominate. `benchmark_attempts` is carried as a
  second column: on the fixed passage the count is directly comparable read to read.

`flagged_words` is the same shape keyed on the lowercased word.

### `db.py` — two readers

Modelled on `recent_attempts` (`db.py:218-227`), which is the right template:

- `attempt_series(conn)` — same column list, **no limit**, `WHERE offline = 0`,
  `ORDER BY created_at, id`. Note `recent_attempts` orders by `id DESC`, not `created_at`;
  a time series must order by the timestamp, which `idx_attempts_created_at` already backs.
- `attempt_payloads(conn)` — `id, created_at, mode, reference_text, azure_raw_json` with the
  same filter and order. Separate because the blobs are 45–170 kB each and the chart does not
  need them.

### `app.py` — the Progress tab

- `st.tabs(["Practice", "Progress"])` immediately after `check_startup()` / `get_connection()`
  (`app.py:1287-1288`). Lines 1296-1383 (mode, text, audio, controls, result) move into tab 1;
  the existing History expander and budget caption (`app.py:1385-1398`) move into tab 2 under
  the new charts. Bare `render()` at `app.py:1401` and `AppTest.from_file` are unaffected —
  still one script file, no `pages/`, no `st.navigation`.
- `render_progress(conn)` — the impure half: reads under `_DB_LOCK` (`app.py:65`, the pattern
  at `app.py:1382-1384`), calls the pure builders, then `st.altair_chart(spec, ...)` and
  `st.dataframe(...)`. Same boundary as `render_colour_coded` (`app.py:1123-1126`): the helper
  returns the object, the wrapper hands it to Streamlit.
- **Caching is required, not optional.** Streamlit renders *both* tab bodies on every rerun,
  including the 0.4 s `JOB_POLL_SECONDS` reruns during an assessment — and re-parsing every
  `azure_raw_json` on each of those is exactly the cost
  `plans/2026-08-19_record-assess-defects.md:79` already flagged for the much cheaper
  `recent_attempts`. So `parse_attempts` is wrapped in an `@st.cache_data` function in
  `app.py` keyed on a cheap fingerprint `(max(id), count(*))`; the connection is passed as
  `_conn` so Streamlit does not try to hash it.
- Empty states are explicit, not blank: no attempts at all → "Nothing recorded yet."; attempts
  but no benchmark read → the cloud renders and a caption names the passage and says the
  headline series starts at the first read of it.
- A caption states the days since the last benchmark read. **No due/overdue nudge, no new
  config key** — the cadence stays the user's discipline.

### `requirements.txt`

`pandas` and `altair` arrive transitively via `streamlit==1.61.1`, but `progress_view.py`
imports them **directly**, so they get their own exact pins per the file's stated policy
("Exact pins, not ranges"). `numpy` is *not* pinned — nothing imports it directly.

**Read the versions out of the built image (`pip show pandas altair`) and pin those exact
values** — do not recall them. That is the standing preference recorded in `progress.md`
("Verify library versions … rather than recalling them") and the reason the original pins
were already stale. Each gets a one-line comment saying it is a direct import of
`progress_view.py`, not a transitive gift.

### `scripts/seed_progress_history.py`

Python, not shell, per the standing preference. Not collected by pytest (`testpaths = tests`).

- Writes ~30 days of synthetic attempts into a **throwaway** database — default
  `data/seed_demo.db`, never `DB_PATH` — via `db.record_attempt(..., created_at=...)`, the
  same keyword-only writer the tests use.
- `azure_raw` is replayed from the committed fixtures (`sample_azure_continuous.json` for
  paragraph, `sample_azure_response.json` for drill), so the phoneme and word aggregates have
  real Azure structure to chew on. **No network, no key, zero spend.**
- Every ~7th day is a benchmark read (`reference_text = BENCHMARK_PASSAGE`, paragraph mode);
  the rest are free practice across both modes with a different reference text.
- Scores follow a gentle upward trend with fixed-seed jitter, so re-running produces the same
  picture. `offline = 0` — the rows must appear in the chart, and the meter it inflates
  belongs to a demo database that is not the real one.
- Prints the `DB_PATH=… make up` line needed to view it.

---

## 5. Tests

New `tests/test_progress_view.py`, in the `test_render.py` style — imports the module
directly, no Streamlit runtime. Rows built with the `add(**overrides)` factory pattern from
`tests/test_db.py:26-34` against an in-memory `db.connect(":memory:")`, passing `created_at`
explicitly to control chronology.

- **Passage integrity** — every token listed in `BENCHMARK_COVERAGE` actually appears in
  `BENCHMARK_PASSAGE` (the guard against the justification drifting from the text); the
  passage contains no digits and no hyphens; word count is in range.
- **Identity** — exact text matches; case, extra whitespace and trailing-punctuation variants
  match; a different paragraph does not; `None` does not.
- **`score_frame`** — a NULL prosody yields no Prosody row rather than a 0.0; benchmark rows
  are tagged `series="Benchmark"` and free practice is not; drill and paragraph are tagged
  distinctly; `offline = 1` rows never appear; an empty table gives an empty frame with the
  right columns rather than raising.
- **Chart spec** — from `chart.to_dict()`: the benchmark layer's mark is `line`, the
  free-practice layer's is `point`, **no `line` layer encodes `mode`**, the y scale domain is
  `[0, 100]`, and the facet is on `metric`.
- **Aggregation** — against the real committed fixtures, per "build parsers against a captured
  payload, not documentation": a drill's object payload and a paragraph's array payload both
  parse; `/θ/ → /s/` on *thursday* surfaces from `sample_azure_response.json`; a phoneme
  flagged in two attempts ranks above one flagged twice in a single attempt.

`tests/test_app.py` additions (AppTest, the headless path): the Progress tab renders with an
empty database without raising and shows the empty-state text; with seeded rows it renders a
chart; the Practice tab still assesses and renders a result after the tab restructure.

---

## 6. Verification

1. `make test` — the full suite offline, no keys, no network. Expect ~352 existing tests plus
   the new ones, all passing.
2. Seed and look at it:
   ```bash
   docker compose run --rm app python scripts/seed_progress_history.py
   ```
   then run the app against the seeded database and open the Progress tab.
3. In the browser, confirm by eye: the benchmark series is a connected line and visually
   distinct from the free-practice cloud; the cloud's drill and paragraph points are different
   shapes and joined by nothing; all four metrics are present; a seeded NULL prosody shows a
   gap, not a dip to zero; the phoneme and word tables are populated. Check both the light and
   the dark Streamlit theme, as the previous two UI chunks did.
4. Confirm the Practice tab is unchanged: record/upload, assess, result, coaching.
5. **Zero spend.** Nothing in this chunk calls Azure or Gemini. The seed script and every test
   replay committed fixtures.

### What merge does *not* prove — stated plainly

**The real 30-day check is a calendar item, not a merge gate.** What merges is a seeded
history that renders as a trajectory; that proves the plumbing, the shapes and the chart, and
nothing about the speaker. **The benchmark series starts empty on the day this ships.** The
first real point appears at the first read of the passage, and the chart is only worth
anything after four or five reads spread over a month. Two things can only be checked then:

- **the real read duration** — the first benchmark attempt's own `audio_seconds` says whether
  198 words lands inside 60–90 s at the actual reading pace; and
- **whether the series moves at all**, which is the entire question the feature exists to
  answer.

Neither is available at merge time and neither should be claimed.

---

## 7. Release (milestone v0.5.0)

Per `CLAUDE.md`: bump `pyproject.toml` to `0.5.0`, conventional commits in chunks along the
way (not one at the end), tag, push tags, `gh release create v0.5.0 --generate-notes`, link
resolved issues, close milestone v0.5.0, and record it in `memory-bank/progress.md`. No
`Co-Authored-By` lines, no watermarks.

Memory bank on landing: a `planned` row in `memory-bank/history.md` for this plan file
immediately (before any code), moved to `implemented` when it lands; `progress.md` gains the
v0.5.0 release note and the benchmark passage's status; `techContext.md` gains
`progress_view.py` in the architecture section, the no-migration identity-by-`reference_text`
decision, and the pandas/altair direct-pin rationale.
