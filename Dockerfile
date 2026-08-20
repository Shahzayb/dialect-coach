# --- Builder stage: compile praat-parselmouth ------------------------------------------------
# `praat-parselmouth` 0.4.7 publishes NO linux-aarch64 wheel at any Python version (all 100
# published files enumerated against PyPI on 2026-08-20: cp312 covers macosx_11_0_arm64,
# manylinux2014_x86_64/i686 and Windows, and nothing else). This container is arm64 Linux, so
# pip falls back to the 22.5 MB sdist and compiles Praat — 535 .cpp files plus vendored fmt and
# pybind11, through scikit-build and CMake.
#
# That compile needs a C++ toolchain, and a toolchain in the runtime image is ~400 MB of attack
# surface and download that never executes a line. So it happens HERE, in a stage that is thrown
# away: only the built wheel crosses into the final image. The stage is also its own Docker
# layer keyed on the version string below, so the compile is paid once and then cached until
# that string changes.
FROM python:3.12-slim AS parselmouth-builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        cmake \
        ninja-build \
    && rm -rf /var/lib/apt/lists/*

# Pinned here rather than read from requirements.txt so this layer's cache key is the version
# alone: editing an unrelated pin must not trigger a 500-file C++ rebuild.
ARG PARSELMOUTH_VERSION=0.4.7
RUN pip wheel --no-deps --no-cache-dir --wheel-dir /wheels \
        "praat-parselmouth==${PARSELMOUTH_VERSION}"

# --- Runtime stage -------------------------------------------------------------------------
# Python 3.12, not newer: pydub needs the stdlib audioop module, removed in 3.13.
FROM python:3.12-slim

# Native runtime dependencies. ffmpeg is for pydub; the rest are the Azure Speech SDK's
# native prerequisites — a missing libasound2 surfaces as an opaque ImportError on
# `import azure.cognitiveservices.speech`, not as a build failure. Debian renamed the
# package to libasound2t64 in trixie, so accept either name.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        libssl3 \
        ca-certificates \
    && (apt-get install -y --no-install-recommends libasound2t64 \
        || apt-get install -y --no-install-recommends libasound2) \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# The compiled wheel, and nothing else, from the builder. `--find-links` puts it on pip's
# search path so the `praat-parselmouth==` pin in requirements.txt resolves to this local
# file instead of going back to PyPI for the sdist and compiling a second time.
COPY --from=parselmouth-builder /wheels /wheels

# Dependencies before source, so editing app.py does not invalidate the install layer.
COPY requirements.txt .
RUN pip install --no-cache-dir --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

COPY . .

EXPOSE 8501

# Container-only settings; the deployed Hugging Face Space supplies its own.
# runOnSave defaults to false, which only shows a "Rerun" prompt when a bind-mounted file
# changes. On here it is a dev container, so apply the edit instead of asking.
ENV PYTHONUNBUFFERED=1 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_RUN_ON_SAVE=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/healthz')"

CMD ["streamlit", "run", "src/app.py"]
