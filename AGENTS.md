# polstools — agent instructions

A single plugin distributed through its marketplace: skills, commands, and
scripts shared across AI coding harnesses from one repository. Everything
committed here is published to a git remote — write accordingly.

## CRITICAL — no private data, and no other project by name

Nothing personally identifiable, confidential, or secret enters this
repository: not tracked files, not history, not commit messages, not fixtures.
That covers credentials of any kind, emails, real names, account or machine
names, LAN IPs, hostnames, MAC addresses, absolute paths from the author's
machine, session ids, and harvested command or session history.

**No other project of the author's is named here, ever.** The skills and design
documents in this repo are usually written from experience gained elsewhere.
Refer to that experience conceptually — "a previous attempt grew into a
compiled binary and stopped running" — and never by name, path, language,
schema, count, or any other detail that identifies which project it was. This
applies equally to prose, examples, code comments, and commit messages.

If any of it turns up — in a file, in a diff, or already in history — stop and
report it before doing anything else. Removal is the author's decision, and
publishing is the author's decision every time.

One deliberate exception: author identity metadata, including standard
`Co-Authored-By` trailers, is published with this repository on purpose and is
not to be scrubbed. The privacy scan treats those records as accepted metadata,
not findings. Nothing else in this section has an exception.

`plugins/p/bin/repo-privacy-audit` catches the mechanical cases. It cannot
recognise a project name, so that half is on the writer.

## Commit messages never go through a double-quoted shell string

Backticks and `$(…)` inside `git commit -m "…"` execute and paste their output
into repository metadata. Use `git commit -F -` fed by a single-quoted
heredoc, or write no backticks at all. Read the message back afterwards —
substitution is silent, and nobody re-reads metadata.

## Layout and conventions

- `.claude-plugin/marketplace.json` and `.agents/plugins/marketplace.json` list
  the same `p` source for Claude and universal Codex packaging respectively.
- `plugins/p/.claude-plugin/plugin.json`,
  `plugins/p/.codex-plugin/plugin.json`, and `plugins/p/plugin.json` are the
  harness manifests for Claude, Codex, and Antigravity; release metadata must
  stay in step across all three and the marketplace entries.
- `plugins/p/skills/<skill>/SKILL.md` — one directory per canonical skill.
- `plugins/p/bin/` — POSIX `sh` or stdlib-only Python 3. No build step, no
  dependencies, no compiled artifacts. A tracked file is executable in the git
  index exactly when it starts with a shebang; `p-validate` enforces that.
  `format-gate` and `agy-format-hook` are shell on purpose: a hook must still
  run when no Python is installed. `format-gate` reports a failure as exit 1
  with empty stdout; `agy-format-hook` always exits 0, prints `{}` and one line
  on stderr.
- `plugins/p/hooks/hooks.json` — hook wiring for Claude Code and Codex session
  events; both events enter through `bin/format-gate`, and commands reference
  plugin files via `${CLAUDE_PLUGIN_ROOT}`. Antigravity reads hooks only from
  `plugins/p/hooks.json`, a separate file with its own events.
- `plugins/p/style/` — the response-format payloads the format hooks print.
- `plugins/p/lib/` — Python modules shared by several `bin/` scripts and copied
  beside the bundled status line renderers.
- `plugins/p/renderer/` — the status line renderers `statusline-ctl` installs
  into harness settings.
- `plugins/p/profiles/` — JSON data read by scripts: skill-activation and
  status line profiles, and the evaluation catalogues.
- `plugins/p/retro_eval/`, `plugins/p/rubrics/`, `plugins/p/ui/` — the optional
  local evaluation layer (package, versioned rubric data, annotation page),
  described in `plugins/p/EVALUATION.md`. Only this layer may use the optional
  dependencies in `plugins/p/requirements-eval.txt`. Dependencies run one way:
  `bin/retro.py` imports the package (the redaction and user-turn rules both
  share live in `retro_eval/text_rules.py`), and the package never executes a
  script.
- `plugins/p/commands/<command>.md` — Claude compatibility adapters,
  namespaced as `/p:<command>`, which forward to matching canonical skills.
  Only the seven behaviours that began as slash commands have one; a new skill
  needs none, because Claude Code already lists every skill as `/p:<skill>`.
  `p-validate` checks that each adapter forwards to its skill with
  `$ARGUMENTS`. In a command adapter or a hook command, refer to plugin files
  as `${CLAUDE_PLUGIN_ROOT}/…`. In a `SKILL.md`, `<plugin-root>` is the absolute
  path two directories above that file and every program runs as
  `sh <plugin-root>/bin/python-launcher <plugin-root>/bin/<program>`, because
  only Claude Code substitutes the variable. Never write a path under the
  author's home directory.
- Complex cross-harness skills keep canonical policy beside `SKILL.md` in a
  versioned contract. Thin files under `references/` own harness transport;
  helpers under `scripts/` own deterministic computation without duplicating
  the policy.
- `plugins/p/tests/` — stdlib `unittest`, no runner or dependency. Run with
  `sh plugins/p/bin/python-launcher -B -m unittest discover -s plugins/p/tests -t plugins/p/tests`.
- `docs/plans/` — design documents, filename dated.
- A change under `plugins/p` ships with a version bump, equal across the three
  manifests and the marketplace entry, and a `CHANGELOG.md` entry under that
  version; `p-validate --base <revision>` fails a pull request without the
  bump. The author tags a release (`vX.Y.Z`) and publishes it; do neither
  unasked.

Scripts in any `bin/` share one exit-code convention: `0` ran clean and flagged
nothing, `1` ran clean and flagged something, `2` could not run.

---

This file is kept identical in substance to `CLAUDE.md`; edit both together.
