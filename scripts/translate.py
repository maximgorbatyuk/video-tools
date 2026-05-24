#!/usr/bin/env python3
"""
scripts/translate.py
====================

Interactive command-line tool that downloads a YouTube video (720p) along
with its subtitles in a chosen source language, then translates those
subtitles into English or Russian via the `claude` CLI, preserving every
timecode and producing a side-by-side Markdown file.

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
                    720p video + audio streams
    claude        — Claude Code CLI               →  npm install -g
                    (used for the actual                 @anthropic-ai/claude-code
                     translation step)                claude login

------------------------------------------------------------------------
Usage
------------------------------------------------------------------------
    python3 scripts/translate.py
        or
    python3 start.py   (then pick option 2)

The script is fully interactive:
    1. Runs preflight checks.
    2. Prompts for a YouTube URL.
    3. Resolves it to the canonical 11-char video ID (via yt-dlp).
    4. Downloads the 720p MP4 (skipped if already on disk).
    5. Prompts for the SOURCE subtitle language as an ISO-639-1 code
       (e.g. en, ru, zh). Defaults to en.
    6. Downloads those subtitles via yt-dlp (skipped if already on disk).
    7. Prompts for the TARGET language: English or Russian (two-option
       menu — those are the only supported targets by design).
    8. Refuses if source == target (nothing to translate).
    9. Calls `claude -p` with a deterministic prompt that asks for the
       same SRT cue order with the original line and a fluent
       translation underneath.
   10. Validates the cue count of claude's response and writes a
       Markdown file containing the side-by-side output.

------------------------------------------------------------------------
Outputs (written to current working directory, named by video ID)
------------------------------------------------------------------------
    <ID>.mp4
        720p video download.

    <ID>.<src-lang>.srt
        Source-language subtitles fetched from YouTube (e.g.
        `<ID>.en.srt`, `<ID>.zh.srt`). Manual subs are preferred,
        auto-generated subs are used as a fallback.

    <ID>.translated.<src-slug>-to-<target-slug>.md
        Side-by-side translation, one cue per block:

            [HH:MM:SS,mmm --> HH:MM:SS,mmm]
            <SRC>: <original line>
            <TGT>: <fluent translation>

    <ID>.translate-source-lang.txt
        Sidecar remembering the source language used last time.

    <ID>.translate-target-lang.txt
        Sidecar remembering the target language used last time.

------------------------------------------------------------------------
Idempotency
------------------------------------------------------------------------
    * If <ID>.mp4 already exists, the download step is skipped.
    * If <ID>.<src-lang>.srt already exists, the subtitle fetch is skipped.
    * If both sidecars exist, the script offers to re-use the previous
      source + target language combination.
    * If <ID>.translated.<src>-to-<tgt>.md already exists for the chosen
      pair, the script asks `[s]kip / [r]e-run with same / [c]hange`.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

# Make sibling _common.py importable.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (  # noqa: E402
    check_claude,
    check_ffmpeg,
    check_yt_dlp,
    count_cue_blocks,
    die,
    download_subtitles_for_lang,
    download_video,
    get_video_id,
    info,
    ok,
    prompt_youtube_url,
    warn,
)


# ----------------------------------------------------------------------------
# Language helpers (label + slug)
# ----------------------------------------------------------------------------

# Map common language strings → short label used in the side-by-side output.
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
    "kk": "KK", "kazakh": "KK",
    "hi": "HI", "hindi": "HI",
}


def lang_label(language: str) -> str:
    """Short 2-letter-ish label for the side-by-side output (e.g. 'RU')."""
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
    headers, and sidecars. Both 'ru' and 'Russian' collapse to 'Russian'
    here; both 'en' and 'English' collapse to 'English'.

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


# ----------------------------------------------------------------------------
# Sidecar files (remember source + target language across runs)
# ----------------------------------------------------------------------------

def source_lang_sidecar(video_id: str) -> Path:
    return Path(f"{video_id}.translate-source-lang.txt")


def target_lang_sidecar(video_id: str) -> Path:
    return Path(f"{video_id}.translate-target-lang.txt")


def read_sidecar(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    val = path.read_text(encoding="utf-8").strip()
    return val or None


def write_sidecar(path: Path, value: str) -> None:
    path.write_text(value.strip() + "\n", encoding="utf-8")


# ----------------------------------------------------------------------------
# Prompts
# ----------------------------------------------------------------------------

def prompt_source_language(default: str = "en") -> str:
    """
    Ask the user which subtitle language to download from YouTube.
    Returns a lowercase ISO-639-1 code (or a short region-tagged variant).
    """
    while True:
        raw = input(
            f"\nSource subtitle language (ISO-639-1 code, e.g. en, ru, zh) "
            f"[default: {default}]: "
        ).strip().lower()
        if not raw:
            return default
        # Accept short codes ("en") and short region-tagged forms ("zh-hans").
        if len(raw) < 2 or len(raw) > 7 or not re.match(r"^[a-z]{2}[a-z0-9-]*$", raw):
            warn(f"{raw!r} doesn't look like a language code. Try again.")
            continue
        return raw


def prompt_target_language(default: Optional[str] = None) -> str:
    """
    Two-option menu: English or Russian. Returns the canonical name.
    """
    hint = f" [default: {default}]" if default else ""
    while True:
        raw = input(
            f"\nTarget language: [e]nglish / [r]ussian{hint}: "
        ).strip().lower()
        if not raw and default:
            return default
        if raw in ("e", "en", "english"):
            return "English"
        if raw in ("r", "ru", "russian"):
            return "Russian"
        warn("Please enter 'e' for English or 'r' for Russian.")


def translated_md_path(video_id: str, source_lang: str, target_lang: str) -> Path:
    return Path(
        f"{video_id}.translated."
        f"{slug_lang(source_lang)}-to-{slug_lang(target_lang)}.md"
    )


def prompt_retranslate(video_id: str, source_lang: str, target_lang: str) -> str:
    """Returns 'skip', 'same', or 'change'."""
    out = translated_md_path(video_id, source_lang, target_lang)
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
# Claude CLI translation
# ----------------------------------------------------------------------------

def build_translate_prompt(
    source_lang: str,
    target_lang: str,
    src_label: str,
    tgt_label: str,
    srt_text: str,
) -> str:
    """Construct the prompt we hand to `claude -p`."""
    return f"""You are translating {source_lang} subtitles into {target_lang}.

Input: an SRT subtitle file.

For each cue in the input, output a block in this EXACT format:

[HH:MM:SS,mmm --> HH:MM:SS,mmm]
{src_label}: <the original {source_lang} line, verbatim>
{tgt_label}: <a natural, fluent translation into {target_lang}>

Rules:
- Preserve every timecode exactly as it appears in the SRT.
- Preserve the order of cues — do not reorder, merge, or drop any cue.
- Translate naturally — not word-for-word — while keeping the meaning,
  tone, and register of each sentence intact.
- Keep proper nouns, technical terms, brand names, and code in the
  original language when appropriate.
- If a cue contains multiple sentences, translate them all in the
  translation line.
- Output ONLY the cue blocks. No preamble, no headings, no closing
  remarks, no markdown code fences. Separate blocks with one blank line.

SRT INPUT:
{srt_text}
"""


def run_claude(prompt: str) -> str:
    """Call `claude -p <prompt>` and return stdout."""
    info("invoking claude CLI (this may take a minute for longer videos)…")
    try:
        result = subprocess.run(
            ["claude", "-p", prompt],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        die(
            "`claude` CLI returned a non-zero exit code.",
            f"stderr:\n{e.stderr}",
        )
    return result.stdout


# ----------------------------------------------------------------------------
# Orchestration
# ----------------------------------------------------------------------------

def pick_languages(video_id: str) -> tuple[str, str]:
    """
    Determine the (source, target) language pair to use for this run,
    honoring sidecars where possible.
    """
    prev_src = read_sidecar(source_lang_sidecar(video_id))
    prev_tgt = read_sidecar(target_lang_sidecar(video_id))

    if prev_src and prev_tgt:
        info(f"previous run: source = {prev_src}, target = {prev_tgt}")
        same = input(
            f"Re-use '{prev_src} → {prev_tgt}'? [Y/n]: "
        ).strip().lower()
        if same in ("", "y", "yes"):
            return prev_src, prev_tgt

    source_lang = prompt_source_language(default=prev_src or "en")
    target_lang = prompt_target_language(default=prev_tgt)

    return source_lang, target_lang


def translate_subtitles(srt_path: Path, video_id: str,
                        source_lang: str, target_lang: str) -> None:
    out_path = translated_md_path(video_id, source_lang, target_lang)
    if out_path.exists():
        choice = prompt_retranslate(video_id, source_lang, target_lang)
        if choice == "skip":
            ok("Skipping translation. Done.")
            return
        if choice == "change":
            target_lang = prompt_target_language()
            out_path = translated_md_path(video_id, source_lang, target_lang)
            # If a translation already exists for the new pair too, just
            # overwrite — the user has been re-prompted once already.

    srt_text = srt_path.read_text(encoding="utf-8").strip()
    if not srt_text:
        die(f"{srt_path} is empty — nothing to translate.")

    src_label = lang_label(source_lang)
    tgt_label = lang_label(target_lang)
    source_name = canonical_lang_name(source_lang)
    target_name = canonical_lang_name(target_lang)

    prompt = build_translate_prompt(
        source_name, target_name, src_label, tgt_label, srt_text,
    )

    output = run_claude(prompt).strip()
    if not output:
        die("claude returned an empty response.")

    src_cues = count_cue_blocks(srt_text)
    out_cues = count_cue_blocks(output)
    if out_cues == 0:
        die(
            "claude's output contained no timecode blocks — the response "
            "didn't follow the requested format.",
            f"First 500 chars of response:\n{output[:500]}",
        )
    if out_cues < src_cues * 0.8:
        warn(
            f"claude returned {out_cues} cue blocks but the SRT has "
            f"{src_cues}. The response may be truncated."
        )
    else:
        ok(f"claude returned {out_cues} cue blocks (source has {src_cues})")

    header = (
        f"# Subtitle translation — {video_id}\n\n"
        f"Source: `{srt_path.name}` ({source_name})  ·  "
        f"Target language: **{target_name}**\n\n"
        f"Each block shows the original timecode, the {source_name} line, "
        f"and a {target_name} rendering of the same line.\n\n"
        f"---\n\n"
    )
    out_path.write_text(header + output + "\n", encoding="utf-8")
    write_sidecar(source_lang_sidecar(video_id), source_lang)
    write_sidecar(target_lang_sidecar(video_id), target_lang)
    ok(f"wrote {out_path}")


def main() -> int:
    print("=== YouTube subtitles → claude translation ===\n")

    info("Preflight checks…")
    check_yt_dlp()
    check_ffmpeg()
    check_claude()

    url = prompt_youtube_url()
    video_id = get_video_id(url)
    info(f"video id: {video_id}")

    download_video(url, video_id)

    source_lang, target_lang = pick_languages(video_id)

    if slug_lang(source_lang) == slug_lang(target_lang):
        die(
            f"Source and target language are the same ({source_lang}). "
            "Nothing to translate.",
            "Pick a different target language and try again.",
        )

    srt_path = download_subtitles_for_lang(url, video_id, source_lang)
    if srt_path is None:
        die(
            f"No {source_lang} subtitles available on YouTube for "
            f"video {video_id}.",
            "Try a different source language, or use the transcription "
            "script (`python3 scripts/transcribe.py`) which produces a "
            "transcript directly from the audio.",
        )

    translate_subtitles(srt_path, video_id, source_lang, target_lang)

    print()
    ok("Translation done.")
    print(f"  • {video_id}.mp4                                          — 720p download")
    print(f"  • {video_id}.{source_lang}.srt                                       — source subtitles")
    print(f"  • {translated_md_path(video_id, source_lang, target_lang)}  — side-by-side translation")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        warn("Interrupted.")
        sys.exit(130)
