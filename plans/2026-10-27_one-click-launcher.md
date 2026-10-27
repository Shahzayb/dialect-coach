# One-click launcher

## Intent

Starting the app is `make up` in a terminal (foreground, `--build` every time), then opening a
browser by hand; stopping is Ctrl-C or `make down`. Replace that with one macOS app icon that
toggles: click once to start and open the browser, click again to stop.

## Design

- `scripts/launcher.py` — stdlib only, runs on the system `/usr/bin/python3` (3.9), so the
  icon works without a project venv.
  - Running container → `docker compose stop` (keeps the container, so the next start is a
    restart, not a create). Notification: "stopped".
  - Not running → start Docker Desktop if its daemon is down and wait for it; `docker compose
    up -d`, adding `--build` only when `Dockerfile` or `requirements.txt` is newer than the
    image (or the image is missing); poll `/healthz`; `open` the URL.
  - `.app` launches get a minimal PATH, so the script prepends the Docker and Homebrew bin dirs.
- `make app` compiles a one-line AppleScript into `~/Applications/Dialect Coach.app` with
  `osacompile`, pointing at this checkout. Re-run it if the repo moves.
- `make up`/`make down` are unchanged.

Measured before the change, daemon already running: `up -d` 0.2 s, healthy 1.5 s later,
`stop` 0.5 s.

## Out of scope

Menu-bar status item, Windows/Linux launchers, a native (non-Docker) run path.
