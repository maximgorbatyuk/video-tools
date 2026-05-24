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
    python3 start.py

Add a new functional script:
    1. Drop it into `scripts/` (e.g. `scripts/summarize.py`).
    2. Register it in the SCRIPTS list below.
    3. Update README.md and AGENTS.md per the docs upkeep rule
       (AGENTS.md § 5).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent / "scripts"

# (menu key, label shown to the user, file under scripts/)
SCRIPTS: list[tuple[str, str, str]] = [
    ("1", "Transcribe a YouTube video (mlx_whisper, runs locally)", "transcribe.py"),
    ("2", "Translate YouTube subtitles to English or Russian (claude)", "translate.py"),
]


def main() -> int:
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

    print()
    result = subprocess.run([sys.executable, str(target)])
    return result.returncode


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print()
        print("Interrupted.")
        sys.exit(130)
