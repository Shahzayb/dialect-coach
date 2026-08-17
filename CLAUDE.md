# CLAUDE.md

This is the workflow guide for this repo — **when** to do something. The technical facts (stack, architecture, commands, constraints) live in `memory-bank/` instead, so they exist in exactly one place.

## Project status

A fresh scaffold for **pronunciation-analyzer**. No source code, dependencies, build tooling, or tests exist yet. As those appear, they get documented in `memory-bank/techContext.md`, not here.

## 1. Before starting any task

`memory-bank/` is the only record of previous work — nothing about the project loads into context automatically.

- Read all of `memory-bank/`, including `memory-bank/history.md`, at the start of any non-trivial task.
- Don't answer from assumption — check the memory bank first.

## 2. Plan files

Not every task needs a plan. The bar: a plan is warranted when the work touches more than one file, adds a dependency, or sets a new pattern. A single-file fix, a rename, or a config tweak doesn't need one.

When a plan is warranted:

1. Write it to `plans/YYYY-MM-DD_short-name.md`, right after the plan is approved and **before** writing any code — plan mode can't write files, so this is a separate step.
2. Once the file exists, append a `planned` row for it in `memory-bank/history.md`.

While implementing, follow the plan as written. If the work needs something the plan didn't cover, stop and say so rather than quietly re-planning mid-implementation.

## 3. After something lands

Work finishing or a decision being made means `memory-bank/` is out of date:

- **Verified facts** — write directly, then say you did.
- **Judgment calls** — propose the exact lines first; don't write unilaterally.
- Add or update the work's row in `memory-bank/history.md` to `implemented`.

## Where things live

- **`memory-bank/`** — current state, scope, architecture, technical context. Single source of truth for project facts; don't duplicate them in this file or in a harness-level memory directory, since a fact stored twice will drift and only this copy is in the repo.
- **`plans/`** — one dated file per feature/task, describing intent.
- The two are linked **by filename**, never by copying content between them.
- The `memory-bank` skill (`.claude/skills/memory-bank/SKILL.md`) explains what belongs in each memory-bank file and how an update pass runs.


## Rules

- No `Co-Authored-By` lines in commits or PR titles/descriptions. No watermarks, period.
