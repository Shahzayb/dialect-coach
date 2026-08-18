# Progress

## Current focus

Building the app one chunk at a time. The diagnosis is legible and audible, and the
coaching layer now turns it into something to practise. What remains is Mode C
(unscripted) and the fixes tracked under Known issues below.

## Releases

**v0.1.0 — 2026-08-18.** The coaching layer, merged via
[PR #19](https://github.com/Shahzayb/pronunciation-analyzer/pull/19), tagged, and released
(`gh release create v0.1.0`). References #9 (prosody feedback) in the release notes without
closing it — `stress_and_rhythm` addresses the "no way to get feedback" complaint, but the
issue is left for the user to close once satisfied. Released with the 8 code-review findings
below still open, on the user's explicit instruction (fix-after rather than fix-before).

## Next concrete step

**Fix the 8 confirmed code-review findings** (see Known issues) before the next feature
chunk — the two most severe touch the coaching layer's core safety promise, not just
polish: `ai_coach.coach()`'s compaction/build pipeline has no exception handling at all
(breaks "always returns a report" on the free path too, not just Gemini's), and the
"Improve with Gemini" button can be repeat-clicked to re-spend a real call whenever the
previous click consumed one but still fell back.

**After that, Mode C (unscripted speech)** — free speech scored on vocabulary, grammar and
topic, not just a script. Blocked on a real question, not busywork:
`enable_content_assessment_with_topic` does not exist in SDK 1.51.1 despite the master plan
citing it (see Dead ends below), so Mode C's content scoring needs another route found and
verified before it can be planned. `UNSCRIPTED_TWO_PASS` is defined and priced by
`budget.passes_for` but unread by any recognition code yet.

## Active plan

`plans/2026-08-18_coaching-layer.md` — complete.
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

A second review pass driven against the running app found four more, all fixed and
re-verified live. Failure paths were exercised by starting the app with a deliberately
invalid `AZURE_TTS_VOICE`, which is a cheap way to reach the error branches without
waiting for a real outage — worth reusing. Omissions were exercised by adding a word to
the reference text that the recording does not contain; Azure marked it `Omission` itself,
confirming `enableMiscue` really is honoured in drill mode.

Total spend across all live testing: 64 s of 18,000 STT seconds and 339 of 500,000 TTS
characters.

**The coaching layer** turns the diagnosis into a report: 2-3 sentences on the attempt, up
to three priority fixes (expected → produced, affected words, why it matters, articulation,
minimal pairs), stress-and-rhythm issues with a drill, and a five-minute practice plan
naming specific words from the attempt. Rendered directly under the metric row — what to do
before the evidence for it — with the top fixes as bordered cards, never a raw model text
blob. The offline coach (`fallback_coach`) writes it for free on every assessment, with no
key and no network; "✨ Improve this with Gemini" is a button that spends one free-tier call
and replaces it in place, with a caption stating up front that a click sends the compacted
analysis and the reference text to Google, never the audio. A visible caption always says
which coach wrote the report on screen.

Verified live on 2026-08-18 with `scripts/coach_test.py`, which spends no Azure quota (it
replays the committed fixture the way `OFFLINE_MODE` does) and one real Gemini call: the
schema was honoured, no phoneme absent from the Azure data survived into the report, the
~39 kB raw response compacted to ~1.8 kB sent, and the stored payload re-parsed back into
the same report. The exit criterion — a complete, useful report with `GEMINI_API_KEY`
deliberately unset — was verified in a running container via the browser tool: uploading
the captured recording and assessing it against its own reference text produced the full
report, correctly naming `/θ/ → /s/` on "thursday" as the flagship fix, entirely offline.

One thing the browser check caught that the offline test suite could not: a click on
"Improve this with Gemini" is handled in the same Streamlit rerun that renders the button,
so the on-screen button still shows as enabled until the *next* rerun — a second click
before then would have bought a second call. Fixed by moving the spend guard into
`coaching_for` itself (`already_asked`), not left on the button's `disabled` flag alone.

`make test` is 247 tests, all offline with no keys and no network.

Not built: Mode C (unscripted).

## Known issues

**Code review findings against the coaching layer, 2026-08-18, all confirmed, none fixed
yet — released anyway in v0.1.0 on the user's instruction (fix-after, not fix-before):**

- `ai_coach.coach()` (line 309-310) runs `fallback_coach.compact()`/`build_from_compacted()`
  before any try/except, and `app.py`'s `coaching_for` never wraps the call either — a bug
  anywhere in the compaction/grouping pipeline crashes the whole render, on the free/offline
  path too, not just Gemini's. Breaks the module's own "always returns a report" guarantee.
- `fallback_coach._substitutions` (line 150): when a "smeared" duplicate phoneme entry
  merges and the kept entry is the earlier, non-final one (because it scored worse), its
  `final_cluster` flag is never recomputed for the new merged position — the swallowed-
  final-cluster note silently doesn't fire when it should. Reproduced with a concrete input.
- `ai_coach._classify` (line 203): `isinstance(exc, (TimeoutError, ConnectionError))` does
  not match httpx's actual transport exceptions (verified against the installed SDK —
  `httpx.TimeoutException`/`ConnectError` are not subclasses of the builtins), so real
  network failures are classified as permanent and skip the retry the docstring promises.
- `app.py` `coaching_for`/the Gemini button (line 552): the disabled flag and the spend
  guard both key off `source == SOURCE_GEMINI`, so any outcome that consumed a real call
  but still fell back (malformed JSON, `validated()` rejecting every fix) leaves the button
  clickable again — repeat clicks can re-spend real Gemini calls indefinitely.
- `ai_coach.validated()` (line 249) only checks `priority_fixes` against `observed_pairs`;
  `overall_comment`, `stress_and_rhythm.issues` and `practice_plan` are never checked, even
  though the UI caption claims every unsupported sound was removed.
- `ai_coach.validated()` (line 276): `if not kept and compacted["observed_pairs"]:` doesn't
  fall back when `observed_pairs` is empty but the model still fabricated fixes — returns
  `priority_fixes=[]` under `source=SOURCE_GEMINI` with the model's original prose intact.
- `ai_coach.coach()` (line 354): when `response.model_dump()` raises, `raw` falls back to
  the flat report shape but `source` stays `SOURCE_GEMINI` — `report_from_raw` can't
  re-parse that stored row later, silently losing an already-shown report.
- `fallback_coach._practice_plan` (line 410): `{1: (4,), 2: (2, 2), 3: (2, 1, 1)}[len(fixes)]`
  is a latent `KeyError` if `MAX_PRIORITY_FIXES` is ever raised without updating this table.

Also found, lower severity: `app.py`'s word card shows the raw unsmeared duplicate phoneme
that the coaching report collapses — the two views can disagree on how many things went
wrong in one word (directly observed live). `ai_coach.py`'s `_client`/`_config`/`_call` lack
type hints (violates CLAUDE.md's "enforce type hints" rule). `ai_coach.report_from_raw` is
unreachable from the running app — nothing in `app.py` calls it, so a Gemini report evicted
from the session cache or lost to a restart can't be recovered from the database despite
being stored specifically for that. `app.py`'s `if entry.attempt_id:` treats an id of `0` as
absent rather than checking `is not None` (PLAUSIBLE, low-probability trigger).

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
