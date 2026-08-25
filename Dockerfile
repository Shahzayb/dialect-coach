# --- Runtime stage -------------------------------------------------------------------------
# Single-stage since 2026-08-25. There used to be a builder stage above this one that compiled
# `praat-parselmouth` from its sdist, because 0.4.7 publishes no linux-aarch64 wheel and this
# container is arm64 Linux — 535 .cpp files through scikit-build and CMake, needing a C++
# toolchain that had no business in the runtime image. The accent measurement and the
# resynthesis were the only things that imported it, both were deleted, and the compile went
# with them. Recoverable at tag `v0.12.0-full` if that work is ever resumed.
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

# Dependencies before source, so editing app.py does not invalidate the install layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

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
