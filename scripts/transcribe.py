#!/usr/bin/env python3
"""
scripts/transcribe.py
=====================

Interactive command-line tool that downloads a YouTube video at 720p,
fetches its YouTube-hosted subtitles (best-effort), transcribes the
audio locally with `mlx_whisper` + the `whisper-large-v3-turbo` model,
and optionally asks `claude` to summarize the resulting transcript.

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
    claude        — Claude Code CLI           →  npm install -g
                    (only used if the user        @anthropic-ai/claude-code
                     opts into the summary       claude login
                     step at the end)

The turbo model (~1.5 GB) is downloaded automatically on first use and
cached at  ~/.cache/huggingface/hub/  for subsequent runs.

------------------------------------------------------------------------
Usage
------------------------------------------------------------------------
    python3 scripts/transcribe.py [-v|--verbose]
        or
    python3 start.py [-v|--verbose]   (then pick option 1)

The `-v` / `--verbose` flag prints dim `[d]` diagnostic lines to stderr —
yt-dlp argv, mlx_whisper duration, paragraph-split stats, claude prompt
char counts, and claude call durations. Use it to diagnose hangs or
unexpected failures.

The script is fully interactive. The algorithm is:

    1. Preflight checks for yt-dlp, ffmpeg, and mlx_whisper. (`claude` is
       only checked later, on demand, since the summary step is opt-in.)
    2. Prompt for a YouTube URL.
    3. Resolve it to the canonical 11-char video ID and fetch the
       video's title (both via yt-dlp).
    4. Decide where outputs go:
         - If a folder under `<CWD>/results/` already contains a file
           starting with `<ID>.`, re-use it.
         - Otherwise create `<CWD>/results/<YYYY-MM-DD>_<slug>/` and
           `chdir` into it.
       From here on every output filename is relative to that folder.
    5. Download the 720p MP4 (skipped if already on disk).
    6. Prompt for the language (ISO-639-1, e.g. `en`, `ru`, `de`) or
       `auto` for Whisper's language detection.
    7. Best-effort fetch of the YouTube-hosted subtitles in the chosen
       language (or English when auto-detect is selected). A miss is a
       warning, not an error.
    8. Run `mlx_whisper` to produce SRT/TXT/VTT/JSON/TSV and a
       paragraph-grouped dialogue.txt.
    9. Ask whether to summarize the transcript via `claude`. If yes:
         - Preflight `claude`.
         - Prompt for speaker name and (optional) context — sidecar-
           backed so re-runs offer "re-use last".
         - Substitute into the template at `prompts/summary_prompt.md`,
           feed the result to `claude -p`, and write `<ID>.summary.md`.

------------------------------------------------------------------------
Outputs (all written into the per-video results folder)
------------------------------------------------------------------------
    results/<YYYY-MM-DD>_<slug>/
        <ID>.mp4                — 720p video download
        <ID>.<lang>.srt         — YouTube-hosted subtitles (if available)
        <ID>.srt                — whisper subtitles with timecodes
        <ID>.txt                — whisper plain transcript
        <ID>.dialogue.txt       — paragraph-grouped readable transcript
                                  with [HH:MM:SS] timecodes per paragraph
        <ID>.lang.txt           — sidecar: language used for the last
                                  whisper run
        <ID>.summary.md         — (optional) claude-generated long-form
                                  summary of the transcript
        <ID>.summary-speaker.txt   — sidecar: speaker name for the summary
        <ID>.summary-context.txt   — sidecar: speaker context for the
                                              summary

mlx_whisper also writes <ID>.vtt, <ID>.json, and <ID>.tsv as side effects
of `--output-format all`; they are not used downstream by this script.

------------------------------------------------------------------------
Idempotency / re-running on the same video
------------------------------------------------------------------------
    * The results folder is re-used across days — the lookup is by
      video ID, not by date. The date only appears in the folder name
      the first time outputs are produced.
    * If <ID>.mp4 already exists, the download step is skipped.
    * If <ID>.<lang>.srt already exists, the subtitle fetch is skipped.
    * If <ID>.srt and <ID>.txt already exist, the script asks whether to:
          [s]kip transcription,
          [r]e-run with the same language as before, or
          [c]hange language and re-run.
      The previous language is remembered via <ID>.lang.txt.
    * If <ID>.summary.md already exists at summary time the script
      offers [s]kip / [r]e-run / [c]hange.

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
    * The summary step uses <ID>.dialogue.txt as input — speaker turns
      have to be inferred by claude from context, not from labels.
"""

from __future__ import annotations

import argparse
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
    check_claude,
    check_ffmpeg,
    check_yt_dlp,
    debug,
    die,
    download_subtitles_for_lang,
    download_video,
    find_or_create_results_dir,
    get_video_id,
    get_video_title,
    info,
    is_verbose,
    ok,
    prompt_youtube_url,
    set_verbose,
    time_block,
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

# Location of the summary prompt template — resolved relative to the script,
# so it works regardless of which folder we chdir into later.
REPO_ROOT = Path(__file__).resolve().parent.parent
SUMMARY_PROMPT_TEMPLATE = REPO_ROOT / "prompts" / "summary_prompt.md"


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
    debug(f"mlx_whisper argv: {cmd}")

    try:
        with time_block("mlx_whisper"):
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
    debug(f"parsed {len(segments)} SRT segments from {srt_path.name}")
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
# Summary step (claude)
# ----------------------------------------------------------------------------

SUMMARY_SPEAKER_SIDECAR_TMPL = "{vid}.summary-speaker.txt"
SUMMARY_CONTEXT_SIDECAR_TMPL = "{vid}.summary-context.txt"


def summary_output_path(video_id: str) -> Path:
    return Path(f"{video_id}.summary.md")


def _read_text_sidecar(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    val = path.read_text(encoding="utf-8").strip()
    return val or None


def _write_text_sidecar(path: Path, value: str) -> None:
    path.write_text(value.strip() + "\n", encoding="utf-8")


def prompt_yes_no(question: str, default_no: bool = True) -> bool:
    suffix = " [y/N]: " if default_no else " [Y/n]: "
    raw = input(question + suffix).strip().lower()
    if not raw:
        return not default_no
    return raw in ("y", "yes")


def prompt_resummarize(video_id: str) -> str:
    """Returns 'skip', 'same', or 'change'."""
    print()
    info(f"summary already exists: {summary_output_path(video_id)}")
    while True:
        choice = input(
            "What now? "
            "[s]kip / "
            "[r]e-run with same speaker + context / "
            "[c]hange and re-run: "
        ).strip().lower()
        if choice in ("s", "skip"):
            return "skip"
        if choice in ("r", "rerun", "re-run", "same"):
            return "same"
        if choice in ("c", "change"):
            return "change"
        warn("Please enter s, r, or c.")


def prompt_summary_inputs(
    video_id: str, force_reprompt: bool = False
) -> tuple[str, str]:
    """
    Returns (speaker_name, context). Either may be empty string (context
    can be empty by design; speaker cannot be empty — we re-ask).

    Sidecar-backed: if previous values are on disk, offers re-use.
    """
    speaker_p = Path(SUMMARY_SPEAKER_SIDECAR_TMPL.format(vid=video_id))
    context_p = Path(SUMMARY_CONTEXT_SIDECAR_TMPL.format(vid=video_id))
    prev_speaker = _read_text_sidecar(speaker_p)
    prev_context = _read_text_sidecar(context_p)

    if not force_reprompt and prev_speaker is not None:
        info(f"previous summary speaker: {prev_speaker}")
        if prev_context:
            info(f"previous summary context: {prev_context}")
        if prompt_yes_no("Re-use these?", default_no=False):
            return prev_speaker, prev_context or ""

    while True:
        default_hint = f" [default: {prev_speaker}]" if prev_speaker else ""
        speaker = input(f"\nSpeaker name{default_hint}: ").strip()
        if not speaker and prev_speaker:
            speaker = prev_speaker
        if speaker:
            break
        warn("Speaker name cannot be empty.")

    ctx_default_hint = ""
    if prev_context:
        ctx_default_hint = f" [default: {prev_context}]"
    context = input(
        f"Context — one or two sentences about the speaker and what the "
        f"interview covers (optional, press Enter to skip){ctx_default_hint}: "
    ).strip()
    if not context and prev_context:
        context = prev_context

    return speaker, context


def build_summary_prompt(template: str, speaker: str, context: str, transcript: str) -> str:
    """
    Substitute the three placeholders into the prompt template.

    Uses plain .replace() so braces in user-supplied context don't get
    interpreted as format-string fields.
    """
    return (
        template
        .replace("{{speaker_name}}", speaker)
        .replace("{{context}}", context)
        .replace("{{transcript}}", transcript)
    )


def run_claude(prompt: str) -> str:
    """Call `claude -p` with the prompt piped via stdin and return stdout.

    Piping rather than passing the prompt as argv keeps us well under the
    ~1 MB ARG_MAX limit on macOS — long transcripts can easily exceed that.
    """
    info("invoking claude CLI (this may take a minute for longer transcripts)…")
    debug(f"claude prompt: {len(prompt)} chars (~{len(prompt.encode('utf-8')) / 1024:.1f} KB)")
    try:
        with time_block("claude -p"):
            result = subprocess.run(
                ["claude", "-p"],
                input=prompt,
                check=True,
                capture_output=True,
                text=True,
            )
    except subprocess.CalledProcessError as e:
        die(
            "`claude` CLI returned a non-zero exit code.",
            f"stderr:\n{e.stderr}",
        )
    debug(f"claude response: {len(result.stdout)} chars")
    return result.stdout


def maybe_summarize(video_id: str) -> None:
    print()
    if not prompt_yes_no("Summarize this video with claude?"):
        info("Skipping summary.")
        return

    if not SUMMARY_PROMPT_TEMPLATE.exists():
        die(
            f"Summary prompt template not found at {SUMMARY_PROMPT_TEMPLATE}.",
            "Make sure you have not moved the `prompts/` folder out of the repo.",
        )

    transcript_path = Path(f"{video_id}.dialogue.txt")
    if not transcript_path.exists():
        die(
            f"{transcript_path} is missing — cannot build a summary without "
            f"a transcript.",
        )

    check_claude()

    force_reprompt = False
    out_path = summary_output_path(video_id)
    if out_path.exists():
        choice = prompt_resummarize(video_id)
        if choice == "skip":
            return
        if choice == "change":
            force_reprompt = True

    speaker, context = prompt_summary_inputs(video_id, force_reprompt=force_reprompt)
    template = SUMMARY_PROMPT_TEMPLATE.read_text(encoding="utf-8")
    transcript = transcript_path.read_text(encoding="utf-8")

    prompt = build_summary_prompt(template, speaker, context, transcript)
    output = run_claude(prompt).strip()
    if not output:
        die("claude returned an empty response.")

    out_path.write_text(output + "\n", encoding="utf-8")
    _write_text_sidecar(
        Path(SUMMARY_SPEAKER_SIDECAR_TMPL.format(vid=video_id)), speaker
    )
    _write_text_sidecar(
        Path(SUMMARY_CONTEXT_SIDECAR_TMPL.format(vid=video_id)), context
    )
    ok(f"wrote {out_path}")


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="transcribe.py",
        description=(
            "Download a YouTube video at 720p, transcribe its audio locally "
            "with mlx_whisper, and optionally summarize with claude."
        ),
    )
    p.add_argument(
        "-v", "--verbose",
        action="store_true",
        help=(
            "Print dim [d] diagnostic lines to stderr (yt-dlp / mlx_whisper "
            "argv + durations, claude prompt sizes, SRT segment counts)."
        ),
    )
    return p.parse_args(argv)


def main() -> int:
    args = _parse_args()
    set_verbose(args.verbose)

    print("=== YouTube → mlx_whisper transcription ===\n")
    if is_verbose():
        debug("verbose mode enabled")

    info("Preflight checks…")
    check_yt_dlp()
    check_ffmpeg()
    check_mlx_whisper()
    check_turbo_model()

    url = prompt_youtube_url()
    video_id = get_video_id(url)
    info(f"video id: {video_id}")

    title = get_video_title(url)
    if title:
        info(f"video title: {title}")

    results_dir = find_or_create_results_dir(video_id, title)
    info(f"results folder: {results_dir.resolve()}")
    os.chdir(results_dir)
    debug(f"chdir → {results_dir.resolve()}")

    video_path = download_video(url, video_id)

    if transcripts_exist(video_id):
        previous_lang = read_previous_language(video_id)
        choice = prompt_retranscribe(video_id, previous_lang)
        if choice == "skip":
            ok("Skipping transcription.")
            language = previous_lang if previous_lang and previous_lang != "auto" else None
            transcription_ran = False
        else:
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
            transcription_ran = True
    else:
        language = prompt_language()
        transcription_ran = True

    # Best-effort YouTube subtitle fetch (independent of transcription).
    # Use the chosen language when one is set; fall back to English on
    # auto-detect so the user still gets *something* if YouTube has it.
    sub_lang = language or "en"
    download_subtitles_for_lang(url, video_id, sub_lang)

    if transcription_ran:
        run_mlx_whisper(video_path, video_id, language)
        write_language_sidecar(video_id, language)
        write_dialogue_txt(video_id)

    print()
    ok("Transcription done.")
    print(f"  • {video_id}.mp4            — 720p download")
    yt_srt = Path(f"{video_id}.{sub_lang}.srt")
    if yt_srt.exists():
        print(f"  • {yt_srt.name}        — YouTube subtitles ({sub_lang})")
    print(f"  • {video_id}.srt            — whisper subtitles with timecodes")
    print(f"  • {video_id}.txt            — whisper plain transcript")
    print(f"  • {video_id}.dialogue.txt   — paragraph-grouped reading copy")

    maybe_summarize(video_id)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        warn("Interrupted.")
        sys.exit(130)
