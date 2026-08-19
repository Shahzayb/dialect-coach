# Make the prosody score actionable — delivery faults as a coaching section

Issue #9 ("Optimize the features for `Prosody` too. Currently there's no way to improve it
or get feedback"), milestone v0.4.0.

## Context

Prosody is scored and it is shown — `render_scores` puts it in the breakdown as a banded
bar, `render_error_counts` gives headline counts of Unexpected break / Missing break /
Monotone, and `render_delivery` lists which words carry each. What none of it does is say
what to **do**. "Prosody 76.4" is the one number on the page that cannot be practised, and
the coaching report currently folds delivery into a single `stress_and_rhythm.issues` line
that describes the problem back at the learner and then gives a drill about something else.

The data is already arriving and already parsed. Delivery faults do **not** live in a word's
`ErrorType` — that field carries only `None` / `Mispronunciation` / `Omission` /
`Insertion`. They live under `PronunciationAssessment.Feedback.Prosody`, in
`Break.ErrorTypes` and `Intonation.ErrorTypes`, which `speech_analyzer._delivery_error_types`
(line 404) already reads and `delivery_summary` (line 710) already aggregates. What is
thrown away is the measurement sitting beside each fault: `Break.BreakLength`, and
`Intonation.Monotone.SyllablePitchDeltaConfidence`.

This chunk feeds the aggregate plus those values to the coach as **a distinct section of the
existing compacted payload** — not a new model call, not a second request — and requires a
concrete drill back per fault. The offline coach covers it with templated advice written
once, which is what makes the chunk work with no API key at all.

**Scope, kept honest.** This makes the existing 0–100 actionable: it names the span and
gives something to perform. A later chunk measures rhythm and pitch *numerically*, from
phoneme durations and an F0 track. The two are complementary, not duplicative — the numeric
version is explicitly **not** built here, and nothing in this chunk should compute a pitch
or duration statistic of its own.

## What already exists — reuse, do not rebuild

| Thing | Where | Use it for |
| --- | --- | --- |
| `_delivery_error_types(word)` | `speech_analyzer.py:404` | the fault names; already handles both the SDK-nested and flat-REST shapes, and already filters the literal `"None"` |
| `delivery_summary(words)` | `speech_analyzer.py:710` | `{fault: [words]}`; keep its shape — `render_delivery` and the headline counts read it |
| `_DELIVERY_SENTENCES` | `fallback_coach.py:44` | the `what_happened` sentence per fault, already written and already covered by a test |
| `compact()` | `fallback_coach.py:180` | the shared payload; `ai_coach.build_prompt` JSON-dumps it whole, so a new key needs no prompt plumbing |
| `DELIVERY_LABELS` | `app.py:99` | the heading per fault |
| `validated()` | `ai_coach.py:276` | the anti-fabrication pass — extend it, don't write a parallel one |
| `_groups()` ranking rationale | `fallback_coach.py:249` | the house style for "deterministic order, and say why each term is there" |

No database change: `attach_coaching` stores the report verbatim and this only changes its
shape. No new dependency.

## Step 0, before any code

1. Copy this file to `plans/2026-08-19_prosody-coaching-payload.md` and append a `planned`
   row to `memory-bank/history.md`. (CLAUDE.md §2 — plan mode cannot write there.)
2. **Introspect `BreakLength` in the container** — `azure.cognitiveservices.speech` is not
   installed on the host, so this runs inside the project image, per the standing preference
   for introspecting the installed package rather than trusting docs. See the next section
   for what the answer changes.

## The measurements, and the unit trap

Every `BreakLength` in `tests/fixtures/sample_azure_response.json` is `0`, so the fixture
proves nothing about its unit. Writing "you paused for 480 ms" when the number is in 100-ns
ticks would be a fabricated fact of exactly the kind `ai_coach.validated` exists to prevent
— and it would be *our* fabrication, in the offline templates, where there is no model to
blame.

So the rule for this chunk: **surface the raw number under Azure's own field name, and add a
unit only if step 0 verifies one.** If it does, the templates may say "ms"; if it does not,
they say `BreakLength 4200` and nothing more. Same discipline for
`SyllablePitchDeltaConfidence`: a 0–1 confidence, surfaced as one, with no claim about
which direction means "more monotone".

Consequence, and it is deliberate: **the measurements never enter the ranking.** Ordering
delivery faults by a number whose direction is unverified would silently mis-rank them. The
order is fixed by span size and then a stated precedence (below), and the measurements are
evidence shown to the learner and to the model, not a sort key.

One parsing subtlety the fixture makes visible: `SyllablePitchDeltaConfidence` is present
(`0.17783079`) on words whose `Intonation.ErrorTypes` is `[]`. The per-word parse keeps it
regardless — it is data Azure sent — but **the aggregate must average only over the words
actually carrying that fault**, or every clean attempt would report a monotone confidence.

## Changes

### 1. `speech_analyzer.py` — carry the values through the parser

New `_prosody_detail`, a sibling of `_delivery_error_types` reading the same block, placed
directly beneath it:

```python
def _prosody_detail(word: dict[str, Any]) -> dict[str, float | None]:
    """The numbers beside the delivery faults: BreakLength, and the monotone confidence.

    Read from the same `Feedback.Prosody` block as `_delivery_error_types`, and kept even
    when the word carries no fault — `SyllablePitchDeltaConfidence` is reported on clean
    words too (0.178 throughout the committed fixture). Filtering that is the aggregate's
    job, not the parser's. No unit is asserted for `break_length`: every value in the
    fixture is 0, so the unit is unverified. See the plan's "unit trap" note.
    """
```

- `_normalise_word` (line 432) gains `"prosody_detail": _prosody_detail(word)`.
- `_omission` (line 526) gains `"prosody_detail": {"break_length": None,
  "monotone_confidence": None}`, so every normalised word has the key and no consumer needs
  a `.get` guard for one code path but not another.

New aggregate, beside `delivery_summary` and sharing its docstring's framing:

```python
FAULT_PRECEDENCE = ("UnexpectedBreak", "MissingBreak", "Monotone")

def delivery_faults(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per fault present, with the span and the measurements behind it.

    `delivery_summary` answers "which words"; this answers "which words, and what did Azure
    measure there" — the form the coaching payload needs. Ordered by how many words the
    fault damaged, then by FAULT_PRECEDENCE: a pause in the middle of a phrase costs a
    listener more than a flat one, and a flat phrase is still understood. Deliberately not
    ordered by the measurements — see the plan. A total order, because a report that
    reshuffles between two runs on identical input is not deterministic.
    """
    # -> [{"fault", "words", "break_length_max", "break_length_mean",
    #      "monotone_confidence_mean"}], values rounded, None where the payload had none.
```

`break_length_*` populated only for `UnexpectedBreak`/`MissingBreak`,
`monotone_confidence_mean` only for `Monotone`, each averaged over that fault's span alone.

### 2. `fallback_coach.py` — the payload section, the schema, the templates

**The payload section.** `compact()` (line 180) gains one top-level key beside the existing
`"delivery"`:

```python
"delivery_faults": speech_analyzer.delivery_faults(assessment.words),
```

That is the distinct section the issue asks for, and it reaches Gemini through
`build_prompt` unchanged. The per-word `"delivery"` list inside `flagged_words` stays names
only — the numbers live once, in the aggregate. At most three entries, so
`test_the_payload_sent_on_is_a_fraction_of_the_raw_response` stays true.

**The schema.** One new model, plain types only — no `Optional`, no defaults, no unions —
so `t_schema` conversion into a Gemini response schema stays clean:

```python
class DeliveryDrill(BaseModel):
    """One delivery fault, and something to perform about it."""

    fault: str = Field(description="UnexpectedBreak, MissingBreak or Monotone.")
    span: list[str] = Field(description="The words from this attempt carrying it.")
    what_happened: str = Field(description="One sentence naming the span. No advice here.")
    drill: str = Field(
        description="An exercise the learner performs, naming those words. Not a "
                    "restatement of the problem."
    )
```

`CoachingReport` gains `delivery_drills: list[DeliveryDrill]`.

**The split with `stress_and_rhythm`.** The delivery sentences move **out** of
`_stress_and_rhythm().issues` (line 355–358) and into `delivery_drills`, and its
break-derived `drill` branch (line 382–389) goes with them. `stress_and_rhythm` keeps what
is genuinely its own — weak stressed syllables, the overall prosody-score sentence — and its
remaining two drill branches. Without this move the page says the same thing twice, three
inches apart, and the new section reads as padding.

An attempt whose only problem is delivery now yields `stress_and_rhythm.issues == []`;
`render_coaching` already guards that case (line 1050).

**The templates, written once.** `_DELIVERY_DRILLS: dict[str, str]`, next to
`_DELIVERY_SENTENCES` which keeps supplying `what_happened`:

- `UnexpectedBreak` — "Read the phrase containing {words} straight through once, without
  stopping anywhere inside it. Then read it again and put the only pause at the punctuation.
  Record both and listen for where the break actually landed."
- `MissingBreak` — "Mark the boundary between {words} with a pencil stroke. Read the
  sentence at half speed putting one clear beat there, then at normal speed keeping the same
  beat."
- `Monotone` — "Say {words} three times: once with the pitch rising on the last stressed
  syllable, once falling, once the way you would say it to someone in the room. Record it
  and listen for whether the shape changed at all between the three."

Each formats in the actual span words. The measurement is appended only when present and
non-zero, in Azure's own vocabulary (`"Azure measured BreakLength 4200 there."`), per the
unit trap above.

`_delivery_drills(compacted) -> list[DeliveryDrill]` builds them; a fault with no template
still produces an entry — its sentence plus the generic half-speed read-back — so the
section can never be empty while a fault exists. `build_from_compacted` passes it into
`CoachingReport`; `emergency_report` gains `delivery_drills=[]`.

### 3. `ai_coach.py` — ask for it, then check it

- `SYSTEM_INSTRUCTION` gains one rule, placed after rule 6 and before the word limit:
  write exactly one `delivery_drills` entry for each fault in `delivery_faults` and none for
  a fault that is not there; each `drill` must be something the learner performs, naming the
  words in that fault's span; the measurements are evidence for which fault is worst, not
  numbers to quote back. Rule 7's 450-word limit covers the new section too.
- `validated()` (line 276) gains a delivery pass in the same shape as the fix pass: drop any
  drill whose `fault` is absent from `compacted["delivery_faults"]`, drop span words absent
  from the attempt, drop an entry whose `drill` is blank, and log each drop the way an
  invented fix is logged. The prose sweep at the end extends over `what_happened` and
  `drill`, so a phoneme fabricated inside a drill rejects the report exactly as one in the
  practice plan does.
- **Backfill rather than reject.** If the payload carried faults and no model drill
  survives, fill `delivery_drills` from `fallback_coach._delivery_drills(compacted)` instead
  of failing the whole report. That makes "a fault in the data always produces advice" a
  property of the code rather than of the model — which is exactly the exit criterion, and
  it must not depend on a network call succeeding. Note this is deliberately *unlike* the
  `priority_fixes` rule (all fixes dropped → whole report rejected): a hollowed-out fix list
  means the model was describing some other recording, whereas the delivery templates are
  correct on their own and there is no reason to throw away good fixes to avoid them.
- `report_from_raw` (line 328) back-compat: reports stored by v0.1.0–v0.3.0 have no
  `delivery_drills` key and would fail validation against a required field, and the function
  swallows that into `None` — a silently unreadable row. Insert `[]` when the key is missing
  on either shape before validating.

### 4. `app.py` — render it

- New `render_delivery_drills(report)` called from `render_coaching` (line 995) between the
  priority-fix cards and the stress-and-rhythm block — the same "what to do before the
  evidence for it" ordering the section already follows. Per entry: the `DELIVERY_LABELS`
  heading, the span words as a caption in the `render_fix` style ("In this attempt: …"),
  `what_happened`, then `**Drill** — …`. Nothing renders at all when the list is empty; no
  "no problems found" line, because `render_delivery` further down already says that.
- `render_delivery` (line 1163) gains the measurements beside each fault's word list, read
  from `speech_analyzer.delivery_faults`, so the coaching section and the evidence panel
  cannot show different numbers for the same fault.
- Escape span words through `html.escape` wherever they land in a markup string, as
  `render_fix` does.

### 5. `speech_analyzer._load_fixture` — an offline payload that has a fault

The committed fixture is clean on Break and Intonation, so offline the app has nothing to
demonstrate. `_load_fixture` (line 313) reads an optional `OFFLINE_FIXTURE` filename before
falling back to the per-mode default in `FIXTURES`; the name is resolved inside
`FIXTURE_DIR` and refused if it escapes it (`Path.is_relative_to`), so the setting selects a
committed fixture and cannot become a file-read primitive.

Commit `tests/fixtures/synthetic_delivery_faults.json`: a copy of the drill fixture with
three words' `Feedback.Prosody` blocks edited to carry an `UnexpectedBreak`, a
`MissingBreak` and a `Monotone` with non-zero `BreakLength` and
`SyllablePitchDeltaConfidence`, and a lowered `ProsodyScore`. A top-level `"_synthetic"` key
states in the file that it is hand-built and not a capture — the parser only `.get`s the
keys it knows, so an extra one is inert. The name is chosen so it can never be mistaken for
a captured payload in a directory where everything else is one.

Document `OFFLINE_FIXTURE` in `.env.example` (under `OFFLINE_MODE`, line 54) and in the
README settings table (line 72), marked as a development setting.

## Tests — offline, no keys, no network

The committed fixture contains **no** delivery fault, so every case below except the two
parse assertions is against a hand-built payload. Each such test says "Synthetic:" in its
docstring, following `test_delivery_faults_become_issues_naming_the_words`.

`tests/test_parsing.py`
- `_prosody_detail` reads `BreakLength: 0` and `SyllablePitchDeltaConfidence: 0.17783079`
  off the **real** committed fixture — the one claim here that is proven rather than
  constructed — and returns `None`/`None` when the `Feedback` block is absent.
- `delivery_faults` aggregates a synthetic three-fault payload: right spans, right
  ordering, `monotone_confidence_mean` averaged over the Monotone words only and **not**
  over the clean ones, `break_length_*` absent for Monotone.
- An omitted word carries `prosody_detail` with both values `None`.
- `OFFLINE_FIXTURE` selects the named file; a name containing `..` or an absolute path is
  refused.

`tests/test_fallback_coach.py`
- A synthetic Monotone span produces a `DeliveryDrill` whose `span` **and** `drill` name the
  words — this is the exit criterion expressed as a unit test.
- All three faults have a template; a fault with no template still yields an entry.
- No fault yields `delivery_drills == []`.
- **Rewrite `test_delivery_faults_become_issues_naming_the_words`** (line 204) to assert on
  `delivery_drills` and to assert delivery sentences no longer appear in
  `stress_and_rhythm.issues` — it currently asserts the opposite and will fail.
- Two builds of the same payload produce identical bytes.
- The existing size-ratio test still passes with the new section present.

`tests/test_ai_coach.py`
- A drill for a fault absent from `delivery_faults` is dropped.
- Faults present with no usable model drill are backfilled from the templates, and the
  surviving `priority_fixes` are untouched.
- A fabricated phoneme inside `drill` or `what_happened` rejects the whole report.
- `build_prompt` output contains the `delivery_faults` section.
- `report_from_raw` re-reads a stored v0.3.0-shaped report with no `delivery_drills` key,
  for both sources.
- `types.GenerateContentConfig(response_schema=CoachingReport)` still constructs with the
  new nested model — the public-API check that already exists, re-run against the new shape.

`tests/test_app.py`
- With a synthetic assessment seeded, the drills block renders, names the span, and shows
  the drill offline with no key.
- The evidence panel shows the measurements.
- A report with `delivery_drills == []` renders no delivery heading at all.

## Verification

```bash
make test
```

**Exit criterion, offline and first.** Run the container with `GEMINI_API_KEY` unset and
`OFFLINE_FIXTURE=synthetic_delivery_faults.json`, drive the real UI in the browser, and
confirm the report names the Monotone span and gives a drill for it, with the caption still
saying the offline coach wrote it. This is the issue's stated exit: *a recording containing
a Monotone span produces advice that names the span, offline, with no API key set.*

Then, the deliberate spend, agreed with the user:

- **One live recording of the weather text read to provoke all three faults** — pause in the
  middle of a phrase, run two sentences together with no pause, stay flat throughout. ~13 s
  of 18,000 STT seconds. If Azure returns any fault, capture it as a committed fixture and
  the coverage stops being synthetic — and the `BreakLength` unit question may answer itself
  from a non-zero value. If Azure returns none, say so plainly and leave the gap standing in
  `progress.md` where it is already recorded; do not report it closed.
- **One Gemini call** on that attempt, to confirm the model honours the new schema section
  and that `validated` accepts real drills rather than stripping them all and backfilling.

## Risks worth naming

- **The live read may not trigger anything.** Azure decides what counts as a break or a
  monotone span; a human trying to sound bad is not a guarantee. The synthetic coverage is
  what the chunk actually rests on, and it must stand on its own.
- **`BreakLength` may stay unitless.** Then the copy stays in Azure's vocabulary. That is a
  worse sentence and a true one.
- **The `stress_and_rhythm` move is a behaviour change**, not only an addition: one existing
  test asserts the old placement and is rewritten above. Any stored report re-read after
  this lands still validates, because the field is unchanged — only what the offline coach
  puts in it changes.

## Explicitly not in this chunk

Numeric rhythm and pitch measurement from phoneme durations or an F0 track; minimal-pair or
drill audio playback; a second model call; any change to `db.py` or its schema; wiring
`report_from_raw` into the running app (a known gap, recorded in `progress.md`, and not this
issue's).

## Commits

Conventional messages, one per landed chunk, on the current branch:

1. `feat: parse break length and pitch delta confidence from the prosody feedback`
2. `feat: send delivery faults as their own coaching payload section`
3. `feat: write a drill for every delivery fault in the offline coach`
4. `feat: require and validate delivery drills from the model coach`
5. `feat: render the delivery drills in the coaching report`
6. `feat: let OFFLINE_FIXTURE select the replayed payload` (+ the synthetic fixture)
7. `docs: record the prosody coaching section in the memory bank`

## When it lands

Update `memory-bank/techContext.md` (the new parser fields and aggregate, the payload
section, the report-model change and its back-compat shim, `OFFLINE_FIXTURE`, and whatever
step 0 settled about `BreakLength`) and `progress.md` (what works; and whether the live
attempt closed the captured-delivery-fault gap or left it open — that gap is currently
recorded under *Known issues* and must be updated either way). Move the history row to
`implemented`. Bump `pyproject.toml` to `0.4.0`, tag, push tags,
`gh release create v0.4.0 --generate-notes`, close #9 with a comment pointing at what
implemented it, and close milestone v0.4.0.
