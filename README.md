# polstools

One `p` plugin for Claude Code and Codex: home/work skill activation,
repository safety, goal design, workflow evidence, evaluation, response
formatting, aligned status lines, and small platform fixes. Runtime scripts use
only POSIX shell or Python's standard library.

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

Start a new session after installing or changing the plugin. Skills may trigger
from their descriptions; invoke one explicitly as `/p:<skill>` in Claude Code
or `$p:<skill>` in Codex.

## Diagnose

Run `/p:doctor` in Claude Code or `$p:doctor` in Codex. From a development
checkout, compare the live installations with that checkout directly:

```sh
sh plugins/p/bin/python-launcher plugins/p/bin/p-doctor --repo-root .
```

The doctor reports installed-version drift, disabled or obsolete polstools
plugins, Python discovery failures, and byte-exact execution of both live format
hooks. It is read-only and omits installation paths and raw command errors.
Exit 0 is healthy, exit 1 found actionable drift, and exit 2 means an available
harness could not be checked.

## Update

Claude Code has a direct update command:

```sh
claude plugin update p@polstools --scope user
```

Codex refreshes the marketplace, then reinstalls the package from it:

```sh
codex plugin marketplace upgrade polstools
codex plugin remove p@polstools
codex plugin add p@polstools
```

Start a new session after either update.

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

Start a new session, then run the doctor against `<repo-root>`.

## Capabilities

| Area | Skills or commands | Purpose |
|---|---|---|
| Plugin health | `doctor` | Compare both live installs and execute both format hooks |
| Repository safety | `auditing-a-repo-for-private-data`, `checking-branch-base-before-a-pr`, `finding-what-a-change-made-false` | Catch private data, branch-base mistakes, and documentation drift |
| Workflow evidence | `auditing-workflow-rules-against-behavior`, `counting-stopped-promises`, `deciding-the-prompt-cache-ttl`, `finding-friction-in-recent-sessions`, `scouting-tools-for-open-frictions` | Measure recurring friction before changing rules or tools |
| Goals and decisions | `writing-goals`, `robust-over-simple` | Bound autonomous work and preserve expandable design seams |
| Response format | `fmt-off`, `fmt-on`, `maintaining-the-format-plugin` | Toggle, test, and audit the structured response format |
| Interface fixes | `aligning-statuslines`, `shift-enter-in-windows-terminal` | Align harness status information and repair multiline input |
| Evaluation | `reviewing-evaluation-taxonomies` | Resume controlled local taxonomy review |
| Review command | `adequacy-review` | Run the deterministic blinded review workflow |
| Statusline commands | `statusline-apply`, `statusline-check`, `statusline-preview`, `statusline-restore` | Apply, inspect, preview, or restore aligned status lines |

## Validate a checkout

The CI workflow runs these commands on Windows and Linux. Run the same contract
locally before integration:

```sh
python -B -m unittest discover -s plugins/p/tests -t plugins/p/tests
python -B plugins/p/bin/format-e2e
sh plugins/p/bin/repo-privacy-audit -C .
git diff --check
```

Use `/p:work` or `$p:work` to keep repository-publication audits and local
session-history workflows out of the current work session. Use `/p:home` or
`$p:home` to restore the compatibility profile where every skill is enabled.
The `managing-skill-activation` skill reports the effective policy, sets a
global default, and manages individual overrides. Claude's supported statusline
paths show the active session as `p:w` or `p:h`; Codex keeps its native footer
and confirms the selection in the command response.

The optional local evaluation layer is documented in
[`plugins/p/EVALUATION.md`](plugins/p/EVALUATION.md). Transcripts, labels,
manifests, telemetry, and generated reports remain outside Git under an
operator-selected `RETRO_HOME`.
