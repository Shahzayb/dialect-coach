# Project Brief

Source of truth for scope. When the memory bank files disagree, this one wins.

## What this is

**An accent coach.** Not a pronunciation analyser, not a dashboard, not a progress tracker.

A coach listens, catches the mistakes, hands back an exercise, listens again while I repeat it,
tells me whether I got closer, and keeps going until it's fixed or we move on to something else.
Everything this project builds is either part of that cycle or it is in the way of it.

## Why this project exists

I want to sound like a native American English speaker, and nothing is coaching me toward it.

Not being able to hear the difference is a symptom, not the problem. Being shown the difference
does not fix it either — I've been shown it, in detail, and it changed nothing about what I do
next. **The gap is between knowing and doing**, and that gap is closed by practice against a
target I can hear, repeated until it lands.

## Who it's for

Me. Not a product, not distributed.

But **no first language is hardcoded into the analysis**. What to practise comes from my own
recordings, not from a list of what speakers of some language get wrong. An L1 may be an optional
hint; it never overrides the recordings.

**The target accent is General American** — fixed, not a preference. Azure supports no other.

## The loop, which is the product

    speak → analyse at every angle → a short set of problems
      → practise one against native → measure whether I moved
      → resolved, or I move on → repeat

Every feature is judged by where it sits on that loop. A feature that produces something the loop
doesn't consume is not finished, however well it works.

Speaking means all three registers — short drills, longer passages, and unscripted speech.

**The unit of practice is the sentence. The word is the way in, not the destination.** Fixing a
single sound in a single word is the easy half; saying the whole sentence — its rhythm, its
stresses, where it links and where it reduces, its melody — is the hard half, and it is the half
that decides whether I sound native. A word said correctly on its own is not fixed yet, because
in isolation I hyperarticulate and in a sentence I don't. So a word is only resolved once it
survives inside the sentence it came from, and a sentence only once it survives inside the
paragraph.

## Core requirements

- **Analyse as deeply as possible, from as many angles as possible.** This is the strong part of
  the project and it should keep growing. Every measurement is kept and stays re-derivable.
- **Surface a shortlist, not the analysis.** Depth of measurement and volume of output are
  different things, and conflating them is what made the app unusable. Everything measured is
  available when I go looking; what reaches me unprompted is the next few things to work on.
- **Every problem comes with the exercise attached.** Naming a fault without handing me the drill
  is half a feature.
- **Three-way listening as the practice surface**: what I said, a native saying it, and my own
  voice corrected — for a word, and just as importantly for a whole sentence or paragraph, where
  the correction is the delivery rather than one sound.
- **Repetition is the normal case.** The tenth attempt at one word must be as cheap and fast as
  the first, ideally with feedback during or immediately after each one.
- **Moving on is a first-class outcome**, equal to resolving. Abandoning a problem and taking a
  different one must never stall the loop or read as failure.
- **Sentence-level delivery is a first-class problem, not a footnote to the sounds.** Rhythm,
  stress, linking, reduction and intonation get the same treatment segmental faults get: named,
  demonstrated, drilled, re-measured.
- **Progress is measured, not asserted.** No change smaller than my own session-to-session
  variation is ever called improvement, including in the flattering direction.
- Keep history locally in SQLite and show it back over time.
- Free tiers only, $0 budget, no accounts, audio kept on disk but never committed.

## Goals

- **Sound like a native American English speaker**
- Stop getting asked to repeat myself
- Raise my IELTS speaking band
- Know exactly what to drill next, and see that drilling it worked

**Native-like is the target. Progress is measured as distance from it, never as a pass mark** — a
binary verdict can't show that a month of practice moved anything.

## How I'll know it's working

Not by the app being accurate — it already is. By me opening it, being handed something to work
on, working on it, and hearing the difference. If a session ends with me having read numbers
rather than having practised, the session failed regardless of what was on screen.

## Non-goals

- **Not an analysis report.** Measuring something is not the same as delivering it.
- **Not a dashboard.** Charts exist to drive practice; one that doesn't is removed from view.
- Not a product for others — personal tool only
- No L1 hardcoded into the analysis
- No gamification, no accounts
- Nothing leaves this machine — no cloud storage, no sync, no hosted database
- No audio leaves this machine. Recordings may be kept locally, with the path and hash in the
  database; they are never committed and never uploaded.
- Never spend money
