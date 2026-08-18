# pronunciation-analyzer

A single-user English pronunciation and delivery coach. It records spoken en-US English,
analyses it at the phoneme, syllable, word and prosody level with Azure Speech
pronunciation assessment, and turns that raw analysis into specific coaching — naming
which sound in which word failed and what was produced instead.

Personal training tool, not a product. It runs locally. Diagnostic specificity over polish:
no accounts, no persistent audio storage.

**Status: assessment works, coaching does not.** You can record or upload a drill sentence
or a paragraph, get real Azure scores down to the phoneme, and have every attempt kept in a
local SQLite file. Not built yet: the Gemini coaching report and its offline fallback,
"Hear it" playback, unscripted mode, and the colour-coded reference text.

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
.venv/bin/streamlit run app.py
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
| `GEMINI_API_KEY` | not yet | Coaching model — not read until the coaching chunk lands |
| `GEMINI_MODEL` | no | Model ID override, so it can be swapped without a code change |
| `AZURE_TTS_VOICE` | not yet | en-US neural voice for target playback |
| `MIN_DURATION_SECONDS`, `MAX_DURATION_SECONDS_*` | no | Recording length guards |
| `UNSCRIPTED_TWO_PASS` | not yet | Two-pass unscripted assessment; counted by the budget guard, but Mode C is not built |
| `MONTHLY_BUDGET_USD`, `AZURE_TIER_CONFIRMED_F0` | no | Budget guard |
| `DB_PATH` | no | Local history file (default `./data/coach.db`) |
| `OFFLINE_MODE` | no | Replay a committed fixture; no network calls at all |

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

**No audio is stored**, only a SHA-256 of it, which is enough to recognise a repeat
attempt. The database is gitignored; a committed one is a leaked one.

The monthly usage meter is derived from that table rather than a separate counter file, so
the two cannot drift apart.

## Testing

```bash
make test
```

Runs offline with no API keys and no network — the suite forces `OFFLINE_MODE` and clears
the credentials, so it can never turn into a billable call. It works against
`tests/fixtures/`, two verbatim Azure responses captured once from a real recording. That
is what lets the parsing, scoring, and colouring layers be developed without spending any
of the monthly allowance.

Rebuild first (`docker compose build`) if `requirements.txt` changed.

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

- Audio is **not stored** anywhere, but it **is transmitted to Azure** for processing.
  There is no local-only mode that still scores pronunciation.
- Azure Speech logging can be disabled in the resource's settings, under
  [data logging](https://learn.microsoft.com/azure/ai-services/speech-service/logging-audio-transcription).
- Once coaching lands, free-tier Gemini prompts and responses **may be used by Google to
  improve their products**. Only the compacted analysis would be sent — never the audio —
  but the reference text is part of it.

## Hosting it somewhere (optional)

Not required — this is built to run on your own machine, and nothing in the app assumes
otherwise. It is laid out to make a Hugging Face Space possible for anyone who wants one:
`packages.txt` lists the apt packages such an image needs, and the app is a single
`app.py` Streamlit entry point.

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
app_file: app.py
pinned: false
---
```

Three things to check first, none of them verified here: that Hugging Face supports the
`sdk_version` you pin, that the `packages.txt` names match the Ubuntu release the image
runs (newer releases want `libasound2t64` / `libssl3t64`), and that **the Space is
private** — a public one exposes your Azure key's monthly quota to anyone who finds the
URL. A free Space also has an ephemeral filesystem, so any local usage counter resets
silently on rebuild.
