# Additional transcript directories (fix 3) — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `retro` measure transcripts from roots other than the single built-in session directory, including gzipped ones, with every ledger row identified by the root it came from.

**Architecture:** A root is a directory laid out like the built-in one. An ordered, de-duplicated list of roots is built from the default root, then a config file in the work directory, then an environment variable. The walk widens to two filename patterns and the reader gains a gzip branch. `measure()` takes the root it was walked from as a parameter, so the transcript path in a row is relative to *that* root and never falls back to a bare filename; the row records a non-default root, and the ledger key becomes the pair (root, relative path). `moments()` resolves back through the row's root and says so on stderr when the file is not there. Sessions that appear under two roots stay two rows; the count of them is printed.

**Tech Stack:** Python 3 stdlib only (`gzip`, `zlib`, `json`, `pathlib`, `os`), no dependencies, no build step. Single file: `plugins/retro/bin/retro.py`.

**Spec:** `docs/plans/2026-08-18-retro-measurement-fixes.md`, section "Fix 3 — additional transcript directories". Read it before starting; this plan argues from it.

## Global Constraints

- **Stdlib only. No dependency, no build step, no daemon.** (spec invariant 7)
- **No other project of the author's is named** — in any file, doc, example, code comment, or commit message. Fix 3 ships a mechanism and nothing else. (spec invariant 1, repo `CLAUDE.md`)
- **No filesystem path belonging to the operator or to another project may appear** in a tracked file, an example, a docstring, a default value, or a commit message. Every path in this plan's commands is a shell variable or a synthetic fixture path, and must stay that way.
- **The ledger contract:** redefining a counter requires `extract --rebuild` before any trend over it means anything. (spec invariant 2) This fix defines no counter and requires no rebuild — see "Ledger compatibility" below.
- **Message text leaves the tool only through `redact()`.** (spec invariant 3)
- **`redact()` does not catch foreign absolute paths** (spec invariant 4). Once an extra root is configured, moments quoted from it can carry that root's paths into a pack. Packs stay in the work directory, are never committed, and are not safe to paste anywhere public without a read-through. Widening redaction is out of scope.
- **No deduplication of sessions that appear in two roots.** (spec non-goals) The overlap is reported, never merged.
- **Exit codes:** `0` ran clean and flagged nothing, `1` ran clean and flagged something, `2` could not run.
- **Verification is read-only against the live corpus.** Never write anywhere under the user's Claude configuration directory. Never run `extract` against the default work directory — always an isolated `RETRO_HOME` under a temp directory.

## Ledger compatibility — why `root` is absent on default-root rows

The spec's done-when requires that "a run with no configuration produces byte-identical output to today". Any always-present new field changes every line of `metrics.jsonl`, so the row records `root` **only when the transcript came from a non-default root**. An absent or empty `root` means the default root. Consequences, all deliberate:

1. A no-extra-roots ledger is byte-identical to one written before this change, and existing ledgers keep working without a rebuild.
2. `row_key()` is `(row.get("root") or "", row.get("transcript") or "")`, which is the same key old rows already had.
3. `--rebuild` starts from an empty row set, so rows belonging to a root that is currently unreachable are lost on a rebuild. That is why an unreachable configured root is reported on stderr rather than silently skipped.

## File Structure

Only one source file changes.

| File | Responsibility after the change |
|---|---|
| `plugins/retro/bin/retro.py` | Adds a "Transcript roots" section (root list, precedence, labelling, row keying), a gzip branch in the reader, a two-pattern walk, and root-aware row assembly and moment resolution. |
| `docs/plans/2026-08-12-retro-design.md` | Swept in Task 4: the metrics-row field list, the `extract` description, and the "Open" bullet about how far back transcripts reach. |

New functions, all in `retro.py`: `default_root`, `root_label`, `row_root`, `row_key`, `config_dirs`, `env_dirs`, `transcript_roots`, `walk_transcripts`, `transcript_stem`, `overlapping_sessions`, `_open_transcript`.

Modified: the module docstring, the import block, the constants block, `read_records`, `measure`, `cmd_extract`, `moments`.

Untouched: `redact`, `text_of`, `tool_calls_of`, `signature`, `classify_user_turn`, `is_error_record`, `parse_ts`, `load_state`, `load_rows`, `totals`, `friction_score`, `cmd_pack`, `installed_skills`, `cmd_skills`, `main`, `COUNTERS`.

## Merge coordination with the sibling fixes

Three sibling fixes are being planned against this same file. This work is not parallelizable within one worktree — the lanes are not file-disjoint, they are all one file — so the four fixes land sequentially and the second-and-later ones are merged by hand. Ordering is the human's call; this table is what that decision needs.

| Site | This fix does | Sibling likely to collide | Severity |
|---|---|---|---|
| `read_records` (HEAD lines 205-221) | Opens `.gz` through `gzip.open`; widens `except OSError` to `(OSError, EOFError, zlib.error)` | **Fix 1** rewrites this function's failure signalling entirely | **High** — the merged version must keep both the gzip branch and all three exception types, or a truncated archive member ends the whole run |
| `measure` signature and docstring (line 224) | Adds a required second parameter `root` | **Fix 1** changes the return contract of the same function | **High** — same line |
| `measure` record loop (lines 236-302) | Nothing | **Fix 2b/2c** and **Fix 4** both rewrite this loop | None textually; it sits between this fix's two edit sites, so expect adjacent hunks |
| `measure` row assembly (lines 304-331) | Replaces the `relative_to(PROJECTS_DIR)` key derivation, adds a conditional `root` key, changes `is_subagent`, changes the session-id fallback | **Fix 4** adds counters to the same row | Low — Fix 4's counters go through the existing `COUNTERS` loop below this block |
| `cmd_extract` (lines 343-394) | Walk, stale-tuple shape, `pool.map` lambda, row keying, ledger sort key, one extra print line | **Fix 1** rewrites the outcome buckets, the `stat()` failure branch, the fingerprint-on-failure behaviour, the printed line, and the return value | **High** — effectively the whole function. Do not let a tool auto-merge these two; re-apply the second by hand |
| `moments` (lines 445-448) | Resolves the path through the row's root; reports a missing file on stderr | **Fix 1** must keep a pack alive when a file is unreadable; **Fix 2c** rewrites the classification inside the loop | Medium at the top of the function, none in the loop body |
| `totals` / `cmd_pack` | Nothing | **Fix 2a** rewrites both | None textually. Data interaction: this fix changes which foreign-root rows carry `is_subagent`, so Fix 2a's per-population numbers move once an extra root is configured |

Two non-textual couplings to state out loud:

1. **The byte-identity check is against the then-current `HEAD`, not against today's.** If Fix 2b, 2c or 4 lands first they redefine counters and change the ledger on purpose; re-take the baseline from the merge base immediately before running the comparison in Task 3.
2. **The stale tuple gains its new element at the end** — `(path, fingerprint, root)` rather than `(root, path, fingerprint)` — specifically so that any `item[0]` / `item[1]` indexing a sibling fix writes against the current shape keeps working.

---

### Task 1: Root-aware rows

Make a row's identity, its `is_subagent` tag and `moments`' path resolution depend on the root the transcript was walked from, rather than on one hardcoded directory with a bare-filename fallback. No configuration yet: the only root is still the default one.

**Files:**
- Modify: `plugins/retro/bin/retro.py` — constants block (after line 40), new "Transcript roots" section (before the `# --- Redaction` header at line 64), `measure` (lines 224, 304-331), `cmd_extract` (lines 350-353, 377-389), `moments` (lines 445-448)
- Test: this repo has no test suite. Checks are throwaway probe scripts under a scratch directory, plus measurement against the live corpus.

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces, for Tasks 2-4 and for the sibling fixes:
  - `default_root() -> Path` — the built-in transcript directory, resolved, cached.
  - `root_label(root: Path) -> str` — `""` for the default root, `str(root)` otherwise.
  - `row_root(row: dict) -> Path` — the directory a row's `transcript` is relative to.
  - `row_key(row: dict) -> tuple[str, str]` — `(root label, relative transcript path)`.
  - `measure(path: Path, root: Path) -> dict | None` — the second parameter is required.

- [ ] **Step 1: Build the fixture corpus**

Two synthetic roots, outside the repo and outside the Claude configuration directory. Write the builder to the scratch directory, never into the repo.

```bash
SCRATCH="${TMPDIR:-${TEMP:-/tmp}}/retro-fix3"
mkdir -p "$SCRATCH"
cat > "$SCRATCH/make_fixture.py" <<'PY'
import gzip, json, shutil, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

base = Path(sys.argv[1]) / "fixture"
if base.exists():
    shutil.rmtree(base)
now = datetime.now(timezone.utc)

def rec(kind, text, minutes_ago, sid=None):
    out = {"type": kind,
           "message": {"role": kind, "content": [{"type": "text", "text": text}]},
           "cwd": "/fixture/project", "gitBranch": "fixture", "version": "0.0.0",
           "timestamp": (now - timedelta(minutes=minutes_ago)).isoformat()}
    if sid:
        out["sessionId"] = sid
    return out

def conversation(sid):
    # A 500-char assistant turn followed by a 15-char reply is a correction by
    # the tool's own thresholds, so every fixture row scores non-zero friction
    # and is eligible to be quoted in a pack.
    return [rec("user", "please do the thing", 30, sid),
            rec("assistant", "x" * 500, 29, sid),
            rec("user", "no, revert that", 28, sid)]

def write(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = "".join(json.dumps(r) + "\n" for r in records)
    if path.suffix == ".gz":
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            fh.write(lines)
    else:
        path.write_text(lines, encoding="utf-8")

a, b = base / "root_a", base / "root_b"
# Same relative path under two roots, different sessions: two rows, not one.
write(a / "proj" / "same.jsonl", conversation("fixture-a-1"))
write(b / "proj" / "same.jsonl", conversation("fixture-b-1"))
# A compressed transcript.
write(a / "proj" / "compressed.jsonl.gz", conversation("fixture-a-2"))
# A subagent transcript under a foreign root, same layout as the default one.
write(a / "proj" / "same" / "subagents" / "sub.jsonl", conversation("fixture-a-1"))
# No sessionId at all: exercises the filename fallback on a compressed file.
write(a / "proj" / "nosession.jsonl.gz",
      [rec("user", "please do the thing", 30),
       rec("assistant", "x" * 500, 29),
       rec("user", "no, revert that", 28)])
# One session id present under both roots: the overlap the report must show.
write(b / "proj" / "shared.jsonl.gz", conversation("fixture-a-2"))
print(a)
print(b)
PY
python "$SCRATCH/make_fixture.py" "$SCRATCH"
```

Expected: two paths printed, six transcript files created — four under `root_a`, two under `root_b`.

- [ ] **Step 2: Write the failing check**

```bash
SCRATCH="${TMPDIR:-${TEMP:-/tmp}}/retro-fix3"
cat > "$SCRATCH/check_task1.py" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1])))          # plugins/retro/bin
import retro

scratch = Path(sys.argv[2])
a = scratch / "fixture" / "root_a"
b = scratch / "fixture" / "root_b"

sub = a / "proj" / "same" / "subagents" / "sub.jsonl"
row = retro.measure(sub, a)
assert row["transcript"] == "proj/same/subagents/sub.jsonl", row["transcript"]
assert row["is_subagent"] is True, row["is_subagent"]
assert row["root"] == str(a), row["root"]
assert retro.row_key(row) == (str(a), "proj/same/subagents/sub.jsonl")

row_a = retro.measure(a / "proj" / "same.jsonl", a)
row_b = retro.measure(b / "proj" / "same.jsonl", b)
assert row_a["is_subagent"] is False
assert row_a["transcript"] == row_b["transcript"] == "proj/same.jsonl"
assert retro.row_key(row_a) != retro.row_key(row_b), "two roots collided on one key"

assert retro.row_root(row) == a
assert retro.row_root({"transcript": "x.jsonl"}) == retro.default_root()
assert retro.root_label(retro.default_root()) == ""
print("task 1 checks pass")
PY
python "$SCRATCH/check_task1.py" plugins/retro/bin "$SCRATCH"
```

- [ ] **Step 3: Run it to confirm it fails**

Run the command above.
Expected: `TypeError: measure() takes 1 positional argument but 2 were given`.

- [ ] **Step 4: Add the constants**

In `plugins/retro/bin/retro.py`, immediately after `STATE_FILE = WORK_DIR / "state.json"`:

```python
CONFIG_FILE = WORK_DIR / "config.json"

# Extra transcript roots come from the config file and the environment; the
# built-in directory is always first. A root is any directory laid out like it:
# project directories holding <session>.jsonl and <session>/subagents/*.jsonl,
# either of which may be gzipped.
ROOTS_ENV_VAR = "RETRO_TRANSCRIPT_DIRS"
TRANSCRIPT_GLOBS = ("*.jsonl", "*.jsonl.gz")
```

- [ ] **Step 5: Add the transcript-roots section**

Insert immediately before the `# --- Redaction ---` header:

```python
# --- Transcript roots ------------------------------------------------------

@lru_cache(maxsize=1)
def default_root():
    """The built-in transcript directory, resolved once so that root equality
    and ledger keys do not depend on how a path happened to be spelled."""
    try:
        return PROJECTS_DIR.resolve()
    except OSError:
        return PROJECTS_DIR


def root_label(root):
    """A root's name in the ledger. The default root is the empty string and
    the key is left out of the row entirely, so a ledger written without extra
    roots is byte-identical to one written before roots existed."""
    return "" if Path(root) == default_root() else str(root)


def row_root(row):
    """The directory a row's `transcript` path is relative to. A row written
    before roots existed carries no `root` and belongs to the default one."""
    label = row.get("root") or ""
    return Path(label) if label else default_root()


def row_key(row):
    """A row's identity: its root, and its path within that root.

    Neither half identifies a row alone. Two roots can hold the same relative
    path, and 21 basenames already repeat inside the default root, so the bare
    filename this used to fall back to loses rows by overwriting them.
    """
    return (row.get("root") or "", row.get("transcript") or "")
```

- [ ] **Step 6: Make `measure` take the root**

Change the signature and docstring at line 224:

```python
def measure(path, root):
    """Reduce one transcript to a metrics row. `root` is the directory the walk
    found it under, and everything about the row's identity derives from it."""
```

Replace the block running from the `# Subagent transcripts live under` comment through the end of the `row` literal:

```python
    # Subagent transcripts live under <session>/subagents/ and carry the PARENT
    # session's id. Keying rows by session id would let them overwrite the
    # parent's row — one row per transcript, tagged, is what aggregates right.
    #
    # The path is relative to the root this transcript was walked from. Taken
    # relative to the wrong root it does not resolve at all, and the old
    # fallback to a bare filename dropped the subagents/ marker with it: every
    # archived subagent then counted as a session and deflated every rate.
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        rel = path.name
    label = root_label(root)

    row = {"transcript": rel}
    if label:
        row["root"] = label
    row.update({
        "is_subagent": "subagents" in path.parts,
        "session_id": session_id or path.stem,
        "project": redact(project or ""),
        "git_branch": branch or "",
        "cc_version": version or "",
        "date": first_ts.date().isoformat() if first_ts else "",
        "duration_s": int((last_ts - first_ts).total_seconds()) if first_ts and last_ts else 0,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cache_read": cache_read,
        "skills_used": sorted(skills),
    })
    for key in COUNTERS:
        row[key] = m[key]
    return row
```

Two notes for the implementer, both measured rather than assumed:

1. `is_subagent` now reads the whole path's components instead of the relative string. Over the 1,920 files of the live corpus the two agree on every file (measured 2026-08-19), and the components version does not depend on the fallback above having worked. A root pointed *at* a `subagents` directory marks everything under it as a subagent transcript, which is the right answer for such a root.
2. `row["root"]` is inserted directly after `transcript`, and only when non-empty, so the JSON key order of a default-root row is unchanged.

- [ ] **Step 7: Key the ledger by root and path in `cmd_extract`**

Three edits inside `cmd_extract`:

```python
        rows = {row_key(r): r for r in load_rows(required=False)
                if "transcript" in r}
```

```python
        for (path, fingerprint), row in zip(
                stale, pool.map(lambda item: measure(item[0], default_root()), stale)):
            if row is None:
                failed += 1
            else:
                rows[row_key(row)] = row
                processed += 1
            state[str(path)] = fingerprint
```

```python
        for row in sorted(rows.values(), key=lambda r: (r.get("date", ""),
                                                        r.get("root") or "",
                                                        r["transcript"])):
```

The `default_root()` inside the lambda is temporary; Task 2 replaces it with the root the file was walked from. The new sort key cannot change today's ordering: with one root every `root` is `""`, and `transcript` is already unique within the row dict.

- [ ] **Step 8: Resolve moments through the row's root, loudly**

Replace the first two lines of `moments`:

```python
    path = row_root(row) / row.get("transcript", "")
    if not path.is_file():
        # Say so. An unresolvable row used to return an empty list, which reads
        # in a pack exactly like a session that had no frictional turns.
        print(f"no transcript on disk for a {row.get('date') or '?'} row: "
              f"{redact(str(path))}", file=sys.stderr)
        return []
```

- [ ] **Step 9: Run the check to confirm it passes**

```bash
SCRATCH="${TMPDIR:-${TEMP:-/tmp}}/retro-fix3"
python "$SCRATCH/check_task1.py" plugins/retro/bin "$SCRATCH"
```

Expected: `task 1 checks pass`.

- [ ] **Step 10: Confirm the live corpus is unaffected**

```bash
SCRATCH="${TMPDIR:-${TEMP:-/tmp}}/retro-fix3"
rm -rf "$SCRATCH/work_task1" && mkdir -p "$SCRATCH/work_task1"
RETRO_HOME="$SCRATCH/work_task1" python plugins/retro/bin/retro.py extract --rebuild
```

Expected: no traceback, and `sessions in ledger` equal to `measured`. Record all four counters from the first line — Task 2 compares against them. Note that `transcripts` does not equal `measured + unchanged + unreadable` today; that is fix 1's defect, not this one, and it must not be treated as a regression here.

- [ ] **Step 11: Commit**

```bash
git add plugins/retro/bin/retro.py
git commit -F - <<'MSG'
fix: derive a row's identity from the root it was walked from

measure() built the ledger key and the is_subagent tag from a path relative to
one hardcoded directory, falling back to the bare filename for anything else.
The fallback loses rows to basename collisions and drops the subagents marker,
which would make every archived subagent transcript count as a session.

The root is now a parameter, the key is (root, path within that root), and
moments() resolves back through it and reports a row it cannot resolve instead
of returning an empty list that reads like a frictionless session.
MSG
```

---

### Task 2: Compressed transcripts

Read `.jsonl.gz` transcripts, and stop the session-id fallback from yielding a name that still ends in `.jsonl`.

**Files:**
- Modify: `plugins/retro/bin/retro.py` — import block (lines 23-33), `read_records` (lines 205-221), the session-id fallback inside `measure`, `cmd_extract`'s walk and stale loop; new `transcript_stem`, `_open_transcript` and `walk_transcripts` helpers
- Test: probe scripts under the scratch directory

**Interfaces:**
- Consumes from Task 1: `measure(path, root)`, `default_root()`, `row_key()`.
- Produces:
  - `transcript_stem(path: Path) -> str` — filename with `.gz` then `.jsonl` stripped.
  - `walk_transcripts(roots: list[Path]) -> Iterator[tuple[Path, Path]]` — `(root, path)` per transcript, roots in the order given, sorted within each root.
  - `_open_transcript(path: Path)` — a text-mode file object, gzip-aware.

- [ ] **Step 1: Write the failing check**

```bash
SCRATCH="${TMPDIR:-${TEMP:-/tmp}}/retro-fix3"
cat > "$SCRATCH/check_task2.py" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1])))
import retro

scratch = Path(sys.argv[2])
a = scratch / "fixture" / "root_a"

row = retro.measure(a / "proj" / "compressed.jsonl.gz", a)
assert row is not None, "a compressed transcript produced no row"
assert row["session_id"] == "fixture-a-2", row["session_id"]
assert row["user_prompts"] == 2, row["user_prompts"]
assert row["correction_turns"] == 1, row["correction_turns"]

nos = retro.measure(a / "proj" / "nosession.jsonl.gz", a)
assert nos["session_id"] == "nosession", nos["session_id"]
assert retro.transcript_stem(Path("x.jsonl.gz")) == "x"
assert retro.transcript_stem(Path("x.jsonl")) == "x"

found = {p.name for _, p in retro.walk_transcripts([a])}
assert found == {"same.jsonl", "compressed.jsonl.gz", "sub.jsonl",
                 "nosession.jsonl.gz"}, found

# A half-written archive member must not end the run.
whole = (a / "proj" / "compressed.jsonl.gz").read_bytes()
trunc = scratch / "fixture" / "truncated.jsonl.gz"
trunc.write_bytes(whole[:len(whole) // 2])
assert list(retro.read_records(trunc)) == [], "truncated member yielded records"
print("task 2 checks pass")
PY
python "$SCRATCH/check_task2.py" plugins/retro/bin "$SCRATCH"
```

- [ ] **Step 2: Run it to confirm it fails**

Expected: `AssertionError: a compressed transcript produced no row` — the plain reader decodes gzip bytes into replacement characters, every line fails to parse, and `measure` returns `None`.

- [ ] **Step 3: Add the imports**

Add `gzip` after `argparse` and `zlib` after `sys`, keeping the block alphabetical.

- [ ] **Step 4: Add the gzip branch, leaving `except OSError` alone for now**

Replace `read_records` and add the opener above it:

```python
def _open_transcript(path):
    """A root may hold transcripts gzipped. Text mode with replacement decoding
    either way, so one bad byte does not end the read."""
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, encoding="utf-8", errors="replace")


def read_records(path):
    """Yield parsed records, skipping malformed lines. A live session is being
    appended to while we read it; a truncated final line is normal, not an
    error."""
    try:
        with _open_transcript(path) as fh:
            for line in fh:
                # json.loads tolerates surrounding whitespace, so testing for
                # blankness beats copying every line of a gigabyte corpus.
                if not line or line.isspace():
                    continue
                try:
                    yield json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
    except OSError:
        return
```

- [ ] **Step 5: Run the check and watch it fail on the truncated member**

```bash
SCRATCH="${TMPDIR:-${TEMP:-/tmp}}/retro-fix3"
python "$SCRATCH/check_task2.py" plugins/retro/bin "$SCRATCH"
```

Expected: the compressed-transcript assertions now pass, and the run ends with an uncaught `EOFError: Compressed file ended before the end-of-stream marker was reached`. That is the point of this step. `gzip.BadGzipFile` is an `OSError` and is already caught, but a truncated member raises `EOFError`, which is not, and a corrupt one can surface `zlib.error`, which is not either. Either escaping here ends a whole extract or pack run over one file.

- [ ] **Step 6: Widen the exception tuple**

```python
    # gzip.BadGzipFile is an OSError, but a truncated member raises EOFError and
    # a corrupt one can raise zlib.error — neither is. Letting either out of
    # here ends the whole run over one file.
    except (OSError, EOFError, zlib.error):
        return
```

- [ ] **Step 7: Fix the session-id fallback**

Add above `measure`:

```python
def transcript_stem(path):
    """The session-id fallback for a filename. Path.stem strips one suffix, so
    a compressed transcript would fall back to a name still ending in .jsonl."""
    name = path.name
    for suffix in (".gz", ".jsonl"):
        if name.endswith(suffix):
            name = name[:-len(suffix)]
    return name
```

and in the row literal change `"session_id": session_id or path.stem,` to:

```python
        "session_id": session_id or transcript_stem(path),
```

- [ ] **Step 8: Add the walk**

Add above `cmd_extract`:

```python
def walk_transcripts(roots):
    """(root, path) for every transcript under every root, in the order given.

    Two patterns, because `*.jsonl` is a whole-name match and does not cover
    `*.jsonl.gz`. A set because the patterns could overlap under some future
    naming scheme, and one file must not be measured twice.
    """
    for root in roots:
        found = set()
        for pattern in TRANSCRIPT_GLOBS:
            found.update(root.rglob(pattern))
        for path in sorted(found):
            yield root, path
```

Then in `cmd_extract` replace the walk and the stale loop header:

```python
    transcripts = list(walk_transcripts([default_root()]))
    stale = []
    skipped = 0
    for root, path in transcripts:
```

append the root to the stale tuple, leaving the existing positions alone:

```python
        # root goes last so existing index-based access keeps working.
        stale.append((path, fingerprint, root))
```

and consume it:

```python
        for (path, fingerprint, root), row in zip(
                stale, pool.map(lambda item: measure(item[0], item[2]), stale)):
```

- [ ] **Step 9: Run the check to confirm it passes**

```bash
SCRATCH="${TMPDIR:-${TEMP:-/tmp}}/retro-fix3"
python "$SCRATCH/check_task2.py" plugins/retro/bin "$SCRATCH"
```

Expected: `task 2 checks pass`.

- [ ] **Step 10: Confirm the live corpus still measures the same**

```bash
SCRATCH="${TMPDIR:-${TEMP:-/tmp}}/retro-fix3"
rm -rf "$SCRATCH/work_task2" && mkdir -p "$SCRATCH/work_task2"
RETRO_HOME="$SCRATCH/work_task2" python plugins/retro/bin/retro.py extract --rebuild
diff <(sort "$SCRATCH/work_task1/metrics.jsonl") <(sort "$SCRATCH/work_task2/metrics.jsonl") \
  && echo "ledgers identical"
```

Expected: `ledgers identical`. The default corpus holds no compressed transcripts (measured 2026-08-19: 1,920 `.jsonl`, 0 `.jsonl.gz`), so widening the walk finds nothing new there. If the diff is non-empty, check whether a live session grew between the two runs before concluding anything — re-run both.

- [ ] **Step 11: Commit**

```bash
git add plugins/retro/bin/retro.py
git commit -F - <<'MSG'
feat: read compressed transcripts

A root may hold transcripts gzipped. The walk matches two patterns because
*.jsonl is a whole-name match and does not cover *.jsonl.gz, the reader picks
its opener from the suffix, and the session-id fallback strips both suffixes
instead of leaving a name that still ends in .jsonl.

The reader also catches EOFError and zlib.error alongside OSError: a truncated
member raises the first and a corrupt one can raise the second, and neither is
an OSError. Either escaping ended the whole run over one file.
MSG
```

---

### Task 3: Configured roots

Build the ordered root list from the default root, a config file and the environment; walk all of them; report how many session ids appear under more than one.

**Files:**
- Modify: `plugins/retro/bin/retro.py` — transcript-roots section (new `config_dirs`, `env_dirs`, `transcript_roots`, `overlapping_sessions`), `cmd_extract`'s walk and printed summary, the module docstring
- Test: probe scripts and end-to-end runs under the scratch directory

**Interfaces:**
- Consumes from Tasks 1-2: `default_root()`, `row_key()`, `walk_transcripts()`, `measure(path, root)`.
- Produces:
  - `config_dirs() -> list[str]` — the config file's `extra_transcript_dirs`.
  - `env_dirs() -> list[str]` — the environment variable's entries.
  - `transcript_roots() -> list[Path]` — resolved, de-duplicated, in precedence order.
  - `overlapping_sessions(rows: Iterable[dict]) -> int`.

- [ ] **Step 1: Write the failing check**

```bash
SCRATCH="${TMPDIR:-${TEMP:-/tmp}}/retro-fix3"
cat > "$SCRATCH/check_task3.py" <<'PY'
import json, os, sys
from pathlib import Path

scratch = Path(sys.argv[2])
a = scratch / "fixture" / "root_a"
b = scratch / "fixture" / "root_b"
work = scratch / "work_roots"
work.mkdir(parents=True, exist_ok=True)
(work / "config.json").write_text(
    json.dumps({"extra_transcript_dirs": [str(a)]}), encoding="utf-8")
# Set before importing: the work directory and the config path are read at
# import time.
os.environ["RETRO_HOME"] = str(work)
os.environ["RETRO_TRANSCRIPT_DIRS"] = os.pathsep.join([str(b), str(a)])
sys.path.insert(0, str(Path(sys.argv[1])))
import retro

roots = retro.transcript_roots()
assert roots[0] == retro.default_root(), roots[0]
assert roots[1] == a.resolve(), roots[1]
assert roots[2] == b.resolve(), roots[2]
assert len(roots) == 3, f"a root named twice must be walked once: {roots}"

rows = [retro.measure(p, r) for r, p in retro.walk_transcripts([a, b])]
assert len(rows) == 6, len(rows)
assert sum(1 for row in rows if row["is_subagent"]) == 1
assert len({retro.row_key(row) for row in rows}) == 6, "rows collided"
assert retro.overlapping_sessions(rows) == 1, retro.overlapping_sessions(rows)
only_a = [r for r in rows if r.get("root") == str(a)]
assert retro.overlapping_sessions(only_a) == 0, "duplicates inside one root are not overlap"
print("task 3 checks pass")
PY
python "$SCRATCH/check_task3.py" plugins/retro/bin "$SCRATCH"
```

- [ ] **Step 2: Run it to confirm it fails**

Expected: `AttributeError: module 'retro' has no attribute 'transcript_roots'`.

- [ ] **Step 3: Add the root-list functions**

Append to the transcript-roots section, after `row_key`:

```python
def config_dirs():
    """`extra_transcript_dirs` from the config file in the work directory.

    The work directory follows RETRO_HOME, and so does this file — a config
    pinned to the default location would be invisible to any run that moved it.

    No config file means no extra roots. A file that exists but cannot be read
    or parsed stops the run: ignoring it would drop a configured root out of
    every measurement without saying anything.
    """
    try:
        raw = CONFIG_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as exc:
        print(f"cannot read config {CONFIG_FILE}: {exc}", file=sys.stderr)
        sys.exit(EXIT_CANNOT_RUN)
    try:
        data = json.loads(raw)
    except ValueError as exc:
        print(f"config {CONFIG_FILE} is not valid JSON: {exc}", file=sys.stderr)
        sys.exit(EXIT_CANNOT_RUN)
    dirs = data.get("extra_transcript_dirs", []) if isinstance(data, dict) else None
    if not isinstance(dirs, list) or not all(isinstance(d, str) for d in dirs):
        print(f"config {CONFIG_FILE}: extra_transcript_dirs must be a list of "
              f"strings", file=sys.stderr)
        sys.exit(EXIT_CANNOT_RUN)
    return dirs


def env_dirs():
    """Extra roots from the environment, delimited by the platform's path
    separator — ';' on Windows, ':' elsewhere."""
    raw = os.environ.get(ROOTS_ENV_VAR) or ""
    return [part for part in raw.split(os.pathsep) if part.strip()]


def transcript_roots():
    """Every root to walk, in order: the built-in one, then the config file's,
    then the environment's.

    Resolved and de-duplicated, so a directory named in two places is walked
    once and its rows carry one spelling of it as their key. A configured root
    that is not a directory is reported and dropped rather than measured as
    empty — under --rebuild its rows would otherwise vanish without a word.
    """
    roots = []
    seen = set()
    for raw in [default_root()] + config_dirs() + env_dirs():
        try:
            path = Path(raw).expanduser().resolve()
        except OSError as exc:
            print(f"cannot resolve a configured transcript root: {exc}",
                  file=sys.stderr)
            continue
        key = str(path).casefold()
        if key in seen:
            continue
        seen.add(key)
        if not path.is_dir():
            print(f"configured transcript root is not a directory: {path}",
                  file=sys.stderr)
            continue
        roots.append(path)
    return roots


def overlapping_sessions(rows):
    """How many session ids appear under more than one root.

    Reported, never merged. Keying the ledger by session id is the design this
    tool already ruled out — it collapses a subagent transcript onto its parent
    — so a session archived in two places stays two rows, and this number is
    how that stays visible instead of silent.
    """
    roots_by_session = {}
    for row in rows:
        sid = row.get("session_id") or ""
        if sid:
            roots_by_session.setdefault(sid, set()).add(row.get("root") or "")
    return sum(1 for seen in roots_by_session.values() if len(seen) > 1)
```

- [ ] **Step 4: Walk every root in `cmd_extract`**

Replace the temporary single-root walk:

```python
    roots = transcript_roots()
    transcripts = list(walk_transcripts(roots))
```

and add one line to the printed summary, between `sessions in ledger` and `ledger`:

```python
    if len(roots) > 1:
        print(f"roots: {len(roots)}  session ids in more than one root: "
              f"{overlapping_sessions(rows.values())} (not deduplicated)")
```

The line prints only when extra roots are configured, so a run without configuration prints exactly what it printed before.

- [ ] **Step 5: Run the check to confirm it passes**

```bash
SCRATCH="${TMPDIR:-${TEMP:-/tmp}}/retro-fix3"
python "$SCRATCH/check_task3.py" plugins/retro/bin "$SCRATCH"
```

Expected: `task 3 checks pass`.

- [ ] **Step 6: End-to-end, both configuration channels at once**

```bash
SCRATCH="${TMPDIR:-${TEMP:-/tmp}}/retro-fix3"
WORK="$SCRATCH/work_e2e"
rm -rf "$WORK" && mkdir -p "$WORK"
python -c "import json,sys;open(sys.argv[1],'w').write(json.dumps({'extra_transcript_dirs':[sys.argv[2]]}))" \
  "$WORK/config.json" "$SCRATCH/fixture/root_a"
RETRO_HOME="$WORK" RETRO_TRANSCRIPT_DIRS="$SCRATCH/fixture/root_b" \
  python plugins/retro/bin/retro.py extract --rebuild
grep -c '"root"' "$WORK/metrics.jsonl"
python - "$WORK/metrics.jsonl" <<'PY'
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1], encoding="utf-8")]
fixture = [r for r in rows if r.get("root")]
print("fixture rows:", len(fixture))
print("subagent rows among them:", sum(1 for r in fixture if r["is_subagent"]))
print("session ids:", sorted({r["session_id"] for r in fixture}))
print("relative paths:", sorted(r["transcript"] for r in fixture))
PY
```

Expected:
- a `roots: 3  session ids in more than one root: 1 (not deduplicated)` line on stdout
- `grep -c` reports 6
- `fixture rows: 6`, `subagent rows among them: 1`
- the session ids include `nosession`, not `nosession.jsonl`
- `proj/same.jsonl` appears twice in the list of relative paths — once per root — and both rows survived

- [ ] **Step 7: A pack quotes a moment from an extra root**

```bash
SCRATCH="${TMPDIR:-${TEMP:-/tmp}}/retro-fix3"
WORK="$SCRATCH/work_e2e"
RETRO_HOME="$WORK" python plugins/retro/bin/retro.py pack --days 7 --sessions 400
grep -c "fixture" "$WORK"/pack-*.md
```

Expected: a non-zero count, and no `no transcript on disk` warning on stderr. Do not print the pack's contents — it holds redacted moments from the live corpus.

- [ ] **Step 8: A malformed config stops the run**

```bash
SCRATCH="${TMPDIR:-${TEMP:-/tmp}}/retro-fix3"
WORK="$SCRATCH/work_badcfg"
rm -rf "$WORK" && mkdir -p "$WORK"
printf '{ not json' > "$WORK/config.json"
RETRO_HOME="$WORK" python plugins/retro/bin/retro.py extract; echo "exit=$?"
printf '{"extra_transcript_dirs": "one-string-not-a-list"}' > "$WORK/config.json"
RETRO_HOME="$WORK" python plugins/retro/bin/retro.py extract; echo "exit=$?"
```

Expected: both runs print a message naming the config file on stderr, and `exit=2`.

- [ ] **Step 9: An unreachable configured root is reported, and the run continues**

```bash
SCRATCH="${TMPDIR:-${TEMP:-/tmp}}/retro-fix3"
WORK="$SCRATCH/work_missing"
rm -rf "$WORK" && mkdir -p "$WORK"
RETRO_HOME="$WORK" RETRO_TRANSCRIPT_DIRS="$SCRATCH/fixture/no_such_root" \
  python plugins/retro/bin/retro.py extract --rebuild; echo "exit=$?"
```

Expected: `configured transcript root is not a directory: ...` on stderr, the default corpus still measured, `exit=0`.

- [ ] **Step 10: No configuration is byte-identical to before the change**

Take the baseline from the merge base rather than trusting a number recorded earlier — a sibling fix may have landed first, and the corpus grows between runs.

```bash
SCRATCH="${TMPDIR:-${TEMP:-/tmp}}/retro-fix3"
BASE="$(git merge-base HEAD main)"
git show "$BASE:plugins/retro/bin/retro.py" > "$SCRATCH/retro_baseline.py"
unset RETRO_TRANSCRIPT_DIRS
for run in before after before2; do rm -rf "$SCRATCH/bi_$run"; mkdir -p "$SCRATCH/bi_$run"; done
RETRO_HOME="$SCRATCH/bi_before"  python "$SCRATCH/retro_baseline.py" extract --rebuild > "$SCRATCH/bi_before.out"
RETRO_HOME="$SCRATCH/bi_after"   python plugins/retro/bin/retro.py    extract --rebuild > "$SCRATCH/bi_after.out"
RETRO_HOME="$SCRATCH/bi_before2" python "$SCRATCH/retro_baseline.py" extract --rebuild > "$SCRATCH/bi_before2.out"
sed 's|^ledger:.*|ledger: <work>|' "$SCRATCH/bi_before.out" > "$SCRATCH/bi_before.norm"
sed 's|^ledger:.*|ledger: <work>|' "$SCRATCH/bi_after.out"  > "$SCRATCH/bi_after.norm"
diff "$SCRATCH/bi_before.norm" "$SCRATCH/bi_after.norm" && echo "stdout identical"
cmp "$SCRATCH/bi_before/metrics.jsonl" "$SCRATCH/bi_after/metrics.jsonl" && echo "ledger identical"
cmp "$SCRATCH/bi_before/metrics.jsonl" "$SCRATCH/bi_before2/metrics.jsonl" && echo "corpus stable across the comparison"
```

Expected: all three lines print. No `config.json` may exist in those three work directories. If the ledgers differ and `corpus stable` does not print, a live session grew mid-comparison — re-run before concluding anything.

- [ ] **Step 11: Update the module docstring**

The docstring describes reading session history from one place. Add, after the subcommand list:

```
Transcripts are read from the default session directory, plus any extra roots
named in `extra_transcript_dirs` in the work directory's config file or in the
RETRO_TRANSCRIPT_DIRS environment variable, path-separator delimited, in that
order. A root may hold transcripts gzipped. A row is identified by its root and
its path within that root, so a session archived under two roots stays two rows
and the count of those is printed rather than merged away.
```

- [ ] **Step 12: Commit**

```bash
git add plugins/retro/bin/retro.py
git commit -F - <<'MSG'
feat: measure transcripts from additional roots

Roots come from the built-in directory, then an extra_transcript_dirs key in
the work directory's config file, then a path-separator delimited environment
variable, resolved and de-duplicated in that order. The work directory follows
RETRO_HOME and the config file follows it.

A session archived under two roots stays two rows — keying by session id is the
design already ruled out, since it collapses a subagent transcript onto its
parent — so extract prints how many session ids span more than one root instead
of merging them. That line and the root field appear only when extra roots are
configured, leaving an unconfigured run byte-identical to before.
MSG
```

---

### Task 4: Sweep what the change made false, and record the measurements

**Files:**
- Modify: `docs/plans/2026-08-12-retro-design.md` — the `extract` bullet, the metrics-row field list, the "Open" bullet about how far back transcripts reach
- Modify: `docs/plans/2026-08-18-retro-measurement-fixes.md` — the verification table's "no-config behaviour" row, filled in with the measured result
- Test: `plugins/core/bin/repo-privacy-audit` over the worktree

**Interfaces:**
- Consumes: the measurements taken in Tasks 1-3.
- Produces: nothing that code depends on.

- [ ] **Step 1: Find every doc the change falsified**

REQUIRED SUB-SKILL: use `core:finding-what-a-change-made-false`, scoped to this branch's diff.

```bash
git diff "$(git merge-base HEAD main)"..HEAD --stat
grep -rn "transcript\|jsonl\|is_subagent" docs/plans/2026-08-12-retro-design.md | head -40
grep -rn "transcript\|extract" plugins/retro/skills/*/SKILL.md | head -20
```

The three skills invoke `extract` and `pack` and describe reading the pack; checked 2026-08-19, none of them describes the row schema, the walk, or `extract`'s stdout, so none should need editing. Confirm that from the grep rather than assuming it.

- [ ] **Step 2: Correct the design document**

Three edits in `docs/plans/2026-08-12-retro-design.md`:

1. The `extract` bullet says it walks "session transcripts" — say that it walks the built-in directory plus any configured extra roots, and that a root may hold them gzipped.
2. The metrics-row field list gains `root` — record that it is present only on rows from a non-default root, and that a row's identity is the pair of `root` and `transcript`.
3. The "Open" bullet stating that transcripts begin on a fixed date and that "all history" is roughly two months — this fix is the mechanism that lifts it. Reword it to say the window is whatever the configured roots cover.

No path, no root name, and no other project appears in any of these edits.

- [ ] **Step 3: Record the measurements in the spec**

Fill the "no-config behaviour" row of the verification table in `docs/plans/2026-08-18-retro-measurement-fixes.md` with the result from Task 3 step 10, and note under fix 3 that the fixture run produced six rows across two roots, one of them a subagent row, with one overlapping session id. Aggregates only — no sample turns, no paths.

- [ ] **Step 4: Privacy scan and commit-message read-back**

```bash
sh plugins/core/bin/repo-privacy-audit -C .
git log "$(git merge-base HEAD main)"..HEAD --format='%B'
```

Expected: the audit reports only the known accepted hit — the author's name and address in commit metadata, published with this repository on purpose. Read the commit messages back: a substitution inside a commit message is silent, and nobody re-reads metadata.

- [ ] **Step 5: Commit**

```bash
git add docs/plans/2026-08-12-retro-design.md docs/plans/2026-08-18-retro-measurement-fixes.md
git commit -F - <<'MSG'
docs: fold the extra-roots change back into the design and the spec

The design document described one transcript directory, a row schema with no
root field, and a fixed history window that this change is the mechanism for
lifting. The spec's verification table now carries the measured no-config
result rather than an expectation.
MSG
```

---

## Verification

Everything below is measured read-only against the live corpus, from an isolated `RETRO_HOME` under a temp directory. Baseline figures were measured on 2026-08-19 from the then-current `HEAD`; re-take them from the merge base at execution time, because the corpus grows and a sibling fix may land first.

| Check | Baseline | Expected after |
|---|---|---|
| Files walked in the default root | 1,920 `.jsonl`, 0 `.jsonl.gz` | unchanged |
| Rows in the ledger, no configuration | 1,898 | unchanged, and the file byte-identical |
| `extract` stdout, no configuration | four counters plus two lines | identical apart from the work-directory path |
| Fixture rows across two extra roots | n/a | 6, of which 1 is `is_subagent` |
| Same relative path under two roots | collapses to one row today | 2 rows, distinct keys |
| Session-id fallback on a compressed file | would be `<name>.jsonl` | `<name>` |
| Truncated `.jsonl.gz` | `EOFError` escapes the reader | yields nothing, run survives |
| Overlapping session ids | not reported | reported on its own stdout line, only when extra roots are configured |
| Pack quotes a moment from an extra root | resolves to a path that does not exist and quotes nothing, silently | quoted, and an unresolvable row is named on stderr |
| Malformed config file | n/a | message on stderr, exit 2 |
| Unreachable configured root | n/a | message on stderr, run continues, exit 0 |
| Full rebuild wall clock | roughly 5-6 s over ~1,900 files | no material regression |
| Privacy audit over the worktree | only the known accepted hit | unchanged |

## Questions for the operator

1. The spec says only "a config file under the work directory" — this plan names it `config.json` and reads a top-level `extra_transcript_dirs` key; confirm that filename or name a different one.
2. A configured root that is not currently a directory is reported on stderr, dropped, and the run continues with exit 0 — should an unreachable root instead flag the run (exit 1) so a run cannot quietly measure less than it was configured to?
3. A malformed or unreadable config file stops the run with exit 2 rather than being ignored — confirm that a broken config should block a retrospective outright.
