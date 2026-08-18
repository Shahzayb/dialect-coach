# Progress

## Current focus

Building the app one chunk at a time. Assessment works end to end and is persisted; the
analysis is not yet turned into coaching, which is the thing the tool exists to do.

## Next concrete step

**Agreed 2026-08-18: make the diagnosis legible and audible** — master plan §11's UI plus
§6's "Hear it" playback, as one chunk.

Why this before coaching. The brief's problem statement is "I can't hear the difference
between my pronunciation and a native speaker's", and the two core requirements that answer
it — show expected vs. actual sound for every flagged word, and hear the correct
pronunciation next to my own recording — are both presentation, not analysis. The data for
the first is already parsed and stored and is currently rendered as a plain table; the
second needs only Azure neural TTS, whose meter and `tts_usage` table already exist.

The two halves belong in one chunk because a "Hear it" button lives *next to* each flagged
word: splitting them means rebuilding the same render path twice.

In scope: colour-coded reference text against the thresholds already in `utils.py`, the
reference-vs-heard diff, expected IPA → produced IPA per flagged word, the delivery panel
aggregating `UnexpectedBreak`/`MissingBreak`/`Monotone`, `tts.py`, per-word and
full-paragraph playback cached in `st.session_state` by `(voice, text)`, and the user's own
recording beneath it for back-to-back comparison.

Out, on the user's explicit instruction: `phoneme_reference.py`, `fallback_coach.py`,
`ai_coach.py` — the whole coaching layer waits for a later session. It is worth building on
top of a legible diagnosis rather than underneath one, and `attach_coaching` and the
`gemini_raw_json` column are already waiting for it, so deferring it costs no migration.

## Active plan

`plans/2026-08-18_azure-analysis-core.md` — complete.
`plans/2026-08-17_project-scaffold.md` — complete.

## What works

Record or upload a drill sentence or a paragraph, get real Azure scores down to the
phoneme, and every attempt is stored in local SQLite with both raw API responses kept
verbatim. Verified end to end against a real recording: `make up`, `make test` (108 tests,
offline, no keys), and the F0 refusal, which `OFFLINE_MODE` correctly bypasses.

Not built: Gemini coaching and its offline fallback, `phoneme_reference.py`, TTS/"Hear it",
Mode C (unscripted), and the rich §11 UI — colour-coded reference text, the
reference-vs-heard diff, and the delivery panel.

## Known issues

- `pydub` 0.25.1 emits `SyntaxWarning: invalid escape sequence` on import under 3.12.
  Cosmetic, upstream, no action. The `audioop` DeprecationWarning is filtered in
  `pytest.ini` for the same reason.
- The multi-utterance merge is only covered by synthetic payloads. The captured 12.8 s
  recording came back as a single utterance in continuous mode, so the real multi-utterance
  path has never run against live data. A longer paragraph recording would close this.
- The captured recording contains no `UnexpectedBreak` / `MissingBreak` / `Monotone`, so
  delivery-fault aggregation is covered by a hand-built payload marked synthetic, not by a
  captured one.

## Dead ends

- **Reading `word["ErrorType"]` from the Azure payload.** It sits inside the word's
  `PronunciationAssessment`, so the top-level read silently returns nothing and every word
  parses as clean. Not worth retrying — the docs' flat REST example is what misleads here.
- **`enable_content_assessment_with_topic`** is not in SDK 1.51.1 despite the master plan
  citing it for Mode C. Do not plan Mode C's content scoring around it without checking
  first.

## Standing preferences

- Project memory lives in this repo's `memory-bank/`, per `.claude/skills/memory-bank/SKILL.md`.
- Take one chunk of work at a time, plan it in its own dated file, then implement only that.
- **Never install anything globally.** Docker is the preferred run path; a project-local
  `.venv` is the acceptable alternative.
- Commit in chunks as work lands, not one commit at the end.
- **Python over `.sh` for anything with branching/conditionals.** Trivial one-liners (a
  single `docker compose up --build`) can stay as a Makefile recipe; put real logic in a
  `scripts/*.py` file instead, as `scripts/setup.py` does.
- Verify library versions and API surfaces against current sources rather than recalling
  them — the pins in the original design were already stale.
- **Build parsers against a captured payload, not documentation.** The real Azure response
  differs from the documented shape in ways that fail silently rather than loudly.
- Spend API quota deliberately and say so, not incidentally: two calls captured both
  fixtures, and every guard now also applies to the capture script.
- **The app runs locally. Deploying it is not a goal** — treat hosting as an option left
  open for someone else, never as a requirement to design around. See `techContext.md`.

## How the direction has evolved

- 2026-08-17 — Docker became the primary run path mid-implementation, to keep the host
  clean and to pin the Azure SDK's native dependencies alongside the Python version.
- 2026-08-17 — A local database is now in scope. The brief previously ruled out stored
  history entirely; SQLite is the chosen engine, and `projectbrief.md` was updated on the
  user's instruction. What gets stored is still open.
- 2026-08-18 — What the database stores is settled: **both raw API responses, verbatim**,
  on the user's instruction. The monthly usage meter is derived from that same table, so
  `.usage.json` and `BUDGET_STATE_PATH` were dropped rather than kept as a second store
  that could disagree with it.
- 2026-08-17 — Hosting dropped as a goal: the tool is for local use. The original design
  treated a Hugging Face Space as the target and derived real requirements from it
  (ephemeral-filesystem handling for the usage meter, cold-start wake time, a private
  Space). Those requirements are gone; the deploy artefacts stay only as an option.
