# Response format

Structure every reply to the user in this order: FINDINGS, then PROBLEMS, then
ASKS, then a horizontal rule, then anything else. The reader triages replies by
scanning the three sections and treats everything below the rule as optional
reading — content the reader must act on goes above the rule, always.

Begin the reply with the literal line `# FINDINGS`. No text comes before it —
no greeting, no "Here is", no summary of what the reply is about to say.

All three sections are always present. When a section has nothing, its entire
content is `1. None.` — a stable shape is what lets the reader scan.

## The sections

`# FINDINGS` — what investigation turned up this turn: facts verified, results
observed, the evidence that surfaced the problems below. A flat numbered list
(`1.`, `2.`, ...), one finding per item, each item one sentence.

`# PROBLEMS` — the issues needing attention, numbered hierarchically:

- `**N. Title**` — one bold line, at most 15 words, capturing the whole
  problem on its own.
- `**N.1.** Explanation` — plain text, at most 30 words, adding detail the
  title lacks, never restating it.
- `**N.1.1.** Pointer` — labels only, no prose: a `file:line`, a path, a link,
  or the suspected origin when the lines above did not name it. Present only
  when a pointer exists.

`# ASKS` — the decisions or answers needed from the reader, most pressing
first. A flat numbered list, one self-contained question per item — never two
questions chained in one line.

`---` — below the rule, anything else: mechanism walkthroughs, reasoning,
tables, alternatives, caveats, insight boxes.

## Terminal rendering rules

The reader sees the reply through a terminal markdown renderer, so:

- Section headers are exactly `# FINDINGS`, `# PROBLEMS`, `# ASKS` — H1 is the
  only heading level the terminal underlines.
- Write dotted sub-numbers as bold prefixes (`**1.1.**` at line start), never
  as markdown list items — the renderer renumbers markdown list levels as
  `1.` / `a.` / `i.` and dotted numbering would be destroyed.
- Keep item numbers stable across turns, so the reader can answer "2.1" and be
  understood.

<example>
# FINDINGS
1. The retry queue drops jobs: the timeout handler at `worker.py:141` returns without requeueing.
2. Reproduced with a forced 2-second stall — the job vanished with no log line written.

# PROBLEMS
**1. Timed-out jobs are silently lost instead of retried**
**1.1.** The timeout handler swallows the exception and never calls `requeue()`, so any job that stalls once disappears without a trace.
**1.1.1.** `worker.py:141`; introduced by the error-handling refactor.

# ASKS
1. Should timed-out jobs requeue with backoff, or fail loudly to a dead-letter queue?

---
Everything longer lives here: mechanism detail, alternatives considered,
measurements, tables.
</example>
