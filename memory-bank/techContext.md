# Tech Context

## Architecture

A single-page Streamlit app. `app.py` is the only source file so far and holds UI only — it
renders a placeholder page and makes no API calls. The intended split keeps every API call
out of `app.py`: separate modules for Azure assessment, the coaching model, the offline
fallback coach, TTS, audio conversion, and shared utilities. None of those exist yet.

No database, no accounts, no persistent audio storage.

## Technologies

- **Python 3.12**, pinned in `.python-version` and in the Docker base image.
- **Streamlit 1.61.1** — UI. `st.audio_input` (needs ≥ 1.41) is the intended capture widget;
  `streamlit-audiorecorder` is deliberately not used.
- **azure-cognitiveservices-speech 1.51.1** — pronunciation assessment. Native library.
- **google-genai 2.18.1** — current Google GenAI SDK (`from google import genai`). The old
  `google-generativeai` package is end-of-life and must not be used.
- **pydub 0.25.1** + system `ffmpeg` — audio conversion.
- **python-dotenv 1.2.3**, **pydantic 2.13.4**.
- **SQLite** via the stdlib `sqlite3` — chosen 2026-08-17, not yet built. No dependency to
  pin and no second service. It persists for free under the existing bind mount, since the
  project directory is the host's; a database file under it survives `docker compose down`.
  What gets stored is not decided yet.

All pins in `requirements.txt` are exact `==`, verified against PyPI on 2026-08-17. Ranges
are rejected: an unattended free-tier rebuild must produce the same image next month.

## Running and testing

`make setup` (creates `.env` from `.env.example` if missing, checks `docker compose` is on
PATH — logic lives in `scripts/setup.py` since it's conditional, not a one-liner), then
`make up` (`docker compose up --build`, serving http://localhost:8501) and `make down`.
The source directory is bind-mounted, so edits reload without a rebuild; rebuild only when
`requirements.txt` changes. The image sets `STREAMLIT_SERVER_RUN_ON_SAVE=true`, because
Streamlit's default only shows a "Rerun" prompt on a file change instead of applying it.

A project-local `.venv` built from Python 3.12 is supported as an optional alternative
(`python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt`). Nothing is ever
installed globally.

Verified in the container: Python 3.12.14, and `streamlit`, `pydub`, `pydantic`, `dotenv`,
`audioop`, `azure.cognitiveservices.speech`, `google.genai` all import.

No tests, lint, or type-check setup yet — and therefore no `pytest` pin. The test suite is
blocked on a committed Azure response fixture, which the parsing work owns.

## Technical constraints

- **Python must stay at 3.12.** `pydub` depends on the stdlib `audioop` module, removed in
  3.13. Moving to 3.13+ requires adding `audioop-lts`.
- **Azure Speech SDK native prerequisites.** A missing `libasound2` shows up as an opaque
  `ImportError` on `import azure.cognitiveservices.speech`, not as an install failure. The
  `python:3.12-slim` base is Debian trixie, which renamed the packages to `libasound2t64`
  and `libssl3t64`, so the Dockerfile tries both names.
- Free tiers only: Azure Speech **F0** (5 audio hours/month) and the Gemini free tier.
  Running locally does not change this — the APIs are remote either way. Creating an Azure
  **S0** resource by mistake is the only way this project costs money.
- Target locale is **en-US** — prosody assessment supports nothing else.
- Secrets come from environment variables only; never hardcoded, logged, or surfaced in a
  UI error or traceback. `.env` is gitignored and never copied into the image.
- Required: `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION`, `GEMINI_API_KEY`. The full annotated
  set, including duration guards and the budget guard, lives in `.env.example`. Nothing
  reads any of them yet.

## Hosting

**This runs locally. Deployment is not a goal** — it is left possible for anyone who forks
the project, and nothing in the app may assume a host. Practical consequences:

- The usage counter can just be a local file. No Hugging Face Dataset persistence, and no
  design work around a Space's ephemeral filesystem.
- No cold-start budget to design against, so no wake-time requirement.
- `packages.txt` and the README's optional frontmatter block are kept for a would-be
  deployer and are **unverified** — package names against the Space's Ubuntu release, and
  `sdk_version` against Hugging Face's supported list. Neither blocks local work.

## Decisions

- **Exact pins over ranges**, for the rebuild reason above.
- **Docker as the primary run path**, on request. It installs nothing on the host and fixes
  both the Python version and the Azure SDK's native libraries — the two things most likely
  to break setup on another machine. The local `.venv` path is kept but secondary.
- **`requirements.txt` stays the single dependency manifest**, not a lockfile or
  `pyproject.toml`. One manifest that both the Dockerfile and the optional local `.venv`
  read cannot drift; it also happens to be what a Space would read.
- **No module stubs.** The other modules the design calls for were left uncreated rather
  than committed empty — dead files that later work would have to clean up.
- **SQLite over Postgres or DuckDB.** Single user, single machine: Postgres would mean a
  second container and a named volume for nothing, and DuckDB's analytical edge does not
  show up at a few hundred rows. Revisit only if the data ever needs to be reachable from
  outside the container.
- When persistence is built, two Streamlit-specific traps apply: the script re-runs on
  every widget interaction, so the connection belongs behind `@st.cache_resource` rather
  than being reopened per rerun, and it needs `check_same_thread=False`. Using
  `st.connection("sql")` instead would add SQLAlchemy — a dependency SQLite does not
  otherwise need.
