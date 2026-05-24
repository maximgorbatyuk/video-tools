# video-tools

A small collection of command-line tools for turning videos into text artifacts
on a local Apple Silicon Mac — no cloud transcription, no third-party API
billing for the heavy lifting.

The intended workflow: take a YouTube URL, end up with a clean transcript (or
a side-by-side translation) on disk that you can then paste into LM Studio, a
local LLM, or any other downstream tool you like.

> **Platform:** macOS on Apple Silicon (M-series). The transcription path
> depends on `mlx_whisper`, which requires the MLX runtime and will not run
> on Intel Macs, Linux, or Windows. The translation path is platform-neutral
> but still expects `yt-dlp` and the `claude` CLI on `PATH`.

---

## Repository layout

```
video-tools/
├── start.py                 ← interactive entrypoint (run this)
├── scripts/
│   ├── transcribe.py        ← functional script: transcribe with mlx_whisper
│   ├── translate.py         ← functional script: download subs + translate via claude
│   └── _common.py           ← shared helpers (not user-facing)
├── docs/transcribe_instruction.md
├── README.md
├── AGENTS.md
└── LICENSE
```

`start.py` is just a friendly menu. The two scripts under `scripts/` do the
real work and can each be invoked directly when you already know which one
you want.

## Scripts

### Entrypoint

| Script | Purpose |
|---|---|
| [`start.py`](./start.py) | Interactive menu. Asks which functional script to run, then dispatches to it as a subprocess. Run this if you're not sure where to start. |

### Functional scripts

| Script | Purpose | Host CLI deps |
|---|---|---|
| [`scripts/transcribe.py`](./scripts/transcribe.py) | Downloads a YouTube video at 720p and transcribes its audio locally using `mlx_whisper` with the `whisper-large-v3-turbo` model. Produces SRT, plain text, and a paragraph-grouped reading copy split on silence gaps. | `yt-dlp`, `ffmpeg`, `mlx_whisper` |
| [`scripts/translate.py`](./scripts/translate.py) | Downloads a YouTube video at 720p plus subtitles in a user-chosen source language, then translates those subtitles to **English or Russian** via the `claude` CLI. Preserves every timecode and the meaning of each sentence; writes a side-by-side Markdown file. | `yt-dlp`, `ffmpeg`, `claude` |

### Reference docs

| File | What it is |
|---|---|
| [`docs/transcribe_instruction.md`](./docs/transcribe_instruction.md) | Reference notes on installing `mlx-whisper`, available Whisper models, and the underlying `mlx_whisper` CLI parameters. Useful as an appendix when you want to call `mlx_whisper` directly or pick a different model. |
| [`AGENTS.md`](./AGENTS.md) | Rules for AI agents working in this repository. Read this if you're an agent (or want to know what they're expected to do). |

---

## Prerequisites (host machine)

The two scripts share `yt-dlp` and `ffmpeg`; `mlx_whisper` is only required
for `transcribe.py`, and `claude` is only required for `translate.py`. Each
script preflight-checks its own dependencies and bails out with an install
hint if anything is missing.

```bash
# Shared
brew install yt-dlp ffmpeg

# Only needed by scripts/transcribe.py — mlx-whisper isn't a Homebrew formula
brew install pipx
pipx ensurepath
pipx install mlx-whisper

# Only needed by scripts/translate.py — Claude Code CLI provides the `claude` binary
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
python3 /path/to/video-tools/start.py
```

You'll be asked which script to run:

```
=== video-tools ===

What do you want to do?
  [1] Transcribe a YouTube video (mlx_whisper, runs locally)
  [2] Translate YouTube subtitles to English or Russian (claude)

Pick 1/2:
```

After picking, the selected functional script takes over and prompts for the
remaining inputs (URL, language, etc.).

### Run a script directly

If you already know which one you want, skip the menu:

```bash
python3 /path/to/video-tools/scripts/transcribe.py
python3 /path/to/video-tools/scripts/translate.py
```

Both scripts are independently runnable — `start.py` is just a friendlier
front door.

---

## Output files

All output files are named by the canonical 11-character YouTube video ID
(resolved via `yt-dlp --print id`), so multiple videos coexist safely in the
same working directory.

| File | Produced by | Notes |
|---|---|---|
| `<ID>.mp4` | both | 720p video download (idempotent — skipped if present) |
| `<ID>.srt`, `<ID>.txt`, `<ID>.vtt`, `<ID>.json`, `<ID>.tsv` | `transcribe.py` | All five formats from `mlx_whisper --output-format all` |
| `<ID>.dialogue.txt` | `transcribe.py` | Paragraph-grouped reading copy split on silence gaps |
| `<ID>.lang.txt` | `transcribe.py` | Sidecar: language used for the last whisper run |
| `<ID>.<src-lang>.srt` | `translate.py` | Source subtitles from YouTube (e.g. `<ID>.en.srt`, `<ID>.zh.srt`) |
| `<ID>.translated.<src-slug>-to-<target-slug>.md` | `translate.py` | Side-by-side translation, one cue per block (e.g. `<ID>.translated.zh-to-russian.md`) |
| `<ID>.translate-source-lang.txt` | `translate.py` | Sidecar: source language used last time |
| `<ID>.translate-target-lang.txt` | `translate.py` | Sidecar: target language used last time |

Re-running on the same URL is safe — heavy steps (video download,
subtitle download, transcription, translation) are skipped or gated behind
a `[s]kip / [r]e-run / [c]hange` prompt. Per-language sidecars let each
script offer "re-use last choice" automatically.

## License

See [`LICENSE`](./LICENSE).
