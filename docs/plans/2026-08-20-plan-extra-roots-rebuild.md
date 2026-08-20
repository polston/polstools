# Additional transcript directories, rebuilt — implementation plan

> **NOT EXECUTED, AND THE BASE HAS MOVED.** Written 2026-08-20 against the branch
> tip of that morning. Since then the ledger gained a schema version that refuses
> a mismatched ledger, the retry counter was renamed and taken out of the score,
> the turn classifier was rewritten and its thresholds settled from hand marks,
> a subagent lens and two more subcommands landed, and `extract` now refuses to
> write inside a repository. Its edit anchors and its "before" numbers were true
> of that morning and are not true now. Re-measure before following it, and see
> issue #1 — the question underneath this work turned out to be a design
> question about which directory owns a transcript, not a defect to patch.


> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure transcripts from roots other than the built-in session directory — including compressed ones — with every file measured exactly once, every row identified by the root it came from, and no configuration mistake acted on in silence.

**Architecture:** Start from the implementation rejected on `retro-lane-a`, which already carries the shape the spec asks for: an ordered root list from the default root, a config file and an environment variable; a two-pattern walk; a gzip branch in the reader; and rows keyed by (root, path within that root). Seven proved defects are then fixed one at a time, each with a check that fails before and passes after. The root list gains containment reduction and platform-correct identity; the subagent tag and the stored path are both read from the transcript's place *inside* its root; both configuration channels pass through one validation step; `extract` builds its roots before deciding it cannot run; a read failure is split into "the bytes are broken" (recorded, so it is not re-read forever) and "the file was out of reach" (retried); and the row stores a derived id for its root, with the id-to-directory map living in the work directory rather than in the ledger.

**Tech Stack:** Python 3 stdlib only (`gzip`, `zlib`, `hashlib`, `json`, `pathlib`, `os`), no dependencies, no build step. One source file: `plugins/retro/bin/retro.py`.

**Spec:** `docs/plans/2026-08-18-retro-measurement-fixes.md`, section "Fix 3 — additional transcript directories", plus the first attempt at it in `docs/plans/2026-08-19-plan-fix3-extra-roots.md`. Read both before starting; this plan argues from the first and supersedes the second.

## Global Constraints

- **Stdlib only. No dependency, no build step, no daemon.** (spec invariant 7)
- **No other project of the author's is named** — in any file, doc, example, code comment, or commit message. This ships a mechanism and nothing else. (spec invariant 1, repo `CLAUDE.md`)
- **No filesystem path belonging to the operator or to another project may appear** in a tracked file, an example, a docstring, a default value, or a commit message. Every path in this plan's commands is a shell variable or a synthetic fixture path, and must stay that way.
- **Verification is read-only against the live corpus**, from an isolated `RETRO_HOME` under a scratch directory. Never run `extract` against the default work directory. Never write anywhere under the user's Claude configuration directory. The probe corpus in Task 1 supplies its own synthetic home, so no probe touches the live one at all.
- **Message text leaves the tool only through `redact()`.** (spec invariant 3) That includes the new stderr line naming a transcript that ended mid-stream.
- **`redact()` does not catch foreign absolute paths** (spec invariant 4). Once an extra root is configured, moments quoted from it can carry that root's paths into a pack. Packs stay in the work directory, are never committed, and are not safe to paste anywhere public without a read-through.
- **No deduplication of sessions that appear under two roots.** (spec non-goals) The overlap count is reported, never merged. Deduplicating *files* is a different thing and is the subject of Task 3.
- **The ledger contract:** redefining a counter requires `extract --rebuild` before any trend over it means anything. (spec invariant 2) This work defines no counter and requires no rebuild.
- **Exit codes:** `0` ran clean and flagged nothing, `1` ran clean and flagged something, `2` could not run.
- **Commit messages go through `git commit -F -` fed by a single-quoted heredoc.** (repo `CLAUDE.md`) Read each one back afterwards.

## The seven defects, and where each is fixed

Every row was reproduced by running the rejected implementation against a synthetic corpus. The "proved by" column is the observed behaviour, not a reading of the code.

| # | Severity | Defect | Proved by | Task |
|---|---|---|---|---|
| 1 | HIGH | Roots are de-duplicated by identity but not containment, so a root nested inside another walks the shared files twice: every session counted twice, every counter doubled, exit 0, nothing said | 3 files under two nested roots produced 7 walk yields, 4 distinct files, 7 rows and 14 user prompts where 4 files, 4 rows and 8 prompts exist | 3 |
| 2 | MEDIUM | Whether a transcript is a subagent transcript is decided from the components of its **absolute** path, so any ancestor directory named `subagents` marks everything beneath it — removing those sessions from session counts and from quoting | an ordinary transcript at `proj/plain.jsonl`, under a root whose own parent is named `subagents`, came back tagged `is_subagent: True` | 4 |
| 3 | MEDIUM-LOW | Entries from the environment variable are not whitespace-stripped, so a list written with a space after the separator silently resolves that root against the working directory | the padded entry resolved to a path under the working directory instead of the directory named | 5 |
| 4 | LOW-MEDIUM | An empty or relative entry in the config file silently becomes the working directory and is walked recursively, with no message | `""` in `extra_transcript_dirs` was accepted as a root equal to the working directory, no message; `relative/dir` was reported only as "not a directory", which is not what is wrong with it | 5 |
| 5 | MEDIUM | `extract` exits before roots are built if the built-in directory is absent, so a machine holding only archives cannot run at all | with a valid configured root and no built-in directory: exit 2, nothing measured | 6 |
| 6 | LOW-MEDIUM | A permanently corrupt compressed file is never fingerprinted, so every run re-reads it and stays flagged forever. A cut-short compressed file also discards every record already parsed, unlike a truncated final line in an uncompressed file | two runs in a row reported `unreadable: 2` and exit 1, and neither file appeared in the state file; a cut archive that had already parsed 2,360 records produced no row at all | 7 |
| 7 | LOW | The ledger stores a configured root as an unredacted absolute path, beside a `project` field that is redacted | the `root` field of a fixture row held the full path of the archive, account name included | 2 |

## The case-folding question, settled

The reviewer suspected the de-duplication key case-folds and asked whether that wrongly merges two distinct roots. It does case-fold — the key is `str(path).casefold()` — and the consequence splits by platform:

1. On a case-insensitive filesystem it is redundant: `Path.resolve()` already returns the on-disk spelling, so two spellings of one root collapse before the key is taken. Measured: resolving an upper-cased and a lower-cased spelling of the same fixture directory returned the same path, and the two compared equal.
2. On a case-sensitive filesystem it is wrong: two directories whose names differ only in case are different directories, and the second one is dropped from the list and never walked — a configured archive would go missing with nothing said.

The fix is to stop comparing strings at all. `pathlib` already carries the platform's rule: `Path` equality and hashing are case-insensitive on Windows and case-sensitive on POSIX (measured both ways), and `Path.is_relative_to` follows the same rule while refusing the sibling-prefix trap that a `startswith` test falls for (`/a/bc` is not inside `/a/b`). Task 3 replaces the case-folded string key with the resolved `Path` itself, which is correct on both platforms without a branch. `os.path.normcase` appears in exactly one place — hashing a root into an id (Task 2) — so that the id does not change with the spelling of a drive letter.

## Baselines measured before writing this plan

Read-only against the live corpus, from an isolated work directory, on the current branch tip. Re-take them at execution time: the corpus grows.

| Measurement | Value |
|---|---|
| Files under the built-in root | 1,943 `.jsonl`, 0 `.jsonl.gz` |
| `extract --rebuild`, no configuration | `transcripts: 1943  measured: 1921  unchanged: 0  not-transcripts: 22  unreadable: 0`, exit 0 |
| Full rebuild wall clock | 5.45 s at the branch tip, 5.63 s with every fix applied |
| No-configuration comparison, tip vs. fixed | stdout identical, ledger identical apart from the single row of the session doing the measuring, whose counters grow while the run happens |

The last row is why the comparison in Task 8 compares keys and fields rather than running `cmp`: a naive byte comparison fails on a live corpus for a reason that has nothing to do with the change.

## Ledger compatibility — why `root` is absent on default-root rows

The spec requires that a run with no configuration produce output identical to today's. Any always-present new field changes every line of `metrics.jsonl`, so the row records `root` **only when the transcript came from a non-default root**. An absent or empty `root` means the default root. Consequences, all deliberate:

1. A no-extra-roots ledger is byte-identical to one written before this change, and existing ledgers keep working without a rebuild. Measured: identical stdout and identical rows, and the work directory gains no new file either.
2. `row_key()` is `(row.get("root") or "", row.get("transcript") or "")`, which is the key old rows already had.
3. `--rebuild` starts from an empty row set, so rows belonging to a root that is currently unreachable are lost on a rebuild. That is why an unreachable configured root is reported on stderr rather than skipped quietly.
4. The root id is derived from the root's path, so re-configuring the same directory produces the same id and updates the same rows instead of duplicating them.

## File Structure

| File | Responsibility after the change |
|---|---|
| `plugins/retro/bin/retro.py` | Gains a "Transcript roots" section (root list, containment reduction, precedence, identity, the id-to-directory map), a gzip branch and a two-part failure contract in the reader, a two-pattern walk that yields each file once, root-aware row assembly, and root-aware moment resolution. |
| `docs/plans/2026-08-12-retro-design.md` | Swept in Task 8: the `extract` bullet, the metrics-row field list and keying paragraph, and the "Open" bullet about how far back transcripts reach. |
| `docs/plans/2026-08-18-retro-measurement-fixes.md` | Swept in Task 8: the verification table's "no-config behaviour" row, and a "Fix 3 — measured after implementation" subsection holding the measured results. |

New functions in `retro.py`: `default_root`, `root_id`, `load_roots`, `save_roots`, `row_root`, `row_key`, `_clean_entries`, `config_dirs`, `env_dirs`, `transcript_roots`, `overlapping_sessions`, `_open_transcript`, `_permanent_failure`, `transcript_stem`, `walk_transcripts`.

Modified: the module docstring, the import block, the constants block, `TranscriptUnreadable`, `read_records`, `measure`, `measure_outcome`, `cmd_extract`, `_moments`.

Untouched: `redact`, `text_of`, `tool_calls_of`, `signature`, `classify_user_turn`, `is_approval`, `is_error_record`, `parse_ts`, `load_state`, `load_rows`, `totals`, `split_population`, `friction_score`, `cmd_pack`, `installed_skills`, `cmd_skills`, `main`, `COUNTERS`.

## Sequencing, and why the implementation does not fan out

All seven fixes live in one 700-line file, and five of them touch `cmd_extract`, `measure` or the roots section. Lanes must be file-disjoint to run concurrently in their own worktrees; these are not lanes, they are edits to one file, and splitting them across worktrees would put every merge into the same hunks. So the tasks run in one worktree, in order.

Two things do run concurrently, from the moment their inputs exist:

1. **The live-corpus baseline** (Task 1, step 3) is a 5.5-second run that blocks nothing. Start it in the background while the fixture is being built.
2. **The review lenses** for this plan, and the privacy audit in Task 8, are independent of each other and of the build.

The task order is fixed by code structure rather than by severity: Task 2 changes what a row stores for its root, and Tasks 3-7 all read or write that field, so it goes first. Doing it later would rewrite every code block after it.

---
### Task 1: Port the rejected implementation, and build the probe corpus

Bring the four `retro-lane-a` commits' change to `retro.py` onto this branch unchanged, and stand up the synthetic corpus every later task measures against. Nothing is fixed here; this is the baseline the seven fixes are applied to, and the checks written here must keep passing through all of them.

**Files:**
- Modify: `plugins/retro/bin/retro.py` — module docstring, import block, constants block, a new "Transcript roots" section, `read_records`, `measure`, `measure_outcome`, `cmd_extract`, `_moments`
- Test: this repo has no test suite. Checks are throwaway probe scripts under a scratch directory, plus measurement against the live corpus.

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces, for every later task:
  - `default_root() -> Path` — the built-in transcript directory, resolved, cached.
  - `root_label(root: Path) -> str` — `""` for the default root, `str(root)` otherwise. **Replaced by `root_id` in Task 2.**
  - `row_root(row: dict) -> Path` — the directory a row's `transcript` is relative to.
  - `row_key(row: dict) -> tuple[str, str]` — `(root label, relative transcript path)`.
  - `config_dirs() -> list[str]`, `env_dirs() -> list[str]`, `transcript_roots() -> list[Path]`.
  - `walk_transcripts(roots) -> Iterator[tuple[Path, Path]]` — `(root, path)` per transcript.
  - `measure(path: Path, root: Path) -> dict | None` — the second parameter is required.
  - `transcript_stem(path: Path) -> str`, `_open_transcript(path: Path)`, `overlapping_sessions(rows) -> int`.

- [ ] **Step 1: Confirm the port applies to this branch tip, then apply it**

```bash
SCRATCH="${TMPDIR:-${TEMP:-/tmp}}/retro-roots"
mkdir -p "$SCRATCH"
git diff c7cfe7e 44c26cb -- plugins/retro/bin/retro.py > "$SCRATCH/extra-roots.patch"
git apply --check -v "$SCRATCH/extra-roots.patch"
git apply "$SCRATCH/extra-roots.patch"
python -c "import ast; ast.parse(open('plugins/retro/bin/retro.py', encoding='utf-8').read()); print('parses')"
```

Expected: every hunk reported as succeeding, most at an offset (the lane was cut before this branch took fixes 1, 2a and 2c), then `parses`. Verified against this branch tip before this plan was written: 17 hunks, all applying, no conflict. If a hunk fails, stop and reconcile by hand rather than forcing it — the three merged fixes rewrote `cmd_extract` and `read_records`, and a mis-merge there is what this whole plan exists to avoid repeating.

- [ ] **Step 2: Build the probe corpus**

Write the builder to the scratch directory, never into the repo. It creates its own synthetic home, so the default root during a probe is a fixture directory holding one transcript, and no probe reads the live corpus.

```bash
SCRATCH="${TMPDIR:-${TEMP:-/tmp}}/retro-roots"
mkdir -p "$SCRATCH"
cat > "$SCRATCH/make_fixture.py" <<'PY'
"""Build a synthetic corpus for the extra-roots checks. Nothing here touches
the live session directory: the fake home below becomes the default root."""
import gzip, json, shutil, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

scratch = Path(sys.argv[1])
base, home = scratch / "fixture", scratch / "fakehome"
for d in (base, home):
    if d.exists():
        shutil.rmtree(d)
now = datetime.now(timezone.utc)


def rec(kind, text, minutes_ago, sid=None):
    out = {"type": kind,
           "message": {"role": kind, "content": [{"type": "text", "text": text}]},
           "cwd": "/fixture/project", "gitBranch": "fixture", "version": "0.0.0",
           "timestamp": (now - timedelta(minutes=minutes_ago)).isoformat()}
    if sid:
        out["sessionId"] = sid
    return out


def conversation(sid=None):
    # 500 assistant characters followed by a 15-character reply is a correction
    # by the tool's own thresholds, so every fixture row scores friction and is
    # eligible to be quoted in a pack.
    return [rec("user", "please do the thing", 30, sid),
            rec("assistant", "x" * 500, 29, sid),
            rec("user", "no, revert that", 28, sid)]


def write(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(r) + "\n" for r in records)
    if path.suffix == ".gz":
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            fh.write(body)
    else:
        path.write_text(body, encoding="utf-8")


# The default root, synthetic: one transcript.
write(home / ".claude" / "projects" / "proj" / "live.jsonl", conversation("home-1"))

# Containment: three transcripts reachable from two nested roots.
outer, inner = base / "outer", base / "outer" / "nested"
for name, sid in (("one", "nest-1"), ("two", "nest-2"), ("three", "nest-3")):
    write(inner / "proj" / f"{name}.jsonl", conversation(sid))

# A root whose own ANCESTOR is named `subagents`, holding one ordinary
# transcript and one real subagent transcript.
weird = base / "subagents" / "archive"
write(weird / "proj" / "plain.jsonl", conversation("weird-1"))
write(weird / "proj" / "plain" / "subagents" / "real.jsonl", conversation("weird-1"))

# Two sibling archives sharing a relative path, and one session id.
write(base / "arch_a" / "proj" / "same.jsonl", conversation("arch-1"))
write(base / "arch_b" / "proj" / "same.jsonl", conversation("arch-1"))

# Compressed: sound, unreadable bytes, and a large one cut in half.
gz = base / "gzroot" / "proj"
write(gz / "ok.jsonl.gz", conversation("gz-1"))
(gz / "corrupt.jsonl.gz").write_bytes(b"these bytes are not an archive\n" * 3)
big = []
for i in range(2000):
    big += [rec("user", f"please do the thing {i}", 60 - i // 100, "gz-2"),
            rec("assistant", "y" * 500, 60 - i // 100, "gz-2")]
write(gz / "big.jsonl.gz", big)
whole = (gz / "big.jsonl.gz").read_bytes()
(gz / "cut.jsonl.gz").write_bytes(whole[:int(len(whole) * 0.6)])

print("fixture:", base)
print("fake home:", home)
PY
python "$SCRATCH/make_fixture.py" "$SCRATCH"
find "$SCRATCH/fixture" "$SCRATCH/fakehome" -type f | wc -l
```

Expected: two paths printed and `12` files — 1 under the synthetic home, 3 under the nested root, 2 under the `subagents`-ancestor root, 2 under the sibling archives, 4 compressed.

- [ ] **Step 3: Take the live-corpus baseline, in the background**

It blocks nothing and takes about five seconds; start it now and read it in Task 8.

```bash
SCRATCH="${TMPDIR:-${TEMP:-/tmp}}/retro-roots"
# HEAD is still the pre-port commit here: the patch is applied to the working
# tree in step 1 and is not committed until step 5.
git show HEAD:plugins/retro/bin/retro.py > "$SCRATCH/retro_baseline.py"
rm -rf "$SCRATCH/base_ledger" && mkdir -p "$SCRATCH/base_ledger"
unset RETRO_TRANSCRIPT_DIRS
RETRO_HOME="$SCRATCH/base_ledger" python "$SCRATCH/retro_baseline.py" extract --rebuild \
  > "$SCRATCH/base_ledger.out" 2>&1 &
```

Expected, when it finishes: four counters on one line, `sessions in ledger` equal to `measured`, exit 0. Measured before this plan was written: 1,943 files, 1,921 measured, 22 not-transcripts, 0 unreadable, 5.45 s.

- [ ] **Step 4: Write the check for what the port already does**

```bash
SCRATCH="${TMPDIR:-${TEMP:-/tmp}}/retro-roots"
cat > "$SCRATCH/check_port.py" <<'PY'
"""What the ported implementation already does: extra roots from both
channels, compressed transcripts, and a row identified by its root."""
import json, os, sys
from pathlib import Path

scratch = Path(sys.argv[2])
work = scratch / "work_port"
work.mkdir(parents=True, exist_ok=True)
a = (scratch / "fixture" / "arch_a").resolve()
b = (scratch / "fixture" / "arch_b").resolve()
gz = (scratch / "fixture" / "gzroot").resolve()
(work / "config.json").write_text(
    json.dumps({"extra_transcript_dirs": [str(a)]}), encoding="utf-8")
# Set before the import: the work directory and the config path are read at
# import time.
os.environ["RETRO_HOME"] = str(work)
os.environ["HOME"] = os.environ["USERPROFILE"] = str(scratch / "fakehome")
os.environ["RETRO_TRANSCRIPT_DIRS"] = os.pathsep.join([str(b), str(gz), str(a)])
sys.path.insert(0, sys.argv[1])
import retro

roots = retro.transcript_roots()
assert roots == [retro.default_root(), a, b, gz], [str(r) for r in roots]

# A compressed transcript measures like any other.
row = retro.measure(gz / "proj" / "ok.jsonl.gz", gz)
assert row is not None, "a compressed transcript produced no row"
assert row["session_id"] == "gz-1", row["session_id"]
assert (row["user_prompts"], row["correction_turns"]) == (2, 1), row
assert retro.transcript_stem(Path("x.jsonl.gz")) == "x"
assert retro.transcript_stem(Path("x.jsonl")) == "x"

# Both filename patterns are walked.
assert {p.name for _, p in retro.walk_transcripts([gz])} == {
    "ok.jsonl.gz", "big.jsonl.gz", "cut.jsonl.gz", "corrupt.jsonl.gz"}

# The same relative path under two roots stays two rows.
row_a, row_b = retro.measure(a / "proj" / "same.jsonl", a), \
    retro.measure(b / "proj" / "same.jsonl", b)
assert row_a["transcript"] == row_b["transcript"] == "proj/same.jsonl"
assert row_a["root"] and row_b["root"] and row_a["root"] != row_b["root"]
assert retro.row_key(row_a) != retro.row_key(row_b), "two roots collided"
assert retro.row_root({"transcript": "x.jsonl"}) == retro.default_root()

# A session id under two roots is reported, never merged.
assert retro.overlapping_sessions([row_a, row_b]) == 1
assert retro.overlapping_sessions([row_a]) == 0
print("port checks pass")
PY
python "$SCRATCH/check_port.py" plugins/retro/bin "$SCRATCH"
```

Expected: `port checks pass`. Verified against the ported code before this plan was written, and again against the code with all seven fixes applied — this check must keep passing to the end.

- [ ] **Step 5: Commit**

```bash
git add plugins/retro/bin/retro.py
git commit -F - <<'MSG'
feat: measure transcripts from additional roots

Roots come from the built-in directory, then an extra_transcript_dirs key in
the work directory's config file, then a path-separator delimited environment
variable, in that order. A root may hold its transcripts compressed. A row is
identified by its root and its path within that root, so a session archived
under two roots stays two rows and the count of those is printed rather than
merged away.

This is the implementation a review rejected, restored unchanged so that each
of the seven defects it was rejected for can be shown failing before it is
fixed. It is not fit to land on its own.
MSG
```

---

### Task 2: A row carries an id for its root, not a path

Defect 7. The ledger stores a configured root as a raw absolute path, in the same row as a `project` field that is redacted. Split the two jobs that field was doing: identity goes in the ledger as a short derived id, and the way back to the directory lives in the work directory.

**Files:**
- Modify: `plugins/retro/bin/retro.py` — constants block, `root_label` (replaced), `row_root`, `measure`'s row assembly, `cmd_extract`'s tail, `_moments`
- Test: `$SCRATCH/check_rootid.py`

**Interfaces:**
- Consumes from Task 1: `default_root()`, `measure(path, root)`, `row_key()`.
- Produces:
  - `root_id(root: Path) -> str` — `""` for the default root, else 12 hex characters derived from the root's path.
  - `load_roots() -> dict[str, str]` — id to directory, from the work directory; cached.
  - `save_roots(roots: list[Path]) -> None` — records this run's roots, keeping earlier entries.
  - `row_root(row: dict) -> Path | None` — `None` when the row's id is unknown here.
- Removes: `root_label`. No later task may use it.

- [ ] **Step 1: Write the failing check**

```bash
SCRATCH="${TMPDIR:-${TEMP:-/tmp}}/retro-roots"
cat > "$SCRATCH/check_rootid.py" <<'PY'
"""Defect 7: a row carries an id for its root, and the work directory maps
the id back to the directory."""
import os, sys
from pathlib import Path

scratch = Path(sys.argv[2])
work = scratch / "work_rootid"
work.mkdir(parents=True, exist_ok=True)
os.environ["RETRO_HOME"] = str(work)
os.environ["HOME"] = os.environ["USERPROFILE"] = str(scratch / "fakehome")
os.environ.pop("RETRO_TRANSCRIPT_DIRS", None)
sys.path.insert(0, sys.argv[1])
import retro

a = (scratch / "fixture" / "arch_a").resolve()
b = (scratch / "fixture" / "arch_b").resolve()

assert retro.root_id(retro.default_root()) == "", "the default root has no id"
assert retro.root_id(a) == retro.root_id(a), "an id must be stable"
assert retro.root_id(a) != retro.root_id(b), "two roots share one id"

row = retro.measure(a / "proj" / "same.jsonl", a)
assert os.sep not in row["root"] and "/" not in row["root"], \
    f"the ledger stores a path where an identifier belongs: {row['root']!r}"
assert str(Path.home()).lower() not in row["root"].lower()

# The map in the work directory is what turns the id back into a directory.
retro.save_roots([retro.default_root(), a, b])
assert retro.row_root(row) == a, retro.row_root(row)
assert retro.row_root({"transcript": "x.jsonl"}) == retro.default_root()
assert retro.row_root({"root": "0123456789ab", "transcript": "x"}) is None, \
    "an unknown root id must not resolve to a directory"

# The map is a cache: it is rebuilt from the same configuration, and keeps
# what earlier runs put in it.
retro.save_roots([a])
assert retro.row_root({"root": retro.root_id(b), "transcript": "x"}) == b
print("root id checks pass")
PY
python "$SCRATCH/check_rootid.py" plugins/retro/bin "$SCRATCH"
```

- [ ] **Step 2: Run it to confirm it fails**

Expected: `AttributeError: module 'retro' has no attribute 'root_id'`.

- [ ] **Step 3: Add the roots file to the constants block**

After `CONFIG_FILE = WORK_DIR / "config.json"`:

```python
ROOTS_FILE = WORK_DIR / "roots.json"
```

- [ ] **Step 4: Replace `root_label` and `row_root`**

Delete `root_label` entirely and put this in its place, above `row_key`:

```python
def root_id(root):
    """A root's identity in the ledger.

    The default root is the empty string and the key is left out of the row
    entirely, so a ledger written without extra roots is byte-identical to one
    written before roots existed. Any other root becomes a short digest of its
    path: derived, so the same directory always gets the same id and its rows
    survive being reconfigured; opaque, so the ledger carries no directory name
    of the machine's beside a `project` field that is redacted.
    """
    path = Path(root)
    if path == default_root():
        return ""
    return hashlib.sha256(
        os.path.normcase(str(path)).encode("utf-8")).hexdigest()[:12]


@lru_cache(maxsize=1)
def load_roots():
    """id -> directory, for every root any run has measured.

    A cache in the work directory, not a source of truth: the id is a function
    of the path, so an extract with the same configuration rewrites an entry
    that has gone missing. A row whose id is not here cannot be resolved back to
    disk, and moments() says so rather than quoting nothing.
    """
    try:
        data = json.loads(ROOTS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items()
            if isinstance(k, str) and isinstance(v, str)}


def save_roots(roots):
    """Record id -> directory for the roots this run walked, keeping the ids
    earlier runs wrote so a row from a root that is not configured today still
    resolves."""
    known = dict(load_roots())
    for root in roots:
        rid = root_id(root)
        if rid:
            known[rid] = str(root)
    if not known:
        # Nothing to record: a run with no extra roots leaves the work
        # directory exactly as it found it.
        return
    ROOTS_FILE.write_text(json.dumps(known, indent=2, sort_keys=True),
                          encoding="utf-8")
    load_roots.cache_clear()


def row_root(row):
    """The directory a row's `transcript` path is relative to, or None when the
    row's root id is not in the work directory's map. A row written before roots
    existed carries no id and belongs to the default one."""
    rid = row.get("root") or ""
    if not rid:
        return default_root()
    path = load_roots().get(rid)
    return Path(path) if path else None
```

`hashlib` is already imported. `os.path.normcase` lowercases on Windows and is identity on POSIX, so the id does not change with the spelling of a drive letter and does not merge two case-distinct POSIX directories.

- [ ] **Step 5: Store the id in the row**

In `measure`, replace the two lines that build the label and the conditional key:

```python
    label = root_label(root)

    row = {"transcript": rel}
    if label:
        row["root"] = label
```

with:

```python
    rid = root_id(root)

    row = {"transcript": rel}
    if rid:
        row["root"] = rid
```

- [ ] **Step 6: Write the map at the end of an extract, and resolve through it in a pack**

In `cmd_extract`, immediately after `STATE_FILE.write_text(...)`:

```python
    save_roots(roots)
```

In `_moments`, replace the first two lines:

```python
    root = row_root(row)
    if root is None:
        print(f"a {row.get('date') or '?'} row names a transcript root this "
              f"work directory has no record of - run extract with that root "
              f"configured", file=sys.stderr)
        return []
    path = root / row.get("transcript", "")
    if not path.is_file():
```

- [ ] **Step 7: Run both checks to confirm they pass**

```bash
SCRATCH="${TMPDIR:-${TEMP:-/tmp}}/retro-roots"
python "$SCRATCH/check_rootid.py" plugins/retro/bin "$SCRATCH"
python "$SCRATCH/check_port.py"   plugins/retro/bin "$SCRATCH"
```

Expected: `root id checks pass`, then `port checks pass`.

- [ ] **Step 8: End-to-end — a pack still quotes a moment from an extra root**

```bash
SCRATCH="${TMPDIR:-${TEMP:-/tmp}}/retro-roots"
WORK="$SCRATCH/work_pack"
rm -rf "$WORK" && mkdir -p "$WORK"
# The synthetic home is set per command, never exported: a stray HOME would
# follow the shell into the live-corpus runs later on.
FAKE="HOME=$SCRATCH/fakehome USERPROFILE=$SCRATCH/fakehome"
env $FAKE RETRO_HOME="$WORK" RETRO_TRANSCRIPT_DIRS="$SCRATCH/fixture/arch_a" \
  python plugins/retro/bin/retro.py extract --rebuild
python -c "import json,sys; print([r.get('root') for r in map(json.loads, open(sys.argv[1], encoding='utf-8'))])" "$WORK/metrics.jsonl"
env $FAKE RETRO_HOME="$WORK" python plugins/retro/bin/retro.py pack --days 7 --sessions 10
grep -c "user said" "$WORK"/pack-*.md
mv "$WORK/roots.json" "$WORK/roots.json.bak"
env $FAKE RETRO_HOME="$WORK" python plugins/retro/bin/retro.py pack --days 7 --sessions 10
mv "$WORK/roots.json.bak" "$WORK/roots.json"
```

Expected: the ledger holds one row with `None` for its root (the synthetic default one) and one with a 12-character id; `grep -c` reports at least 2; and with the map moved away, the pack prints `a <date> row names a transcript root this work directory has no record of` on stderr instead of quoting nothing in silence. Do not print pack contents — a pack holds quoted turns.

- [ ] **Step 9: Commit**

```bash
git add plugins/retro/bin/retro.py
git commit -F - <<'MSG'
fix: the ledger identifies a root, it does not spell one out

A row from a configured root stored that root as a raw absolute path, in the
same row as a project field that goes through redact(). The two jobs the field
was doing are now separate: the row carries a short digest of the root, and the
work directory keeps the map from digest to directory.

The map is a cache rather than a source of truth - the digest is a function of
the path, so any extract with the same configuration rewrites an entry that has
gone missing, and a pack that meets an id it has no record of says so instead of
quoting nothing.
MSG
```

---
### Task 3: One file, one measurement

Defect 1, and the case-folding question. Two roots that share a subtree walk the shared files twice, and every counter those sessions carry is doubled. Reduce the root list by containment as well as identity, compare roots as paths rather than as case-folded strings, and make the walk hand each file to exactly one root.

**Files:**
- Modify: `plugins/retro/bin/retro.py` — `transcript_roots` (replaced), `walk_transcripts`
- Test: `$SCRATCH/check_roots.py`

**Interfaces:**
- Consumes from Tasks 1-2: `default_root()`, `config_dirs()`, `env_dirs()`, `measure(path, root)`.
- Produces: `transcript_roots()` and `walk_transcripts(roots)` keep their signatures. New guarantee, relied on by Task 4: **every path yielded by `walk_transcripts` is under the root yielded with it, and no path is yielded twice.**

- [ ] **Step 1: Write the failing check**

```bash
SCRATCH="${TMPDIR:-${TEMP:-/tmp}}/retro-roots"
cat > "$SCRATCH/check_roots.py" <<'PY'
"""Defect 1 and the case-folding question: two roots that share a subtree."""
import os, sys
from pathlib import Path

scratch = Path(sys.argv[2])
work = scratch / "work_roots"
work.mkdir(parents=True, exist_ok=True)
os.environ["RETRO_HOME"] = str(work)
os.environ["HOME"] = os.environ["USERPROFILE"] = str(scratch / "fakehome")
sys.path.insert(0, sys.argv[1])
import retro

outer = scratch / "fixture" / "outer"
inner = outer / "nested"

# Outer named first: the inner root adds nothing and is dropped.
os.environ["RETRO_TRANSCRIPT_DIRS"] = os.pathsep.join([str(outer), str(inner)])
roots = retro.transcript_roots()
assert len(roots) == 2, f"a root inside another must be dropped: {roots}"

walked = list(retro.walk_transcripts(roots))
assert len(walked) == len({p for _, p in walked}) == 4, \
    f"a file was walked twice: {len(walked)} yields, " \
    f"{len({p for _, p in walked})} files"
rows = [r for r in (retro.measure(p, root) for root, p in walked) if r]
assert len(rows) == 4, f"expected 4 rows, got {len(rows)}"
assert sum(r["user_prompts"] for r in rows) == 8, \
    f"counters doubled: {sum(r['user_prompts'] for r in rows)} user prompts"

# Inner named first: the outer root holds files the inner one does not, so it
# stays, and the shared files are measured once under the inner root.
os.environ["RETRO_TRANSCRIPT_DIRS"] = os.pathsep.join([str(inner), str(outer)])
roots = retro.transcript_roots()
assert len(roots) == 3, f"a root containing another must be kept: {roots}"
walked = list(retro.walk_transcripts(roots))
assert len(walked) == 4, f"a file was walked twice: {len(walked)}"
claimed = {p: root for root, p in walked}
one = inner / "proj" / "one.jsonl"
assert claimed[one] == inner.resolve(), \
    f"a shared file must go to the root named first: {claimed[one]}"

# Root identity follows the platform's own case rule rather than case-folding.
import posixpath
from pathlib import PurePosixPath as P
assert posixpath.normcase("/a/B") != posixpath.normcase("/a/b"), \
    "this check assumes POSIX paths are case-sensitive"
assert P("/a/B") != P("/a/b"), "two case-distinct POSIX roots are one path"
assert str(inner).casefold() == str(inner).upper().casefold(), \
    "this check assumes case-folding merges the two spellings"
print("root checks pass")
PY
python "$SCRATCH/check_roots.py" plugins/retro/bin "$SCRATCH"
```

- [ ] **Step 2: Run it to confirm it fails**

Expected: `AssertionError: a root inside another must be dropped:` followed by three roots. Reproduced before this plan was written; the same run measured the harm downstream — 7 walk yields over 4 distinct files, 7 rows, and 14 user prompts where 8 exist.

- [ ] **Step 3: Replace `transcript_roots`**

```python
def transcript_roots():
    """Every root to walk, in order: the built-in one, then the config file's,
    then the environment's.

    Resolved, and reduced until no file is reachable from two of them. Roots are
    compared as paths, not as case-folded strings: `Path` already carries the
    platform's own rule, and case-folding merges two genuinely distinct
    directories on a case-sensitive filesystem, so the second would never be
    walked. A root nested inside one already accepted is dropped, since every
    file under it is walked from there anyway; a root that CONTAINS one is kept,
    since it holds files the inner one does not, and the walk gives the shared
    files to whichever root was named first. Either way it is said out loud: two
    roots sharing a subtree used to measure every file in it twice, which
    doubles every counter those sessions carry.

    A configured root that is not a directory is reported and dropped rather
    than measured as empty - under --rebuild its rows would otherwise vanish
    without a word.
    """
    roots = []
    for raw in [default_root()] + config_dirs() + env_dirs():
        try:
            path = Path(raw).resolve()
        except OSError as exc:
            print(f"cannot resolve a configured transcript root: {exc}",
                  file=sys.stderr)
            continue
        if path in roots:
            continue
        if not path.is_dir():
            # The built-in directory being absent is not a configuration error;
            # cmd_extract reports it, and a machine that holds only archives
            # measures them regardless.
            if path != default_root():
                print(f"configured transcript root is not a directory: {path}",
                      file=sys.stderr)
            continue
        covered = [r for r in roots if path.is_relative_to(r)]
        if covered:
            print(f"transcript root {path} is inside {covered[0]} and every "
                  f"file under it is walked from there: dropping it",
                  file=sys.stderr)
            continue
        contained = [r for r in roots if r.is_relative_to(path)]
        if contained:
            print(f"transcript root {path} contains "
                  f"{', '.join(str(r) for r in contained)}: the shared files "
                  f"are measured once, under the root named first",
                  file=sys.stderr)
        roots.append(path)
    return roots
```

`Path.is_relative_to` is stdlib from Python 3.9. It carries the platform's case rule and refuses the sibling-prefix trap that a string `startswith` falls for — `/a/bc` is not inside `/a/b`. Both were measured before this plan was written.

- [ ] **Step 4: Make the walk hand each file to one root**

In `walk_transcripts`, extend the docstring and add the `seen` set:

```python
    A file reachable from two roots is yielded once, under the first root that
    covers it. transcript_roots() already drops a root nested inside another,
    but it keeps one that contains an earlier root, and the second measurement
    of a file doubles every counter its session carries.
    """
    seen = set()
    for root in roots:
        found = set()
        for pattern in TRANSCRIPT_GLOBS:
            found.update(root.rglob(pattern))
        for path in sorted(found):
            if path in seen:
                continue
            seen.add(path)
            yield root, path
```

The set holds `Path` objects, whose hashing follows the platform's case rule — the same reason the root list stopped case-folding. It compares the spellings the walk produced, which come from roots that are already resolved; a second route to one file through a symbolic link is not covered, and the containment reduction above is what handles the ordinary case.

- [ ] **Step 5: Run the checks to confirm they pass**

```bash
SCRATCH="${TMPDIR:-${TEMP:-/tmp}}/retro-roots"
python "$SCRATCH/check_roots.py"  plugins/retro/bin "$SCRATCH"
python "$SCRATCH/check_rootid.py" plugins/retro/bin "$SCRATCH"
python "$SCRATCH/check_port.py"   plugins/retro/bin "$SCRATCH"
```

Expected: `root checks pass`, `root id checks pass`, `port checks pass`, and two lines on stderr naming the overlap in each direction.

- [ ] **Step 6: Commit**

```bash
git add plugins/retro/bin/retro.py
git commit -F - <<'MSG'
fix: two roots that share a subtree no longer measure it twice

Roots were de-duplicated by identity but not by containment, so a configured
root nested inside another - or containing it - walked the shared files twice.
Every session under the shared subtree was counted twice and every counter it
carried was doubled, with a clean exit and nothing on stderr.

The root list now drops a root that sits inside one already accepted, keeps one
that contains an earlier root because it holds files the inner one does not,
says which of the two happened, and the walk hands each file to the first root
that covers it.

Roots are also compared as paths rather than as case-folded strings. Path
equality already follows the platform: case-insensitive where the filesystem is,
case-sensitive where it is not. Case-folding was redundant on the first and
wrong on the second, where it merged two genuinely distinct directories and the
second one was never walked.
MSG
```

---

### Task 4: The subagent tag comes from inside the root

Defect 2. Whether a transcript is a subagent transcript is read from the components of its absolute path, so any ancestor directory named `subagents` marks everything beneath it. Those rows leave the session population and stop being quoted.

**Files:**
- Modify: `plugins/retro/bin/retro.py` — the row-assembly block of `measure`
- Test: `$SCRATCH/check_subagent.py`

**Interfaces:**
- Consumes from Tasks 1-3: `measure(path, root)`, `root_id()`, and the walk's guarantee that a path is always under the root it arrives with.
- Produces: no new function. `measure` raises `ValueError` when handed a path outside its root, instead of falling back to the bare filename.

- [ ] **Step 1: Write the failing check**

```bash
SCRATCH="${TMPDIR:-${TEMP:-/tmp}}/retro-roots"
cat > "$SCRATCH/check_subagent.py" <<'PY'
"""Defect 2: the subagent tag comes from the path inside its root."""
import os, sys
from pathlib import Path

scratch = Path(sys.argv[2])
work = scratch / "work_subagent"
work.mkdir(parents=True, exist_ok=True)
os.environ["RETRO_HOME"] = str(work)
os.environ["HOME"] = os.environ["USERPROFILE"] = str(scratch / "fakehome")
os.environ.pop("RETRO_TRANSCRIPT_DIRS", None)
sys.path.insert(0, sys.argv[1])
import retro

# A root that happens to live under a directory named `subagents`.
root = scratch / "fixture" / "subagents" / "archive"
plain = retro.measure(root / "proj" / "plain.jsonl", root)
assert plain["transcript"] == "proj/plain.jsonl", plain["transcript"]
assert plain["is_subagent"] is False, \
    "an ancestor of the root named `subagents` tagged an ordinary transcript"

real = retro.measure(root / "proj" / "plain" / "subagents" / "real.jsonl", root)
assert real["is_subagent"] is True, "a real subagent transcript lost its tag"

# The population split follows the tag, so the session count follows it too.
main, sub = retro.split_population([plain, real])
assert (len(main), len(sub)) == (1, 1), (len(main), len(sub))

# A path paired with the wrong root is a caller bug and says so.
try:
    retro.measure(root / "proj" / "plain.jsonl", scratch / "fixture" / "arch_a")
except ValueError:
    pass
else:
    raise AssertionError("a path outside its root was measured anyway")
print("subagent checks pass")
PY
python "$SCRATCH/check_subagent.py" plugins/retro/bin "$SCRATCH"
```

- [ ] **Step 2: Run it to confirm it fails**

Expected: `AssertionError: an ancestor of the root named 'subagents' tagged an ordinary transcript`.

- [ ] **Step 3: Read the path and the tag from the same relative path**

In `measure`, replace the block that starts at `try: rel = path.relative_to(root).as_posix()` and runs to `"is_subagent": ...`, keeping the existing comment about subagent transcripts carrying the parent's session id and adding the second paragraph:

```python
    # Subagent transcripts live under <session>/subagents/ and carry the PARENT
    # session's id. Keying rows by session id would let them overwrite the
    # parent's row — one row per transcript, tagged, is what aggregates right.
    #
    # Both the stored path and the tag are read from where the transcript sits
    # INSIDE its root. Read from the absolute path instead, any ancestor
    # directory that happens to be named `subagents` marks everything beneath it
    # as a subagent transcript, which takes those sessions out of the session
    # count and out of quoting. A path that is not under the root it was handed
    # is a caller pairing the two wrongly, and raises rather than falling back
    # to a bare filename that drops the marker as well.
    rel = path.relative_to(root)
    rid = root_id(root)

    row = {"transcript": rel.as_posix()}
    if rid:
        row["root"] = rid
    row.update({
        "is_subagent": "subagents" in rel.parts,
```

The rest of the `row.update({...})` literal is unchanged.

- [ ] **Step 4: Run the checks to confirm they pass**

```bash
SCRATCH="${TMPDIR:-${TEMP:-/tmp}}/retro-roots"
for check in subagent roots rootid port; do
  python "$SCRATCH/check_$check.py" plugins/retro/bin "$SCRATCH" || break
done
```

Expected: four lines, each ending in `checks pass`.

- [ ] **Step 5: Confirm the live corpus is unmoved by the change**

The tag used to be read from the whole path and is now read from part of it. Under the built-in root the two agree on every file, and this is the run that says so rather than assuming it.

```bash
SCRATCH="${TMPDIR:-${TEMP:-/tmp}}/retro-roots"
unset RETRO_TRANSCRIPT_DIRS
rm -rf "$SCRATCH/tag_check" && mkdir -p "$SCRATCH/tag_check"
RETRO_HOME="$SCRATCH/tag_check" python plugins/retro/bin/retro.py extract --rebuild
python - "$SCRATCH/tag_check/metrics.jsonl" "$SCRATCH/base_ledger/metrics.jsonl" <<'PY'
import json, sys
def load(p):
    return {(r.get("root") or "", r["transcript"]): r
            for r in map(json.loads, open(p, encoding="utf-8"))}
now, was = load(sys.argv[1]), load(sys.argv[2])
print("rows:", len(now), "baseline:", len(was))
print("keys added:", len(set(now) - set(was)), "removed:", len(set(was) - set(now)))
moved = [k for k in set(now) & set(was)
         if now[k]["is_subagent"] != was[k]["is_subagent"]]
print("rows whose subagent tag moved:", len(moved))
PY
```

Expected: the same number of rows as the baseline (allowing for sessions that started since), no key added or removed for a reason other than a new session, and **0** rows whose tag moved.

- [ ] **Step 6: Commit**

```bash
git add plugins/retro/bin/retro.py
git commit -F - <<'MSG'
fix: read the subagent tag from inside the root, not from the whole path

Whether a transcript is a subagent transcript was decided from the components
of its absolute path. Any ancestor directory of a configured root that happened
to carry the same name as the subagent directory marked every transcript
beneath it, which takes those sessions out of the session count and out of
quoting - the population split follows the tag.

The stored path and the tag now come from the same relative path. A path that
is not under the root it was handed raises instead of falling back to a bare
filename, which dropped the marker just as thoroughly.
MSG
```

---
### Task 5: A configuration entry is stripped, non-empty and absolute

Defects 3 and 4. Both channels feed the same list and both accept text nobody validated: an environment entry written with a space after the separator resolves against the working directory, and an empty entry in the config file *becomes* the working directory and is walked recursively with nothing said. One validation step serves both.

**Files:**
- Modify: `plugins/retro/bin/retro.py` — new `_clean_entries`, tail of `config_dirs`, body of `env_dirs`
- Test: `$SCRATCH/check_entries.py`

**Interfaces:**
- Consumes from Tasks 1-3: `transcript_roots()`, `default_root()`.
- Produces: `_clean_entries(entries: Iterable[str], source: str) -> list[Path]`. **`config_dirs()` and `env_dirs()` now return `list[Path]`, absolute and `~`-expanded, not `list[str]`.** `transcript_roots()` resolves them; nothing else calls them.

- [ ] **Step 1: Write the failing check**

```bash
SCRATCH="${TMPDIR:-${TEMP:-/tmp}}/retro-roots"
cat > "$SCRATCH/check_entries.py" <<'PY'
"""Defects 3 and 4: whitespace, empty and relative configuration entries."""
import json, os, sys
from pathlib import Path

scratch = Path(sys.argv[2])
work = scratch / "work_entries"
work.mkdir(parents=True, exist_ok=True)
os.environ["RETRO_HOME"] = str(work)
os.environ["HOME"] = os.environ["USERPROFILE"] = str(scratch / "fakehome")
sys.path.insert(0, sys.argv[1])
import retro

arch = (scratch / "fixture" / "arch_a").resolve()
cwd = Path(os.getcwd()).resolve()

# A list written with a space after the separator names the same directory.
os.environ["RETRO_TRANSCRIPT_DIRS"] = os.pathsep.join([str(arch), " " + str(arch)])
entries = retro.env_dirs()
assert all(Path(e).resolve() == arch for e in entries), \
    f"a padded entry resolved somewhere else: {[str(e) for e in entries]}"

# An empty entry, and a relative one, are named and dropped - never the
# working directory, walked recursively.
os.environ["RETRO_TRANSCRIPT_DIRS"] = os.pathsep.join([str(arch), "  "])
(work / "config.json").write_text(
    json.dumps({"extra_transcript_dirs": ["", "  ", "relative/dir"]}),
    encoding="utf-8")
roots = retro.transcript_roots()
assert cwd not in roots, f"the working directory became a transcript root: {roots}"
assert roots == [retro.default_root(), arch], [str(r) for r in roots]
print("entry checks pass")
PY
python "$SCRATCH/check_entries.py" plugins/retro/bin "$SCRATCH"
```

- [ ] **Step 2: Run it to confirm it fails**

Expected: `AssertionError: a padded entry resolved somewhere else:` followed by two entries, the second one padded and pointing under the working directory. Reproduced before this plan was written; the same run showed an empty config entry being accepted as a root equal to the working directory with no message at all.

- [ ] **Step 3: Add the validation step**

Immediately after `row_key`:

```python
def _clean_entries(entries, source):
    """Strip each entry, drop the empty ones, expand a leading `~`, and require
    what is left to be absolute.

    An entry written with a space after the separator used to resolve against
    the working directory, and an empty one used to BE the working directory -
    accepted as a root and walked recursively, with nothing said. Every rejected
    entry is named on stderr.
    """
    out = []
    for raw in entries:
        entry = raw.strip()
        if not entry:
            print(f"{source}: ignoring an empty transcript root",
                  file=sys.stderr)
            continue
        try:
            path = Path(entry).expanduser()
        except RuntimeError as exc:
            print(f"{source}: cannot expand {entry!r}: {exc}", file=sys.stderr)
            continue
        if not path.is_absolute():
            print(f"{source}: a transcript root must be an absolute path, "
                  f"ignoring {entry!r}", file=sys.stderr)
            continue
        out.append(path)
    return out
```

`expanduser` raises `RuntimeError` when a `~user` form has no home to expand to, which is why it is guarded. A relative entry is now rejected for being relative rather than reported as "not a directory", which was true but not the thing that was wrong with it.

- [ ] **Step 4: Route both channels through it**

In `config_dirs`, replace the final `return dirs` with:

```python
    return _clean_entries(dirs, f"config {CONFIG_FILE}")
```

Replace the body of `env_dirs`:

```python
    raw = os.environ.get(ROOTS_ENV_VAR) or ""
    if not raw.strip():
        # An unset or blank variable is not a configuration mistake, and must
        # not put a line on stderr on every run that never configured one.
        return []
    return _clean_entries(raw.split(os.pathsep), ROOTS_ENV_VAR)
```

`transcript_roots` already calls `Path(raw).resolve()`, which accepts the `Path` these now return.

- [ ] **Step 5: Run the checks to confirm they pass**

```bash
SCRATCH="${TMPDIR:-${TEMP:-/tmp}}/retro-roots"
for check in entries subagent roots rootid port; do
  python "$SCRATCH/check_$check.py" plugins/retro/bin "$SCRATCH" || break
done
```

Expected: five lines ending in `checks pass`, and three messages on stderr from the entry check — one naming the empty config entry, one naming the relative one, one naming the blank environment entry.

- [ ] **Step 6: Confirm an unconfigured run is still silent**

```bash
SCRATCH="${TMPDIR:-${TEMP:-/tmp}}/retro-roots"
unset RETRO_TRANSCRIPT_DIRS
rm -rf "$SCRATCH/quiet" && mkdir -p "$SCRATCH/quiet"
RETRO_HOME="$SCRATCH/quiet" python plugins/retro/bin/retro.py extract --rebuild \
  > /dev/null 2> "$SCRATCH/quiet.err"
wc -c < "$SCRATCH/quiet.err"
```

Expected: `0`. An unset variable must not produce a line about an empty entry — this is the reason for the early return in `env_dirs`, and it was caught by running it rather than by reading it.

- [ ] **Step 7: Commit**

```bash
git add plugins/retro/bin/retro.py
git commit -F - <<'MSG'
fix: validate a configured transcript root before walking it

Entries from the environment variable were not stripped, so a list written with
a space after the separator resolved that root against the working directory.
An empty or relative entry in the config file resolved to the working directory
itself and was then walked recursively, with no message.

Both channels now pass through one step that strips each entry, drops the empty
ones, expands a leading tilde and requires what is left to be absolute, naming
every entry it rejects. An unset environment variable stays silent: it is not a
configuration mistake.
MSG
```

---

### Task 6: A machine that holds only archives can still run

Defect 5. `extract` decides it cannot run before it has built its roots, so the absence of the built-in directory stops a run that had a perfectly good archive configured.

**Files:**
- Modify: `plugins/retro/bin/retro.py` — the head of `cmd_extract`
- Test: two end-to-end runs against the fixture, one with configuration and one without

**Interfaces:**
- Consumes from Tasks 1-5: `transcript_roots()`, `walk_transcripts()`.
- Produces: no new function. New behaviour: `extract` exits 2 only when the root list is empty; the built-in directory being absent is reported and the run continues if any configured root survives.

- [ ] **Step 1: Show it failing**

```bash
SCRATCH="${TMPDIR:-${TEMP:-/tmp}}/retro-roots"
mkdir -p "$SCRATCH/emptyhome"
FAKE="HOME=$SCRATCH/emptyhome USERPROFILE=$SCRATCH/emptyhome"
rm -rf "$SCRATCH/archives_only" && mkdir -p "$SCRATCH/archives_only"
env $FAKE RETRO_HOME="$SCRATCH/archives_only" \
  RETRO_TRANSCRIPT_DIRS="$SCRATCH/fixture/arch_a" \
  python plugins/retro/bin/retro.py extract --rebuild
echo "exit=$?"
```

Expected: `no session directory at ...` on stderr, nothing measured, `exit=2`. Reproduced before this plan was written.

- [ ] **Step 2: Build the roots before deciding the run cannot happen**

Replace the head of `cmd_extract`:

```python
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    roots = transcript_roots()
    if not PROJECTS_DIR.is_dir():
        # Said, not fatal. A machine that holds only archives has no built-in
        # directory at all, and exiting here left it unable to measure anything.
        print(f"no session directory at {PROJECTS_DIR}", file=sys.stderr)
    if not roots:
        sys.exit(EXIT_CANNOT_RUN)
```

and delete the now-duplicated `roots = transcript_roots()` from further down, immediately above `transcripts = list(walk_transcripts(roots))`.

The message and the exit code for a machine with no built-in directory *and* no configuration are unchanged, deliberately: that case is still `no session directory at <path>` on stderr and exit 2.

- [ ] **Step 3: Run both cases**

```bash
SCRATCH="${TMPDIR:-${TEMP:-/tmp}}/retro-roots"
FAKE="HOME=$SCRATCH/emptyhome USERPROFILE=$SCRATCH/emptyhome"
rm -rf "$SCRATCH/archives_only" && mkdir -p "$SCRATCH/archives_only"
env $FAKE RETRO_HOME="$SCRATCH/archives_only" \
  RETRO_TRANSCRIPT_DIRS="$SCRATCH/fixture/arch_a" \
  python plugins/retro/bin/retro.py extract --rebuild
echo "with an archive configured: exit=$?"
rm -rf "$SCRATCH/nothing_at_all" && mkdir -p "$SCRATCH/nothing_at_all"
unset RETRO_TRANSCRIPT_DIRS
env $FAKE RETRO_HOME="$SCRATCH/nothing_at_all" \
  python plugins/retro/bin/retro.py extract --rebuild
echo "with nothing configured: exit=$?"
```

Expected: the first run prints `no session directory at ...` on stderr, then `transcripts: 1  measured: 1  unchanged: 0  not-transcripts: 0  unreadable: 0`, `sessions in ledger: 1`, and `exit=0`. The second prints the same stderr line, nothing on stdout, and `exit=2`.

- [ ] **Step 4: Run every check again**

```bash
SCRATCH="${TMPDIR:-${TEMP:-/tmp}}/retro-roots"
for check in entries subagent roots rootid port; do
  python "$SCRATCH/check_$check.py" plugins/retro/bin "$SCRATCH" || break
done
```

Expected: five lines ending in `checks pass`.

- [ ] **Step 5: Commit**

```bash
git add plugins/retro/bin/retro.py
git commit -F - <<'MSG'
fix: extract builds its roots before deciding it cannot run

The absence of the built-in session directory ended the run before any
configured root had been looked at, so a machine holding only archives could
not measure anything at all.

The roots are built first, the missing built-in directory is reported rather
than fatal, and the run stops only when no root at all survives. A machine with
neither keeps the message and the exit code it had.
MSG
```

---

### Task 7: A broken archive is settled once; a cut one keeps what it parsed

Defect 6, both halves. A compressed file whose bytes will never decode is never fingerprinted, so every run re-reads it and every run stays flagged. A compressed file cut short throws away every record it had already parsed, where the uncompressed reader keeps everything before a truncated final line.

**Files:**
- Modify: `plugins/retro/bin/retro.py` — `TranscriptUnreadable`, new `_permanent_failure`, `read_records`, the outcome constants, `measure_outcome`, the unreadable branch of `cmd_extract`
- Test: two end-to-end runs against the compressed fixture root

**Interfaces:**
- Consumes from Tasks 1-6: `_open_transcript()`, `measure(path, root)`, `redact()`.
- Produces:
  - `TranscriptUnreadable(path, permanent=False)` — carries `.path` and `.permanent`.
  - `_permanent_failure(exc) -> bool`.
  - `CORRUPT` — a fourth outcome from `measure_outcome`, counted in the same printed bucket as `UNREADABLE`.

- [ ] **Step 1: Show both halves failing**

```bash
SCRATCH="${TMPDIR:-${TEMP:-/tmp}}/retro-roots"
FAKE="HOME=$SCRATCH/fakehome USERPROFILE=$SCRATCH/fakehome"
rm -rf "$SCRATCH/gz" && mkdir -p "$SCRATCH/gz"
for run in 1 2; do
  env $FAKE RETRO_HOME="$SCRATCH/gz" RETRO_TRANSCRIPT_DIRS="$SCRATCH/fixture/gzroot" \
    python plugins/retro/bin/retro.py extract
  echo "run $run exit=$?"
done
python -c "import json,sys; print(sorted(k.rsplit('proj',1)[-1] for k in json.load(open(sys.argv[1], encoding='utf-8'))))" "$SCRATCH/gz/state.json"
```

Expected, before the fix: both runs print `unreadable: 2` and `exit=1`, and the state file names only the transcripts that measured — the corrupt and the cut archive are absent, so the next run reads them again and flags again, for good. Reproduced before this plan was written; the cut archive had already parsed 2,360 records when it was discarded.

- [ ] **Step 2: Split the failure in two**

Replace the `TranscriptUnreadable` class and add the test below it:

```python
class TranscriptUnreadable(Exception):
    """The bytes could not be read.

    Distinct from a file that read fine and holds no conversation: one is a
    fault worth retrying, the other is a settled fact about the file. Reporting
    both as "unreadable" is what put 22 workflow journals in that bucket.

    `permanent` splits the fault in two. Compression that will not decode is a
    fact about the bytes and will read the same way forever, so the caller
    fingerprints the file and stops re-reading it; a lock or a permission is a
    fact about reaching them, and must stay retryable.
    """

    def __init__(self, path, permanent=False):
        super().__init__(str(path))
        self.path = path
        self.permanent = permanent


def _permanent_failure(exc):
    """Is this failure about the file's bytes, or about reaching them?
    gzip.BadGzipFile is an OSError, so the type test has to come first."""
    return isinstance(exc, (gzip.BadGzipFile, zlib.error, EOFError))
```

- [ ] **Step 3: Keep what a cut archive already parsed**

Replace `read_records` entirely:

```python
def read_records(path):
    """Yield parsed records, skipping malformed lines. A live session is being
    appended to while we read it; a truncated final line is normal, not an
    error.

    A compressed transcript that ends before its end-of-stream marker is the
    same fact wearing a different exception: everything decoded before the cut
    is real conversation, so it is kept and the shortfall is reported. Only a
    file that yields nothing at all is unreadable - throwing away thousands of
    parsed records because the last one was cut is what the uncompressed path
    never did.

    Raises TranscriptUnreadable if the file cannot be opened, or if reading
    fails partway through for any other reason. Returning an empty stream
    instead is what made a read failure indistinguishable from a file with no
    conversation in it.
    """
    parsed = 0
    try:
        with _open_transcript(path) as fh:
            lines = iter(fh)
            while True:
                try:
                    line = next(lines)
                except StopIteration:
                    break
                except EOFError as exc:
                    if not parsed:
                        raise TranscriptUnreadable(path, permanent=True) from exc
                    print(f"transcript ends mid-stream, keeping the "
                          f"{parsed} records before the cut: "
                          f"{redact(str(path))}", file=sys.stderr)
                    break
                # json.loads tolerates surrounding whitespace, so testing for
                # blankness beats copying every line of a gigabyte corpus.
                if not line or line.isspace():
                    continue
                try:
                    record = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                parsed += 1
                yield record
    # gzip.BadGzipFile is an OSError, but a corrupt member can raise zlib.error
    # and a cut-short one EOFError - neither is. Letting either out of here ends
    # the whole run over one file.
    except (OSError, EOFError, zlib.error) as exc:
        raise TranscriptUnreadable(
            path, permanent=_permanent_failure(exc)) from exc
```

`TranscriptUnreadable` is not an `OSError`, so the inner raise passes through the outer handler untouched. The stderr line goes through `redact()` like every other piece of text this tool prints.

- [ ] **Step 4: Add the fourth outcome**

Below the three outcome constants:

```python
# A fourth outcome, reported in the same bucket as UNREADABLE so the printed
# line keeps its three counts: the bytes are broken for good rather than
# briefly out of reach.
CORRUPT = "corrupt"
```

In `measure_outcome`:

```python
    try:
        row = measure(path, root)
    except TranscriptUnreadable as exc:
        return (CORRUPT if exc.permanent else UNREADABLE), None
    return (MEASURED, row) if row is not None else (NOT_TRANSCRIPT, None)
```

- [ ] **Step 5: Record the settled failure in `cmd_extract`**

Replace the unreadable branch:

```python
            if outcome in (UNREADABLE, CORRUPT):
                unreadable += 1
                if outcome == CORRUPT:
                    # Bytes that will not decode are a settled fact about the
                    # file, recorded like any other. Left unrecorded, the file
                    # is re-read on every run and the run stays flagged for
                    # good. A transient failure - a lock, a permission - is
                    # still left out, so a live transcript briefly out of reach
                    # is retried rather than dropped.
                    state[str(path)] = fingerprint
                continue
```

- [ ] **Step 6: Run both halves again**

```bash
SCRATCH="${TMPDIR:-${TEMP:-/tmp}}/retro-roots"
FAKE="HOME=$SCRATCH/fakehome USERPROFILE=$SCRATCH/fakehome"
rm -rf "$SCRATCH/gz" && mkdir -p "$SCRATCH/gz"
for run in 1 2; do
  env $FAKE RETRO_HOME="$SCRATCH/gz" RETRO_TRANSCRIPT_DIRS="$SCRATCH/fixture/gzroot" \
    python plugins/retro/bin/retro.py extract
  echo "run $run exit=$?"
done
python -c "import json,sys; print(sorted(k.rsplit('proj',1)[-1] for k in json.load(open(sys.argv[1], encoding='utf-8'))))" "$SCRATCH/gz/state.json"
```

Expected, and measured before this plan was written:

| | run 1 | run 2 |
|---|---|---|
| printed | `transcripts: 5  measured: 4  unchanged: 0  not-transcripts: 0  unreadable: 1` | `transcripts: 5  measured: 0  unchanged: 5  not-transcripts: 0  unreadable: 0` |
| exit | 1 | 0 |
| stderr | one line naming the cut archive and the 2,360 records kept | nothing |

and the state file names all five files, the corrupt one included.

- [ ] **Step 7: Run every check again**

```bash
SCRATCH="${TMPDIR:-${TEMP:-/tmp}}/retro-roots"
for check in entries subagent roots rootid port; do
  python "$SCRATCH/check_$check.py" plugins/retro/bin "$SCRATCH" || break
done
```

Expected: five lines ending in `checks pass`.

- [ ] **Step 8: Commit**

```bash
git add plugins/retro/bin/retro.py
git commit -F - <<'MSG'
fix: tell a broken archive apart from one that is briefly out of reach

A compressed transcript whose bytes will never decode was never fingerprinted,
so every run read it again and every run stayed flagged. A read failure is now
either a fact about the bytes - which is recorded, like any other settled fact
about a file - or a fact about reaching them, which stays retryable exactly as
before.

An archive cut short also threw away every record it had already parsed, where
the uncompressed reader keeps everything before a truncated final line. The two
now agree: the records before the cut are kept, the shortfall is reported on
stderr, and only a file that yields nothing at all counts as unreadable.
MSG
```

---
### Task 8: Prove it against the live corpus, sweep the docs, and scan

**Files:**
- Modify: `docs/plans/2026-08-12-retro-design.md` — the `extract` bullet, the metrics-row field list, the row-keying paragraph, the "Open" bullet about how far back transcripts reach
- Modify: `docs/plans/2026-08-18-retro-measurement-fixes.md` — the verification table's "no-config behaviour" row, and a new "Fix 3 — measured after implementation" subsection
- Test: `plugins/core/bin/repo-privacy-audit` over the worktree, plus the whole-corpus comparison below

**Interfaces:**
- Consumes: the baseline ledger from Task 1 step 3 and every measurement taken since.
- Produces: nothing code depends on.

- [ ] **Step 1: Compare the whole corpus against the baseline, no configuration**

A live corpus grows while it is being measured, so a byte comparison fails for a reason that has nothing to do with the change: measured before this plan was written, the only differing row was the session doing the measuring, and only in counters that grow. Compare keys and fields, and re-take the baseline so the two runs bracket the same corpus.

```bash
SCRATCH="${TMPDIR:-${TEMP:-/tmp}}/retro-roots"
unset RETRO_TRANSCRIPT_DIRS
for d in final_before final_after; do rm -rf "$SCRATCH/$d"; mkdir -p "$SCRATCH/$d"; done
RETRO_HOME="$SCRATCH/final_after"  python plugins/retro/bin/retro.py extract --rebuild > "$SCRATCH/final_after.out"
RETRO_HOME="$SCRATCH/final_before" python "$SCRATCH/retro_baseline.py"   extract --rebuild > "$SCRATCH/final_before.out"
diff <(sed 's|^ledger:.*|ledger: <work>|' "$SCRATCH/final_before.out") \
     <(sed 's|^ledger:.*|ledger: <work>|' "$SCRATCH/final_after.out") && echo "stdout identical"
ls "$SCRATCH/final_after"
python - "$SCRATCH/final_before/metrics.jsonl" "$SCRATCH/final_after/metrics.jsonl" <<'PY'
import json, sys

def load(path):
    return {(r.get("root") or "", r["transcript"]): r
            for r in map(json.loads, open(path, encoding="utf-8"))}

was, now = load(sys.argv[1]), load(sys.argv[2])
print("rows:", len(was), "->", len(now))
print("keys added:", len(set(now) - set(was)),
      " keys removed:", len(set(was) - set(now)))
differing = {k: [f for f in set(was[k]) | set(now[k])
                 if was[k].get(f) != now[k].get(f)]
             for k in set(was) & set(now) if was[k] != now[k]}
print("rows differing:", len(differing))
# Fields a session that is still running can move between the two measurements.
growth = {"turns", "tool_calls", "tool_errors", "tool_retries", "user_prompts",
          "correction_turns", "approval_turns", "interrupts", "skills_used",
          "permission_mode_changes", "queued_prompts", "skill_runs",
          "duration_s", "tokens_in", "tokens_out", "cache_read", "git_branch"}
for key, fields in differing.items():
    verdict = "a live session growing" if set(fields) <= growth else "LOOK AT THIS"
    print(" ", verdict, "->", sorted(fields))
PY
```

Expected: `stdout identical`; `final_after` holding `metrics.jsonl` and `state.json` and **no** `roots.json`; no key added or removed; and every differing row explained by a live session growing. Any row printed as `LOOK AT THIS` stops the task until it is explained.

- [ ] **Step 2: Measure the whole fixture end to end, both channels at once**

```bash
SCRATCH="${TMPDIR:-${TEMP:-/tmp}}/retro-roots"
WORK="$SCRATCH/work_e2e"
FAKE="HOME=$SCRATCH/fakehome USERPROFILE=$SCRATCH/fakehome"
rm -rf "$WORK" && mkdir -p "$WORK"
python -c "import json,sys; open(sys.argv[1],'w').write(json.dumps({'extra_transcript_dirs':[sys.argv[2], sys.argv[3]]}))" \
  "$WORK/config.json" "$SCRATCH/fixture/arch_a" "$SCRATCH/fixture/outer"
env $FAKE RETRO_HOME="$WORK" \
  RETRO_TRANSCRIPT_DIRS="$SCRATCH/fixture/arch_b$(python -c 'import os;print(os.pathsep)')$SCRATCH/fixture/outer/nested" \
  python plugins/retro/bin/retro.py extract --rebuild
echo "exit=$?"
python - "$WORK/metrics.jsonl" <<'PY'
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1], encoding="utf-8")]
print("rows:", len(rows))
print("distinct roots:", len({r.get("root") or "" for r in rows}))
print("subagent rows:", sum(1 for r in rows if r["is_subagent"]))
print("relative paths:", sorted(r["transcript"] for r in rows))
PY
```

Expected, measured before this plan was written:

```
transcript root <...>/fixture/outer/nested is inside <...>/fixture/outer and every file under it is walked from there: dropping it
transcripts: 6  measured: 6  unchanged: 0  not-transcripts: 0  unreadable: 0
sessions in ledger: 6
roots: 4  session ids in more than one root: 1 (not deduplicated)
```

then `rows: 6`, `distinct roots: 4` (the synthetic default one plus three), `subagent rows: 0`, and the relative paths `nested/proj/one.jsonl`, `nested/proj/three.jsonl`, `nested/proj/two.jsonl`, `proj/live.jsonl`, and `proj/same.jsonl` twice — once per sibling archive. The three nested transcripts appear once each, under the outer root, which is the whole point of the containment reduction.

- [ ] **Step 3: Sweep what the change made false**

REQUIRED SUB-SKILL: use `core:finding-what-a-change-made-false`, scoped to this branch's diff.

```bash
git diff "$(git merge-base HEAD main)"..HEAD --stat
grep -n "walk session transcripts\|transcript, is_subagent\|keyed by transcript path\|roughly two months" docs/plans/2026-08-12-retro-design.md
grep -rn "extract\|transcript" plugins/retro/skills/*/SKILL.md | head -20
```

The three skills invoke `extract` and `pack` and describe reading a pack. Confirm from the grep that none of them describes the row schema, the walk or `extract`'s stdout; edit any that does.

- [ ] **Step 4: Correct the design document**

Four edits in `docs/plans/2026-08-12-retro-design.md`:

1. The `extract` bullet says it walks "session transcripts" — say that it walks the built-in directory plus any configured extra roots, that a root may hold them compressed, and that a file reachable from two roots is measured once.
2. The metrics-row field list gains `root` — present only on rows from a non-default root, and holding an id rather than a path.
3. The paragraph saying rows are keyed by transcript path — a row's identity is now the pair of `root` and `transcript`, and the directory that id stands for lives in the work directory, not in the ledger.
4. The "Open" bullet stating that transcripts begin on a fixed date and that "all history" is roughly two months — this is the mechanism that lifts it. Reword it to say the window is whatever the configured roots cover.

No path, no root name, and no other project appears in any of these edits.

- [ ] **Step 5: Record the measurements in the spec**

In `docs/plans/2026-08-18-retro-measurement-fixes.md`, fill the "no-config behaviour" row of the verification table with the result from step 1, and add a "Fix 3 — measured after implementation" subsection in the same shape as the one fix 1 has: the fixture run's row count, the overlap count, the two runs over the compressed root, and the archives-only run. Aggregates only — no sample turns, no paths.

- [ ] **Step 6: Privacy scan and commit-message read-back**

```bash
sh plugins/core/bin/repo-privacy-audit -C .
git log "$(git merge-base HEAD main)"..HEAD --format='%B'
git diff "$(git merge-base HEAD main)"..HEAD -- docs plugins | grep -n "Users\|home/\|C:" | head
```

Expected: the audit reports only the known accepted hit — the author's name and address in commit metadata, published with this repository on purpose. The commit messages read back as written: a substitution inside one is silent and nobody re-reads metadata. The last grep must find nothing; a filesystem path from this machine in a tracked file is a stop-everything finding, not a nit.

- [ ] **Step 7: Commit**

```bash
git add docs/plans/2026-08-12-retro-design.md docs/plans/2026-08-18-retro-measurement-fixes.md
git commit -F - <<'MSG'
docs: fold the extra-roots change back into the design and the spec

The design document described one transcript directory, a row schema with no
root field, rows keyed by transcript path alone, and a fixed history window
that this change is the mechanism for lifting. The spec's verification table
now carries measured results rather than expectations.
MSG
```

---

## Verification

Everything below was measured before this plan was written, read-only against the live corpus or against the synthetic fixture, from an isolated work directory. Re-take every live number at execution time: the corpus grows.

| Check | Before | Expected after |
|---|---|---|
| Files under the built-in root | 1,943 `.jsonl`, 0 `.jsonl.gz` | unchanged |
| Ledger with no configuration | 1,921 rows | same rows, same fields, no new file in the work directory |
| `extract` stdout with no configuration | four counters plus two lines | identical apart from the work-directory path |
| stderr with no configuration | empty | empty |
| Two nested roots, three shared files | 7 walk yields over 4 files, 7 rows, 14 user prompts, exit 0, nothing said | 4 yields, 4 rows, 8 user prompts, and the overlap named on stderr |
| Root de-duplication key | case-folded string — redundant on a case-insensitive filesystem, wrong on a case-sensitive one | resolved `Path`, which carries the platform's own rule |
| Ordinary transcript under a root whose ancestor is named `subagents` | tagged as a subagent, dropped from the session population | tagged correctly; a real subagent transcript still tagged |
| Environment entry padded with a space | resolved against the working directory | resolves to the directory named |
| Empty config entry | became the working directory, walked recursively, no message | named on stderr and dropped |
| Relative config entry | reported as "not a directory" | reported as not absolute |
| Unset environment variable | silent | still silent |
| Built-in directory absent, archive configured | exit 2, nothing measured | archive measured, absence reported, exit 0 |
| Built-in directory absent, nothing configured | exit 2 | exit 2, same message |
| Compressed root, two consecutive runs | `unreadable: 2` and exit 1 both times; neither file fingerprinted | run 1 `unreadable: 1` exit 1, run 2 `unreadable: 0` exit 0, all five files fingerprinted |
| Archive cut short after 2,360 parsed records | discarded entirely, no row | row measured from the records before the cut, shortfall on stderr |
| Ledger `root` field | absolute path, account name included, beside a redacted `project` | 12-character derived id, with the directory in the work directory's map |
| Pack quoting from an extra root | quoted | still quoted; an id the work directory has no record of is named on stderr |
| Session ids under two roots | reported, never merged | unchanged |
| Full rebuild wall clock | 5.45 s over 1,943 files | 5.63 s with every fix applied — no material regression |
| Privacy audit over the worktree | only the known accepted hit | unchanged |

## Self-review notes

- Every function named in a task's **Interfaces** block is defined in that task or an earlier one. `root_label` is introduced by the port in Task 1 and deleted in Task 2; no task after Task 2 refers to it.
- `config_dirs()` and `env_dirs()` change return type in Task 5, from `list[str]` to `list[Path]`. `transcript_roots()` is their only caller and accepts both, so Tasks 3 and 4 are unaffected by the order.
- The fixture built in Task 1 carries every file the later tasks need; no task builds a second one.
- The five check scripts are cumulative: each task runs its own and every earlier one.

## Questions for the operator

1. The config file is `config.json` in the work directory, with a top-level `extra_transcript_dirs` key — confirm that name, or give a different one.
2. A row stores an opaque 12-character id for its root, and the id-to-directory map lives in `roots.json` in the work directory. The alternative is a readable path in the ledger, which is what the rejected implementation did and what a reviewer objected to. Confirm the id, or say you would rather read directory names in the ledger and accept the inconsistency with the redacted `project` field.
3. When one configured root contains another, the outer one is kept, the shared files are measured under whichever root was named first, and both facts are printed. The alternative is to refuse to run until the configuration is fixed. Confirm the first.
4. A configured root that is not a directory is reported on stderr, dropped, and the run continues with exit 0 — should an unreachable root flag the run (exit 1) instead, so that a run cannot quietly measure less than it was configured to?
5. A malformed or unreadable config file stops the run with exit 2 rather than being ignored — confirm that a broken config should block a retrospective outright.
6. An archive cut short now produces a row measured from the records before the cut, and is fingerprinted at its current size — an undercounted row rather than no row. Confirm that is the trade you want, given the uncompressed reader has always behaved that way for a truncated final line.
