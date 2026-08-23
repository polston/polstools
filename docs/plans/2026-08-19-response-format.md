# Response-format feature — design record

2026-08-19, updated 2026-08-23. The `p` plugin makes every turn-ending reply
open with a fixed triage block — FINDINGS, PROBLEMS, ASKS. Interim tool progress
is one concise sentence instead. A horizontal rule separates optional detail
only when such detail follows.

## Mechanism: why hooks, not an output style or instruction-file prose

1. Output styles are deprecated. Anthropic now ships its former built-in
   styles as plugins whose SessionStart hook emits the style text as
   additional context; this plugin copies that exact pattern.
2. Instruction files (CLAUDE.md and kin) are documented as the weakest
   channel: loaded as context, "no guarantee of strict compliance", and they
   compete for a finite instruction budget.
3. Format adherence decays within a conversation (measured multi-turn
   degradation ~39% on average; drift observable within ~8 turns). The
   measured antidote is re-injecting the rules late in context — RECAP-style
   re-injection lifted format adherence by ~16 points in benchmarks — so the
   plugin adds a UserPromptSubmit hook that repeats a compact reminder every
   turn. The reminder distinguishes interim progress from a turn-ending reply;
   in-prompt self-check instructions alone underperform because models can
   restate constraints while violating them.

Both hooks emit plain stdout (appended to context for SessionStart and
UserPromptSubmit); no JSON envelope is needed, so the payloads stay as
readable markdown files under `style/`.

The injection is toggleable per session: both hooks route through
`bin/format-ctl gate`, which prints the payload unless a per-session flag
file exists under the operating-system temp directory. `/p:fmt-off` and
`/p:fmt-on` are native skills that flip the flag — the hook side reads the
session id from hook input JSON, while the skill side accepts the native Claude
Code or Codex session environment. Unreadable hook input fails open to ON;
flags older than 14 days are pruned on every toggle. A bad payload makes `gate`
exit 1, not 2 — exit 2 from a UserPromptSubmit hook blocks processing and
erases the user's prompt. Hook commands execute the controller directly through
its Python 3 shebang.

## Renderer facts the format is built around

Verified against the installed Claude Code CLI (2.1.237) by inspecting its
embedded markdown-to-terminal renderer:

1. `# H1` renders bold + italic + underlined — the only underlined heading
   level. `##` and deeper render bold only. Hence the section headers are H1.
2. Ordered markdown lists are renumbered by depth as `1.` / `a.` / `i.`;
   hand-written dotted-decimal markers (`1.1.`) at line start are tokenized as
   nested lists and destroyed. Dotted sub-numbers therefore ride as bold
   prefixes (`**1.1.**`), which the list tokenizer ignores.
3. `---` renders as a literal horizontal rule line, usable as the
   above/below-the-fold separator.

## Wording choices, with the evidence grade

1. Skeleton plus one filled `<example>` rather than prose rules alone —
   vendor-doc guidance; examples outperform abstract instructions for output
   consistency.
2. "Begin a turn-ending reply with the literal line `# FINDINGS`" — the
   vendor-endorsed anti-preamble device now that response prefill is
   unsupported. Interim tool progress is explicitly exempt.
3. Fixed sections in turn-ending replies (`**1.** None.` when empty) —
   structural constraints are the best-followed constraint family in
   IFEval-style measurements; optional final sections invite drift.
4. Five top-level items across the whole triage block, 30-word substantive
   lines, and 15-word problem titles reflect repeated history evidence. Numeric
   length compliance remains approximate, so excess detail has a sanctioned
   home below the rule rather than being silently discarded.
5. Plain register, positive framing, no CAPS/IMPORTANT inflation — current
   models overtrigger on aggressive wording; emphasis is reserved for
   nothing here.
6. The free-form zone below the rule is deliberate: measured results show
   over-strict output formats degrade reasoning quality, so the structured
   block stays minimal and everything else has somewhere sanctioned to go. The
   separator is conditional because a rule with no following detail separates
   nothing.
7. Every problem carries reader impact and a recommended action, and every
   answer-requiring question lives in ASKS. Unresolved problem and ask numbers
   persist across turns; turn-local findings restart at 1.

Sources: Anthropic prompt-engineering documentation (output consistency,
examples, long-context tips); IFEval and successors; CFBench; RECAP; "Let Me
Speak Freely". One secondary-sourced figure (per-turn decay of a reasoning
model, arXiv 2511.03508) was not confirmed against the primary and is not
relied on above.

## Layout

- `plugins/p/hooks/hooks.json` — registers both hooks; each command runs
  `bin/format-ctl gate`, which prints its payload unless the session is
  toggled off. A missing payload still surfaces as a failed hook.
- `plugins/p/bin/format-ctl` — the gate plus the `off`/`on`/`status`
  toggle (stdlib Python 3). Reconfigures stdio to UTF-8 first: piped stdout
  on Windows defaults to the ANSI code page and mangles non-ASCII payload
  bytes (caught by running the gate, invisible in file reads).
- `plugins/p/bin/format-e2e` — 25-check end-to-end verifier: wiring,
  gates byte-exact, UTF-8 validity, toggle lifecycle, exit-code contract,
  catalog sync.
- `plugins/p/skills/maintaining-the-format-plugin/` — mechanical and
  behavioral health checks, repair direction, deliberate-choices table, and
  full-removal checklist.
- `plugins/p/skills/fmt-off/` and `plugins/p/skills/fmt-on/` — native
  `/p:fmt-off` and `/p:fmt-on` toggles for Claude Code and Codex sessions.
- `plugins/p/style/` — the full and per-turn payloads; semantic contract tests
  keep their duplicated rules aligned.
