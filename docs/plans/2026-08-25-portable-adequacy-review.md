# Portable adequacy review

Date: 2026-08-25. Status: implementation in progress on the isolated task
branch; integration and publication have not occurred.

## Recommendation

Make the skill the portable entry point, keep review policy in one versioned
JSON contract, and put only native subagent transport in harness references.
A small standard-library helper renders canonical prompts and deterministically
distills structured findings.

| Contract | Decision |
|---|---|
| Outcome | Claude Code and Codex execute equivalent blinded ensemble review from one plugin-owned contract. |
| Done when | Focused and full tests, format, installed-copy validation, privacy, diff hygiene, and Codex adequacy review pass on a committed branch. |
| Protected scope | Preserve current review semantics, activation, command compatibility, dependency-free runtime, privacy, and unrelated plugin behavior. |
| Review gate | Present the committed branch, this plan, exact validation, and Codex review evidence before local integration. |

## Capability matrix

| Capability | Claude Code | Codex | Contract decision |
|---|---|---|---|
| Portable workflow unit | Plugin skills use `skills/<name>/SKILL.md`. | Plugins package skills from `skills/`. | Keep one canonical skill entry point. |
| Supporting computation | Skills may include scripts and references. | Skills may include scripts and references. | Use one stdlib helper and one JSON contract. |
| Parallel isolated review | Plugin agents and forked skills are native. | Skills may direct project or skill-authorized subagent workflows. | Keep spawning and waiting mechanics adapter-owned. |
| Model choice | Skill or plugin-agent metadata can select a model. | Spawn requests or custom-agent configuration select a model. | Keep model identity out of the canonical contract. |
| Commands | Commands remain supported compatibility skills. | Reusable commands must become skills. | Preserve the thin Claude command adapter only. |
| Plugin-shipped agents | `agents/` is a supported plugin component. | Universal conversion moves reusable agent procedures into skills. | Do not require plugin-shipped agents for core behavior. |

Sources:

1. [OpenAI plugin packaging](https://developers.openai.com/plugins/build/plugins)
2. [OpenAI skill authoring](https://developers.openai.com/plugins/build/skills)
3. [OpenAI Claude-plugin conversion](https://developers.openai.com/plugins/guides/submit-claude-plugin)
4. [Codex subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)
5. [Claude Code plugins](https://code.claude.com/docs/en/plugins)
6. [Claude Code skills](https://code.claude.com/docs/en/slash-commands)
7. [Claude Code plugin reference](https://code.claude.com/docs/en/plugins-reference)

## Contract boundary

1. `contract-v1.json` owns inputs, defaults, prompts, schemas, agreement,
   ranking, caps, contested output, and unchecked-behavior disclosure.
2. `scripts/adequacy_review.py` validates the contract, renders reviewer
   packets, validates structured responses, and distills findings.
3. `references/claude-code.md` and `references/codex.md` own only native spawn,
   wait, model, and result-transfer mechanics.
4. `SKILL.md` detects the active harness, loads exactly one adapter, and never
   restates review policy.
5. The legacy JavaScript workflow is removed after equivalent contract tests
   pass; it is not retained as a second editable recipe.

## Implementation checklist and evidence

| Step | State | Exact evidence |
|---|---|---|
| Recover state and isolate work | Done | Main was clean and synchronized; task branch began at `2e6c053`. |
| Verify current harness capabilities | Done | Official documentation supports skills plus native subagent orchestration in both harnesses. |
| RED: portable contract and drift detection | Done | Initial run: 4 tests with 2 failures and 5 missing-file errors; validator RED: 9 tests with 2 drift-detection failures. |
| GREEN: contract, helper, and adapters | Done | Focused portability suite passes 9 tests, including installed-copy self-check and validator drift cases. |
| Installed-copy and release wiring | Done | Source, Claude copy, and Codex copy pass 3/3; README, activation, auto-discovered CI tests, and 1.9.0 metadata agree. |
| Codex adequacy review | Pending | Stable important or critical findings are resolved or invalidated with evidence. |
| Full validation and commit | In progress | Pre-review: 309 tests passed with one skip; format 36/36; privacy zero; diff check passed. |
| Skill refinement review | Pending | Reusable drafting friction is assessed before completion. |

## Verification contract

```sh
sh plugins/p/bin/python-launcher -B -m unittest discover -s plugins/p/tests -p test_adequacy_review_portability.py -t plugins/p/tests
sh plugins/p/bin/python-launcher -B -m unittest discover -s plugins/p/tests -t plugins/p/tests
sh plugins/p/bin/python-launcher -B plugins/p/bin/format-e2e
sh plugins/p/bin/p-validate
sh plugins/p/bin/repo-privacy-audit -C .
git diff --check
```

## Progress log

| Time | State | Result |
|---|---|---|
| 2026-08-25 | Recovery | Main and origin were zero ahead and zero behind; primary tracked state was clean. |
| 2026-08-25 | Research | Both harnesses support plugin skills with bundled references/scripts and skill-authorized subagent workflows. |
| 2026-08-25 | Design | One data contract and helper selected; adapters contain transport only and new harnesses remain additive. |
| 2026-08-25 | RED | Missing contract surfaces failed first; then mutated adapter and contract copies proved validator drift was undetected. |
| 2026-08-25 | GREEN | Nine focused tests pass with equivalent packets/results, installed-copy self-check, and package-level drift rejection. |
| 2026-08-25 | Installed copies | Source, temporary Claude, and temporary Codex package checks passed 3/3. |
| 2026-08-25 | Pre-review validation | 309 tests passed with one skip; format 36/36; privacy zero; diff hygiene passed. |
| 2026-08-25 | Stale-doc sweep | Current workflow claims in AGENTS.md, CLAUDE.md, and README were replaced; historical plans remained unchanged. |

## Protected scope

No push, publication, tag, release, live install, dependency, MCP, app, daemon,
credentialed subprocess, host-side reviewed-code execution, private data,
identifying project references, weakened tests, or unrelated behavior changes.
