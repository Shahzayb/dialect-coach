# Project Brief

Source of truth for scope. When the memory bank files disagree, this one wins.

## Why this project exists

I can't hear the difference between my pronunciation and a native speaker's, so I don't know
what to practise or whether anything is improving.

## Who it's for

Me. Not a product, not distributed.

But **no first language is hardcoded into the analysis**. What to practise comes from my own
recordings, not from a list of what speakers of some language get wrong. An L1 may be an
optional hint; it never overrides the recordings.

**The target accent is General American** — fixed, not a preference. Azure supports no other.

## Core requirements

- Record speech in 3 modes: short drills, longer paragraphs, unscripted speech
- Analyze phoneme/syllable/word/prosody level (Azure Speech)
- Show expected vs. actual sound for every flagged word, not just a score
- Turn analysis into specific coaching (Gemini, with offline fallback)
- Let me hear the correct pronunciation next to my own recording
- Train, not only diagnose — practice that carries over between sessions
- Keep history in a local SQLite file, and show it back over time
- Free tiers only, $0 budget, no accounts, no stored audio

## Goals

- **Sound like a native American English speaker**
- Stop getting asked to repeat myself
- Raise my IELTS speaking band
- Know exactly what to drill next, and see that drilling it worked

**Native-like is the target. Progress is measured as distance from it, never as a pass mark** —
a binary verdict can't show that a month of practice moved anything.

## Non-goals

- Not a product for others — personal tool only
- No L1 hardcoded into the analysis
- No gamification, no accounts
- Nothing leaves this machine — no cloud storage, no sync, no hosted database
- Never spend money
