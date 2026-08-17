#!/usr/bin/env python3
"""First-time local setup: create .env and check Docker is available.

Run via `make setup`, not directly — it assumes the project root as cwd.
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"


def ensure_env_file() -> None:
    if ENV_FILE.exists():
        print(f"[setup] {ENV_FILE.name} already exists, leaving it alone")
        return
    shutil.copyfile(ENV_EXAMPLE, ENV_FILE)
    print(f"[setup] created {ENV_FILE.name} from {ENV_EXAMPLE.name} — fill in the required secrets")


def check_docker() -> None:
    if shutil.which("docker") is None:
        print("[setup] docker not found on PATH — install Docker to use `make up`/`make down`")
        return
    try:
        subprocess.run(
            ["docker", "compose", "version"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        print("[setup] docker compose is available")
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("[setup] `docker compose` isn't working — check your Docker install")


def main() -> int:
    ensure_env_file()
    check_docker()
    print("[setup] done — run `make up` to start the app")
    return 0


if __name__ == "__main__":
    sys.exit(main())
