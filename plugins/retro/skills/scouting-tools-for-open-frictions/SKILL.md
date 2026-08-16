---
name: scouting-tools-for-open-frictions
description: Use when looking for tooling that would remove a known, named friction in how work gets done — not for browsing what is new. Also use when a retrospective produced frictions that no local rule can fix, or when evaluating whether a tool already in use should be replaced.
---

# Scouting tools for open frictions

## Overview

Tool scouting has two directions and only one of them works. Searching by
novelty — what shipped this month, what is trending — produces a list of things
to install, each of which costs setup, config, and a slot in working memory, and
most of which solve a problem nobody had. Searching by seam starts from a
measured friction and asks what would close it.

This skill only runs in the second direction. If there is no named open friction,
there is nothing to scout, and the correct output is to say so.

## The procedure

**1. Start from the friction list, not from a search box.** Take open items from
a recent retrospective. If there are none:

```bash
python "${CLAUDE_PLUGIN_ROOT}/bin/retro.py" extract
python "${CLAUDE_PLUGIN_ROOT}/bin/retro.py" pack --days 30
```

Take the top signals from the pack. If nothing is elevated, stop and report that
— an empty scout is a valid result and a cheap one.

**2. Turn each friction into a search that describes the problem.** Search the
symptom and its consequence, not a guessed product category. The category is what
you are trying to discover; assuming it up front narrows the search to what you
already imagined.

**3. Check the local ground first.** Before searching outward, verify the friction
is not already addressed by something installed and unconfigured. A dormant
feature in a tool already present beats anything new — no install, no new
surface. This is the single highest-yield step and it is the one most often
skipped.

**4. Verify every candidate against its own documentation.** Read the actual
docs, not a summary or a listicle. Confirm: it runs on this platform, it is
maintained, and it does the specific thing the friction needs rather than a
neighboring thing.

**5. Cap at three candidates.** Each one carries four fields:

- **Seam** — the exact friction it closes, quoted from the pack.
- **Cost** — install, config, and what it adds to the daily surface.
- **Evidence** — where the capability claim was verified. Cite the doc.
- **Kill criterion** — what observation would mean it is not working, and the
  date to check.

**6. Propose; do not install.** Adoption is a decision with an ongoing cost, and
it is not this skill's to make.

## The kill criterion is the load-bearing part

Tools accumulate. Each one arrived solving something, and without a stated
condition for removal none of them ever leaves, because "it might be useful"
always outranks the diffuse cost of keeping it.

Write the criterion as an observation with a date: "if permission-mode changes
per session have not fallen by the first of next month, remove it." Not "if it
does not help."

A candidate you cannot write a kill criterion for is one whose benefit you cannot
describe. Drop it at that point, before the install.

## Common mistakes

**Scouting without a friction.** Produces a shopping list. Every item will look
reasonable and none of them are answering anything.

**Reporting a tool's own marketing as a capability.** A project page describes
intent. The docs describe behavior, and only sometimes. Check which one you read.

**Ignoring the thing already installed.** Configuring what is present is nearly
always cheaper than adopting what is not.

**Recommending a replacement for a working tool on the basis that the new one is
newer.** Migration cost is real and the friction was never about age.

**More than three candidates.** A list of nine gets skimmed and nothing is
adopted.

## Red flags

- "Here's what's new in..." — wrong direction, restart from the friction
- A candidate with no named seam
- A kill criterion phrased as a feeling rather than an observation
- Any recommendation where the docs were never opened
