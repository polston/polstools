# polstools

A local plugin marketplace: skills, commands, and scripts shared across AI
coding harnesses from one repository. Everything committed here is published to
a git remote — write accordingly.

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

One deliberate exception: the author's name and address in commit metadata are
published with this repository on purpose, and are not to be scrubbed. A
privacy scan will flag them every time; that is a known accepted hit, not a
finding. Nothing else in this section has an exception.

`plugins/core/bin/repo-privacy-audit` catches the mechanical cases. It cannot
recognise a project name, so that half is on the writer.

## Commit messages never go through a double-quoted shell string

Backticks and `$(…)` inside `git commit -m "…"` execute and paste their output
into repository metadata. Use `git commit -F -` fed by a single-quoted
heredoc, or write no backticks at all. Read the message back afterwards —
substitution is silent, and nobody re-reads metadata.

## Layout and conventions

- `.claude-plugin/marketplace.json` lists every plugin; each plugin also has its
  own `plugins/<name>/.claude-plugin/plugin.json`. Both must stay in step.
- `plugins/<name>/skills/<skill>/SKILL.md` — one directory per skill.
- `plugins/<name>/bin/` — POSIX `sh` or stdlib-only Python 3. No build step, no
  dependencies, no compiled artifacts.
- `plugins/<name>/hooks/hooks.json` — hook wiring, for plugins that act on
  session events; commands reference plugin files via `${CLAUDE_PLUGIN_ROOT}`
  (the `format` plugin prints payload files kept under `style/`).
- `plugins/<name>/commands/<command>.md` — slash commands, one file per
  command, namespaced as `/<plugin>:<command>`.
- `docs/plans/` — design documents, filename dated.

Scripts in any `bin/` share one exit-code convention: `0` ran clean and flagged
nothing, `1` ran clean and flagged something, `2` could not run.
