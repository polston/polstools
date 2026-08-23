# Response-format history audit

Date: 2026-08-23. Repository state: `f4896ac`.

This audit asks whether the response-format rules improve real replies, not
whether their hook wiring merely runs. It began as a proposal; the same change
set now applies its three response-contract refinements, maintenance guidance,
semantic tests, design-document repair, and version 1.2.0 catalog update.

No transcript quotation, project name, project path, session identifier, or
other private history data is reproduced here.

## Implementation status

- Applied: reserve the full triage block for turn-ending replies.
- Applied: five top-level items, 30-word substantive lines, and a conditional
  separator.
- Applied: reader impact and recommendations for problems, ASKS-only reader
  questions, and scoped persistent numbering.
- Applied: behavioral maintenance guidance, semantic contract tests, current
  layout documentation, and synchronized 1.2.0 manifests.
- Deferred outside this change: one aggregate-only cross-harness history
  analyzer; the existing retrospective parser remains Claude-specific.

## Evidence and limits

### Claude Code

The seven-day retrospective pack covered 45 main sessions and surfaced 29
quoted correction candidates in its highest-friction moments. Manual review
kept 14 as genuine response-shape evidence: repeated demands for shorter
numbered replies, questions that had become hard to locate, internal statements
whose user impact was unclear, and problem reports that offered no solution.

That history cannot measure current-format compliance. The consolidated `p`
plugin was installed after the newest Claude transcript in the corpus, and
Claude transcripts do not persist hook-added context. Pre-install replies are
therefore a preference baseline, not formatter failures.

### Codex

The directly observable cohort excluded the running audit session and a session
that deliberately exercised the off toggle. It contained two prior active
sessions, four user turns, and 39 user-visible assistant messages:

- 37 messages were interim commentary and two were turn-ending answers.
- All 39 began with `# FINDINGS`; 37 carried the complete contract.
- The two misses omitted only the trailing horizontal rule and had no optional
  detail to place below it.
- The triage block consumed a median 83% of each formatted message: 661 median
  characters above the rule and 135 below it.
- No observed finding was multi-sentence or future intent, and no observed
  problem title or explanation exceeded its word cap.
- No non-empty ask occurred, so recommendation and carry-forward behavior
  remain unmeasured.

This is a small, workload-specific sample. Its conformance results are
illustrative, while the 37-to-two interim/final split and measured shell cost
are sufficient to expose a contract-scope problem.

## Baseline rule audit

| Rule | Verdict | Evidence |
|---|---|---|
| Exact opener | Followed | 39/39 directly observed Codex messages. |
| Header order and required sections | Followed | 39/39 carried all three headers in order. |
| Mandatory horizontal rule | Violated | 2/39 omitted a rule that would have separated nothing. |
| Bold numbering and hierarchy | Followed | No malformed numbered lines in the directly observed cohort. |
| One-sentence findings | Followed | No measured multi-sentence finding. |
| Problem word caps | Followed | No measured title or explanation overage. |
| Roughly seven items per section | Followed but mis-scoped | It permits roughly twenty-one top-level items across the triage block. |
| Recommended answer in asks | Unmeasurable | The cohort contained no non-empty ask. |
| Carry unanswered asks forward | Unmeasurable | No open ask crossed a reply boundary. |
| Keep item numbers stable | Ambiguous | Findings are turn-local, while unresolved problems and asks are session-persistent. |
| Put actionable material above the rule | Incomplete | The contract does not require user impact or a recommended response for each problem. |

## Three implemented refinements

### 1. Reserve the full triage block for turn-ending replies

**What fought us.** A contract designed for final reports dominated short
progress updates; 37 of 39 directly observed messages were interim commentary.

**Original edit proposal.** Add this scope paragraph near the top of
`plugins/p/style/response-format.md`:

> A turn-ending reply uses the complete format below. An interim message that
> immediately precedes a tool call is one concise status sentence: omit the
> triage headers, ask no reader question, and repeat every actionable item in
> the turn-ending reply.

Replace the opening of `plugins/p/style/turn-reminder.md` with:

> For a turn-ending reply, begin with the literal line `# FINDINGS` and follow
> the full triage contract. For interim tool progress, write one concise status
> sentence with no question; the final reply must remain self-contained.

This phase boundary is the expandable seam: new harness message phases can map
to interim or turn-ending behavior without changing the triage schema.

**Metric.** Full-shell commentary should fall from 37/39 observed messages to
zero; final-answer structural adherence should remain 100%.

### 2. Cap the whole triage block and make the rule conditional

**What fought us.** Claude history repeatedly demanded one to five directly
addressable points of at most about 30 words, while the baseline limit applied
separately to three sections.

**Original edit proposal.** Replace the paragraph beginning `A section past ~7 items` in
`plugins/p/style/response-format.md` with:

> Keep at most five non-`None` top-level items across FINDINGS, PROBLEMS, and
> ASKS combined. Keep every finding, problem explanation, recommendation, and
> ask at or below 30 words; keep problem titles at or below 15. Move excess
> evidence below the rule.

Replace the unconditional horizontal-rule sentence with:

> Write `---` only when optional detail follows it. If the triage block ends
> the reply, omit the separator.

**Metric.** No turn-ending reply should exceed five top-level items; the two
observed separator-only conformance misses should cease to be failures.

### 3. Make every problem actionable and every question locatable

**What fought us.** The Claude pack repeatedly showed questions becoming hard
to recover, internal facts lacking reader impact, and problem-heavy replies
lacking solutions.

**Original edit proposal.** Replace the PROBLEMS and ASKS descriptions in
`plugins/p/style/response-format.md` with this contract:

> `# PROBLEMS` contains only issues that still need attention. Each problem has
> `**N. Title**`, `**N.1.**` reader-visible consequence, and `**N.2.**`
> recommended next action. If the reader must decide, `N.2` points to the
> matching ASK. An optional `**N.1.1.**` contains short locator or origin labels,
> not an explanatory sentence.
>
> `# ASKS` is the only place for questions that need a reader answer. Each ask
> is one self-contained question with the recommended answer inline when one
> exists. Unresolved PROBLEMS and ASKS retain their numbers across turns;
> FINDINGS restart at 1 because they describe this turn. Do not reuse a retired
> problem or ask number in the same session.

Also add the compact pointer example `**1.1.1.** worker.py:141; origin: timeout
refactor` to remove the baseline tension between “labels only” and the
prose-like example.

**Metric.** Every problem should have an `N.2` recommendation, and future
retrospective packs should contain no retained correction whose cause is a
buried question, unexplained impact, or problem report without a proposed move.

## Rules deleted or narrowed

1. The implication that every user-visible message needs the full triage shell
   was replaced with turn-ending scope.
2. The unconditional separator became conditional on following optional
   detail.
3. The per-section `~7` threshold became five top-level items across the
   complete triage block.
4. Number stability now applies only to open PROBLEMS and ASKS; turn-local
   FINDINGS restart at 1.

## Skill and measurement refinements

No skill should be retired. The existing workflow audit, recent-friction, and
format-maintenance skills each covered a distinct part of this review.

The description of `plugins/p/skills/maintaining-the-format-plugin/SKILL.md`
now reads:

> Use when the p response format misbehaves, when auditing its usefulness or
> compliance across Claude Code and Codex histories, or after editing its
> wiring or payloads. Separate interim from turn-ending replies and mechanical
> health from behavioral evidence.

Its behavioral-health section now requires:

1. Exclude the running audit session and deliberately toggled-off intervals.
2. Report interim and turn-ending messages separately.
3. Measure exact opener, section order, item shape, above-fold size, and open
   problem/ask continuity.
4. Do not infer Claude noncompliance unless a transcript postdates the installed
   plugin; hook context is not persisted there.
5. Never write transcript text, project identifiers, or raw audit ledgers into
   a repository.

The current `retro.py` parser is Claude-specific. A small `format-audit`
subcommand should add Codex parsing behind a harness adapter and emit aggregate
counts only. Until that exists, cross-harness format audits remain a manual,
non-repeatable procedure.

## Documentation that was false at audit time

Before this change, the present-tense mechanism and layout sections in
`docs/plans/2026-08-19-response-format.md` did not match the implementation:

- `plugins/format/` became `plugins/p/`.
- `/format:off` and `/format:on` became `/p:fmt-off` and `/p:fmt-on` native
  skills.
- State moved from user configuration directories to the operating-system temp
  directory.
- The obsolete interpreter portability caveat no longer describes the direct
  controller wiring.
- The verifier now has 25 checks rather than 19.

This change updates those present-tense sections. The dated rationale and
renderer observations remain historical record rather than being rewritten as
though they were current measurements.
