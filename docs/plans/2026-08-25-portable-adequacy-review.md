# Portable adequacy review

Date: 2026-08-25. Status: candidate committed and validated on the isolated
task branch; local integration awaits explicit approval and publication has not occurred.

## Recommendation

Make the skill the portable entry point, keep review policy in one versioned
JSON contract, and put only native subagent transport in harness references.
A standard-library helper renders canonical prompts, validates native semantic
clusters, and deterministically applies agreement, ranking, and caps.

| Contract | Decision |
|---|---|
| Outcome | Claude Code and Codex execute equivalent blinded ensemble review from one plugin-owned contract. |
| Done when | Focused and full tests, format, installed-copy validation, privacy, diff hygiene, and Codex adequacy review pass on a committed branch. |
| Protected scope | Preserve current review semantics, activation, command compatibility, dependency-free runtime, privacy, and unrelated plugin behavior. |
| Review gate | Present the committed branch, the sealed requirements specification, this progress plan, exact validation, and Codex review evidence before local integration. |

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
2. `scripts/adequacy_review.py` validates the contract, embeds schemas in
   reviewer and distiller prompts, validates semantic clusters, and applies the
   deterministic filtering, ranking, cap, and output rules.
3. `references/claude-code.md` and `references/codex.md` own only native spawn,
   wait, model, semantic-distiller, and result-transfer mechanics.
4. `SKILL.md` detects the active harness, loads exactly one adapter, and never
   restates review policy.
5. The legacy JavaScript workflow is removed after equivalent contract tests
   pass; it is not retained as a second editable recipe.
6. `2026-08-25-portable-adequacy-review-spec.md` is the requirements-only
   review input. This evolving plan is progress evidence and is never passed to
   a cold reviewer.

## Implementation checklist and evidence

| Step | State | Exact evidence |
|---|---|---|
| Recover state and isolate work | Done | Main was clean and synchronized; task branch began at `2e6c053`. |
| Verify current harness capabilities | Done | Official documentation supports skills plus native subagent orchestration in both harnesses. |
| RED: portable contract and drift detection | Done | Initial run: 4 tests with 2 failures and 5 missing-file errors; validator RED: 9 tests with 2 drift-detection failures. |
| GREEN: contract, helper, and adapters | Done | Initial 9 tests passed; successive review-driven RED waves culminated in 14 failures and one error for closed-schema and contract-policy regressions; all 32 focused tests now pass. |
| Installed-copy and release wiring | Done | Source, Claude copy, and Codex copy pass 3/3; README, activation, auto-discovered CI tests, and 1.9.0 metadata agree. |
| Codex adequacy review | Done | Final native K=4 review plus semantic distiller reported zero ensemble-stable findings; three distinct important findings remained contested at 1/4 each. |
| Full validation and commit | Done | Final committed branch: 32 focused and 332 full tests passed with one skip; format 36/36; installed copies 3/3; privacy zero; diff hygiene clean. |
| Skill refinement review | Done | Propose requiring a separate requirements-only sealed review specification and explicit author-context exclusions when an evolving plan accompanies blinded review. |

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
| 2026-08-25 | Codex review 1 | K=4 found schema transport, exact-key consensus, multibyte fallback, and unchecked-disclosure defects at agreement 3/4, 4/4, 4/4, and 2/4. |
| 2026-08-25 | Review-fix GREEN | Embedded schemas, native semantic clustering, Unicode-safe references, unchecked propagation, and complete schema validation pass 13 focused tests and installed copies 3/3. |
| 2026-08-25 | Protocol RED | Requirements-only review input, mandatory unchecked disclosure, visible stable-cap truncation, and nested schema contradiction checks failed 5 assertions and 2 errors. |
| 2026-08-25 | Protocol GREEN | A separate sealed specification plus contract validation and rendering changes pass 17 focused tests and installed copies 3/3; diff hygiene passes. |
| 2026-08-25 | Requirements-only review | Native K=4 reviewers plus one semantic distiller found target-history leakage at 2/4, placeholder drift at 3/4, and reviewer-input drift at 2/4. |
| 2026-08-25 | Stable-fix RED/GREEN | Five failures and eight missing-signature errors preceded explicit exclusions, exact reviewer-count binding, validated templates, and contract-owned rendering; 22 focused tests and installed copies 3/3 now pass. |
| 2026-08-25 | Filtered-target review | Native K=4 reviewers plus one semantic distiller found prompt/ranking policy outside the contract at 3/4 and multiline rendering injection at 2/4. |
| 2026-08-25 | Stable-fix 2 RED/GREEN | Ten failures and five errors preceded packet-file provenance, contract-owned prompt/ranking/verdict policy, nested-shape validation, and single-line text rejection; 24 focused tests and installed copies 3/3 pass. |
| 2026-08-25 | Packet-bound review | Native K=4 reviewers plus one semantic distiller found stale intermediate acceptance and Unicode line-boundary injection, both at 3/4. |
| 2026-08-25 | Stable-fix 3 RED/GREEN | Four failures and sixteen missing-provenance errors preceded request IDs, ordered-review digests, canonical packet reconstruction, supported-schema keyword checks, and Unicode boundary rejection; 26 focused tests and installed copies 3/3 pass. |
| 2026-08-26 | Exact-schema review | Native K=4 reviewers plus one semantic distiller found contract-policy duplication, incomplete contract-shape validation, and permissive result-shape validation, each at 2/4. |
| 2026-08-26 | Stable-fix 4 RED/GREEN | Fourteen failures and one malformed-severity error preceded contract-driven ranking, generic supported-schema enforcement, closed reviewer/distiller objects, exact v1 containers, and nonblank unchecked disclosures; 32 focused and 332 full tests pass. |
| 2026-08-26 | Final Codex review | Native K=4 reviewers plus one semantic distiller reported zero ensemble-stable findings; three distinct important findings were contested at 1/4 each. |
| 2026-08-26 | Draft-goal refinement audit | The evolving evidence plan contaminated an earlier blinded review; a reusable sealed-spec and author-context-exclusion rule is proposed for user approval, not edited automatically. |
| 2026-08-26 | Final validation | Committed branch passed 32 focused and 332 full tests with one skip, format 36/36, installed copies 3/3, privacy zero, and clean diff hygiene. |

## Protected scope

No push, publication, tag, release, live install, dependency, MCP, app, daemon,
credentialed subprocess, host-side reviewed-code execution, private data,
identifying project references, weakened tests, or unrelated behavior changes.
