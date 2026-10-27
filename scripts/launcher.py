#!/usr/bin/env python3
"""One-click start/stop toggle: starts the app and opens the browser, or stops it if running.

Run by the `Dialect Coach.app` icon that `make app` installs. Stdlib only and Python 3.9
compatible, because the icon runs it with macOS's own /usr/bin/python3, not a project venv.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

# Not datetime.UTC, which ruff's py312 target prefers: it arrived in 3.11 and this runs on 3.9.
UTC = timezone.utc  # noqa: UP017

ROOT = Path(__file__).resolve().parent.parent
URL = "http://localhost:8501"
IMAGE = "dialect-coach-app:local"
# An .app launch inherits launchd's minimal PATH, which has neither docker nor Homebrew.
EXTRA_PATH = [
    "/usr/local/bin",
    "/opt/homebrew/bin",
    "/Applications/Docker.app/Contents/Resources/bin",
]


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True)


def notify(message: str) -> None:
    script = f'display notification "{message}" with title "Dialect Coach"'
    subprocess.run(["osascript", "-e", script], capture_output=True)


def wait_for(check: Callable[[], bool], timeout: float, interval: float = 0.25) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(interval)
    return False


def docker_ready() -> bool:
    return run("docker", "info", "--format", "{{.ServerVersion}}").returncode == 0


def app_running() -> bool:
    return bool(run("docker", "compose", "ps", "-q", "--status", "running").stdout.strip())


def healthy() -> bool:
    try:
        with urllib.request.urlopen(f"{URL}/_stcore/health", timeout=1) as response:
            return bool(response.status == 200)
    except OSError:
        return False


def image_created() -> datetime | None:
    result = run("docker", "image", "inspect", IMAGE, "--format", "{{.Created}}")
    if result.returncode != 0:
        return None
    # Docker prints nanoseconds; fromisoformat on 3.9 takes at most microseconds and no "Z".
    stamp = result.stdout.strip().replace("Z", "")
    whole, _, fraction = stamp.partition(".")
    return datetime.fromisoformat(f"{whole}.{fraction[:6] or '0'}").replace(tzinfo=UTC)


def needs_build() -> bool:
    """Rebuild only when the image is missing or older than what it is built from."""
    created = image_created()
    if created is None:
        return True
    inputs = [ROOT / "Dockerfile", ROOT / "requirements.txt"]
    newest = max(datetime.fromtimestamp(p.stat().st_mtime, UTC) for p in inputs)
    return newest > created


def start() -> int:
    if not docker_ready():
        notify("Starting Docker…")
        subprocess.run(["open", "-g", "-a", "Docker"])
        if not wait_for(docker_ready, timeout=120, interval=1):
            notify("Docker did not start")
            return 1
    command: list[str] = ["docker", "compose", "up", "-d"]
    if needs_build():
        notify("Rebuilding image — this one takes a while")
        command.append("--build")
    result = run(*command)
    if result.returncode != 0:
        notify("Failed to start — run `make up` to see why")
        sys.stderr.write(result.stderr)
        return 1
    if not wait_for(healthy, timeout=60):
        notify("Started, but the app is not answering")
        return 1
    subprocess.run(["open", URL])
    return 0


def stop() -> int:
    result = run("docker", "compose", "stop", "-t", "3")
    notify("Stopped" if result.returncode == 0 else "Failed to stop")
    return result.returncode


def main() -> int:
    os.environ["PATH"] = os.pathsep.join([*EXTRA_PATH, os.environ.get("PATH", "")])
    if docker_ready() and app_running():
        return stop()
    return start()


if __name__ == "__main__":
    sys.exit(main())
