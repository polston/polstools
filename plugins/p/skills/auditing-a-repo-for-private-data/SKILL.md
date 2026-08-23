---
name: auditing-a-repo-for-private-data
description: Use before a repository's first push, when adding a remote, when making a repo public, or when auditing an existing repo that may already carry private data — personal paths, real names, emails, machine or network identifiers, harvested history, or credentials. Also use when a file with any of that has already been committed and needs removing from history.
---

# Auditing a repo for private data

## Overview

A file deleted from the working tree is still in history. A grep of the working
tree therefore proves nothing about a repository. Six places can hold the data,
and a scrub is verified only when every non-exempt category reads zero. This
repository deliberately treats standard Git authorship records as published
metadata; email addresses anywhere else remain findings.

Prefix-shaped searching is the other trap: a past audit reported "clean" from
credential-shaped prefixes and still pushed live credentials, because the real
ones were bare hex, human-chosen passwords, and plain values in shell
variables. Search for the *data*, not for the shapes credentials usually take.

## The six places

Run all six. Each catches something the others structurally cannot.

| # | Place | Command |
|---|---|---|
| 1 | Commit messages | `git log --all --format='%B'` |
| 2 | Every commit's tree | `git grep <pat> $(git rev-list --all)` — not just HEAD |
| 3 | Full patch text, added **and** removed lines | `git log --all --format= -p` — patch bodies without duplicate commit headers/messages |
| 4 | Tag messages | `git tag -l --format='%(contents)'` |
| 5 | Author/committer metadata | `git log --all --format='%an <%ae> %cn <%ce>'` — email is accepted here by repository policy |
| 6 | Files ever added | `git log --all --diff-filter=A --name-only` |

A `^\+`-filtered patch sweep misses removed values. Commit messages, tags, and
identities are separate inputs rather than duplicated through Git's generated
patch headers.

## What to search for

Search by category, not by credential prefix:

- absolute paths from the machine, and any other project's paths or names
- real names, account/machine usernames, email addresses
- LAN addresses, hostnames, MAC addresses, network topology
- session IDs, harvested command or session history, anything built from it
- credentials of any kind: API keys, tokens, passwords, webhook URLs
- **money and plan state**: measured spend, balances, billing or subscription
  tier, usage-credit flags

That last category is the one an identity-shaped sweep cannot see, and it was
added after a measured-spend total reached a tracked file and every pattern in
the list above read zero. A spend figure is just a number — it carries no name,
no path, and no prefix to match on. What separates it from a published list
price is magnitude, so the auditor thresholds at a thousand: a per-token rate
off a pricing page is public, a four-figure total of someone's actual spend is
not.

Note what this paragraph does **not** contain: an example figure. Writing one in
would put a matching value into the very file that documents the check, which is
the same trap as a verification pattern that embeds the data it hunts. The first
draft of this skill did exactly that, and the auditor caught it.

The lesson generalises past money. When you add a category to an audit, ask what
shape it has that the existing patterns key on — if the answer is "none", the
existing sweep was never going to find it, and a clean report from it proves
nothing about the new category.

Parse structured files (JSON, JSONL, TOML) **as structure**. Grepping them as
flat text misses values that span lines or sit behind escaping.

## Auditing an existing repo

Same six places, plus:

1. `git log --all --diff-filter=A --name-only | sort -u` and read the whole
   list. Look for fixtures, corpora, dumps, `.env` files, anything sized like
   data rather than code.
2. Check the largest blobs ever committed, not just current files:
   `git rev-list --objects --all` piped through `git cat-file --batch-check`,
   sorted by size. Harvested data is usually the biggest thing in the repo.
3. If the repo has a remote, the audit's verdict applies to the remote too —
   private does not undo publication. Say so plainly in the report.

## Reporting

**Never print a found value into the conversation.** Name the service or the
category and truncate. The transcript is itself a file that travels.

State findings as: what category, which of the six places, how many commits.
If it is clean, say which six checks were run, that each non-exempt category
read zero, and how many authorship records were accepted — a bare "clean" is
not a result anyone can act on.

## Removing what you find

Back up before rewriting: `git bundle create <file> --all`. `filter-repo`
deletes the file from the working tree too, so an un-backed-up run loses the
content entirely.

- Drop a file from all history: `git filter-repo --path <p> --invert-paths`
- Scrub literal strings in blobs: `git filter-repo --replace-text <file>`
- **Commit messages need `--replace-message` separately** — `--replace-text`
  rewrites blobs only, and a message-borne leak survives it silently.

Then re-run all six checks. A rewrite is not a scrub until they read zero.

Two adjacent traps: a verification pattern written into a tracked file embeds
the data it hunts; and if the data reached a remote, rewriting local history
does not retract it — the remote's own copies and any forks or caches need
handling separately.

## Common mistakes

**Grepping the working tree.** Proves nothing. Place 6 exists because a
deleted file is still in history.

**Reporting clean from a shape search.** `ghp_`, `sk-`, `AKIA` find the easy
ones. Bare hex and plain values in variables are the ones that got pushed.

**Flagging a file you have not opened.** State findings from the file's
contents, never from its name or its provenance.

**Treating "it's a private repo" as mitigation.** It is on someone else's
server either way.

## Red flags

- "It's just config" / "it's only a fixture"
- "I already deleted that file"
- "The prefix scan came back clean"
- "It's private, so it's fine"

All of these mean: run all six places, and read what they return.
