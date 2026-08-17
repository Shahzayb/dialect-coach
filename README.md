# pronunciation-analyzer

A single-user English pronunciation and delivery coach. It records spoken en-US English,
analyses it at the phoneme, syllable, word and prosody level with Azure Speech
pronunciation assessment, and turns that raw analysis into specific coaching — naming
which sound in which word failed and what was produced instead.

Personal training tool, not a product. It runs locally. Diagnostic specificity over polish:
no accounts, no database, no persistent audio storage.

**Status: scaffold.** The app starts and renders a placeholder page. Recording,
assessment, and coaching are not implemented yet.

## Running it — Docker (preferred)

Docker is the supported path: nothing is installed on the host, and the container matches
the Python version and native libraries the app needs.

```bash
docker compose up --build
```

The app serves on http://localhost:8501. The source directory is mounted, so edits reload
without a rebuild; rebuild only when `requirements.txt` changes. Stop it with
`docker compose down`.

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

Copy `.env.example` to `.env` and fill in the required values. `.env` is gitignored, is
never baked into the image, and is read at container start if present.

| Variable | Required | Purpose |
| --- | --- | --- |
| `AZURE_SPEECH_KEY` | yes | Azure Speech resource key (**F0** tier) |
| `AZURE_SPEECH_REGION` | yes | Azure Speech resource region |
| `GEMINI_API_KEY` | yes | Coaching model |
| `GEMINI_MODEL` | no | Model ID override, so it can be swapped without a code change |
| `AZURE_TTS_VOICE` | no | en-US neural voice for target playback |
| `MIN_DURATION_SECONDS`, `MAX_DURATION_SECONDS_*` | no | Recording length guards |
| `UNSCRIPTED_TWO_PASS` | no | Two-pass unscripted assessment (default on) |
| `MONTHLY_BUDGET_USD`, `AZURE_TIER_CONFIRMED_F0`, `BUDGET_STATE_PATH` | no | Budget guard |
| `OFFLINE_MODE` | no | Replay a committed fixture; no network calls at all |

See `.env.example` for the full annotated list. Nothing reads these yet.

## Cost

Designed to run entirely on free tiers: Azure Speech **F0** (5 audio hours/month) and the
Gemini free tier. Running locally does not change this — the APIs are remote either way, so
the same monthly allowances apply.

An F0 resource cannot bill: it returns `403` once the monthly allowance is gone. Creating
an **S0** resource by mistake is the only way this project costs money. Likewise a Gemini
key from a project with no billing account attached returns `429` rather than a charge.

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
