# Fix five interaction defects on record-and-assess, then the 8 open code-review findings

## Context

The record-and-assess surface (`app.py`) works end to end but has five interaction gaps
tracked as GitHub issues #4–#8 (milestone v0.2.0): no way to reset the recording/text, no
delete control on the recording itself, `Assess` stays clickable while a request is running,
there's no way to stop a request in flight, and Paragraph mode's flagged-word list gets
cluttered with words that scored a perfect 100 but got flagged anyway (delivery faults).

They're one chunk because they're one surface and #6/#7 (disable-while-running, Stop) are
genuinely coupled: both are about what "a request is in flight" means in a framework that
reruns the whole script on every widget click. Getting that state machine right is the
majority of the risk here; #4, #5 and #8 are comparatively mechanical once it's in place.

After this chunk lands, the plan also closes the 8 confirmed code-review findings already
logged against the coaching layer in `memory-bank/progress.md` (released in v0.1.0 on a
fix-after basis) — two of which are about `ai_coach.coach()`'s "always returns a report"
guarantee and the Gemini button's re-spend risk, not just polish.

## Streamlit 1.61.1 mechanics — verified, not assumed

Researched against Streamlit's own threading docs and community/maintainer discussion,
since this determines whether "Stop" can do anything at all:

- **A new widget interaction does *not* interrupt a blocking call already in progress.**
  Streamlit's rerun-kills-the-old-run behavior only fires at points where the script calls
  back into Streamlit's own machinery (an `st.*` delta call). A raw blocking call — like the
  Azure SDK's `recognize_once_async().get()` or the continuous-recognition `done.wait(...)`
  — never yields to that check, so a click on a "Stop" button while such a call is running
  has **zero effect** until the call returns on its own. This is confirmed both by
  Streamlit's own architecture and by a maintainer thread on the discuss forum stating
  plainly that "once a blocking call starts, new widget interactions cannot interrupt it
  mid-execution."
- **Streamlit does not officially support multithreading**, and its own docs are explicit:
  the supported pattern is a plain `threading.Thread` that calls *no* Streamlit API at all,
  with the main script thread retrieving the result afterwards (`thread.join()` or polling
  `thread.is_alive()`). Calling `st.*` from inside a worker thread is unsupported and can
  silently do nothing or warn.
- **A widget's `disabled=` prop only reflects the state computed on the previous completed
  run.** This project has already hit this exact bug once: the "Improve with Gemini"
  button's `already_asked` guard exists precisely because a click is handled in the same
  rerun that rendered the button, so the on-screen button still shows enabled until the
  *next* rerun (`techContext.md`, "UI contract" section). The fix there was a state-based
  guard checked before acting, not reliance on `disabled=`. The same lesson applies to
  `Assess`/`Stop` and is designed in from the start rather than discovered live again.
- **The established pattern for a cancellable long-running task in Streamlit** — confirmed
  against the framework's own docs and multiple community threads — is: background thread
  does the pure work and touches no Streamlit API; the main thread stores a handle
  (`Thread`, a `threading.Event` for cancellation, a plain object the thread writes its
  result into) in `st.session_state`; and the main thread polls by sleeping briefly and
  calling `st.rerun()`, which ends the current script pass and starts a fresh one that
  re-renders the latest state and re-evaluates whatever the user clicked in the meantime.
  There is no way to "await" a background thread mid-script without doing this.

**Consequence for design**: the Azure call must move off the script thread entirely. The
main thread's job becomes: start the worker, remember it in `session_state`, and repeatedly
poll-and-rerun until it's done — rendering `Stop` and a disabled `Assess` on every one of
those reruns, and reacting to a `Stop` click by setting a `threading.Event` the worker
checks at its own cancellation points (not by trying to kill the thread).

## The state machine (#6 + #7)

### New pieces in `app.py`

- `AssessJob` (plain `@dataclass`): `thread: threading.Thread`, `cancel_event:
  threading.Event`, `outcome: AssessOutcome | None = None` (written once, in place, by the
  worker just before it returns), plus the request's `key`/`reference_text`/`mode` so the
  main thread can cache the result under the right key once the job finishes.
- `AssessOutcome` (plain `@dataclass`): `assessment`, `attempt_id`, `error: tuple[str, str] |
  None` (icon/message — reusing the existing convention from `play()`, since this is
  produced off-thread and must never call `st.error` itself), `cancelled: bool`,
  `cancel_reached_azure: bool`.
- `st.session_state["assess_job"]` holds at most one `AssessJob` — one attempt in flight at
  a time, matching how the surface already works.
- `_DB_LOCK = threading.Lock()` (module-level in `app.py`). The only new source of
  cross-thread DB access is the worker's `db.record_attempt(...)` call racing the main
  thread's *unconditional* end-of-`render()` reads — `budget.summary_line(conn)` and
  `db.recent_attempts(conn, ...)` run on every single rerun, including the polling reruns
  that happen every ~0.4s while a job is alive. Wrap exactly those three call sites (the one
  write, the two reads) in `with _DB_LOCK:` — nothing else needs it, since every other DB
  call in the app (`attach_coaching`, `record_tts_usage`, `preflight_stt`'s read) still only
  ever runs on the main thread, synchronously, one at a time, exactly as today.
  `check_same_thread=False` and WAL mode (`db.py`, already chosen "so a read never blocks
  the write that follows it") make this safe at the SQLite level regardless; the lock is
  belt-and-braces against two Python threads calling into the same `sqlite3.Connection`
  object at literally the same instant, cheap enough to add without deliberating further.

### Flow

1. **Right after `get_connection()`, before any widget renders**: if `assess_job` exists and
   its thread has finished, collect `job.outcome`, clear `assess_job` from `session_state`,
   and fold the result into existing state (`_cache_put` + `last_key` on success;
   `st.error`/`st.info` on failure or cancellation) — exactly once, synchronously. Doing this
   *before* any widget is instantiated is what lets `running` (step 2) reflect the current
   pass accurately instead of one pass stale. No extra rerun is needed here: the script keeps
   running downward into the normal "render last result" section this same pass.
   **Defensive**: if `job.outcome` is still `None` here (the worker thread died without
   reaching any of its own `except` clauses — should not happen, but "should not happen" is
   exactly the class of bug that must not crash the page), treat it as a generic error rather
   than letting a `None` propagate into the cache/render path. The worker itself also gets a
   single outermost `except Exception` as a last-resort net that always sets *some*
   `AssessOutcome.error`, so this branch is a belt-and-braces backstop, not the primary path.
2. **Buttons**: `running = st.session_state.get("assess_job") is not None`, computed *after*
   step 1 so a job that just finished doesn't show as still running.
   `st.button("Assess", disabled=running or source is None)`,
   `st.button("Stop", ...)` rendered only when `running` is true,
   `st.button("↺ Reset", disabled=running, on_click=_reset_form)` — all three side by side,
   matching #4's "next to Assess" and #7's "beside Assess, visible only during a request."
   **All click-handling logic (step 3 onward) runs after the `st.columns(...)` block used for
   this row has closed** — the row only ever contains the three `st.button(...)` calls
   themselves. This isn't a style preference: `validate_reference()` calls `st.error`/
   `st.warning`, and this codebase has already measured what happens when an alert renders
   from inside an `st.columns` entry (124px of a 672px row, one word per line — see
   `playback_buttons`/`play()` for the existing pattern of returning rather than rendering
   from inside a column). Reusing that pattern here means `validate_reference` and any error
   from starting a job must be *called* outside the columns block, exactly like `play()`'s
   failures are.
3. **Click handling, guarded by state, not by the widget's `disabled` flag** — the same fix
   already applied to the Gemini button: `if assess_clicked and not running:` before doing
   anything, `if stop_clicked and job is not None: job.cancel_event.set()`. This closes the
   double-submit race the same way `already_asked` closed it for Gemini.
4. **Starting a job**: on `Assess`, do the existing fast/local work synchronously exactly as
   today — `audio_utils.prepare()`, `budget.preflight_stt()` — and **check the session cache
   first**. A cache hit stays fully synchronous (no thread, no poll, no extra rerun): this is
   the deliberate preservation of "one click to retry the same drill sentence," the fastest
   path in the app. Only a cache miss spawns a worker thread (`speech_analyzer.analyse` +
   `db.record_attempt`, both currently in `run_assessment`), stores the `AssessJob`, starts
   the thread, and calls `st.rerun()` to leave the current pass immediately.
5. **While a job is alive**: render a status line ("Assessing… click Stop to cancel"),
   `time.sleep(0.4)`, then `st.rerun()`. This is the poll loop; each iteration is a full,
   cheap script rerun, so a `Stop` click lands on the very next one regardless of whether
   that rerun was triggered by the click itself or by the timer.

### The worker (new, pure — no Streamlit import needed beyond what's already used)

Splits today's `run_assessment` at its natural boundary: the fast, local, already-synchronous
part (`audio_utils.prepare`, `budget.preflight_stt`) stays on the main thread so its errors
still show immediately with no state-machine involved at all. Only the actual network call
and the row write move to the worker:

```
_run_assessment_job(conn, wav_bytes, seconds, reference_text, mode, cancel_event) -> AssessOutcome
```

Reuses `audio_utils.temp_wav`, `speech_analyzer.analyse`, `db.record_attempt`,
`budget.mark_quota_exhausted`, `utils.redact` exactly as `run_assessment` does today — no
reimplementation, just relocation. `db.record_attempt` stays a single call with everything
already computed, so it stays atomic: there is no partial/placeholder row at any point, which
is what makes "no half-written attempt row" trivially true rather than something to defend.

The function this lands in gets one **outermost** `except Exception` around its whole body,
in addition to the specific `except` clauses for the error types `run_assessment` already
handles today — not because any of those are expected to be incomplete, but because this
function now runs unsupervised on a background thread where an uncaught exception simply
kills the thread silently (Python does not propagate a thread's exception back to the
spawner). Without this, a bug here would leave `job.outcome` permanently `None` and the main
thread's poll loop would see `is_alive() == False` with nothing to show — exactly the
"ended unexpectedly" case the render-side defensive check (flow step 1) exists to catch, but
better caught here with a real message than surfaced there as a generic one.

### Cancellation reaching into `speech_analyzer`

`recognise()` and `analyse()` gain two optional keyword args, defaulting to `None` so every
existing call site (tests, scripts, the fixture-replay path) is untouched:

- `on_attempt: Callable[[int], None] | None` — already exists as an internal closure inside
  `recognise()`; now also invoked externally so the worker can learn "a request was actually
  dispatched to Azure" the moment it happens, not after the call returns.
- `cancel_event: threading.Event | None` — checked in exactly two places:
  - **Before dispatch**, at the top of `recognise()`: if already set, raise a new
    `speech_analyzer.Cancelled(reached_azure=False)` without calling Azure at all.
  - **During the wait**, inside `_assess_continuous` only: the current single
    `done.wait(timeout=CONTINUOUS_TIMEOUT_SECONDS)` becomes a loop of short (`0.2s`) waits
    that also checks `cancel_event`; if set, it breaks out, calls
    `recognizer.stop_continuous_recognition_async()` (already happens in the `finally`) and
    raises `Cancelled(reached_azure=True)` — this is the one path where Stop genuinely
    interrupts an in-progress Azure call, because continuous recognition is the one Azure
    API here with a real "stop early" method.

`_assess_single_shot` (Drill) is **not** given a mid-call cancel point: `recognize_once_async()`
is one blocking round trip with no SDK-exposed way to abort it safely mid-flight, and
inventing one (e.g. closing the recognizer from another thread) is exactly the kind of
unverified, risky SDK behavior this project's own standing preference ("verify SDK surfaces
by introspecting the installed package, not from docs or memory") warns against. Per the
confirmed answer: Drill still shows `Stop`, but it takes effect only once the call returns —
`recognise()`'s own cancel check (before dispatch) can still catch a very fast double-click,
and the worker checks `cancel_event.is_set()` again right after `analyse()` returns,
discarding a result that arrived after Stop was clicked even though it couldn't be
interrupted. The UI is honest about this: the cancellation message differs by
`cancel_reached_azure` (see below) rather than implying every Stop is instant.

### The metering/storage rule — decided explicitly

**A cancelled run never writes an `attempts` row and is never billed, regardless of whether
it reached Azure.** Implemented by: `AssessOutcome.cancelled=True` short-circuits the worker
before it ever reaches `db.record_attempt` (see the worker sketch above — the `cancel_event`
check happens *before* the DB write in every path that can set it). This is the plan's
explicit answer to "must also stop the meter charging for work that will not be used."

This does **not** touch the *existing*, unrelated rule that retries/failures which did reach
Azure remain billed: `assessment.attempts` (already multiplied into `audio_seconds` at
`db.record_attempt` time) still counts every dispatch a *non-cancelled* run made, exactly as
`run_assessment` does today — nothing about that path changes. The two rules coexist because
they answer different questions: "how many times did a completed attempt re-upload the
audio" (existing, unchanged) versus "should a *discarded* attempt be recorded or billed at
all" (new: never).

The `reached_azure` flag is kept anyway, but purely for **honest messaging**, not billing:
cancelling before dispatch shows "Cancelled before anything was sent to Azure"; cancelling a
paragraph mid-stream (the one case where real bytes may have already reached Azure before
the abort landed) shows "Cancelled — some audio may already have reached Azure, but nothing
was recorded or billed locally." No new table, no partial-row scheme: the existing
`attempts` table (the only place STT usage is metered — there's no `stt_usage` sibling to
`tts_usage`) is simply never touched by a cancelled run. This mirrors `budget.py`'s own
stated philosophy that this module is "a second line of defence against your own
misconfiguration," not the real spend guarantee (Azure's F0 tier is) — a rare race between a
Stop click and an in-flight dispatch being under-counted locally is an acceptable, bounded
tradeoff for not reintroducing partial-write risk.

## #4 — Reset button

Per the confirmed answer: full reset. One `↺ Reset` button beside `Assess`/`Stop`, disabled
while a job is running (avoids resetting inputs a background job is mid-processing, even
though the job itself only reads the values it was started with and wouldn't actually break).

**Verified against Streamlit's own docs on widget behavior** (not assumed): an argument like
`value=` *is* part of an unkeyed widget's identity, which is why today's `st.text_area(...,
value=default_text)` already resets when the preset selectbox changes — a real but somewhat
incidental mechanism. The officially documented way to programmatically reset a widget is an
explicit `key=` plus mutating `st.session_state[key]` from an `on_click`/`on_change`
callback, specifically *before* the widget is instantiated on the next run — mutating
`session_state` for an already-instantiated widget's key in the same pass raises
`StreamlitAPIException`. So:

- The reference textarea gets `key="reference_text_input"`, no more `value=`.
- The preset selectbox gets `key="preset_choice"` and `on_change=_apply_preset`, where
  `_apply_preset` sets `st.session_state["reference_text_input"]` from the newly chosen
  preset — replacing today's every-rerun `default_text` recomputation with the idiomatic
  callback, and preserving the exact same visible behavior (switching presets replaces the
  text; typing in the box does not get overwritten by an unrelated rerun).
- Two widget-key generation counters live in `session_state` (`recording_generation`,
  `upload_generation`, both new, start at `0`). `st.audio_input` and `st.file_uploader` are
  keyed as `f"audio_input_{recording_generation}"` / `f"uploader_{upload_generation}"` —
  confirmed via Streamlit's own issue tracker that neither widget supports being cleared by
  writing to `st.session_state[key]` directly (open feature request, unresolved as of
  1.61.1), so rebinding the key to force a brand-new, empty widget instance is the only
  reliable way. This is the same mechanism issue #5 needs, built once and shared.
- `_reset_form` (the Reset button's `on_click` callback) sets: `reference_text_input = ""`,
  `preset_choice = "Write my own"`, bumps both generation counters, and clears `last_key` /
  `now_playing` — the "genuine fresh start" the confirmed answer asked for. No explicit
  `st.rerun()` needed: a button click always triggers Streamlit's normal rerun, and an
  `on_click` callback runs *before* that rerun's script body, so every widget it touches
  reads the new value on the very next render.

## #5 — Delete-recording control

A small `🗑️ Delete recording` button rendered directly under `st.audio_input`, shown only
when `audio is not None` (nothing to delete otherwise). Scoped to the recording only — the
reference text is untouched, unlike Reset — because the point is letting someone discard a
bad take and re-record without losing what they already typed. Uses the same
`recording_generation` counter and the same `on_click` pattern as Reset: `_delete_recording`
bumps the counter and clears `now_playing` (a queued player shouldn't survive discarding the
take it came from). The upload widget is unaffected by this control (`file_uploader` already
has its own native per-file remove "×" in Streamlit's UI, which is adequate — the issue is
specifically about the Record field).

## #8 — Collapse 100-score flagged words by default

`speech_analyzer.is_flagged()` flags a word for any of three independent reasons: a
non-`None` `error_type`, an accuracy below `WORD_AMBER`, or a delivery fault
(`delivery_error_types`). A word can score a perfect 100 and still be flagged purely for a
delivery reason (`Monotone`/`UnexpectedBreak`/`MissingBreak`) — this shows up more in
Paragraph mode because continuous recognition is the only path that produces delivery
feedback across a multi-sentence span, but nothing in `is_flagged` is mode-gated, so the fix
isn't either: any 100-scored flagged word, in either mode, is collapsed.

In `render_result`, split the already-computed `flagged` list into `needs_attention`
(`accuracy != 100` or no score) and `perfect_but_flagged` (`accuracy >= 100`). Render
`needs_attention` exactly as today, in the same loop. Render `perfect_but_flagged` (if any)
inside a collapsed `st.expander(f"Scored 100 but still flagged ({n})", expanded=False)`,
continuing the same `index` counter across both groups so `playback_buttons`' per-word
widget keys (`key_prefix=f"word-{index}"`) stay unique — no change to `render_word_card`
itself.

## The 8 code-review findings

All in `ai_coach.py` / `fallback_coach.py` / `app.py`, referencing the line numbers recorded
in `memory-bank/progress.md`. Each fix is narrow and reuses existing types/helpers — no new
abstractions:

1. **`ai_coach.coach()` has no exception handling around
   `compact`/`build_from_compacted`** (lines 309-310), and neither does `app.py`'s
   `coaching_for` for its own direct `fallback_coach.build(...)` call on the offline path.
   Add `fallback_coach.emergency_report(reason: str) -> CoachingReport`: a hardcoded,
   schema-valid report (empty `priority_fixes`, a `stress_and_rhythm` with no issues and a
   generic drill, a short explanatory `overall_comment`/`practice_plan`) that cannot itself
   fail to construct. Wrap the compaction/build pipeline in both `ai_coach.coach()` and
   `app.py`'s `coaching_for` (the `ask_model=False` branch) in `try/except Exception`,
   falling through to `emergency_report(...)` on any failure, logged. This is what actually
   makes "always returns a report" true on the free/offline path, not just Gemini's.
2. **`fallback_coach._substitutions`: `final_cluster` not recomputed when a smeared merge
   keeps the earlier entry** (line ~150). In the `else: found[-1]["_index"] = index` branch,
   also assign `found[-1]["final_cluster"] = entry["final_cluster"]` — `entry` was built
   fresh at the new index in this same iteration, so its `final_cluster` is already correct
   for the position the merge now represents; it was just never propagated onto the kept
   entry.
3. **`ai_coach._classify` doesn't match httpx's real transport exceptions** (line 203).
   Lazily `import httpx` (already an implicit dependency of `google-genai`, confirmed
   importable per the original finding) and check
   `isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, TimeoutError,
   ConnectionError))` — keeping the builtins alongside the httpx types rather than replacing
   them, since nothing guarantees every transport failure the SDK can raise is an httpx type.
4. **Gemini button re-spend after a fallback outcome** (`app.py` line 552). The guard
   currently keys off `cached_source == SOURCE_GEMINI`, so a click that spent a real call but
   fell back (bad JSON, nothing survived validation) leaves the button clickable again. Add a
   second small session cache, `gemini_attempted` (same `OrderedDict` + `lru_put`/`CACHE_LIMIT`
   idiom as `assessments`/`coaching`), marked `True` for `entry.key` the moment `coaching_for`
   decides to make a real call — independent of the outcome. Both the button's `disabled=`
   and `coaching_for`'s internal guard (replacing `already_asked`) key off this instead of
   the source of what came back.
5. **`ai_coach.validated()` only checks `priority_fixes` against `observed_pairs`**; prose
   fields are never checked despite the UI caption claiming otherwise (line 249). Add a
   regex-based scan (`/([^/\s]{1,6})/`, matching the existing IPA-in-slashes convention used
   throughout the UI) over `overall_comment`, `practice_plan`, and each
   `stress_and_rhythm.issues` entry; normalise every matched symbol with the existing
   `_symbol()` and reject the whole report (return `None`, same fall-through behavior as
   today) if any mentioned symbol is absent from the set of expected/produced phonemes in
   `observed_pairs`. Rejecting the whole report rather than trying to edit prose in place is
   deliberate — there's no safe way to remove a clause from a sentence without producing
   broken English, and the offline fallback this triggers is already a complete, correct
   report.
6. **`if not kept and compacted["observed_pairs"]:` misses the empty-`observed_pairs` case**
   (line 276). Replace the condition with `if report.priority_fixes and not kept:` — the
   right question is "did the model claim fixes that all got filtered out", which is
   independent of whether `observed_pairs` itself was empty; the old condition only degraded
   correctly when `observed_pairs` was non-empty.
7. **A `response.model_dump()` failure stores flat-shaped `raw` but keeps
   `source=SOURCE_GEMINI`**, so `report_from_raw` can't re-parse the row later (line 354).
   Rather than changing what `source` is reported as right now (which would make the
   *current* render's caption lie about whether Gemini was actually called), make
   `report_from_raw`'s `SOURCE_GEMINI` branch tolerant: if parsing the full response envelope
   (`candidates[0].content.parts[...]`) fails, fall back to `CoachingReport.model_validate(raw)`
   — the flat shape `ai_coach.coach()` stores in exactly this failure case. Fixes the actual
   defect (a stored row silently becoming unrecoverable) without touching the live UI's
   honesty about what happened during this request.
8. **`fallback_coach._practice_plan`'s `{1: (4,), 2: (2,2), 3: (2,1,1)}[len(fixes)]` is a
   latent `KeyError`** if `MAX_PRIORITY_FIXES` is ever raised (line 410). Replace the table
   with `divmod(4, len(fixes))`-based allocation: `base, extra = divmod(4, count); tuple(base
   + (1 if i < extra else 0) for i in range(count))` — verified to reproduce the exact
   existing outputs for `count` 1, 2, 3, and generalizes to any count without a lookup table.

## Files touched

- `speech_analyzer.py` — `Cancelled` exception; `on_attempt`/`cancel_event` kwargs on
  `recognise()`/`analyse()`; polling wait loop in `_assess_continuous`.
- `app.py` — the assess/stop/reset state machine and buttons; delete-recording control;
  widget-key generation counters; the split-out worker function; 100-score collapsing in
  `render_result`; the `gemini_attempted` guard in `coaching_for`/`render_coaching`.
- `ai_coach.py` — exception handling in `coach()`; `_classify`'s httpx types; `validated()`'s
  prose check and the `not kept` condition; `report_from_raw`'s tolerant `SOURCE_GEMINI`
  branch.
- `fallback_coach.py` — `emergency_report()`; the `final_cluster` propagation fix;
  `_practice_plan`'s general allocation formula.

## Verification

Everything here stays inside the "no API cost, testable with `OFFLINE_MODE=true`" constraint
— including Stop's actual cancellation behavior, which matters because the offline
fixture-replay path (`speech_analyzer._load_fixture`) returns in microseconds. That's fast
enough that a human clicking `Stop` by hand against the offline path has no realistic window
to land the click before the worker thread has already finished — so genuine mid-flight
cancellation is a job for **automated tests with controlled fakes**, not live clicking, and
not a temporary flip to `OFFLINE_MODE=false` (which would need real credentials and defeats
the point). Concretely: a test constructs a fake Azure `SpeechRecognizer`-shaped object (the
existing test suite already does this kind of thing for the fixture/error-path tests) whose
`recognized`/`canceled`/`session_stopped` callbacks are fired under the test's own control,
sets a `cancel_event` before letting the fake's `done` complete, and asserts
`_assess_continuous` raises `Cancelled(reached_azure=True)` without ever reaching
`_raw_json`/appending to `payloads`. No timing, no sleep, no cost.

1. `make test` after each of the 8 review-finding fixes and after the state-machine change —
   existing suite must stay green; add unit tests for: `Cancelled` raised before dispatch
   (`cancel_event` pre-set) and mid-continuous-wait (fake recognizer, as above), the worker
   never calling `db.record_attempt` on any cancelled outcome, `_substitutions`'
   final_cluster propagation, `_practice_plan` for `len(fixes)` up to at least 4,
   `validated()` rejecting a prose-only fabrication, the `gemini_attempted` guard blocking a
   second call after a fallback outcome, and `report_from_raw` recovering a flat-shaped
   `SOURCE_GEMINI` row.
2. Run the app in the browser (`make up`, `OFFLINE_MODE=true`) and drive what's actually
   observable at offline speed — the state-machine's *shape*, not Stop's raw timing:
   - Rapid-double-click `Assess` (as fast as the browser allows) and confirm only one
     assessment is ever recorded — no double-submit. This is checkable even though the job
     itself finishes almost instantly, because the guard is state-based (step 3 of the flow),
     not a race against how fast the job runs.
   - Retry the identical drill sentence twice and confirm the second click is still
     instant (cache hit — no thread, no `Stop` button ever appears) — the friction
     constraint.
   - Use Delete-recording mid-way through filling the form and confirm the reference text is
     untouched; use Reset and confirm recording, text, preset, and the last result all clear.
   - Assess a paragraph whose fixture/fixture-like input carries a delivery fault on an
     otherwise perfect word, and confirm it renders inside the collapsed expander, not the
     main flagged list.
   - Force a Gemini call that falls back (e.g. an injected bad client via
     `scripts/coach_test.py`-style harness) and confirm the "Improve with Gemini" button is
     disabled afterward, in the running app, not just in a unit test.
   - Confirm the SQLite `attempts` table gains exactly one row per completed (non-cancelled)
     assessment — a quick `sqlite3` check against `DB_PATH`, cross-checked with what step 1's
     cancellation tests already assert at the unit level for the cancelled case.
3. Update `memory-bank/progress.md` and `memory-bank/history.md` per this repo's workflow
   once implemented, closing out the "Known issues" section for the 8 findings and recording
   the v0.2.0 work; close GitHub issues #4–#8 and the milestone once verified live.
