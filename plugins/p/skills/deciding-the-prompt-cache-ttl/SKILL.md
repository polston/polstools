---
name: deciding-the-prompt-cache-ttl
description: Use when deciding whether Claude Code's prompt cache should use the one-hour or the five-minute TTL, when a cache-related environment variable is about to be set, or when re-checking that the TTL in force still earns its keep after working habits changed. Measures session history rather than reasoning about it.
---

# Deciding the prompt-cache TTL

Before any other action, resolve the plugin root from this `SKILL.md` and run
`<python> <plugin-root>/bin/skill-profile-ctl check deciding-the-prompt-cache-ttl`.
If it exits 1 or 2, stop and report its output.

## Overview

Whether to run the prompt cache at a one-hour or a five-minute time to live,
decided from what the machine actually did rather than from intuition.

Run it:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/cache_ttl.py" report
"${CLAUDE_PLUGIN_ROOT}/bin/cache_ttl.py" report --days 30
"${CLAUDE_PLUGIN_ROOT}/bin/cache_ttl.py" report --project SUBSTR
"${CLAUDE_PLUGIN_ROOT}/bin/cache_ttl.py" report --json
```

`${CLAUDE_PLUGIN_ROOT}` is how every other skill in this plugin invokes its
script, and it is the only form that resolves when the plugin is installed
rather than run from a checkout.

- `--days N` restricts to the last N days by UTC timestamp; default is the
  whole corpus.
- `--project SUBSTR` restricts to transcripts whose path contains this
  substring, and composes with `--days`. Only the hashed project label ever
  appears in output — never the substring or the directory name it matched.
- `--json` emits the same figures machine-readably, for scripting or a
  scheduled check. The schema is fixed, and its per-project breakdown uses the
  same hashed labels the plain-text report shows. Neither mode ever emits a raw
  directory name.

Exit `0` means the TTL in force is the right one, `1` means it should change,
`2` means it could not run.

### The smaller JSON shape on the four early-return paths

`--json` combined with an outcome that has no verdict to give — no session
directory, no readable transcripts, no main-thread requests in the window, or
every main-thread model unpriced — emits a different, smaller payload than a
full report: `window_days`, a machine-readable `reason` code, and
`keep_current_ttl: null` in place of a real verdict. Check for a null
`keep_current_ttl` (or the presence of `reason`) before reading any other key
that only the full shape carries.

## The mechanic that decides it

A cache read refreshes the time to live. So session length is irrelevant — what
matters is the **gap between consecutive requests**. A nine-hour session whose
every pause stays under five minutes gets nothing from the one-hour TTL.

The decision therefore lives in one narrow band: gaps between five minutes and
one hour. Shorter and both policies hit; longer and both mostly miss.

## Reading the output

- **Setting governs N% of read tokens.** Subagents run on the five-minute TTL
  whatever you set, and some models are pinned to five minutes too. The verdict
  applies to the governed share, never to total spend.
- **Cost is cache-related only.** Uncached input and output tokens are excluded.
  They cancel in the comparison but the figure is not a bill.
- **The subagent band table is the validation.** Subagents already run the
  five-minute policy, so their bands show the counterfactual directly. Reads
  should collapse and writes should spike as gaps cross five minutes. If that
  shape ever disappears, the cost model's premise has changed and the verdict
  needs rechecking before it is trusted.

## The procedure

1. Run the report over all history. Read the verdict line last; read the
   "setting governs" line first, because it bounds what the verdict applies to.
2. Check the subagent validation table still shows reads collapsing and writes
   spiking across five minutes. If that shape is gone, stop — the cost model's
   premise has changed and the verdict is not trustworthy until you know why.
3. Check `PRICES_VERIFIED_ON` against the pricing page before quoting a dollar
   figure.
4. Check the unpriced bucket for any model carrying a non-zero token count.
   A `<synthetic>` row with zero tokens is normal and expected — it costs
   nothing and changes no total. A non-zero-token entry means a real model is
   missing from the cost model, so its requests are silently absent from
   both the observed and counterfactual totals.
5. Only then act on the verdict, by setting or clearing the environment
   variable below.

## The knobs

| Variable | Effect |
| --- | --- |
| `FORCE_PROMPT_CACHING_5M=1` | Force five minutes regardless of authentication |
| `ENABLE_PROMPT_CACHING_1H=1` | Opt into one hour on an API key or third-party provider; on a subscription, hold one hour while drawing on usage credits |
| `DISABLE_PROMPT_CACHING=1` | Turn caching off entirely; for debugging only |

On a Claude subscription the one-hour TTL is requested automatically, and drops
to five minutes only while drawing on usage credits.

## Counting rules the tool depends on

Get any of these wrong and the numbers move without looking wrong:

1. **Deduplicate by request id globally, not per file.** Resuming or forking a
   session copies rows, request id and usage intact, into the new transcript.
   Per-file counting double-counts them.
2. **When rows of one request disagree, the settled row wins** — the one with
   the largest total token count. Streaming leaves zeroed placeholder rows.
3. **A request's timestamp is the earliest of its rows.** Rows span minutes,
   enough to move a request across the five-minute boundary.
4. **Walk with `rglob` and split by path depth.** A `*/*.jsonl` glob silently
   drops every subagent transcript.
5. **Order by timestamp, not file position.** Some rows are out of order, and
   trusting file order produces negative gaps.

## Before trusting a dollar figure

Check `PRICES_VERIFIED_ON` in the script against the pricing page. Prices change;
the table does not update itself. Unknown model ids are reported in an "unpriced"
bucket rather than assigned a default, alongside their token counts — a
zero-token entry is an ordinary synthetic row, but a non-zero-token entry means
the table needs a new row.

## Privacy

Output carries counts, dollar figures, and hashed project labels only. Never
paste a run into a tracked file: project directory names on a real machine are
mangled absolute paths that embed the account name and other projects.

## Common mistakes

- **Reading the verdict as applying to your whole bill.** It covers the governed
  share only — subagents and any five-minute-pinned model are outside it, and
  the figure is cache-related cost, not total spend.
- **Treating a narrow ratio as precise.** Grouping gaps by project directory
  instead of by transcript moves the ratio by several points. The report prints
  that sensitivity; read it before quoting a margin.
- **Quoting a dollar figure without checking the price date.** The table does
  not update itself.
- **Comparing two runs as if they were the same measurement.** The corpus grows
  continuously and may be pruned by a retention setting, so counts move in both
  directions between runs. Compare the ratio, not the totals.

## Red flags

- The unpriced bucket has a model with a non-zero token count → a real model
  is missing from the cost model. Zero-token entries are ordinary synthetic
  rows and are not a red flag on their own.
- The subagent validation table has lost its five-minute cliff → the premise
  the counterfactual rests on no longer holds.
- The verdict says "nothing to decide" → the window or filter matched nothing
  priceable; widen it rather than reading that as a result.
- The report names a project directory rather than a hash → stop and fix it
  before the output goes anywhere.
