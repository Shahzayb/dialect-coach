# 2026-08-17 — Project scaffold: a Streamlit app that starts

## Goal

Turn the empty repo into a runnable project: a pinned Python environment, a pinned
dependency manifest, config/deploy scaffolding, and a placeholder Streamlit page that
starts and renders. **No feature work.** No Azure calls, no Gemini calls, no audio
capture, no parsing, no tests.

Success = `streamlit run app.py` serves a page locally, and a fresh clone can reproduce
the environment from committed files alone.

## Scope

### In

1. **Python pinned to 3.12** via `.python-version`.
   `pydub` needs the stdlib `audioop` module, removed in 3.13. This machine only has
   system Python 3.14, so the venv is built from a uv-managed 3.12 interpreter. Pinning
   now avoids the `audioop-lts` workaround later.
2. **`requirements.txt`** — exact `==` pins for the full runtime dependency set, versions
   verified against PyPI today rather than taken from the master plan's illustrative
   numbers (which are stale):
   - `streamlit==1.61.1` (need ≥ 1.41 for `st.audio_input`)
   - `azure-cognitiveservices-speech==1.51.1` (need ≥ 1.40 for prosody + syllables)
   - `google-genai==2.18.1` (the current SDK; `google-generativeai` is EOL — never use it)
   - `pydub==0.25.1`, `python-dotenv==1.2.3`, `pydantic==2.13.4`
   The whole set is pinned in this chunk, not just Streamlit, so the version question is
   settled once and the native Azure SDK install is proven to work on 3.12 before any
   code depends on it. No `numpy`.
3. **`packages.txt`** — apt packages for the Hugging Face Space image: `ffmpeg`,
   `libssl3`, `libasound2`, `ca-certificates`. A missing `libasound2` shows up as an
   opaque `ImportError` on `import azure.cognitiveservices.speech`.
4. **`.env.example`** — every variable the master plan defines (required secrets,
   optional overrides, duration guards, budget guard, `OFFLINE_MODE`), with comments and
   safe defaults. No real values anywhere.
5. **`.gitignore`** — `.venv/`, `__pycache__/`, `*.pyc`, `.env`, `.usage.json`,
   `.streamlit/secrets.toml`, and `plans/master-plan.md` (the master plan stays local and
   out of git by explicit instruction).
6. **`README.md`** — Hugging Face YAML frontmatter (a Space will not build without it),
   one-paragraph description, local setup/run commands, and the env-var table. Status
   line saying the app is a scaffold.
7. **Docker as the primary run path** — `Dockerfile`, `compose.yaml`, `.dockerignore`.
   Added on request partway through implementation: nothing gets installed on the host, and
   the container fixes the Python version and the Azure SDK's native libraries. Base image
   `python:3.12-slim` (Debian trixie), which renamed `libasound2` → `libasound2t64`, so the
   Dockerfile accepts either name. Source is bind-mounted for reload; `.env` is read at
   start if present and never baked into the image. A local `.venv` path stays supported as
   an optional alternative and is documented as such.
8. **`app.py`** — placeholder page only: `st.set_page_config`, title, a note that no
   analysis is wired up, and the three planned modes listed as inert text. Module-level
   `logging.getLogger(__name__)`, type hints, docstring — the conventions every later
   module has to follow, established here in the cheapest possible file.

### Out (later chunks, each gets its own plan)

- `speech_analyzer.py`, `ai_coach.py`, `fallback_coach.py`, `phoneme_reference.py`,
  `tts.py`, `audio_utils.py`, `utils.py` — not even as empty stubs. Empty modules would
  be committed dead weight.
- `tests/` and `tests/fixtures/sample_azure_response.json`, and therefore `pytest`. The
  fixture is the foundation of the parsing chunk and belongs to it.
- Secret loading, the env→`st.secrets`→`.env` loader, budget meters, duration guards.
  `.env.example` documents the variables; nothing reads them yet.
- Actual Hugging Face deployment.
- Anything in master plan §§4–7, 10, 10a, 11, 12.

## Steps

Committed in chunks as each piece lands, not as one commit at the end.

1. Write `.python-version`, `requirements.txt`, `packages.txt`, `.env.example`; extend
   `.gitignore`.
2. Write `app.py`.
3. Write `Dockerfile`, `compose.yaml`, `.dockerignore`.
4. Rewrite `README.md` with frontmatter, Docker instructions, optional local-venv
   instructions, and the env-var table.
5. Build the image and confirm `import azure.cognitiveservices.speech`, `import pydub`,
   `import google.genai`, `import streamlit`, `import audioop` all succeed inside it.
6. `docker compose up` and confirm the page serves and renders.
7. Update `memory-bank/` (`techContext.md`, `progress.md`, `history.md`,
   `projectbrief.md`) and open a PR.

## Verification

- Python inside the container reports 3.12.x, and `audioop` imports.
- All four runtime imports succeed, including the native Azure SDK.
- `docker compose up` serves HTTP 200 on `/` and the page visibly renders.
- The optional local path also runs on a project-local `.venv` built from 3.12.

## Known unknowns / assumptions

- **`sdk_version` in the README frontmatter is set to `1.61.1` to match the pin.** Hugging
  Face maintains its own list of supported Streamlit versions; if it does not yet support
  1.61.1 the Space build will warn or fall back. This must be verified against HF's
  supported list in the deployment chunk, not assumed here.
- `packages.txt` is copied from the master plan and is **unverified** against the Ubuntu
  release the free Space image currently runs. On newer Ubuntu the names may need to be
  `libasound2t64` / `libssl-dev`. Confirm at deploy time.
- Gemini model ID and its live free-tier RPM/RPD limits are deliberately not resolved
  here — `GEMINI_MODEL` is documented in `.env.example` as an override and gets a
  verified default in the coaching chunk.
- `uv` is used locally to get a 3.12 interpreter and build the venv. It is a local
  convenience only; `requirements.txt` stays the single manifest so Hugging Face (which
  reads `requirements.txt`) and local dev never diverge.
