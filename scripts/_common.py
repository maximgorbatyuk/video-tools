"""
Shared helpers for the functional scripts in this folder.

This module is NOT a runnable script and is not registered in start.py.
It exposes:

    Log helpers:    info / ok / warn / err / die / debug
    Verbose mode:   set_verbose, is_verbose, time_block
    Preflight:      check_yt_dlp, check_ffmpeg, check_claude
    YouTube:        YOUTUBE_ID_RE, prompt_youtube_url, get_video_id,
                    get_video_title, get_video_metadata,
                    available_video_heights, available_subtitle_langs,
                    download_video, download_subtitles_for_lang
    Artifacts:      find_existing_artifacts
    Results folder: video_title_to_slug, find_or_create_results_dir
    SRT:            SRT_TIMECODE_RE, count_cue_blocks

Both scripts/transcribe.py and scripts/translate.py import from here.
Keep this module side-effect-free at import time.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Iterator, Optional


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
# Verbose / debug logging
# ----------------------------------------------------------------------------
#
# Off by default. Scripts call set_verbose(True) from their argparse handler
# when `-v` / `--verbose` is passed. debug() is a no-op when off, so it's
# safe to sprinkle liberally at hot points (claude calls, yt-dlp shells,
# validation passes) without polluting the normal output.

_VERBOSE = False


def set_verbose(enabled: bool) -> None:
    """Enable or disable debug-level logging globally for this process."""
    global _VERBOSE
    _VERBOSE = bool(enabled)


def is_verbose() -> bool:
    return _VERBOSE


def debug(msg: str) -> None:
    """Dim-colored diagnostic line. Silent unless set_verbose(True) was called."""
    if not _VERBOSE:
        return
    print(f"\033[90m[d]\033[0m {msg}", file=sys.stderr)


@contextmanager
def time_block(label: str) -> Iterator[None]:
    """
    Context manager that emits a `start` + `done in Xs` pair at debug level.

    No-op (still yields) when verbose is off, so wrapping a slow call with
    this is free in the common case.
    """
    if not _VERBOSE:
        yield
        return
    debug(f"{label}: start")
    t0 = time.monotonic()
    try:
        yield
    finally:
        debug(f"{label}: done in {time.monotonic() - t0:.2f}s")


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


def check_claude() -> None:
    """Verify the `claude` CLI is on PATH."""
    if shutil.which("claude") is None:
        die(
            "`claude` CLI is not installed or not on PATH.",
            "Install Claude Code (it provides the `claude` binary):\n"
            "\n"
            "    npm install -g @anthropic-ai/claude-code\n"
            "    claude login\n"
            "\n"
            "Then open a new terminal so PATH picks it up.",
        )
    ok("claude CLI found")


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


def get_video_title(url: str) -> str:
    """Return the YouTube video's title (best-effort; falls back to '')."""
    try:
        result = subprocess.run(
            ["yt-dlp", "--print", "title", "--no-warnings", url],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError:
        return ""
    return (result.stdout or "").strip().splitlines()[-1] if result.stdout else ""


def get_video_metadata(url: str) -> dict:
    """
    Return yt-dlp's full JSON metadata for the URL.

    One round-trip (~1–2s). Callers use the result to enumerate available
    video heights and subtitle languages without further yt-dlp invocations.
    """
    debug(f"yt-dlp -J: fetching metadata for {url}")
    try:
        with time_block("yt-dlp -J"):
            result = subprocess.run(
                ["yt-dlp", "-J", "--no-warnings", url],
                check=True,
                capture_output=True,
                text=True,
            )
    except subprocess.CalledProcessError as e:
        die(
            "yt-dlp failed to fetch video metadata.",
            f"yt-dlp stderr:\n{e.stderr}",
        )
    try:
        metadata = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        die(f"yt-dlp returned invalid JSON metadata: {e}")
        return {}  # unreachable; for the type-checker
    debug(
        f"yt-dlp returned {len(result.stdout)} chars of JSON; "
        f"{len(metadata.get('formats') or [])} formats, "
        f"{len(metadata.get('subtitles') or {})} manual sub langs, "
        f"{len(metadata.get('automatic_captions') or {})} auto sub langs"
    )
    return metadata


def available_video_heights(metadata: dict) -> list[int]:
    """
    Pure parser: extract the sorted-ascending unique list of video heights
    available in yt-dlp metadata. Audio-only formats and entries without a
    height are dropped.
    """
    heights: set[int] = set()
    for fmt in metadata.get("formats", []) or []:
        if not isinstance(fmt, dict):
            continue
        if fmt.get("vcodec") in (None, "none"):
            continue
        h = fmt.get("height")
        if isinstance(h, int) and h > 0:
            heights.add(h)
    return sorted(heights)


def available_subtitle_langs(metadata: dict) -> list[str]:
    """
    Pure parser: return the sorted unique list of subtitle language codes
    advertised by yt-dlp — merging manual subs and automatic captions, since
    download_subtitles_for_lang handles both.
    """
    langs: set[str] = set()
    for key in ("subtitles", "automatic_captions"):
        section = metadata.get(key) or {}
        if not isinstance(section, dict):
            continue
        for code in section.keys():
            if isinstance(code, str) and code.strip():
                langs.add(code.strip())
    return sorted(langs)


def download_video(url: str, video_id: str, max_height: int = 720) -> Path:
    """
    Download the video at <= `max_height`p if not already on disk; return
    the path to the .mp4. Default ceiling is 720p (transcribe.py's
    expectation); translate.py passes the user-chosen height.
    """
    out_path = Path(f"{video_id}.mp4")
    if out_path.exists():
        ok(f"video already downloaded: {out_path}  (skipping download)")
        return out_path

    info(f"downloading {url} at <= {max_height}p  →  {out_path}")
    cmd = [
        "yt-dlp",
        "-f", f"bv*[height<={max_height}]+ba/b[height<={max_height}]",
        "--merge-output-format", "mp4",
        "-o", f"{video_id}.%(ext)s",
        url,
    ]
    debug(f"yt-dlp argv: {cmd}")
    try:
        with time_block("yt-dlp download"):
            subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError:
        die("yt-dlp failed to download the video.")

    if not out_path.exists():
        die(
            f"yt-dlp finished but {out_path} is missing.",
            "Check yt-dlp's output above — the available format may have "
            "produced a different container.",
        )
    try:
        size_mb = out_path.stat().st_size / 1024 / 1024
        debug(f"{out_path.name}: {size_mb:.1f} MB on disk")
    except OSError:
        pass
    return out_path


def download_subtitles_for_lang(
    url: str, video_id: str, source_lang: str
) -> Optional[Path]:
    """
    Fetch subtitles in the given language via yt-dlp.

    Returns the path to <ID>.<source_lang>.srt on success, else None.
    yt-dlp prefers manual (creator-uploaded) subs and falls back to
    auto-generated; both land at the same filename.

    Best-effort: a missing-subs failure is reported via the return value,
    not by raising.
    """
    out_path = Path(f"{video_id}.{source_lang}.srt")
    if out_path.exists():
        ok(f"{source_lang} subtitles already present: {out_path}  (skipping fetch)")
        return out_path

    info(f"fetching {source_lang} subtitles for {video_id}…")
    cmd = [
        "yt-dlp",
        "--skip-download",
        "--write-subs",
        "--write-auto-subs",
        # <code>.* covers regional variants (en-US, zh-Hans, …); <code>
        # covers the bare code itself.
        "--sub-langs", f"{source_lang}.*,{source_lang}",
        "--sub-format", "srt/best",
        "--convert-subs", "srt",
        "-o", f"{video_id}.%(ext)s",
        url,
    ]
    debug(f"yt-dlp argv: {cmd}")
    try:
        with time_block(f"yt-dlp subs ({source_lang})"):
            result = subprocess.run(
                cmd, check=False, capture_output=True, text=True,
            )
        debug(f"yt-dlp subs exit: {result.returncode}")
    except Exception as e:  # pragma: no cover — extremely unlikely
        warn(f"yt-dlp subtitles fetch raised an exception: {e}")
        return None

    # yt-dlp may write `<ID>.<lang>.srt`, `<ID>.<lang>-orig.srt`,
    # `<ID>.<lang>-US.srt`, etc. — pick the first match.
    candidates = sorted(Path(".").glob(f"{video_id}.{source_lang}*.srt"))
    if not candidates:
        debug(f"no {source_lang} subtitle file produced")
        return None

    chosen = candidates[0]
    if chosen != out_path:
        chosen.rename(out_path)
    try:
        size_kb = out_path.stat().st_size / 1024
        debug(f"{out_path.name}: {size_kb:.1f} KB")
    except OSError:
        pass
    ok(f"saved {source_lang} subtitles: {out_path}")
    return out_path


# ----------------------------------------------------------------------------
# Results folder (per-video output directory)
# ----------------------------------------------------------------------------

RESULTS_DIR_NAME = "results"

# Marker files we look for when deciding whether an existing results folder
# already belongs to a given video ID. Any one of these is enough.
_RESULTS_DIR_MARKERS = (
    "{vid}.mp4",
    "{vid}.lang.txt",
    "{vid}.srt",
    "{vid}.txt",
    "{vid}.summary.md",
    "{vid}.translate-source-lang.txt",
    "{vid}.translate-target-lang.txt",
    "{vid}.video-quality.txt",
)


def find_existing_artifacts(video_id: str) -> tuple[Optional[Path], list[Path]]:
    """
    Inspect the CWD for a previous translate.py run's downloads.

    Returns (mp4 path or None, list of per-language SRT paths). The SRT
    list matches `<ID>.<anything>.srt` — that covers `<ID>.en.srt`,
    `<ID>.zh-Hans.srt`, etc. without picking up `<ID>.srt` (the whisper
    output of transcribe.py).
    """
    mp4 = Path(f"{video_id}.mp4")
    mp4_path: Optional[Path] = mp4 if mp4.exists() else None
    srts = [p for p in sorted(Path(".").glob(f"{video_id}.*.srt"))]
    return mp4_path, srts


def video_title_to_slug(title: str, max_len: int = 60) -> str:
    """
    Convert a YouTube video title into a filesystem-safe snake_case slug.

    Keeps Unicode letters and digits (so a Cyrillic or CJK title stays
    legible), collapses everything else to single underscores. Falls back
    to 'untitled' if the result is empty.
    """
    if not title:
        return "untitled"
    s = re.sub(r"\W+", "_", title, flags=re.UNICODE).lower().strip("_")
    if not s:
        return "untitled"
    if len(s) > max_len:
        s = s[:max_len].rstrip("_") or "untitled"
    return s


def find_or_create_results_dir(
    video_id: str,
    title: str,
    base: Optional[Path] = None,
) -> Path:
    """
    Locate or create the per-video results folder under `<base>/`.

    Lookup order:
      1. If any sub-folder of `<base>/` already contains a marker file
         starting with `<video_id>.`, re-use that folder (idempotent across
         days).
      2. Otherwise create `<base>/<YYYY-MM-DD>_<slug>/` using today's date
         and the snake-cased video title.

    The returned path is created on disk and is suitable to `os.chdir()` into.
    """
    base = base or Path(RESULTS_DIR_NAME)
    base.mkdir(parents=True, exist_ok=True)

    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        for marker_tmpl in _RESULTS_DIR_MARKERS:
            if (entry / marker_tmpl.format(vid=video_id)).exists():
                return entry

    slug = video_title_to_slug(title)
    today = date.today().isoformat()
    new_dir = base / f"{today}_{slug}"
    new_dir.mkdir(parents=True, exist_ok=True)
    return new_dir


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
