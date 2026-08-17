# History

Append-only. One row per plan and implementation, oldest first. Newest entry last; never rewrite a row except to move its status from `planned` to `implemented`.

| Date | Plan file | Description | Status |
| --- | --- | --- | --- |
| 2026-08-17 | `plans/2026-08-17_project-scaffold.md` | Runnable scaffold: placeholder Streamlit page, pinned env, config + deploy files. Python 3.12 (`pydub` needs stdlib `audioop`, removed in 3.13). Exact `==` pins verified against PyPI, not recalled — streamlit 1.61.1, azure-cognitiveservices-speech 1.51.1, google-genai 2.18.1, pydub 0.25.1, python-dotenv 1.2.3, pydantic 2.13.4; no numpy, no pytest yet. Docker became the primary run path mid-implementation (host stays clean, native Azure SDK deps pinned); `python:3.12-slim` is Debian trixie, so `libasound2`→`libasound2t64` and the Dockerfile accepts either — a missing one is an opaque `ImportError`, not a build failure. `requirements.txt` kept as the single manifest because HF Spaces reads exactly that. Deliberately left out: all seven feature modules (not even empty stubs), `tests/` and the Azure fixture, secret loading, budget meters. Unverified and deferred to deploy: `packages.txt` names vs the Space image, `sdk_version: 1.61.1` vs HF's supported list. | implemented |
