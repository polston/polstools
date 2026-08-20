export const meta = {
  name: 'adequacy-review',
  description: 'Blinded, grounded, ensemble code review: catches reinvention, dumb/over-complex choices, subtle spec-mismatch bugs, and doc drift that a surface read misses. Distills to a few ensemble-stable findings + a contested bucket.',
  whenToUse: 'On-demand review of a change or file where you want the rot a quick read hides, judged by independent cold reviewers (not the author), with one-off nitpicks filtered out.',
  phases: [
    { title: 'Review', detail: 'K blinded reviewers, each grounds in the repo + traces inputs', model: 'sonnet' },
    { title: 'Distill', detail: 'keep findings >=2 reviewers agree on, rank, bucket the rest', model: 'sonnet' },
  ],
}

// ── Inputs. Every value reads from `args` first and falls back to the default
//    below, so the workflow runs either way: pass `args` if the harness
//    propagates it to this script, otherwise substitute these defaults and run
//    the script inline. The command file drives that choice. ──
const target = (args && args.target) || 'REPLACE_WITH_TARGET' // a file path, a dir, or a git range like "main...HEAD"
const spec   = (args && args.spec)   || ''  // path to the sealed plan/spec, or '' to infer from the code's own contract
const repo   = (args && args.repo)   || ''  // repo root for grounding; '' = infer from target
const K      = (args && args.k)      || 4   // ensemble size

const REV_SCHEMA = {
  type: 'object',
  required: ['verdict', 'findings'],
  properties: {
    verdict: { type: 'string', enum: ['good', 'has-issues'] },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        required: ['issue', 'severity'],
        properties: {
          issue: { type: 'string' },
          severity: { type: 'string', enum: ['critical', 'important', 'suggestion'] },
          where: { type: 'string', description: 'file:line or function' },
        },
      },
    },
  },
}

const specLine = spec
  ? `SEALED SPEC: ${spec} — this is the reference the code must satisfy.`
  : `No separate spec given — infer the intended behavior from the target's own docstrings, types, names, and tests, and say what you inferred.`

const repoLine = repo
  ? `REPO (for grounding): ${repo}`
  : `Determine the repo root from the target and ground against it.`

phase('Review')
const reviews = await parallel(
  Array.from({ length: K }, (_, n) => () =>
    agent(
      `You are a BLINDED code reviewer. You get only the code and what it is supposed to do — never any author rationale or commit message. Judge it cold, like a stranger opening the PR.\n\n` +
      `TARGET: ${target}\n(If TARGET is a git range, get the diff with \`git diff <range>\`. If a file or dir, read it.)\n` +
      `${specLine}\n${repoLine}\n\n` +
      `Two non-negotiable steps:\n` +
      `1. GROUND — before calling anything reinvented, inconsistent, or dumb, grep/read the repo for existing utilities, conventions, and patterns this code should have reused. Judge against what the repo already provides, not generic opinion.\n` +
      `2. TRACE — do not accept code that merely looks reasonable. Reason through concrete boundary inputs (empty, exact-limit, zero/negative, multibyte) by reading. If you genuinely need to RUN a small test to confirm behavior, you MUST use the context-mode sandbox tool \`ctx_execute\` (isolated, no host filesystem) — ToolSearch for 'ctx_execute' if it isn't loaded. You may NOT use Bash or host \`node\`/\`node -e\` to execute anything; host execution is forbidden in review. The sandbox is the only place code runs.\n\n` +
      `Report real correctness bugs and genuine quality issues with severity + location. Be honest: if it is solid, set verdict=good and invent nothing.`,
      { label: `review:${n + 1}`, phase: 'Review', model: 'sonnet', schema: REV_SCHEMA }
    )
  )
)

phase('Distill')
const r = reviews.filter(Boolean)
const distilled = await agent(
  `Distill ${r.length} independent blinded reviews of the same target into operator-facing output. Do NOT dump the raw reviews.\n\n` +
  `REVIEWS:\n${JSON.stringify(r)}\n\n` +
  `Produce:\n` +
  `1. ENSEMBLE-STABLE findings — those appearing in >=2 reviews — ranked by severity, each with agreement count (e.g. 3/${r.length}) and location. Cap at 5. Multi-reviewer + higher-severity ranks first.\n` +
  `2. CONTESTED / single-reviewer / taste bucket — everything that did not reach >=2 agreement. These are the only items that may need a human judgment call.\n` +
  `3. One honest line on what could NOT be checked (runtime/integration behavior, behavior with no spec anchor, etc.).\n` +
  `If nothing reaches important+ severity with >=2 agreement, say it is clean in one line.`,
  { label: 'distill', phase: 'Distill', model: 'sonnet' }
)

return {
  target,
  reviewer_verdicts: r.map(x => x.verdict),
  raw_finding_counts: r.map(x => x.findings.length),
  distilled,
}
