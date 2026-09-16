# History table: Prosody column, plain Pron number

**Branch:** `feat/prosody` · **Date:** 2026-09-16

## Intent

History's `st.dataframe` shows Pron/Accuracy/Fluency but not Prosody, even though
`attempts.prosody` is stored and already selected by `db.attempt_page`. Pron renders as a
`ProgressColumn`, whose bar is drawn in Streamlit's primary (red) colour; it should be a plain
number like the other scores.

## Changes

All code changes are in `src/app.py`; `db.py` is untouched because the column is already fetched.

1. `_attempt_record` — add `"Prosody": score("prosody")` after `"Fluency"`. A NULL column
   renders as the dataframe's `placeholder="—"`, never `0`, matching the Analyze convention.
2. `render_history` — insert `"Prosody"` after `"Fluency"` in `column_order`; replace the Pron
   `ProgressColumn` with a `NumberColumn` shaped like Accuracy/Fluency; add a Prosody
   `NumberColumn`.
3. `_history_filters` — the score-slider row grows from three to four so the "one filter per
   column" rule still holds. `keeps()` already leaves NULL-scored rows visible while the slider
   is untouched, which covers legacy rows with no prosody score.
4. Tests — extend the column-per-facet test; add one asserting a row with no prosody score
   still renders with an empty Prosody cell.
5. Memory bank — `progress.md` names the four score columns; `history.md` gets this row.

## Verification

- `make check` in the container.
- `coach-offline` launch: Prosody sits right of Fluency, Pron is a bare number with no bar, a
  NULL-prosody row shows "—", and the Filters expander has four score sliders on one row.
