# Progress

## Current state

v0.13.0 is implemented: the two-page scope the brief describes, and nothing else. Analyze
records scripted or unscripted audio and shows every documented field Azure returned, the
deterministic coaching, and — on a click — Gemini's prosody annotation. History paginates
every attempt, filters by mode, opens one in place without the inputs, and can delete one.

`make check` (ruff format + ruff + strict mypy + 471 tests) passes. Verified live through
`coach-offline` on 2026-08-25: both modes end to end, the full Azure detail panels, History
paging, mode filter, open, and confirmed delete, with no console errors.

Everything else was deleted behind tag `v0.12.0-full`. `techContext.md` maps each removed
feature to its plan file.

## Next concrete work

Nothing is queued. The next thing is real use: assess real readings and see what the two
pages are actually missing.

## Live evidence still needed

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
