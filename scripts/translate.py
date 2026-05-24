#!/usr/bin/env python3
"""
scripts/translate.py
====================

Interactive command-line tool that downloads a YouTube video and every
subtitle language YouTube advertises for it, then optionally translates
one of those subtitle files into English, Russian, or Kazakh via the
`claude` CLI — preserving every timecode and validating the result with
Python before declaring success.

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
                    (used for the translation             @anthropic-ai/claude-code
                     step)                            claude login

------------------------------------------------------------------------
Usage
------------------------------------------------------------------------
    python3 scripts/translate.py
        or
    python3 start.py   (then pick option 2)

The script is fully interactive. Algorithm:

    1. Preflight checks for yt-dlp, ffmpeg, claude.
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
         - Substitute into `prompts/translate_prompt.md` and call
           `claude -p`.
         - Run `validate_timecodes()` (pure Python) over claude's
           response. If every timecode matches the source, write the
           side-by-side Markdown file.
         - If the validator finds inconsistencies, retry once with
           `prompts/translate_fix_prompt.md` — the new prompt embeds
           the list of mismatched cues. Validate again. If still
           inconsistent, die with a clear error pointing to the broken
           output and the issue list.

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

        <ID>.translated.<src-slug>-to-<target-slug>.md
            Side-by-side translation, one cue per block:

                [HH:MM:SS,mmm --> HH:MM:SS,mmm]
                <SRC>: <original line>
                <TGT>: <fluent translation>

        <ID>.video-quality.txt
            Sidecar remembering the last chosen download height.

        <ID>.translate-source-lang.txt
            Sidecar remembering the source language used last time.

        <ID>.translate-target-lang.txt
            Sidecar remembering the target language used last time.

------------------------------------------------------------------------
Idempotency
------------------------------------------------------------------------
    * The results folder is re-used across days — the lookup is by
      video ID, not by date.
    * `<ID>.mp4` already on disk → `[p]roceed / [r]e-download` gate
      before any network I/O.
    * Individual subtitle downloads skip if `<ID>.<lang>.srt` already
      exists.
    * `<ID>.translated.<src>-to-<tgt>.md` already exists → asks
      `[s]kip / [r]e-run with same / [c]hange target language`.
    * Sidecars (`.translate-*-lang.txt`, `.video-quality.txt`) are
      offered as defaults on subsequent runs.

------------------------------------------------------------------------
Limitations
------------------------------------------------------------------------
    * Single Claude call per translation pass (plus at most one
      automated fix-up attempt) — very long videos may exceed Claude's
      context window. The fix-up loop catches timecode-shape failures
      but does NOT detect semantic errors in the translation itself.
    * Target language is restricted to English, Russian, or Kazakh by
      design. To add another, extend `prompt_target_language`,
      `_LANG_LABELS`, and `canonical_lang_name`.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

# Make sibling _common.py importable.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (  # noqa: E402
    available_subtitle_langs,
    available_video_heights,
    check_claude,
    check_ffmpeg,
    check_yt_dlp,
    die,
    download_subtitles_for_lang,
    download_video,
    find_existing_artifacts,
    find_or_create_results_dir,
    get_video_id,
    get_video_metadata,
    get_video_title,
    info,
    ok,
    prompt_youtube_url,
    warn,
)


# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------

# Resolved relative to this script so chdir into the results folder doesn't
# break template lookup.
REPO_ROOT = Path(__file__).resolve().parent.parent
TRANSLATE_PROMPT_TEMPLATE = REPO_ROOT / "prompts" / "translate_prompt.md"
TRANSLATE_FIX_PROMPT_TEMPLATE = REPO_ROOT / "prompts" / "translate_fix_prompt.md"

# Max number of fix-up attempts after the first translation pass.
# 1 = first call + 1 retry on timecode-validation failure.
MAX_FIXUP_RETRIES = 1


# ----------------------------------------------------------------------------
# Language helpers (label + slug + canonical name) — pure, unit-testable
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
    "kk": "KK", "kazakh": "KK", "қазақ": "KK", "қазақша": "KK",
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
# Timecode validation (pure — runs in Python, not via the LLM)
# ----------------------------------------------------------------------------

# Full timecode line, captured as a single normalized string.
SRT_TIMECODE_LINE_RE = re.compile(
    r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})"
)


def extract_timecodes(text: str) -> list[str]:
    """Return every `HH:MM:SS,mmm --> HH:MM:SS,mmm` range found in `text`, in order."""
    return [
        f"{m.group(1)} --> {m.group(2)}"
        for m in SRT_TIMECODE_LINE_RE.finditer(text)
    ]


def validate_timecodes(source_text: str, translated_text: str) -> list[str]:
    """
    Compare the timecode sequence in `translated_text` against `source_text`.

    Returns a (possibly empty) list of human-readable issue descriptions.
    Empty list = the two sequences match position-by-position.

    Pure: no I/O.
    """
    src = extract_timecodes(source_text)
    tgt = extract_timecodes(translated_text)

    issues: list[str] = []
    if len(src) != len(tgt):
        issues.append(
            f"Cue count mismatch: source has {len(src)} cues, "
            f"translation has {len(tgt)} cues."
        )

    n = min(len(src), len(tgt))
    for i in range(n):
        if src[i] != tgt[i]:
            issues.append(
                f"Cue #{i + 1}: expected `{src[i]}`, got `{tgt[i]}`."
            )

    if len(tgt) < len(src):
        for i in range(len(tgt), len(src)):
            issues.append(
                f"Cue #{i + 1}: missing from translation (source: `{src[i]}`)."
            )
    elif len(tgt) > len(src):
        for i in range(len(src), len(tgt)):
            issues.append(
                f"Position #{i + 1}: extra cue in translation (`{tgt[i]}`) — "
                f"not in source."
            )
    return issues


# ----------------------------------------------------------------------------
# Prompt builders (template-based, pure, brace-safe)
# ----------------------------------------------------------------------------

def build_translate_prompt(
    template: str,
    source_lang: str,
    target_lang: str,
    src_label: str,
    tgt_label: str,
    srt_text: str,
) -> str:
    """Substitute the five placeholders into the first-pass translate prompt."""
    return (
        template
        .replace("{{source_lang}}", source_lang)
        .replace("{{target_lang}}", target_lang)
        .replace("{{src_label}}", src_label)
        .replace("{{tgt_label}}", tgt_label)
        .replace("{{srt_text}}", srt_text)
    )


def build_translate_fix_prompt(
    template: str,
    video_id: str,
    source_lang: str,
    target_lang: str,
    src_label: str,
    tgt_label: str,
    original_srt: str,
    broken_translation: str,
    timecode_issues: str,
) -> str:
    """Substitute placeholders into the fix-up reprompt."""
    return (
        template
        .replace("{{video_id}}", video_id)
        .replace("{{source_lang}}", source_lang)
        .replace("{{target_lang}}", target_lang)
        .replace("{{src_label}}", src_label)
        .replace("{{tgt_label}}", tgt_label)
        .replace("{{original_srt}}", original_srt)
        .replace("{{broken_translation}}", broken_translation)
        .replace("{{timecode_issues}}", timecode_issues)
    )


# ----------------------------------------------------------------------------
# Claude invocation
# ----------------------------------------------------------------------------

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
# Translation orchestrator (with validation + retry)
# ----------------------------------------------------------------------------

def translate_subtitles(
    srt_path: Path,
    video_id: str,
    source_lang: str,
    target_lang: str,
) -> None:
    """First pass + validated retry. Writes the .md on success, dies on
    final failure."""
    out_path = translated_md_path(video_id, source_lang, target_lang)
    if out_path.exists():
        choice = prompt_retranslate(video_id, source_lang, target_lang)
        if choice == "skip":
            ok("Skipping translation. Done.")
            return
        if choice == "change":
            target_lang = prompt_target_language()
            out_path = translated_md_path(video_id, source_lang, target_lang)
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

    src_label = lang_label(source_lang)
    tgt_label = lang_label(target_lang)
    source_name = canonical_lang_name(source_lang)
    target_name = canonical_lang_name(target_lang)

    # ---------- First pass --------------------------------------------------
    template = TRANSLATE_PROMPT_TEMPLATE.read_text(encoding="utf-8")
    prompt = build_translate_prompt(
        template, source_name, target_name, src_label, tgt_label, srt_text,
    )
    output = run_claude(prompt).strip()
    if not output:
        die("claude returned an empty response on the first translation pass.")

    issues = validate_timecodes(srt_text, output)

    # ---------- Optional fix-up attempts -----------------------------------
    if issues:
        warn(
            f"Timecode validation found {len(issues)} issue(s) in claude's "
            f"first pass. Asking claude to redo with the failing cues "
            f"highlighted…"
        )
        if not TRANSLATE_FIX_PROMPT_TEMPLATE.exists():
            die(
                f"Fix-up prompt template not found at "
                f"{TRANSLATE_FIX_PROMPT_TEMPLATE}.",
                "Make sure the `prompts/` folder hasn't been moved out of "
                "the repo.",
            )
        fix_template = TRANSLATE_FIX_PROMPT_TEMPLATE.read_text(encoding="utf-8")

        for attempt in range(MAX_FIXUP_RETRIES):
            issues_block = "\n".join(f"- {line}" for line in issues)
            fix_prompt = build_translate_fix_prompt(
                fix_template,
                video_id=video_id,
                source_lang=source_name,
                target_lang=target_name,
                src_label=src_label,
                tgt_label=tgt_label,
                original_srt=srt_text,
                broken_translation=output,
                timecode_issues=issues_block,
            )
            info(
                f"fix-up attempt {attempt + 1} of {MAX_FIXUP_RETRIES}…"
            )
            output = run_claude(fix_prompt).strip()
            if not output:
                die(
                    "claude returned an empty response on the fix-up pass.",
                )
            issues = validate_timecodes(srt_text, output)
            if not issues:
                ok(
                    f"timecode validation passed on fix-up attempt "
                    f"{attempt + 1}."
                )
                break
        else:
            # All retries exhausted with issues still present.
            # Persist the broken output for inspection, then die.
            broken_path = Path(
                f"{video_id}.translated."
                f"{slug_lang(source_lang)}-to-{slug_lang(target_lang)}.broken.md"
            )
            broken_path.write_text(output + "\n", encoding="utf-8")
            preview = "\n".join(issues[:10])
            more = (
                f"\n…and {len(issues) - 10} more." if len(issues) > 10 else ""
            )
            die(
                "claude could not produce a timecode-consistent translation "
                f"after {MAX_FIXUP_RETRIES + 1} attempt(s).",
                "First issues:\n"
                f"{preview}{more}\n\n"
                f"The last attempt's output has been saved to "
                f"{broken_path} for inspection.",
            )
    else:
        ok("timecode validation passed on the first pass.")

    # ---------- Write the side-by-side file --------------------------------
    header = (
        f"# Subtitle translation — {video_id}\n\n"
        f"Source: `{srt_path.name}` ({source_name})  ·  "
        f"Target language: **{target_name}**\n\n"
        f"Each block shows the original timecode, the {source_name} line, "
        f"and a {target_name} rendering of the same line.\n\n"
        f"Timecode integrity verified by Python against the source SRT.\n\n"
        f"---\n\n"
    )
    out_path.write_text(header + output + "\n", encoding="utf-8")
    write_sidecar(source_lang_sidecar(video_id), source_lang)
    write_sidecar(target_lang_sidecar(video_id), target_lang)
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


def main() -> int:
    print("=== YouTube downloader + claude translation ===\n")

    info("Preflight checks…")
    check_yt_dlp()
    check_ffmpeg()
    check_claude()

    url = prompt_youtube_url()
    video_id = get_video_id(url)
    info(f"video id: {video_id}")

    title = get_video_title(url)
    if title:
        info(f"video title: {title}")

    results_dir = find_or_create_results_dir(video_id, title)
    info(f"results folder: {results_dir.resolve()}")
    os.chdir(results_dir)

    # ----- Idempotency gate -------------------------------------------------
    existing_mp4, existing_srts = find_existing_artifacts(video_id)
    skip_downloads = False
    if existing_mp4 is not None:
        action = prompt_existing_files_action(existing_mp4, existing_srts)
        if action == "proceed":
            skip_downloads = True
            video_path = existing_mp4
        else:  # "redownload"
            wipe_downloads(existing_mp4, existing_srts)

    # ----- Download phase ---------------------------------------------------
    if not skip_downloads:
        metadata = get_video_metadata(url)
        heights = available_video_heights(metadata)
        prev_quality = read_quality(video_id)
        chosen_height = prompt_quality(heights, prev_quality)
        write_sidecar(quality_sidecar(video_id), str(chosen_height))

        video_path = download_video(url, video_id, max_height=chosen_height)

        sub_langs_raw = available_subtitle_langs(metadata)
        sub_langs = normalize_subtitle_langs(sub_langs_raw)
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
