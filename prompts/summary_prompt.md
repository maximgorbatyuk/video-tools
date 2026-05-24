You will produce a long-form, quote-rich Markdown summary of an interview transcript.

## Inputs

- `speaker_name` — name of the main interviewee.
- `context` — one or two sentences about who the speaker is and what the interview covers (use this to orient the reader in the intro paragraph).
- `transcript` — the transcript text. It interleaves the interviewer's questions and the speaker's answers **without speaker labels**. You must distinguish them from context (first-person voice, content, who-is-being-asked-vs-answering).

## Output format

Pure Markdown. No preamble — emit the document directly. Structure:

1. **Intro paragraph (2-3 sentences)** identifying the speaker, the source, and what the reader is about to get.
2. **8-15 sections** following the interview's natural order. Each section consists of:
   - **H2 heading** (2-6 words) naming the topic.
   - **1-2 paragraphs of prose** in third person, summarizing what the speaker said about that topic — the argument, the example, the reframing, the analogy.
   - **One or more direct quotes** from the speaker, formatted as Markdown blockquotes with italics:

     > *"Quote text here."*

## What makes a good quote

Each quote is **1-3 sentences** that capture **one complete, important thought the speaker wanted to point attention to** — the kind of line a reader would screenshot, repost, or remember. Choose quotes that:

- Express a **self-contained idea** — not a sentence fragment, not setup-without-punchline.
- Land a sharp framing, a strong opinion, a definition, a vivid analogy, or a memorable example.
- Use the speaker's **exact wording**.
- **Complement** the surrounding prose rather than repeat it word-for-word.

## Hard rules

- **Verbatim quotes only.** Do not paraphrase, smooth out, "correct grammar," or rewrite. The speaker's voice must be preserved — contractions, emphatic restatements, idiosyncratic phrasing, all kept.
- **Trimming with ellipses is allowed.** If a quote contains filler ("like, you know", false starts), trim with `…` while staying faithful to the original meaning. Never invent connecting words.
- **No interviewer quotes.** Only the named speaker. Use prose to convey what the interviewer asked, when relevant.
- **Follow the natural order of the interview.** Don't reorder topics to make the summary "flow better."
- **No invented quotes.** Every blockquote must trace to a real line in the transcript.
- **Prose body uses paragraphs only.** No bullet lists, no bold emphasis inside body paragraphs. Headings, paragraphs, and blockquotes are the only allowed elements.

## Length target

1500-3000 words including quotes. Err toward the longer end if the transcript is rich.

---

Speaker: {{speaker_name}}
Context: {{context}}

Transcript:

{{transcript}}
