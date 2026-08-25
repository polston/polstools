# polstools

Personal tooling bundled as one Claude Code plugin: home/work skill activation,
privacy auditing, workflow analysis and evaluation, response formatting, code
review, aligned status lines, and platform-specific fixes.

```sh
claude plugin marketplace add polston/polstools
claude plugin install p@polstools --scope user
```

Commands and skills use the `p` namespace. Toggle the response format with
`/p:fmt-off` and `/p:fmt-on` in Claude Code, or `$p:fmt-off` and `$p:fmt-on`
in Codex.

Use `/p:work` or `$p:work` to keep repository-publication audits and local
session-history workflows out of the current work session. Use `/p:home` or
`$p:home` to restore the compatibility profile where every skill is enabled.
The `managing-skill-activation` skill reports the effective policy, sets a
global default, and manages individual overrides. Claude's supported statusline
paths show the active session as `p:w` or `p:h`; Codex keeps its native footer
and confirms the selection in the command response.

The optional local evaluation layer is documented in
[`plugins/p/EVALUATION.md`](plugins/p/EVALUATION.md). Its transcripts, labels,
manifests, telemetry, and generated reports remain outside Git under an
operator-selected `RETRO_HOME`.
