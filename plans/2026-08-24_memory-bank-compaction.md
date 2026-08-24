# Memory-bank compaction

## Intent

Reduce the memory bank to a session-sized working set while preserving current scope,
architecture decisions, active risks, and links to detailed plans.

## Changes

1. Replace narrative material in `techContext.md` with durable contracts, pointers to their
   owning modules, and only the constraints that are expensive to rediscover.
2. Replace completed-work narration in `progress.md` with current state, open validation,
   and active decisions.
3. Keep `history.md` as the chronological plan index; compact every row to at most 300 words
   while preserving its date, plan link, outcome, and status.
4. Tighten the memory-bank skill with per-file budgets, a test for whether a fact earns a
   line, and an explicit no-duplication rule.

## Verification

- Count words across `memory-bank/` and confirm the total is at most half of the pre-change
  27,885-word baseline.
- Confirm every history row is at most 300 words.
- Confirm the four core files and all plan links remain present.
