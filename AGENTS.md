# AGENTS.md

Rules for AI agents (and humans acting like them) working in this repository.
If you are continuing or extending the tooling here, read this file first.

---

## 1. What this repo is

A small collection of command-line tools that turn a video (today: a YouTube
URL) into local text artifacts — transcripts, subtitles, side-by-side
translations. The end user is comfortable on the command line but isn't
writing the code, so the tools must be self-explanatory, idempotent, and safe
to re-run.

The high-level project overview, scripts, and quick-start instructions live
in [`README.md`](./README.md). Treat that file as the user-facing manual and
this file as the agent-facing rulebook.

---

## 2. Repository structure

```
video-tools/
├── start.py                       ← interactive entrypoint, dispatches to scripts/
├── scripts/
│   ├── transcribe.py              ← functional script (mlx_whisper + optional claude summary)
│   ├── translate.py               ← functional script (claude — download + translate)
│   └── _common.py                 ← shared helpers (NOT a runnable script)
├── prompts/
│   ├── summary_prompt.md          ← consumed by transcribe.py's summary step
│   └── translate_prompt.md        ← consumed by translate.py (numbered tab-
│                                    delimited cue list; timecodes never
│                                    leave Python)
├── docs/transcribe_instruction.md
├── results/                       ← per-video output folders (gitignored; created on first run)
├── README.md
├── AGENTS.md
├── CLAUDE.md
├── LICENSE
└── .gitignore
```

Two rules govern this layout. Follow them when adding new tooling:

1. **`start.py` is the only top-level script.** It does no real work itself —
   it only prints a menu and dispatches to a script under `scripts/` as a
   subprocess (`subprocess.run([sys.executable, str(target)])`). When you add
   a new functional script, register it in the `SCRIPTS` list inside
   `start.py` so it appears in the menu.

2. **Every functional script lives in `scripts/`.** Each one is an
   independent, runnable Python file with its own `main()` and `if __name__
   == "__main__"` block. They must work when invoked directly
   (`python3 scripts/<name>.py`), not only via `start.py`. Shared helpers
   live in `scripts/_common.py`; importing from it is the only allowed form
   of inter-script coupling.

`scripts/_common.py` is a module, not a script. It must remain side-effect-
free at import time, must not call `sys.exit`, `input`, or `print` at module
scope, and must not be registered in `start.py`'s menu.

`prompts/*.md` files are static prompt templates read at runtime by the
functional scripts. They use `{{placeholder}}` markers that scripts
substitute with plain `str.replace()` (no Python format-string semantics),
so user-supplied values are free to contain `{` and `}` without escaping.
Scripts should resolve the prompts directory via a path derived from
`__file__`, not from the current working directory — both functional
scripts `chdir` into the per-video results folder before they ever read
a prompt.

---

## 3. How to start

1. **Read [`README.md`](./README.md) first** to understand the user-facing
   surface.
2. **Read the top-of-file docstring** of any script you're about to touch.
   Each functional script in this repo carries a complete docstring covering
   platform, prerequisites, usage, outputs, idempotency rules, and known
   limitations.
3. **Read § 5 ("Non-functional requirements")** before writing any code.
   These rules apply to *every* script in the repo.
4. **Before writing any code**, summarize in 3–5 bullet points what you will
   change and what you will not change. Then proceed.
5. When in doubt about a UI/CLI element the user referenced ("the prompt",
   "the menu"), confirm the exact function or section before changing it.

---

## 4. Required host CLI dependencies

These must be installed on the user's macOS Apple Silicon machine. Every
functional script that depends on one of these MUST preflight-check for it
on `PATH` and bail out with a copy-paste-able install hint if it's missing.
Shared preflight helpers (`check_yt_dlp`, `check_ffmpeg`) live in
`scripts/_common.py`.

| Tool | Used by | Install |
|---|---|---|
| `yt-dlp` | `scripts/transcribe.py`, `scripts/translate.py` | `brew install yt-dlp` |
| `ffmpeg` | `scripts/transcribe.py`, `scripts/translate.py` | `brew install ffmpeg` |
| `mlx_whisper` | `scripts/transcribe.py` | `brew install pipx && pipx ensurepath && pipx install mlx-whisper` |
| `claude` | `scripts/translate.py` (always); `scripts/transcribe.py` (only if the user opts into the summary step) | `npm install -g @anthropic-ai/claude-code && claude login` |

Notes:
- `mlx-whisper` is **not** a Homebrew formula. Install via `pipx`. The binary
  is named `mlx_whisper` (underscore).
- The Whisper turbo model (~1.5 GB) auto-downloads to
  `~/.cache/huggingface/hub/` on first transcription. A missing cache is a
  warning, not a fatal error.
- After installing pipx-managed or npm-global tools the user must open a new
  terminal so `PATH` picks them up. Mention this in install hints.

When a new script introduces a new dependency, add a row to this table in
the same edit. When a script no longer needs a dependency, drop it from the
"Used by" column.

---

## 5. Non-functional requirements (apply to every script)

Every script in this repo must follow these rules. If you're adding a new
script, design for these from the start; don't bolt them on later.

### 5.1 Idempotency

Re-running a script on the same input must be safe and cheap. Specifically:

- Skip network downloads if the target file already exists on disk.
- For heavy local steps (transcription, translation, model invocations) that
  produce a primary output, detect the existing output and present the user
  with `[s]kip / [r]e-run / [c]hange` (or equivalent), never silently
  overwriting.
- When a script produces a *set* of related downloads that should be
  refreshed together (e.g. translate.py: one mp4 plus N per-language SRT
  files), gate the entire phase up front with a `[p]roceed / [r]e-download`
  prompt that lists what's already on disk. The `[r]edownload` path deletes
  the bulky artifacts but preserves the small sidecars so the user's
  previous choices are still offered as defaults.
- Use **sidecar files** (small plain-text files alongside the main output)
  to remember per-run choices (language, target language, model, video
  quality). This lets the script offer "re-use last choice" without making
  the user remember.

### 5.2 Preflight checks

At startup, every functional script verifies its required CLI dependencies
are on `PATH` (see § 4). On failure, exit with a non-zero code and print a
copy-paste-able install command. Missing optional caches (e.g., HuggingFace
model weights) should warn, not fail. `start.py` itself does not preflight
host CLI deps — that lives in the functional scripts so they remain
standalone-runnable.

### 5.3 Platform guardrails

This repo targets **macOS on Apple Silicon** (M-series). Scripts that rely
on `mlx_whisper` or other Apple-Silicon-only dependencies should either:
- Check `platform.machine() == "arm64"` up front, or
- Document the platform constraint prominently in their docstring and let
  the dependency check fail with a clear hint.

Don't suggest Linux-only commands in error messages or install hints.

### 5.4 Deterministic, namespaced output filenames

Outputs must be named by a stable identifier derived from the input (for
the YouTube tools: the canonical 11-character video ID, resolved via
`yt-dlp --print id`). This lets multiple inputs coexist in the same working
directory without collisions.

Use suffixes to disambiguate purposes:
- Primary outputs: `<ID>.<ext>` (e.g. `<ID>.srt`, `<ID>.mp4`)
- Per-language source files: `<ID>.<lang>.srt` (e.g. `<ID>.en.srt`,
  `<ID>.zh.srt`)
- Translated subtitles: `<ID>.translated.<target-code>.srt` (e.g.
  `<ID>.translated.ru.srt`). The `.translated.` infix is what
  separates these from YouTube-supplied per-language files; never
  drop it, or translate.py will silently overwrite the source-side
  SRTs.
- Sidecars: `<ID>.<purpose>.txt` (e.g. `<ID>.lang.txt`,
  `<ID>.translate-source-lang.txt`, `<ID>.translate-target-lang.txt`,
  `<ID>.video-quality.txt`, `<ID>.summary-speaker.txt`,
  `<ID>.summary-context.txt`)

#### Per-video results folder

Both `scripts/transcribe.py` and `scripts/translate.py` group all of one
video's outputs under a **results folder**:

    <CWD>/results/<YYYY-MM-DD>_<video-title-slug>/

The slug is built by lowercasing the YouTube title and collapsing
non-word characters into underscores (Unicode letters/digits are kept).
The lookup is **video-ID-keyed, not date-keyed** — on a re-run the helper
scans `results/*/` for any folder already containing a file starting
with `<ID>.`, and re-uses it instead of creating a fresh dated folder.
The date prefix is therefore the date of the *first* run for a given
video, not the most recent run.

The helper that implements this lives in `scripts/_common.py`
(`find_or_create_results_dir`). Functional scripts that adopt the
results-folder layout should call it then `os.chdir()` into the returned
path before any other I/O, so the rest of the script can keep using
CWD-relative filenames.

If you add a script that produces more than ~3 files for a single input,
prefer the results-folder pattern.

### 5.5 Stdlib-only Python

Scripts are written in Python 3 and must use **only the standard library**
plus `subprocess` calls to the host CLI tools listed in § 4. No `pip install`
step for the user, no requirements.txt, no virtualenv. If you find yourself
reaching for a third-party Python package, first see if shelling out to an
existing host CLI achieves the same thing.

### 5.6 Interactive UX defaults

Default to interactive prompts (clear questions, `[s]/[r]/[c]`-style menus,
sensible defaults shown in brackets). Non-interactive / CLI-flag-driven
modes are welcome additions but should be opt-in, not the default — the
target user runs these tools by hand.

Use the color-coded log helpers from `scripts/_common.py` (`info` / `ok` /
`warn` / `err` / `die`) for readable terminal output. Don't add emoji unless
the user explicitly asks for it.

For diagnostic logs that should be off by default, use `debug(msg)` from
`scripts/_common.py` — silent unless `set_verbose(True)` has been called,
prints dim `[d]` lines to stderr. Wrap slow subprocess calls with the
`time_block(label)` context manager from the same module so verbose runs
show start + duration. Every functional script accepts `-v` / `--verbose`
via `argparse` and calls `set_verbose(args.verbose)` at the very top of
`main()`. `start.py` forwards `--verbose` to the dispatched script and
also honors `VIDEO_TOOLS_VERBOSE=1` in the environment.

### 5.7 No external state

A script must not depend on global config, environment variables, or hidden
state outside its working directory and the user's standard caches
(`~/.cache/huggingface/`, npm/pipx install dirs). All per-run state lives in
sidecar files next to the outputs.

### 5.8 Privacy / copyright

Test outputs generated against real third-party videos (the `.mp4`, `.srt`,
`.txt`, `.json`, `.tsv`, `.vtt`, `.dialogue.txt`,
`.translated.<tgt>.srt`, `.translated.<tgt>.broken.srt`, `.lang.txt`,
`.translate-*.txt`, `.video-quality.txt`, `.summary.md`, and
`.summary-*.txt` files named by an 11-character YouTube ID — as well as
the entire `results/` folder both functional scripts write them into)
are **not** committed to the repo. `.gitignore` covers these extensions
and the `results/` directory. When adding new output file types or
top-level output directories, extend `.gitignore` in the same change.

### 5.9 Self-documenting scripts

Every functional script begins with a top-level docstring covering:
- one-line purpose,
- platform,
- prerequisites (with install commands),
- usage (both via `start.py` and direct invocation),
- outputs (filename → meaning table),
- idempotency rules,
- known limitations.

If you change behavior, update the docstring in the same edit.

---

## 6. Documentation upkeep (meta-rule)

When you add a new script, or modify an existing one such that its functional
requirements, outputs, prompts, or host dependencies change, you MUST update
the project documentation in the **same change**:

- [`README.md`](./README.md):
  - Add or update the row in the **Functional scripts** table.
  - Update the **Prerequisites** section if a new host CLI is required.
  - Update the **Output files** table if filenames or sidecars change.
  - Update the **Quick start** flow if the user-visible interaction changes.

- [`AGENTS.md`](./AGENTS.md) (this file):
  - Update § 2 (repository structure) if the layout changes.
  - Update § 4 (host CLI dependencies) if a new tool was added.
  - Update § 5 (non-functional requirements) if the change establishes a new
    convention worth applying to future scripts.
  - Update § 7 (per-script notes) with anything script-specific.

- [`start.py`](./start.py): add a row to the `SCRIPTS` list for any new
  functional script so it appears in the menu.

- The script's own top-of-file docstring (see § 5.9).

Docs that drift behind the code are worse than no docs. Treat doc updates as
part of the implementation, not a follow-up.

---

## 7. Per-script notes

### `start.py`

- Top-level interactive menu. Reads a numeric choice and dispatches to a
  script under `scripts/` via `subprocess.run([sys.executable, ...])`.
- Does NOT perform host-CLI preflight (that lives in the functional scripts).
- Adding a new functional script requires one change here: append a tuple to
  the `SCRIPTS` list at the top of the file.

### `scripts/_common.py`

- Helpers shared by both functional scripts: log functions, preflight checks
  (`check_yt_dlp`, `check_ffmpeg`, `check_claude`), YouTube URL prompt + ID
  resolver + title fetch, `get_video_metadata` (one-shot `yt-dlp -J`),
  pure parsers `available_video_heights` / `available_subtitle_langs` over
  that metadata, `download_video` (idempotent; accepts `max_height`,
  defaults 720p), `download_subtitles_for_lang` (per-language SRT fetch,
  idempotent, best-effort), `find_existing_artifacts` (returns the mp4
  and the list of `<ID>.<lang>.srt` files in CWD — used by translate.py's
  idempotency gate), `video_title_to_slug` + `find_or_create_results_dir`
  (per-video results folder), SRT timecode regex, `count_cue_blocks`.
- Side-effect-free at import time. Do not add module-level `print`, `input`,
  or `sys.exit` calls.
- Leading underscore signals "internal module". Don't register it in
  `start.py`.

### `scripts/transcribe.py`

- Pipeline: resolve URL → fetch ID + title → `find_or_create_results_dir`
  + `chdir` → `download_video` → optional `download_subtitles_for_lang`
  (best-effort YouTube subs) → `mlx_whisper` (turbo, `--output-format all`)
  → `write_dialogue_txt` → optional summary step.
- All outputs land inside `results/<YYYY-MM-DD>_<slug>/`. The folder is
  resolved by video ID, so re-running on a later day re-uses the same
  folder rather than creating a new dated one.
- Post-processes the SRT into `<ID>.dialogue.txt` (paragraphs split on
  silence gaps controlled by the `PARAGRAPH_GAP_SECONDS` constant — the
  threshold above which a gap starts a new paragraph).
- Sidecar `<ID>.lang.txt` records the last language used so re-runs can
  offer "same language".
- **Summary step (opt-in)**: after dialogue.txt is written, the script
  asks `Summarize this video with claude? [y/N]`. If yes, it preflight-
  checks `claude`, asks for a speaker name and optional context (both
  sidecar-backed via `<ID>.summary-speaker.txt` and
  `<ID>.summary-context.txt`), substitutes them along with the
  dialogue.txt body into `prompts/summary_prompt.md` using plain
  `str.replace()`, calls `claude -p`, and writes `<ID>.summary.md`. A
  re-run with an existing summary offers `[s]/[r]/[c]`. The prompt
  template path is resolved from `__file__`, not CWD, so the chdir into
  the results folder doesn't break it.
- No speaker diarization. If diarization becomes a requirement, the
  agreed-upon path is to layer `pyannote-audio` on top of mlx_whisper.
  The summary step infers speaker turns from context rather than from
  labels.

### `scripts/translate.py`

- Pipeline: resolve URL → fetch ID + title → `find_or_create_results_dir`
  + `chdir` → idempotency gate (`[p]roceed / [r]e-download` if files
  already on disk) → on a fresh / re-download run: `get_video_metadata` →
  quality menu over `available_video_heights` → `download_video` at the
  chosen height → loop `download_subtitles_for_lang` over every
  base-code subtitle language → ask whether to translate → pick source
  SRT → pick target (EN/RU/KK) → `parse_source_cues` → strip timecodes →
  `chunk_cues` splits the cue list into `CHUNK_SIZE`-sized windows →
  `ThreadPoolExecutor(max_workers=MAX_WORKERS)` dispatches one
  `claude -p` per chunk in parallel (background `_Heartbeat` thread
  prints progress every `HEARTBEAT_INTERVAL_S` seconds) → merge each
  chunk's `parse_claude_translations` output → one global
  `validate_translation_coverage` over the merged dict →
  `assemble_translated_srt` writes the final SRT using the *source's*
  timecodes.
- All outputs land inside `results/<YYYY-MM-DD>_<slug>/`, resolved
  by video ID, so re-running on a later day re-uses the same folder.
- **Idempotency gate**: if `<ID>.mp4` already exists in the results
  folder, the script lists what's there and asks
  `[p]roceed / [r]e-download`. Proceed jumps straight to the
  translation prompt. Re-download wipes the existing mp4 + per-language
  SRTs but preserves sidecars (`.translate-*-lang.txt`,
  `.video-quality.txt`) and any translation `.md` files, then runs the
  full download flow.
- **Quality menu**: heights are read from `yt-dlp -J`. Default is the
  previous sidecar value if still on offer, else the highest <= 720p,
  else the highest advertised height. Recorded in
  `<ID>.video-quality.txt`.
- **Subtitle downloads**: every advertised language is fetched (manual
  subs + auto-generated captions), normalized to base ISO codes
  (`en-US` → `en`). Each call is itself idempotent — re-runs that pick
  `[p]roceed` only fetch what's missing.
- **Target language is restricted to English, Russian, or Kazakh** by
  an explicit three-option menu (`prompt_target_language`). To add
  another target, extend that function, `_LANG_LABELS`, and
  `canonical_lang_name` in the same edit.
- **Source language is picked from the downloaded SRT files** via a
  numbered menu (auto-skipped when only one SRT is on disk). The code is
  parsed from the filename by `lang_from_srt_path`.
- **Timecodes never reach the LLM**. `parse_source_cues` parses the
  source SRT into `[(cue_num, timecode_line, text)]` tuples;
  `serialize_cues_for_prompt` emits only `<cue_num>\t<text>` lines
  (translatable cues only). Claude returns the same shape with
  translated text. `parse_claude_translations` reads its response back
  into `{cue_num: translation}`. `assemble_translated_srt` walks the
  *source* cue list to write the final SRT, substituting each cue's
  translated text under the original cue number + timecode line.
  Timecode corruption is impossible by construction.
- **Validation is cue-count parity only**. `validate_translation_coverage`
  checks that every translatable source cue has a matching key in the
  parsed translations. On mismatch the raw claude output is written to
  `<ID>.translated.<tgt>.broken.srt` and the script dies — there is no
  retry pass.
- **Output is a plain SubRip (.srt) file**, written by Python (not by
  claude). `<ID>.translated.<tgt>.srt` is ready for any video player.
  `<tgt>` is the lowercased 2-letter code produced by
  `lang_label(target_lang).lower()` (`ru`, `en`, `kk`).
- Three sidecars (`<ID>.translate-source-lang.txt`,
  `<ID>.translate-target-lang.txt`, `<ID>.video-quality.txt`) let the
  script offer "re-use last" on subsequent runs.
- **Parallel chunking**: cues are split into `CHUNK_SIZE`-sized
  windows (default 1000) by `chunk_cues`, then dispatched to a
  `ThreadPoolExecutor(max_workers=MAX_WORKERS)` (default 4). Each
  worker runs its own `claude -p`. Tune the two constants near the
  top of `scripts/translate.py` if you need different throughput vs
  rate-limit tradeoffs. A single failed worker (empty claude
  response, subprocess error) aborts the whole translation.
- **Strict per-chunk filtering**: each `_translate_chunk` filters
  claude's parsed response down to the cue IDs that were actually in
  that chunk's input. Claude sometimes invents cue numbers from
  neighboring ranges to keep its output line count matching when it
  has merged two cues, and without the filter those bogus numbers
  would overwrite real translations from adjacent chunks during the
  global `dict.update()` merge.
- **Retry pass for merged cues**: if the validation after the parallel
  pass shows missing cues — typically because claude collapsed a
  sentence-split pair like
  `6045\tLike a dream,` + `6046\tmay not have actually occurred.`
  into one translation — one extra synchronous `claude -p` call goes
  out with just the missing cue IDs (provided there are at most
  `MAX_RETRY_MISSING` of them; default 100). Re-translating in
  isolation almost always succeeds because there are no neighbors to
  merge with. If retry still leaves cues missing, the script writes
  `<ID>.translated.<tgt>.broken.srt` and dies as before.
- **Heartbeat**: a daemon thread (`_Heartbeat`) prints elapsed-time
  + chunks-done every `HEARTBEAT_INTERVAL_S` seconds so long chunks
  don't look hung. Stops automatically when all chunks finish.
- All language- and SRT-handling helpers (`lang_label`, `slug_lang`,
  `canonical_lang_name`, `normalize_subtitle_langs`,
  `lang_from_srt_path`, `default_quality_choice`, `parse_source_cues`,
  `chunk_cues`, `serialize_cues_for_prompt`,
  `parse_claude_translations`, `validate_translation_coverage`,
  `assemble_translated_srt`, `build_translate_prompt`,
  `translated_srt_path`, `broken_translated_srt_path`) are pure
  functions designed for unit-testing. Keep them pure if you change
  them. `build_translate_prompt` takes the template as a string
  argument so the file I/O stays out of the pure layer.

### Adding a new functional script

Before merging, confirm:
- [ ] File lives at `scripts/<name>.py` and is independently runnable.
- [ ] Top-of-file docstring covers all items in § 5.9.
- [ ] Preflight checks for every host CLI it shells out to.
- [ ] Outputs are namespaced by a stable ID (§ 5.4).
- [ ] Re-runs are idempotent or gated by a `[s]/[r]/[c]` prompt (§ 5.1).
- [ ] Registered in `start.py`'s `SCRIPTS` list.
- [ ] `README.md` **Functional scripts** table and **Output files** table
      updated.
- [ ] `AGENTS.md` § 4 (CLI deps) and § 7 (per-script notes) updated.
- [ ] `.gitignore` covers any new generated output extensions.

---

## 8. Verification before claiming done

Before reporting a change as complete:

1. `python3 -m py_compile start.py scripts/_common.py scripts/<changed>.py` —
   syntax check on every file you touched.
2. Dry-run the preflight section in an environment where one of the
   dependencies is hidden (e.g.
   `PATH=/usr/bin python3 scripts/transcribe.py`) and confirm the install
   hint fires.
3. For new pure functions, run a small inline unit test:
   ```bash
   python3 - <<'PY'
   import sys; sys.path.insert(0, "scripts")
   from translate import slug_lang, lang_label
   assert slug_lang("Brazilian Portuguese") == "brazilian-portuguese"
   assert lang_label("Russian") == "RU"
   print("ok")
   PY
   ```
4. If the script has an existing test suite (none today), run it. Don't
   report completion until the full suite passes.

---

## 9. Style and conventions

- Conventional commit format (`feat:`, `fix:`, `chore:`, `docs:`, `test:`).
- Do not mention yourself or add yourself as a co-author in commits.
- The user is on macOS with `zsh`; don't suggest Linux-only commands.
- When referencing functions or pieces of code in chat, use the
  `file_path:line_number` pattern.
- Keep messages to the user short and direct; no trailing summaries unless
  asked.
