"""
Shared helpers for the functional scripts in this folder.

This module is NOT a runnable script and is not registered in start.py.
It exposes:

    Log helpers:    info / ok / warn / err / die
    Preflight:      check_yt_dlp, check_ffmpeg
    YouTube:        YOUTUBE_ID_RE, prompt_youtube_url, get_video_id, download_video
    SRT:            SRT_TIMECODE_RE, count_cue_blocks

Both scripts/transcribe.py and scripts/translate.py import from here.
Keep this module side-effect-free at import time.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


# ----------------------------------------------------------------------------
# Pretty printing helpers
# ----------------------------------------------------------------------------

def info(msg: str) -> None:
    print(f"\033[36m[i]\033[0m {msg}")


def ok(msg: str) -> None:
    print(f"\033[32m[+]\033[0m {msg}")


def warn(msg: str) -> None:
    print(f"\033[33m[!]\033[0m {msg}")


def err(msg: str) -> None:
    print(f"\033[31m[x]\033[0m {msg}", file=sys.stderr)


def die(msg: str, hint: Optional[str] = None) -> "None":
    err(msg)
    if hint:
        print()
        print(hint)
    sys.exit(1)


# ----------------------------------------------------------------------------
# Preflight checks (host CLI dependencies common to multiple scripts)
# ----------------------------------------------------------------------------

def check_yt_dlp() -> None:
    if shutil.which("yt-dlp") is None:
        die(
            "`yt-dlp` is not installed or not on PATH.",
            "Install it with Homebrew:\n"
            "    brew install yt-dlp",
        )
    ok("yt-dlp found")


def check_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        die(
            "`ffmpeg` is not installed or not on PATH (yt-dlp needs it to "
            "merge 720p video + audio streams).",
            "Install it with Homebrew:\n"
            "    brew install ffmpeg",
        )
    ok("ffmpeg found")


# ----------------------------------------------------------------------------
# YouTube URL handling
# ----------------------------------------------------------------------------

YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def prompt_youtube_url() -> str:
    while True:
        url = input("\nPaste the YouTube URL: ").strip()
        if not url:
            warn("Empty input. Try again.")
            continue
        if "youtube.com" not in url and "youtu.be" not in url:
            warn("That doesn't look like a YouTube URL. Try again.")
            continue
        return url


def get_video_id(url: str) -> str:
    """Use yt-dlp itself to canonicalize the 11-char video ID."""
    try:
        result = subprocess.run(
            ["yt-dlp", "--print", "id", "--no-warnings", url],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        die(
            "yt-dlp failed to resolve the video ID from that URL.",
            f"yt-dlp stderr:\n{e.stderr}",
        )

    video_id = result.stdout.strip().splitlines()[-1] if result.stdout else ""
    if not YOUTUBE_ID_RE.match(video_id):
        die(
            f"yt-dlp returned an unexpected video id: {video_id!r}",
            "Double-check the URL and try again.",
        )
    return video_id


def download_video(url: str, video_id: str) -> Path:
    """Download at 720p if not already on disk; return path to the .mp4."""
    out_path = Path(f"{video_id}.mp4")
    if out_path.exists():
        ok(f"video already downloaded: {out_path}  (skipping download)")
        return out_path

    info(f"downloading {url}  →  {out_path}")
    cmd = [
        "yt-dlp",
        "-f", "bv*[height<=720]+ba/b[height<=720]",
        "--merge-output-format", "mp4",
        "-o", f"{video_id}.%(ext)s",
        url,
    ]
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError:
        die("yt-dlp failed to download the video.")

    if not out_path.exists():
        die(
            f"yt-dlp finished but {out_path} is missing.",
            "Check yt-dlp's output above — the available format may have "
            "produced a different container.",
        )
    return out_path


# ----------------------------------------------------------------------------
# SRT helpers
# ----------------------------------------------------------------------------

SRT_TIMECODE_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*"
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3})"
)


def count_cue_blocks(text: str) -> int:
    """Count blocks of the form [HH:MM:SS,mmm --> HH:MM:SS,mmm]."""
    return len(SRT_TIMECODE_RE.findall(text))
