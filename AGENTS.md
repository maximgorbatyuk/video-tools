# AGENTS.md

Rules for AI agents (and humans acting like them) working in this repository.
If you are continuing or extending the tooling here, read this file first.

---

## 1. What this repo is

A small collection of single-file command-line tools that turn a video (today:
a YouTube URL) into local text artifacts — transcripts, subtitles, side-by-side
translations. The end user is comfortable on the command line but isn't writing
the code, so the tools must be self-explanatory, idempotent, and safe to re-run.

The high-level project overview, scripts, and quick-start instructions live in
[`README.md`](./README.md). Treat that file as the user-facing manual and this
file as the agent-facing rulebook.

---

## 2. How to start

1. **Read `README.md` first** to understand the user-facing surface (what the
   tools are, what they output, what the user expects).
2. **Read the top-of-file docstring** of any script you're about to touch.
   Each script in this repo is expected to carry a complete docstring
   covering platform, prerequisites, usage, outputs, idempotency rules, and
   known limitations.
3. **Read this file's § 4 ("Non-functional requirements")** before writing
   any code. These rules apply to *every* script in the repo.
4. **Before writing any code**, summarize in 3–5 bullet points what you will
   change and what you will not change. Then proceed.
5. When in doubt about a UI/CLI element the user referenced ("the prompt",
   "the menu"), confirm the exact function or section before changing it.

---

## 3. Required host CLI dependencies

These must be installed on the user's macOS Apple Silicon machine. Every
script that depends on one of these MUST preflight-check for it on `PATH` and
bail out with a copy-paste-able install hint if it's missing.

| Tool | Used by | Install |
|---|---|---|
| `yt-dlp` | `transcribe.py` (video + subtitle download) | `brew install yt-dlp` |
| `ffmpeg` | `transcribe.py` (yt-dlp merges 720p video+audio via ffmpeg) | `brew install ffmpeg` |
| `mlx_whisper` | `transcribe.py` (local audio transcription) | `brew install pipx && pipx ensurepath && pipx install mlx-whisper` |
| `claude` | `transcribe.py` (translation path) | `npm install -g @anthropic-ai/claude-code && claude login` |

Notes:
- `mlx-whisper` is **not** a Homebrew formula. Install via `pipx`. The binary
  is named `mlx_whisper` (underscore).
- The Whisper turbo model (~1.5 GB) auto-downloads to
  `~/.cache/huggingface/hub/` on first transcription. A missing cache is a
  warning, not a fatal error.
- After installing pipx-managed or npm-global tools the user must open a new
  terminal so `PATH` picks them up. Mention this in install hints.

When a new script introduces a new dependency, add a row to this table in the
same edit.

---

## 4. Non-functional requirements (apply to every script)

Every script in this repo must follow these rules. If you're adding a new
script, design for these from the start; don't bolt them on later.

### 4.1 Idempotency

Re-running a script on the same input must be safe and cheap. Specifically:

- Skip network downloads if the target file already exists on disk.
- For heavy local steps (transcription, translation, model invocations) that
  produce a primary output, detect the existing output and present the user
  with `[s]kip / [r]e-run / [c]hange` (or equivalent), never silently
  overwriting.
- Use **sidecar files** (small plain-text files alongside the main output)
  to remember per-run choices (language, target language, model). This lets
  the script offer "re-use last choice" without making the user remember.

### 4.2 Preflight checks

At startup, every script verifies its required CLI dependencies are on
`PATH` (see § 3). On failure, exit with a non-zero code and print a
copy-paste-able install command. Missing optional caches (e.g., HuggingFace
model weights) should warn, not fail.

### 4.3 Platform guardrails

This repo targets **macOS on Apple Silicon** (M-series). Scripts that rely
on `mlx_whisper` or other Apple-Silicon-only dependencies should either:
- Check `platform.machine() == "arm64"` up front, or
- Document the platform constraint prominently in their docstring and let
  the dependency check fail with a clear hint.

Don't suggest Linux-only commands in error messages or install hints.

### 4.4 Deterministic, namespaced output filenames

Outputs must be named by a stable identifier derived from the input (for
`transcribe.py`: the canonical 11-character YouTube video ID, resolved via
`yt-dlp --print id`). This lets multiple inputs coexist in the same working
directory without collisions.

Use suffixes to disambiguate purposes:
- Primary outputs: `<ID>.<ext>` (e.g. `<ID>.srt`, `<ID>.mp4`)
- Per-language outputs: `<ID>.translated.<lang-slug>.md`
- Sidecars: `<ID>.<purpose>.txt` (e.g. `<ID>.lang.txt`,
  `<ID>.translate-lang.txt`)

### 4.5 Stdlib-only Python

Scripts are written in Python 3 and must use **only the standard library**
plus `subprocess` calls to the host CLI tools listed in § 3. No `pip install`
step for the user, no requirements.txt, no virtualenv. If you find yourself
reaching for a third-party Python package, first see if shelling out to an
existing host CLI achieves the same thing.

### 4.6 Interactive UX defaults

Default to interactive prompts (clear questions, `[s]/[r]/[c]`-style menus,
sensible defaults shown in brackets). Non-interactive / CLI-flag-driven
modes are welcome additions but should be opt-in, not the default — the
target user runs these tools by hand.

Use color-coded log helpers (info / ok / warn / err) for readable terminal
output. Don't add emoji unless the user explicitly asks for it.

### 4.7 No external state

A script must not depend on global config, environment variables, or hidden
state outside its working directory and the user's standard caches
(`~/.cache/huggingface/`, npm/pipx install dirs). All per-run state lives in
sidecar files next to the outputs.

### 4.8 Privacy / copyright

Test outputs generated against real third-party videos (the `.mp4`, `.srt`,
`.txt`, `.json`, `.tsv`, `.vtt`, `.dialogue.txt`, `.translated.*.md`,
`.lang.txt`, `.translate-lang.txt`, and `.summary.md` files named by an 11-
character YouTube ID) are **not** committed to the repo. The current
`.gitignore` already excludes the common extensions. When adding new output
file types, extend `.gitignore` in the same change.

### 4.9 Self-documenting scripts

Every script begins with a top-level docstring covering:
- one-line purpose,
- platform,
- prerequisites (with install commands),
- usage,
- outputs (filename → meaning table),
- idempotency rules,
- known limitations.

If you change behavior, update the docstring in the same edit.

---

## 5. Documentation upkeep (meta-rule)

When you add a new script, or modify an existing one such that its functional
requirements, outputs, prompts, or host dependencies change, you MUST update
the project documentation in the **same change**:

- [`README.md`](./README.md):
  - Add or update the row in the **Scripts** table.
  - Update the **Prerequisites** section if a new host CLI is required.
  - Update the **Output files** table if filenames or sidecars change.
  - Update the **Quick start** flow if the user-visible interaction changes.

- [`AGENTS.md`](./AGENTS.md) (this file):
  - Update § 3 (host CLI dependencies) if a new tool was added.
  - Update § 4 (non-functional requirements) if the change establishes a new
    convention worth applying to future scripts.
  - Update § 6 (per-script notes) with anything script-specific.

- The script's own top-of-file docstring (see § 4.9).

Docs that drift behind the code are worse than no docs. Treat doc updates as
part of the implementation, not a follow-up.

---

## 6. Per-script notes

### `transcribe.py`

- Two execution paths gated by a `[t]ranscribe / [s]ubtitles → translate` menu:
  - **Transcribe path** → `mlx_whisper` with `whisper-large-v3-turbo`,
    `--output-format all`. Post-processes the SRT into
    `<ID>.dialogue.txt` (paragraphs split on silence gaps controlled by
    the `PARAGRAPH_GAP_SECONDS` constant).
  - **Translate path** → fetch YouTube English subs via `yt-dlp
    --skip-download`, build a deterministic prompt (see
    `build_translate_prompt`), call `claude -p`, validate cue count, write
    `<ID>.translated.<slug>.md` with a header.
- Silent fall-through to the transcribe path if no English subtitles are
  available on YouTube — this is an explicit design decision, do not add a
  prompt without re-opening the discussion.
- No speaker diarization. `dialogue.txt` paragraphs are split on silence,
  not speaker turns. If diarization becomes a requirement, the agreed-upon
  path is to layer `pyannote-audio` on top of mlx_whisper.
- Single Claude call per translation (no chunking). The script warns if the
  cue count in Claude's output is < 80% of the source — treat that as the
  signal to revisit chunking.
- All language-related helpers (`lang_label`, `slug_lang`, `count_cue_blocks`,
  `build_translate_prompt`, `translated_md_path`) are pure functions designed
  for unit-testing. Keep them pure if you change them.

### Adding a new script

Before merging, confirm:
- [ ] Top-of-file docstring covers all nine items in § 4.9.
- [ ] Preflight checks for every host CLI it shells out to.
- [ ] Outputs are namespaced by a stable ID (§ 4.4).
- [ ] Re-runs are idempotent or gated by a `[s]/[r]/[c]` prompt (§ 4.1).
- [ ] `README.md` Scripts table updated.
- [ ] `AGENTS.md` § 3 and § 6 updated.
- [ ] `.gitignore` covers any new generated output extensions.

---

## 7. Verification before claiming done

Before reporting a change as complete:

1. `python3 -m py_compile <script>.py` — syntax check.
2. Dry-run the preflight section in an environment where one of the
   dependencies is hidden (e.g. `PATH=/usr/bin python3 transcribe.py`) and
   confirm the install hint fires.
3. For new pure functions, run a small inline unit test:
   ```bash
   python3 - <<'PY'
   from transcribe import slug_lang, lang_label
   assert slug_lang("Brazilian Portuguese") == "brazilian-portuguese"
   assert lang_label("Russian") == "RU"
   print("ok")
   PY
   ```
4. If the script has an existing test suite (none today), run it. Don't
   report completion until the full suite passes.

---

## 8. Style and conventions

- Conventional commit format (`feat:`, `fix:`, `chore:`, `docs:`, `test:`).
- Do not mention yourself or add yourself as a co-author in commits.
- The user is on macOS with `zsh`; don't suggest Linux-only commands.
- When referencing functions or pieces of code in chat, use the
  `file_path:line_number` pattern.
- Keep messages to the user short and direct; no trailing summaries unless
  asked.
