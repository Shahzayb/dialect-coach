# Generous limits

## Intent

Raise every input limit that can stop a legitimate long practice session, as high as the
providers allow. Duration has had no ceiling since 2026-08-25; the remaining caps are the
reference text, the upload size, the Gemini annotation, and the recognition backstops.

## Changes

- **Reference text: no cap.** Remove `utils.MAX_REFERENCE_CHARS` and its check in
  `app.validate_reference`. Scripted assessment is continuous at any length and miscues come
  from the local diff, so nothing downstream depends on the 1000-char cut. The empty and
  digit checks stay.
- **Upload size: 2 GB.** `STREAMLIT_SERVER_MAX_UPLOAD_SIZE=2000` (MB) in the Dockerfile's
  existing Streamlit `ENV` block, rather than a new `.streamlit/config.toml`. Uploads are held
  in memory, so this is about where the container's RAM, not Streamlit, becomes the limit.
- **Gemini annotation: 5000 words** (`ai_coach.MAX_ANNOTATED_WORDS`, was 400). Roughly what
  one response's output budget holds with markup; longer reads are still annotated up to the
  cut and the surface says so.
- **Recognition backstops: 4 h scripted / 8 h unscripted** (`speech_analyzer`, were 1 h / 2 h),
  so a backstop sized for the old inputs cannot fire on a legitimate long read.
- Update `guides/03-one-click-end-to-end.md` and the `MAX_ANNOTATED_WORDS` open decision in
  `memory-bank/progress.md`.

## Out of scope

Coaching design caps (`MAX_PRIORITY_FIXES`, `MAX_QUOTED_WORDS`), retry counts, the 1.5 s
minimum duration.
