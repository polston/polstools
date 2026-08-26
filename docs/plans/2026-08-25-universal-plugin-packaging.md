# Universal plugin packaging

Date: 2026-08-25. Status: integrated and validated on local `main`; publication
remains outside this plan.

## Recommendation

Publish one source tree that both harnesses consume directly: universal Codex
metadata beside the existing Claude metadata, canonical skills for every
invocable behavior, and one repository validator that exercises both source and
temporary installed-copy layouts.

| Contract | Decision |
|---|---|
| Outcome | Package `p` as a current universal plugin while preserving Claude compatibility and existing behavior. |
| Done when | Source and temporary Claude/Codex install copies pass the repository validation contract; manifests agree at version 1.8.0; the task branch is committed and clean. |
| Protected scope | No publication, push, tag, live registration, MCP, app, dependency, private data, or unrelated workflow behavior. |
| Review gate | After green validation and a committed branch, present the plan, diff, and exact results for explicit approval before merging to `main`. |

## Chosen architecture

1. Keep `.claude-plugin/marketplace.json` and
   `plugins/p/.claude-plugin/plugin.json` as the Claude compatibility surface.
2. Add `.agents/plugins/marketplace.json` and
   `plugins/p/.codex-plugin/plugin.json` as the universal packaging surface.
3. Make `plugins/p/skills/<name>/SKILL.md` canonical for all five existing
   command-only behaviors. Keep `plugins/p/commands/<name>.md` as thin Claude
   adapters that forward arguments to the matching skill.
4. Add `plugins/p/bin/p-validate` as a POSIX launcher-backed entry point. Its
   stdlib implementation validates metadata, component coverage, adapter
   wiring, source execution, and temporary Claude/Codex installed copies.
5. Keep runtime hooks in `plugins/p/hooks/hooks.json`; universal metadata does
   not declare unsupported manifest fields and relies on normal component
   discovery.

## Implementation checklist and evidence

| Step | State | Exact evidence |
|---|---|---|
| Recover state and isolate work | Done | `main` was clean at `6615300`; worktree created on `universal-plugin-packaging`. |
| RED: universal metadata and adapters | Done | Focused run: 29 tests; expected 9 failures and 5 errors for absent universal files, adapters, validator, launcher normalization, CI/README wiring, and 1.8.0 sync. |
| GREEN: manifests, skills, adapters, validator | Done | Launcher auto-discovery found Python 3.12.10; all 57 focused packaging, doctor, CI, README, and activation tests passed. |
| Update doctor, README, CI, and release metadata | Done | Dual manifests and marketplaces, doctor metadata checks, portable CI commands, documentation, and version 1.8.0 are synchronized. |
| Full source and installed-copy validation | Done | 292 tests passed with one declared skip; format 36/36; source and two installed copies 3/3; official ingestion validation, privacy, and diff checks passed. |
| Refinement review | Done | Skill refinement: no change proposed; the Goal correctly bounded scope, evidence, review authority, and publication exclusions. |
| Commit and review gate | Done | Commit `2a4f879` was approved and fast-forwarded into local `main` after remote-base counts confirmed 0 ahead and 0 behind. |
| Post-merge validation | Done | On `main`: 292 tests passed with one declared skip; format 36/36; source and installed copies 3/3; official ingestion validation passed. |

## Evidence of success

```sh
sh plugins/p/bin/python-launcher -B -m unittest discover -s plugins/p/tests -t plugins/p/tests
sh plugins/p/bin/python-launcher -B plugins/p/bin/format-e2e
sh plugins/p/bin/p-validate
sh plugins/p/bin/repo-privacy-audit -C .
git diff --check
```

`p-validate` must build fresh temporary install copies representing both
harness layouts and run their portable checks without relying on the source
checkout path. The task branch must then be committed and clean. Integration,
post-merge validation, and worktree removal happen only after explicit review
approval.

## Progress log

| Time | State | Result |
|---|---|---|
| 2026-08-25 | Recovery | Primary checkout clean; no conflicting task branch or target worktree existed. |
| 2026-08-25 | Design | Official universal metadata plus canonical skills and compatibility adapters selected; no MCP or app surface needed. |
| 2026-08-25 | RED | Focused packaging contracts failed only on the intended missing behavior; the launcher also reproduced its Windows-path fallback defect. |
| 2026-08-25 | GREEN | Launcher auto-discovery succeeded without an override; the 57-test focused suite passed. |
| 2026-08-25 | Full validation | 292 tests passed with one declared skip; format 36/36; source plus Claude/Codex installed-copy checks 3/3. |
| 2026-08-25 | External contract | The current official plugin-ingestion validator passed; the repository privacy audit found zero findings across every category and place. |
| 2026-08-25 | Integration | Approval received; local `main` fast-forwarded from `6615300` to `2a4f879` with no unrelated commits or checkout changes. |
| 2026-08-25 | Post-merge validation | On local `main`, 292 tests passed with one declared skip; format 36/36; source plus Claude/Codex installed-copy checks 3/3; official ingestion validation passed. |

## Skill refinement review

Skill refinement: no change proposed. The contract already required an
isolated worktree, TDD, exact source and installed-copy evidence, protected
publication boundaries, a committed review artifact, and post-approval
integration. The Windows launcher defect was implementation evidence rather
than a reusable weakness in `$draft-goal`.

## Sources

1. [Build plugins](https://developers.openai.com/plugins/build/plugins)
2. [Convert a Claude Code plugin](https://developers.openai.com/plugins/guides/submit-claude-plugin)
