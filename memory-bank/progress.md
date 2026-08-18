# Progress

## Current focus

Building the app one chunk at a time. The diagnosis is now legible and audible; turning it
into coaching is the thing the tool still does not do.

## Next concrete step

**The coaching layer** — master plan §7: `phoneme_reference.py`, `fallback_coach.py`,
`ai_coach.py`. Deferred until now on the user's explicit instruction, so that it would be
built on top of a legible diagnosis rather than underneath one. Nothing blocks it:
`db.attach_coaching` and the `gemini_raw_json` / `coach_source` columns have existed since
schema version 1, so it is an UPDATE and not a migration.

Order worth keeping when it is planned: `phoneme_reference.py` and `fallback_coach.py`
first, `ai_coach.py` second. The master plan is explicit that the fallback "must be good
enough to use permanently" because the free Gemini tier will run out — which makes it the
primary path, not the degraded one, and it is fully testable offline against the committed
fixture. `GEMINI_API_KEY` and `GEMINI_MODEL` are still unread by any code.

Two things to re-verify rather than recall when that chunk starts: the Gemini model ID
(`gemini-3.6-flash` was live on 2026-08-17 and these retire without much notice), and
`response_schema` support in google-genai 2.18.1.

## Active plan

`plans/2026-08-18_legible-audible-diagnosis.md` — complete.
`plans/2026-08-18_azure-analysis-core.md` — complete.
`plans/2026-08-17_project-scaffold.md` — complete.

## What works

Record or upload a drill sentence or a paragraph and get real Azure scores down to the
phoneme, rendered as: the metric row, a script-versus-heard diff, colour-coded reference
text with the score on hover, a card per flagged word naming the sound actually produced in
place of the target (`/θ/ → /t/`, not "your /θ/ scored 41"), the syllable/stress line, and
the delivery panel. "Hear it" and "Hear it slowly" synthesise a native rendering — per word
and for the whole text — with your own recording directly beneath for back-to-back
comparison. Every attempt is stored in local SQLite with both raw API responses kept
verbatim.

Verified end to end, offline and online. `make test` is 161 tests with no keys and no
network. The online run on 2026-08-18 used the real `.env` and the 12.8 s weather recording:

- The F0 guard refused to start at `AZURE_TIER_CONFIRMED_F0=false`, as designed, and the
  acknowledgement was given by the user rather than assumed. It was passed to the container
  as an environment variable rather than written into `.env`, so the file still says false.
- Live assessment returned `pron_score` 83.0, accuracy 89.0, **prosody 76.4** — prosody is
  genuinely populated, not blank.
- Live TTS returned real audio: RIFF WAV, 24 kHz mono, 1.04 s for one word, 7.9 s for the
  whole text. `audio_config=None` is confirmed necessary and sufficient.
- The slow path returned 1.6 s against 1.04 s for the same word — the 1.54× that
  `rate="-35%"` predicts, so the SSML reaches Azure intact.
- **The meter charged once per distinct phrase, not once per click.** Four clicks produced
  three `tts_usage` rows (8 chars for "thursday", 167 for its SSML, 135 for the whole text);
  the repeat click was served from the session cache and charged nothing.
- Exactly one synthesised player renders at a time, and the two offline replays sitting in
  the table are correctly excluded from the STT meter — 12.82 s charged, not 16.82 s.

Not built: Gemini coaching and its offline fallback, `phoneme_reference.py`, and Mode C
(unscripted).

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
- The reference text sent to TTS is the *script*, not what was heard, so whole-text
  playback always renders the intended reading. That is the point, but it means a
  paragraph's playback does not line up word-for-word with a recording that omitted words.

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
- **Verify SDK surfaces by introspecting the installed package**, not from docs or memory.
  The `SpeechSynthesizer` default-speaker trap was found by printing the constructor
  signature in the project image, and it would not have been found by reading a sample.
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
