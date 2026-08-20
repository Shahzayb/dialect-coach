#!/usr/bin/env python3
"""Re-measure every stored recording against the current measurement code. Costs nothing.

**This is the promise v0.10.0 made when it started keeping audio.** `audio_utils.keep` and the
`vowel_measurements` table exist so that a changed normalisation scheme or reference table is a
re-derivation over files already on disk, not a request that the passage be read again. This
chunk changed both — `MIN_TRAJECTORY_MS` now refuses a glide the old code measured, and
`model_reference.py` is a second reference table that did not exist — so the script is owed.

No Azure call, no allowance, no network: it re-runs `vowel_measure.extract` over the kept WAV
and the verbatim payload already in the database, and rewrites the token rows.

    docker compose run --rm app python scripts/rederive.py            # report only
    docker compose run --rm app python scripts/rederive.py --write

Refuses to write by default. Rewriting the measurement history is the kind of thing that
should be a decision, and the dry run prints exactly what would change.

**An attempt recorded before v0.10.0 is permanently unmeasurable**: its audio was deleted on
the way out, by the design that then applied. Those are reported and skipped, not failed.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import db  # noqa: E402
import speech_analyzer  # noqa: E402
import utils  # noqa: E402
import vowel_measure  # noqa: E402
from utils import Mode  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Actually rewrite the token rows. Without it this only reports what would change.",
    )
    args = parser.parse_args()
    utils.configure_logging(logging.WARNING)

    conn = db.connect()
    stored = db.stored_audio(conn)
    if not stored:
        print("[rederive] no kept recordings. Nothing to re-derive.")
        return 0

    print(f"[rederive] {len(stored)} kept recording(s)")
    changed = skipped = failed = 0

    for row in stored:
        attempt_id = int(row["attempt_id"])
        path = Path(str(row["path"]))
        if not path.exists():
            print(f"[rederive]   attempt {attempt_id}: audio missing at {path}, skipped")
            skipped += 1
            continue
        try:
            payload = json.loads(str(row["azure_raw_json"]))
            payloads = payload if isinstance(payload, list) else [payload]
            mode = Mode(str(row["mode"]))
            _, _, words = speech_analyzer.normalise(
                payloads, str(row["reference_text"] or ""), mode
            )
        except Exception as exc:  # noqa: BLE001 — one bad row must not lose the rest
            print(f"[rederive]   attempt {attempt_id}: payload unreadable ({exc}), skipped")
            failed += 1
            continue

        before = db.vowel_measurements_for(conn, attempt_id)
        accepted_before = sum(1 for token in before if token["accepted"])
        ceiling = float(before[0]["lpc_ceiling_hz"]) if before else None
        style = str(before[0]["style_tag"]) if before else "read"
        snr = before[0]["snr_db_min"] if before else None

        try:
            measurement = vowel_measure.extract(
                words, path.read_bytes(), ceiling_hz=ceiling, snr_db_min=snr, style=style
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[rederive]   attempt {attempt_id}: measurement failed ({exc})")
            failed += 1
            continue

        accepted_after = len(measurement.accepted)
        glides = sum(1 for token in measurement.accepted if token.trajectory_usable)
        note = (
            "" if accepted_after == accepted_before else f"  ({accepted_before} → {accepted_after})"
        )
        print(
            f"[rederive]   attempt {attempt_id}: {accepted_after} accepted, "
            f"{glides} long enough to measure a glide{note}"
        )
        if args.write:
            db.record_vowel_measurements(conn, attempt_id, vowel_measure.token_rows(measurement))
        changed += 1

    print(f"\n[rederive] {changed} re-measured, {skipped} skipped (audio gone), {failed} failed.")
    if not args.write:
        print("[rederive] dry run — nothing was written. Re-run with --write to keep it.")
    else:
        print("[rederive] token rows rewritten. Baselines are NOT recomputed: re-calibrate")
        print("[rederive] from the Accent tab if the noise floor should move too.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
