# Workflow improvement goals

Three sequential autonomous contracts. Each has one independently verifiable
outcome. Complete and record one before activating the next; do not register
the sequence as an open-ended goal. This document drafts the work and does not
itself activate a run.

## Run controls

- **Primary bound: four hours total, approved by the operator.** Record the
  UTC start and deadline in a
  private run record. The allowance covers the whole sequence, including
  investigation, reviews, and validation. Subsequent goals inherit the
  remaining allowance; starting another goal does not reset the deadline.
  No token budget is implied.
- **Private evidence:** resolve the operator's external recommendation report
  and supporting artifacts from the initiating session. Keep their concrete
  locations only in the external run record. Before relying on temporary
  artifacts across sessions, preserve an unchanged copy in the selected
  external work directory and record its fingerprint. If source evidence is
  unavailable, continue synthetic implementation and record the precise
  limitation; do not manufacture a live finding.
- **Read first for every goal:** `AGENTS.md`, `CLAUDE.md`, `README.md`,
  `plugins/p/EVALUATION.md`, `plugins/p/profiles/skill-activation-v1.json`, this
  contract, current Git state, and the preceding goal's checkpoint. Recover
  actual implementation state rather than assuming the recommendation report
  still describes it. Preserve unrelated and pre-existing changes.
- **Execution loop:** establish the baseline and a short checklist; implement
  one meaningful increment; run the relevant checks; record changed files,
  exact evidence, remaining work, and the next action. Keep private run state
  outside Git. Resume from the last verified checkpoint after interruptions
  or compaction. Run the full validation contract at each goal's completion,
  and repeat only checks invalidated by subsequent changes.
- **Review:** inspect the final diff and apply `finding-what-a-change-made-false`
  to affected documentation. Use `adequacy-review` for substantive runtime
  changes under its canonical policy, with a sealed requirements-only packet
  and author-context exclusions. Resolve important or critical findings and
  assess disclosed unchecked behavior before marking the goal complete.
  Review work stays within the deadline and the active session's authority.
- **Shared protected scope:** preserve privacy boundaries, source attribution,
  metric versioning, evidence eligibility, calibrated-label restrictions,
  safety guards, and existing working behavior. Do not lower tests, thresholds,
  or acceptance to obtain a pass. Runtime remains POSIX shell or stdlib Python
  without a build step. Use synthetic fixtures; never commit harvested history,
  private identifiers, other projects' names, or local evidence paths. Do not
  change global instructions, install the plugin, publish, merge, or deploy
  under these contracts. Keep any touched harness metadata synchronized.

### Validation contract

Use the existing commands from `README.md`, from the repository root:

```sh
sh plugins/p/bin/python-launcher -B -m unittest discover -s plugins/p/tests -t plugins/p/tests
sh plugins/p/bin/python-launcher -B plugins/p/bin/format-e2e
sh plugins/p/bin/p-validate
sh plugins/p/bin/repo-privacy-audit -C .
git diff --check
```

For stopped-promises changes, also run:

```sh
sh plugins/p/bin/python-launcher -B plugins/p/bin/stopped-promises.py --selftest
```

Add focused regressions to the existing unittest suite for changed runtime
contracts. No new wrapper or magic success phrase is needed. Record exit codes
and skipped or unavailable checks; an unavailable required check is not a pass.
If a pre-existing failure is found, establish its baseline and whether it
invalidates this goal's acceptance before choosing repair or a review exit.

### Terminal states and handoff

- **Complete:** every acceptance condition for the active goal is supported by
  saved evidence, its required validation passes, and no material review
  finding or unchecked acceptance condition remains. Later goals may still be
  incomplete; do not imply otherwise.
- **Review needed:** all independently executable preparation is finished, but
  a specific subjective judgment, authority decision, or required review
  cannot be resolved from available evidence. Deliver the concrete result and
  one precise decision. Do not mark the goal complete. Do not ask again for
  actions already authorized by the session.
- **Blocked:** record the exact failed prerequisite, attempted remedies, work
  preserved, missing input or external state, and an executable resume check.
  Continue unaffected work first. Use the harness's blocked status only under
  its actual lifecycle rules; do not create a looser local substitute.
- **Bound reached:** checkpoint and hand off incomplete work with its next
  executable action. Do not mark complete or blocked solely because time ran
  out. Use available pause controls; otherwise request the operator's pause
  action rather than claiming the harness was paused.
- **Handoff:** give the goal's disposition, artifact paths, validation and
  review results, unresolved evidence, and the exact next action and owner.
  Separate proven tooling behavior from any unproven behavioral benefit.

## Goal 1 — Make workflow measurements safe to interpret

**Objective:** repair the existing retrospective and outcome-reporting path so
that unsupported histories, missing opportunities, and source completion
cannot be presented as evidence of successful work.

**Additional read-first material:** the external recommendation's measurement
repair proposal; `plugins/p/bin/retro.py`,
`plugins/p/bin/stopped-promises.py`, the affected retrospective skills,
`plugins/p/retro_eval/adapters/`,
`plugins/p/retro_eval/deterministic_scorers.py`,
`plugins/p/retro_eval/reporting.py`, and the metric, scorer, and rubric
catalogues under `plugins/p/rubrics/`.

**Work and acceptance:**

1. Correct the retrospective skills' harness coverage and ranking descriptions
   to match execution. Reports identify supported and observed sources,
   eligible main and child populations, exclusions, window or snapshot bounds,
   and unavailable signals. Different source populations are not silently
   compared. Reuse the existing cross-harness evaluation path.
2. Unsupported stopped-promises formats produce an explicit unsupported or
   cannot-run result, never a reassuring zero. Exercise supported, unsupported,
   mixed, and empty input populations with synthetic fixtures, preserving
   existing supported-format behavior and the script exit-code convention.
   Adding a new harness adapter is not required to report honest coverage.
3. Separate structural source completion from verified task outcomes, including
   cost per verified outcome. Verification requires a recoverable done contract
   and evidence satisfying it. Missing evidence yields an explicit abstention
   or unobservable state. Use the correct eligible denominator and version
   changed definitions and derived reports.
4. Demonstrate a task-complete event with explicitly unfinished work cannot
   count as verified success. Exercise missing contract, incomplete evidence,
   and valid contract-bound evidence where the implementation supports it.
   Do not fake a positive outcome from transcript wording; if no authoritative
   positive evidence path exists, verified measurement remains unavailable
   and that limitation is documented and tested.
5. Add an insufficient-evidence verdict to the rule/skill audit. Remove claims
   that never-fired necessarily means a bad trigger, absence of selected
   violations establishes compliance, or every audit must delete rules.
6. Run the corrected reports against an isolated external snapshot when
   available and save source-specific coverage. This smoke run proves report
   behavior, not that a harness or workflow performs better. The complete
   validation and substantive-runtime review requirements pass.

**Finish line:** a reviewable implementation with regressions and a report
demonstrating honest outcome and coverage states. No catalog-wide scorer
implementation, taxonomy promotion, or universal harness support is implied.

**Handoff/next owner:** the executing agent records evidence and remaining
limitations. Goal 2 may start only after this finish line is met and time
remains in the authorized allowance.

## Goal 2 — Make agent-contract problems diagnosable

**Objective:** add a usable agent-contract audit that traces a delegation
problem to the effective dispatch, authority, inputs, and return requirements,
and produces a bounded remedy without weakening guards.

**Prerequisite:** Goal 1 complete.

**Additional read-first material:** the external agent-audit proposal;
`plugins/p/skills/adequacy-review/contract-v1.json` and the active harness's
adapter; the existing subagent lens; evidence collection in
`plugins/p/retro_eval/private_evidence.py`; and relevant authoring skills before
creating or editing a canonical skill.

**Work and acceptance:**

1. Add `plugins/p/skills/auditing-agent-contracts/SKILL.md` with the canonical
   activation gate, required capability coverage, Claude command adapter,
   discovery metadata, and documentation following existing conventions.
2. Its procedure inspects the effective dispatched brief and applicable rules,
   not merely a named agent file. It records objective, allowed read/write
   roots, available inputs, dependencies, evidence, return schema, stopping
   condition, and intended parent use.
3. Distinguish legitimate guard enforcement, unavailable authorized input,
   malformed transport/tool call, excess authority, incomplete result, and
   unknown cause. Guard rejection counts alone cannot justify permissions
   changes or broad claims about agent quality.
4. Produce one end-to-end private audit artifact using available redacted
   dispatch evidence. If it cannot establish the cause, emit an honest
   insufficient-evidence disposition and the exact missing observation.
   Separately exercise synthetic missing-input, forbidden-target,
   unconsumed-result, and fully specified dispatch scenarios. State which
   conclusions come from fixtures and which from real observations.
5. Evaluate whether a native agent definition adds a reusable capability.
   Add or amend a plugin-local definition only when observed dispatch evidence
   supports it; otherwise record the reason for retaining dynamic dispatch.
   Reuse the existing canonical review policy. Any added execution role has
   an assigned isolated workspace; any read-only role lacks write authority.
   Do not pin a model contrary to caller policy or add roles merely to fill
   a directory.
6. Validation proves packaging and activation, relevant boundary protection,
   access to required authorized inputs, and an actionable audit result.
   Runtime helpers, if necessary, receive focused semantic regressions rather
   than tests that merely mirror the skill's prose.

**Finish line:** the packaged audit can produce an evidence-grounded diagnosis
or an explicit missing-evidence result, and its authority protections pass.
Reduced rejection rates and improved parent result use are future experiment
outcomes, not prerequisites that force this goal to wait for new work.

**Handoff/next owner:** the executing agent records the audit and agent-definition
disposition. Goal 3 may start after this finish line and within the remaining
allowance.

## Goal 3 — Make implemented improvements reviewable for actual benefit

**Objective:** add a working improvement-effect review path that follows one
existing proposal from implementation through an evidence-based keep, revise,
revert, or insufficient-evidence disposition and a concrete next action.

**Prerequisites:** Goals 1 and 2 complete.

**Additional read-first material:** the external effect-review proposal;
`plugins/p/bin/retro.py` effect reporting; instruction manifests;
`plugins/p/retro_eval/proposals.py` and related reporting; existing external
evidence and dataset contracts; relevant skill-authoring instructions.

**Work and acceptance:**

1. Add `plugins/p/skills/reviewing-improvement-effects/SKILL.md` with activation,
   capability coverage, command adapter, discovery, and documentation. Extend
   existing proposal/evaluation mechanisms; do not create a second platform,
   backlog, automatic scheduler, or data store.
2. The effect-review record binds an existing proposal identity, activation
   version, task family, baseline, follow-up evidence, owner, intended benefit,
   quality constraints, review trigger, disposition, and next action. Persist
   real records and evidence links only outside Git.
3. Distinguish response completion, verified delivery, later use, reopened
   work, parent rework, and user interventions. Retain unknown observations.
   Include ordinary and successful work alongside failures. An unmatched
   before/after trend cannot establish causality, and unvalidated labels
   cannot be promoted into decision evidence.
4. Demonstrate the complete record-to-review path using synthetic evidence
   covering each disposition, changed source populations, absent activation
   provenance, missing follow-up evidence, and a quality regression despite
   reduced effort. Verify evidence references and the external-only boundary
   in the existing validation suite wherever runtime code changes.
5. Register one reversible local pilot using an eligible change from these
   goals, with a baseline, exact comparison, review trigger, quality guardrails,
   and rollback. If future observations or subjective acceptance are missing,
   produce an insufficient-evidence review naming what must be collected and
   who acts next. Do not fabricate observations or wait for weeks of new work
   to declare the tooling finished. Proposed sample counts are pilot review
   triggers, not statistical significance guarantees.
6. Document when to run the existing friction, rule, stopped-promises, format,
   adequacy-review, and tool-scouting skills. Follow-up questions about value,
   adoption, or priorities remain explicit unknowns when artifacts and session
   evidence cannot answer them. Validation and applicable reviews pass.

**Finish line:** a packaged review workflow, exercised dispositions, and one
real pilot record with an honest current review and resumable next action.
Neither shipping the workflow nor producing a pilot proves that work improved.

**Final handoff/next owner:** the executing agent delivers the three goal
dispositions, implementation diff, validation/review evidence, private pilot
location, and remaining observations or decisions. The pilot record identifies
the actor and evidence trigger for follow-up. Publication and installation
remain separate actions requiring the operator's authorization.
