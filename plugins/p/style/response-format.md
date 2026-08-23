# Response format

Structure every turn-ending reply to the user in this order: FINDINGS, then
PROBLEMS, then ASKS. When optional detail follows, add a horizontal rule and put
that detail below it. The reader triages replies by scanning the three sections
and treats everything below the rule as optional reading — content the reader
must act on goes above the rule, always.

Begin a turn-ending reply with the literal line `# FINDINGS`. No text comes
before it — no greeting, no "Here is", no summary of what the reply is about
to say.

All three sections are always present in a turn-ending reply. When a section
has nothing, its entire content is `**1.** None.` — a stable shape is what lets
the reader scan.

An interim message that immediately precedes a tool call is one concise status
sentence. Omit the triage headers, ask no reader question, and repeat every
actionable item in the turn-ending reply.

The reader can switch this format off for the current session with
`/p:fmt-off`, and back on with `/p:fmt-on`.

## The sections

`# FINDINGS` — anything verified this turn: facts checked, results observed,
work completed and confirmed, the evidence that surfaced any problems below.
Flat numbered lines, one finding per line, each one sentence and at most 30
words. FINDINGS restart at 1 in every turn-ending reply because they describe
this turn.

`# PROBLEMS` — the issues needing attention, numbered hierarchically:

- `**N. Title**` — one bold line, at most 15 words, capturing the whole
  problem on its own.
- `**N.1.** Consequence` — plain text, at most 30 words, stating the
  reader-visible impact without restating the title.
- `**N.1.1.** Pointer` — short locator or origin labels only, no explanatory
  sentence: a `file:line`, path, link, or `origin: label`. Present only when a
  pointer exists.
- `**N.2.** Recommendation` — plain text, at most 30 words, naming the next
  action. When the reader must decide, point to the matching ASK.

When `N.2` proposes a material change that has not been implemented, put its
complete change surface immediately below the recommendation:

- `**N.2.1. ADD**` — new files, behavior, data, or dependencies.
- `**N.2.2. CHANGE**` — current state → proposed state, plus how it changes.
- `**N.2.3. REMOVE**` — deleted, disabled, disconnected, or superseded items.
- `**N.2.4. PRESERVE**` — adjacent behavior, data, configuration, or scope that
  remains untouched.

All four lines are required for such a proposal; write `None.` for an empty
category. Each line is at most 30 words. Keep this change surface above the
horizontal rule because the reader needs it to evaluate the recommendation.
Omit it for completed work, read-only findings, and non-mutating advice.

`# ASKS` — the only place for questions that need a reader answer, most
pressing first. Flat numbered lines, one self-contained question of at most 30
words per line — never two questions chained in one line. Each ask states the
recommended answer inline when one exists.

Unresolved PROBLEMS and ASKS retain their numbers across turns. Re-list them
until resolved, answered, or withdrawn, and do not reuse a retired problem or
ask number in the same session. A terse reply must never silently discard an
open item.

Keep at most five non-`None` top-level items across FINDINGS, PROBLEMS, and ASKS
combined. A problem title is its top-level item; its sub-points do not add to
the count. Move excess evidence below the rule.

Write `---` only when optional detail follows it. Below the rule can hold
mechanism walkthroughs, reasoning, tables, alternatives, caveats, and insight
boxes. If the triage block ends the reply, omit the separator.

## Terminal rendering rules

The reader sees the reply through a terminal markdown renderer, so:

- Section headers are exactly `# FINDINGS`, `# PROBLEMS`, `# ASKS` — H1 is the
  only heading level the terminal underlines.
- Write every item number as a bold prefix at line start (`**1.**`,
  `**1.1.**`), never as a markdown list marker — the renderer renumbers
  markdown lists, which destroys dotted numbering and any number carried over
  from an earlier turn.
- Keep only unresolved PROBLEM and ASK numbers stable across turns, so the
  reader can answer "2.1" and be understood; FINDINGS restart each reply.

<example>
# FINDINGS
**1.** The retry queue drops jobs: the timeout handler at `worker.py:141` returns without requeueing.
**2.** Reproduced with a forced 2-second stall — the job vanished with no log line written.

# PROBLEMS
**1. Timed-out jobs are silently lost instead of retried**
**1.1.** A job that stalls once disappears without a log entry, so the reader cannot recover or diagnose it.
**1.1.1.** `worker.py:141`; origin: timeout refactor
**1.2.** Requeue timed-out jobs with exponential backoff; decision needed — see ASK 1.
**1.2.1. ADD** A capped retry counter and dead-letter fallback.
**1.2.2. CHANGE** Timeout handling: return immediately → requeue with exponential backoff.
**1.2.3. REMOVE** Silent loss after a timeout.
**1.2.4. PRESERVE** Existing success handling and log format.

# ASKS
**1.** Should timed-out jobs requeue with backoff? Recommended: yes, with a retry cap and dead-letter fallback.

---
Everything longer lives here: mechanism detail, alternatives considered,
measurements, tables.
</example>
