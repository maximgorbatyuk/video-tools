#!/usr/bin/env python3
"""
start.py
========

Top-level interactive entrypoint for the video-tools repo.

This script does not do any video work itself. It just asks the user which
of the functional scripts under `scripts/` they want to run, then dispatches
to it as a subprocess. Each functional script is independently runnable
(no environment is passed through) — `start.py` is just a friendlier front
door for users who don't yet know which script they want.

Usage:
    python3 start.py [-v|--verbose]

The `-v` / `--verbose` flag is forwarded to the dispatched script so it
prints dim `[d]` diagnostic lines (yt-dlp argv, claude prompt sizes,
call durations, etc.) to stderr. The env var `VIDEO_TOOLS_VERBOSE=1` is
an equivalent way to enable it for the whole shell session.

Add a new functional script:
    1. Drop it into `scripts/` (e.g. `scripts/summarize.py`).
    2. Register it in the SCRIPTS list below.
    3. Update README.md and AGENTS.md per the docs upkeep rule
       (AGENTS.md § 5).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent / "scripts"

# (menu key, label shown to the user, file under scripts/)
SCRIPTS: list[tuple[str, str, str]] = [
    ("1", "Transcribe a YouTube video (mlx_whisper, runs locally)", "transcribe.py"),
    ("2", "Download video + subtitles, optionally translate to EN/RU/KK (claude)", "translate.py"),
]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="start.py",
        description=(
            "Interactive menu for the video-tools repo. Forwards -v/--verbose "
            "to the dispatched functional script."
        ),
    )
    p.add_argument(
        "-v", "--verbose",
        action="store_true",
        help=(
            "Forward --verbose to the dispatched script (or set "
            "VIDEO_TOOLS_VERBOSE=1 in the environment)."
        ),
    )
    return p.parse_args(argv)


def main() -> int:
    args = _parse_args()
    verbose = args.verbose or os.environ.get("VIDEO_TOOLS_VERBOSE", "").strip() not in ("", "0", "false", "False")

    print("=== video-tools ===\n")
    print("What do you want to do?")
    for key, label, _ in SCRIPTS:
        print(f"  [{key}] {label}")
    print()

    keys = [key for key, _, _ in SCRIPTS]
    valid = set(keys)
    while True:
        choice = input(f"Pick {'/'.join(keys)}: ").strip()
        if choice in valid:
            break
        print(f"Please enter one of: {', '.join(keys)}.")

    target = SCRIPTS_DIR / next(name for key, _, name in SCRIPTS if key == choice)
    if not target.exists():
        print(f"error: expected script not found at {target}", file=sys.stderr)
        return 1

    cmd = [sys.executable, str(target)]
    if verbose:
        cmd.append("--verbose")
    print()
    result = subprocess.run(cmd)
    return result.returncode


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print()
        print("Interrupted.")
        sys.exit(130)
