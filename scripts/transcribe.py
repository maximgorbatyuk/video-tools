#!/usr/bin/env python3
"""
scripts/transcribe.py
=====================

Interactive command-line tool that downloads a YouTube video at 720p and
transcribes its audio into subtitles + plain text + a paragraph-grouped
reading copy, using mlx_whisper (the Apple Silicon / MLX port of OpenAI
Whisper) with the `whisper-large-v3-turbo` model.

This script is one of the functional scripts in this repo. It can be
launched from the top-level menu via `python3 start.py` or run directly
with `python3 scripts/transcribe.py`.

------------------------------------------------------------------------
Platform
------------------------------------------------------------------------
Designed for macOS on Apple Silicon (M-series). mlx_whisper requires the
MLX runtime and will not run on Intel Macs, Linux, or Windows. Tested
with Python 3.10+.

------------------------------------------------------------------------
Prerequisites (all must be on PATH)
------------------------------------------------------------------------
    yt-dlp        — YouTube downloader        →  brew install yt-dlp
    ffmpeg        — needed by yt-dlp to merge →  brew install ffmpeg
                    720p video + audio streams
    mlx_whisper   — Whisper on MLX            →  brew install pipx
                    (NOT a Homebrew formula)     pipx ensurepath
                                                 pipx install mlx-whisper

The turbo model (~1.5 GB) is downloaded automatically on first use and
cached at  ~/.cache/huggingface/hub/  for subsequent runs.

------------------------------------------------------------------------
Usage
------------------------------------------------------------------------
    python3 scripts/transcribe.py
        or
    python3 start.py   (then pick option 1)

The script is fully interactive:
    1. Runs preflight checks for the three CLI dependencies above.
    2. Prompts for a YouTube URL.
    3. Resolves it to the canonical 11-char video ID (via yt-dlp).
    4. Downloads the 720p MP4 (skipped if already on disk).
    5. Prompts for the language (ISO-639-1 code, e.g. `en`, `ru`, `de`)
       or `auto` for Whisper's language detection.
    6. Transcribes with mlx_whisper.
    7. Post-processes the SRT into a readable dialogue.txt grouped into
       paragraphs by silence gaps.

------------------------------------------------------------------------
Outputs (written to current working directory, named by video ID)
------------------------------------------------------------------------
    <ID>.mp4           — 720p video download
    <ID>.srt           — subtitles with timecodes (from mlx_whisper)
    <ID>.txt           — plain transcript          (from mlx_whisper)
    <ID>.dialogue.txt  — paragraph-grouped readable transcript with
                         [HH:MM:SS] timecodes per paragraph
    <ID>.lang.txt      — sidecar remembering which language was used
                         (so re-runs can offer "same language" option)

mlx_whisper also writes <ID>.vtt, <ID>.json, and <ID>.tsv as side effects
of `--output-format all`; they are not used downstream by this script.

------------------------------------------------------------------------
Idempotency / re-running on the same video
------------------------------------------------------------------------
    * If <ID>.mp4 already exists, the download step is skipped.
    * If <ID>.srt and <ID>.txt already exist, the script asks whether to:
          [s]kip transcription,
          [r]e-run with the same language as before, or
          [c]hange language and re-run.
      The previous language is remembered via the <ID>.lang.txt sidecar.

------------------------------------------------------------------------
Limitations
------------------------------------------------------------------------
    * No speaker diarization. Paragraphs in <ID>.dialogue.txt are split
      purely on silence gaps in the SRT (>= PARAGRAPH_GAP_SECONDS), not
      on speaker turns.
    * Hardcoded to 720p; tweak the yt-dlp format string in download_video
      (in _common.py) if you need a different resolution.
    * Hardcoded to the turbo model (see MODEL_ID below) for the best
      speed/quality trade-off on Apple Silicon.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

# Make sibling _common.py importable whether run as `python3 scripts/transcribe.py`
# or `python3 -m scripts.transcribe`.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (  # noqa: E402
    check_ffmpeg,
    check_yt_dlp,
    die,
    download_video,
    get_video_id,
    info,
    ok,
    prompt_youtube_url,
    warn,
)

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------

MODEL_ID = "mlx-community/whisper-large-v3-turbo"
HF_CACHE_DIR = Path.home() / ".cache" / "huggingface" / "hub"
MODEL_CACHE_DIR = HF_CACHE_DIR / "models--mlx-community--whisper-large-v3-turbo"

# Pause (in seconds) between SRT segments above which we start a new paragraph
# in the dialogue.txt output.
PARAGRAPH_GAP_SECONDS = 1.5


# ----------------------------------------------------------------------------
# Preflight checks specific to this script
# ----------------------------------------------------------------------------

def check_mlx_whisper() -> None:
    # The pipx-installed binary is named `mlx_whisper` (underscore).
    if shutil.which("mlx_whisper") is None:
        die(
            "`mlx_whisper` is not installed or not on PATH.",
            "Note: mlx-whisper isn't a Homebrew formula — install it via\n"
            "pipx (recommended) or pip:\n"
            "\n"
            "    brew install pipx\n"
            "    pipx ensurepath\n"
            "    pipx install mlx-whisper\n"
            "\n"
            "Then open a new terminal so PATH picks up pipx's bin dir.",
        )
    ok("mlx_whisper found")


def check_turbo_model() -> None:
    """Warn (don't fail) if the turbo model isn't cached yet."""
    if MODEL_CACHE_DIR.exists() and any(MODEL_CACHE_DIR.rglob("*.safetensors")):
        ok(f"turbo model cached at {MODEL_CACHE_DIR}")
        return

    warn(
        f"Turbo model not found in {MODEL_CACHE_DIR}.\n"
        f"    It will be downloaded automatically on first transcription\n"
        f"    (~1.5 GB, one-time)."
    )


# ----------------------------------------------------------------------------
# Language prompt + sidecar
# ----------------------------------------------------------------------------

# A non-exhaustive set of common ISO-639-1 codes Whisper accepts.
# This list is just for the prompt's sanity check — Whisper supports ~99
# languages, so we accept anything 2 chars long.
COMMON_LANGS = {
    "en", "ru", "es", "fr", "de", "it", "ja", "zh", "ko", "pt",
    "ar", "tr", "pl", "nl", "uk", "kk", "hi", "sv", "no", "fi",
    "cs", "el", "he", "th", "vi", "id", "ms", "ro", "hu", "da",
}


def prompt_language(default: Optional[str] = None) -> Optional[str]:
    """Returns an ISO code (e.g. 'en'), or None to mean auto-detect."""
    hint = f" [default: {default}]" if default else " [default: auto-detect]"
    raw = input(
        f"\nLanguage code (ISO-639-1, e.g. en, ru, de) or 'auto'{hint}: "
    ).strip().lower()

    if not raw:
        return default  # may itself be None

    if raw == "auto":
        return None

    if len(raw) != 2:
        warn(f"{raw!r} doesn't look like an ISO-639-1 code. Using auto-detect.")
        return None

    if raw not in COMMON_LANGS:
        warn(
            f"{raw!r} isn't in the common-languages list, but Whisper may "
            f"still support it. Continuing."
        )
    return raw


def transcripts_exist(video_id: str) -> bool:
    return Path(f"{video_id}.srt").exists() and Path(f"{video_id}.txt").exists()


def lang_sidecar_path(video_id: str) -> Path:
    return Path(f"{video_id}.lang.txt")


def read_previous_language(video_id: str) -> Optional[str]:
    """Returns 'auto', an ISO code, or None if no sidecar exists."""
    p = lang_sidecar_path(video_id)
    if not p.exists():
        return None
    val = p.read_text(encoding="utf-8").strip()
    return val or None


def write_language_sidecar(video_id: str, language: Optional[str]) -> None:
    lang_sidecar_path(video_id).write_text(
        (language or "auto") + "\n", encoding="utf-8"
    )


def prompt_retranscribe(video_id: str, previous_lang: Optional[str]) -> str:
    """Returns one of: 'skip', 'same', 'change'."""
    print()
    info(f"transcripts already exist for {video_id}:")
    for ext in ("srt", "txt", "dialogue.txt"):
        p = Path(f"{video_id}.{ext}")
        if p.exists():
            print(f"    • {p}")
    if previous_lang:
        info(f"previous run used language: {previous_lang}")
    else:
        info("previous run's language is unknown (no .lang.txt sidecar)")

    while True:
        choice = input(
            "\nWhat now? "
            "[s]kip transcription / "
            "[r]e-run with same language / "
            "[c]hange language and re-run: "
        ).strip().lower()
        if choice in ("s", "skip"):
            return "skip"
        if choice in ("r", "rerun", "re-run", "same"):
            return "same"
        if choice in ("c", "change"):
            return "change"
        warn("Please enter s, r, or c.")


# ----------------------------------------------------------------------------
# mlx_whisper invocation
# ----------------------------------------------------------------------------

def run_mlx_whisper(video_path: Path, video_id: str, language: Optional[str]) -> None:
    info(
        f"transcribing {video_path}  "
        f"(language: {language or 'auto-detect'})"
    )
    cmd = [
        "mlx_whisper",
        str(video_path),
        "--model", MODEL_ID,
        "--output-dir", ".",
        "--output-format", "all",  # produces srt, txt, vtt, json, tsv
        "--word-timestamps", "False",
        "--verbose", "True",
    ]
    if language:
        cmd += ["--language", language]

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError:
        die("mlx_whisper failed.")

    # Sanity check that the expected files exist (mlx_whisper writes them
    # using the input file's stem; since we use <ID>.mp4 that is <ID>).
    for ext in ("srt", "txt"):
        if not Path(f"{video_id}.{ext}").exists():
            die(
                f"Expected {video_id}.{ext} but it wasn't produced.",
                "Check mlx_whisper's output above for clues.",
            )
    ok(f"wrote {video_id}.srt and {video_id}.txt")


# ----------------------------------------------------------------------------
# SRT → dialogue.txt post-processing
# ----------------------------------------------------------------------------

SRT_TIMECODE_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*"
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3})"
)


def srt_time_to_seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def seconds_to_hms(sec: float) -> str:
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def parse_srt(path: Path) -> list[tuple[float, float, str]]:
    """Return a list of (start_seconds, end_seconds, text) tuples."""
    segments: list[tuple[float, float, str]] = []
    blocks = path.read_text(encoding="utf-8").strip().split("\n\n")
    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        match = SRT_TIMECODE_RE.search(lines[1])
        if not match:
            continue
        h1, m1, s1, ms1, h2, m2, s2, ms2 = match.groups()
        start = srt_time_to_seconds(h1, m1, s1, ms1)
        end = srt_time_to_seconds(h2, m2, s2, ms2)
        text = " ".join(line.strip() for line in lines[2:]).strip()
        if text:
            segments.append((start, end, text))
    return segments


def write_dialogue_txt(video_id: str) -> None:
    srt_path = Path(f"{video_id}.srt")
    out_path = Path(f"{video_id}.dialogue.txt")
    if not srt_path.exists():
        warn(f"{srt_path} missing — cannot build dialogue.txt")
        return

    segments = parse_srt(srt_path)
    if not segments:
        warn(f"{srt_path} contained no usable segments")
        return

    paragraphs: list[tuple[float, list[str]]] = []
    cur_start = segments[0][0]
    cur_chunks: list[str] = []
    prev_end = segments[0][0]

    for start, end, text in segments:
        gap = start - prev_end
        if gap >= PARAGRAPH_GAP_SECONDS and cur_chunks:
            paragraphs.append((cur_start, cur_chunks))
            cur_start = start
            cur_chunks = []
        cur_chunks.append(text)
        prev_end = end
    if cur_chunks:
        paragraphs.append((cur_start, cur_chunks))

    with out_path.open("w", encoding="utf-8") as f:
        f.write(f"# Transcript for {video_id}\n")
        f.write(
            "# Paragraphs are split on silence gaps "
            f"(>= {PARAGRAPH_GAP_SECONDS}s). No speaker diarization.\n\n"
        )
        for start, chunks in paragraphs:
            f.write(f"[{seconds_to_hms(start)}]\n")
            f.write(" ".join(chunks).strip() + "\n\n")

    ok(f"wrote {out_path}  ({len(paragraphs)} paragraphs)")


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def main() -> int:
    print("=== YouTube → mlx_whisper transcription ===\n")

    info("Preflight checks…")
    check_yt_dlp()
    check_ffmpeg()
    check_mlx_whisper()
    check_turbo_model()

    url = prompt_youtube_url()
    video_id = get_video_id(url)
    info(f"video id: {video_id}")

    video_path = download_video(url, video_id)

    if transcripts_exist(video_id):
        previous_lang = read_previous_language(video_id)
        choice = prompt_retranscribe(video_id, previous_lang)
        if choice == "skip":
            ok("Skipping transcription. Done.")
            return 0
        if choice == "same":
            if previous_lang and previous_lang != "auto":
                language = previous_lang
                info(f"reusing previous language: {language}")
            elif previous_lang == "auto":
                language = None
                info("reusing previous setting: auto-detect")
            else:
                warn(
                    "No record of the previous language. "
                    "Please enter the language to use now."
                )
                language = prompt_language()
        else:  # "change"
            language = prompt_language()
    else:
        language = prompt_language()

    run_mlx_whisper(video_path, video_id, language)
    write_language_sidecar(video_id, language)
    write_dialogue_txt(video_id)

    print()
    ok("Transcription done.")
    print(f"  • {video_id}.mp4            — 720p download")
    print(f"  • {video_id}.srt            — subtitles with timecodes")
    print(f"  • {video_id}.txt            — plain transcript")
    print(f"  • {video_id}.dialogue.txt   — paragraph-grouped reading copy")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        warn("Interrupted.")
        sys.exit(130)
