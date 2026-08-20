# dialect-coach

A single-user English pronunciation and delivery coach. It records spoken en-US English,
analyses it at the phoneme, syllable, word and prosody level with Azure Speech
pronunciation assessment, and turns that raw analysis into specific coaching — naming
which sound in which word failed and what was produced instead.

Personal training tool, not a product. It runs locally. Diagnostic specificity over polish:
no accounts, no persistent audio storage.

**Status: the diagnosis and the coaching both work.** You can record or upload a drill
sentence or a paragraph and get real Azure scores down to the phoneme, rendered as
colour-coded reference text, a script-versus-heard diff, and a card per flagged word naming
the sound you actually produced in place of the target — plus "Hear it" playback of a native
rendering, at normal speed or slowed, next to your own recording. Under it, a coaching
report names the top substitutions worth practising, with articulation notes and minimal
pairs, built from the Azure data alone and free on every attempt; a button offers to spend
one Gemini call improving it, and never fabricates a sound the Azure data did not report.
Every attempt, and whichever coach wrote about it, is kept in a local SQLite file. Not
built yet: unscripted mode.

## Running it — Docker (preferred)

Docker is the supported path: nothing is installed on the host, and the container matches
the Python version and native libraries the app needs.

```bash
make setup   # first time only: creates .env, checks docker is available
make up      # docker compose up --build
```

The app serves on http://localhost:8501. The source directory is mounted, so edits reload
without a rebuild; rebuild only when `requirements.txt` changes. Stop it with `make down`.

## Running it — local Python (optional)

Only if you would rather not use Docker. Two host requirements:

- **Python 3.12** — `pydub` needs the stdlib `audioop` module, removed in 3.13.
- **`ffmpeg`** on PATH (`brew install ffmpeg` on macOS).

Everything installs into a project-local `.venv/`, never globally:

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

```bash
.venv/bin/streamlit run src/app.py
```

If you use `uv`, `uv venv --python 3.12 && uv pip install -r requirements.txt` fetches a
3.12 interpreter for you and does not touch the system Python.

## Configuration

`make setup` creates `.env` from `.env.example` if it doesn't already exist — fill in the
required values there. `.env` is gitignored, is never baked into the image, and is read at
container start if present.

| Variable | Required | Purpose |
| --- | --- | --- |
| `AZURE_SPEECH_KEY` | yes | Azure Speech resource key (**F0** tier) |
| `AZURE_SPEECH_REGION` | yes | Azure Speech resource region |
| `GEMINI_API_KEY` | no | Coaching model. Without it, or under `OFFLINE_MODE`, coaching still works — the offline coach writes the report instead |
| `GEMINI_MODEL` | no | Model ID override, so it can be swapped without a code change (default `gemini-3.6-flash`) |
| `AZURE_TTS_VOICE` | no | en-US neural voice for "Hear it" playback (default `en-US-BrianNeural`) |
| `MIN_DURATION_SECONDS`, `MAX_DURATION_SECONDS_*` | no | Recording length guards |
| `UNSCRIPTED_TWO_PASS` | not yet | Two-pass unscripted assessment; counted by the budget guard, but Mode C is not built |
| `MONTHLY_BUDGET_USD`, `AZURE_TIER_CONFIRMED_F0` | no | Budget guard |
| `DB_PATH` | no | Local history file (default `./data/coach.db`) |
| `OFFLINE_MODE` | no | Replay a committed fixture; no network calls at all |
| `OFFLINE_FIXTURE` | dev | Which fixture `OFFLINE_MODE` replays. `bad_delivery_capture.json` is a real reading done badly on purpose; `synthetic_delivery_faults.json` is hand-built and is the only one carrying a pausing fault |
| `GA_REFERENCE_SET` | for accent | `men` or `women`. **No default and never an average** — formants scale with vocal tract length, so the wrong set is wrong by about the size of the effect. Vowel position is not scored until it is set |
| `KEEP_AUDIO`, `AUDIO_DIR` | no | Keep recordings on disk so a measurement can be re-derived without re-recording (default on, `./audio/attempts`) |
| `LPC_CEILING_HZ` | dev | Override the formant ceiling stored on the baseline. Normally empty — it is established once by a sweep and then held still |
| `CALIBRATION_GAP_MINUTES` | no | Minimum gap between the two calibration reads (default 10). Their displacement is the measurement noise floor |

See `.env.example` for the full annotated list.

While `MONTHLY_BUDGET_USD` is `0.00`, the app refuses to start unless
`AZURE_TIER_CONFIRMED_F0=true`. The SDK cannot read your resource's pricing tier, so
confirming it is F0 and not S0 is something only you can do. `OFFLINE_MODE=true` skips that
check — nothing is being spent, so the zero-cost path should not be the harder one.

## What gets stored

One row per attempt in a local SQLite file, holding **both raw API responses verbatim** —
Azure's now, Gemini's once coaching lands — alongside the scores and the recognised text.
Keeping the responses whole means a later change of mind about what to show is a re-parse,
not a re-recording that spends quota again.

**Recordings are kept on disk since v0.10.0**, under a gitignored `audio/` directory, with
only the path and a SHA-256 in the database. They are content-addressed by that digest, so
re-reading the same passage with byte-identical audio stores one file.

That is not so the accent measurement can be deferred — it runs inside the assessment
request, while the audio is in memory. It is so a measurement can be **re-derived**:
normalisation schemes and reference tables will change, and when they do the stored audio is
measured again instead of you being asked to read the calibration passage again. Roughly
2.9 MB per 90-second read. Set `KEEP_AUDIO=false` to go back to deleting; attempts recorded
before v0.10.0 have no audio and can never be measured.

The database is gitignored; a committed one is a leaked one. So is `audio/`.

The monthly usage meter is derived from that table rather than a separate counter file, so
the two cannot drift apart.

## Seeing progress over time

The **Progress** tab charts pronunciation, accuracy, fluency and prosody across everything
stored, plus the sounds and words that keep getting flagged.

The headline is a **fixed benchmark passage**. Plotting scores across whatever text you
happened to pick that day measures the text, not you — an easier paragraph scores higher and
reads as improvement. So one passage is fixed, read on a schedule, and scored identically
every time; everything else is drawn behind it as a faint cloud of unconnected points, for
context only. Pick **"Benchmark — the same words each morning"** from the paragraph presets
rather than typing it, since a read is identified by matching the text.

That passage is deliberately dense: it carries the commonly substituted consonants and every
vowel in the en-US inventory, several times each, in stressed and unreduced positions — so
the same 80-second read also serves as the calibration recording for vowel measurement later.

Two things the chart will not do. It will not join a Drill score to a Paragraph score: those
are computed differently (a paragraph's overall scores come from a duration-weighted merge
across utterances) and a line between them would be meaningless. And it will not plot a
missing prosody score as zero — Azure sometimes returns none, and that is a gap in the line,
not a collapse.

Offline replays are excluded throughout: the fixture scores the same every time.

### Rhythm

Below the trajectory is **nPVI** — how much each vowel differs in length from the vowel after
it. Stress-timed English varies a lot: a stressed vowel is held and the unstressed ones around
it are crushed to schwa. Syllable-timed languages give each syllable closer to equal time, and
carrying that into English is one of the most recognisable prosodic markers of a second-language
accent — audible long before any individual sound is.

The number is compared against **the same passage read by Azure TTS through this same
pipeline**, not against a published General American band. That is deliberate. Published bands
come from hand-segmented corpora reading other material, and nPVI moves with both the
segmentation method and the text — on one unchanged recording here, four defensible ways of
cutting the segments give 50.3, 54.8, 55.9 and 56.3. Comparing to a published band compares
three things at once; comparing to a synthesised read of the same words through the same code
changes one. The baseline is a fixed reference point, not a native speaker.

Capture it once (about 975 TTS characters and a minute of speech-to-text, both far inside the
free tiers):

```bash
docker compose run --rm app python scripts/capture_baseline.py
```

The assessment JSON it writes is committed; the WAV lands in the gitignored `audio/` so the
passage can be re-assessed later without paying for synthesis again. Rhythm is measured on
benchmark reads only, and only where there is enough connected speech — a short drill produces
a sentence saying so rather than a number.

To see what a populated view looks like without waiting a month, seed a throwaway database:

```bash
docker compose run --rm app python scripts/seed_progress_history.py
```

Then `DB_PATH=data/seed_demo.db make up`. It writes to `data/seed_demo.db`, refuses to touch
your configured `DB_PATH`, and spends nothing.

## Measuring an accent

The **Accent** tab measures the part a pronunciation score cannot express. Azure's diagnosis
is categorical — this phoneme is /θ/ or /t/, scored out of a hundred — and an accent is
continuous: a vowel scoring 78 while drifting toward the target and one scoring 78 while
drifting away are the same number to Azure and opposite findings to you.

Four measurements, all from one pass over the recording:

| | |
|---|---|
| **Position** | where each vowel sits, F1/F2 in Lobanov z-space |
| **Trajectory** | F2 movement from 20% to 80% of the vowel — whether a diphthong is a diphthong |
| **Rhoticity** | F3−F2. The single most useful number here: /ɝ/ sits near 300 Hz in the reference where every other vowel sits between 546 and 1613 |
| **Duration and reduction** | tense/lax and pre-fortis ratios, and how far unstressed vowels collapse toward your own schwa |

Findings always render as the same four-column table — feature, what you did, what the
target is, and the signed delta **with the articulatory instruction it implies**. A delta
with no instruction is a measurement; an instruction with no delta is vague advice.

### Calibrating, and why the passage is read twice

Before anything can be scored, read the benchmark passage **twice in one sitting, at least
ten minutes apart**, on the same microphone in the same room.

A vowel centroid moves between sessions from microphone placement, room, posture, time of
day and vocal warm-up, with no learning at all. The displacement between those two reads
**is** that noise floor. Without it the progress view would render exactly that wander as
progress — against a project whose whole goal is seeing that drilling something worked.
Afterwards, **no movement smaller than the band is ever reported as change**, including when
it moves the flattering way. Two back-to-back reads are refused: they measure a microphone
holding still, and the band would come out flatteringly small.

There is a five-second **room check** first. Formant estimation degrades badly with reverb
and a poor microphone, and being told your vowels are wrong when the real finding is that
the room is wrong wastes a calibration read.

### What the reference is, and is not

`GA_REFERENCE_SET` picks the men's or women's set from Hillenbrand et al. (1995). It has no
default and there is never an average of the two — formants scale with vocal tract length,
so the wrong set is wrong by about the size of the thing being measured.

Three things worth knowing before trusting a number:

- **It covers 12 vowels.** There is no published mean for /aɪ aʊ ɔɪ ə ɚ ɑɹ ɔɹ ɛɹ ɪɹ ʊɹ/, so
  those report your position with an honest blank target rather than an invented one.
- **Its durations are citation-form `/hVd/` words read in isolation**, so only *ratios*
  transfer to connected speech. Absolute milliseconds are never compared against it. Nothing
  in it ends in a voiceless consonant, so pre-fortis clipping has no published target at all.
- **It is upper-Midwest speech from the early 1990s.** The low-back /ɑ/–/ɔ/ merger has spread
  and GOOSE has fronted since, so those carry a deliberately widened tolerance band rather
  than flagging a change the reference predates.

The published means are what the **numbers** are measured against; the synthesised voice you
practise with is what your **ear** is trained on. They do not coincide, and each surface says
which it used.

## Testing

```bash
make test
```

Runs offline with no API keys and no network — the suite forces `OFFLINE_MODE`, clears the
credentials, and refuses any non-loopback socket connection outright, so it can never turn
into a billable call. It works against `tests/fixtures/`, two verbatim Azure responses
captured once from a real recording. That is what lets the parsing, scoring, and colouring
layers be developed without spending any of the monthly allowance.

Rebuild first (`docker compose build`) if `requirements.txt` changed.

## Checks

```bash
make check
```

Formatting, linting, types and the test suite — `make lint`, `make typecheck` and `make test`
individually. All of them run in the container against the pinned tools in
`requirements.txt`, which is exactly what CI runs, so a green `make check` is a green CI run.
`make format` applies the formatter and the safe lint fixes.

Ruff and mypy are configured in `pyproject.toml`, where every rule choice carries the reason
it was made. mypy is strict on every module under `src/`.

CI runs on every push and pull request and **cannot reach the network or spend quota**: the
workflow references no secrets, a CI checkout has no `.env`, the suite clears the credentials,
and the socket guard refuses the connection. Releases are a separate workflow, triggered by a
`v*` tag, so CI itself stays read-only.

## Cost

A realistic mixed month, against the F0 allowance of 5 audio hours (18,000 seconds):

| Mode | Frequency | Length | Monthly seconds |
|---|---|---|---|
| A — Drill | 5/day x 25 days = 125 | 25 s | 3,125 |
| B — Paragraph | 3/week = 12 | 90 s | 1,080 |
| C — Unscripted (two-pass) | 2/week = 8 | 210 s x 2 | 3,360 |
| **Total** | **145 attempts** | | **7,565 s (~42% of quota)** |

The point that table makes: the quota is not the binding constraint once attempts stop
being uniform length. Don't design around a fixed attempt count.

Designed to run entirely on free tiers: Azure Speech **F0** (5 audio hours/month) and the
Gemini free tier. Running locally does not change this — the APIs are remote either way, so
the same monthly allowances apply.

An F0 resource cannot bill: it returns `403` once the monthly allowance is gone. Creating
an **S0** resource by mistake is the only way this project costs money. Likewise a Gemini
key from a project with no billing account attached returns `429` rather than a charge.

## What leaves your machine

Be clear-eyed about this rather than reassured:

- Audio is **transmitted to Azure** for processing. There is no local-only mode that still
  scores pronunciation.
- Audio is also **kept on this machine** since v0.10.0, under a gitignored `audio/`
  directory — see *What gets stored*. It is never committed and never uploaded anywhere
  other than Azure, and `KEEP_AUDIO=false` turns it off.
- Azure Speech logging can be disabled in the resource's settings, under
  [data logging](https://learn.microsoft.com/azure/ai-services/speech-service/logging-audio-transcription).
- Coaching is **opt-in per attempt**: the report is written by the offline coach, free,
  every time. Only clicking "Improve this with Gemini" sends anything to Google — the
  compacted analysis and the reference text, never the audio — and free-tier prompts and
  responses **may be used by Google to improve their products**.

## Hosting it somewhere (optional)

Not required — this is built to run on your own machine, and nothing in the app assumes
otherwise. It is laid out to make a Hugging Face Space possible for anyone who wants one:
`packages.txt` lists the apt packages such an image needs, and the app is a single
`src/app.py` Streamlit entry point.

Going that route means adding YAML frontmatter to the top of this file, since a Space will
not build without it:

```yaml
---
title: Pronunciation Coach
emoji: 🗣️
colorFrom: indigo
colorTo: blue
sdk: streamlit
sdk_version: 1.61.1
app_file: src/app.py
pinned: false
---
```

Three things to check first, none of them verified here: that Hugging Face supports the
`sdk_version` you pin, that the `packages.txt` names match the Ubuntu release the image
runs (newer releases want `libasound2t64` / `libssl3t64`), and that **the Space is
private** — a public one exposes your Azure key's monthly quota to anyone who finds the
URL. A free Space also has an ephemeral filesystem, so any local usage counter resets
silently on rebuild.
