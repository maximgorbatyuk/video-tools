Your previous translation of these subtitles failed an automated
timecode-consistency check. You will redo the translation, this time
paying strict attention to the timecodes.

## Context

- Video ID: `{{video_id}}`
- Source language: {{source_lang}}
- Target language: {{target_lang}}

## What went wrong

The output you produced on the previous attempt did not preserve the
timecodes from the source SRT file. A Python validator extracted every
`HH:MM:SS,mmm --> HH:MM:SS,mmm` line from the source and from your
output, then compared them position-by-position. The mismatches it
found are listed below.

### Timecode inconsistencies

{{timecode_issues}}

## Your task

Redo the translation. Emit a valid SubRip (.srt) subtitle file in
{{target_lang}}, with this exact structure per cue:

    <cue_number>
    HH:MM:SS,mmm --> HH:MM:SS,mmm
    <translated {{target_lang}} text>

Separate cues with one blank line. Output ONLY the .srt content — no
preamble, no headings, no markdown code fences, no commentary. The very
first character of your response must be the `1` of the first cue.

## Hard rules (re-iterated)

1. **Copy every timecode character-for-character** from the source
   SRT. The validator will run again on your output.
2. **Emit one cue per source cue, in the same order**, with the same
   cue numbers. No reordering. No merging. No dropping. The cue count
   must equal the source cue count.
3. **Preserve sentence-level meaning** when translating. Natural prose,
   not word-for-word — but every sentence in the source must be
   reflected in its target cue.
4. **Do not include the original {{source_lang}} text** in the output.
   Only the {{target_lang}} translation goes under each timecode.

## Original SRT (the source of truth for timecodes)

{{original_srt}}

## Your previous (broken) output, for reference only

The block below is what you produced last time. Do NOT copy its
timecodes — they are the ones that failed validation. Use it only to
see which translations you can keep verbatim and which you must redo.

{{broken_translation}}
