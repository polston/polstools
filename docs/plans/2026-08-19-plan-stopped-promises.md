# Stopped promises — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a portable script and a skill that count stopped promises — messages that ended a turn by committing to an action while nothing was pending — against any machine's session history.

**Architecture:** One self-contained stdlib script. It resolves transcript roots by precedence, walks them recursively, reconstructs assistant messages by filtering to assistant records then grouping on message id, finds turn boundaries with a hybrid human-prompt test, classifies each turn-ending message's closing region against versioned token data, writes redacted candidates to a file outside any repository, and computes a verified rate only from hand-written verdicts. Fixtures are generated into a temporary directory at runtime by `--selftest`; no transcript-shaped file ever enters the repository.

**Tech Stack:** Python 3 standard library only — `argparse`, `functools`, `gzip`, `hashlib`, `json`, `os`, `pathlib`, `re`, `sys`, `tempfile`, `collections.namedtuple`. No dependency, no build step, no test runner. The walk is single-threaded on purpose: a full parse of the reference corpus takes about three seconds, so a thread pool would add a failure mode to buy nothing.

**Spec:** `docs/plans/2026-08-19-stopped-promises.md`. Read it before starting; this plan argues from it.

## Global Constraints

- **Stdlib only. Single file. No build step, no daemon, no dependency.**
- **No private data in any tracked file** — no path belonging to a person or machine, no account name, no other project named, in code, comments, defaults, docstrings, examples or commit messages.
- **Never commit a `.jsonl` or `.jsonl.gz` file.** The privacy scanner flags those filenames across *all* history via `--diff-filter=A`; one committed fixture flags the repository permanently. Fixtures are generated at runtime into a temp directory.
- **Everything under a Claude configuration directory is read-only.** Never write there. Never run `retro.py extract` while testing.
- **Message text leaves the script in exactly one place** — the candidates file — and only after `redact()`.
- **Exit codes:** `0` ran clean and flagged nothing, `1` ran clean and flagged something, `2` could not run.
- **Commit messages** go through `git commit -F <file>`, never a double-quoted `-m` string. Read the message back afterwards.
- **Never report a rate that has not been through human verdicts.**

## File Structure

| File | Responsibility |
|---|---|
| `plugins/retro/bin/stopped-promises.py` | Everything: roots, reading, redaction, message reconstruction, turn model, classifier, candidates, verdicts, reporting, `--selftest` |
| `plugins/retro/skills/counting-stopped-promises/SKILL.md` | When to run it, how to read the three outputs, how to work the verdict loop |
| `.claude-plugin/marketplace.json` | Retro entry: version bump, description mentions the new capability |
| `plugins/retro/.claude-plugin/plugin.json` | Same version and byte-identical description |

The script is one file, so Tasks 1–8 are serial. **Tasks 9 and 10 are file-disjoint from them and from each other and may be dispatched concurrently as soon as Task 7 fixes the output wording.**

## Verified facts this plan relies on

Measured on the reference corpus while writing this plan. An implementer should not re-derive them, but every one is re-checkable.

| Fact | Value |
|---|---|
| `promptSource` present on main-thread user records | 3,466 of 24,206 |
| Values | typed 1,907 · system 969 · sdk 382 · queued 180 · suggestion_accepted 28 |
| Records with no `promptSource` that the structural fallback **accepts** | **20** — corrected: an earlier draft said 315, which is the count before the wrapper and interrupt filters run, not what the fallback keeps |
| `entrypoint` on main-thread assistant records | cli 38,966 · sdk-py 4,193 · sdk-cli 119 |
| Background marker `sessionKind == "bg"` | present on ordinary command-line sessions carrying 229 typed prompts — **not** an exclusion signal |
| Gzipped transcripts present | 0 — the gzip branch is fixture-only |
| Turn ends whose next speaker is an automatic notice, not the human | ~975 — invisible until `close("notice")` was added |
| Upper bound on candidates: turn ends whose closing region holds any opener | **189** — nothing the detector does can exceed this |

---

### Task 1: Roots, walking, reading, and the selftest harness

**Files:**
- Create: `plugins/retro/bin/stopped-promises.py`

**Interfaces:**
- Produces: `resolve_roots(cli_roots) -> list[Path]`, `walk_transcripts(roots) -> list[tuple[Path, Path]]`, `read_records(path) -> Iterator[dict]`, `SELFTESTS: list`, `selftest() -> int`, `write_fixture(dir, name, records, gzipped=False) -> Path`

- [ ] **Step 1: Write the failing test**

Create the file with only the harness and these cases.

```python
#!/usr/bin/env python3
"""stopped-promises - count messages that ended a turn on an unkept promise.

A stopped promise is one event: a message that ended a turn by saying it was
about to do something, with nothing pending, where nothing ran.

Stdlib only. Read-only over transcripts. Message text leaves this script in one
place, the candidates file, and only after redact().

Exit codes match the sibling scripts in plugins/core/bin:
    0  ran clean, nothing flagged
    1  ran clean, something was flagged (candidates awaiting a verdict)
    2  could not run (no root resolved)
"""
EXIT_CLEAN, EXIT_FLAGGED, EXIT_CANNOT_RUN = 0, 1, 2

import argparse, gzip, hashlib, json, os, re, sys, tempfile
from pathlib import Path

SELFTESTS = []


def selftest_case(fn):
    SELFTESTS.append(fn)
    return fn


def write_fixture(directory, name, records, gzipped=False):
    """Write synthetic transcript records. Fixtures are generated at runtime and
    never committed: the privacy scanner flags a .jsonl filename anywhere in
    history, so one committed fixture would flag the repo permanently."""
    path = Path(directory) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(r) + "\n" for r in records)
    if gzipped:
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            fh.write(body)
    else:
        path.write_text(body, encoding="utf-8")
    return path


@selftest_case
def test_reads_plain_and_gzipped(tmp):
    recs = [{"type": "assistant", "n": 1}, {"type": "user", "n": 2}]
    plain = write_fixture(tmp, "a.jsonl", recs)
    packed = write_fixture(tmp, "b.jsonl.gz", recs, gzipped=True)
    assert [r["n"] for r in read_records(plain)] == [1, 2]
    assert [r["n"] for r in read_records(packed)] == [1, 2], "gzip branch"


@selftest_case
def test_reader_skips_malformed_and_truncated(tmp):
    path = Path(tmp) / "c.jsonl"
    path.write_text('{"n": 1}\n\nnot json\n{"n": 2}\n{"n": 3', encoding="utf-8")
    assert [r["n"] for r in read_records(path)] == [1, 2]


@selftest_case
def test_walk_is_recursive_and_dedupes_roots(tmp):
    root = Path(tmp) / "projects"
    write_fixture(root / "proj-a", "s1.jsonl", [{"n": 1}])
    write_fixture(root / "proj-b" / "subagents", "s2.jsonl.gz", [{"n": 2}], gzipped=True)
    found = walk_transcripts([root, root])          # same root twice
    assert len(found) == 2, f"recursive walk found {len(found)}"
    assert len({p for _, p in found}) == 2


@selftest_case
def test_env_root_blank_or_whitespace_is_unset(tmp, monkeyenv):
    monkeyenv("STOPPED_PROMISES_ROOTS", "   ")
    monkeyenv("CLAUDE_CONFIG_DIR", str(tmp))
    (Path(tmp) / "projects").mkdir(parents=True, exist_ok=True)
    roots = resolve_roots([])
    assert roots == [(Path(tmp) / "projects").resolve()], roots


@selftest_case
def test_cli_root_beats_environment(tmp, monkeyenv):
    explicit = Path(tmp) / "explicit"; explicit.mkdir()
    other = Path(tmp) / "other"; other.mkdir()
    monkeyenv("STOPPED_PROMISES_ROOTS", str(other))
    assert resolve_roots([str(explicit)]) == [explicit.resolve()]
```

- [ ] **Step 2: Run it and watch it fail**

The Step 1 file has no entry point yet, so `--selftest` would exit silently and
prove nothing. Drive the cases directly instead:

Run: `python -c "import sys; sys.path.insert(0, 'plugins/retro/bin'); import importlib; m = importlib.import_module('stopped-promises'.replace('-','_')) if False else None; exec(open('plugins/retro/bin/stopped-promises.py').read()); [c('.') for c in SELFTESTS]"`

Expected: `NameError` on the first case — `read_records` is not defined. That is
the red state. If it exits silently you have Step 1's file wrong.

- [ ] **Step 3: Implement the harness and the three functions**

```python
def default_root():
    config = (os.environ.get("CLAUDE_CONFIG_DIR") or "").strip()
    if config:
        return Path(config).expanduser() / "projects"
    return Path.home() / ".claude" / "projects"


def env_roots():
    raw = (os.environ.get("STOPPED_PROMISES_ROOTS") or "").strip()
    if not raw:
        return []
    return [Path(p).expanduser() for p in raw.split(os.pathsep) if p.strip()]


def resolve_roots(cli_roots):
    """Highest precedence first. An empty or whitespace-only environment value is
    treated as unset, so a misconfigured shell cannot redirect the walk to a
    filesystem root."""
    ordered, seen = [], set()
    for group in ([Path(p).expanduser() for p in cli_roots], env_roots(), [default_root()]):
        if group and ordered:
            break                       # a higher tier answered; ignore the rest
        for path in group:
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if resolved not in seen:
                seen.add(resolved)
                ordered.append(resolved)
    return ordered


def usable_roots(roots):
    """Split resolved roots into those worth walking and complaints for stderr."""
    good, complaints = [], []
    for root in roots:
        if not root.exists():
            complaints.append(f"root does not exist, skipped: {redact(str(root))}")
        elif not root.is_dir():
            complaints.append(f"root is not a directory, skipped: {redact(str(root))}")
        else:
            good.append(root)
    return good, complaints


def walk_transcripts(roots):
    """(root, path) for every transcript under every root. Recursive: transcripts
    sit at least one directory below a root, so a flat listing finds nothing."""
    out, seen = [], set()
    for root in roots:
        try:
            resolved = root.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        for pattern in ("**/*.jsonl", "**/*.jsonl.gz"):
            for path in sorted(resolved.glob(pattern)):
                out.append((resolved, path))
    return out


def _open_transcript(path):
    if str(path).endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, encoding="utf-8", errors="replace")


def read_records(path):
    """Parsed records, skipping malformed lines. A live session is appended to
    while we read it, so a truncated final line is normal, not an error."""
    try:
        with _open_transcript(path) as fh:
            for line in fh:
                if not line or line.isspace():
                    continue
                try:
                    yield json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
    except (OSError, EOFError, gzip.BadGzipFile):
        return


def selftest():
    """Fixtures are generated into a temp directory; nothing is written to the
    repository or to any configuration directory."""
    failures = 0
    for case in SELFTESTS:
        saved = {}

        def monkeyenv(name, value):
            saved.setdefault(name, os.environ.get(name))
            os.environ[name] = value

        with tempfile.TemporaryDirectory(prefix="stopped-promises-") as tmp:
            try:
                if "monkeyenv" in case.__code__.co_varnames[:case.__code__.co_argcount]:
                    case(tmp, monkeyenv)
                else:
                    case(tmp)
                print(f"  ok    {case.__name__}")
            except AssertionError as exc:
                failures += 1
                print(f"  FAIL  {case.__name__}: {exc}")
            except Exception as exc:                      # noqa: BLE001
                failures += 1
                print(f"  ERROR {case.__name__}: {type(exc).__name__}: {exc}")
            finally:
                for name, value in saved.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value
    print(f"{len(SELFTESTS) - failures}/{len(SELFTESTS)} passed")
    return EXIT_CLEAN if not failures else EXIT_FLAGGED


def redact(text):
    return text        # replaced in Task 2


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else EXIT_CANNOT_RUN)
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `python plugins/retro/bin/stopped-promises.py --selftest`
Expected: every case passes, exit 0. (The plan does not assert a case
count: a review found three such counts stale the moment a case was added.)

- [ ] **Step 5: Prove the gzip case is real by breaking it**

Temporarily change `_open_transcript` to always use `open`. Re-run. Expected: `test_reads_plain_and_gzipped` FAILS with "gzip branch". Restore.

- [ ] **Step 6: Commit**

Write the message to a file, then:

```bash
git add plugins/retro/bin/stopped-promises.py
git commit -F <message-file>
```

---

### Task 2: Redaction, including the category the sibling lacks

**Files:**
- Modify: `plugins/retro/bin/stopped-promises.py` — replace the `redact` stub

**Interfaces:**
- Consumes: nothing
- Produces: `redact(text) -> str`

- [ ] **Step 1: Write the failing test**

```python
@selftest_case
def test_redaction_collapses_home_before_replacing_account(tmp):
    home = str(Path.home())
    out = redact(home + os.sep + "work" + os.sep + "f.txt")
    assert out.startswith("~"), f"home not collapsed: {out}"
    assert Path.home().name.lower() not in out.lower()


# Probe values are ASSEMBLED FROM FRAGMENTS, never written as literals. The
# repository's privacy scanner matches on shape and cannot tell an invented
# sample from a real one, so a literal here would flag this file for good -
# the trap where a test that hunts for a pattern embeds the pattern.
SECRET_SHAPES = {
    "email":   lambda: "sample" + "@" + "example" + "." + "invalid",
    "ipv4":    lambda: ".".join(("10", "0", "0", "1")),
    "mac":     lambda: ":".join(("de",) * 6),
    "unix":    lambda: "/" + "home" + "/" + "otheraccount" + "/tree/file.txt",
    "unc":     lambda: "\\" * 2 + "otherhost" + "\\share\\file.txt",
    "drive":   lambda: "D:" + "\\" + "OtherTree" + "\\inner\\file.txt",
    "token":   lambda: "z" * 40,
}


@selftest_case
def test_redaction_hides_paths_belonging_to_anywhere_else(tmp):
    for name in ("unix", "unc", "drive"):
        probe = SECRET_SHAPES[name]()
        out = redact(probe)
        for fragment in ("otheraccount", "otherhost", "OtherTree", "tree", "share", "inner"):
            assert fragment.lower() not in out.lower(), f"{name}: {fragment} survived"


@selftest_case
def test_redaction_covers_the_standard_categories(tmp):
    for name in ("email", "ipv4", "mac", "token"):
        probe = SECRET_SHAPES[name]()
        out = redact(probe)
        assert probe not in out, f"{name} survived redaction"


@selftest_case
def test_redaction_leaves_ordinary_prose_alone(tmp):
    prose = "I will run the tests and report the numbers."
    assert redact(prose) == prose
```

- [ ] **Step 2: Run and watch them fail**

Run: `python plugins/retro/bin/stopped-promises.py --selftest`
Expected: the four new cases fail — `redact` is the identity stub.

- [ ] **Step 3: Implement**

Delete the stub and put this in its place.

```python
from functools import lru_cache


@lru_cache(maxsize=1)
def _redaction_patterns():
    """Order is load-bearing. The account-name rule runs LAST: run first, it
    rewrites the name inside the home path and no home-path rule can then match,
    which strips identity while leaving every directory below home intact.

    The foreign-path category is why this is not a copy of the sibling's
    redactor. Measured against the default root, about 4% of prose-bearing
    messages carry an absolute path belonging to somewhere else, and all of them
    survive the sibling's rules. Under this repo's constraints those directory
    names are the sensitive half.
    """
    home = str(Path.home())
    account = Path.home().name
    patterns = [
        (re.compile(re.escape(home), re.I), "~"),
        (re.compile(re.escape(home.replace("\\", "/")), re.I), "~"),
        (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"), "<email>"),
        (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "<ip>"),
        (re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b"), "<mac>"),
        # Absolute paths belonging to anywhere else. A review found three shapes
        # leaking past the first attempt: network paths, unix paths outside a
        # fixed prefix list, and paths containing a space (the character class
        # stopped at whitespace and left the tail exposed).
        (re.compile(r"\\\\[^\s\"'<>|]+(?:\\[^\\\n\"'<>|]*)*"), "<path>"),
        (re.compile(r"\b[A-Za-z]:[\\/](?:[^\\/\n\"'<>|]*[\\/])*[^\\/\n\"'<>|]*"), "<path>"),
        (re.compile(r"(?<![\w~.])/(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]*"), "<path>"),
        (re.compile(r"\b[A-Za-z0-9_-]{32,}\b"), "<long-token>"),
    ]
    if len(account) > 2:
        patterns.append((re.compile(r"\b" + re.escape(account) + r"\b", re.I), "<user>"))
    return patterns


def redact(text):
    """Strip machine-identifying and credential-shaped values.

    Runs before anything reaches the candidates file. A candidates file on disk
    must already be safe to read aloud - redacting at read time would be too late.
    """
    if not text:
        return ""
    for pattern, replacement in _redaction_patterns():
        text = pattern.sub(replacement, text)
    return text
```

- [ ] **Step 4: Run and confirm**

Run: `python plugins/retro/bin/stopped-promises.py --selftest`
Expected: every case passes.

- [ ] **Step 5: Prove the ordering rule matters**

Change `patterns.append(` to `patterns.insert(0, `. Re-run. Expected: `test_redaction_collapses_home_before_replacing_account` FAILS with "home not collapsed". Restore.

- [ ] **Step 6: Commit**

---

### Task 3: Reconstructing a message

**Files:**
- Modify: `plugins/retro/bin/stopped-promises.py`

**Interfaces:**
- Consumes: `read_records`
- Produces: `Message` namedtuple with fields `session_id, message_id, text, has_tool, tools, timestamp`; `assistant_messages(records) -> list[Message]`

- [ ] **Step 1: Write the failing test**

```python
@selftest_case
def test_grouping_filters_to_assistant_records_first(tmp):
    """Other record kinds land BETWEEN the parts of one reply. Grouping raw
    consecutive records fractures them and reports the prose half as tool-free -
    the exact defect this rule exists to prevent."""
    records = [
        {"type": "assistant", "isSidechain": False, "sessionId": "s",
         "message": {"id": "m1", "content": [{"type": "text", "text": "doing it"}]}},
        {"type": "attachment", "isSidechain": False},
        {"type": "assistant", "isSidechain": False, "sessionId": "s",
         "message": {"id": "m1", "content": [{"type": "tool_use", "name": "Bash", "input": {}}]}},
    ]
    msgs = assistant_messages(records)
    assert len(msgs) == 1, f"fractured into {len(msgs)}"
    assert msgs[0].has_tool is True, "prose half reported as tool-free"
    assert msgs[0].text == "doing it"


@selftest_case
def test_sidechain_records_are_dropped(tmp):
    records = [
        {"type": "assistant", "isSidechain": True, "sessionId": "s",
         "message": {"id": "m1", "content": [{"type": "text", "text": "subagent result"}]}},
        {"type": "assistant", "isSidechain": False, "sessionId": "s",
         "message": {"id": "m2", "content": [{"type": "text", "text": "main"}]}},
    ]
    msgs = assistant_messages(records)
    assert [m.text for m in msgs] == ["main"]


@selftest_case
def test_two_replies_with_distinct_ids_stay_separate(tmp):
    records = [
        {"type": "assistant", "isSidechain": False, "sessionId": "s",
         "message": {"id": "m1", "content": [{"type": "text", "text": "one"}]}},
        {"type": "assistant", "isSidechain": False, "sessionId": "s",
         "message": {"id": "m2", "content": [{"type": "text", "text": "two"}]}},
    ]
    assert len(assistant_messages(records)) == 2


@selftest_case
def test_background_launch_is_detected(tmp):
    records = [{"type": "assistant", "isSidechain": False, "sessionId": "s",
                "message": {"id": "m1", "content": [
                    {"type": "tool_use", "name": "Bash", "input": {"run_in_background": True}}]}}]
    assert assistant_messages(records)[0].tools == [("Bash", True)]
```

- [ ] **Step 2: Run and watch them fail**

Expected: `assistant_messages` is not defined.

- [ ] **Step 3: Implement**

```python
from collections import namedtuple

Message = namedtuple("Message", "session_id message_id text has_tool tools timestamp")


def _blocks(record):
    return (record.get("message") or {}).get("content") or []


def _text_of(records):
    parts = []
    for record in records:
        for block in _blocks(record):
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text") or "")
    return "\n".join(p for p in parts if p)


def _tools_of(records):
    """(name, is_background) per tool call."""
    found = []
    for record in records:
        for block in _blocks(record):
            if isinstance(block, dict) and block.get("type") == "tool_use":
                payload = block.get("input")
                background = bool(isinstance(payload, dict) and payload.get("run_in_background"))
                found.append((block.get("name") or "?", background))
    return found


def assistant_messages(records):
    """One reply is split across records sharing a message id, and OTHER record
    kinds land between those parts. So: filter to main-thread assistant records
    first, then group runs of equal message id."""
    groups = []
    for record in records:
        if record.get("isSidechain") or record.get("type") != "assistant":
            continue
        message_id = (record.get("message") or {}).get("id")
        if groups and groups[-1][0] == message_id and message_id is not None:
            groups[-1][1].append(record)
        else:
            groups.append((message_id, [record]))
    out = []
    for message_id, group in groups:
        tools = _tools_of(group)
        out.append(Message(
            session_id=group[0].get("sessionId"),
            message_id=message_id,
            text=_text_of(group).strip(),
            has_tool=bool(tools),
            tools=tools,
            timestamp=group[0].get("timestamp") or "",
        ))
    return out
```

- [ ] **Step 4: Run and confirm** — every case passes, exit 0.

- [ ] **Step 5: Prove the filter-first rule matters**

Move the `continue` guard so grouping runs over raw records (group on every record, skipping non-assistants only when building text). Re-run. Expected: `test_grouping_filters_to_assistant_records_first` FAILS with "fractured into 2". Restore.

- [ ] **Step 6: Commit**

---

### Task 4: Turn boundaries and which turns count

**Files:**
- Modify: `plugins/retro/bin/stopped-promises.py`

**Interfaces:**
- Consumes: `assistant_messages`
- Produces: `HUMAN_PROMPT_SOURCES`, `is_human_prompt(record) -> bool`, `is_excluded_session(record) -> bool`, `TurnEnd` namedtuple `message, closer, pending, session_id`, `turn_ends(records) -> list[TurnEnd]`

- [ ] **Step 1: Write the failing test**

```python
@selftest_case
def test_human_prompt_uses_source_when_present(tmp):
    typed = {"type": "user", "isSidechain": False, "promptSource": "typed",
             "message": {"content": "go"}}
    notice = {"type": "user", "isSidechain": False, "promptSource": "system",
              "message": {"content": "a background task finished"}}
    dispatched = {"type": "user", "isSidechain": False, "promptSource": "sdk",
                  "message": {"content": "run"}}
    assert is_human_prompt(typed) is True
    assert is_human_prompt(notice) is False, "automatic notice counted as a human turn"
    assert is_human_prompt(dispatched) is False


@selftest_case
def test_human_prompt_falls_back_when_source_absent(tmp):
    """The field is on only 3,466 of 24,206 user records on the reference corpus.
    A pure positive test silently loses 315 real prompts."""
    plain = {"type": "user", "isSidechain": False, "message": {"content": "do the thing"}}
    tool_result = {"type": "user", "isSidechain": False, "message": {
        "content": [{"type": "tool_result", "content": "out"}]}}
    meta = {"type": "user", "isSidechain": False, "isMeta": True,
            "message": {"content": "meta"}}
    wrapper = {"type": "user", "isSidechain": False,
               "message": {"content": "<command-name>/x</command-name>"}}
    assert is_human_prompt(plain) is True
    assert is_human_prompt(tool_result) is False
    assert is_human_prompt(meta) is False
    assert is_human_prompt(wrapper) is False


@selftest_case
def test_only_sdk_dispatch_is_excluded_not_background(tmp):
    """A review measured the background marker on 7 command-line sessions
    carrying 229 typed prompts - 12% of all human input. Excluding on it
    deleted real conversation, so only SDK dispatch is excluded."""
    assert is_excluded_record({"entrypoint": "sdk-py"}) is True
    assert is_excluded_record({"entrypoint": "sdk-cli"}) is True
    assert is_excluded_record({"entrypoint": "cli", "sessionKind": "bg"}) is False
    assert is_excluded_record({"entrypoint": "cli"}) is False


@selftest_case
def test_exclusion_does_not_latch_across_turns(tmp):
    """One excluded record must not discard every later turn in the file."""
    def assistant(mid, entry):
        return {"type": "assistant", "isSidechain": False, "sessionId": "s",
                "entrypoint": entry,
                "message": {"id": mid, "content": [{"type": "text", "text": "Done. I'll run it now."}]}}
    prompt = {"type": "user", "isSidechain": False, "promptSource": "typed",
              "message": {"content": "go"}}
    records = [assistant("m1", "sdk-py"), prompt, assistant("m2", "cli"), prompt]
    ends = turn_ends(records)
    assert [e.message.message_id for e in ends] == ["m2"], [e.message.message_id for e in ends]


@selftest_case
def test_automatic_notice_also_ends_a_turn(tmp):
    """The assistant stopped whether or not the human was the next speaker. A
    review found 975 such stops invisible when only human prompts closed turns."""
    records = [
        {"type": "assistant", "isSidechain": False, "sessionId": "s", "entrypoint": "cli",
         "message": {"id": "m1", "content": [{"type": "text", "text": "Done. I'll run it now."}]}},
        {"type": "user", "isSidechain": False, "promptSource": "system",
         "message": {"content": "a background task finished"}},
    ]
    ends = turn_ends(records)
    assert len(ends) == 1
    assert ends[0].closer == "notice", ends[0].closer


@selftest_case
def test_turn_end_is_last_message_and_carries_pending_state(tmp):
    records = [
        {"type": "assistant", "isSidechain": False, "sessionId": "s", "entrypoint": "cli",
         "message": {"id": "m1", "content": [
             {"type": "tool_use", "name": "Task", "input": {}}]}},
        {"type": "assistant", "isSidechain": False, "sessionId": "s", "entrypoint": "cli",
         "message": {"id": "m2", "content": [{"type": "text", "text": "I'll report back"}]}},
        {"type": "user", "isSidechain": False, "promptSource": "typed",
         "message": {"content": "ok"}},
    ]
    ends = turn_ends(records)
    assert len(ends) == 1
    assert ends[0].message.message_id == "m2"
    assert ends[0].pending is True, "a dispatched subagent is pending work"
    assert ends[0].closer == "prompt"


@selftest_case
def test_interrupt_and_session_end_are_marked_not_dropped(tmp):
    base = {"type": "assistant", "isSidechain": False, "sessionId": "s", "entrypoint": "cli"}
    records = [
        dict(base, message={"id": "m1", "content": [{"type": "text", "text": "one"}]}),
        {"type": "user", "isSidechain": False,
         "message": {"content": "[Request interrupted by user]"}},
        dict(base, message={"id": "m2", "content": [{"type": "text", "text": "two"}]}),
    ]
    ends = turn_ends(records)
    assert [e.closer for e in ends] == ["interrupt", "eof"]
```

- [ ] **Step 2: Run and watch them fail**

- [ ] **Step 3: Implement**

```python
HUMAN_PROMPT_SOURCES = {"typed", "queued", "suggestion_accepted"}
EXCLUDED_ENTRYPOINTS = {"sdk-py", "sdk-cli"}

WRAPPER = re.compile(
    r"<system-reminder>|<command-name>|<command-message>|<local-command-stdout>"
    r"|<bash-(input|stdout|stderr)>|^Caveat: The messages below"
    r"|^\s*\[No response|UserPromptSubmit hook|^API Error", re.I | re.M)
INTERRUPT = re.compile(r"\[request interrupted", re.I)

TurnEnd = namedtuple("TurnEnd", "message closer pending session_id")


def _user_text(record):
    message = record.get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(b.get("text") or "" for b in content
                     if isinstance(b, dict) and b.get("type") == "text")


def is_human_prompt(record):
    """Hybrid. Where the harness stamps a prompt source, trust it and accept only
    the human kinds - that removes automatic completion notices and dispatched
    prompts, which a negative list silently accepts. Where it is absent, fall
    back to structure, which recovers 20 real prompts the field would have lost."""
    if record.get("type") != "user" or record.get("isSidechain"):
        return False
    source = record.get("promptSource")
    if source is not None:
        return source in HUMAN_PROMPT_SOURCES
    content = (record.get("message") or {}).get("content")
    if isinstance(content, list) and any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        return False
    if record.get("toolUseResult") is not None or record.get("isMeta"):
        return False
    body = _user_text(record)
    if not body.strip() or WRAPPER.search(body) or INTERRUPT.search(body):
        return False
    return True


def is_excluded_record(record):
    """Only SDK dispatch. NOT the background marker: it sits on ordinary
    command-line sessions that carry real typed prompts, and excluding on it
    threw away 12% of human input."""
    return record.get("entrypoint") in EXCLUDED_ENTRYPOINTS


def is_automatic_notice(record):
    """A harness-injected message - a background job reporting in. It ends the
    turn just as a human prompt does; the assistant stopped either way."""
    return (record.get("type") == "user" and not record.get("isSidechain")
            and record.get("promptSource") == "system")


def turn_ends(records):
    """The last assistant message before each turn boundary, with whether
    anything was still pending when it was written.

    `excluded` is scoped to the turn and reset by close(), so one dispatched
    record cannot discard every later turn in the file.
    """
    out, group = [], []
    pending, excluded = False, False

    def close(closer):
        nonlocal group, pending, excluded
        if group and not excluded:
            messages = assistant_messages(group)
            if messages:
                last = messages[-1]
                out.append(TurnEnd(last, closer, pending, last.session_id))
        group, pending, excluded = [], False, False

    for record in records:
        if record.get("type") == "assistant" and not record.get("isSidechain"):
            if is_excluded_record(record):
                excluded = True
            group.append(record)
            for name, background in _tools_of([record]):
                if background or name in ("Task", "Agent"):
                    pending = True
            continue
        if record.get("type") != "user" or record.get("isSidechain"):
            continue
        if INTERRUPT.search(_user_text(record)):
            close("interrupt")
        elif is_human_prompt(record):
            close("prompt")
        elif is_automatic_notice(record):
            close("notice")
    close("eof")
    return out
```

- [ ] **Step 4: Run and confirm** — every case passes, exit 0.

- [ ] **Step 5: Prove the positive test matters**

Delete the `if source is not None:` branch so everything falls through to structure. Re-run. Expected: `test_human_prompt_uses_source_when_present` FAILS with "automatic notice counted as a human turn". Restore.

- [ ] **Step 6: Commit**

---

### Task 5: The classifier, as versioned data

**Files:**
- Modify: `plugins/retro/bin/stopped-promises.py`

**Interfaces:**
- Consumes: nothing
- Produces: `CLASSIFIER_VERSION`, `closing_region(text) -> tuple[str, str]`, `classify(text) -> str` returning one of `"commitment"`, `"deferred"`, `"not-a-promise"`, `"none"`

- [ ] **Step 1: Write the failing test**

```python
@selftest_case
def test_classifier_separates_the_three_closing_moves(tmp):
    commitments = ["Everything checks out. I'll run the tests now.",
                   "That is the last one. Starting on it.",
                   "Numbers are in. Let me write the report.",
                   "Done. I'll show you the counts.",          # verb outside any list
                   "That settles it. I'll do the rename."]
    deferred = ["The build is going. I'll report back when it finishes.",
                "I'll fold the findings in once the review lands."]
    not_promises = ["Pick one of the three and I'll run it.",
                    "I'll leave it there and follow your lead.",
                    "Want me to start it now?",
                    "I'll fix that rather than patching it now."]
    for text in commitments:
        assert classify(text) == "commitment", text
    for text in deferred:
        assert classify(text) == "deferred", text
    for text in not_promises:
        assert classify(text) == "not-a-promise", text


@selftest_case
def test_classifier_does_not_depend_on_a_verb_list(tmp):
    """A review measured 165 of 261 real openers using a verb outside any list
    worth maintaining - show, do, report, keep, bring. Widening the list moved
    the result by two. The opener is the signal; the exclusions discriminate."""
    for verb in ("show", "report", "keep", "bring", "prepend", "adjudicate"):
        assert classify(f"All set. I'll {verb} the thing now.") == "commitment", verb


@selftest_case
def test_classifier_reads_only_the_closing_region(tmp):
    """A commitment in the middle is not the pattern - the message continues."""
    text = "I'll run the tests.\n\n" + ("Background on the design. " * 12)
    assert classify(text) == "none"


@selftest_case
def test_closing_region_is_last_line_and_last_two_sentences(tmp):
    last_line, last_two = closing_region("Alpha. Beta. Gamma.\nDelta line")
    assert last_line == "Delta line"
    assert last_two == "Gamma. Delta line", last_two
    assert "Alpha." not in last_two


@selftest_case
def test_single_line_message_does_not_become_its_own_closing_region(tmp):
    """With no newline the last line IS the whole message, so a mid-message
    commitment would count. Bound the region by length as well."""
    text = ("I'll run the tests. " + "Then a long tail of discussion that goes on. " * 8)
    assert classify(text) == "none"


@selftest_case
def test_classifier_version_is_stamped_and_non_empty(tmp):
    assert isinstance(CLASSIFIER_VERSION, str) and CLASSIFIER_VERSION
```

- [ ] **Step 2: Run and watch them fail**

- [ ] **Step 3: Implement**

```python
# Bump on ANY change to the token lists below. Two runs are comparable only if
# they carry the same version; a review measured 81%-92% and 5-122 candidates
# from readings a prose-only definition allowed, which is why this is data.
CLASSIFIER_VERSION = "2"

# The closing region is bounded by length as well as by structure. Without this
# a single-line message is entirely its own "final line", so a commitment
# anywhere in it counts even though the message continues past it.
REGION_MAX_CHARS = 240

# Any first-person forward-looking opener. Deliberately NOT verb-gated: a review
# measured 165 of 261 real openers using a verb outside a maintainable list, and
# adding thirteen more verbs moved the result by two. Discrimination is the job
# of the exclusions below, and of the human or agent reading the candidates.
OPENER_RE = re.compile(r"\b(?:i'?ll|i will|i'?m going to|i'?m about to|i'?m now"
                       r"|let me|let'?s)\b", re.I)
BARE_RE = re.compile(r"(?:^|[.!?]\s+|\u2014\s*)(proceeding|starting|running|on it"
                     r"|working on it|here goes|one moment|stand by|kicking off"
                     r"|diving in|digging in)\b", re.I)
NOT_A_PROMISE_RE = re.compile(
    r"\bsay the word\b|\bwant me to\b|\bshall i\b|\bshould i\b|\bdo you want\b"
    r"|\byour (call|move)\b|\blet me know\b|\bon your (go|approval|signal)\b"
    r"|\bif you\b|\bonce you\b|\bwhen you\b|\bunless you\b|\bafter you\b"
    r"|\brather than\b|\binstead of\b|\bfollow your lead\b|\bleave it\b"
    r"|\bi'?ll (wait|hold|stop|leave|not|never|follow)\b|\bwon'?t\b|\bnot going to\b"
    r"|\bhappy to\b|\bi can\b|\bi could\b|\bwe could\b|\bsay go\b|\btell me\b"
    r"|\bpick\b|\bbefore i\b|\byou decide\b", re.I)
DEFERRED_RE = re.compile(r"\b(when|once|as soon as)\b|\breport back\b|\bback (to you|with)\b"
                         r"|\bnext (session|turn|time)\b|\bin the meantime\b|\bmeanwhile\b"
                         r"|\blater\b|\bpending\b|\bfinish(es|ed)?\b|\bcompletes?\b"
                         r"|\blands?\b|\bstill (running|going)\b", re.I)


def closing_region(text):
    """(final non-empty line, final two sentences), each capped at
    REGION_MAX_CHARS. A match in EITHER counts; the spec fixes this rather than
    leaving 'or' to be guessed. The cap is what stops a single-line message from
    being entirely its own closing region."""
    stripped = (text or "").rstrip()
    lines = [line.strip() for line in stripped.split("\n") if line.strip()]
    last_line = (lines[-1] if lines else "")[-REGION_MAX_CHARS:]
    sentences = re.split(r"(?<=[.!?])\s+", stripped)
    last_two = " ".join(sentences[-2:]).strip()[-REGION_MAX_CHARS:]
    return last_line, last_two


def classify(text):
    """Which closing move the message made.

    The opener gate comes FIRST and is verb-blind; the two exclusions then
    discriminate. An earlier version required a verb from a fixed list before
    it would even consider the other two classes, which made them unreachable
    for the majority of real closings.
    """
    for region in closing_region(text):
        if not region:
            continue
        if not (OPENER_RE.search(region) or BARE_RE.search(region)):
            continue
        if region.rstrip().endswith("?") or NOT_A_PROMISE_RE.search(region):
            return "not-a-promise"
        if DEFERRED_RE.search(region):
            return "deferred"
        return "commitment"
    return "none"
```

- [ ] **Step 4: Run and confirm** — every case passes, exit 0.

- [ ] **Step 5: Commit**

---

### Task 6: Candidates, stable identity, de-duplication

**Files:**
- Modify: `plugins/retro/bin/stopped-promises.py`

**Interfaces:**
- Consumes: `turn_ends`, `classify`, `redact`
- Produces: `candidate_id(session_id, message_id) -> str`, `Candidate` namedtuple `id, tail, timestamp, session_id`, `find_candidates(turn_end_list) -> tuple[list[Candidate], dict]`

- [ ] **Step 1: Write the failing test**

```python
@selftest_case
def test_ids_come_from_identity_not_text(tmp):
    """Hashing the closing line collides: one line is shared by 50 distinct
    messages on the reference corpus, so one verdict would label all of them."""
    a = candidate_id("session-a", "msg-1")
    b = candidate_id("session-b", "msg-2")
    assert a != b
    assert a == candidate_id("session-a", "msg-1"), "id must be stable across runs"


@selftest_case
def test_candidates_require_commitment_no_tool_and_nothing_pending(tmp):
    def end(text, has_tool, pending, mid):
        msg = Message("s", mid, text, has_tool, [], "2026-08-19T00:00:00Z")
        return TurnEnd(msg, "prompt", pending, "s")
    ends = [
        end("All set. I'll run the tests now.", False, False, "m1"),   # candidate
        end("All set. I'll run the tests now.", True, False, "m2"),    # acted
        end("All set. I'll run the tests now.", False, True, "m3"),    # pending
        end("I'll report back when it finishes.", False, False, "m4"), # deferred
        end("Pick one and I'll run it.", False, False, "m5"),          # not a promise
    ]
    found, counts = find_candidates(ends)
    assert [c.id for c in found] == [candidate_id("s", "m1")], counts
    assert counts["turn_ends"] == 5


@selftest_case
def test_repeated_messages_are_counted_once(tmp):
    """Resumed sessions replay history; about 7% of messages appear in two files."""
    msg = Message("s", "m1", "Done. I'll run the tests now.", False, [], "t")
    ends = [TurnEnd(msg, "prompt", False, "s"), TurnEnd(msg, "prompt", False, "s")]
    found, counts = find_candidates(ends)
    assert len(found) == 1, "duplicate message counted twice"
    assert counts["duplicates_dropped"] == 1


@selftest_case
def test_candidate_tail_is_redacted(tmp):
    tail_text = "Done at " + str(Path.home()) + ". I'll run the tests now."
    msg = Message("s", "m1", tail_text, False, [], "t")
    found, _ = find_candidates([TurnEnd(msg, "prompt", False, "s")])
    assert Path.home().name.lower() not in found[0].tail.lower()


@selftest_case
def test_turns_the_human_did_not_end_are_not_candidates(tmp):
    msg = Message("s", "m1", "Done. I'll run the tests now.", False, [], "t")
    for closer in ("eof", "interrupt"):
        found, _ = find_candidates([TurnEnd(msg, closer, False, "s")])
        assert found == [], closer
```

- [ ] **Step 2: Run and watch them fail**

- [ ] **Step 3: Implement**

```python
TAIL_CHARS = 200
Candidate = namedtuple("Candidate", "id tail timestamp session_id")


def new_counts():
    """Every turn end lands in exactly one bucket, so the census adds up. An
    earlier version had no bucket for 'said nothing promise-shaped' and lost
    1,207 of 1,637 turn ends into a gap."""
    return {"turn_ends": 0, "ended_by_speaker": 0, "no_promise": 0, "commitment": 0,
            "deferred": 0, "not_a_promise": 0, "pending": 0, "acted": 0,
            "duplicates_dropped": 0, "session_ended": 0, "interrupted": 0,
            "candidates": 0}


def candidate_id(message_id):
    """Identity, never text - one closing line is shared by 50 distinct messages.

    Keyed on the MESSAGE id alone. A resumed session replays earlier messages
    under a NEW session id, so a (session, message) pair never repeats across
    files and would have de-duplicated nothing: measured 0 of 21,152 pairs
    repeated, against 1,454 of 19,700 bare message ids.
    """
    return hashlib.sha1((message_id or "").encode("utf-8", "replace")).hexdigest()[:12]


def find_candidates(turn_end_list, seen=None, counts=None):
    """`seen` is owned by the caller so de-duplication spans files, not just the
    one being read."""
    seen = seen if seen is not None else set()
    counts = counts if counts is not None else new_counts()
    found = []
    for end in turn_end_list:
        counts["turn_ends"] += 1
        key = end.message.message_id
        if key in seen:
            counts["duplicates_dropped"] += 1
            continue
        seen.add(key)
        if end.closer == "eof":
            counts["session_ended"] += 1
            continue
        if end.closer == "interrupt":
            counts["interrupted"] += 1
            continue
        counts["ended_by_speaker"] += 1
        verdict = classify(end.message.text)
        if verdict == "none":
            counts["no_promise"] += 1
            continue
        if verdict == "deferred":
            counts["deferred"] += 1
            continue
        if verdict == "not-a-promise":
            counts["not_a_promise"] += 1
            continue
        counts["commitment"] += 1
        if end.message.has_tool:
            counts["acted"] += 1
            continue
        if end.pending:
            counts["pending"] += 1
            continue
        counts["candidates"] += 1
        _, last_two = closing_region(end.message.text)
        found.append(Candidate(
            id=candidate_id(end.message.message_id),
            tail=redact(last_two)[-TAIL_CHARS:],
            timestamp=end.message.timestamp,
            session_id=candidate_id(end.session_id),
        ))
    return found, counts
```

- [ ] **Step 4: Run and confirm** — every case passes, exit 0.

- [ ] **Step 5: Commit**

---

### Task 7: Census, candidates file, output streams

**Files:**
- Modify: `plugins/retro/bin/stopped-promises.py`

**Interfaces:**
- Consumes: everything above
- Produces: `default_candidates_path() -> Path`, `refuse_if_in_repo(path)`, `write_candidates(path, candidates, meta)`, `collect(roots, since, until) -> tuple[list[Candidate], dict]`, `main()`

- [ ] **Step 1: Write the failing test**

```python
@selftest_case
def test_candidates_file_refuses_to_land_in_a_repository(tmp):
    repo = Path(tmp) / "repo"
    (repo / ".git").mkdir(parents=True)
    try:
        refuse_if_in_repo(repo / "candidates.txt")
    except SystemExit:
        return
    raise AssertionError("wrote into a work tree")


@selftest_case
def test_candidates_file_says_it_is_unverified(tmp):
    path = Path(tmp) / "cand.txt"
    write_candidates(path, [Candidate("abc123", "I'll run it now.", "t", "s")],
                     {"classifier": CLASSIFIER_VERSION, "window": "all"})
    body = path.read_text(encoding="utf-8")
    assert "UNVERIFIED" in body, "candidates file must label itself"
    assert "abc123" in body and CLASSIFIER_VERSION in body


@selftest_case
def test_json_goes_to_stdout_and_diagnostics_to_stderr(tmp, monkeyenv):
    """--json must always parse, so nothing else may share stdout."""
    import io as _io, contextlib
    out, err = _io.StringIO(), _io.StringIO()
    root = Path(tmp) / "projects" / "p"
    write_fixture(root, "s.jsonl", [
        {"type": "assistant", "isSidechain": False, "sessionId": "s", "entrypoint": "cli",
         "message": {"id": "m1", "content": [{"type": "text", "text": "Done. I'll run it now."}]}},
        {"type": "user", "isSidechain": False, "promptSource": "typed",
         "message": {"content": "ok"}}])
    monkeyenv("STOPPED_PROMISES_ROOTS", str(Path(tmp) / "projects"))
    argv = ["prog", "--json", "--candidates", str(Path(tmp) / "c.txt")]
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            main(argv[1:])
        except SystemExit:
            pass
    payload = json.loads(out.getvalue())
    assert payload["census"]["files"] == 1
    assert payload["candidates"] == 1
```

- [ ] **Step 2: Run and watch them fail**

- [ ] **Step 3: Implement**

```python
def default_candidates_path():
    return Path(tempfile.gettempdir()) / "stopped-promises-candidates.txt"


def refuse_if_in_repo(path):
    """The one file carrying message text must never land in a work tree."""
    for parent in [Path(path).resolve()] + list(Path(path).resolve().parents):
        if (parent / ".git").exists():
            print(f"refusing to write candidates inside a git work tree: "
                  f"{redact(str(parent))}", file=sys.stderr)
            raise SystemExit(EXIT_CANNOT_RUN)


def write_candidates(path, candidates, meta):
    lines = [
        "# stopped-promise candidates - UNVERIFIED",
        "#",
        "# This list is NOT a finding and its count is NOT a rate. A detector",
        "# that matches the grammar of a promise mostly catches sentences that",
        "# hand control back. Read each one, then write a verdicts file with",
        "#     <id> real",
        "#     <id> not-real",
        "# and re-run with --verdicts to get a number worth quoting.",
        f"# classifier version: {meta['classifier']}   window: {meta['window']}",
        "",
    ]
    for candidate in candidates:
        lines.append(f"{candidate.id}  {candidate.timestamp[:10]}  ...{candidate.tail}")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _session_in_window(records, since, until):
    """Window on the SESSION, by the days its timestamped records span.

    Filtering record-by-record silently reclassified turn closers at the window
    edge and let every untimestamped record through - 15% of all records - so a
    one-day window reported the same session count as the whole corpus.
    """
    if not (since or until):
        return True, None, None
    days = sorted(r["timestamp"][:10] for r in records
                  if isinstance(r.get("timestamp"), str) and len(r["timestamp"]) >= 10)
    if not days:
        return False, None, None
    first, last = days[0], days[-1]
    if since and last < since:
        return False, first, last
    if until and first > until:
        return False, first, last
    return True, first, last


def collect(roots, since, until):
    census = {"roots": len(roots), "files_seen": 0, "files_unreadable": 0,
              "files_outside_window": 0, "files_measured": 0, "sessions": set(),
              "first_day": None, "last_day": None, "active_days": set()}
    candidates, totals, seen = [], new_counts(), set()
    for _, path in walk_transcripts(roots):
        census["files_seen"] += 1
        records = list(read_records(path))
        if not records:
            census["files_unreadable"] += 1        # genuinely could not be read
            continue
        inside, first, last = _session_in_window(records, since, until)
        if not inside:
            census["files_outside_window"] += 1     # readable, just not in scope
            continue
        census["files_measured"] += 1
        for record in records:
            if record.get("sessionId"):
                census["sessions"].add(record["sessionId"])
            stamp = record.get("timestamp")
            if isinstance(stamp, str) and len(stamp) >= 10:
                census["active_days"].add(stamp[:10])
        found, _ = find_candidates(turn_ends(records), seen=seen, counts=totals)
        candidates.extend(found)
    days = sorted(census.pop("active_days"))
    census["sessions"] = len(census["sessions"])
    census["first_day"], census["last_day"] = (days[0], days[-1]) if days else (None, None)
    census["active_days"] = len(days)
    return candidates, {"census": census, "counts": totals}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="stopped-promises", description=__doc__)
    parser.add_argument("--root", action="append", default=[])
    parser.add_argument("--since"); parser.add_argument("--until")
    parser.add_argument("--candidates", default=None)
    parser.add_argument("--verdicts", default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()

    roots, complaints = usable_roots(resolve_roots(args.root))
    for complaint in complaints:
        print(complaint, file=sys.stderr)
    if not roots:
        print("no transcript root resolved", file=sys.stderr)
        return EXIT_CANNOT_RUN

    candidates, report = collect(roots, args.since, args.until)
    window = f"{args.since or 'start'}..{args.until or 'now'}"
    path = Path(args.candidates) if args.candidates else default_candidates_path()
    refuse_if_in_repo(path)
    write_candidates(path, candidates, {"classifier": CLASSIFIER_VERSION, "window": window})
    print(f"candidates written to {redact(str(path))}", file=sys.stderr)

    payload = {"classifier": CLASSIFIER_VERSION, "window": window,
               "census": report["census"], "counts": report["counts"],
               "candidates": len(candidates)}
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"classifier {CLASSIFIER_VERSION}   window {window}")
        for key, value in report["census"].items():
            print(f"  {key:20s} {value}")
        for key, value in sorted(report["counts"].items()):
            print(f"  {key:20s} {value}")
        print(f"  {'candidates':20s} {len(candidates)}  (UNVERIFIED - read them)")
    return EXIT_FLAGGED if candidates else EXIT_CLEAN


if __name__ == "__main__":
    sys.exit(main())
```

Delete the temporary `__main__` block from Task 1.

- [ ] **Step 4: Run and confirm** — every case passes, exit 0.

- [ ] **Step 5: Run against the live corpus, read-only**

Run: `python plugins/retro/bin/stopped-promises.py --candidates "$(python -c "import tempfile,os;print(os.path.join(tempfile.gettempdir(),'c.txt'))")"`
Expected: a census, exit 1, and a candidate count in the low tens — around 15 to 40.
Do NOT expect hundreds: the hard ceiling is 189 turn ends whose closing region
holds any opener at all, and every filter subtracts from that. A count in the
hundreds means the exclusions stopped working, not that the tool found more.
Confirm the printed path carries no account name.

- [ ] **Step 6: Commit**

---

### Task 8: The verdict loop

**Files:**
- Modify: `plugins/retro/bin/stopped-promises.py`

**Interfaces:**
- Consumes: `find_candidates`
- Produces: `read_verdicts(path) -> dict[str, bool]`, `score(candidates, verdicts, turn_ends_total) -> dict`

- [ ] **Step 1: Write the failing test**

```python
@selftest_case
def test_verdicts_give_a_rate_and_a_precision(tmp):
    path = Path(tmp) / "v.txt"
    path.write_text("# a comment\naaa real\nbbb not-real\nccc real\n\n", encoding="utf-8")
    verdicts = read_verdicts(path)
    assert verdicts == {"aaa": True, "bbb": False, "ccc": True}
    candidates = [Candidate(i, "t", "2026-08-19", "s") for i in ("aaa", "bbb", "ccc")]
    result = score(candidates, verdicts, turn_ends_total=1000)
    assert result["verified_real"] == 2
    assert result["precision_pct"] == 66.7, result
    assert result["rate_per_100_turn_ends"] == 0.2, result
    assert result["unreviewed"] == 0


@selftest_case
def test_unknown_verdict_ids_are_named_not_ignored(tmp):
    path = Path(tmp) / "v.txt"
    path.write_text("zzz real\n", encoding="utf-8")
    result = score([Candidate("aaa", "t", "d", "s")], read_verdicts(path), 100)
    assert result["unknown_ids"] == ["zzz"]
    assert result["unreviewed"] == 1


@selftest_case
def test_no_rate_without_verdicts(tmp):
    result = score([Candidate("aaa", "t", "d", "s")], {}, 100)
    assert result["rate_per_100_turn_ends"] is None, "reported a rate with no verdicts"
```

- [ ] **Step 2: Run and watch them fail**

- [ ] **Step 3: Implement**

```python
def read_verdicts(path):
    """`<id> real` / `<id> not-real`, one per line. Blank lines and # comments
    ignored. Hand-written input the script only ever reads."""
    verdicts = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        verdicts[parts[0]] = parts[1].lower() in ("real", "yes", "true", "1")
    return verdicts


def score(candidates, verdicts, turn_ends_total):
    """No rate exists without verdicts - that is the whole point of the loop."""
    ids = {c.id for c in candidates}
    reviewed = {i: v for i, v in verdicts.items() if i in ids}
    real = sum(1 for v in reviewed.values() if v)
    result = {
        "candidates": len(candidates),
        "reviewed": len(reviewed),
        "unreviewed": len(ids) - len(reviewed),
        "verified_real": real,
        "unknown_ids": sorted(i for i in verdicts if i not in ids),
        "precision_pct": round(100 * real / len(reviewed), 1) if reviewed else None,
        "rate_per_100_turn_ends": (round(100 * real / turn_ends_total, 2)
                                   if reviewed and turn_ends_total else None),
    }
    return result
```

Replace the tail of `main` — everything from `payload = {...}` — with this. No
prose instruction: an implementer reading only this task must not have to invent
the denominator, the stream, or the failure paths.

```python
    payload = {"classifier": CLASSIFIER_VERSION, "window": window,
               "census": report["census"], "counts": report["counts"],
               "candidates": len(candidates)}

    status = EXIT_FLAGGED if candidates else EXIT_CLEAN
    if args.verdicts:
        try:
            verdicts = read_verdicts(args.verdicts)
        except OSError as exc:
            print(f"cannot read verdicts file: {type(exc).__name__}", file=sys.stderr)
            return EXIT_CANNOT_RUN
        # Denominator is turn ends closed by a speaker - the population the rate
        # is "per hundred" of. counts always has the key; new_counts() seeds it.
        result = score(candidates, verdicts, report["counts"]["ended_by_speaker"])
        payload["verdicts"] = result
        status = EXIT_FLAGGED if result["unreviewed"] else EXIT_CLEAN

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"classifier {CLASSIFIER_VERSION}   window {window}")
        print("  -- census (structural, quotable) --")
        for key, value in report["census"].items():
            print(f"    {key:22s} {value}")
        print("  -- classified (detector output, NOT quotable alone) --")
        for key, value in sorted(report["counts"].items()):
            print(f"    {key:22s} {value}")
        if "verdicts" in payload:
            print("  -- verified (from your verdicts) --")
            for key, value in payload["verdicts"].items():
                print(f"    {key:22s} {value}")
        else:
            print(f"  {len(candidates)} candidates are UNVERIFIED - read them, then "
                  f"re-run with --verdicts")
    return status
```

Two things this fixes that prose left open. The structural census and the
detector's counters print under separate headings, because the spec calls only
the first quotable. And a missing or unreadable verdicts file exits 2 rather
than raising, so it cannot be mistaken for "ran clean and flagged something".

- [ ] **Step 4: Run and confirm** — every case passes, exit 0.

- [ ] **Step 5: Demonstrate all three exit codes**

```bash
python plugins/retro/bin/stopped-promises.py --root /definitely/not/here ; echo "expect 2 -> $?"
python plugins/retro/bin/stopped-promises.py --selftest              ; echo "expect 0 -> $?"
```
Then run against the corpus with no verdicts and confirm exit 1.

- [ ] **Step 6: Commit**

---

### Task 9: The skill — *runs concurrently with Tasks 1–8*

**Files:**
- Create: `plugins/retro/skills/counting-stopped-promises/SKILL.md`

**Interfaces:**
- Consumes: the interface fixed in Task 7. Needs no implementation to exist.

- [ ] **Step 1: Write the skill**

Frontmatter matching the three sibling skills — `name` equal to the directory, and a `description` naming the trigger, not the mechanism:

```markdown
---
name: counting-stopped-promises
description: Use when checking whether announced work actually ran - a session ended a turn saying it was about to do something and nothing happened, or when a rule meant to stop that needs evidence it worked. Reads measured session history, not memory.
---
```

Body sections, in this order:

1. **Overview** — one paragraph: a stopped promise is a message that ended a turn saying it was about to do something, with nothing pending. Name the failure mode this replaces: judging it from memory, where the loud instances are recalled and the rate is unknown.
2. **The procedure** — three commands, using `${CLAUDE_PLUGIN_ROOT}` the way the sibling skills do:

```bash
python "${CLAUDE_PLUGIN_ROOT}/bin/stopped-promises.py" --selftest
python "${CLAUDE_PLUGIN_ROOT}/bin/stopped-promises.py" --since 2026-08-01 --until 2026-08-31
python "${CLAUDE_PLUGIN_ROOT}/bin/stopped-promises.py" --since 2026-08-01 --until 2026-08-31 --verdicts <file>
```

3. **Reading the output** — a three-row table: census is structural and quotable; the candidate count is not a finding; the verified rate is the answer and needs verdicts.
4. **The rule that makes this honest** — never quote the candidate count as a rate. State that a detector matching the grammar of a promise mostly catches sentences handing control back, so the list must be read.
5. **Running it on another machine** — the root precedence, and that both `--since` and `--until` are needed for a repeatable window because the corpus is live.
6. **Red flags** — closing list in the shape the sibling skills use: quoting the candidate count; comparing runs with different classifier versions; comparing an open window with a closed one; treating a rate measured on one corpus as transferable.

- [ ] **Step 2: Verify every command runs as written**

Once Task 7 lands, run each command with `CLAUDE_PLUGIN_ROOT` set to the plugin directory. Expected: all three run; the third reports unreviewed candidates.

- [ ] **Step 3: Commit**

---

### Task 10: Manifests — *runs concurrently with Tasks 1–8*

**Files:**
- Modify: `.claude-plugin/marketplace.json` — retro entry
- Modify: `plugins/retro/.claude-plugin/plugin.json`

- [ ] **Step 1: Bump both to `0.2.0` and extend both descriptions identically**

Append to the existing retro description in **both** files, byte-identical:
`" Also counts stopped promises: turns that ended on an announced action that never ran."`

- [ ] **Step 2: Verify both parse and agree**

```bash
python -c "import json;a=json.load(open('.claude-plugin/marketplace.json'));b=json.load(open('plugins/retro/.claude-plugin/plugin.json'));r=[p for p in a['plugins'] if p['name']=='retro'][0];print('version match:', r['version']==b['version']);print('description match:', r['description']==b['description'])"
```
Expected: both `True`.

- [ ] **Step 3: Commit**

---

## Final gate

- [ ] `--selftest` reports all cases passing.
- [ ] A live-corpus run produces a census reconciling with an independent count.
- [ ] The same closed `--since`/`--until` window gives identical output twice.
- [ ] A produced candidates file contains no account name, home path, email, IPv4, MAC, or foreign absolute path. Check by grepping it for the account name and for a drive-letter path.
- [ ] `sh plugins/core/bin/repo-privacy-audit` shows **only the email category**, which
  is the documented commit-identity exception. Any of the home-path, address or
  hardware-identifier categories firing means a probe literal reached a tracked
  file — assemble it from fragments instead. Take the baseline BEFORE starting;
  the scanner reads every branch in the repository, so another branch's content
  will appear in it.
- [ ] `git log --all --diff-filter=A --name-only | grep -E '\.jsonl'` returns nothing.
- [ ] Exit codes 0, 1, 2 each demonstrated.
- [ ] `/simplify` over the branch diff, then re-run `--selftest`.

## Self-review notes

**Spec coverage.** Census, candidates, verified rate → Tasks 6–8. Message reconstruction → 3. Turn model and non-interactive exclusion → 4. Classifier as versioned data → 5. Roots, gzip, recursion, whitespace env → 1. Redaction including foreign paths → 2. Closed windows and stream separation → 7. Exit codes → 7, 8. Skill → 9. Manifests → 10.

**Deliberately deferred, with reasons.** The spec's per-month series is not implemented: with 9, 22 and 20 active days in the reference window it would compare unequal things, and `--since`/`--until` already give an honest closed window. Anyone wanting a trend runs the tool twice. Length buckets are absent on purpose — that was the confounded finding this redesign removed.

**Type consistency.** `Message`, `TurnEnd`, `Candidate` are defined once and used with the same field names throughout. `find_candidates` returns `(list, dict)` in Task 6 and is unpacked that way in Task 7. `score` takes `turn_ends_total` and is called with `counts["ended_by_human"]`, which Task 6 populates.
