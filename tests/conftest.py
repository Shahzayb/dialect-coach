"""Test-suite setup.

Two jobs: put the repo root on `sys.path` so the root-level modules import (there is no
package and no `pyproject.toml`), and force `OFFLINE_MODE=true` for every test so a
misconfigured environment can never turn a test run into a billable API call.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import utils

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURES = ROOT / "tests" / "fixtures"


@pytest.fixture(autouse=True)
def offline_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No network, no keys, deterministic guards — for every test, without opting in.

    `.env` is bind-mounted into the container, so a real one would otherwise be loaded by
    `utils.get` and quietly re-supply the keys this fixture just cleared. Marking dotenv
    as already-loaded keeps the suite reading from the environment set here and nowhere
    else — the tests must behave the same on a machine with credentials and without.
    """
    monkeypatch.setattr(utils, "_dotenv_loaded", True)
    monkeypatch.setenv("OFFLINE_MODE", "true")
    monkeypatch.delenv("AZURE_SPEECH_KEY", raising=False)
    monkeypatch.delenv("AZURE_SPEECH_REGION", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("MIN_DURATION_SECONDS", "1.5")
    monkeypatch.setenv("MAX_DURATION_SECONDS_DRILL", "30")
    monkeypatch.setenv("MAX_DURATION_SECONDS_PARAGRAPH", "120")
    monkeypatch.setenv("MAX_DURATION_SECONDS_UNSCRIPTED", "300")


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES
