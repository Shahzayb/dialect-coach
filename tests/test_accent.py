"""The accent surfaces: the four-column contract, the Accent tab, and the calibration flow."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import accent_view
import db
import progress_view
import vowel_measure
from utils import Mode

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_vowel_measure import INVENTORY_SPEC, build_recording

APP = str(Path(__file__).resolve().parent.parent / "src" / "app.py")


# --- The output contract ---------------------------------------------------------------------


def test_the_markdown_table_has_exactly_the_four_headers_in_order() -> None:
    """Every accent surface in this project renders findings through this one function.

    The headers are the contract, so they get an assertion of their own rather than being
    covered incidentally: a fifth column, or a reordering, would silently change what every
    later chunk is expected to produce.
    """
    rows = [
        vowel_measure.Finding(
            "/i/ FLEECE — F2 (Lobanov z)", "+1.12 (n=14)", "+1.68", "−0.56 → front"
        )
    ]
    rendered = accent_view.to_markdown(rows)
    lines = rendered.splitlines()
    assert lines[0] == (
        "| Acoustic Feature | User Realization | Target Realization | Delta / Adjustment Needed |"
    )
    assert lines[1] == "|---|---|---|---|"
    assert lines[2].startswith("| /i/ FLEECE — F2 (Lobanov z) | +1.12 (n=14) | +1.68 |")


def test_an_empty_table_says_so_rather_than_rendering_a_bare_header() -> None:
    assert accent_view.to_markdown([]) == accent_view.EMPTY_TABLE


def test_a_pipe_in_a_cell_cannot_break_the_table() -> None:
    """A raw pipe would split a cell and a newline would end the table mid-row."""
    import re

    rows = [vowel_measure.Finding("a|b", "c|d", "e\nf", "g")]
    rendered = accent_view.to_markdown(rows).splitlines()[2]
    # Five UNESCAPED pipes: the four cells' delimiters. The pipes inside the cells survive as
    # `\|`, which is what keeps them visible to the reader instead of structural.
    assert len(re.findall(r"(?<!\\)\|", rendered)) == 5
    assert rendered == r"| a\|b | c\|d | e f | g |"


def test_the_frame_carries_the_same_four_columns() -> None:
    frame = accent_view.findings_frame([vowel_measure.Finding("f", "u", "t", "d")])
    assert list(frame.columns) == list(vowel_measure.COLUMNS)


# --- The chart ---------------------------------------------------------------------------------


def test_the_vowel_chart_reverses_both_axes() -> None:
    """A vowel chart is a schematic of the mouth, not an ordinary scatter plot.

    Front vowels belong on the left and close vowels on top. Drawn unreversed, the same
    numbers are upside down and back to front — still a picture, and unreadable to anyone who
    has seen a vowel chart before.
    """
    positions = vowel_measure.reference_positions("men")
    frame = accent_view.vowel_frame(positions, positions)
    spec = accent_view.vowel_chart(frame).to_dict()
    encodings = [layer["encoding"] for layer in spec["layer"]]
    for encoding in encodings:
        assert encoding["x"]["scale"]["reverse"] is True
        assert encoding["y"]["scale"]["reverse"] is True


def test_the_chart_encodes_the_token_count_so_thin_evidence_looks_thin() -> None:
    positions = vowel_measure.reference_positions("men")
    frame = accent_view.vowel_frame(positions, positions)
    assert "n" in frame.columns
    spec = accent_view.vowel_chart(frame).to_dict()
    assert any("size" in layer["encoding"] for layer in spec["layer"])


def test_movement_inside_the_band_is_never_reported_as_change() -> None:
    positions = vowel_measure.reference_positions("men")
    moved = {
        vowel: vowel_measure.VowelPosition(
            **{
                **{field: getattr(position, field) for field in position.__dataclass_fields__},
                "f1_z": (position.f1_z or 0.0) + 0.05,
            }
        )
        for vowel, position in positions.items()
    }
    floor = vowel_measure.NoiseFloor(per_vowel={}, median_z=0.30, vowels=0)
    rows = accent_view.movement_rows(positions, moved, floor)
    assert rows
    assert all(vowel_measure.WITHIN_NOISE in row.delta for row in rows)

    # And with no floor measured at all, movement is not called progress either.
    unbanded = accent_view.movement_rows(positions, moved, None)
    assert all("cannot be called progress" in row.delta for row in unbanded)


def test_the_noise_caption_says_what_the_band_forbids() -> None:
    floor = vowel_measure.NoiseFloor(per_vowel={"i": 0.3}, median_z=0.30, vowels=1)
    caption = accent_view.noise_caption(floor)
    assert "0.30 z" in caption
    assert "flattering" in caption
    assert "No noise floor yet" in accent_view.noise_caption(None)


# --- Storage round trip -------------------------------------------------------------------------


@pytest.fixture(scope="module")
def calibration_pair():
    """Two synthetic reads of the same inventory, as a calibration would produce."""
    first_wav, first_words = build_recording(INVENTORY_SPEC * 3, drift=1.00)
    second_wav, second_words = build_recording(INVENTORY_SPEC * 3, drift=1.02)
    first = vowel_measure.extract(first_words, first_wav, ceiling_hz=5000.0, snr_db_min=30.0)
    second = vowel_measure.extract(second_words, second_wav, ceiling_hz=5000.0, snr_db_min=30.0)
    return first, second


def test_tokens_survive_a_round_trip_through_the_database(calibration_pair) -> None:
    """This is what makes the stored rows worth storing: re-derivation without re-recording."""
    measurement, _ = calibration_pair
    conn = db.connect(":memory:")
    attempt_id = db.record_attempt(
        conn,
        mode=Mode.PARAGRAPH,
        reference_text=progress_view.BENCHMARK_PASSAGE,
        recognised_text="x",
        audio_seconds=90.0,
        audio_sha256="a" * 64,
        overall_scores={"snr_db_min": 30.0},
        azure_raw={},
    )
    written = db.record_vowel_measurements(conn, attempt_id, vowel_measure.token_rows(measurement))
    assert written == len(measurement.tokens)

    rows = [dict(row) for row in db.vowel_measurements_for(conn, attempt_id)]
    rebuilt = vowel_measure.tokens_from_rows(rows)
    assert len(rebuilt) == len(measurement.tokens)

    original = vowel_measure.positions(
        measurement.accepted, vowel_measure.lobanov(measurement.accepted)
    )
    recovered = vowel_measure.positions(
        [t for t in rebuilt if t.accepted],
        vowel_measure.lobanov([t for t in rebuilt if t.accepted]),
    )
    for vowel, position in original.items():
        assert recovered[vowel].f1_hz == pytest.approx(position.f1_hz)
        assert recovered[vowel].f2_hz == pytest.approx(position.f2_hz)
        assert recovered[vowel].n == position.n
    conn.close()


def test_rejected_tokens_are_stored_too(calibration_pair) -> None:
    """What was refused, and why, is the only record that a token existed at all."""
    measurement, _ = calibration_pair
    rows = vowel_measure.token_rows(measurement)
    assert any(row["accepted"] == 0 for row in rows) or not measurement.rejected
    assert all("rejected_reason" in row for row in rows)


def test_a_baseline_round_trips_and_retires_the_one_it_replaces(calibration_pair) -> None:
    first, second = calibration_pair
    baseline = vowel_measure.calibrate(
        first.accepted, second.accepted, reference_set="men", ceiling_hz=5000.0
    )
    conn = db.connect(":memory:")
    for _ in range(2):
        db.save_baseline(
            conn,
            positions=vowel_measure.positions_to_json(baseline.positions),
            normaliser=vowel_measure.normaliser_to_json(baseline.normaliser),
            noise_floor=vowel_measure.noise_to_json(baseline.noise),
            lpc_ceiling_hz=baseline.ceiling_hz,
            reference_set="men",
            style_tag="read",
            tokens=baseline.tokens,
            attempt_ids=(1, 2),
        )
    assert len(db.baseline_history(conn)) == 2
    current = db.current_baseline(conn, style="read")
    assert current is not None
    # Exactly one row is current, or every z-score on screen is ambiguous about its space.
    assert sum(1 for row in db.baseline_history(conn) if row["superseded_at"] is None) == 1

    recovered = vowel_measure.noise_from_json(json.loads(current["noise_floor_json"]))
    assert recovered.vowels == baseline.noise.vowels
    assert recovered.median_z == pytest.approx(baseline.noise.median_z)
    conn.close()


def test_calibration_is_refused_when_too_few_vowels_can_be_compared() -> None:
    wav, words = build_recording(INVENTORY_SPEC[:3] * 3)
    one = vowel_measure.extract(words, wav, ceiling_hz=5000.0)
    with pytest.raises((vowel_measure.CalibrationRefused, vowel_measure.TooFewTokens)):
        vowel_measure.calibrate(one.accepted, one.accepted, reference_set="men", ceiling_hz=5000.0)


def test_calibration_normalises_both_reads_through_one_centroid(calibration_pair) -> None:
    """Otherwise Lobanov absorbs the between-session drift and the floor comes out too small.

    A flatteringly small band is not a cosmetic problem: it is what would license reporting
    microphone placement as progress, which is the whole failure the second read prevents.
    """
    first, second = calibration_pair
    baseline = vowel_measure.calibrate(
        first.accepted, second.accepted, reference_set="men", ceiling_hz=5000.0
    )
    own = vowel_measure.lobanov(second.accepted, categories=vowel_measure.REFERENCE_CATEGORIES)
    shared = vowel_measure.noise_floor(
        vowel_measure.positions(first.accepted, baseline.normaliser),
        vowel_measure.positions(second.accepted, baseline.normaliser),
    )
    separate = vowel_measure.noise_floor(
        vowel_measure.positions(first.accepted, baseline.normaliser),
        vowel_measure.positions(second.accepted, own),
    )
    assert shared.median_z is not None and separate.median_z is not None
    assert baseline.noise.median_z == pytest.approx(shared.median_z)


# --- The Accent tab ------------------------------------------------------------------------------


def _app(**env) -> AppTest:
    app = AppTest.from_file(APP, default_timeout=60)
    for key, value in env.items():
        app.session_state[key] = value
    app.run()
    return app


def test_the_accent_tab_refuses_to_score_without_a_reference_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No default and never an average: the wrong set is wrong by the size of the effect."""
    monkeypatch.setenv("GA_REFERENCE_SET", "")
    app = _app()
    assert not app.exception
    assert any("GA_REFERENCE_SET" in warning.value for warning in app.warning)


def test_the_accent_tab_says_there_is_no_baseline_yet(monkeypatch: pytest.MonkeyPatch) -> None:
    """The day-one state has to be words, not an empty chart."""
    monkeypatch.setenv("GA_REFERENCE_SET", "men")
    app = _app()
    assert not app.exception
    assert any("No baseline yet" in info.value for info in app.info)
    assert any("calibration reads so far" in info.value for info in app.info)


def _seed_calibration(pair, *, minutes_apart: int) -> tuple[int, int]:
    """Write two calibration reads into the app's own database, as a session would.

    The two reads are the benchmark passage, tagged `read`, with their vowel tokens stored —
    which is exactly the state the Accent tab looks for.
    """
    import os

    first, second = pair
    conn = db.connect(os.environ["DB_PATH"])
    ids: list[int] = []
    for index, (measurement, when) in enumerate(
        ((first, "2026-07-01T08:00:00Z"), (second, f"2026-07-01T08:{minutes_apart:02d}:00Z"))
    ):
        attempt_id = db.record_attempt(
            conn,
            mode=Mode.PARAGRAPH,
            reference_text=progress_view.BENCHMARK_PASSAGE,
            recognised_text=progress_view.BENCHMARK_PASSAGE,
            audio_seconds=90.0,
            audio_sha256=f"calibration-{index}",
            overall_scores={"snr_db_min": 30.0},
            azure_raw={},
            created_at=when,
        )
        db.tag_attempt(conn, attempt_id, "read")
        db.record_vowel_measurements(conn, attempt_id, vowel_measure.token_rows(measurement))
        ids.append(attempt_id)
    conn.close()
    return ids[0], ids[1]


def test_two_back_to_back_reads_are_refused_as_a_calibration(
    calibration_pair, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gap is the whole point of reading it twice.

    Two reads taken minutes apart measure a microphone holding still. The band that produced
    would be flatteringly small, and a flatteringly small band is what licenses noise being
    reported as progress.
    """
    monkeypatch.setenv("GA_REFERENCE_SET", "men")
    _seed_calibration(calibration_pair, minutes_apart=2)
    app = _app()
    assert not app.exception
    assert any("minutes apart" in warning.value for warning in app.warning)
    assert any("flatteringly small" in warning.value for warning in app.warning)


def test_a_proper_calibration_stores_a_baseline_and_a_noise_floor(
    calibration_pair, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exit criterion, end to end: two reads in, a baseline and a band out."""
    import os

    monkeypatch.setenv("GA_REFERENCE_SET", "men")
    older, newer = _seed_calibration(calibration_pair, minutes_apart=15)

    app = _app()
    assert not app.exception
    calibrate = [b for b in app.button if "Set the read baseline" in b.label]
    assert calibrate, "no calibration button offered"
    calibrate[0].click().run()

    conn = db.connect(os.environ["DB_PATH"])
    row = db.current_baseline(conn, style="read")
    assert row is not None, "no baseline was stored"
    assert row["reference_set"] == "men"
    assert row["style_tag"] == "read"
    assert json.loads(row["attempt_ids"]) == [older, newer]

    floor = vowel_measure.noise_from_json(json.loads(row["noise_floor_json"]))
    assert floor.vowels >= vowel_measure.MIN_CATEGORIES
    assert floor.median_z is not None and floor.median_z > 0.0

    positions = vowel_measure.positions_from_json(json.loads(row["positions_json"]))
    assert len(positions) >= vowel_measure.MIN_CATEGORIES
    conn.close()


def test_the_baseline_renders_a_chart_and_states_what_the_band_forbids(
    calibration_pair, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GA_REFERENCE_SET", "men")
    _seed_calibration(calibration_pair, minutes_apart=15)

    app = _app()
    [b for b in app.button if "Set the read baseline" in b.label][0].click().run()
    assert not app.exception

    markdown = " ".join(block.value for block in app.markdown)
    assert "Measurement noise floor" in markdown
    assert "including when it moves the flattering way" in markdown
    # The per-vowel band table goes through the same four-column renderer as everything else.
    assert "| Acoustic Feature | User Realization |" in markdown
    assert "between-read displacement" in markdown


def test_the_chart_keeps_a_series_legend() -> None:
    """Without it there is no way to tell your own vowels from the target's.

    Regression, found in the browser rather than in a test: altair merges a layered chart's
    legends, so `legend=None` on the label layer removed the merged colour legend for both.
    The size legend survived, which made the loss easy to miss — the chart still looked
    finished and simply did not say which dots were whose.
    """
    positions = vowel_measure.reference_positions("men")
    frame = accent_view.vowel_frame(positions, positions)
    spec = accent_view.vowel_chart(frame).to_dict()
    for layer in spec["layer"]:
        colour = layer["encoding"]["color"]
        assert colour["field"] == "series"
        assert colour.get("legend") is not None or "legend" not in colour, (
            "a layer suppressing the colour legend suppresses the merged one"
        )


def test_the_rejection_table_renders_when_nothing_can_be_normalised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The branch that shows what was refused when there is nothing else to show.

    It exists so a measurement that produced no usable vowels is still legible — a thin table,
    visibly thin — and it was reached by no test until a rename broke it and only mypy noticed.
    """
    wav, words = build_recording([("had", [("h", 60), ("æ", 20), ("d", 70)])] * 4)
    measurement = vowel_measure.extract(words, wav, ceiling_hz=5000.0, snr_db_min=30.0)
    assert not measurement.accepted, "the fixture is meant to have nothing usable"

    rows = vowel_measure.rejection_findings(measurement.tokens)
    assert rows
    rendered = accent_view.to_markdown(rows)
    assert "| Acoustic Feature |" in rendered
    assert vowel_measure.REJECT_SHORT in rendered
    assert "Refused rather than guessed" in rendered


def test_the_suite_never_writes_a_recording_into_the_working_tree(tmp_path) -> None:
    """`audio/` being gitignored is not the same promise as never being written to.

    `audio_utils.keep` writes real WAV bytes, and its default path is inside the repository.
    The offline suite runs `run_assessment_job` in several places, so without an isolated
    `AUDIO_DIR` a plain `make test` leaves recordings in the checkout — which it did, until
    this fixture was added.
    """
    import os

    import audio_utils

    assert (
        os.environ["AUDIO_DIR"].startswith(str(tmp_path.parent.parent))
        or "audio" in (os.environ["AUDIO_DIR"])
    )
    kept = audio_utils.keep(b"RIFF" + b"\x00" * 40, "a" * 64)
    assert kept is not None
    assert not str(kept.resolve()).startswith(str(Path(__file__).resolve().parent.parent))


# --- The chart page, end to end ------------------------------------------------------------------


def test_the_accent_page_charts_a_reading_once_a_baseline_exists(
    calibration_pair, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Six chart-and-table pairs, each rendering the SAME rows the whole table carries."""

    monkeypatch.setenv("GA_REFERENCE_SET", "men")
    _seed_calibration(calibration_pair, minutes_apart=15)

    app = _app()
    assert not app.exception
    calibrate = [b for b in app.button if "Set the read baseline" in b.label]
    calibrate[0].click().run()
    assert not app.exception

    headings = " ".join(block.value for block in app.markdown)
    for instrument in ("Rhoticity", "Vowel space", "Diphthongs", "Vowel length", "Rhythm"):
        assert instrument in headings, f"the {instrument} section did not render"

    # Every rendered table keeps the four-column contract.
    tables = [block.value for block in app.markdown if "| Acoustic Feature |" in block.value]
    assert tables, "no four-column table rendered beside any chart"
    for table in tables:
        assert table.splitlines()[0] == (
            "| Acoustic Feature | User Realization | Target Realization "
            "| Delta / Adjustment Needed |"
        )


def test_the_page_says_it_is_post_hoc_rather_than_live(
    calibration_pair, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #14 sketches real-time overlapping curves. This is drawn after the fact."""
    monkeypatch.setenv("GA_REFERENCE_SET", "men")
    _seed_calibration(calibration_pair, minutes_apart=15)
    app = _app()
    [b for b in app.button if "Set the read baseline" in b.label][0].click().run()
    text = " ".join(block.value for block in app.caption) + " ".join(
        block.value for block in app.markdown
    )
    assert "Post-hoc" in text or "post-hoc" in text


def test_the_charts_refuse_before_a_baseline_exists(
    calibration_pair, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A confident dot drawn from a normalisation that does not exist is the failure here."""
    monkeypatch.setenv("GA_REFERENCE_SET", "men")
    _seed_calibration(calibration_pair, minutes_apart=15)
    app = _app()  # no baseline stored yet — the button has not been clicked
    assert not app.exception
    text = " ".join(block.value for block in app.info) + " ".join(
        block.value for block in app.markdown
    )
    assert "calibration passage" in text or "No stored baseline" in text


# --- Which reading is on screen ------------------------------------------------------------------
# The 2026-08-20 defect: the tab drew attempt 12's acoustics under attempt 10's label. The cause
# is not "the render where `options` grows" — it is that `st.rerun()` builds `RerunData` with no
# widget states (`streamlit/commands/execution_control.py`), a `RerunException` is not a premature
# stop (`scriptrunner/exec_code.py`), and any rerun raised in Today or Practice ends the script
# before the Accent tab renders. The selector is then stale, its stored value is deleted, and the
# next full run re-registers it from scratch — landing on positional index 0, the newest reading,
# while the browser still paints the label it already had.


def _seed_reading(measurement, *, when: str, sha: str) -> int:
    """One more measured reading, stored the way a finished assessment stores it."""
    import os

    conn = db.connect(os.environ["DB_PATH"])
    attempt_id = db.record_attempt(
        conn,
        mode=Mode.PARAGRAPH,
        reference_text=progress_view.BENCHMARK_PASSAGE,
        recognised_text=progress_view.BENCHMARK_PASSAGE,
        audio_seconds=90.0,
        audio_sha256=sha,
        overall_scores={"snr_db_min": 30.0},
        azure_raw={},
        created_at=when,
    )
    db.tag_attempt(conn, attempt_id, "read")
    db.record_vowel_measurements(conn, attempt_id, vowel_measure.token_rows(measurement))
    conn.close()
    return attempt_id


def _reading_picker(app: AppTest):
    return next(box for box in app.selectbox if box.label == "Which reading?")


def _calibrated(calibration_pair, monkeypatch: pytest.MonkeyPatch) -> tuple[AppTest, int, int]:
    monkeypatch.setenv("GA_REFERENCE_SET", "men")
    older, newer = _seed_calibration(calibration_pair, minutes_apart=15)
    app = _app()
    [b for b in app.button if "Set the read baseline" in b.label][0].click().run()
    assert not app.exception
    return app, older, newer


def test_a_rerun_that_never_reaches_the_accent_tab_keeps_the_chosen_reading(
    calibration_pair, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The selection must survive a rerun raised before this tab is reached.

    `🎧 Shadow this passage` calls `st.rerun()` from the Today tab, which is the same shape as
    the assessment poll that was running when this was found live: the script ends, the Accent
    tab never registers its selector, and a server-initiated rerun carries nothing back from the
    browser to restore it with.
    """
    app, older, _newer = _calibrated(calibration_pair, monkeypatch)

    _reading_picker(app).set_value(older).run()
    assert _reading_picker(app).value == older, "the picker would not take an explicit choice"

    next(b for b in app.button if "Shadow this passage" in b.label).click().run()
    assert not app.exception
    assert _reading_picker(app).value == older, (
        "a rerun raised before the Accent tab silently moved the selection"
    )


def test_a_newly_stored_reading_does_not_steal_the_selection(
    calibration_pair, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The live symptom: a new attempt lands and the charts quietly switch to it."""
    app, older, _newer = _calibrated(calibration_pair, monkeypatch)

    _reading_picker(app).set_value(older).run()
    latest = _seed_reading(calibration_pair[0], when="2026-07-01T09:00:00Z", sha="later-read")

    next(b for b in app.button if "Shadow this passage" in b.label).click().run()
    assert not app.exception
    picker = _reading_picker(app)
    assert latest in picker.options or any(f"#{latest}" in option for option in picker.options), (
        "the new reading never reached the picker, so this proves nothing"
    )
    assert picker.value == older, "the newest reading took the selection without being chosen"
    assert any(f"Plotting #{older}" in c.value for c in app.caption), (
        "the page must state which reading it drew, sourced from the measurement it loaded"
    )
