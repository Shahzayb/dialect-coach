# src/ layout, Ruff, mypy, GitHub Actions — milestone v0.9.0

Closes #15 (Ruff), #16 (Actions), #17 (src/), #18 (mypy). One chunk, deliberately.

## Context

The repo has 9,582 lines of source across 15 flat modules at the repo root, no formatter, no
linter, no type checker and no CI. Every quality gate today is a human reading a diff.

This lands **after** the feature work and **before** the audio-measurement chunks, on purpose.
Moving files and switching on mypy while feature branches are in flight is a merge-conflict
generator; doing it now means the strictness lands on finished code and is switched on before
the hardest, newest and most numerically delicate code in the project gets written.

The four issues are one chunk because they touch the same files. Sequencing them separately
means reformatting every file, then moving every file, then annotating every file — three
passes over the same 9,582 lines and three chances for a real change to hide inside churn.

**The single most important constraint:** CI must run the offline suite only. No secrets, no
API keys reachable by any job, structurally — not merely unconfigured. A CI run must never be
able to spend Azure STT/TTS minutes or Gemini free-tier quota.

### Decisions taken with the user

| Decision | Choice |
| --- | --- |
| `src/` layout | **Bare root** — `src/*.py`, flat modules, `import utils` unchanged. Not a `dialect_coach` package. |
| CI runtime | **Docker** — one `docker compose build`, then ruff/mypy/pytest as `compose run` steps. Exact parity with `make test`; the apt list for the Azure SDK's native deps stays only in the Dockerfile. |
| #16 scope | Lint + type + test on push/PR, **and** a tag → release workflow. **No Python version matrix.** |
| Network guard | **Yes** — an autouse conftest fixture that raises on any non-loopback `socket.connect`. |

**No Python matrix, and this must be said when closing #16.** #16 suggests 3.11 + 3.12.
`.python-version`, the Dockerfile and `requirements.txt` all pin 3.12 deliberately — `pydub`
needs the stdlib `audioop`, removed in 3.13 — and nothing has ever been run on 3.11. A matrix
would assert support the project does not have.

## Measured baseline

Run with `uvx ruff@latest` / `uvx mypy@latest` against the current tree at `line-length = 100`.

| | Count |
| --- | --- |
| `ruff format` | 45 files reformatted, ~5,400 diff lines |
| `ruff check` (proposed rule set) | 124 findings — 83 auto-fixable, ~41 by hand (23 of them `E501` comment rewraps) |
| mypy, `utils`+`db`+`budget`+`speech_analyzer` at strict | **13** |
| mypy, `audio_utils`/`shadowing`/`perception_trainer`/`practice_queue`/`phoneme_reference` at strict | **0** |
| mypy, `tts`/`rhythm`/`fallback_coach`/`ai_coach`/`progress_view` | 12 / 13 / 15 / 23 / 18 |
| mypy, `app.py` | 51 |
| mypy, `tests/` | 1 |
| mypy, `scripts/` | 14 |

**Caveat, and it matters:** these were measured with `--ignore-missing-imports` and no
third-party packages installed. With streamlit, pandas, altair, pydantic and google-genai
actually present, counts will move — most likely **up** for `app.py` and `progress_view.py`,
where `Any` becomes a real type. Re-measure inside the container before committing to a
strictness tier.

### One real bug found while sizing this

`tests/test_app.py:764` — `def _hanging_job(app: AppTest, stop: "threading.Event")`. The
module never imports `threading` at module scope, only inside function bodies. The annotation
is a string so it is never evaluated and the suite passes today; it would fail under
`typing.get_type_hints`. Ruff `F821` and mypy both catch it.

### Two things that will visibly change and are worth knowing before approving

- **`ruff format` destroys the aligned trailing-comment columns.** `utils.py`'s threshold
  block (`WORD_RED = 80.0      # below this: red`) collapses to a single space before `#`.
  This is inherent to adopting a formatter, and it is a large share of the ~5,400 churn lines.
- **`line-length = 100`, not the default 88.** The codebase is hand-wrapped near 96: 2,035
  lines exceed 88 but only 23 exceed 100. At 88 the churn commit would rewrap essentially
  every prose comment in the repo.

## Plan

### 0. Before any code

Per `CLAUDE.md` §2: copy this plan to `plans/2026-08-20_src-layout-linting-typing-ci.md` and
append a `planned` row to `memory-bank/history.md`.

### Commit 1 — `fix: import threading at module scope in test_app`

One line. Kept out of the churn commit so `git log` stays bisectable, which is the whole point
of the ordering below.

### Commit 2 — `refactor: move source modules under src/`

`git mv` the 15 root modules into `src/`: `ai_coach app audio_utils budget db fallback_coach
perception_trainer phoneme_reference practice_queue progress_view rhythm shadowing
speech_analyzer tts utils`.

**No file content inside those modules changes** — bare-root layout means `import utils` keeps
resolving. What changes is how each runner finds `src/`, and each runner gets exactly one
mechanism:

| Runner | Mechanism |
| --- | --- |
| Streamlit | `src/app.py` as the entry point — Streamlit puts the script's own directory on `sys.path` |
| pytest | `pythonpath = src` in `pytest.ini` (built-in since pytest 7; pinned at 9.1.1) |
| `scripts/*.py` | retarget the existing shim: `sys.path.insert(0, str(ROOT / "src"))` |
| mypy | `mypy_path = "src"` |
| ruff (isort) | `src = ["src"]`, so `utils`/`db`/… classify as first-party |

Path references to update, all of them:

- [pytest.ini](pytest.ini) — add `pythonpath = src`. **Keep `testpaths = tests` and its comment
  verbatim.** That scoping is load-bearing: `scripts/smoke_test.py` and
  `scripts/pronunciation_test.py` hold `test_`-prefixed functions that make real billable API
  calls, and pytest would collect them otherwise. Verify after the move that
  `pytest --collect-only` still reports zero items from `scripts/`.
- [tests/conftest.py](tests/conftest.py) — drop the `sys.path.insert(ROOT)` block (pytest.ini
  owns resolution now), keep `ROOT` for `FIXTURES`, and rewrite the docstring, which currently
  says "there is no package and no `pyproject.toml`".
- [scripts/capture_baseline.py](scripts/capture_baseline.py),
  [scripts/capture_fixture.py](scripts/capture_fixture.py),
  [scripts/list_voices.py](scripts/list_voices.py),
  [scripts/seed_progress_history.py](scripts/seed_progress_history.py) and the two live-call
  diagnostics — `ROOT` → `ROOT / "src"`. (`scripts/setup.py` imports nothing first-party.)
- [Dockerfile](Dockerfile) — `CMD ["streamlit", "run", "src/app.py"]`.
- [.claude/launch.json](.claude/launch.json) — the `coach-seeded-demo` config runs
  `streamlit run app.py` explicitly.
- [README.md](README.md) — `.venv/bin/streamlit run src/app.py` (line 49); the Hugging Face
  section's `app_file: app.py` and the "a single `app.py` Streamlit entry point" sentence
  (lines 208–223).

### Commit 3 — `style: adopt ruff format and ruff lint` — PURE CHURN, no behavioural change

Config in `pyproject.toml` under `[tool.ruff]` (the file already exists and holds only
name/version).

```toml
line-length = 100
target-version = "py312"
src = ["src"]

[tool.ruff.lint]
select = ["E", "W", "F", "I", "UP", "B", "C4", "SIM", "RET", "RUF"]
ignore = [
  "RUF001", "RUF002", "RUF003",  # IPA symbols and en/em dashes are deliberate throughout
  "UP042",   # str+Enum → StrEnum changes str() semantics on values written to SQLite
  "B905",    # zip(strict=) is a behavioural choice, not churn
  "RUF015",  # list(x)[0] → next(iter(x)) raises StopIteration, not IndexError
  "RET504",  # named returns are used deliberately for readability
]
```

Deliberately **not** selected, with reasons — worth recording so nobody re-litigates them:
`ANN` (312 hits; mypy's job, done better), `T20` (81 hits, all legitimate `print` in
`scripts/`), `ARG` (107 hits, nearly all Streamlit callbacks and test stubs), `TRY` (43 hits of
`TRY003` style opinion), `N` (`N818` would rename `TierNotAcknowledged` → `…Error`, a public
API change, not churn), `PTH` (8 `os.path` → `pathlib` rewrites — a fine follow-up, not this
commit), `D` (would fight docstrings that are already the best documentation in the repo).

Then, in order: `ruff format .`, `ruff check --fix .`, then the ~41 hand fixes. Notable:

- **19 `RUF100` unused-noqa** — every `# noqa: E402` in `scripts/`. Ruff recognises the
  `sys.path` bootstrap pattern and does not flag `E402` there (verified: `ruff check --select
  E402 scripts/` passes clean), so the comments simply go.
- **23 `E501`** — `ruff format` never reflows comment text, so the comment-only ones are hand
  rewraps. Includes one decorative `# --- Grading ---…` rule at
  [practice_queue.py:249](practice_queue.py:249).
- `UP035` (11) `typing.Callable/Iterable` → `collections.abc`; `UP017` (24)
  `datetime.timezone.utc` → `datetime.UTC`; `F401` (7); `F541` (6); `I001` (3).

**Rule for this commit:** if a fix changes runtime behaviour, it does not belong here. Either
add the rule to `ignore` or fix it in commit 4 with a test. Verify with `make test` before and
after — the test count must be identical and every test must pass.

### Commit 4 — `chore: add mypy`

Config as `[tool.mypy]` in `pyproject.toml` rather than a separate `mypy.ini` — one config file
for one project, and per-module overrides read fine as `[[tool.mypy.overrides]]`. (A standalone
`mypy.ini` is a trivial swap if preferred.)

```toml
[tool.mypy]
python_version = "3.12"
mypy_path = "src"
files = ["src", "tests", "scripts"]
warn_unused_configs = true
warn_redundant_casts = true
warn_unused_ignores = true
no_implicit_optional = true
warn_return_any = true
check_untyped_defs = true
```

Then three tiers, and **re-measure inside the container first** — the baseline above was taken
without the real dependencies installed.

- **Strict** (`disallow_untyped_defs`, `disallow_incomplete_defs`, `disallow_any_generics`):
  the four pure modules named in the brief — `utils`, `db`, `budget`, `speech_analyzer` (13
  errors) — plus `audio_utils`, `shadowing`, `perception_trainer`, `practice_queue`,
  `phoneme_reference`, which already measure **zero** at that setting and so cost nothing.
- **Promote if the count holds:** `tts`, `rhythm`, `fallback_coach`, `ai_coach`,
  `progress_view` (~81 combined). All are pure — none imports Streamlit — so they are worth
  the work if the real-dependency count is close to the estimate. If it balloons, leave them on
  the base tier and say so.
- **Base tier only:** `app.py`. **Do not chase 100% on the UI layer in this chunk.**

`[[tool.mypy.overrides]] ignore_missing_imports = true` for the SDKs that ship no stubs — at
minimum `pydub.*` and `azure.cognitiveservices.speech.*`; confirm which others are needed
rather than listing them speculatively.

Real fixes that land here, not in commit 3: two `round(float | None)` calls mypy flags as
genuinely unsafe, and one `Returning Any` at [db.py:324](db.py:324).

Add `ruff` and `mypy` to [requirements.txt](requirements.txt), pinned `==`, **versions verified
against PyPI at implementation time, not recalled** — the repo's standing rule. They go in the
one manifest for the same reason `pytest` already does: the image is a local dev container, and
a second manifest is a second thing to drift.

Add Makefile targets mirroring the CI steps exactly, so a green local run means a green CI run:
`lint`, `format`, `typecheck`, and `check` (= lint + typecheck + test), each a
`docker compose run --rm app …`.

### Commit 5 — `test: block non-loopback sockets in the suite`

Autouse fixture in [tests/conftest.py](tests/conftest.py), patching `socket.socket.connect` to
raise on anything that is not `127.0.0.1` / `::1` / `localhost`.

This is a behavioural change to the suite, hence its own commit. The tests that deliberately
undo `OFFLINE_MODE` — [test_tts.py:19](tests/test_tts.py:19),
[test_budget.py:34](tests/test_budget.py:34), [test_ai_coach.py:102](tests/test_ai_coach.py:102),
[test_parsing.py:363](tests/test_parsing.py:363) — must be re-verified against it. They should
all pass unchanged: each asserts a refusal that happens *before* a client is built. If one
fails, that is the guard finding something worth knowing about.

Add a test that asserts the guard itself fires, so it cannot silently rot into a no-op.

### Commit 6 — `ci: run lint, types and the offline suite on push and PR`

`.github/workflows/ci.yml`:

```yaml
on: [push, pull_request]        # pull_request, never pull_request_target
permissions:
  contents: read                # no job can write anything
concurrency: {group: ci-${{ github.ref }}, cancel-in-progress: true}
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker compose build
      - run: docker compose run --rm app ruff format --check .
      - run: docker compose run --rm app ruff check .
      - run: docker compose run --rm app mypy
      - run: docker compose run --rm -e OFFLINE_MODE=true app python -m pytest -q
```

`-e OFFLINE_MODE=true` is passed on the run step rather than set as a workflow-level `env:`,
because `docker compose run` does not forward host environment into the container and a
workflow-level variable would be decorative.

**Why a CI run cannot spend quota — four independent layers, any one of which suffices:**

1. **`ci.yml` references no `secrets.*` at all.** An unreferenced secret is never injected into
   the job environment. `pull_request` (not `pull_request_target`) means a fork PR could not
   reach repository secrets even if one were added later.
2. **No `.env` exists in a CI checkout** — it is gitignored. `compose.yaml` declares
   `env_file: {path: .env, required: false}`, so the container starts with no
   `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION` or `GEMINI_API_KEY`.
3. **`tests/conftest.py` deletes those three variables and forces `OFFLINE_MODE=true`** for
   every test, whatever the environment says.
4. **The commit-5 socket guard** raises on any non-loopback connection attempt.

`.github/workflows/release.yml` — separate file, so `ci.yml`'s `permissions: contents: read`
is not diluted:

```yaml
on: {push: {tags: ["v*"]}}
permissions:
  contents: write
jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: {fetch-depth: 0}
      - run: gh release create "$GITHUB_REF_NAME" --generate-notes --verify-tag
        env: {GH_TOKEN: "${{ secrets.GITHUB_TOKEN }}"}
```

This one does reference a secret, and that is fine: `GITHUB_TOKEN` is Actions' auto-provisioned
repo token, cannot authenticate to Azure or Google, and the job runs only on a tag push. The
constraint is "no API keys", and it holds.

**This replaces a documented human step.** `CLAUDE.md`'s release rule currently says to run
`gh release create vX.Y.Z --generate-notes` by hand. That is a repo-instruction change, so per
`CLAUDE.md` §3 the exact replacement lines get **proposed to the user, not written
unilaterally**.

### Commit 7 — `docs: record the src/ move, the linters and CI`

- `memory-bank/techContext.md` — architecture section gains the `src/` root and a Tooling
  section (rule set and the reasons above, mypy tiers, the four-layer CI offline argument).
  Verified facts, written directly; anything reading as a judgment call gets proposed first.
- `memory-bank/history.md` — flip the row to `implemented`.
- `README.md` — the three path fixes from commit 2, plus the new `make` targets and a CI badge.

### Finally — GitHub bookkeeping

Close #15, #17, #18. Close #16 **with a comment stating the Python matrix was deliberately not
built, and why** — closing it silently would leave a third of its body looking forgotten. Close
milestone v0.9.0.

Then, per `CLAUDE.md`'s release rule: bump `pyproject.toml` to `0.9.0`, tag, push the tag. That
also serves as the end-to-end proof that `release.yml` works.

## Verification

Everything below runs offline and spends nothing.

1. `docker compose build`
2. `docker compose run --rm app ruff format --check .` → clean
3. `docker compose run --rm app ruff check .` → clean
4. `docker compose run --rm app mypy` → clean
5. `make test` → **632 tests pass**, the count unchanged from `main` (a changed count in the
   churn commit means something behavioural got in)
6. `docker compose run --rm app python -m pytest --collect-only -q | tail -1` → confirm nothing
   under `scripts/` is collected. This is the pytest.ini guarantee; assert it explicitly rather
   than trusting the config survived the move.
7. `grep -n "secrets\." .github/workflows/ci.yml` → **no output**. The structural claim, checked.
8. **The app actually starts from its new path** — `make up` (or the `coach` launch config),
   load `:8501`, confirm the page renders and the tabs are there. Then the
   `coach-seeded-demo` config, which exercises `streamlit run src/app.py` explicitly against
   the seeded offline database. This is the one thing tests cannot prove about commit 2.
9. **A script still resolves its imports** — `docker compose run --rm app python
   scripts/seed_progress_history.py --help`, which touches no network.
10. Push the branch; confirm the `check` job is green in Actions.
11. Push the `v0.9.0` tag; confirm `release.yml` publishes the release.

## Out of scope, stated so it is a decision and not an omission

- **No Python version matrix** (see above).
- **No `PTH` rules** — 8 `os.path` → `pathlib` rewrites, a clean follow-up chunk.
- **No push toward full typing on `app.py`** — 2,672 lines of Streamlit UI, explicitly left on
  the base tier.
- **No exception renames** (`N818`) and **no `StrEnum` migration** (`UP042`) — both change
  behaviour or public API, and both are lint suggestions rather than problems anyone has hit.
