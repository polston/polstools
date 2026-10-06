---
name: auditing-a-repo-for-private-data
description: Use before a repository's first push, when adding a remote, when making a repo public, or when auditing an existing repo that may already carry private data — personal paths, real names, emails, machine or network identifiers, harvested history, or credentials. Also use when a file with any of that has already been committed and needs removing from history.
---

# Auditing a repo for private data

Before any other action, run
`<python> <plugin-root>/bin/skill-profile-ctl check auditing-a-repo-for-private-data`.
If it exits 1 or 2, stop and report its output. `<plugin-root>` is the absolute
path two directories above this `SKILL.md`, whose directory is
`<plugin-root>/skills/auditing-a-repo-for-private-data`; take it from this
file's own path, never from the working directory or an environment variable.
`<python>` is `sh <plugin-root>/bin/python-launcher`.

## Overview

A file deleted from the working tree is still in history. A grep of the working
tree therefore proves nothing about a repository. Several places can hold the
data, and a scrub is verified only when every category reads zero in all of
them. Standard Git authorship records -- author, committer and tagger
addresses, and well-formed `Co-authored-by` trailers -- are treated as
published metadata; email addresses anywhere else remain findings.

Prefix-shaped searching is the other trap: a past audit reported "clean" from
credential-shaped prefixes and still pushed live credentials, because the real
ones were bare hex, human-chosen passwords, and plain values in shell
variables. Search for the *data*, not for the shapes credentials usually take.

## Run the auditor

```sh
sh <plugin-root>/bin/repo-privacy-audit -C <repo> [-v] [-k] [-p <pattern>]...
```

Exit 0 means every category read zero in every place; 1 means at least one
hit; 2 means it could not run -- not a repository, an unusable argument, a Git
failure, or a shallow clone, whose missing history cannot be vouched for
(`git fetch --unshallow` first). It reports counts and locations only, never a
matched value. `-v` lists the paths and commit ids behind each count; a path
whose own name matches a category is printed as `<name withheld: ...>`, since
printing it would carry the value into the log. `-p` adds identifiers you know
to hunt for -- pass them on the command line, never in a tracked file.

## The places

Each catches something the others structurally cannot. The auditor sweeps all
of them; the commands show what each one reads.

| Column | Place | Read from |
|---|---|---|
| msgs | Commit messages | `git log --all --format='%B'` |
| trees | Every commit's tree, not just HEAD | each distinct blob in `git rev-list --objects --all`, read once through `git cat-file --batch` |
| patches | Patch hunk bodies, added **and** removed lines | `git log --all --format= -p`, filtered to hunk bodies |
| tags | Tag messages | `git for-each-ref refs/tags --format='%(contents)'` |
| idents | Author, committer and tagger names | `git log --all --format='%an %cn'`; their addresses are counted on the `accepted` line |
| names | Every path that ever existed, swept as text | `git log --all --raw -m --no-renames`, plus the index and working tree |
| work | What is about to be committed | the index, and tracked or untracked-but-not-ignored working-tree files |

Below the table: sensitive filenames ever present under any name -- after a
rename, inside a merge, or untracked -- and the largest blobs.

A `^\+`-filtered patch sweep misses removed values. Commit messages, tags, and
identities are separate inputs rather than duplicated through Git's generated
patch headers. Files with NUL bytes, UTF-16 text among them, are swept as their
extracted text runs rather than skipped.

What it does not read, and says so when present: commits reachable only from a
reflog (not pushed, but still on disk after a manual history rewrite),
submodule content (audit each submodule repository on its own), and embedded
repositories. Ignored files are never read.

**The accepted line.** Every run states how many author, committer and tagger
addresses and how many co-author trailers it accepted. That is this repository's
publication policy, applied to whatever repository is audited. If the audited
repository should not publish its authors' addresses, that count is a finding.

## What to search for

Search by category, not by credential prefix:

- absolute paths from the machine, and any other project's paths or names
- real names, account/machine usernames, email addresses
- LAN addresses, hostnames, MAC addresses, network topology
- session IDs, harvested command or session history, anything built from it
- credentials of any kind: API keys, tokens, passwords, webhook URLs
- **money and plan state**: measured spend, balances, billing or subscription
  tier, usage-credit flags

The auditor's categories cover the mechanical shapes of these: email,
credential (token formats, private-key blocks, authorization headers,
passwords in URLs), Windows and Unix home paths in raw, JSON-escaped and
percent-encoded forms, private IPv4 and IPv6 ranges, MAC addresses,
private-use hostnames, UUIDs, dollar amounts of a thousand or more and
billing fields. `-k` adds `password=`-style assignments, off by default
because configuration code matches it legitimately. Real names, another
project's name and a bare password do not have a shape: pass them with `-p`, and read the rest.

There is no allowlist. A committed list of accepted values is a place to hide a
real one, and a pattern that keeps hitting legitimate content is fixed in the
pattern or made opt-in, with the measurement that justified it. Test fixtures
assemble their example values from parts at run time, so the tracked file never
holds a value its own audit would flag.

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

Same places, plus:

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

State findings as: what category, which place, how many commits. If it is
clean, say which places were swept, that each category read zero, what the
`note:` lines say was not scanned, and how many authorship records were
accepted — a bare "clean" is not a result anyone can act on.

## Removing what you find

Back up before rewriting: `git bundle create <file> --all`. `filter-repo`
deletes the file from the working tree too, so an un-backed-up run loses the
content entirely.

- Drop a file from all history: `git filter-repo --path <p> --invert-paths`
- Scrub literal strings in blobs: `git filter-repo --replace-text <file>`
- **Commit messages need `--replace-message` separately** — `--replace-text`
  rewrites blobs only, and a message-borne leak survives it silently.

Then re-run the auditor. A rewrite is not a scrub until every place reads
zero.

Two adjacent traps: a verification pattern written into a tracked file embeds
the data it hunts; and if the data reached a remote, rewriting local history
does not retract it — the remote's own copies and any forks or caches need
handling separately.

## Common mistakes

**Grepping the working tree.** Proves nothing. The trees, patches and names
columns exist because a deleted file is still in history.

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

All of these mean: run the auditor, and read what it returns.
