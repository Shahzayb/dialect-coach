# Four defects from the 2026-08-20 manual session

## Context

Five issues were found by hand on 2026-08-20 against a real voice, none of them by the 974-test
suite. They are written up in `memory-bank/progress.md` (four under **Known issues**, one under
**Not yet proven live**). This plan fixes **1–4**. **Issue 5 (shadowing) is deliberately left
open** and is not touched; if any of this work lands near `render_shadow` or `SHADOW_METRICS`,
the behaviour there stays exactly as it is and the commit says so.

Each fix carries a test that fails against the bug it covers — the pattern the v0.11.0 review
pass established. Work lands in chunks, one commit per issue, on the current branch
`claude/accent-tab-reading-mismatch-f94888`. No `Co-Authored-By`, no watermarks.

Plan file to write into the repo before any code: `plans/2026-08-20_manual-session-defects.md`
(this document), plus a `planned` row appended to `memory-bank/history.md`.

---

## Issue 1 — the Accent tab renders one reading's acoustics under another's label

### What the source actually says

The report guessed "the render where `options` grows". Reading the installed streamlit 1.61.1
in the image (`accent-measurement-engine-2c048c-app:latest`) shows a larger and simpler cause,
and one that has been firing on **every** early-terminated rerun, not only on the one where a
new attempt lands:

1. `st.rerun()` builds `RerunData(...)` with **no `widget_states`**
   (`streamlit/commands/execution_control.py:181`). The script runner only calls
   `on_script_will_rerun` — which is what re-injects the browser's widget values —
   `if rerun_data.widget_states is not None` (`scriptrunner/script_runner.py:718`). A
   server-initiated rerun therefore never restores anything from the browser.
2. A `RerunException` is **not** a premature stop — `exec_code.py:115` documents
   `premature_stop` as "False for RerunExceptions" — so `_on_script_finished` still runs
   `session_state.on_script_finished(...)` → `_remove_stale_widgets(...)`
   (`state/session_state.py:1113`).
3. Any `st.rerun()` in the Today or Practice tab ends the script **before** the Accent tab is
   reached (tabs render in order; `app.py:3760` even says "`st.rerun()` ends the script"). The
   Accent selector is therefore not in `widget_ids_this_run`, so it is stale, so its value is
   deleted from both `_new_widget_state` and `_old_state`. It is not preserved: preservation
   needs `bind="query-params"` or `persist_state`, and it has neither.
4. On the next full run the selectbox registers **fresh**: `deserialize(None)` returns
   `self.options[self.default_option_index]` (`widgets/selectbox.py`, `SelectboxSerde`), and
   with no `index=` that default is **position 0 — the newest measured attempt**.
5. Because this is a first registration, neither `value_changed` nor `value_needs_reset` is
   true, so `set_value` is never put on the proto and the browser is never told. It keeps
   painting the label it already had.

That accounts for every observed detail: it fires when a new attempt lands (the assessment's
0.4 s poll at `app.py:3761`/`3912` is what discards the selection), the backend uses the newest
while the browser shows the old label, tab switching does not rerun the script so it survives,
and operating the selector sends a real browser rerun carrying `widget_states`, which fixes it.
It was invisible before attempt 12 only because the reset kept landing back on the same newest
attempt.

`measurement_for` (`src/app.py:1983`) and `db.vowel_measurements_for` are clean — confirmed.

### Reproduce before changing anything

Do this first and record the result in the commit message, per the standing rule about not
reasoning about library internals from memory.

`AppTest` reproduces this faithfully, because `ElementTree.run` collects widget states **from
the previous run's element tree** — exactly like the browser — and a rerun-truncated run never
emits the Accent tab, so its selectbox is absent from that collection. In `tests/test_accent.py`:

- seed three measured attempts (reuse `_seed_calibration` there, extended, or `db.record_attempt`
  + `db.record_vowel_measurements` directly),
- run, select the **oldest** in `Which reading?`, run again,
- force one rerun-truncated pass (click a Today/Practice control that calls `st.rerun()`, or use
  `tests/test_app.py:817 _hanging_job` **without** the `settled_poll` fixture, patching `st.rerun`
  to raise the real `RerunException` once and then no-op),
- run again and assert the selector still holds the oldest attempt.

Today that assertion fails: it holds the newest. Confirm live afterwards with `make up` plus a
real recording, watching the label against the rhoticity table.

### Fix

`src/app.py`, `render_accent_charts` (~2018–2060):

- Add a module constant `ACCENT_CHART_CHOICE = "accent_chart_attempt_id"` — a **plain** session
  key, not a widget key. `_remove_stale_widgets` only strips element-id keys
  (`session_state.py:1176` keeps `not is_element_id(k)`), so a plain key survives every rerun.
- Resolve the default by **identity, never position**:
  ```python
  options = list(labels)
  remembered = st.session_state.get(ACCENT_CHART_CHOICE)
  index = options.index(remembered) if remembered in labels else 0
  attempt_id = int(st.selectbox(..., options=options, index=index, format_func=..., key=...))
  st.session_state[ACCENT_CHART_CHOICE] = attempt_id
  ```
  The chosen reading now sticks (the user's decision): a newer reading appears at the top of the
  list and is not auto-selected. When the widget's own state survives it wins and is re-recorded;
  when it is discarded, `deserialize(None)` lands on the remembered attempt instead of position 0,
  so backend and browser agree either way.
- Keep the docstring honest about why `index=` is not optional here, naming the `st.rerun()`
  mechanism above.

### The invariant, as a refusal

New pure function beside `plot_gate` in `src/vowel_measure.py` (~1875), returning a reason string
(`""` = draw), matching how `STYLE_MISMATCH` and friends are already shaped:

```python
LABEL_MISMATCH = (...)  # names both counts
def label_matches_measurement(labelled_tokens: int, measurement: Measurement) -> str: ...
```

Wired in `render_accent_charts` immediately after `measurement_for` returns: compare the selected
`attempts` row's `accepted` count against `len(measurement.accepted)` and `st.error` + `return`
when they disagree. A label claiming 138 tokens above a table reporting n=2 is arithmetically
impossible and the page must refuse rather than draw it.

**Stated plainly:** for a single id these two counts agree by construction today —
`measured_attempts`' SQL filter (`accepted = 1`) and `Measurement.accepted`
(`vowel_measure.py:212`) read the same flag. So this is a **tripwire on the id → row → tokens
pairing**, not a detector for the mechanism found above; it fires if a future change ever lets the
label and the measurement be resolved from different ids or different snapshots. Its test builds a
`Measurement` from one attempt's rows and hands it the other attempt's label count, and asserts
the refusal.

### Say what was drawn, independently of the widget

One caption under the selector, sourced from the loaded measurement and its row:
`Plotting #12 · 51 accepted tokens · 2026-08-20 12:34`. That is what makes a recurrence visible at
a glance instead of requiring the reader to do arithmetic across two panels.

### Minor, taken here because it is one line

`app.py:2216` prints `{len(model_tracks)} model voice(s).` — a plural hedge for a case that cannot
happen, since `native_model.renderings_for` stores one rendering per text. Pluralise properly from
the count.

**Files:** `src/app.py`, `src/vowel_measure.py`, `tests/test_accent.py`, `tests/test_vowel_measure.py`.

---

## Issue 2 — the practice queue never rotates

`next_due` is written back only when the state changed or the item regressed (`src/app.py:3458`),
so a completed block that leaves the target `active` writes `last_seen` and nothing else.
`due()` (`src/practice_queue.py:519`) sorts on `(active?, next_due)`, a stable sort pins the same
row at index 0, and `render_today` takes `trainable[0]` (`src/app.py:3018`) forever. `last_seen`
is written at `app.py:3447` and `app.py:3537` and **read nowhere** — confirmed by grep across
`src/`.

**Taking the preferred fix, and agreeing with the reasoning.** Order `due()` by `last_seen`:
never-seen first, then oldest. The alternative leaves ordering on a timestamp whose documented
meaning is "due now" for every active target (`practice_queue.next_due:479`), which would make the
sort key mean two different things at once.

`src/practice_queue.py`, `due()`:

```python
ready.sort(key=lambda row: (
    0 if str(row.get("state")) == ACTIVE else 1,
    str(row.get("last_seen") or ""),   # "" sorts first: never practised goes to the front
    str(row.get("next_due") or ""),
    str(row.get("item") or ""),        # total order, so two runs never disagree
))
```

Update the docstring: it currently says "soonest first", which stops being the rule. Note in it
that `next_due` remains the *gate* (an item is only `ready` when it is due) and becomes a
tiebreak, while `last_seen` is the *rotation*.

**Test** (`tests/test_practice_queue.py`): three active targets sharing one `next_due`, two of
them with `last_seen` set; assert the never-seen one comes first, then the least recently seen,
and assert that practising the head (setting its `last_seen` to now) moves it to the back. Fails
today — the input order survives.

**Out of scope, worth a line in the memory bank:** a stress drill has no completion event, so it
never writes `last_seen` and `drills[0]` still cannot rotate. `render_shadow_offer` also calls
`due()`, but shadow items ride a fixed interval and are unaffected by the new tiebreak.

**Files:** `src/practice_queue.py`, `tests/test_practice_queue.py`.

---

## Issue 3 — a repeated word is reported as a phoneme substitution

The speaker said "Wednesday" twice. `_diff_miscue` (`src/speech_analyzer.py:604`) already handles
it correctly — the `insert` opcode tags the second occurrence `error_type: "Insertion"`,
`error_source: "local_diff"`, which is what italicises it in the script-versus-heard diff. The
*first* occurrence is aligned `equal`, keeps its Azure score of 6, and is flagged on low accuracy;
its final `/eɪ/` has the second word's `/w/` onset as its best alternate, so
`app.weakest_phoneme` (`src/app.py:473`) renders `/eɪ/ → sounded like /w/`. The signal exists; it
is not wired to the card.

**Detect it where the diff already knows.** `src/speech_analyzer.py`, a new pure pass run right
after `_diff_miscue`: for each `Insertion` produced by the local diff whose normalised text equals
its immediate neighbour's (`utils.normalise_words`, the same tokeniser both diffs already share),
mark **both** words `disfluency: "repetition"`. Add the key to `_omission`'s dict too, so every
normalised word carries it and no consumer needs a per-construction-path guard — the rule that
file already states.

**Then suppress the phantom substitution on both surfaces** (the user's decision, and the rule
`techContext.md` states: one definition, so the card and the coaching report can never disagree
about a substitution):

- `app.weakest_phoneme` returns no substitution claim for a word marked `repetition`. The card
  instead says the true thing — the word was said twice, and the low score is the aligner reading
  across the stumble, not a sound to drill. The phoneme table below it is left alone: it is the
  raw payload and stays faithful.
- `fallback_coach._substitutions` (`src/fallback_coach.py:190`) returns `[]` for such a word, so
  the pair never enters `compact()`'s `observed_pairs` and `ai_coach.validated()` will drop it if
  the model names it anyway. The word still appears in `flagged_words` with its score and its
  error type — the stumble is real and worth reporting; only the invented substitution goes.

**Tests:** a payload fixture with a doubled word (hand-built, marked `_synthetic` in the file per
the existing rule if it lands in `tests/fixtures/`, or constructed inline in the test as
`tests/test_render.py` already does with its `word()` helper). Assert: both occurrences carry the
repetition mark; `weakest_phoneme` makes no `→ sounded like` claim; `compact()['observed_pairs']`
does not contain the cross-boundary pair; and — the regression guard — an ordinary
mispronunciation in the same payload is still reported. All fail today.

**Files:** `src/speech_analyzer.py`, `src/app.py`, `src/fallback_coach.py`, `tests/test_parsing.py`,
`tests/test_render.py`, `tests/test_fallback_coach.py`.

---

## Issue 4 — the error-count badges put two different units side by side

`render_error_counts` (`src/app.py:1067`) counts **words** for every badge. "2 Mispronunciations"
is two independently wrong words; "28 Monotone" is **one** flat stretch spanning 28 words, which
is exactly how the Delivery panel below words it. Side by side the row implies the monotone
problem is fourteen times the articulation problem, and because prose comes in spans the monotone
badge is structurally always the largest and least informative number on the row.

`speech_analyzer.delivery_faults` already cuts each span into contiguous `runs` via `_runs`
(`src/speech_analyzer.py:926`) — the count wanted here already exists and must not be recomputed
in `app.py`.

- Extend `ERROR_BADGES` with a unit so the row stays declarative, and count stretches for
  `Monotone`: badge number `1`, label `Monotone stretch (28 words)`, pluralising the noun on the
  stretch count.
- **Breaks stay word counts.** An unexpected or missing break is a point event located at a word,
  not a span; two flagged words are two breaks. A comment says so, so nobody "fixes" it later.
- It stays a headline count row — no spans, no confidence figures, no second copy of the Delivery
  panel.

**Test** (`tests/test_render.py`): pull the badge row out of `render_error_counts` into a
Streamlit-free helper returning `(count, label)` pairs — the boundary that file's own docstring
describes — and assert that a Monotone span of 28 contiguous words gives `(1, "Monotone stretch
(28 words)")` while two mispronounced words give `(2, "Mispronunciations")`; plus a two-stretch
case for the plural. Fails today (28 vs 1).

**Files:** `src/app.py`, `tests/test_render.py`.

---

## Not touched

**Issue 5 — shadowing.** Simultaneous mode is unusable for this speaker; echo mode has no recorder
(`render_shadow` calls `st.audio_input` only in the simultaneous branch). Three candidate
directions are recorded in `progress.md`. The user chose to leave this open on 2026-08-20 and it
stays open. No file touched by issues 1–4 changes shadowing behaviour.

---

## Verification

- `make test` after each chunk; `make check` (lint + mypy + tests) before the final commit. The
  suite was green at **974 passed** when these were filed — the count should rise, never fall.
- Every new test must be seen **failing** against the unfixed code first. Run it, capture the
  failure, then fix.
- Live pass with `make up` on `:8501`:
  - **Issue 1** needs a real recording. With the Accent tab already rendered and a reading
    selected, record and assess a new attempt, then read the label against the rhoticity table
    without touching the selector. Also exercise the cheaper trigger now that it is known: click
    anything in Today/Practice that reruns the script, and confirm the Accent selection holds.
  - **Issue 3** needs a real recording with a deliberate repeated word.
  - **Issues 2 and 4** are reachable from stored data alone — issue 2 by completing a block below
    the 90% bar and confirming the next block offers a different target; issue 4 from any stored
    attempt carrying a Monotone span.
- **Do not open `data/coach.db` from the host** while the app holds its connection — SQLite WAL is
  not readable across processes over the macOS bind mount and shows a stale table. Use the app's
  own History panel or `docker compose logs app`.

## When it lands

- `memory-bank/progress.md`: move the four fixed items out of **Known issues** into **What works**,
  describing what the work *found* (particularly that issue 1 was a lost widget value on every
  early-terminated rerun, not a one-frame paint glitch on the render where the options grew), and
  leave issue 5 exactly where it is.
- `memory-bank/history.md`: move this plan's row from `planned` to `implemented`.
- Judgment calls (any wording in the memory bank that is interpretation rather than verified fact)
  get proposed as exact lines before being written, per `CLAUDE.md` §3.
