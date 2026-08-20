# Cache TTL economics — spec

Decide, from measured session history rather than intuition, whether Claude
Code's prompt-cache time-to-live should sit at one hour or five minutes on this
machine. Ship the measurement as a reusable script plus a skill, so the question
can be re-answered when working habits change.

Date: 2026-08-19. Status: spec, revised after review round one.

Figures below come from a corpus snapshot taken 2026-08-20. The corpus grows
while sessions run, so exact totals drift; the script reports its own snapshot
boundary on every run.

## The question, stated correctly

The operator asked whether to raise the cache timeout from five minutes to one
hour. Measurement shows the machine is **already on one hour** for most traffic,
so the live question is the reverse: is there a reason to drop to five minutes?

Two paragraphs of background make the rest readable.

Prompt caching stores the prefix of a request — tools, system prompt, then
conversation history — so the next request re-reads it instead of reprocessing
it. A **write** stores the prefix; a **read** retrieves it. Reads are cheap;
writes cost more than plain input. The **TTL** is how long a cached prefix
survives without being touched, and the API offers exactly two: five minutes and
one hour.

The one-hour TTL costs more per write but survives longer gaps between requests.
So the decision is a trade: pay more on every write, or pay full price to rewrite
the prefix every time a break runs past five minutes.

## Verified facts

| Fact | Value | How it was established |
| --- | --- | --- |
| Authentication | Subscription OAuth, no API key | `oauthAccount` present in `~/.claude.json`, checked key-presence-only; `ANTHROPIC_API_KEY` unset |
| Usage credits | Not enabled, so the automatic drop to five minutes cannot trigger | Checked key-presence-only in the harness account config; the flag name and value stay out of this file |
| Cache env vars | All unset | `ENABLE_PROMPT_CACHING_1H`, `FORCE_PROMPT_CACHING_5M`, `DISABLE_PROMPT_CACHING` absent from environment and from the `env` block of `settings.json`, checked values-blind |
| TTL in force, main thread | One hour for 88.5% of requests | 183,187,805 one-hour write tokens against 18,710,140 five-minute |
| Opus 4.7 is pinned to five minutes | Yes, model-level | Every main-thread five-minute write token comes from `claude-opus-4-7`, which never received a one-hour write on any CLI version. On version 2.1.229, the same version, Fable 5 and Opus 5 wrote one-hour while Opus 4.7 wrote five-minute — so it is the model, not the version |
| Subagents ignore the setting | Yes | 26,658 subagent requests, 145,531,300 five-minute write tokens, **zero** one-hour. Every model that emits one-hour writes on the main thread emits exactly zero as a subagent |
| Knob to force five minutes | `FORCE_PROMPT_CACHING_5M=1` | Claude Code prompt-caching docs |
| Knob to hold one hour on usage credits | `ENABLE_PROMPT_CACHING_1H=1` | Same doc. **Inactive on this account** — with usage credits disabled the auto-drop path cannot trigger |
| 5-minute write price | 1.25× base input | Anthropic pricing page |
| 1-hour write price | 2× base input | Anthropic pricing page |
| Cache read price | 0.1× base input | Anthropic pricing page |
| Reads refresh the TTL | Yes | "Cached prefixes expire after a period of inactivity. Each request that hits the cache resets" it |
| No long-context price tier | Confirmed | "Claude 4.6 and later models... include the full 1M token context window at standard pricing... Prompt caching... discounts apply at standard rates across the full context window." Every model with meaningful volume here is 4.6 or later, so the flat price table is valid |
| No other price modifiers | Confirmed | Every request is `speed: standard`, `service_tier: standard`, `inference_geo: not_available` — no fast-mode or data-residency premium |

Sources, using canonical hosts because `docs.claude.com` 302-redirects to
`platform.claude.com`: [Claude Code prompt
caching](https://code.claude.com/docs/en/prompt-caching), [Anthropic
pricing](https://platform.claude.com/docs/en/about-claude/pricing), [prompt
caching API reference](https://platform.claude.com/docs/en/build-with-claude/prompt-caching).

### The mechanic that decides everything

Because a read refreshes the TTL, session length is irrelevant. What matters is
the **gap between consecutive requests**. A nine-hour session whose every pause
stays under five minutes gets no benefit from the one-hour TTL at all.

So the decision lives in one narrow band: requests whose gap falls between five
minutes and one hour. Faster and both policies hit; slower and both mostly miss.

### What validates the model, and what does not

The main thread shows a sharp collapse in mean read tokens at the 60-minute
boundary. That is real, but it validates only the **observed** policy: it is
consistent with any TTL of an hour or more, and says nothing about how a
five-minute TTL would behave in the 5-to-60 minute band, which is the
counterfactual's only load-bearing premise.

The evidence that does validate it is the **subagent corpus**, which is pinned to
the five-minute TTL and is therefore the counterfactual observed directly rather
than modelled:

| Gap since previous request | Requests | Share with zero read | Mean read tokens | Mean write tokens |
| --- | --- | --- | --- | --- |
| 0–1 min | 23,620 | 0.6% | 134,746 | 3,071 |
| 1–5 min | 1,327 | 0.5% | 150,743 | 7,346 |
| 5–10 min | 73 | 68.5% | 26,381 | 124,566 |
| 10–15 min | 38 | 92.1% | 2,505 | 166,976 |
| 15–60 min | 64 | 92.2% | 1,406 | 168,378 |
| over 60 min | 42 | 95.2% | 763 | 167,525 |

The moment a gap crosses five minutes, reads collapse and writes rise about
40-fold. That is exactly the substitution the counterfactual's miss branch
models, measured rather than assumed. The script reprints this table on every run
so a future change in shape is visible.

## Corpus

Session transcripts are JSONL under `~/.claude/projects/<project>/`. Two splits
matter, and conflating them corrupts the answer:

- **Main thread** — `<project>/<session-id>.jsonl`. Governed by the TTL setting.
  19,953 requests, 183,187,805 one-hour write tokens, 18,710,140 five-minute,
  7,402,606,654 read.
- **Subagents and workflows** — `<session-id>/subagents/**.jsonl`. Pinned to five
  minutes regardless of the setting. 26,658 requests, 145,531,300 five-minute
  write tokens, zero one-hour, 3,397,590,153 read.

Subagents hold 31.5% of all cache-read tokens. They are excluded from the
counterfactual, because no setting change moves them, and used instead as the
validation corpus above.

Combined with Opus 4.7's pinning, **the setting governs about 67% of all
cache-read tokens on this machine.** The report states that share explicitly, so
the verdict is never read as applying to total spend.

### Counting rules

These are not optional; each one silently changes the answer.

1. **Deduplicate by `requestId` globally, across the whole corpus — not per
   file.** 1,452 main-thread request IDs appear in two top-level transcripts
   each, because resuming or forking a session copies recent history, `requestId`
   and `usage` intact, into the new file. Per-file counting double-counts them:
   roughly +7.4% read tokens and +12.8% one-hour write tokens, and an 11% swing
   in the final dollar delta.
2. **When rows of one request disagree, the settled row wins.** Rows are
   streaming snapshots; most repeat identical usage, but 9 main-thread groups
   carry a zeroed placeholder row beside the full one, and 18,382 subagent groups
   disagree on `output_tokens`. Keep the row with the largest total token count.
   First-row-wins corrupts the zero-read shares — it inflated the over-120-minute
   band from a true 5.5% to 10.2% in the pre-review draft.
3. **A request's timestamp is the earliest across its rows.** Rows of one request
   span up to 353 seconds, enough to move requests across the 300-second decision
   boundary in both directions. The earliest row is the request's start, which is
   what the gap measures.
4. **Walk with `rglob`, then split by path.** A `projects/*/*.jsonl` glob matches
   only the 447 top-level files and drops all ~1,513 subagent transcripts.
5. **Order by timestamp, not by file position.** Four main-thread rows appear out
   of order within their own file; trusting file order yields negative gaps.

### The gap's grouping key

Gaps are computed **within one session file**, over requests owned by that file
(owner being the file where the request's earliest row appears). This is stated
because the choice matters: grouping by project directory instead moves the
5–15 minute band's zero-read share from 0.8% to 10.0%, and 244 of 441 session
files overlap in time with another file in the same directory.

Per-session-file is the primary model because the conversation history dominates
the cached prefix and is session-specific. The script reports the per-directory
grouping as a labelled sensitivity line rather than burying the choice.

## Cost model

Per main-thread request *i*, from its deduplicated `usage` block:

- `R` — `cache_read_input_tokens`
- `W1` — `cache_creation.ephemeral_1h_input_tokens`
- `W5` — `cache_creation.ephemeral_5m_input_tokens`
- `gap` — seconds since the previous main-thread request owned by the same file

Let `p1h`, `p5m`, `pread` be that request's model's per-token prices.

**Observed policy (one hour):**

    cost_1h = W1·p1h + W5·p5m + R·pread

**Counterfactual policy (`FORCE_PROMPT_CACHING_5M=1`):**

    gap <= 300s   (still hits)
        cost_5m = (W1 + W5)·p5m + R·pread

    gap > 300s    (misses; prefix rewritten)
        cost_5m = (R + W1 + W5)·p5m

    no previous request (session opener)
        cost_5m = (W1 + W5)·p5m + R·pread

On a miss the rewritten prefix subsumes both the tokens that would have been read
and the increment the request was writing anyway, which is why the miss branch
has no read term. Session openers — one per chain, so roughly 2% of requests
and never more than the number of transcripts carrying priceable requests — take the unchanged
branch; the sensitivity for forcing them all to miss is reported separately and
measures +1.2%.

Both branches omit uncached input and output tokens. They cancel in the delta, so
the comparison is valid, but the figure is **cache-related cost, not total
spend**, and is labelled that way in the output.

### Prices

Keyed by exact model ID string, since prose names do not match transcript values.
USD per million tokens, read directly from the pricing table on 2026-08-19. The
data file records that date and the source URL so staleness is visible.

| Model ID | Base input | 5m write | 1h write | Read |
| --- | --- | --- | --- | --- |
| `claude-fable-5` | 10.00 | 12.50 | 20.00 | 1.00 |
| `claude-opus-5` | 5.00 | 6.25 | 10.00 | 0.50 |
| `claude-opus-4-8` | 5.00 | 6.25 | 10.00 | 0.50 |
| `claude-opus-4-7` | 5.00 | 6.25 | 10.00 | 0.50 |
| `claude-sonnet-5` | 2.00 | 2.50 | 4.00 | 0.20 |
| `claude-sonnet-4-5-20250929` | 3.00 | 3.75 | 6.00 | 0.30 |
| `claude-haiku-4-5-20251001` | 1.00 | 1.25 | 2.00 | 0.10 |

Unknown IDs go to a reported "unpriced" bucket, never a default price. Thirty
requests currently land there, all `<synthetic>` rows carrying zero tokens, so
the output must distinguish "no unpriced requests" from "unpriced requests worth
nothing".

### Measured result

Absolute spend figures are deliberately absent. This repository publishes, and
what an author spent is confidential in a way that request and token counts are
not — counts are already house practice here, money is not. The ratio and the
direction are what the decision turns on; the absolutes are reproducible on
demand by running the tool. `plugins/core/bin/repo-privacy-audit` now has a
`money_amount` category that fails if one comes back.

Running the model over the snapshot:

| Quantity | Value |
| --- | --- |
| Observed one-hour cache cost | measured; absolute figure deliberately not recorded here |
| Counterfactual five-minute cost | measured; likewise |
| Extra cost of switching | about a third again on top of the observed cost |
| Ratio | **1.33×** |
| Requests in the decisive 5–60 minute band | 998 |

**Reads outweigh writes 35 to 1 overall, but that is the wrong number** — 94% of
read tokens sit in the 0–5 minute band and cost the same under both policies. The
decision-relevant ratio is decisive-band reads against all writes, **2.04 to 1**.
The conclusion survives comfortably on 2:1; the earlier draft's reasoning cited a
ratio that was arithmetically true and argumentatively irrelevant.

### Why the answer does not depend on how the subscription meters

In raw token terms the two policies are near-identical, because the miss branch
relabels the same tokens from read to write. This is **true by construction, not
a finding**, and the document says so rather than presenting it as a result.

- **Unweighted metering** — the TTL choice is close to irrelevant.
- **Weighted metering** — the 1.33× above applies.

Neither branch favours five minutes, which is what allows a definitive answer.
An earlier draft also claimed one hour "wins on latency"; that is **not
measurable from this corpus** — zero assistant rows carry a duration field — so
the claim is withdrawn rather than asserted unmeasured.

## Deliverables

### Script — `plugins/retro/bin/cache-ttl.py`

Stdlib-only Python 3, matching `plugins/retro/bin/retro.py`'s conventions:
guarded field access throughout, since transcript shape varies by CLI version.

It **imports `retro.py`** and reuses `redact()`. An earlier draft claimed a
hyphenated filename made this fragile; that was tested and is false — the hyphen
is on the importing file, which is irrelevant to Python's import machinery, and
`import retro` succeeds both from the directory and when invoked by absolute
path. Note `retro.py` has three subcommands (`extract`, `pack`, `skills`), not
two.

    cache-ttl.py report [--days N] [--project SUBSTR] [--json]

- `--days N` restricts to the last N days by UTC timestamp; default is the whole
  corpus. The whole corpus gives 1.33×; the last 30 days gives 1.42×, so the
  window is reported alongside the verdict, and the verdict is always driven by
  the window actually requested.
- `--project SUBSTR` matches against the redacted project label, and composes
  with `--days`.
- `--json` emits the same figures machine-readably. Schema is fixed and carries
  **no project identifiers**.

A request whose in-window gap would reach back before the window boundary is
treated as a session opener; the count of such requests is reported.

Output sections: corpus summary with the main/subagent/pinned-model split and the
governed share; the subagent validation table; the gap-band table; observed
versus counterfactual cost; the verdict with its margin; and the sensitivity
lines.

Exit codes follow the repo convention: `0` ran clean and the current setting is
right, `1` ran clean and the setting should change, `2` could not run — meaning
no projects directory or no readable transcripts. An empty result from `--days`
or `--project` is an ordinary filtered result and exits `0`, not `2`.

The script infers the TTL in force from the observed write split rather than
reading config, because that split is model-conditional and the config may be
unset while the behaviour is still one hour. It reports the inference and the
evidence for it.

Performance is a non-issue: a single-threaded stdlib parse of all ~1,960 files
takes about 4 seconds, so no thread pool or ledger is needed.

### Skill — `plugins/retro/skills/deciding-the-prompt-cache-ttl/SKILL.md`

Covers when to run this, how to read the output, both env vars and which
direction each moves, the reads-refresh-the-TTL mechanic, the subagent corpus as
the validation trick, and the five counting rules.

It contains **no pasted sample run**, because sample output would carry project
labels into a published repository.

Adding a script and a skill to `retro` requires, per the project CLAUDE.md rule
that the manifests stay in step: bump `version` and update `description` and
`keywords` in **both** `plugins/retro/.claude-plugin/plugin.json` and
`.claude-plugin/marketplace.json`. Neither file enumerates skills, so no skill
list needs editing. The current description names three retrospective lenses and
must be widened to cover a configuration-economics lens.

## Privacy

The tool reads a corpus full of machine-identifying data and writes into a
repository that publishes to a public remote. An earlier draft proposed printing
"project-directory basenames" as the *mitigation*; those basenames are mangled
absolute paths that embed the account name, other projects' names, and sometimes
a session UUID — three separately forbidden categories.

Rules, all enforced in the script rather than left to the caller:

1. No message text ever leaves the script.
2. Project labels pass through `redact()` before display. `redact()` alone is not
   sufficient — it rewrites the home directory and username but passes paths
   outside home through verbatim — so labels are additionally reduced to a stable
   short hash unless `--project` was given, in which case only the matched label
   is shown, redacted.
3. The `--json` payload carries no project identifiers at all.
4. No output of this tool is committed to the repository, and the skill carries
   no sample run.

## Non-goals

- No live API querying. The corpus on disk is the whole input.
- No writing to `settings.json`. The tool reports; changing config is the
  operator's call.
- No general session-metrics framework. One question, answered well.
- No per-model recommendation. The TTL setting is global.

## Risks

| Risk | Mitigation |
| --- | --- |
| Prices go stale | Price table carries verified-on date and source URL; skill says to re-check |
| New model IDs appear | Reported "unpriced" bucket, never defaulted; zero-token buckets distinguished from empty ones |
| A future model pinned to 5m gains nonzero 1h writes | The pinned-model split is computed, not hardcoded, and reported on every run |
| Transcript schema shifts | Every field access guarded; skipped requests tallied and reported. A skipped request breaks the gap chain rather than bridging it, so a hole cannot silently invent a long gap |
| Non-temporal prefix invalidation | Compaction, rewind, and model switches break a prefix regardless of the clock. Measured at 0.2% of consecutive pairs, so immaterial here, but the assumption is now stated rather than implied |
| Corpus contains private paths | See Privacy above |

## Known residual uncertainties

Stated rather than hidden, since none of them change the verdict's direction:

- At gaps over an hour the main thread still reads about 24,000 tokens, so the
  all-or-nothing miss branch mildly overcharges the counterfactual. Measured at
  about 1.3% of the delta, and it runs against the conclusion, not for it. An
  earlier draft said 0.4%, which was asserted rather than measured.
- Parallel same-directory sessions could rescue some 5–60 minute gaps under a
  five-minute TTL. Measured at 1.1% of that band's requests and 0.6% of its read
  tokens.
- An earlier draft called the session-opener treatment "deliberately
  conservative" on the grounds that sessions rarely restart within five minutes.
  That is false: of the 180 warm openers, 74.5% of those with a prior
  same-project request were within five minutes, median 53 seconds. The whole
  effect is 0.064% of main-thread reads. The conservatism claim is withdrawn and
  replaced by the measured +1.2% sensitivity.
