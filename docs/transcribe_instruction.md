# Transcribing Video Interviews with mlx-whisper

A complete workflow for transcribing video on Apple Silicon Macs using `mlx-whisper` (turbo model), then summarizing the transcript with Gemma in LM Studio.

> **Requirements:** Apple Silicon Mac (M1/M2/M3/M4), macOS 13.5+, Python 3.9+

---

## 1. Apps and CLI Tools to Install

### Required

| Tool | Purpose | Install Command |
|------|---------|----------------|
| **Homebrew** | macOS package manager | `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"` |
| **Python 3.9+** | Runtime for mlx-whisper | `brew install python` |
| **ffmpeg** | Audio extraction from video | `brew install ffmpeg` *(you already have this)* |
| **mlx-whisper** | Whisper transcription on Apple Silicon | See [Section 2](#2-installing-and-launching-mlx-whisper) |

### Already on your machine

- **LM Studio** with Gemma model — for summarizing the transcript after transcription

### Optional but recommended

| Tool | Purpose | Install Command |
|------|---------|----------------|
| **pipx** | Isolated install of Python CLI tools (cleanest option) | `brew install pipx` |

---

## 2. Installing and Launching mlx-whisper

### Option A: Install with pipx (recommended)

Cleanest install — keeps `mlx-whisper` isolated from system Python:

```bash
brew install pipx
pipx ensurepath
pipx install mlx-whisper
```

After installation, restart your terminal so the `mlx_whisper` command becomes available.

### Option B: Install in a virtual environment

If you want full control over the environment:

```bash
# Create a virtual environment
python3 -m venv ~/whisper-env

# Activate it
source ~/whisper-env/bin/activate

# Install mlx-whisper
pip install mlx-whisper
```

> **Note:** With this option, you must run `source ~/whisper-env/bin/activate` in every new terminal session before using `mlx_whisper`.

### Option C: Install globally (simplest, but less clean)

```bash
pip3 install mlx-whisper
```

### Verify installation

```bash
mlx_whisper --help
```

If you see the help text listing all options, you're ready to go.

### About the turbo model

You don't need to download the model manually. The first time you run `mlx_whisper` with `--model mlx-community/whisper-large-v3-turbo`, it will automatically download from HuggingFace and cache it locally in `~/.cache/huggingface/hub/`.

The turbo model is about **1.5 GB** and downloads once.

---

## 3. The Transcription Command

### Step 1: Extract audio from video (optional but faster)

```bash
ffmpeg -i interview.mp4 -ar 16000 -ac 1 -c:a pcm_s16le interview.wav
```

> You can skip this — `mlx-whisper` handles video files directly via ffmpeg. But pre-extracting gives you a clean reusable WAV file and a small speed boost if you re-transcribe.

### Step 2: Transcribe

**Basic command:**

```bash
mlx_whisper interview.wav --model mlx-community/whisper-large-v3-turbo
```

**Full command with all useful options:**

```bash
mlx_whisper interview.wav \
  --model mlx-community/whisper-large-v3-turbo \
  --output-dir ./transcripts \
  --output-format txt \
  --language en \
  --task transcribe \
  --word-timestamps False \
  --verbose True
```

### Parameter Reference

| Parameter | What it does | Available values | Default |
|-----------|--------------|------------------|---------|
| *(first positional arg)* | Path to input audio/video file | Any audio/video path (`.wav`, `.mp3`, `.m4a`, `.mp4`, `.mov`, etc.) | *required* |
| `--model` | Which Whisper model to use | See [model list](#available-models) below | `mlx-community/whisper-tiny` |
| `--output-dir` | Folder for the transcript files | Any directory path | Current directory |
| `--output-format` | Transcript file format | `txt`, `srt`, `vtt`, `json`, `tsv`, `all` | `srt` |
| `--language` | Source language of the audio | `en`, `ru`, `es`, `fr`, `de`, `it`, `ja`, `zh`, `ko`, `pt`, `ar`, `tr`, `pl`, `nl`, `uk`, `kk`, `hi`, plus ~80 more (ISO 639-1 codes), or `None` for auto-detect | Auto-detect |
| `--task` | What to do with the audio | `transcribe` (in source language) or `translate` (to English) | `transcribe` |
| `--word-timestamps` | Add per-word timing data | `True`, `False` | `False` |
| `--verbose` | Print progress and partial output | `True`, `False` | `True` |
| `--temperature` | Sampling randomness (higher = more varied output) | `0.0` to `1.0` | `0.0` |
| `--initial-prompt` | Hint about content (helps with names, jargon) | Any text string in quotes | None |
| `--condition-on-previous-text` | Use prior segments as context | `True`, `False` | `True` |
| `--no-speech-threshold` | Threshold to skip silent segments | `0.0` to `1.0` | `0.6` |
| `--hallucination-silence-threshold` | Skip suspected hallucinations in silence | Seconds (e.g., `2.0`), or omit | None |

### Output format explained

- **`txt`** — Plain text, no timestamps. Best for feeding into Gemma.
- **`srt`** — SubRip subtitles with timestamps. Good for video subtitles.
- **`vtt`** — WebVTT subtitles. Web-friendly format.
- **`json`** — Full machine-readable output with timestamps, confidence scores, segment data. Best if you want to script further processing.
- **`tsv`** — Tab-separated values with timestamps. Easy to open in spreadsheets.
- **`all`** — Produces all of the above at once.

### Available models

All on the `mlx-community` HuggingFace organization. Listed from fastest to most accurate:

| Model ID | Size | Speed | Accuracy | Notes |
|----------|------|-------|----------|-------|
| `mlx-community/whisper-tiny-mlx` | ~75 MB | Fastest | Lowest | Drafts only |
| `mlx-community/whisper-base-mlx` | ~140 MB | Very fast | Low | Quick previews |
| `mlx-community/whisper-small-mlx` | ~460 MB | Fast | Decent | Clean audio |
| `mlx-community/whisper-medium-mlx` | ~1.5 GB | Moderate | Good | Solid all-rounder |
| `mlx-community/whisper-large-v3-turbo` | ~1.5 GB | Fast | Very good | **Recommended** — best speed/quality balance |
| `mlx-community/whisper-large-v3-mlx` | ~3 GB | Slower | Best | Highest accuracy |

Quantized variants (smaller, less RAM, minor quality drop):

- `mlx-community/whisper-large-v3-turbo-q4` — 4-bit quantized
- `mlx-community/whisper-large-v3-mlx-4bit` — 4-bit quantized large-v3

### Example workflows

**Quick transcription, English interview, plain text:**

```bash
mlx_whisper interview.mp4 \
  --model mlx-community/whisper-large-v3-turbo \
  --language en \
  --output-format txt
```

**Generate subtitles (SRT) with word-level timing:**

```bash
mlx_whisper interview.mp4 \
  --model mlx-community/whisper-large-v3-turbo \
  --language en \
  --output-format srt \
  --word-timestamps True
```

**Translate a non-English interview into English text:**

```bash
mlx_whisper interview.mp4 \
  --model mlx-community/whisper-large-v3-turbo \
  --task translate \
  --output-format txt
```

**Help Whisper with names and jargon (use `--initial-prompt`):**

```bash
mlx_whisper interview.mp4 \
  --model mlx-community/whisper-large-v3-turbo \
  --initial-prompt "Interview with Dr. Anya Petrova about CRISPR and gene editing."
```

---

## 4. Full End-to-End Workflow

```bash
# 1. Extract audio (optional)
ffmpeg -i interview.mp4 -ar 16000 -ac 1 -c:a pcm_s16le interview.wav

# 2. Transcribe to plain text
mlx_whisper interview.wav \
  --model mlx-community/whisper-large-v3-turbo \
  --language en \
  --output-format txt \
  --output-dir ./transcripts

# 3. Open the resulting interview.txt in your editor
open ./transcripts/interview.txt

# 4. Copy the text into LM Studio's chat with Gemma and prompt:
#    "Here is an interview transcript. Summarize the key points,
#     list the main themes, and pull out 3-5 notable quotes."
```

---

## 5. Troubleshooting

| Problem | Fix |
|---------|-----|
| `mlx_whisper: command not found` | Run `pipx ensurepath` and restart your terminal, or re-activate the venv with `source ~/whisper-env/bin/activate` |
| Slow first run | First run downloads the model (~1.5 GB). Subsequent runs use the cache and are much faster. |
| Wrong language detected | Set `--language` explicitly (e.g., `--language en`) |
| Gibberish in silent parts | Add `--hallucination-silence-threshold 2.0` |
| Out of memory | Switch to a smaller model or a quantized variant (e.g., `whisper-large-v3-turbo-q4`) |
| Names misspelled | Use `--initial-prompt` with the correct spellings |