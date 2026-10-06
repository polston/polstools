# polstools

One `p` plugin for Claude Code, Codex, and Antigravity: home/work skill activation,
repository safety, cross-harness diagnostics and goal design, workflow
evidence, evaluation, response formatting, aligned status lines, and small
platform fixes. Runtime scripts use only POSIX shell or Python's standard
library.

## Requirements

Python 3.9 or newer and a POSIX `sh`. The scripts are started through `sh`; on
Windows that is the one Git Bash provides. They find a suitable Python
themselves through `plugins/p/bin/python-launcher`.

## Install

### Claude Code

```sh
claude plugin marketplace add polston/polstools
claude plugin install p@polstools --scope user
```

### Codex

```sh
codex plugin marketplace add polston/polstools
codex plugin add p@polstools
```

### Antigravity (`agy`)

From GitHub:
```sh
agy plugin install https://github.com/polston/polstools/tree/main/plugins/p
```

Or from a local checkout:
```sh
agy plugin install ./plugins/p
```

Start a new session after installing or changing the plugin. Skills may trigger
from their descriptions; invoke one explicitly as `/p:<skill>` in Claude Code and
Antigravity, or `$p:<skill>` in Codex. Antigravity also accepts `/<skill>`, but
seven names are both a command and a skill there and the short form is
ambiguous for them, so use `/p:<skill>`.

Antigravity reads a plugin's hooks from `<plugin-root>/hooks.json`, so its
response-format hook is declared there, separately from `hooks/hooks.json`.

## Diagnose

Run `/p:doctor` in Claude Code or Antigravity, or `$p:doctor` in Codex. From a development
checkout, compare the live installations with that checkout directly:

```sh
sh plugins/p/bin/python-launcher plugins/p/bin/p-doctor --repo-root .
```

The doctor reports package-metadata drift, installed-version drift, disabled or
obsolete polstools plugins, Python discovery failures, and byte-exact execution
of each harness's live format hook. It is read-only and omits installation paths and raw
command errors.
Exit 0 is healthy, exit 1 found actionable drift, and exit 2 means an available
harness could not be checked.

## Update

Use the plugin-owned updater from a checkout or installed plugin root:

```sh
sh plugins/p/bin/python-launcher plugins/p/bin/p-update
```

It reads every harness (Claude Code, Codex, Antigravity) first, skips one that
has no p, and stops before changing anything if one cannot be read. It then updates each harness that has
p, preserves prior Codex cache snapshots so already-running sessions keep valid
skill paths, and finishes by running the newly installed doctor. Start new
sessions to load the new version.

Antigravity records no install source, so the updater reinstalls it from the
copy another harness now loads. With Antigravity alone, name the plugin
directory. `--dry-run` reads every harness and prints the planned commands
without running them:

```sh
sh plugins/p/bin/python-launcher plugins/p/bin/p-update --dry-run
sh plugins/p/bin/python-launcher plugins/p/bin/p-update --agy-source <repo-root>/plugins/p
```

## Local development

Replace the configured remote marketplace with the checkout root. These
commands change only the harness's local plugin registration; they do not push
or publish the repository.

### Claude Code

```sh
claude plugin marketplace remove polstools --scope user
claude plugin marketplace add <repo-root> --scope user
claude plugin update p@polstools --scope user
```

### Codex

```sh
codex plugin remove p@polstools
codex plugin marketplace remove polstools
codex plugin marketplace add <repo-root>
codex plugin add p@polstools
```

### Antigravity

```sh
agy plugin install <repo-root>/plugins/p
```

Start a new session, then run the doctor against `<repo-root>`.

## Uninstall

Each command removes only the plugin registration.

```sh
claude plugin uninstall p@polstools --scope user
codex plugin remove p@polstools
agy plugin uninstall p
```

Remove a marketplace registration separately with
`claude plugin marketplace remove polstools` or
`codex plugin marketplace remove polstools`. The name Antigravity expects is
the one `agy plugin list` shows.

## Capabilities

| Area | Skills or commands | Purpose |
|---|---|---|
| Plugin health | `doctor`, `update` | Compare every harness's live install, execute each format hook, and update without breaking active sessions |
| Skill activation | `home`, `work`, `managing-skill-activation` | Switch session profiles and manage defaults or overrides |
| Repository safety | `auditing-a-repo-for-private-data`, `checking-branch-base-before-a-pr`, `finding-what-a-change-made-false` | Catch private data, branch-base mistakes, and documentation drift |
| Workflow evidence | `auditing-workflow-rules-against-behavior`, `counting-stopped-promises`, `deciding-the-prompt-cache-ttl`, `finding-friction-in-recent-sessions`, `scouting-tools-for-open-frictions` | Measure recurring friction before changing rules or tools |
| Agent contracts | `auditing-agent-contracts` | Diagnose dispatch scope, missing inputs, unusable results, and parent rework before adding agent roles |
| Improvement follow-up | `reviewing-improvement-effects` | Bind a reversible pilot to comparable evidence, quality constraints, and a keep/revise/revert or insufficient-evidence review |
| Goals and decisions | `writing-goals`, `robust-over-simple` | Bound autonomous work and preserve expandable design seams |
| Response format | `fmt-off`, `fmt-on`, `maintaining-the-format-plugin` | Toggle sessions, set global or per-harness defaults (off by default), and audit the structured response format |
| Interface fixes | `aligning-statuslines`, `shift-enter-in-windows-terminal` | Align harness status information and repair multiline input |
| Evaluation | `reviewing-evaluation-taxonomies` | Resume controlled local taxonomy review |
| Review command | `adequacy-review` | Run portable blinded ensemble review from one versioned contract |
| Statusline commands | `statusline-apply`, `statusline-check`, `statusline-preview`, `statusline-restore` | Apply, inspect, preview, or restore aligned status lines |

## Validate a checkout

The CI workflow runs these commands on Linux, macOS, and Windows under Python
3.9 and 3.14, plus a shell syntax check of the POSIX scripts and the
stopped-promises self-test. Run the same contract locally before integration:

```sh
sh plugins/p/bin/python-launcher -B -m unittest discover -s plugins/p/tests -t plugins/p/tests
sh plugins/p/bin/python-launcher -B plugins/p/bin/format-e2e
sh plugins/p/bin/p-validate
sh plugins/p/bin/repo-privacy-audit -C .
git diff --check
```

`p-validate` checks the three harness manifests and both marketplace entries,
canonical skill adapters, the adequacy-review contract and native adapters,
activation coverage, the root `hooks.json` hook contract, and one relocated copy
of the plugin run from a path other than the checkout. When `agy` is installed
it also runs Antigravity's own validator on a temporary copy under a scratch
home; without `agy` that check is skipped. It does not register, install,
publish, or otherwise change any harness. `p-validate --base <revision>` also
fails when `plugins/p` differs from that revision without a version increase;
a revision the clone does not have exits 2.

Use `/p:work` (Claude Code, Antigravity) or `$p:work` (Codex) to keep
repository-publication audits and local session-history workflows out of the
current work session. Use `/p:home` or `$p:home` to restore the compatibility
profile where every skill is enabled. The `managing-skill-activation` skill
reports the effective policy, sets a global default, and manages individual
overrides. A per-session switch needs the harness's session id in the agent's
shell; on Antigravity the controller looks for `ANTIGRAVITY_CONVERSATION_ID`.
Without a session id, `skill-profile-ctl work` exits 2 with "no supported
harness session id is set", and `skill-profile-ctl use work --global` sets the
default for every session instead.

Activation is advisory on every harness: each governed skill checks its profile
first and stops when told to, but no harness blocks a skill that skips the
check. Only Codex can hide disabled skills from its catalog, through an
explicit `skill-profile-ctl sync-native`.

Claude's supported statusline paths show the active session as `p:w` or `p:h`;
Codex keeps its native footer and confirms the selection in the command
response. An Antigravity plugin cannot register a status line. Check whether
its p renderer is active with
`sh plugins/p/bin/python-launcher plugins/p/bin/statusline-ctl antigravity`;
when it is not, the command exits 1 and prints the `/statusline <command>` line
to type in an Antigravity session.

The optional local evaluation layer is documented in
[`plugins/p/EVALUATION.md`](plugins/p/EVALUATION.md). Transcripts, labels,
manifests, telemetry, and generated reports remain outside Git under an
operator-selected `RETRO_HOME`.

## Session history coverage

`retro.py extract` reads Claude, Codex, and Antigravity CLI transcripts;
`retro.py pack --days 7` includes redacted moments from each source. Set
`RETRO_HOME` to a directory outside every repository before extracting.
Antigravity's data directory defaults to `~/.gemini/antigravity-cli` and can be
overridden for ingestion with `RETRO_ANTIGRAVITY_HOME`. Only one transcript
export per conversation is measured, preferring `transcript_full.jsonl`.

Antigravity session populations are not observable in these exports, and token
usage is not measured: exports do not carry it on every step. Its moments are
explicitly candidate-sampled, not ranked or included in main-session rates. The
separate evaluation adapters read Antigravity exports as well. Packs select
sessions by their start date; an active session is a snapshot, not a completed
outcome.
