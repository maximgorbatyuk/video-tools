# video-tools

A small collection of command-line tools for turning videos into text artifacts
on a local Apple Silicon Mac — no cloud transcription, no third-party API
billing for the heavy lifting.

The intended workflow: take a YouTube URL, end up with a clean transcript (or
a translated `.srt` subtitle file) on disk that you can then paste into LM
Studio, a local LLM, or any other downstream tool you like.

> **Platform:** macOS on Apple Silicon (M-series). The transcription path
> depends on `mlx_whisper`, which requires the MLX runtime and will not run
> on Intel Macs, Linux, or Windows. The translation path is platform-neutral
> but still expects `yt-dlp`, `ffmpeg`, and the `claude` CLI on `PATH`.

---

## Repository layout

```
video-tools/
├── start.py                       ← interactive entrypoint (run this)
├── scripts/
│   ├── transcribe.py              ← functional script: transcribe with mlx_whisper (+ optional claude summary)
│   ├── translate.py               ← functional script: download video + all subs, optionally translate via claude
│   └── _common.py                 ← shared helpers (not user-facing)
├── prompts/
│   ├── summary_prompt.md          ← used by transcribe.py's summary step
│   └── translate_prompt.md        ← used by translate.py (numbered tab-delimited cue list; Python writes the timecodes)
├── docs/transcribe_instruction.md
├── results/                       ← per-video output folders (gitignored; created on first run)
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
| [`scripts/transcribe.py`](./scripts/transcribe.py) | Downloads a YouTube video at 720p, best-effort fetches its YouTube-hosted subtitles, and transcribes the audio locally with `mlx_whisper` + the `whisper-large-v3-turbo` model. All outputs land inside `results/<YYYY-MM-DD>_<video-slug>/`. At the end the script offers to summarize the transcript with an LLM (you pick `claude` or `opencode` + a model) using the template in [`prompts/summary_prompt.md`](./prompts/summary_prompt.md). | `yt-dlp`, `ffmpeg`, `mlx_whisper`, and — for the optional summary — `claude` **or** `opencode` |
| [`scripts/translate.py`](./scripts/translate.py) | Downloads a YouTube video at a quality you pick (only the resolutions YouTube actually serves are offered) plus every subtitle language the platform advertises. Then optionally translates one of those subtitle files to **English, Russian, or Kazakh**. You choose which LLM CLI (`claude` or `opencode`) and which model (e.g. a GLM model via opencode) runs the translation; the result is written as a plain `.srt` file (`<ID>.translated.<tgt>.srt`). Translation runs in parallel chunks with a cue-count check, an automatic retry, and a resumable cache if anything is left uncovered. All outputs land inside `results/<YYYY-MM-DD>_<video-slug>/`. Re-running on a video whose files are already on disk gates with `[p]roceed / [r]e-download`. | `yt-dlp`, `ffmpeg`, and `claude` **or** `opencode` |

### Reference docs

| File | What it is |
|---|---|
| [`docs/transcribe_instruction.md`](./docs/transcribe_instruction.md) | Reference notes on installing `mlx-whisper`, available Whisper models, and the underlying `mlx_whisper` CLI parameters. Useful as an appendix when you want to call `mlx_whisper` directly or pick a different model. |
| [`AGENTS.md`](./AGENTS.md) | Rules for AI agents working in this repository. Read this if you're an agent (or want to know what they're expected to do). |

---

## Prerequisites (host machine)

Both scripts need `yt-dlp` and `ffmpeg`. `mlx_whisper` is only required for
`transcribe.py`. For the LLM step (always in `translate.py`; opt-in in
`transcribe.py`'s summary) you need **one of** `claude` **or** `opencode` —
you pick which at runtime, so only the one you choose has to be installed.
Each script preflight-checks its own dependencies and bails out with an
install hint if anything is missing.

```bash
# Shared
brew install yt-dlp ffmpeg

# Only needed by scripts/transcribe.py — mlx-whisper isn't a Homebrew formula
brew install pipx
pipx ensurepath
pipx install mlx-whisper

# LLM CLI — install whichever you want to translate/summarize with (or both).
# Claude Code provides the `claude` binary:
npm install -g @anthropic-ai/claude-code
claude login

# opencode gives access to GLM and other non-Claude models:
brew install opencode
opencode auth login
```

After installing pipx-managed or npm-global tools, restart your terminal so
`PATH` picks them up. `opencode` addresses models as `provider/model`
(e.g. `zai-coding-plan/glm-5.1`); run `opencode models` to list them all.

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
  [2] Download video + subtitles, optionally translate to EN/RU/KK (claude or opencode)

Pick 1/2:
```

After picking, the selected functional script takes over and prompts for the
remaining inputs (URL, language, etc.). When it reaches a step that needs an
LLM (translation, or the optional summary), it asks which CLI tool
(`claude` or `opencode`) and which model to use — `opencode` lists all its
models in a numbered menu so you can pick a GLM model, for example. Your
choice is remembered in sidecar files so the next run offers it as the
default.

### Run a script directly

If you already know which one you want, skip the menu:

```bash
python3 /path/to/video-tools/scripts/transcribe.py
python3 /path/to/video-tools/scripts/translate.py
```

Both scripts are independently runnable — `start.py` is just a friendlier
front door.

### Verbose diagnostic logs

Pass `-v` / `--verbose` to any entrypoint to enable dim `[d]` diagnostic
lines on stderr — yt-dlp argv, claude prompt sizes, claude call durations,
mlx_whisper duration, validator cue counts, and the first few
timecode-validation issues. Use it to diagnose hangs or unexpected
failures.

```bash
python3 start.py -v
python3 scripts/translate.py --verbose
python3 scripts/transcribe.py --verbose
```

The env var `VIDEO_TOOLS_VERBOSE=1` is an equivalent way to turn verbose
mode on when launching via `start.py` (the functional scripts themselves
take the flag directly).

---

## Output layout

All output files are named by the canonical 11-character YouTube video ID
(resolved via `yt-dlp --print id`), so multiple videos coexist safely
without colliding.

### `scripts/transcribe.py`

Outputs are grouped per video under `results/<YYYY-MM-DD>_<title-slug>/`.
The date prefix is the date of the *first* run for that video — re-runs
on later days re-use the same folder (the lookup is by video ID, not by
date).

```
<CWD>/results/2026-05-24_some_video_title/
├── <ID>.mp4                 ← 720p video download
├── <ID>.<lang>.srt          ← YouTube-hosted subtitles (best-effort)
├── <ID>.srt                 ← whisper subtitles with timecodes
├── <ID>.txt                 ← whisper plain transcript
├── <ID>.vtt, .json, .tsv    ← extra whisper formats (--output-format all)
├── <ID>.dialogue.txt        ← paragraph-grouped reading copy
├── <ID>.lang.txt            ← sidecar: language used last time
├── <ID>.summary.md          ← (optional) LLM-generated summary
├── <ID>.summary-speaker.txt ← sidecar: speaker name for the summary
├── <ID>.summary-context.txt ← sidecar: speaker context for the summary
├── <ID>.summary-tool.txt    ← sidecar: LLM CLI used for the summary (claude/opencode)
└── <ID>.summary-model.txt   ← sidecar: model used for the summary
```

### `scripts/translate.py`

Outputs are grouped per video under `results/<YYYY-MM-DD>_<title-slug>/`,
the same layout as `transcribe.py`. The date prefix is the date of the
*first* run for that video — re-runs re-use the same folder.

```
<CWD>/results/2026-05-24_some_video_title/
├── <ID>.mp4                            ← video at the chosen quality
├── <ID>.<lang>.srt                     ← one file per advertised subtitle language
├── <ID>.translated.<tgt>.srt           ← translated subtitles, plain .srt ready for any player (e.g. .translated.ru.srt)
├── <ID>.translated.<tgt>.broken.srt    ← (only if a run failed to cover every cue) resumable cache of cues translated so far; re-running fills the gaps, then deletes this file
├── <ID>.video-quality.txt              ← sidecar: last chosen download height
├── <ID>.translate-source-lang.txt      ← sidecar: source language used last time
├── <ID>.translate-target-lang.txt      ← sidecar: target language used last time
├── <ID>.translate-tool.txt             ← sidecar: LLM CLI used last time (claude/opencode)
└── <ID>.translate-model.txt            ← sidecar: model used last time (claude alias or opencode provider/model)
```

### Idempotency

Re-running either script on the same URL is safe. Heavy steps (video
download, subtitle download, transcription, translation, summary) are
skipped or gated behind a `[s]kip / [r]e-run / [c]hange` prompt. The
per-purpose sidecars let each script offer "re-use last choice"
automatically.

`translate.py` additionally prompts up front if a previous run's video
+ subtitles are already on disk — you can `[p]roceed` straight to
translation using the existing files, or `[r]e-download` everything from
scratch in one go. The redownload path preserves sidecars and any
previously-completed `<ID>.translated.<tgt>.srt` files.

If a previous translation failed partway (leaving a
`<ID>.translated.<tgt>.broken.srt`), translation also resumes from it:
the cues already translated are reused and only the missing ones are
sent to claude. If that file already contains every cue, no claude calls
are made — the script just assembles the final `.srt`. The broken file
is deleted once a complete translation is written.

## License

See [`LICENSE`](./LICENSE).
