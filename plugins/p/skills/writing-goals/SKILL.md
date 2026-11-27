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
2. Never invent wrapper commands or success phrases solely to simplify
   evaluation; establish validation first for greenfield work. Verify exact CLI
   syntax and flag-separator order (e.g., engine vs. user args around `--`),
   map every declared invariant to a static or runtime check, keep setup
   mutations (`mkdir`, `chmod`, staging) in the execution loop, verify expected
   counts and stdout with final flags, and run commit-dependent checks after
   committing.
3. Keep one finish line. If two conditions can complete independently, they are
   separate goals. Necessary supporting work stays inside the selected outcome.
   When phasing work across an unresolved operator decision, put a short
   open-decisions block (with concrete options) up front so runnable phases stay
   unblocked while decision-gated phases park cleanly.
4. Replace vague outcomes with countable evidence. Every unattended goal has a
   primary turn or time bound, per-command timeouts for external binaries, and a
   per-checkpoint failure retry cap. Ask for missing scope-changing thresholds
   rather than inventing them. Keep pre-launch confirmation notes outside the
   fenced `/goal` block while copying every concrete operational default inside
   so the pasted block stays self-contained and free of drafting annotations.
5. Keep terminal states (`Completed`, `Review needed`, `Blocked`, reached bound)
   mutually exclusive; a reached bound pauses an incomplete run rather than
   completing it.
6. A blocked exit names the exact failure, relevant attempted remedies, missing
   authority or external state, and the check that resumes work.
7. Preserve completed work across pauses, compaction, and handoffs. Do not expose
   a harness-internal difference when the operator need not act on it.
8. Reconcile authority boundaries across both repo and live host state: affirm
   or override standing repo rules (commit policy, auto-push/release) explicitly,
   and require pre-mutation verification before destructive cleanup outside git
   (installed binaries, user configs/skills, hooks, state logs).
9. Right-size subagent delegation for cost, speed, and reasoning: delegate
   independent parallel work or context-heavy scans/verifications to fast
   read-only tiers, reserve deeper reasoning for root-cause debugging or
   architecture, and skip delegation for single-step lookups. Keep contracts
   free of harness tool names or model IDs.
