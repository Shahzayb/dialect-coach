# Progress

## Current state

v0.13.0 is implemented: the two-page scope the brief describes, and nothing else. Analyze
records scripted or unscripted audio and shows every documented field Azure returned, the
deterministic coaching, and — on a click — Gemini's prosody annotation. History is one
`st.dataframe` — attempt text, Pron/Accuracy/Fluency, date, type, audio length and Live vs
Fixture replay — with a filter per column, inline Open and Delete buttons, and the grid's own
scrolling. Opening one renders it in place without the inputs; deleting one asks first.

The app is named Dialect Coach and the page is `layout="wide"`. Analyze's Assess/Stop/Reset
row is a horizontal container, so Stop can come and go without leaving a hole. While an
assessment runs there is exactly one indicator — an `st.status` beside the buttons; Streamlit's
own toolbar chip is hidden by CSS because the poll loop keeps it permanently lit.

`make check` (ruff format + ruff + strict mypy + 480 tests) passes. Verified live through
`coach-offline` on 2026-08-26: the History table renders every column, the text filter narrows
it to "1 attempt of 38", and switching tabs keeps the grid sized. See the caveat below about
what a browser cannot verify here.

Everything else was deleted behind tag `v0.12.0-full`. `techContext.md` maps each removed
feature to its plan file.

## Next concrete work

Nothing is queued. The next thing is real use: assess real readings and see what the two
pages are actually missing.

## Live evidence still needed

- The History table's Open and Delete buttons have never been clicked. `st.dataframe` renders
  to a canvas, so neither AppTest nor a browser driver can reach a cell — even column-header
  sorting does not respond to a synthetic click. The routing from "row N as rendered" to an
  attempt id is unit-tested against a stand-in session state, and everything on the far side
  of the click (the confirm step, the delete, the detail view) is covered by driving the
  session keys directly. What is unproven is only Streamlit's own click delivery. A human
  clicking both icons once settles it.
- The prosody annotation has never run against a live Gemini call. Offline verification
  covered the refusal path only; `scripts/coach_test.py` exercises the real one for the price
  of one free-tier call.
- Per-word "how you said it" clips were verified against a synthetic recording whose length
  did not match the fixture's offsets, so most spans correctly fell outside the audio. A real
  recording is what proves the common case rather than the guard.
- Azure has produced real `Monotone` faults, but no real `UnexpectedBreak` or `MissingBreak`.
  Treat those two as service behaviour unless a real capture proves otherwise.
- Unscripted plumbing and two-pass billing were verified with synthesized input. A human
  spontaneous baseline and repeat measurement are still required.

## Open decisions

- Whether losing Azure's own miscue detection costs anything in practice. Scripted assessment
  is continuous-only now, so every omission and insertion comes from the local diff and
  completeness is recomputed — on the committed fixture that moved completeness from Azure's
  85 to 100. Watch for a real reading where the diff and the ear disagree.
- Whether `MAX_ANNOTATED_WORDS` (400) is the right cut. Untested against a long reading.

## Standing preferences

- Local-first; no hosting, cloud storage, accounts, or sync.
- Never install globally. Use Docker or a project-local Python 3.12 environment.
- Prefer Python to a shell script once branching is needed.
- Use captured payloads and installed-SDK introspection, not recalled documentation.
- Do not create a helper, service, wrapper, or dependency until existing code was checked for
  an appropriate home.
- Verify UI work through the `coach-offline` launch config. The normal one spends Azure quota.

## Historical pointers

Detailed implementation narratives, experiments, test counts, and dead ends are deliberately
kept in `plans/` and indexed by `history.md`. Add a fact here only while it changes the next
action; promote a durable constraint to `techContext.md` and then remove it from this file.
