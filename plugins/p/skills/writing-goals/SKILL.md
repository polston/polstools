---
name: writing-goals
description: Draft or review bounded autonomous /goal contracts for Claude Code, Codex, or another harness, especially for unattended work and goals that churn or stop early.
---

# Writing Goals

Treat a `/goal` as the contract for one autonomous outcome, not as an
open-ended backlog.

Before any other action, run
`<python> <plugin-root>/bin/skill-profile-ctl check writing-goals`. If it exits
1 or 2, stop and report its output. `<plugin-root>` is the absolute path two
directories above this `SKILL.md`, whose directory is
`<plugin-root>/skills/writing-goals`; take it from this file's own path, never
from the working directory or an environment variable. `<python>` is `sh
<plugin-root>/bin/python-launcher`.
Quote both paths and write them with forward slashes, also on Windows. If the
check exits 2 because session variables of two harnesses are set, rerun it once
with `P_SKILL_HARNESS` set to this session's harness (`claude`, `codex`, or
`antigravity`).

## Overview

Use this skill to draft a bounded autonomous `/goal` contract or tighten an
existing one that churns, stalls, or stops prematurely. Route to the active
harness adapter first, then apply the shared contract and rules below.

## Route before drafting

Identify the active harness before drafting. Use session context when it already
answers the question; do not ask the operator to identify a known environment.
Read exactly one adapter:

1. Claude Code: `references/claude-code.md`
2. Codex: `references/codex.md`
3. Antigravity: `references/antigravity.md`

For another harness, verify its official goal lifecycle and evaluator semantics
before drafting. Apply the shared contract below, but do not borrow mechanics
from either adapter.

## Shared contract

Include only fields that control the run:

| Field | Requirement |
|---|---|
| Objective | One coherent outcome, larger than a normal prompt and smaller than an open backlog |
| Baseline | *(Optional)* Verified starting measurements or observed defects already established, so the run does not repeat completed discovery |
| Read first | Durable files, plans, logs, or context that must ground the work |
| Evidence | Existing or plan-authorized commands, artifacts, or observations that prove the outcome |
| Protected scope | Invariants, explicit reconciliation with repo standing rules, and boundaries on git or live host-machine mutations |
| Execution loop | Checkpoints, validation after meaningful changes, and a short progress record |
| Other exits | Exact review gate, blocker evidence and resume check, and a primary bound |
| Handoff | Final artifacts, validation results, before/after delta, and unresolved items |

## Shared rules

1. Derive evidence and artifacts from the repository or an authoritative plan.
   Verify that a referenced plan exists and read its verification section. When
   that plan is already listed in `Read first`, reference its exact sections
   instead of re-pasting specification prose into the goal.
2. Never invent a wrapper command or success phrase solely to simplify goal
   evaluation. For greenfield work without validation, establish the validation
   contract first. Keep setup mutations (such as `chmod` or staging) in the
   execution loop, not in `Evidence`, and run commit-dependent checks (privacy
   audits, base-diff validators, index-mode checks) after creating the local
   commit so final metadata is verified.
3. Keep one finish line. If two conditions can complete independently, they are
   separate goals. Necessary supporting work stays inside the selected outcome.
   When phasing work into sequential goals separated by an unresolved operator
   decision, include a short up-front open-decisions block (with concrete
   options) so immediately runnable phases stay unblocked while decision-gated
   phases park cleanly.
4. Replace vague outcomes with countable evidence. Every unattended goal has a
   primary turn or time bound. Ask for a missing threshold that would materially
   change scope; do not silently invent one. When drafting a contract for
   operator review before launch, label proposed turn or time bounds explicitly
   as proposed defaults awaiting confirmation.
5. Distinguish completion from `Review needed`, a genuine blocker, and a reached
   bound. A bound stops an incomplete run; it does not make the outcome complete.
6. A blocked exit names the exact failure, relevant attempted remedies, missing
   authority or external state, and the check that resumes work.
7. Preserve completed work across pauses, compaction, and handoffs. Do not expose
   a harness-internal difference when the operator need not act on it.
8. Reconcile authority boundaries explicitly across both repository and live
   host state. Affirm or temporarily override standing repository rules (such as
   automatic push or release rules during intermediate phases), and guard
   mutations outside git (installed binaries, user-scope skills or configs,
   hooks, state logs) with pre-mutation verification before destructive cleanup.
9. Delegate where appropriate and right-size workers to the task. When the
   active harness supports subagents, delegate independent parallel work or
   context-heavy subtasks (broad codebase or log surveys, isolated verification
   runs), and skip delegation for trivial single-step lookups. Balance cost,
   speed, and reasoning depth: use lightweight, fast worker tiers for
   mechanical, high-volume, or read-only scans, and reserve deeper-reasoning
   tiers for complex architecture, subtle root-cause debugging, or high-stakes
   synthesis. Keep contracts free of harness-specific tool names or model IDs.
