---
name: checking-branch-base-before-a-pr
description: Use before cutting a branch that will become a pull request, and again before opening or merging one. Also use when a PR's diff lists files you never touched, or when a squash merge swallowed more than the change it was named for.
---

# Checking a branch's base before a PR

## Overview

A pull request diffs against the **remote** base, not your local one. If local
`main` is ahead of `origin/main`, every unpushed commit rides along in the PR
and gets swallowed by the squash — under the title of your one-line fix.

This is silent. The branch is correct, the tests pass, the merge succeeds, and
the remote history ends up mislabeled.

## The check

Two numbers, before branching and again before opening or merging:

```bash
git fetch origin
git rev-list --count origin/main..main   # ahead:  unpushed local commits
git rev-list --count main..origin/main   # behind: unpulled remote commits
```

**Ahead is not zero → stop.** Do not branch yet. Two ways forward:

1. Push local `main` first, so the PR base is current. Correct when the
   unpushed commits are meant to be on the remote anyway.
2. Branch off the remote instead: `git rebase --onto origin/main <base>`, so
   the branch carries only the intended commit.

**Behind is not zero →** pull or reset before branching, or the branch starts
from a stale tree.

Substitute the repository's actual default branch for `main`.

## Why it stays true

Some repositories are routinely ahead: local commits that are notes, specs, or
plan documents which were never meant to be pushed. There is no state where
"they're probably in sync" is a safe assumption — the check is two commands and
it is cheap every time.

## The symptom, after the fact

A PR whose diff stat lists files you never touched. That is a stale base, not a
tooling glitch. Stop and re-check before merging — after a squash there is one
commit and the original boundaries are gone.

## Common mistakes

**Checking only before the merge.** By then the branch is already based on the
stale ref. The check that matters is the one before branching.

**Using `git pull --ff-only` to sync after a squash merge.** It fails when
local is ahead. After a squash, sync with `git fetch origin && git reset --hard
origin/main` — the local commits' content is already inside the squash commit
on the remote.

**Assuming a fresh clone is in sync.** It is, at clone time. It stops being so
the first time you commit locally without pushing.

## Red flags

- "I just cloned it, it's fine"
- "I'll check when I open the PR"
- "The diff looks big but that's probably the formatter"

All of these mean: run the two counts.
