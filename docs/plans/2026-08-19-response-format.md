# Response-format plugin — design record

2026-08-19. The `format` plugin makes every assistant reply open with a fixed
triage block — FINDINGS, PROBLEMS, ASKS — followed by a horizontal rule, with
everything else below the rule as optional reading.

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
   plugin adds a UserPromptSubmit hook that repeats a four-line reminder every
   turn. In-prompt self-check instructions alone underperform: models restate
   constraints while violating them.

Both hooks emit plain stdout (appended to context for SessionStart and
UserPromptSubmit); no JSON envelope is needed, so the payloads stay as
readable markdown files under `style/`.

The injection is toggleable per session: both hooks route through
`bin/format-ctl gate`, which prints the payload unless a per-session flag
file exists in a state directory outside any repo (`%LOCALAPPDATA%` on
Windows, `~/.local/state` elsewhere). `/format:off` and `/format:on`
flip the flag — the hook side reads the session id from the hook input JSON,
the command side from `CLAUDE_CODE_SESSION_ID` (present in the tool
environment). Unreadable hook input fails open to ON; flags older than 14
days are pruned on every toggle.

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
2. "Begin the reply with the literal line `# FINDINGS`" — the vendor-endorsed
   anti-preamble device now that response prefill is unsupported.
3. Fixed sections that are always present (`**1.** None.` when empty) —
   structural constraints are the best-followed constraint family in
   IFEval-style measurements; optional sections invite drift.
4. Word caps ("at most 15 words") kept from the operator's spec but treated
   as soft targets — measured compliance with numeric length caps is only
   approximate (40–88% under perturbation), and length is the worst-followed
   constraint family.
5. Plain register, positive framing, no CAPS/IMPORTANT inflation — current
   models overtrigger on aggressive wording; emphasis is reserved for
   nothing here.
6. The free-form zone below the rule is deliberate: measured results show
   over-strict output formats degrade reasoning quality, so the structured
   block stays minimal and everything else has somewhere sanctioned to go.

Sources: Anthropic prompt-engineering documentation (output consistency,
examples, long-context tips); IFEval and successors; CFBench; RECAP; "Let Me
Speak Freely". One secondary-sourced figure (per-turn decay of a reasoning
model, arXiv 2511.03508) was not confirmed against the primary and is not
relied on above.

## Layout

- `plugins/format/hooks/hooks.json` — registers both hooks; each command runs
  `bin/format-ctl gate`, which prints its payload unless the session is
  toggled off. A missing payload still surfaces as a failed hook.
- `plugins/format/bin/format-ctl` — the gate plus the `off`/`on`/`status`
  toggle (stdlib Python 3).
- `plugins/format/commands/` — `/format:off` and `/format:on`.
- `plugins/format/style/` — the payloads; editing the format means editing
  these two files only.
