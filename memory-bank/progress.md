# Progress

## Current state

v0.12.0 is implemented. The app supports scripted (Modes A/B) and unscripted (Mode C)
assessment, deterministic and Gemini-enhanced coaching, local recordings and history,
benchmark progress, perception practice, shadowing, the four-rung practice ladder, and guarded
accent measurement/resynthesis. The implementation has passing offline tests, ruff, and mypy
at the last recorded implementation point; re-run `make check` before relying on that status.

## Next concrete work

1. Retain or implement only the Analyze page: scripted/unscripted recording, Azure results and
   scores, plus flagged-word side-by-side native comparison (audio, IPA, and lay mouth guidance).
2. Retain or implement only the paginated History page, where opening an item shows Analyze with
   input controls hidden.
3. Treat all other features as deferred. Do not remove code until that later implementation
   session explicitly decides what to retain versus delete.

## Live evidence still needed

- The benchmark trajectory has too few real points for a trend.
- The perception trainer's graduation rule has not fired in normal use.
- Azure has produced real `Monotone` faults, but no real `UnexpectedBreak` or `MissingBreak`.
  Treat those two as service behaviour unless a real capture proves otherwise.
- Simultaneous shadowing has produced worse delivery for this speaker; echo is workable but is
  not assessable. Do not claim transfer, fatigue, or a design failure from the small sample.
- Mode C plumbing, two-pass billing, and Gemini content scores were verified with synthesized
  input. A human spontaneous baseline and repeat measurement are still required.
- Accent calibration and resynthesis were validated once on a real voice. More real samples are
  needed before treating the noise floor, model reference, or trajectory charts as stable.

## Open decisions

- Shadowing: retain the current simultaneous-only assessed path, add assessable echo, or change
  the transfer criterion only after sufficient observations. This is intentionally unresolved.
- Break-fault confidence: Azure sends continuous break confidence but has not emitted the
  corresponding fault labels in real captures. Do not surface it as an error metric without a
  defined interpretation and validation plan.
- Content scoring remains Gemini-based because Azure content assessment is retired. Keep its
  provenance distinct from acoustic assessment.

## Standing preferences

- Local-first; no hosting, cloud storage, accounts, or sync.
- Never install globally. Use Docker or a project-local Python 3.12 environment.
- Prefer Python to a shell script once branching is needed.
- Use captured payloads and installed-SDK introspection, not recalled documentation.
- Do not create a helper, service, wrapper, or dependency until existing code was checked for
  an appropriate home.

## Historical pointers

Detailed implementation narratives, experiments, test counts, and dead ends are deliberately
kept in `plans/` and indexed by `history.md`. Add a fact here only while it changes the next
action; promote a durable constraint to `techContext.md` and then remove it from this file.
