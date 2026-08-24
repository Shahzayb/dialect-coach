# Project Brief

Source of truth for scope. When the memory bank files disagree, this one wins.

## Goals

- **Sound like a native American English speaker**
- **Analyze Page**: Record scripted or unscripted audio, get it analyzed by Azure, and see detailed results.
  - All the scores returned by Azure.
  - Flagged words + how I said it vs how a native says it side-by-side (audio + IPA + mouth placement in laymen).
- **History Page**: See an entire paginated history of the analysis.
  - Clicking an item opens the Analyze page with input fields hidden.

## Non-goals

- Not a product for others — personal tool only
- No L1 hardcoded into the analysis
- No gamification, no accounts
- Nothing leaves this machine — no cloud storage, no sync, no hosted database
- No audio leaves this machine. Recordings are kept locally, with the path and hash in the
  database; they are never committed and never uploaded.
- Never spend money
