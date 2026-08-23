# polstools

Personal tooling bundled as one Claude Code plugin: privacy auditing, workflow
analysis, response formatting, code review, and platform-specific fixes.

```sh
claude plugin marketplace add polston/polstools
claude plugin install p@polstools --scope user
```

Commands and skills use the `p` namespace. Toggle the response format with
`/p:fmt-off` and `/p:fmt-on` in Claude Code, or `$p:fmt-off` and `$p:fmt-on`
in Codex.
