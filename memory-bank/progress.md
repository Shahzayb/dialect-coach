# Progress

## Current focus

Scaffolding, one chunk at a time. The runnable scaffold is done; nothing functional is
built yet.

## Next concrete step

_Proposed, not yet agreed:_ create `tests/fixtures/sample_azure_response.json` — a realistic
saved Azure pronunciation-assessment payload — and build parsing/normalisation against it.
It unblocks the whole parsing, colouring, and fallback-coaching layer without spending any
of the 5-hour monthly Azure quota, so it comes before any code that calls Azure.

## Active plan

`plans/2026-08-17_project-scaffold.md` — complete.

## What works

`docker compose up --build` serves a placeholder Streamlit page on port 8501 that renders.
Python 3.12 and every runtime dependency, including the native Azure Speech SDK, import
inside the container. That is all — no recording, no assessment, no coaching.

## Known issues

- `pydub` 0.25.1 emits `SyntaxWarning: invalid escape sequence` on import under 3.12.
  Cosmetic, upstream, no action.

## Dead ends

_None recorded._ Record failed attempts here with whether they are worth retrying.

## Standing preferences

- Project memory lives in this repo's `memory-bank/`, per `.claude/skills/memory-bank/SKILL.md`.
- Take one chunk of work at a time, plan it in its own dated file, then implement only that.
- **Never install anything globally.** Docker is the preferred run path; a project-local
  `.venv` is the acceptable alternative.
- Commit in chunks as work lands, not one commit at the end.
- Verify library versions and API surfaces against current sources rather than recalling
  them — the pins in the original design were already stale.
- **The app runs locally. Deploying it is not a goal** — treat hosting as an option left
  open for someone else, never as a requirement to design around. See `techContext.md`.

## How the direction has evolved

- 2026-08-17 — Docker became the primary run path mid-implementation, to keep the host
  clean and to pin the Azure SDK's native dependencies alongside the Python version.
- 2026-08-17 — Hosting dropped as a goal: the tool is for local use. The original design
  treated a Hugging Face Space as the target and derived real requirements from it
  (ephemeral-filesystem handling for the usage meter, cold-start wake time, a private
  Space). Those requirements are gone; the deploy artefacts stay only as an option.
