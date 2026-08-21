#!/usr/bin/env python3
"""Seed a read baseline and one spontaneous reading, so the register split can be seen offline.

The Mode C accent surfaces are mostly REFUSALS — a spontaneous reading is not normalised
against a read baseline, and the page has to say which baseline is missing rather than that
none exists. Refusals are hard to check by hand without a database that has exactly one style
calibrated, which is what this builds.

Synthetic tokens, deliberately: nothing here is a measurement of anybody's voice and no chart
drawn from it means anything about an accent. It exists so the gates, the messages and the
calibration panels can be driven without spending a second of Azure allowance.

    docker compose run --rm -e DB_PATH=data/seed_demo.db -e GA_REFERENCE_SET=men \
        app python scripts/seed_accent_demo.py

Write to a THROWAWAY DB_PATH. It also has to be a database no app is holding open: SQLite's
WAL is not readable across processes over the macOS bind mount, and writing to a live one
leaves "database disk image is malformed" behind.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import db  # noqa: E402
import progress_view  # noqa: E402
import utils  # noqa: E402
import vowel_measure as vm  # noqa: E402
from acoustics import FormantPoint  # noqa: E402
from utils import Mode  # noqa: E402

PROMPT = "Explain a technical decision you made recently."

# Enough per category to clear `MIN_TOKENS_PER_CATEGORY` on the read side, and deliberately
# NOT enough on the spontaneous side: an uneven, thin inventory is what free speech actually
# produces, and the refusal to plot a lonely category is part of what this seeds.
READ_TOKENS_PER_CATEGORY = 6
SPONTANEOUS_TOKENS_PER_CATEGORY = 2


def _tokens(style: str, per_category: int, spread: float) -> list[vm.Token]:
    """A synthetic inventory: one cluster per reference category, jittered by `spread`."""
    built: list[vm.Token] = []
    for index, vowel in enumerate(vm.REFERENCE_CATEGORIES):
        for repeat in range(per_category):
            point = FormantPoint(
                400.0 + 40.0 * index + random.gauss(0, 20) * spread,
                1200.0 + 70.0 * index + random.gauss(0, 40) * spread,
                2600.0 + random.gauss(0, 40) * spread,
                60.0,
                90.0,
                130.0,
            )
            position = index * per_category + repeat
            built.append(
                vm.Token(
                    vowel=vowel,
                    word=f"w{index}{repeat}",
                    word_index=position,
                    start_s=0.1 * position,
                    end_s=0.1 * position + 0.12,
                    duration_ms=120.0,
                    at20=point,
                    at50=point,
                    at80=point,
                    rms_dbfs=-20.0,
                    f0_hz=120.0,
                    stress=1,
                    azure_score=90.0,
                    coda_voiceless=None,
                    accepted=True,
                    style=style,
                )
            )
    return built


def _store(conn, *, mode: Mode, text: str, style: str, tokens: list[vm.Token], when: str) -> int:
    attempt_id = db.record_attempt(
        conn,
        mode=mode,
        reference_text=text,
        recognised_text=text,
        audio_seconds=90.0,
        audio_sha256=f"seed-{when}",
        overall_scores={"pron_score": 88.0, "accuracy": 92.0, "fluency": 88.0, "prosody": 82.0},
        azure_raw={},
        created_at=when,
    )
    db.tag_attempt(conn, attempt_id, style)
    measurement = vm.Measurement(
        tokens=tuple(tokens), ceiling_hz=5000.0, snr_db_min=30.0, style=style
    )
    db.record_vowel_measurements(conn, attempt_id, vm.token_rows(measurement))
    return attempt_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=7, help="RNG seed, so runs are repeatable")
    args = parser.parse_args()

    random.seed(args.seed)
    utils.configure_logging()
    conn = db.connect()

    reference_set = (utils.get("GA_REFERENCE_SET") or "").strip().lower()
    if reference_set not in vm.vowel_reference.REFERENCE_SETS:
        print("[seed] set GA_REFERENCE_SET to 'men' or 'women' first — there is no default.")
        return 1

    passage = progress_view.BENCHMARK_PASSAGE
    first_id = _store(
        conn,
        mode=Mode.PARAGRAPH,
        text=passage,
        style=vm.STYLE_READ,
        tokens=_tokens(vm.STYLE_READ, READ_TOKENS_PER_CATEGORY, 1.0),
        when="2026-08-21T09:00:00Z",
    )
    second_id = _store(
        conn,
        mode=Mode.PARAGRAPH,
        text=passage,
        style=vm.STYLE_READ,
        tokens=_tokens(vm.STYLE_READ, READ_TOKENS_PER_CATEGORY, 1.0),
        when="2026-08-21T09:20:00Z",
    )

    baseline = vm.calibrate(
        vm.tokens_from_rows([dict(r) for r in db.vowel_measurements_for(conn, first_id)]),
        vm.tokens_from_rows([dict(r) for r in db.vowel_measurements_for(conn, second_id)]),
        reference_set=reference_set,
        ceiling_hz=5000.0,
        style=vm.STYLE_READ,
        attempt_ids=(first_id, second_id),
    )
    db.save_baseline(
        conn,
        positions=vm.positions_to_json(baseline.positions),
        normaliser=vm.normaliser_to_json(baseline.normaliser),
        noise_floor=vm.noise_to_json(baseline.noise),
        lpc_ceiling_hz=baseline.ceiling_hz,
        reference_set=reference_set,
        style_tag=vm.STYLE_READ,
        tokens=baseline.tokens,
        attempt_ids=baseline.attempt_ids,
    )

    # One spontaneous reading and no spontaneous baseline. That is the state worth seeing: the
    # accent surfaces must refuse it, and must say the SPONTANEOUS baseline is what is missing
    # rather than that no baseline exists.
    spontaneous_id = _store(
        conn,
        mode=Mode.UNSCRIPTED,
        text=PROMPT,
        style=vm.STYLE_SPONTANEOUS,
        tokens=_tokens(vm.STYLE_SPONTANEOUS, SPONTANEOUS_TOKENS_PER_CATEGORY, 2.0),
        when="2026-08-21T10:00:00Z",
    )

    print(f"[seed] read attempts {first_id} and {second_id}, calibrated as a read baseline")
    print(f"[seed] spontaneous attempt {spontaneous_id}, deliberately with NO baseline")
    print(f"[seed] read baseline: {db.current_baseline(conn, style=vm.STYLE_READ) is not None}")
    print(
        f"[seed] spontaneous baseline: "
        f"{db.current_baseline(conn, style=vm.STYLE_SPONTANEOUS) is not None}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
