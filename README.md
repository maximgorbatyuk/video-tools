# video-tools

A small collection of command-line tools for turning videos into text artifacts
on a local Apple Silicon Mac — no cloud transcription, no third-party API
billing for the heavy lifting.

The intended workflow: take a YouTube URL, end up with a clean transcript (or
a side-by-side translation) on disk that you can then paste into LM Studio, a
local LLM, or any other downstream tool you like.

> **Platform:** macOS on Apple Silicon (M-series). The transcription path
> depends on `mlx_whisper`, which requires the MLX runtime and will not run
> on Intel Macs, Linux, or Windows.

---

## Scripts

| Script | Purpose |
|---|---|
| [`transcribe.py`](./transcribe.py) | Interactive orchestrator. Downloads a YouTube video at 720p, then either (a) transcribes the audio locally with `mlx_whisper` (turbo model) or (b) fetches YouTube's English subtitles and translates them via the `claude` CLI into a target language, preserving timecodes. Produces SRT, plain text, a paragraph-grouped reading copy, and — for the translation path — a side-by-side `EN ↔ target` Markdown file. |

### Reference docs

| File | What it is |
|---|---|
| [`transcribe_instruction.md`](./docs/transcribe_instruction.md) | Reference notes on installing `mlx-whisper`, available Whisper models, and the underlying `mlx_whisper` CLI parameters. Hand-written before `transcribe.py` existed; useful as an appendix when you want to call `mlx_whisper` directly or pick a different model. |
| [`AGENTS.md`](./AGENTS.md) | Rules for AI agents working in this repository. Read this if you're an agent (or want to know what they're expected to do). |

---

## Prerequisites (host machine)

All four binaries below must be on `PATH`. `transcribe.py` performs a preflight
check and bails out with an install hint if any are missing.

```bash
# Homebrew packages
brew install yt-dlp ffmpeg pipx

# mlx-whisper is not a Homebrew formula — install via pipx
pipx ensurepath
pipx install mlx-whisper

# Claude Code CLI (provides the `claude` binary used by the translation path)
npm install -g @anthropic-ai/claude-code
claude login
```

After installing pipx-managed or npm-global tools, restart your terminal so
`PATH` picks them up.

The Whisper turbo model (~1.5 GB) is downloaded on first transcription run
and cached at `~/.cache/huggingface/hub/`.

---

## Quick start

```bash
cd /path/where/you/want/the/output/files
python3 /path/to/video-tools/transcribe.py
```

The script is fully interactive:

1. Paste a YouTube URL when prompted.
2. The video is downloaded at 720p (`<ID>.mp4`).
3. If YouTube has English subtitles, you're asked whether to:
   - `[t]ranscribe with mlx_whisper` — process the audio locally, or
   - `[s]ubtitles → translate with claude` — translate the YouTube subs into
     a target language of your choice.
4. Pick a language (Whisper takes an ISO-639-1 code or `auto`; the translation
   path takes any string, e.g. `Russian`, `ru`, `Brazilian Portuguese`).
5. Wait.

If no English subtitles are available the script silently falls back to the
transcription path.

## Output files

All output files are named by the canonical 11-character YouTube video ID, so
multiple videos coexist safely in the same working directory.

| File | When written |
|---|---|
| `<ID>.mp4` | Video download (720p) |
| `<ID>.en.srt` | YouTube English subtitles (translation path only) |
| `<ID>.srt`, `<ID>.txt`, `<ID>.vtt`, `<ID>.json`, `<ID>.tsv` | mlx_whisper outputs (transcription path) |
| `<ID>.dialogue.txt` | Paragraph-grouped reading copy split on silence gaps |
| `<ID>.lang.txt` | Sidecar: language used for the last whisper run |
| `<ID>.translated.<lang-slug>.md` | Side-by-side EN ↔ target Markdown (translation path) |
| `<ID>.translate-lang.txt` | Sidecar: target language used for the last translation run |

Re-running the script on the same URL is safe — heavy steps (video download,
transcription, translation) are skipped or gated behind a `[s]kip / [r]e-run /
[c]hange` prompt.

## License

See [`LICENSE`](./LICENSE).
