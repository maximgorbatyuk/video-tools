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
`HH:MM:SS,mmm --> HH:MM:SS,mmm` range from the source and from your
output, then compared them in order. The mismatches it found are listed
below.

### Timecode inconsistencies

{{timecode_issues}}

## Your task

Redo the translation. The output format is unchanged from the previous
attempt:

    [HH:MM:SS,mmm --> HH:MM:SS,mmm]
    {{src_label}}: <the original {{source_lang}} line, verbatim>
    {{tgt_label}}: <a natural, fluent translation into {{target_lang}}>

Separate blocks with one blank line. Output ONLY the cue blocks — no
preamble, no headings, no closing remarks, no markdown code fences.

## Hard rules (re-iterated)

1. **Copy every timecode character-for-character** from the source SRT.
   Do not round, re-format, shift, merge, or split timecodes. The
   validator will run again on your output.
2. **Emit one block per cue in the source**, in the same order. No
   reordering. No merging. No dropping. The block count must equal the
   source cue count.
3. **Preserve sentence-level meaning** when translating. Natural prose,
   not word-for-word, but every sentence in the source must be reflected
   in the {{tgt_label}} line of the corresponding block.
4. **Use the original {{source_lang}} text verbatim** on the
   {{src_label}} line.

## Original SRT (the source of truth for timecodes)

{{original_srt}}

## Your previous (broken) output, for reference only

The block below is what you produced last time. Do NOT copy its
timecodes — they are the ones that failed validation. Use it only to
see which translations you can keep and which you must redo.

{{broken_translation}}
