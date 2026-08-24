---
name: writing-goals
description: Draft or review bounded autonomous /goal contracts for Claude Code, Codex, or another harness, especially for unattended work and goals that churn or stop early.
---

# Writing Goals

Before any other action, resolve the plugin root from this `SKILL.md` and run
`<python> <plugin-root>/bin/skill-profile-ctl check writing-goals`. If it exits
1 or 2, stop and report its output.

## Overview

Treat a `/goal` as the contract for one autonomous outcome, not as an
open-ended backlog.

## Route before drafting

Identify the active harness before drafting. Use session context when it already
answers the question; do not ask the operator to identify a known environment.
Read exactly one adapter:

1. Claude Code: `references/claude-code.md`
2. Codex: `references/codex.md`

For another harness, verify its official goal lifecycle and evaluator semantics
before drafting. Apply the shared contract below, but do not borrow mechanics
from either adapter.

## Shared contract

Include only fields that control the run:

| Field | Requirement |
|---|---|
| Objective | One coherent outcome, larger than a normal prompt and smaller than an open backlog |
| Read first | Durable files, plans, logs, or context that must ground the work |
| Evidence | Existing or plan-authorized commands, artifacts, or observations that prove the outcome |
| Protected scope | Invariants that prevent weakened tests, reduced scope, or unrelated changes |
| Execution loop | Checkpoints, validation after meaningful changes, and a short progress record |
| Other exits | Exact review gate, blocker evidence and resume check, and a primary bound |
| Handoff | Final artifacts, validation results, and unresolved items |

## Shared rules

1. Derive evidence and artifacts from the repository or an authoritative plan.
   Verify that a referenced plan exists and read its verification section.
2. Never invent a wrapper command or success phrase solely to simplify goal
   evaluation. For greenfield work without validation, establish the validation
   contract first.
3. Keep one finish line. If two conditions can complete independently, they are
   separate goals. Necessary supporting work stays inside the selected outcome.
4. Replace vague outcomes with countable evidence. Every unattended goal has a
   primary turn or time bound. Ask for a missing threshold that would materially
   change scope; do not silently invent one.
5. Distinguish completion from `Review needed`, a genuine blocker, and a reached
   bound. A bound stops an incomplete run; it does not make the outcome complete.
6. A blocked exit names the exact failure, relevant attempted remedies, missing
   authority or external state, and the check that resumes work.
7. Preserve completed work across pauses, compaction, and handoffs. Do not expose
   a harness-internal difference when the operator need not act on it.
