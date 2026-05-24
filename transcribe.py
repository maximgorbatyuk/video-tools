#!/usr/bin/env python3
"""
transcribe.py
=============

Interactive command-line tool that downloads a YouTube video at 720p and
transcribes its audio into subtitles + plain text + a paragraph-grouped
reading copy, using mlx_whisper (the Apple Silicon / MLX port of OpenAI
Whisper) with the `whisper-large-v3-turbo` model.

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
    mlx_whisper   — Whisper on MLX           →  brew install pipx
                    (NOT a Homebrew formula)     pipx ensurepath
                                                 pipx install mlx-whisper

The turbo model (~1.5 GB) is downloaded automatically on first use and
cached at  ~/.cache/huggingface/hub/  for subsequent runs.

------------------------------------------------------------------------
Usage
------------------------------------------------------------------------
    python3 transcribe.py

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
      if you need a different resolution.
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
# Preflight checks
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


def check_claude() -> None:
    """Verify the `claude` CLI is on PATH (used by the translation flow)."""
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
# YouTube interaction
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
    """Use yt-dlp itself to canonicalize the video ID."""
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


def download_subtitles(url: str, video_id: str) -> Optional[Path]:
    """
    Try to fetch English subtitles for the video via yt-dlp.

    Returns the Path to <video_id>.en.srt if successful, else None.
    Idempotent: skips the network call if the file already exists.
    yt-dlp prefers manual (creator-uploaded) subs and falls back to
    auto-generated; both land at the same filename.
    """
    out_path = Path(f"{video_id}.en.srt")
    if out_path.exists():
        ok(f"English subtitles already present: {out_path}  (skipping fetch)")
        return out_path

    info(f"fetching English subtitles for {video_id}…")
    cmd = [
        "yt-dlp",
        "--skip-download",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs", "en.*,en",
        "--sub-format", "srt/best",
        "--convert-subs", "srt",
        "-o", f"{video_id}.%(ext)s",
        url,
    ]
    try:
        # Don't `check=True` — a missing-subs failure shouldn't kill the
        # whole script. We just want to know if we got a file or not.
        subprocess.run(cmd, check=False, capture_output=True, text=True)
    except Exception as e:  # pragma: no cover — extremely unlikely
        warn(f"yt-dlp subtitles fetch raised an exception: {e}")
        return None

    # yt-dlp may write `<ID>.en.srt`, `<ID>.en-orig.srt`, `<ID>.en-US.srt`,
    # etc., depending on what's available. Pick the first match.
    candidates = sorted(Path(".").glob(f"{video_id}.en*.srt"))
    if not candidates:
        warn(
            f"No English subtitles available on YouTube for {video_id}.\n"
            f"    Falling back to mlx_whisper transcription."
        )
        return None

    chosen = candidates[0]
    if chosen != out_path:
        # Normalize the filename to <ID>.en.srt for the rest of the script.
        chosen.rename(out_path)
    ok(f"saved English subtitles: {out_path}")
    return out_path


# ----------------------------------------------------------------------------
# Transcription
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
# Action menu (transcribe vs translate)
# ----------------------------------------------------------------------------

def prompt_action(has_subs: bool) -> str:
    """Returns 'transcribe' or 'translate'."""
    if not has_subs:
        # Silent fall-through per the spec: no menu, just go to whisper.
        info("No English subtitles → falling back to mlx_whisper transcription.")
        return "transcribe"

    print()
    info("English subtitles are available for this video.")
    while True:
        choice = input(
            "What now? "
            "[t]ranscribe with mlx_whisper / "
            "[s]ubtitles → translate with claude: "
        ).strip().lower()
        if choice in ("t", "transcribe", "whisper"):
            return "transcribe"
        if choice in ("s", "subs", "subtitles", "translate", "claude"):
            return "translate"
        warn("Please enter t or s.")


# ----------------------------------------------------------------------------
# Claude CLI translation path
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


def lang_label(target_lang: str) -> str:
    """Short 2-letter-ish label for the side-by-side output (e.g. 'RU')."""
    s = target_lang.strip().lower()
    if s in _LANG_LABELS:
        return _LANG_LABELS[s]
    if len(s) >= 2:
        return s[:2].upper()
    return "TR"  # generic 'translation'


def slug_lang(target_lang: str) -> str:
    """Filename-safe slug, e.g. 'Russian' -> 'russian', 'ru' -> 'ru'."""
    s = re.sub(r"[^a-z0-9]+", "-", target_lang.strip().lower()).strip("-")
    return s or "translated"


def translate_lang_sidecar_path(video_id: str) -> Path:
    return Path(f"{video_id}.translate-lang.txt")


def read_previous_translate_lang(video_id: str) -> Optional[str]:
    p = translate_lang_sidecar_path(video_id)
    if not p.exists():
        return None
    val = p.read_text(encoding="utf-8").strip()
    return val or None


def write_translate_lang_sidecar(video_id: str, target_lang: str) -> None:
    translate_lang_sidecar_path(video_id).write_text(
        target_lang.strip() + "\n", encoding="utf-8"
    )


def translated_md_path(video_id: str, target_lang: str) -> Path:
    return Path(f"{video_id}.translated.{slug_lang(target_lang)}.md")


def prompt_target_language(prompt_label: str = "Target language") -> str:
    """Free-text language prompt. Accepts ISO codes ('ru') or names ('Russian')."""
    while True:
        raw = input(
            f"\n{prompt_label} (e.g. 'Russian', 'ru', 'Spanish'): "
        ).strip()
        if raw:
            return raw
        warn("Please enter a language.")


def prompt_retranslate(video_id: str, target_lang: str) -> str:
    """Returns 'skip', 'same', or 'change'."""
    out = translated_md_path(video_id, target_lang)
    print()
    info(f"translation already exists: {out}")
    while True:
        choice = input(
            "What now? "
            "[s]kip / "
            "[r]e-run with same language ({}) / "
            "[c]hange language and re-run: ".format(target_lang)
        ).strip().lower()
        if choice in ("s", "skip"):
            return "skip"
        if choice in ("r", "rerun", "re-run", "same"):
            return "same"
        if choice in ("c", "change"):
            return "change"
        warn("Please enter s, r, or c.")


def build_translate_prompt(target_lang: str, label: str, srt_text: str) -> str:
    """Construct the prompt we hand to `claude -p`."""
    return f"""You are translating English subtitles into {target_lang}.

Input: an SRT subtitle file.

For each cue in the input, output a block in this EXACT format:

[HH:MM:SS,mmm --> HH:MM:SS,mmm]
EN: <the original English line, verbatim>
{label}: <a natural, fluent translation into {target_lang}>

Rules:
- Preserve every timecode exactly as it appears in the SRT.
- Preserve the order of cues.
- Translate naturally — not word-for-word. Keep proper nouns,
  technical terms, brand names, and code in English when appropriate.
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


def count_cue_blocks(text: str) -> int:
    """Count blocks of the form [HH:MM:SS,mmm --> HH:MM:SS,mmm]."""
    return len(SRT_TIMECODE_RE.findall(text))


def translate_subtitles(srt_path: Path, video_id: str) -> None:
    # --- pick target language, honoring sidecar if it exists ---
    previous = read_previous_translate_lang(video_id)
    if previous:
        info(f"previous translation target was: {previous}")
        same = input(
            f"Re-use '{previous}'? [Y/n]: "
        ).strip().lower()
        if same in ("", "y", "yes"):
            target_lang = previous
        else:
            target_lang = prompt_target_language()
    else:
        target_lang = prompt_target_language()

    # --- idempotency check on the per-language output file ---
    out_path = translated_md_path(video_id, target_lang)
    if out_path.exists():
        choice = prompt_retranslate(video_id, target_lang)
        if choice == "skip":
            ok("Skipping translation. Done.")
            return
        if choice == "change":
            target_lang = prompt_target_language("New target language")
            out_path = translated_md_path(video_id, target_lang)

    # --- read SRT, build prompt, call claude ---
    srt_text = srt_path.read_text(encoding="utf-8").strip()
    if not srt_text:
        die(f"{srt_path} is empty — nothing to translate.")

    label = lang_label(target_lang)
    prompt = build_translate_prompt(target_lang, label, srt_text)

    output = run_claude(prompt).strip()
    if not output:
        die("claude returned an empty response.")

    # --- light validation: cue count should roughly match ---
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

    # --- write the side-by-side markdown file with a small header ---
    header = (
        f"# Subtitle translation — {video_id}\n\n"
        f"Source: `{srt_path.name}`  ·  Target language: **{target_lang}**\n\n"
        f"Each block shows the original timecode, the English line, "
        f"and a {target_lang} rendering of the same line.\n\n"
        f"---\n\n"
    )
    out_path.write_text(header + output + "\n", encoding="utf-8")
    write_translate_lang_sidecar(video_id, target_lang)
    ok(f"wrote {out_path}")


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def do_transcribe(video_path: Path, video_id: str) -> None:
    """The mlx_whisper path (factored out of main())."""
    if transcripts_exist(video_id):
        previous_lang = read_previous_language(video_id)
        choice = prompt_retranscribe(video_id, previous_lang)
        if choice == "skip":
            ok("Skipping transcription. Done.")
            return
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


def do_translate(srt_path: Path, video_id: str) -> None:
    translate_subtitles(srt_path, video_id)
    print()
    ok("Translation done.")
    print(f"  • {video_id}.mp4                       — 720p download")
    print(f"  • {video_id}.en.srt                    — English subtitles (from YouTube)")
    print(f"  • {video_id}.translated.<lang>.md      — side-by-side EN ↔ target")


def main() -> int:
    print("=== YouTube → mlx_whisper / claude translation ===\n")

    info("Preflight checks…")
    check_yt_dlp()
    check_ffmpeg()
    check_mlx_whisper()
    check_claude()
    check_turbo_model()

    url = prompt_youtube_url()
    video_id = get_video_id(url)
    info(f"video id: {video_id}")

    video_path = download_video(url, video_id)
    srt_path = download_subtitles(url, video_id)

    action = prompt_action(has_subs=srt_path is not None)
    if action == "transcribe":
        do_transcribe(video_path, video_id)
    else:
        assert srt_path is not None  # prompt_action guarantees this
        do_translate(srt_path, video_id)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        warn("Interrupted.")
        sys.exit(130)
