# Antigravity plugin packaging and session corpus integration

Date: 2026-09-07. Status: candidate design and implementation.

## Recommendation

Package `p` for Google Antigravity (`agy`) directly from this repository beside
Claude Code and Codex. Introduce a root `plugin.json` manifest conforming to the
official Antigravity specification, update repository validation and health
checks to verify tripartite manifest agreement, update the update tool for
`agy`, and document Antigravity session corpus discovery and local retention
mechanics in all relevant skills.

| Contract | Decision |
|---|---|
| Outcome | Package `p` as a native Antigravity plugin while preserving Claude Code and Codex behavior; point session analysis skills to Antigravity session history; confirm local retention semantics. |
| Done when | `plugins/p/plugin.json` exists; `p-validate` validates tripartite metadata and runs `agy plugin validate`; `p-doctor` inspects `agy` status; session skills document `agy` history; local retention is confirmed indefinite; full test suite, format e2e, validation, and privacy audit pass clean. |
| Protected scope | No publication, push, tag, MCP addition, private data, or breaking changes to Claude Code or Codex runtime behavior. |
| Review gate | Present plan, diffs, and exact validation results for explicit operator approval before integration. |

## Capability matrix

| Capability | Claude Code | Codex | Antigravity (`agy`) | Repository decision |
|---|---|---|---|---|
| Plugin manifest | `plugins/p/.claude-plugin/plugin.json` | `plugins/p/.codex-plugin/plugin.json` | `plugins/p/plugin.json` | Maintain tripartite manifest agreement across all three files at identical version. |
| Marketplace packaging | `.claude-plugin/marketplace.json` | `.agents/plugins/marketplace.json` | Direct directory installation (`agy plugin install ./plugins/p`) | Preserve existing marketplaces; provide root `plugin.json` for direct `agy` ingestion. |
| Skills | Canonical `skills/<name>/SKILL.md` | Canonical `skills/<name>/SKILL.md` | Canonical `skills/<name>/SKILL.md` | Fully shared; `agy` ingests all 26 canonical skills natively. |
| Commands | Thin forwarders in `commands/<name>.md` | Routed via canonical skills | Converted to skills by `agy` on ingestion | Retain existing command forwarders. |
| Lifecycle hooks | `hooks/hooks.json` (`SessionStart`, `UserPromptSubmit`) | `hooks/hooks.json` (`SessionStart`, `UserPromptSubmit`) | `<plugin_root>/hooks.json` (`PreInvocation`, `PreToolUse`, `PostToolUse`, `Stop`) | Keep Claude/Codex hooks in `hooks/hooks.json`. |
| Session corpus | `~/.claude/projects/<project-slug>/<session-id>.jsonl` | `~/.codex/sessions/**/rollout-*.jsonl` | `~/.gemini/antigravity-cli/brain/<conversation-id>/.system_generated/logs/transcript.jsonl` | Document all three locations in session analysis skills. |
| Local history retention | Retained locally under projects directory | Retained locally under sessions directory | Retained locally under `brain/` and `conversations/` indefinitely (no automatic TTL/purge) | Confirm indefinite local retention; advise standard filesystem persistence. |
| Diagnostics & Doctor | `claude plugin list --json` | `codex plugin list --json` | `agy plugin list` | Extend `p-doctor` to probe `agy` when available. |
| Validation | `p-validate` installed copy smoke | `p-validate` installed copy smoke | `agy plugin validate` | Add Antigravity validation to `p_validate.py`. |

## Session corpus and local retention analysis

Antigravity stores session history in three coordinated local tiers under `~/.gemini/antigravity-cli/`:
1. **Transcripts**: `~/.gemini/antigravity-cli/brain/<conversation-id>/.system_generated/logs/transcript.jsonl` (and `transcript_full.jsonl`). Each turn contains JSONL records (`step_index`, `source`, `type`, `status`, `created_at`, `content`).
2. **Conversation Databases**: `~/.gemini/antigravity-cli/conversations/<conversation-id>.db` and the consolidated index `conversation_summaries.db`.
3. **Input Prompt History**: `~/.gemini/antigravity-cli/history.jsonl` (contains timestamps, workspace paths, conversation IDs, and prompt text).

### Retention mechanics
- **No background TTL or purge**: Analysis of the `agy` CLI binary and its storage engines confirms there is no background expiration worker, maximum age pruning threshold, or automatic eviction of past conversation logs.
- **Indefinite persistence**: Local session transcripts and databases persist indefinitely across sessions, system restarts, and CLI updates. They are only removed upon explicit user deletion (e.g., interactive deletion via the `/resume` picker) or manual filesystem deletion.
- **Long-term preservation (1+ year)**: Because files sit in the user's home directory (`~/.gemini/antigravity-cli/`) rather than temporary or cache directories (`/tmp`, `/var/tmp`), operating system cleaners do not sweep them. Retaining them indefinitely requires only preserving the directory during machine migrations or operating system reinstalls.

## Contract boundary

1. `plugins/p/plugin.json` declares the Antigravity plugin manifest. Its `name`, `version`, and `description` must stay strictly synchronized with `plugins/p/.claude-plugin/plugin.json` and `plugins/p/.codex-plugin/plugin.json`.
2. `plugins/p/bin/p_validate.py` inspects all three plugin manifests and exercises `agy plugin validate` on installed copies when `agy` is present.
3. `plugins/p/bin/p-doctor` queries `agy plugin list` when the `agy` executable is discoverable, evaluating install presence, enabled status, and version match.
4. `plugins/p/bin/p-update` supports updating the `agy` installation using `agy plugin install` when `agy` is present.
5. Skills analyzing session history (`finding-friction-in-recent-sessions`, `counting-stopped-promises`, `auditing-workflow-rules-against-behavior`, `deciding-the-prompt-cache-ttl`, `scouting-tools-for-open-frictions`, `maintaining-the-format-plugin`) explicitly document the Antigravity transcript and session database paths alongside Claude Code and Codex.

## Implementation checklist

- [ ] Add root `plugins/p/plugin.json` with synchronized 1.10.0 metadata.
- [ ] Extend `plugins/p/bin/p_validate.py` to validate Antigravity manifest and run `agy plugin validate`.
- [ ] Extend `plugins/p/bin/p-doctor` to evaluate `agy` alongside Claude and Codex.
- [ ] Extend `plugins/p/bin/p-update` to support `agy` plugin installation and updates.
- [ ] Update session corpus skills to document Antigravity session history locations and retention mechanics.
- [ ] Update `AGENTS.md` and `CLAUDE.md` layout descriptions.
- [ ] Run full test suite, format tests, validator, and privacy audit.
