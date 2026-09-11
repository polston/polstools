---
name: auditing-agent-contracts
description: Diagnose rejected, overlapping, or unusable delegated work by auditing effective agent briefs, scope, inputs, and parent use. Use when reviewing agent definitions or recurring delegation problems.
---

# Auditing agent contracts

Before any other action, resolve the plugin root from this `SKILL.md` and run
`<python> <plugin-root>/bin/skill-profile-ctl check auditing-agent-contracts`.
If it exits 1 or 2, stop and report its output.

Audit the dispatched task and the authority it actually received. A named
agent definition can be overridden by the caller, session instructions, tool
permissions, or workspace assignment. More agent names are not themselves an
improvement. This skill diagnoses and proposes; it does not grant permissions
or authorize configuration changes.

## Select evidence

Start with the caller's concrete failed or questionable delegation. For an
existing definition, find a representative actual dispatch that uses it. If
only the definition is available, review its stated contract and leave runtime
behavior unknown.

Before reading session history or history-derived packets, run:

```text
<python> <plugin-root>/bin/skill-profile-ctl check-capability local-session-history --component auditing-agent-contracts
```

If unavailable, continue a supplied synthetic or static-definition review and
state the missing history coverage. Do not change activation to bypass the gate.

For historical selection, `bin/retro.py subagents --days 30` provides a
Claude-only mechanical lens. Counts select candidates; they do not establish
the cause or whether a guard should change. Keep main and child populations
separate, exclude the running audit, and report source and date coverage.

Use existing redacted packets, or the registered adapter's
`private_tool_evidence(path, root, redactor)` through the helpers described in
`<plugin-root>/retro_eval/private_evidence.py`. Select the relevant source files
before extraction rather than collecting the whole corpus again. Keep packet
content and its source fingerprint outside Git. Read redacted evidence, not raw
transcript text. Current excerpts are bounded: omitted prompt context, absent
parent-child links, or redacted paths may prevent a finding. In particular,
identical redaction placeholders cannot prove two filesystem locations match.

## Recover the effective contract

Record each item with a supporting evidence reference or `unknown`:

| Item | What must be established |
|---|---|
| Objective | The bounded result requested of this delegation |
| Authority | Allowed read roots, allowed write roots, workspace, and tool permissions |
| Inputs | Required artifacts, their availability, and dependencies |
| Evidence | What observations or checks the result must contain |
| Return | Required shape, destination, and completion/stop conditions |
| Parent use | How the parent intended to consume the result and what it actually did |
| Provenance | Effective definition, caller overrides, applicable instruction version, source, and time bounds |

Do not fill a missing historical field from today's definition. An isolated
workspace is a location constraint, not blanket authority to execute anything.

## Diagnose the observed consequence

- **Legitimate enforcement:** a prohibited target or operation was refused.
  Keep the guard; remedy the dispatch or supply an authorized input.
- **Missing authorized input:** the job needs evidence it could not access.
  Prefer supplying the bounded artifact over expanding filesystem authority.
- **Malformed call or transport:** the interface rejected the request before
  the intended operation. Correct that interface use, not the permission policy.
- **Excess authority:** a brief or effective permission grants more access than
  its task requires. Name the excess and the proposed narrower contract.
- **Incomplete or unusable result:** terminal evidence fails the declared
  return contract. Distinguish an incomplete child result from a parent that
  ignored a sufficient result and repeated the work.
- **Insufficient evidence:** applicable scope, context, linkage, or parent use
  cannot be recovered. Name the exact missing observation and collection step.

Multiple independent causes may coexist. Separate observations from inferred
causes; a guard rejection is not proof of escape, a completed child is not proof
of parent use, and lack of textual overlap is not proof of non-use. Respect the
evaluation catalogue's restrictions on unvalidated behavioral labels.

## Deliver the smallest supported proposal

Write one private audit using [the report template](references/report-template.md).
Name the concrete definition, caller, or transport to change and give replacement
text or an exact edit when evidence supports it. Otherwise name the evidence
needed before such an edit can be specified. Preserve the guard and unrelated
behavior. User questions concern only missing authority or judgments the
available evidence cannot resolve.

Before proposing a new reusable agent, check existing definitions and dynamic
dispatch. A role is justified by repeated requirements it can encode, not a
failure count. Reuse `skills/adequacy-review/contract-v1.json` for that review
workflow; native files must not duplicate its policy. Respect caller model
selection. A proposed read-only role lacks write authority; an execution role
has an explicitly assigned isolated workspace and bounded permissions.

Define the follow-up comparison using matched task families, accepted results,
parent rework, and retained boundary protection. Save the baseline or say it is
unavailable. Do not claim benefit from more dispatches, fewer guard refusals,
or an unevaluated definition. End with the next concrete action and its owner.
