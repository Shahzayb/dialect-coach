# Project Brief

Source of truth for scope. When the memory bank files disagree, this one wins.

## Why this project exists

I can't hear the difference between my pronunciation and a native speaker's, so I
don't know what to practice and how to improve my english speaking.

## Core requirements

- Record speech in 3 modes: short drills, longer paragraphs, unscripted speech
- Analyze phoneme/syllable/word/prosody level (Azure Speech)
- Show expected vs. actual sound for every flagged word, not just a score
- Turn analysis into specific coaching (Gemini, with offline fallback)
- Let me hear the correct pronunciation next to my own recording
- Keep history in a local SQLite file.
- Free tiers only, $0 budget, no accounts, no stored audio

## Goals

- Stop getting asked to repeat myself
- Sound less accented, raise IELTS speaking band
- Make practice diagnostic — know exactly what to drill next

## Non-goals

- Not a product for others — personal tool only
- No gamification, no accounts
- Nothing leaves this machine — no cloud storage, no sync, no hosted database
- Never spend money