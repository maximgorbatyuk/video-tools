#!/usr/bin/env python3
"""
scripts/translate.py
====================

Interactive command-line tool that downloads a YouTube video and every
subtitle language YouTube advertises for it, then optionally translates
one of those subtitle files into English, Russian, or Kazakh via the
`claude` CLI — preserving every timecode, validating the result with
Python, and writing the output as a plain SubRip (.srt) file ready for
playback.

This script is one of the functional scripts in this repo. It can be
launched from the top-level menu via `python3 start.py` or run directly
with `python3 scripts/translate.py`.

------------------------------------------------------------------------
Platform
------------------------------------------------------------------------
Designed for macOS on Apple Silicon (M-series). The script itself is
platform-neutral Python; the binaries it shells out to may not be.

------------------------------------------------------------------------
Prerequisites (all must be on PATH)
------------------------------------------------------------------------
    yt-dlp        — YouTube downloader (video + subs)
                                                  →  brew install yt-dlp
    ffmpeg        — needed by yt-dlp to merge     →  brew install ffmpeg
                    video + audio streams
    claude        — Claude Code CLI               →  npm install -g
                    (one of the two LLM CLIs;             @anthropic-ai/claude-code
                     used for the translation         claude login
                     step)
    opencode      — opencode CLI                  →  brew install opencode
                    (the other LLM CLI option;        opencode auth login
                     gives access to GLM and
                     other non-Claude models)

    At translation time the user picks which of `claude` / `opencode` to
    use and which model; only the chosen one needs to be installed.

------------------------------------------------------------------------
Usage
------------------------------------------------------------------------
    python3 scripts/translate.py [-v|--verbose]
        or
    python3 start.py [-v|--verbose]   (then pick option 2)

The `-v` / `--verbose` flag prints dim `[d]` diagnostic lines to stderr —
yt-dlp argv, claude prompt char counts, claude call durations, validator
cue counts, the first few timecode-validation issues, and output file
sizes. Use it to diagnose hangs or unexpected failures.

The script is fully interactive. Algorithm:

    1. Preflight checks for yt-dlp, ffmpeg. (The LLM CLI — claude or
       opencode — is checked later, once the user picks which one to
       translate with.)
    2. Prompt for a YouTube URL.
    3. Resolve it to the canonical 11-char video ID and fetch the
       video's title (both via yt-dlp).
    4. Decide where outputs go:
         - If a folder under `<CWD>/results/` already contains a file
           starting with `<ID>.`, re-use it.
         - Otherwise create `<CWD>/results/<YYYY-MM-DD>_<slug>/` and
           `chdir` into it.
    5. Idempotency gate. If `<ID>.mp4` is already on disk, list the
       existing files and ask:
           [p]roceed to translation using existing files
           [r]e-download everything from scratch
       'redownload' wipes the existing mp4 + per-language .srt files;
       sidecars (.translate-*-lang.txt, .video-quality.txt) and any
       translation .md files are preserved.
    6. Otherwise (or on `[r]`):
         - Fetch yt-dlp metadata once (`yt-dlp -J`).
         - Show the available video heights (e.g. 360 / 480 / 720 /
           1080) and let the user pick. Default = highest <= 720p, or
           the highest if all formats exceed 720p. Sidecar
           `<ID>.video-quality.txt` remembers the choice.
         - Download the video at the chosen quality.
         - Enumerate every advertised subtitle language (manual +
           automatic), normalize to base codes (`en-US` → `en`), and
           fetch each one as `<ID>.<lang>.srt`. Progress is reported
           per language.
    7. Print a summary of what landed on disk.
    8. Ask whether to translate. If no, exit cleanly.
    9. Otherwise:
         - List the downloaded `.srt` files and let the user pick which
           one is the source.
         - Ask for the target language: [e]nglish / [r]ussian /
           [k]azakh. Refuse source == target.
         - Parse the source SRT into a list of cues. Send claude only
           a numbered tab-delimited list — `<n>\t<source text>` — one
           cue per line, with timecodes stripped. Timecodes never
           leave Python, so they can't be corrupted.
         - If a `<ID>.translated.<tgt>.broken.srt` cache from a prior
           failed run exists, seed its already-translated cues and only
           translate the ones still missing. If the cache already covers
           every cue, skip the LLM entirely and jump to reassembly.
         - When there's work to do, ask which LLM CLI (`claude` or
           `opencode`) and which model to use — remembered in sidecars,
           and the chosen tool is preflight-checked at this point.
         - Split the remaining cue list into chunks of `CHUNK_SIZE` and
           dispatch up to `MAX_WORKERS` chunks in parallel via
           `concurrent.futures.ThreadPoolExecutor`. Each worker runs its
           own LLM subprocess (`claude -p` or `opencode run`, prompt piped
           via stdin). A background heartbeat thread prints elapsed time +
           chunks-done every `HEARTBEAT_INTERVAL_S` seconds so a slow
           chunk never looks hung.
         - Per chunk: strictly filter the parsed response to cue numbers
           that were actually in that chunk's input. Claude sometimes
           hallucinates extra numbers from neighboring ranges (it
           reaches for "missing" line count by inventing cue numbers
           further along), and unfiltered they would overwrite real
           translations from adjacent chunks at the global merge.
         - Merge each chunk's `{cue_number: translation}` into one
           global dict. Validate cue-count parity.
         - If any cues are missing (typically because claude merged
           sentence-split cues into one translation), do ONE retry
           call with just the missing cue IDs. Threshold:
           `MAX_RETRY_MISSING` — above that, skip retry.
         - On final mismatch: write every cue translated so far to
           `<ID>.translated.<tgt>.broken.srt` as a sorted, resumable
           `<n>\t<text>` cache, then die. A subsequent run reads that
           cache and only translates the cues still missing.
         - Reassemble the final SRT in Python, walking the source cue
           list and substituting each cue's translated text under the
           source's original cue number + timecode line. Write to
           `<ID>.translated.<tgt>.srt` and delete the now-superseded
           broken cache.

------------------------------------------------------------------------
Outputs (all written into the per-video results folder)
------------------------------------------------------------------------
    results/<YYYY-MM-DD>_<slug>/
        <ID>.mp4
            Video download at the chosen height.

        <ID>.<lang>.srt
            Per-language subtitles fetched from YouTube. One file per
            advertised language. Manual subs preferred, auto-generated
            fall back to the same filename.

        <ID>.translated.<tgt>.srt
            The translated subtitles as a valid SubRip (.srt) file in
            the target language. `<tgt>` is the lowercased 2-letter
            code (e.g. `ru`, `en`, `kk`). The file is ready to load
            into any video player that accepts SRT.

        <ID>.translated.<tgt>.broken.srt
            Written only when Claude couldn't cover every cue after the
            retry pass. It is a sorted `<cue_num>\t<translation>` cache
            of every cue translated so far — both a salvage artifact you
            can inspect by hand and a resume point: re-running translate.py
            on the same video seeds these cues and only translates the
            missing ones, then deletes this file once the full
            `<ID>.translated.<tgt>.srt` is written.

        <ID>.video-quality.txt
            Sidecar remembering the last chosen download height.

        <ID>.translate-source-lang.txt
            Sidecar remembering the source language used last time.

        <ID>.translate-target-lang.txt
            Sidecar remembering the target language used last time.

        <ID>.translate-tool.txt
            Sidecar remembering the LLM CLI used last time
            (`claude` or `opencode`).

        <ID>.translate-model.txt
            Sidecar remembering the model used last time (a claude alias
            like `sonnet`, or an opencode `provider/model` string;
            empty = the tool's own default).

------------------------------------------------------------------------
Idempotency
------------------------------------------------------------------------
    * The results folder is re-used across days — the lookup is by
      video ID, not by date.
    * `<ID>.mp4` already on disk → `[p]roceed / [r]e-download` gate
      before any network I/O.
    * Individual subtitle downloads skip if `<ID>.<lang>.srt` already
      exists.
    * `<ID>.translated.<tgt>.srt` already exists → asks
      `[s]kip / [r]e-run with same / [c]hange target language`.
    * `<ID>.translated.<tgt>.broken.srt` from a prior failed run is
      treated as a partial-translation cache: its cues are seeded and
      only the missing ones are re-translated. If it already covers
      every cue, no claude calls are made at all.
    * Sidecars (`.translate-*-lang.txt`, `.video-quality.txt`) are
      offered as defaults on subsequent runs.

------------------------------------------------------------------------
Limitations
------------------------------------------------------------------------
    * Validation is cue-count parity only; the script does NOT detect
      semantic errors in the translation itself.
    * Translation is parallelized across cue chunks (see CHUNK_SIZE
      and MAX_WORKERS below), but each individual chunk is a single
      synchronous LLM call (`claude -p` or `opencode run`). If a chunk's
      prompt exceeds the chosen model's context window, that chunk
      fails. Tune CHUNK_SIZE downwards if you hit that.
    * No automatic retry on rate-limit or transient API errors — a
      single failed chunk aborts the whole translation.
    * Output quality and format-adherence vary by model. Weaker models
      may add preamble or drop cues more often; the tolerant parser,
      the retry pass, and the resumable broken-cache mitigate this (you
      can even re-run with a stronger model to fill the gaps).
    * Target language is restricted to English, Russian, or Kazakh by
      design. To add another, extend `prompt_target_language`,
      `_LANG_LABELS`, and `canonical_lang_name`.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Optional

# Make sibling _common.py importable.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (  # noqa: E402
    available_subtitle_langs,
    available_video_heights,
    check_ffmpeg,
    check_yt_dlp,
    choose_llm_backend,
    debug,
    die,
    download_subtitles_for_lang,
    download_video,
    find_existing_artifacts,
    find_or_create_results_dir,
    get_video_id,
    get_video_metadata,
    get_video_title,
    info,
    is_verbose,
    ok,
    prompt_youtube_url,
    run_llm,
    set_verbose,
    warn,
)


# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------

# Resolved relative to this script so chdir into the results folder doesn't
# break template lookup.
REPO_ROOT = Path(__file__).resolve().parent.parent
TRANSLATE_PROMPT_TEMPLATE = REPO_ROOT / "prompts" / "translate_prompt.md"

# Translation chunking. The source cue list is split into chunks of at most
# CHUNK_SIZE cues; up to MAX_WORKERS chunks run in parallel claude -p calls.
# A background heartbeat thread prints progress every HEARTBEAT_INTERVAL_S
# seconds so a slow chunk doesn't look hung.
CHUNK_SIZE = 1000
MAX_WORKERS = 4
HEARTBEAT_INTERVAL_S = 30.0

# Retry pass: if the main pass leaves cues uncovered (typically because
# claude merged sentence-split cues), we make ONE additional claude call
# with just those missing cues. If more than this many are missing, we
# skip the retry and fail — it indicates something more fundamental went
# wrong than the usual sentence-merge artifact.
MAX_RETRY_MISSING = 100


# ----------------------------------------------------------------------------
# Language helpers (label + slug + canonical name) — pure, unit-testable
# ----------------------------------------------------------------------------

# Map common language strings → 2-letter code used in output filenames
# (`<ID>.translated.<code>.srt`).
_LANG_LABELS = {
    "ru": "RU", "russian": "RU", "русский": "RU",
    "en": "EN", "english": "EN",
    "es": "ES", "spanish": "ES",
    "fr": "FR", "french": "FR",
    "de": "DE", "german": "DE",
    "it": "IT", "italian": "IT",
    "ja": "JA", "japanese": "JA",
    "zh": "ZH", "chinese": "ZH",
    "ko": "KO", "korean": "KO",
    "pt": "PT", "portuguese": "PT",
    "ar": "AR", "arabic": "AR",
    "tr": "TR", "turkish": "TR",
    "pl": "PL", "polish": "PL",
    "nl": "NL", "dutch": "NL",
    "uk": "UK", "ukrainian": "UK",
    "kk": "KK", "kazakh": "KK", "қазақ": "KK", "қазақша": "KK",
    "hi": "HI", "hindi": "HI",
}


def lang_label(language: str) -> str:
    """Short 2-letter-ish code (e.g. 'RU') used in output filenames."""
    s = language.strip().lower()
    if s in _LANG_LABELS:
        return _LANG_LABELS[s]
    if len(s) >= 2:
        return s[:2].upper()
    return "TR"  # generic 'translation'


def slug_lang(language: str) -> str:
    """Filename-safe slug, e.g. 'Russian' -> 'russian', 'ru' -> 'ru'."""
    s = re.sub(r"[^a-z0-9]+", "-", language.strip().lower()).strip("-")
    return s or "translated"


def canonical_lang_name(language: str) -> str:
    """
    Normalize the user's input into a stable display name used in prompts,
    headers, and sidecars. Both 'ru' and 'Russian' collapse to 'Russian'.

    For arbitrary other languages (used as the source-language input),
    we just title-case the input as a best-effort.
    """
    s = language.strip().lower()
    aliases = {
        "ru": "Russian", "russian": "Russian", "русский": "Russian",
        "en": "English", "english": "English",
        "es": "Spanish", "spanish": "Spanish",
        "fr": "French", "french": "French",
        "de": "German", "german": "German",
        "it": "Italian", "italian": "Italian",
        "ja": "Japanese", "japanese": "Japanese",
        "zh": "Chinese", "chinese": "Chinese",
        "ko": "Korean", "korean": "Korean",
        "pt": "Portuguese", "portuguese": "Portuguese",
        "ar": "Arabic", "arabic": "Arabic",
        "tr": "Turkish", "turkish": "Turkish",
        "pl": "Polish", "polish": "Polish",
        "nl": "Dutch", "dutch": "Dutch",
        "uk": "Ukrainian", "ukrainian": "Ukrainian",
        "kk": "Kazakh", "kazakh": "Kazakh",
        "hi": "Hindi", "hindi": "Hindi",
    }
    return aliases.get(s, language.strip().title())


def normalize_subtitle_langs(codes: list[str]) -> list[str]:
    """
    Collapse YouTube's regional subtitle variants to base ISO codes.

    'en-US', 'en-orig', 'en' → 'en'. download_subtitles_for_lang's
    `<lang>.*,<lang>` glob handles the actual variant selection on the
    yt-dlp side, so passing the base code is sufficient.

    Pure: no I/O.
    """
    bases: set[str] = set()
    for code in codes:
        if not isinstance(code, str):
            continue
        base = code.split("-")[0].strip().lower()
        if base:
            bases.add(base)
    return sorted(bases)


def lang_from_srt_path(srt_path: Path, video_id: str) -> str:
    """Extract the `<lang>` from `<video_id>.<lang>.srt`. Pure."""
    name = srt_path.name
    prefix = f"{video_id}."
    suffix = ".srt"
    if name.startswith(prefix) and name.endswith(suffix):
        return name[len(prefix):-len(suffix)]
    return ""


# ----------------------------------------------------------------------------
# Sidecar files
# ----------------------------------------------------------------------------

def source_lang_sidecar(video_id: str) -> Path:
    return Path(f"{video_id}.translate-source-lang.txt")


def target_lang_sidecar(video_id: str) -> Path:
    return Path(f"{video_id}.translate-target-lang.txt")


def quality_sidecar(video_id: str) -> Path:
    return Path(f"{video_id}.video-quality.txt")


def tool_sidecar(video_id: str) -> Path:
    return Path(f"{video_id}.translate-tool.txt")


def model_sidecar(video_id: str) -> Path:
    return Path(f"{video_id}.translate-model.txt")


def read_sidecar(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    val = path.read_text(encoding="utf-8").strip()
    return val or None


def write_sidecar(path: Path, value: str) -> None:
    path.write_text(value.strip() + "\n", encoding="utf-8")


def read_quality(video_id: str) -> Optional[int]:
    raw = read_sidecar(quality_sidecar(video_id))
    if raw is None:
        return None
    try:
        n = int(raw)
        return n if n > 0 else None
    except ValueError:
        return None


# ----------------------------------------------------------------------------
# Idempotency gate
# ----------------------------------------------------------------------------

def prompt_existing_files_action(mp4_path: Path, srts: list[Path]) -> str:
    """Returns 'proceed' or 'redownload'."""
    print()
    info("Files from a previous run already on disk:")
    try:
        size_mb = mp4_path.stat().st_size / 1024 / 1024
        print(f"    • {mp4_path.name}  ({size_mb:.1f} MB)")
    except OSError:
        print(f"    • {mp4_path.name}")
    if srts:
        print(f"    • {len(srts)} subtitle file(s):")
        for p in srts:
            print(f"        - {p.name}")
    else:
        print("    • (no subtitle files yet)")
    while True:
        raw = input(
            "\nWhat now? "
            "[p]roceed to translation using existing files / "
            "[r]e-download everything from scratch: "
        ).strip().lower()
        if raw in ("p", "proceed"):
            return "proceed"
        if raw in ("r", "redownload", "re-download"):
            return "redownload"
        warn("Please enter 'p' or 'r'.")


def wipe_downloads(mp4_path: Optional[Path], srts: list[Path]) -> None:
    """Delete mp4 + per-language SRT files. Sidecars and .md files survive."""
    if mp4_path is not None and mp4_path.exists():
        mp4_path.unlink()
        info(f"deleted {mp4_path.name}")
    for p in srts:
        if p.exists():
            p.unlink()
            info(f"deleted {p.name}")


# ----------------------------------------------------------------------------
# Quality prompt
# ----------------------------------------------------------------------------

def default_quality_choice(heights: list[int], previous: Optional[int]) -> int:
    """
    Pure helper: pick the default height to highlight in the menu.

    Preference order:
      1. The previously-used height, if it's in the advertised list.
      2. The highest height <= 720.
      3. The single highest available height.
    """
    if previous is not None and previous in heights:
        return previous
    under_720 = [h for h in heights if h <= 720]
    if under_720:
        return max(under_720)
    return heights[-1]


def prompt_quality(heights: list[int], previous: Optional[int]) -> int:
    """Interactive menu. Returns the chosen height."""
    if not heights:
        die(
            "yt-dlp did not advertise any video formats with a height.",
            "Try a different URL — this video may be audio-only or "
            "restricted.",
        )

    default = default_quality_choice(heights, previous)

    print()
    info("Available qualities:")
    for i, h in enumerate(heights, 1):
        marker = "  ← default" if h == default else ""
        print(f"  [{i}] {h}p{marker}")

    while True:
        raw = input(
            f"\nPick a quality [1-{len(heights)}, Enter for {default}p]: "
        ).strip()
        if not raw:
            return default
        if raw.isdigit():
            n = int(raw)
            if 1 <= n <= len(heights):
                return heights[n - 1]
        warn(f"Please enter a number between 1 and {len(heights)}.")


# ----------------------------------------------------------------------------
# Subtitle download loop
# ----------------------------------------------------------------------------

def download_all_subtitles(
    url: str, video_id: str, langs: list[str]
) -> list[Path]:
    """Fetch every base-code subtitle language, returning the paths that landed."""
    if not langs:
        warn("yt-dlp did not advertise any subtitle languages for this video.")
        return []

    print()
    info(f"Fetching subtitles for {len(langs)} language(s)…")
    downloaded: list[Path] = []
    for i, lang in enumerate(langs, 1):
        print(f"  [{i}/{len(langs)}] {lang}")
        path = download_subtitles_for_lang(url, video_id, lang)
        if path:
            downloaded.append(path)
    return downloaded


# ----------------------------------------------------------------------------
# Source-subtitle picker + target-language menu
# ----------------------------------------------------------------------------

def prompt_pick_source_subtitle(srts: list[Path], video_id: str) -> Path:
    if not srts:
        die(
            "No subtitle files on disk — nothing to translate.",
            "Re-run and pick [r]e-download at the existing-files prompt "
            "to retry, or use `python3 scripts/transcribe.py` to "
            "transcribe the audio directly.",
        )
    if len(srts) == 1:
        info(f"using the only available subtitles: {srts[0].name}")
        return srts[0]

    print()
    info("Available subtitle files:")
    for i, p in enumerate(srts, 1):
        lang = lang_from_srt_path(p, video_id) or "?"
        print(f"  [{i}] {p.name}    ({lang})")

    while True:
        raw = input(
            f"\nWhich subtitle file is the source? [1-{len(srts)}]: "
        ).strip()
        if raw.isdigit():
            n = int(raw)
            if 1 <= n <= len(srts):
                return srts[n - 1]
        warn(f"Please enter a number between 1 and {len(srts)}.")


def prompt_target_language(default: Optional[str] = None) -> str:
    """Three-option menu. Returns the canonical name ('English'/'Russian'/'Kazakh')."""
    hint = f" [default: {default}]" if default else ""
    while True:
        raw = input(
            f"\nTarget language: [e]nglish / [r]ussian / [k]azakh{hint}: "
        ).strip().lower()
        if not raw and default:
            return default
        if raw in ("e", "en", "english"):
            return "English"
        if raw in ("r", "ru", "russian"):
            return "Russian"
        if raw in ("k", "kk", "kazakh", "kz"):
            return "Kazakh"
        warn("Please enter 'e' for English, 'r' for Russian, or 'k' for Kazakh.")


def prompt_yes_no(question: str, default_no: bool = True) -> bool:
    suffix = " [y/N]: " if default_no else " [Y/n]: "
    raw = input(question + suffix).strip().lower()
    if not raw:
        return not default_no
    return raw in ("y", "yes")


# ----------------------------------------------------------------------------
# Translation output path + retranslate prompt
# ----------------------------------------------------------------------------

def _target_code(target_lang: str) -> str:
    """Lowercased 2-letter code used in output filenames (e.g. 'ru', 'en')."""
    return lang_label(target_lang).lower()


def translated_srt_path(video_id: str, target_lang: str) -> Path:
    """Canonical translated-subtitles output, e.g. `<ID>.translated.ru.srt`."""
    return Path(f"{video_id}.translated.{_target_code(target_lang)}.srt")


def broken_translated_srt_path(video_id: str, target_lang: str) -> Path:
    """Path used to persist a translation that failed timecode validation."""
    return Path(f"{video_id}.translated.{_target_code(target_lang)}.broken.srt")


def prompt_retranslate(video_id: str, source_lang: str, target_lang: str) -> str:
    """Returns 'skip', 'same', or 'change'."""
    out = translated_srt_path(video_id, target_lang)
    print()
    info(f"translation already exists: {out}")
    while True:
        choice = input(
            "What now? "
            "[s]kip / "
            f"[r]e-run with same languages ({source_lang} → {target_lang}) / "
            "[c]hange target language and re-run: "
        ).strip().lower()
        if choice in ("s", "skip"):
            return "skip"
        if choice in ("r", "rerun", "re-run", "same"):
            return "same"
        if choice in ("c", "change"):
            return "change"
        warn("Please enter s, r, or c.")


# ----------------------------------------------------------------------------
# SRT parsing + cue serialization (pure — runs in Python, never the LLM)
# ----------------------------------------------------------------------------
#
# The translate flow strips timecodes from the round-trip with claude:
#   1. Python parses the source SRT into [(cue_num, timecode_line, text)].
#   2. Python serializes the *translatable* cues as a tab-delimited
#      `<cue_num>\t<text>` block and sends only that to claude.
#   3. Claude returns the same shape with translated text.
#   4. Python parses the response back into {cue_num: translation}.
#   5. Python re-emits the final SRT, walking the source cue list and
#      writing the *original* cue number + timecode line under each
#      translation. Empty source cues (no text) are copied through
#      unchanged.
#
# Net effect: timecodes can't be corrupted because claude never sees them.

# Full timecode line, used to locate the timecode inside a cue block.
SRT_TIMECODE_LINE_RE = re.compile(
    r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})"
)


# (cue_num, timecode_line, text) — text is a single line with internal
# newlines + tabs collapsed to single spaces. text is "" for empty cues.
Cue = tuple[int, str, str]


def parse_source_cues(srt_text: str) -> list[Cue]:
    """Parse an SRT body into [(cue_num, timecode_line, text)].

    The cue_num is a 1-based sequential index assigned by this parser,
    not the SRT's own cue number — SRTs in the wild sometimes have
    gaps or non-sequential numbering, and our reassembly only needs
    a stable handle to match input and output lines.

    timecode_line is the raw `HH:MM:SS,mmm --> HH:MM:SS,mmm` line,
    preserved verbatim so it can be written back into the final SRT.

    text is the cue's body with internal newlines and tabs collapsed
    to single spaces (so the prompt's tab delimiter is unambiguous).
    Empty cues are included with text == "".
    """
    cues: list[Cue] = []
    blocks = re.split(r"\n\s*\n", srt_text.strip())
    for block in blocks:
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        tc_idx: Optional[int] = None
        for i, line in enumerate(lines):
            if SRT_TIMECODE_LINE_RE.search(line):
                tc_idx = i
                break
        if tc_idx is None:
            continue
        timecode_line = lines[tc_idx].strip()
        text_lines = lines[tc_idx + 1:]
        text = " ".join(ln.strip() for ln in text_lines if ln.strip())
        text = text.replace("\t", " ")
        cues.append((len(cues) + 1, timecode_line, text))
    return cues


def chunk_cues(cues: list[Cue], chunk_size: int) -> list[list[Cue]]:
    """Split `cues` into consecutive windows of at most `chunk_size` cues.

    Empty cues are kept in their natural window — serialize_cues_for_prompt
    will skip them when emitting the prompt body, so empty cues don't cost
    a token in the LLM round-trip but still anchor chunk boundaries.

    A non-positive `chunk_size` returns the input as a single chunk.
    Pure: no I/O.
    """
    if chunk_size <= 0:
        return [list(cues)]
    return [cues[i:i + chunk_size] for i in range(0, len(cues), chunk_size)]


def serialize_cues_for_prompt(cues: list[Cue]) -> str:
    """Render `<cue_num>\\t<text>\\n` for every cue with non-empty text.

    Empty cues are omitted from the LLM round-trip — they're copied
    through unchanged by assemble_translated_srt without paying the
    token cost of asking claude to translate empty input.
    """
    return "\n".join(
        f"{num}\t{text}" for (num, _tc, text) in cues if text
    )


def parse_claude_translations(output: str) -> dict[int, str]:
    """Parse `<cue_num>\\t<text>` lines into `{cue_num: translation}`.

    Tolerant: skips blank lines, lines without a tab, and lines whose
    pre-tab token isn't an int. validate_translation_coverage decides
    whether the resulting dict actually covers every translatable cue.
    """
    out: dict[int, str] = {}
    for raw in output.splitlines():
        line = raw.rstrip("\r")
        if not line.strip():
            continue
        tab = line.find("\t")
        if tab <= 0:
            continue
        head, body = line[:tab].strip(), line[tab + 1:].strip()
        if not head.isdigit() or not body:
            continue
        out[int(head)] = body
    return out


def load_partial_translations(text: str, cues: list[Cue]) -> dict[int, str]:
    """Parse a previously-saved partial/broken translation into {cue_num: text}.

    `<ID>.translated.<tgt>.broken.srt` is a resumable cache: a tab-delimited
    `<cue_num>\\t<translation>` list of every cue that was translated before
    a prior run failed validation. We read it back through the same tolerant
    parser used for claude's live output (so an older broken file written as
    concatenated raw chunk dumps still parses), then keep only the cue numbers
    that correspond to a translatable source cue — dropping any hallucinated
    or out-of-range numbers, exactly as the per-chunk strict filter does.

    Pure: no I/O.
    """
    translatable_ids = {n for (n, _tc, t) in cues if t}
    parsed = parse_claude_translations(text)
    return {n: t for n, t in parsed.items() if n in translatable_ids}


def serialize_translations_cache(translations: dict[int, str]) -> str:
    """Render {cue_num: translation} as sorted `<cue_num>\\t<text>` lines.

    This is the on-disk form of `<ID>.translated.<tgt>.broken.srt` — a
    resumable partial-translation cache. Sorted by cue number so it's easy
    to diff/inspect by hand and so `load_partial_translations` reads it back
    deterministically. Pure: no I/O.
    """
    return "\n".join(f"{n}\t{translations[n]}" for n in sorted(translations))


def validate_translation_coverage(
    cues: list[Cue], translations: dict[int, str]
) -> list[str]:
    """Return a list of human-readable issues; empty = all good.

    Every cue with non-empty source text must have a matching key in
    `translations`. Extra keys in translations (cue numbers that don't
    exist in the source) are reported but won't block the output —
    they're just ignored during assembly.
    """
    issues: list[str] = []
    translatable = [num for (num, _tc, text) in cues if text]
    expected = set(translatable)
    got = set(translations.keys())

    missing = sorted(expected - got)
    extra = sorted(got - expected)

    if missing:
        sample = ", ".join(str(n) for n in missing[:10])
        more = f" (+{len(missing) - 10} more)" if len(missing) > 10 else ""
        issues.append(
            f"Missing translations for {len(missing)} cue(s): {sample}{more}"
        )
    if extra:
        sample = ", ".join(str(n) for n in extra[:10])
        more = f" (+{len(extra) - 10} more)" if len(extra) > 10 else ""
        issues.append(
            f"Translation has {len(extra)} cue number(s) not in source: "
            f"{sample}{more}"
        )
    if len(translatable) != len(got):
        # Also surfaces when translation has the right *set* of numbers
        # but a different count from translatable (shouldn't happen
        # once missing/extra are zero, but defensive).
        issues.append(
            f"Cue count mismatch: source has {len(translatable)} "
            f"translatable cue(s), translation has {len(got)}."
        )
    return issues


def assemble_translated_srt(
    cues: list[Cue], translations: dict[int, str]
) -> str:
    """Reconstruct a valid SRT body from the source cue list + translations.

    For each source cue:
      * Emit "<cue_num>\\n<timecode_line>\\n<text>\\n\\n"
      * Translated text comes from `translations[cue_num]` if present;
        otherwise the original source text is preserved (covers empty
        source cues we never sent to claude).

    Cue numbers in the output are the 1-based indices we assigned in
    parse_source_cues, so the output is always strictly sequential
    even if the source had gaps.
    """
    parts: list[str] = []
    for num, tc_line, src_text in cues:
        body = translations.get(num, src_text)
        parts.append(f"{num}\n{tc_line}\n{body}\n")
    return "\n".join(parts) + "\n"


# ----------------------------------------------------------------------------
# Prompt builder (template-based, pure, brace-safe)
# ----------------------------------------------------------------------------

def build_translate_prompt(
    template: str,
    source_lang: str,
    target_lang: str,
    cues_block: str,
) -> str:
    """Substitute the three placeholders into the translate prompt."""
    return (
        template
        .replace("{{source_lang}}", source_lang)
        .replace("{{target_lang}}", target_lang)
        .replace("{{cues_block}}", cues_block)
    )


# ----------------------------------------------------------------------------
# Verbose-mode validation summary helper
# ----------------------------------------------------------------------------

def _debug_validation_summary(
    cues: list[Cue], translations: dict[int, str], issues: list[str]
) -> None:
    """Emit cue counts + first few issues at debug level."""
    if not is_verbose():
        return
    src_n = sum(1 for (_n, _tc, t) in cues if t)
    tgt_n = len(translations)
    debug(
        f"validator: source has {src_n} translatable cue(s), "
        f"translation returned {tgt_n}"
    )
    debug(f"validator: {len(issues)} issue(s) total")
    for line in issues[:5]:
        debug(f"  • {line}")
    if len(issues) > 5:
        debug(f"  …and {len(issues) - 5} more")


# ----------------------------------------------------------------------------
# Chunked-parallel translation: worker + heartbeat
# ----------------------------------------------------------------------------

class _Heartbeat:
    """Context manager that prints elapsed time + chunks-done periodically.

    Spawns a daemon thread on __enter__ that wakes up every `interval_s`
    seconds and emits an info() line as long as not every chunk is done.
    Each worker thread calls .mark_done() when it finishes; .mark_done()
    is the only mutator besides the stop event.
    """

    def __init__(self, total: int, interval_s: float = HEARTBEAT_INTERVAL_S):
        self.total = total
        self.interval_s = interval_s
        self.done = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._t0 = 0.0

    def __enter__(self) -> "_Heartbeat":
        self._t0 = time.monotonic()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def mark_done(self) -> None:
        with self._lock:
            self.done += 1

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            with self._lock:
                d = self.done
            if d >= self.total:
                return
            elapsed = time.monotonic() - self._t0
            mm = int(elapsed // 60)
            ss = int(elapsed % 60)
            info(
                f"…still working: {d}/{self.total} chunk(s) complete, "
                f"elapsed {mm}m {ss:02d}s"
            )


def _translate_chunk(
    chunk: list[Cue],
    template: str,
    source_name: str,
    target_name: str,
    chunk_idx: int,
    total_chunks: int,
    tool: str,
    model: Optional[str],
) -> tuple[dict[int, str], str]:
    """Translate one chunk of cues. Runs in a worker thread.

    Returns (translations dict, raw LLM output). Raises RuntimeError
    on empty LLM response; lets subprocess errors from run_llm
    propagate via SystemExit (which the future will surface to main).
    """
    cues_block = serialize_cues_for_prompt(chunk)
    if not cues_block:
        # All cues in this chunk were empty — nothing to translate, but
        # still record an empty raw output so the slot is occupied.
        ok(f"chunk {chunk_idx + 1}/{total_chunks}: no translatable cues (all empty)")
        return {}, ""

    prompt = build_translate_prompt(
        template, source_name, target_name, cues_block,
    )
    translatable_count = sum(1 for c in chunk if c[2])
    info(
        f"chunk {chunk_idx + 1}/{total_chunks}: dispatching "
        f"({translatable_count} translatable cue(s), "
        f"{len(cues_block)} chars)"
    )
    output = run_llm(prompt, tool, model).strip()
    if not output:
        raise RuntimeError(
            f"chunk {chunk_idx + 1}/{total_chunks}: {tool} returned empty."
        )
    raw_translations = parse_claude_translations(output)

    # Strict per-chunk filter: drop any cue number claude emitted that
    # wasn't actually in this chunk's input. Claude sometimes hallucinates
    # numbers from neighboring ranges when it merges sentence-split cues
    # (to keep the line count matching), and those bogus numbers could
    # otherwise overwrite real translations from adjacent chunks during
    # the global dict.update() merge.
    expected_ids = {n for (n, _tc, t) in chunk if t}
    translations = {n: t for n, t in raw_translations.items() if n in expected_ids}
    dropped = len(raw_translations) - len(translations)
    if dropped > 0:
        debug(
            f"chunk {chunk_idx + 1}/{total_chunks}: dropped {dropped} "
            f"hallucinated cue ID(s) outside chunk range"
        )

    extras_note = f" (dropped {dropped} extras)" if dropped > 0 else ""
    ok(
        f"chunk {chunk_idx + 1}/{total_chunks}: "
        f"{len(translations)}/{translatable_count} cue(s) translated"
        f"{extras_note}"
    )
    return translations, output


# ----------------------------------------------------------------------------
# Translation orchestrator
# ----------------------------------------------------------------------------

def translate_subtitles(
    srt_path: Path,
    video_id: str,
    source_lang: str,
    target_lang: str,
) -> None:
    """Translate the SRT and write `<ID>.translated.<tgt>.srt`.

    Timecodes are stripped before each LLM call and re-emitted by Python
    afterwards, so the model never sees them. Any prior
    `<ID>.translated.<tgt>.broken.srt` cache is seeded first, so only the
    still-missing cues are translated (and none at all if the cache is
    complete). When there's work to do the user picks the CLI tool +
    model (claude / opencode) via `choose_llm_backend`; the remaining cue
    list is split into `CHUNK_SIZE`-sized chunks, dispatched up to
    `MAX_WORKERS` in parallel, then merged. Validation is cue-count parity
    only; missing cues trigger one isolated retry pass, and a still-failing
    result is persisted to `<ID>.translated.<tgt>.broken.srt` before the
    script dies.
    """
    out_path = translated_srt_path(video_id, target_lang)
    if out_path.exists():
        choice = prompt_retranslate(video_id, source_lang, target_lang)
        if choice == "skip":
            ok("Skipping translation. Done.")
            return
        if choice == "change":
            target_lang = prompt_target_language()
            out_path = translated_srt_path(video_id, target_lang)
            if slug_lang(source_lang) == slug_lang(target_lang):
                die(
                    f"Source and target language are the same "
                    f"({source_lang}). Nothing to translate.",
                )

    srt_text = srt_path.read_text(encoding="utf-8").strip()
    if not srt_text:
        die(f"{srt_path} is empty — nothing to translate.")

    if not TRANSLATE_PROMPT_TEMPLATE.exists():
        die(
            f"Translate prompt template not found at "
            f"{TRANSLATE_PROMPT_TEMPLATE}.",
            "Make sure the `prompts/` folder hasn't been moved out of "
            "the repo.",
        )

    source_name = canonical_lang_name(source_lang)
    target_name = canonical_lang_name(target_lang)

    cues = parse_source_cues(srt_text)
    translatable = [c for c in cues if c[2]]
    if not translatable:
        die(
            f"{srt_path} parsed to zero translatable cues.",
            "The file may be empty or malformed.",
        )

    template = TRANSLATE_PROMPT_TEMPLATE.read_text(encoding="utf-8")

    all_translations: dict[int, str] = {}

    # ----- Resume from a prior broken/partial translation -------------------
    #
    # If a previous run left a `<ID>.translated.<tgt>.broken.srt` behind, it
    # is a resumable cache: a sorted `<cue_num>\t<text>` list of every cue
    # that was translated before that run failed validation. Seed those so
    # we only call claude for the cues still missing — and skip claude
    # entirely if the cache already covers every translatable cue.
    broken_path = broken_translated_srt_path(video_id, target_lang)
    if broken_path.exists():
        seed = load_partial_translations(
            broken_path.read_text(encoding="utf-8"), cues,
        )
        if seed:
            all_translations.update(seed)
            info(
                f"found {broken_path.name}: resuming with "
                f"{len(seed)}/{len(translatable)} cue(s) already translated"
            )
        else:
            debug(f"{broken_path.name} present but parsed to 0 usable cues")

    pending_ids = {n for (n, _tc, t) in cues if t} - set(all_translations)
    pending = [c for c in cues if c[2] and c[0] in pending_ids]

    # Bound for the retry pass below; only ever read when pending was
    # non-empty (a full cache hit produces no issues and skips both).
    tool: Optional[str] = None
    model: Optional[str] = None

    if not pending:
        ok(
            f"existing translation cache already covers all "
            f"{len(translatable)} cue(s) — no LLM calls needed."
        )
    else:
        # Pick the CLI tool + model (claude / opencode), preflight it, and
        # remember the choice in sidecars for next time.
        prev_tool = read_sidecar(tool_sidecar(video_id))
        prev_model = read_sidecar(model_sidecar(video_id))
        tool, model = choose_llm_backend(prev_tool, prev_model)
        write_sidecar(tool_sidecar(video_id), tool)
        write_sidecar(model_sidecar(video_id), model or "")

        chunks = chunk_cues(pending, CHUNK_SIZE)
        info(
            f"translating {len(pending)} remaining cue(s) "
            f"in {len(chunks)} chunk(s) of up to {CHUNK_SIZE}; "
            f"running up to {MAX_WORKERS} in parallel"
        )
        debug(
            f"{srt_path.name}: {source_name} → {target_name}; "
            f"chunk sizes: {[len(chunk) for chunk in chunks]}"
        )

        t0 = time.monotonic()
        with _Heartbeat(total=len(chunks)) as hb:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=MAX_WORKERS,
                thread_name_prefix="translate",
            ) as pool:
                futures = [
                    pool.submit(
                        _translate_chunk,
                        chunk, template, source_name, target_name,
                        i, len(chunks), tool, model,
                    )
                    for i, chunk in enumerate(chunks)
                ]
                for future in concurrent.futures.as_completed(futures):
                    chunk_translations, _raw = future.result()
                    all_translations.update(chunk_translations)
                    hb.mark_done()

        elapsed = time.monotonic() - t0
        info(f"all chunks completed in {elapsed:.1f}s")

    issues = validate_translation_coverage(cues, all_translations)
    _debug_validation_summary(cues, all_translations, issues)

    # ----- Retry pass for cues the model merged out of existence ------------
    #
    # Most failures of the main pass are the model merging two consecutive
    # source cues (typically a sentence split across cues like
    # "Like a dream,\nmay not have actually occurred."). Re-translating
    # those cues in isolation almost always succeeds since the model has no
    # adjacent context to merge into.
    if issues:
        translatable_set = {n for (n, _tc, t) in cues if t}
        missing = sorted(translatable_set - set(all_translations.keys()))
        if missing and len(missing) <= MAX_RETRY_MISSING:
            info(
                f"retry pass: {len(missing)} cue(s) missing from the main "
                f"pass (the model likely merged them with neighbors). "
                f"Re-translating in isolation…"
            )
            src_by_id = {n: (n, tc, t) for (n, tc, t) in cues}
            retry_chunk = [src_by_id[n] for n in missing]
            retry_block = serialize_cues_for_prompt(retry_chunk)
            if retry_block:
                retry_prompt = build_translate_prompt(
                    template, source_name, target_name, retry_block,
                )
                debug(
                    f"retry prompt: {len(retry_prompt)} chars for "
                    f"{len(missing)} cue(s)"
                )
                retry_output = run_llm(retry_prompt, tool, model).strip()
                retry_translations = parse_claude_translations(retry_output)
                missing_set = set(missing)
                retry_translations = {
                    n: t for n, t in retry_translations.items()
                    if n in missing_set
                }
                all_translations.update(retry_translations)
                ok(
                    f"retry pass: recovered "
                    f"{len(retry_translations)}/{len(missing)} cue(s)"
                )
                issues = validate_translation_coverage(cues, all_translations)
                _debug_validation_summary(cues, all_translations, issues)
        elif missing and len(missing) > MAX_RETRY_MISSING:
            warn(
                f"{len(missing)} cue(s) missing — above the retry threshold "
                f"({MAX_RETRY_MISSING}). Skipping retry pass."
            )

    if issues:
        # Persist everything translated so far as a sorted, resumable cache
        # (see the resume block above) so the next run only fills the gaps
        # instead of re-translating from scratch.
        broken_path.write_text(
            serialize_translations_cache(all_translations) + "\n",
            encoding="utf-8",
        )
        preview = "\n".join(issues[:10])
        more = f"\n…and {len(issues) - 10} more." if len(issues) > 10 else ""
        die(
            "the model's response did not cover every source cue, even after "
            "the retry pass.",
            "Issues:\n"
            f"{preview}{more}\n\n"
            f"The cues translated so far have been saved to {broken_path} as "
            f"a resumable cache — just re-run translate.py on the same video "
            f"and it will translate only the missing cues.",
        )

    ok(
        f"cue-count validation passed "
        f"({len(all_translations)}/{len(translatable)} translatable cues covered)."
    )

    final_srt = assemble_translated_srt(cues, all_translations)
    out_path.write_text(final_srt, encoding="utf-8")
    write_sidecar(source_lang_sidecar(video_id), source_lang)
    write_sidecar(target_lang_sidecar(video_id), target_lang)
    # The partial cache is now superseded by a complete translation.
    if broken_path.exists():
        broken_path.unlink()
        info(f"removed {broken_path.name} (translation now complete)")
    try:
        size_kb = out_path.stat().st_size / 1024
        debug(f"{out_path.name}: {size_kb:.1f} KB written")
    except OSError:
        pass
    ok(f"wrote {out_path}")


# ----------------------------------------------------------------------------
# Orchestration
# ----------------------------------------------------------------------------

def maybe_translate(video_id: str, video_path: Path) -> None:
    """Top-level translation gate after downloads are complete."""
    print()
    if not prompt_yes_no("Translate subtitles?"):
        info("Skipping translation.")
        return

    # Pick the source subtitle file.
    _, srts = find_existing_artifacts(video_id)
    srt_path = prompt_pick_source_subtitle(srts, video_id)
    source_lang = lang_from_srt_path(srt_path, video_id) or "unknown"
    if source_lang == "unknown":
        warn(
            f"could not parse a language code from {srt_path.name}; "
            "labelling source as 'unknown'."
        )

    # Pick the target.
    prev_target = read_sidecar(target_lang_sidecar(video_id))
    target_lang = prompt_target_language(default=prev_target)

    if slug_lang(source_lang) == slug_lang(target_lang):
        die(
            f"Source and target language are the same ({source_lang}). "
            "Nothing to translate.",
            "Pick a different target language and try again.",
        )

    translate_subtitles(srt_path, video_id, source_lang, target_lang)


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="translate.py",
        description=(
            "Download a YouTube video + every advertised subtitle language, "
            "then optionally translate one of those subtitles into EN/RU/KK "
            "via claude."
        ),
    )
    p.add_argument(
        "-v", "--verbose",
        action="store_true",
        help=(
            "Print dim [d] diagnostic lines to stderr (yt-dlp argv, claude "
            "prompt sizes, claude durations, source/translated cue counts, "
            "first few missing-cue issues, output file sizes)."
        ),
    )
    return p.parse_args(argv)


def main() -> int:
    args = _parse_args()
    set_verbose(args.verbose)

    print("=== YouTube downloader + claude translation ===\n")
    if is_verbose():
        debug("verbose mode enabled")

    info("Preflight checks…")
    check_yt_dlp()
    check_ffmpeg()
    # The LLM CLI (claude or opencode) is checked later, once the user picks
    # which one to translate with — neither is required just to download.

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

    # ----- Idempotency gate -------------------------------------------------
    existing_mp4, existing_srts = find_existing_artifacts(video_id)
    debug(
        f"idempotency check: mp4={'yes' if existing_mp4 else 'no'}, "
        f"existing srts={len(existing_srts)}"
    )
    skip_downloads = False
    if existing_mp4 is not None:
        action = prompt_existing_files_action(existing_mp4, existing_srts)
        debug(f"idempotency action: {action}")
        if action == "proceed":
            skip_downloads = True
            video_path = existing_mp4
        else:  # "redownload"
            wipe_downloads(existing_mp4, existing_srts)

    # ----- Download phase ---------------------------------------------------
    if not skip_downloads:
        metadata = get_video_metadata(url)
        heights = available_video_heights(metadata)
        debug(f"available heights: {heights}")
        prev_quality = read_quality(video_id)
        chosen_height = prompt_quality(heights, prev_quality)
        debug(f"chosen height: {chosen_height}p")
        write_sidecar(quality_sidecar(video_id), str(chosen_height))

        video_path = download_video(url, video_id, max_height=chosen_height)

        sub_langs_raw = available_subtitle_langs(metadata)
        sub_langs = normalize_subtitle_langs(sub_langs_raw)
        debug(
            f"subtitle langs: raw={sub_langs_raw}, normalized={sub_langs}"
        )
        download_all_subtitles(url, video_id, sub_langs)

    # ----- Summary ----------------------------------------------------------
    _, final_srts = find_existing_artifacts(video_id)
    print()
    ok("Downloads complete.")
    print(f"  • {video_path.name}  — video")
    if final_srts:
        print(f"  • {len(final_srts)} subtitle file(s):")
        for p in final_srts:
            print(f"      - {p.name}")
    else:
        print("  • (no subtitle files were available for this video)")

    # ----- Translation gate -------------------------------------------------
    maybe_translate(video_id, video_path)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        warn("Interrupted.")
        sys.exit(130)
