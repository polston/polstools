#!/usr/bin/env python3
"""retro — derive workflow-friction metrics from Claude Code session history.

Four subcommands:

    extract    walk session transcripts, append one metrics row per session
    pack       build an evidence pack (trends + redacted moments) for a window
    skills     which installed skills actually fire
    subagents  mechanical failures in subagent transcripts, over a window

Counts only. Message text leaves this script in exactly one place — the
`moments` section of a pack — and only after passing through redact().

Stdlib only. Every field access is guarded: transcript shape varies by CLI
version, and a KeyError partway through a 900MB corpus loses the whole run.

Exit codes match the sibling scripts in plugins/core/bin:
    0  ran clean, nothing flagged
    1  ran clean, something was flagged (a transcript that would not read,
       friction in the window, dormant skills)
    2  could not run (no session directory, no ledger)
"""

EXIT_CLEAN, EXIT_FLAGGED, EXIT_CANNOT_RUN = 0, 1, 2

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

HOME = Path.home()
CLAUDE_DIR = HOME / ".claude"
PROJECTS_DIR = CLAUDE_DIR / "projects"
WORK_DIR = Path(os.environ.get("RETRO_HOME", HOME / ".retro"))
METRICS_FILE = WORK_DIR / "metrics.jsonl"
STATE_FILE = WORK_DIR / "state.json"

# --- Tuning constants ------------------------------------------------------
# These define what counts as friction. They are the knobs worth arguing about;
# everything else in this file is bookkeeping.

# A user prompt shorter than this, arriving right after a long assistant turn,
# reads as a correction ("no", "stop", "I said X") rather than a new request.
CORRECTION_MAX_CHARS = 120
# ...and the assistant turn it follows has to have been substantial, or every
# short back-and-forth in a fast exchange scores as a correction.
CORRECTION_MIN_PRIOR_CHARS = 400

# A short reply that only agrees is the process working, not friction. The whole
# reply has to be one of these, ignoring case and trailing punctuation: "yes" is
# an approval, "yes, but drop the cache" is a correction.
#
# A seed list of unambiguous whole-reply affirmatives, deliberately short. The
# `label` subcommand exists to settle this list from marked turns rather than
# from a guess - add a phrase when the marks show it is being missed.
APPROVAL_PHRASES = (
    "yes", "yep", "yeah", "yup", "ok", "okay", "sure", "correct", "agreed",
    "go ahead", "go for it", "do it", "sounds good", "looks good", "lgtm",
    "perfect", "exactly", "approved", "please do", "ship it",
)
_APPROVAL_TAIL = re.compile(r"[\s.!,]+$")
_APPROVAL = re.compile(
    r"^(?:%s)$" % "|".join(re.escape(p) for p in APPROVAL_PHRASES), re.I)

# Row schema. Every counter here is a column. Bump SCHEMA_VERSION whenever this
# list changes OR a counter's definition changes, because a ledger holding two
# definitions at once is worse than no ledger: it reports a number belonging to
# neither, and nothing in the output says so. `extract` rebuilds on a mismatch
# rather than trusting prose to prevent it.
SCHEMA_VERSION = 3
COUNTERS = ["turns", "user_prompts", "tool_calls", "tool_errors", "repeat_calls",
            "correction_turns", "approval_turns", "interrupts",
            "permission_mode_changes", "queued_prompts", "skill_runs"]

# How a run answered, in precedence order. One value per row, in the `ending`
# column. The first two are a result delivered; only the last two are the agent
# failing to answer, and `interrupted` is the caller's doing, not the agent's.
ENDINGS = ("structured", "text", "interrupted", "unanswered", "silent")

# The subagent lens: one entry per column, mapping the column to the population
# each signal could have occurred in. A share divides by THIS,
# never by every row. A workspace-guard refusal cannot arise in a run that had
# no isolated workspace, and a schema rejection cannot arise in a run that never
# made a structured-result call. Measured on the corpus: the workspace signals
# were possible in 465 of 1,492 subagent rows, so dividing them by 1,492 states
# them at a third of their real rate. Dividing every signal by every row is the
# same defect this lens was rebuilt to remove, with the sign flipped.
#
# ISOLATED_WORKSPACE means the run worked inside an isolated workspace. Any
# other value is the set of tools that emit the refusal; an empty set means the
# run only had to call some tool.
ISOLATED_WORKSPACE = "isolated-workspace"
SIGNAL_POPULATION = {
    "schema_rejected": frozenset(("StructuredOutput",)),
    "unread_before_write": frozenset(("Write", "Edit")),
    "missing_path_target": frozenset(("Read", "Grep")),
    "search_pattern_rejected": frozenset(("Grep",)),
    "invalid_tool_input": frozenset(),
    "workspace_target_outside": ISOLATED_WORKSPACE,
    "workspace_shape_unverifiable": ISOLATED_WORKSPACE,
}

# Same ledger contract as COUNTERS - each name is a column, and adding one means
# an extract --rebuild - but a separate list, so the pack's trend table and
# per-session line, which iterate COUNTERS, are untouched. Derived from the map
# above rather than written out again, so a column cannot exist with no
# population to divide it by.
#
# Every one was re-earned by counting the corpus, not carried over from an
# earlier attempt whose categories a recount disproved. The measurement and the
# checks each category had to pass are in
# docs/plans/2026-08-20-plan-subagent-lens-rebuild.md.
SUBAGENT_COUNTERS = list(SIGNAL_POPULATION)

# No "abandoned session" counter, deliberately. See the metric-definitions
# section of docs/plans/2026-08-12-retro-design.md for the measurement that
# ruled it out.


# --- Redaction -------------------------------------------------------------

@lru_cache(maxsize=1)
def _redaction_patterns():
    """Compile once. Categories mirror plugins/core/bin/repo-privacy-audit --
    keep the two lists in step when either gains a category.
    """
    home = str(HOME)
    user = HOME.name
    pats = [
        (re.compile(re.escape(home), re.I), "~"),
        (re.compile(re.escape(home.replace("\\", "/")), re.I), "~"),
        (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"), "<email>"),
        (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "<ip>"),
        (re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b"), "<mac>"),
        (re.compile(r"\b[A-Za-z0-9_-]{32,}\b"), "<long-token>"),
    ]
    if len(user) > 2:
        pats.insert(0, (re.compile(r"\b" + re.escape(user) + r"\b", re.I), "<user>"))
    return pats


def redact(text):
    """Strip machine-identifying and credential-shaped values from text.

    Runs before anything is written to a pack. A pack file on disk must already
    be safe to read aloud — redacting at read time would be too late.
    """
    if not text:
        return ""
    for pattern, replacement in _redaction_patterns():
        text = pattern.sub(replacement, text)
    return text


# --- Transcript parsing ----------------------------------------------------

def text_of(message):
    """Flatten a message's content to plain text. Content is a string on some
    records and a list of typed blocks on others."""
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict):
            if block.get("type") == "text":
                parts.append(block.get("text") or "")
            elif block.get("type") == "tool_result":
                inner = block.get("content")
                if isinstance(inner, str):
                    parts.append(inner)
    return "\n".join(p for p in parts if p)


def tool_calls_of(message):
    """Yield (block_id, tool_name, input_signature) for each tool use.

    The id is the only link between a call and the result it produced: a
    tool_result block carries the id and never the name. Attribution by name is
    what the mechanical-failure columns need, because the same refusal text is
    emitted by more than one tool.
    """
    if not isinstance(message, dict):
        return
    content = message.get("content")
    if not isinstance(content, list):
        return
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            yield (block.get("id") or "", block.get("name") or "?",
                   signature(block.get("input")))


_DIGITS = re.compile(r"\d+")
_SPACES = re.compile(r"\s+")
_INTERRUPT = re.compile(r"\[request interrupted", re.I)

def signature(tool_input):
    """An exact digest of a tool call's input.

    It used to normalise digits and whitespace away and hash only the first 2KB,
    on the theory that a retry is the same command with a tweaked number. Counted
    against the corpus, that theory cost more than it bought: of 1,387 calls it
    flagged as repeats, 1,157 had genuinely different inputs -- 637 were one file
    read at successive offsets, 314 were updates to a task list. Truncation also
    let two long writes to different paths collide, because sorted keys put the
    content ahead of the path. Exact is both honest and, measured, faster.
    """
    if tool_input is None:
        return ""
    raw = json.dumps(tool_input, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:16]


# --- Mechanical failures ---------------------------------------------------
#
# A failure is counted when the refusal text STARTS the result body and the tool
# that produced it is one that emits that refusal. Both halves were measured:
#
#   - Starts-with, because a command's own output can quote a refusal. One
#     failed result in the subagent corpus is a command that printed a workspace
#     refusal it had read out of a file. A substring test counts it as a real
#     refusal; this rule does not. The anchor is also what keeps successful
#     results out: 73 successful results in the corpus contain one of these
#     texts somewhere - agents read and search transcripts - and exactly 0 begin
#     with one.
#   - Tool-attributed, because the same text means a different mistake
#     depending on which tool emitted it: the unread-file refusal comes from
#     Write and from Edit, the missing-path refusal from Read and from Grep.
#
# The is_error gate below is NOT what makes these specific - measured, it
# excludes nothing the anchor has not already excluded. It is here because it is
# the harness's own record that the call failed, and a counter of failures
# should read that field rather than infer failure from text alone.

TOOL_ERROR_PREFIX = "<tool_use_error>"

# The workspace guard's two refusal families share an opening sentence and are
# told apart by what follows. They are DIFFERENT phenomena and never share a
# counter: one names a target outside the workspace, the other is the guard
# declining because it could not statically verify the command's shape. The
# second is not evidence that anything left the workspace.
ISOLATION_HEADS = ("This session is isolated in the worktree",
                   "This agent is isolated in the worktree")
SHARED_CHECKOUT = ("shared checkout", "shared-checkout")

# (column, tools that emit it or None for any, text that must start the body)
FAILURE_MARKERS = (
    ("schema_rejected", ("StructuredOutput",),
     "Output does not match required schema"),
    ("unread_before_write", ("Write", "Edit"), "File has not been read yet."),
    ("missing_path_target", ("Read",), "File does not exist."),
    ("missing_path_target", ("Grep",), "Path does not exist"),
    ("invalid_tool_input", None, "InputValidationError"),
    ("search_pattern_rejected", ("Grep",), "Search failed"),
)

# The tools through which a result is handed back instead of written as prose.
# An agent that called one of these did answer; it just did not answer in text.
RESULT_TOOLS = ("StructuredOutput", "ReportFindings")

# An isolated workspace is a checkout of its own, and lives under a directory
# with this name. It is the gate on the two workspace signals: a run that was
# never in one could not have been refused by the guard, and counting it in
# their denominator would state the rate as a third of what it is.
WORKTREE_SEGMENT = "worktrees"


def in_isolated_workspace(cwd):
    """Was this working directory inside an isolated workspace?

    A path segment test, not a substring one, so a repository that merely has
    the word in its name is not swept in. Measured over the subagent corpus,
    464 of 1,492 rows match and every one of them matches on this exact segment.
    """
    if not cwd:
        return False
    return WORKTREE_SEGMENT in str(cwd).replace("\\", "/").lower().split("/")


def strip_error_wrapper(body):
    """A tool result body with the harness's error wrapper removed."""
    body = body.lstrip()
    if body.startswith(TOOL_ERROR_PREFIX):
        body = body[len(TOOL_ERROR_PREFIX):].lstrip()
    return body


def failure_body(block):
    """The refusal text of a failed tool_result block, or None.

    is_error is compared to True exactly: measured corpus-wide it is only ever
    True, False or absent, so an identity test loses nothing and cannot be
    surprised by a truthy string later. A non-string body is skipped rather
    than serialised - JSON-dumping it would invent text for a marker to match.
    """
    if not isinstance(block, dict) or block.get("type") != "tool_result":
        return None
    if block.get("is_error") is not True:
        return None
    body = block.get("content")
    if not isinstance(body, str):
        return None
    return strip_error_wrapper(body)


def guard_spoke(block):
    """Did the workspace guard address this run, refusing or not?

    One row in the corpus was refused by the guard while its recorded working
    directory was not inside an isolated workspace. Without this, that row would
    contribute to a numerator whose denominator excluded it.
    """
    if not isinstance(block, dict) or block.get("type") != "tool_result":
        return False
    body = block.get("content")
    if not isinstance(body, str):
        return False
    return strip_error_wrapper(body).startswith(ISOLATION_HEADS)


def classify_failure(tool, body):
    """Which mechanical-failure column a failed result belongs in, or "".

    `tool` is the name resolved from the result's tool-use id; an id with no
    matching call yields "" and therefore matches no tool-scoped category.
    """
    for column, tools, marker in FAILURE_MARKERS:
        if body.startswith(marker) and (tools is None or tool in tools):
            return column
    if body.startswith(ISOLATION_HEADS):
        if any(phrase in body for phrase in SHARED_CHECKOUT):
            return "workspace_target_outside"
        if "verif" in body:
            return "workspace_shape_unverifiable"
    return ""


def prose_of(message):
    """A message's text blocks only.

    Deliberately not text_of(), which also flattens tool_result bodies into the
    string. That is right for quoting a turn and wrong for asking whether the
    agent itself said anything: a transcript that merely read a file mentioning
    the interrupt marker would otherwise read as interrupted.
    """
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(block.get("text") or "" for block in content
                     if isinstance(block, dict) and block.get("type") == "text")


def eligible_signals(tools_used, isolated):
    """The signals this run could have produced, as a sorted list.

    Stored on the row so a report can divide each signal by the population it
    could have arisen in. Recomputing it needs the set of tools the run called,
    which the ledger does not carry, so it is derived once here.
    """
    out = []
    for column, need in SIGNAL_POPULATION.items():
        if need == ISOLATED_WORKSPACE:
            ok = isolated
        elif need:
            ok = bool(tools_used & need)
        else:
            ok = bool(tools_used)
        if ok:
            out.append(column)
    return sorted(out)


def is_approval(reply):
    """Is this whole reply nothing but agreement? `reply` is already stripped.

    Shared with the label report's threshold sweep, so the sweep cannot drift
    from the rule the ledger was built with.
    """
    if not APPROVAL_PHRASES:
        return False
    return bool(_APPROVAL.match(_APPROVAL_TAIL.sub("", reply)))


def classify_user_turn(body, prior_assistant_chars):
    """The pack's central definition, in one place: what a user turn means.

    Returns "interrupt", "question", "approval", "correction" or "". Precedence
    is fixed in that order, so a turn that could read as two things is always
    the earlier one. `measure` counts the result and `moments` quotes it, so
    both read the same rule rather than two copies that drift.

    Only a short reply to a substantial assistant turn is classified at all.
    Anything longer is a new request, and returns "" as it always has.
    """
    if _INTERRUPT.search(body):
        return "interrupt"
    reply = body.strip()
    if not (0 < len(reply) <= CORRECTION_MAX_CHARS
            and prior_assistant_chars >= CORRECTION_MIN_PRIOR_CHARS):
        return ""
    if reply.endswith("?"):
        return "question"
    if is_approval(reply):
        return "approval"
    return "correction"


def is_error_record(rec):
    """Did this record carry a failed tool result?

    Measured across the corpus: `is_error` on the tool_result content block is
    the dominant marker (519 in a 373-session sample), a string-valued
    `toolUseResult` beginning with an error word is next (489), and a
    `toolUseResult.error` key appears once. Both of the first two can be present
    for the same failure, so this returns a boolean and the caller counts once.
    """
    result = rec.get("toolUseResult")
    if isinstance(result, dict) and result.get("error"):
        return True
    if isinstance(result, str) and result.lower().startswith("error"):
        return True
    message = rec.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), list):
        for block in message["content"]:
            if isinstance(block, dict) and block.get("type") == "tool_result" \
                    and block.get("is_error"):
                return True
    return False


def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


class TranscriptUnreadable(Exception):
    """The bytes could not be read.

    Distinct from a file that read fine and holds no conversation: one is a
    fault worth retrying, the other is a settled fact about the file. Reporting
    both as "unreadable" is what put 22 workflow journals in that bucket.
    """


def read_records(path):
    """Yield parsed records, skipping malformed lines. A live session is being
    appended to while we read it; a truncated final line is normal, not an
    error.

    Raises TranscriptUnreadable if the file cannot be opened, or if reading
    fails partway through. Returning an empty stream instead is what made a
    read failure indistinguishable from a file with no conversation in it.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                # json.loads tolerates surrounding whitespace, so testing for
                # blankness beats copying every line of a gigabyte corpus.
                if not line or line.isspace():
                    continue
                try:
                    yield json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
    except OSError as exc:
        raise TranscriptUnreadable(path) from exc


# The three ways a walked file can end up. `extract` prints them as three
# separate counts, and they must sum to the number of files walked.
MEASURED, NOT_TRANSCRIPT, UNREADABLE = "measured", "not-transcript", "unreadable"


def measure(path):
    """Reduce one transcript to a metrics row.

    Returns None for a file that read fine and holds no conversation. Raises
    TranscriptUnreadable for one whose bytes would not read. Callers wanting
    the three outcomes as a single value call measure_outcome() instead.
    """
    m = Counter()
    session_id = project = branch = version = None
    first_ts = last_ts = None
    seen_sigs = set()
    skills = set()
    prior_assistant_chars = 0
    conversation = 0
    tokens_in = tokens_out = cache_read = 0
    prev_mode = None
    prev_skill = None
    tool_by_id = {}
    tools_used = set()
    open_tool_ids = set()
    last_assistant = None
    caller_interrupted = False
    answered_structured = False
    isolated = False

    for rec in read_records(path):
        if not isinstance(rec, dict):
            continue
        rtype = rec.get("type")
        # Structural test for "is this a transcript at all". Filenames would
        # need a new rule for every sidecar format the CLI adds; record types
        # do not.
        if rtype in ("user", "assistant"):
            conversation += 1
        ts = parse_ts(rec.get("timestamp"))
        if ts:
            first_ts = first_ts or ts
            last_ts = ts

        session_id = session_id or rec.get("sessionId") or rec.get("session_id")
        project = project or rec.get("cwd")
        # Whether the run had an isolated workspace decides whether the two
        # workspace signals could have happened in it at all, so it gates their
        # denominator rather than being reported on its own.
        isolated = isolated or in_isolated_workspace(rec.get("cwd"))
        branch = branch or rec.get("gitBranch")
        version = version or rec.get("version")

        # attributionSkill is stamped on EVERY assistant record produced while a
        # skill is active, so counting records counts turns, not invocations.
        # A run is one contiguous stretch of the same skill.
        skill = rec.get("attributionSkill")
        if skill:
            skills.add(str(skill))
            if skill != prev_skill:
                m["skill_runs"] += 1
        prev_skill = skill

        if rtype == "permission-mode":
            # These records are a repeated snapshot of the current mode, not a
            # change event: 5,645 records across the corpus hold 68 actual
            # transitions. Count transitions.
            mode = rec.get("mode") or rec.get("permissionMode")
            if prev_mode is not None and mode != prev_mode:
                m["permission_mode_changes"] += 1
            prev_mode = mode
        elif rtype == "queue-operation":
            # enqueue/dequeue/remove/popAll are paired; a single total would
            # count one queued prompt up to three times.
            if (rec.get("subtype") or rec.get("operation")) == "enqueue":
                m["queued_prompts"] += 1
        elif rtype == "assistant":
            m["turns"] += 1
            msg = rec.get("message") or {}
            last_assistant = msg
            usage = msg.get("usage") or {}
            tokens_in += int(usage.get("input_tokens") or 0)
            tokens_out += int(usage.get("output_tokens") or 0)
            cache_read += int(usage.get("cache_read_input_tokens") or 0)
            body = text_of(msg)
            prior_assistant_chars = len(body)
            for block_id, name, sig in tool_calls_of(msg):
                tool_by_id[block_id] = name
                tools_used.add(name)
                open_tool_ids.add(block_id)
                if name in RESULT_TOOLS:
                    answered_structured = True
                m["tool_calls"] += 1
                key = (name, sig)
                if sig and key in seen_sigs:
                    m["repeat_calls"] += 1
                seen_sigs.add(key)
        elif rtype == "user":
            body = text_of(rec.get("message") or {})
            kind = classify_user_turn(body, prior_assistant_chars)
            if kind == "interrupt":
                m["interrupts"] += 1
            elif rec.get("toolUseResult") is None and body.strip():
                m["user_prompts"] += 1
                if kind == "correction":
                    m["correction_turns"] += 1
                elif kind == "approval":
                    m["approval_turns"] += 1
                prior_assistant_chars = 0

        # Only user records carry tool results; checking the rest re-walks
        # message content for nothing.
        if rtype == "user":
            if is_error_record(rec):
                m["tool_errors"] += 1
            user_msg = rec.get("message") or {}
            # An interrupt is the caller stopping the run. Read from the text
            # blocks only - a tool result that happens to quote the marker is
            # not the caller interrupting anything.
            if _INTERRUPT.search(prose_of(user_msg)):
                caller_interrupted = True
            user_content = user_msg.get("content")
            if isinstance(user_content, list):
                for block in user_content:
                    if not (isinstance(block, dict)
                            and block.get("type") == "tool_result"):
                        continue
                    open_tool_ids.discard(block.get("tool_use_id"))
                    if guard_spoke(block):
                        isolated = True
                    # Mechanical failures the harness refused on its own terms.
                    # A non-zero command exit is NOT one of these: the command
                    # ran, and the exit code is information, not an agent
                    # mistake. Neither is a tool use the operator declined, nor
                    # one a permission rule declined - both are decisions about
                    # the environment.
                    body = failure_body(block)
                    if body is None:
                        continue
                    column = classify_failure(
                        tool_by_id.get(block.get("tool_use_id"), ""), body)
                    if column:
                        m[column] += 1

    if not conversation:
        return None

    # Subagent transcripts live under <session>/subagents/ and carry the PARENT
    # session's id. Keying rows by session id would let them overwrite the
    # parent's row — one row per transcript, tagged, is what aggregates right.
    try:
        rel = path.relative_to(PROJECTS_DIR).as_posix()
    except ValueError:
        rel = path.name

    row = {
        "transcript": rel,
        "is_subagent": "subagents/" in rel,
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
    }
    row["schema"] = SCHEMA_VERSION
    for key in COUNTERS:
        row[key] = m[key]

    # How the run answered, in ENDINGS precedence order. A run that handed a
    # result back through a structured-result call answered, whatever prose sits
    # beside it: asking instead whether the LAST record carried prose made the
    # value turn on which record happened to come last, and measured, 43 rows
    # made a structured-result call and still read as text. Only `unanswered`
    # and `silent` are the agent failing to answer.
    if answered_structured:
        ending = "structured"
    elif prose_of(last_assistant).strip():
        ending = "text"
    elif caller_interrupted:
        ending = "interrupted"
    elif open_tool_ids:
        ending = "unanswered"
    else:
        ending = "silent"
    row["ending"] = ending
    for key in SUBAGENT_COUNTERS:
        row[key] = m[key]
    # Which signals could have occurred here at all. The report divides each
    # count by the rows carrying its name, never by every row.
    row["eligible"] = eligible_signals(tools_used, isolated)
    return row


def measure_outcome(path):
    """One file's outcome: (MEASURED, row), (NOT_TRANSCRIPT, None) or
    (UNREADABLE, None). Never raises for an unreadable file - the thread pool
    in cmd_extract abandons its whole result stream on the first exception."""
    try:
        row = measure(path)
    except TranscriptUnreadable:
        return UNREADABLE, None
    return (MEASURED, row) if row is not None else (NOT_TRANSCRIPT, None)


# --- extract ---------------------------------------------------------------

def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def cmd_extract(args):
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    if not PROJECTS_DIR.is_dir():
        print(f"no session directory at {PROJECTS_DIR}", file=sys.stderr)
        sys.exit(EXIT_CANNOT_RUN)

    rebuild = args.rebuild
    rows = {}
    if not rebuild:
        existing = load_rows(required=False, check_schema=False)
        stale = [r for r in existing if r.get("schema") != SCHEMA_VERSION]
        if stale:
            # Rebuilding is the only correct response. Keeping the old rows and
            # measuring the rest would mix two definitions in one file, which is
            # the failure this version exists to make impossible.
            print(f"schema changed since this ledger was written "
                  f"({len(stale)} of {len(existing)} rows) - rebuilding all of it")
            rebuild = True
        else:
            rows = {r["transcript"]: r for r in existing if "transcript" in r}
    state = {} if rebuild else load_state()

    transcripts = sorted(PROJECTS_DIR.rglob("*.jsonl"))
    stale = []
    unchanged = 0
    unreadable = 0
    for path in transcripts:
        try:
            stat = path.stat()
        except OSError:
            # Nothing to compare and nothing to record: this is a read failure
            # like any other, and belongs in a bucket rather than in none.
            unreadable += 1
            continue
        # A live session grows; re-measure when size or mtime moved.
        fingerprint = f"{stat.st_size}:{int(stat.st_mtime)}"
        if state.get(str(path)) == fingerprint:
            unchanged += 1
            continue
        stale.append((path, fingerprint))

    # measure() shares no state, and the work is dominated by reading a
    # gigabyte off disk, so a thread pool converts most of the wall clock into
    # concurrent I/O. Warm the redaction patterns first so the cache is not
    # raced by the workers.
    _redaction_patterns()
    measured = not_transcripts = 0
    with ThreadPoolExecutor(max_workers=min(8, (os.cpu_count() or 4))) as pool:
        for (path, fingerprint), (outcome, row) in zip(
                stale, pool.map(lambda item: measure_outcome(item[0]), stale)):
            if outcome == UNREADABLE:
                # Deliberately not fingerprinted. Recording one would retire
                # the file until it changes, so a live transcript that was
                # briefly locked would be dropped for good.
                unreadable += 1
                continue
            if outcome == MEASURED:
                rows[row["transcript"]] = row
                measured += 1
            else:
                not_transcripts += 1
            state[str(path)] = fingerprint

    with open(METRICS_FILE, "w", encoding="utf-8") as fh:
        for row in sorted(rows.values(), key=lambda r: (r.get("date", ""), r["transcript"])):
            fh.write(json.dumps(row) + "\n")
    STATE_FILE.write_text(json.dumps(state), encoding="utf-8")

    print(f"transcripts: {len(transcripts)}  measured: {measured}  "
          f"unchanged: {unchanged}  not-transcripts: {not_transcripts}  "
          f"unreadable: {unreadable}")
    print(f"sessions in ledger: {len(rows)}")
    print(f"ledger: {METRICS_FILE}")
    return EXIT_FLAGGED if unreadable else EXIT_CLEAN


# --- pack ------------------------------------------------------------------

def load_rows(required=True, check_schema=True):
    """Read the ledger. `required` is False for extract, which is allowed to
    start from an empty ledger and build one.

    A reader refuses a ledger that was not written by this version of the schema.
    That is deliberately blunt: measured on a real ledger, running new code over
    old rows without rebuilding left 1,923 of 1,938 rows on the previous schema
    and still exited 0, so every total was a sum across two definitions with
    nothing in the output admitting it.
    """
    if not METRICS_FILE.exists():
        if not required:
            return []
        print(f"no metrics ledger at {METRICS_FILE} - run `extract` first",
              file=sys.stderr)
        sys.exit(EXIT_CANNOT_RUN)
    out = []
    for line in METRICS_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    if check_schema:
        stale = sum(1 for r in out if r.get("schema") != SCHEMA_VERSION)
        if stale:
            print(f"ledger holds {stale} of {len(out)} rows from an older schema "
                  f"(current is {SCHEMA_VERSION}) - run `extract --rebuild`; "
                  f"totals across two definitions mean nothing", file=sys.stderr)
            sys.exit(EXIT_CANNOT_RUN)
    return out


def totals(rows):
    """Aggregate the rows handed in, and nothing else.

    The caller chooses the population. Deciding it here is what made the
    printed rates wrong: every counter was summed over all transcripts and then
    divided by a session count that excluded subagent transcripts.
    """
    agg = Counter()
    for row in rows:
        for key in COUNTERS:
            agg[key] += int(row.get(key) or 0)
        agg["tokens_out"] += int(row.get("tokens_out") or 0)
        agg["transcripts"] += 1
    return agg


def split_population(rows):
    """Main-session rows, then subagent rows. One row per transcript either
    way — subagent transcripts are spend, not sessions, and counting them as
    sessions deflates every per-session rate."""
    main = [row for row in rows if not row.get("is_subagent")]
    sub = [row for row in rows if row.get("is_subagent")]
    return main, sub


def friction_score(row):
    """Rank sessions for which ones are worth quoting. Weighted toward signals
    that mean a human had to intervene, over ones that just mean a long session.

    `repeat_calls` is deliberately unscored. It was 42% of this score and the
    largest single input, and a recount showed most of what it counted was not a
    retry at all. Measured after removing it: no window's top 8 changes when the
    replacement counters are added back, so the improvement came from taking the
    bad signal OUT, not from finding a better one. The column is still reported;
    it just no longer decides what a human is shown.
    """
    return (int(row.get("correction_turns") or 0) * 4
            + int(row.get("interrupts") or 0) * 4
            + int(row.get("permission_mode_changes") or 0) * 3
            + int(row.get("tool_errors") or 0))


MOMENTS_PER_SESSION = 3


def moments(row):
    """Redacted evidence for one session, or nothing at all if its transcript
    will not read. A pack covers hundreds of sessions and must not die because
    one file went unreadable."""
    try:
        return _moments(row)
    except TranscriptUnreadable:
        return []


def _moments(row):
    """Pull the user turns that scored this session as frictional, with the
    assistant text immediately before each. Redacted."""
    path = PROJECTS_DIR / row.get("transcript", "")
    if not path.is_file():
        return []
    out = []
    prior = ""
    for rec in read_records(path):
        if not isinstance(rec, dict):
            continue
        if rec.get("type") == "assistant":
            prior = text_of(rec.get("message") or {})
        elif rec.get("type") == "user" and rec.get("toolUseResult") is None:
            body = text_of(rec.get("message") or {})
            kind = classify_user_turn(body, len(prior))
            if kind in ("interrupt", "correction"):
                out.append({
                    "at": rec.get("timestamp") or "",
                    "kind": kind,
                    "said": redact(body.strip())[:400],
                    "after": redact(prior.strip()[-300:]),
                })
            prior = ""
        if len(out) >= MOMENTS_PER_SESSION:
            break
    return out


def cmd_pack(args):
    rows = load_rows()
    now = datetime.now(timezone.utc).date()
    start = now - timedelta(days=args.days)
    prior_start = start - timedelta(days=args.days)

    window = [r for r in rows if r.get("date") and start.isoformat() <= r["date"]]
    prior = [r for r in rows if r.get("date")
             and prior_start.isoformat() <= r["date"] < start.isoformat()]

    main, sub = split_population(window)
    prior_main, prior_sub = split_population(prior)
    now_t, prev_t = totals(main), totals(prior_main)
    now_s = totals(sub)

    lines = [f"# Evidence pack — last {args.days} days",
             f"Window: {start} to {now}. Sessions: {len(main)} "
             f"(prior window: {len(prior_main)}).", "",
             "## Trends", "",
             "Main sessions only. Subagent transcripts are spend and are "
             "reported under the table — every rate here divides one "
             "population by itself.", "",
             "| signal | this window | prior | delta |", "|---|---|---|---|"]
    table = [("sessions", len(main), len(prior_main))]
    table += [(key, now_t[key], prev_t[key]) for key in ["tokens_out"] + COUNTERS]
    for key, a, b in table:
        delta = "n/a" if not b else f"{(a - b) / b * 100:+.0f}%"
        lines.append(f"| {key} | {a} | {b} | {delta} |")

    lines.append("")
    if main:
        lines.append("Per session: " + ", ".join(
            f"{key} {now_t[key] / len(main):.1f}" for key in COUNTERS))
    lines.append("")
    lines.append(f"Subagent spend — {len(sub)} transcripts "
                 f"(prior window: {len(prior_sub)}), no per-session rate: "
                 + ", ".join(f"{key} {now_s[key]}"
                             for key in ["tokens_out"] + COUNTERS))
    lines += ["", "## Moments", ""]

    ranked = sorted(main, key=friction_score, reverse=True)[:args.sessions]
    if not ranked:
        lines.append("_No sessions in window._")
    for row in ranked:
        score = friction_score(row)
        if score == 0:
            continue
        lines.append(f"### {row['date']} · {row.get('project') or '?'} · "
                     f"branch `{row.get('git_branch') or '-'}` · score {score}")
        lines.append(f"corrections {row.get('correction_turns')}, "
                     f"interrupts {row.get('interrupts')}, "
                     f"permission-mode changes {row.get('permission_mode_changes')}, "
                     f"repeat calls {row.get('repeat_calls')}, "
                     f"tool errors {row.get('tool_errors')}, "
                     f"queued prompts {row.get('queued_prompts')}")
        for moment in moments(row):
            lines.append("")
            lines.append(f"- **{moment['kind']}** at {moment['at']}")
            lines.append(f"  - assistant, just before: _{moment['after']}_")
            lines.append(f"  - user said: **{moment['said']}**")
        lines.append("")

    WORK_DIR.mkdir(parents=True, exist_ok=True)
    out_path = WORK_DIR / f"pack-{now.isoformat()}-{args.days}d.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(out_path)
    return EXIT_FLAGGED if any(friction_score(r) for r in ranked) else EXIT_CLEAN


# --- skills ----------------------------------------------------------------

def installed_skills():
    """Every skill available to this machine, by directory name. Two homes:
    loose skills under the user's skills directory, and skills vendored inside
    installed plugins. glob on a missing directory yields nothing, so neither
    needs an existence guard."""
    loose = (CLAUDE_DIR / "skills").glob("*/SKILL.md")
    vendored = (CLAUDE_DIR / "plugins" / "cache").rglob("skills/*/SKILL.md")
    return {p.parent.name for p in loose} | {p.parent.name for p in vendored}


def cmd_skills(args):
    rows = load_rows()
    if args.days:
        start = (datetime.now(timezone.utc).date() - timedelta(days=args.days)).isoformat()
        rows = [r for r in rows if (r.get("date") or "") >= start]
    used = Counter()
    for row in rows:
        for name in row.get("skills_used") or []:
            used[name.split(":")[-1]] += 1

    installed = installed_skills()
    window = f"last {args.days} days" if args.days else "all history"
    print(f"# Skill firing - {window}, {len(rows)} transcripts\n")
    print("## Fired")
    for name, count in used.most_common():
        # Names with no SKILL.md on disk are harness built-ins (/simplify,
        # /loop, deep-research) or a skill that has since been renamed.
        mark = "" if name in installed else "   (built-in command, or renamed)"
        print(f"  {count:5d}  {name}{mark}")
    dormant = sorted(installed - set(used))
    print(f"\n## Never fired ({len(dormant)} of {len(installed)} installed)")
    for name in dormant:
        print(f"         {name}")
    return EXIT_FLAGGED if dormant else EXIT_CLEAN


# --- subagents -------------------------------------------------------------

# One entry per column: what it means when it fires, the population it could
# have fired in, and what a person could change to move it. The wording matters
# for the two workspace signals - one is a target outside the workspace, the
# other is a command the guard could not check - because reading the second as
# the first is the mistake this report exists to stop.
#
# The last field is the honest answer to "what would I do about this?". Six of
# the seven are reachable by editing an instruction or a schema. The seventh is
# not, and says so rather than sitting in the table implying otherwise.
SUBAGENT_SIGNALS = [
    ("workspace_shape_unverifiable",
     "command shape the workspace guard could not verify (NOT proof it left)",
     "runs in an isolated workspace",
     "a standing rule asking for plain single commands"),
    ("workspace_target_outside",
     "named a target outside its workspace",
     "runs in an isolated workspace",
     "a dispatch instruction to stay inside the workspace"),
    ("schema_rejected",
     "structured output rejected against its own schema",
     "runs that made a structured-result call",
     "the schema, or the instruction that describes it"),
    ("missing_path_target",
     "read or searched a path that does not exist",
     "runs that read or searched",
     "an instruction to confirm a path before using it"),
    ("unread_before_write",
     "wrote or edited a file it had not read",
     "runs that wrote or edited a file",
     "an instruction to read before writing"),
    ("search_pattern_rejected",
     "search pattern, glob or file type rejected",
     "runs that searched",
     "an instruction about search syntax"),
    ("invalid_tool_input",
     "tool call rejected before it ran, on its input",
     "runs that called any tool",
     ""),
]

# Why the one blank above is blank, kept beside it so the two cannot drift:
# measured over the corpus, 23 of 28 are a tool input that would not parse. That
# is the call itself being malformed, and no instruction and no schema reaches
# it. The column is kept because the count is real and worth watching; it is
# marked unactionable because pretending otherwise sends someone editing
# instructions at a signal instructions cannot move.
UNACTIONABLE_NOTE = ("no instruction or schema edit reaches this one - the "
                     "call itself was malformed")

# The table above and the schema list name the same seven columns. Keeping them
# in step by hand is how a column quietly stops being reported, so a mismatch is
# an error the first time the file is imported rather than a silent gap.
assert {key for key, _, _, _ in SUBAGENT_SIGNALS} == set(SUBAGENT_COUNTERS)

ENDING_MEANING = {
    "structured": "handed a result back through a structured-result call",
    "text": "answered in text",
    "interrupted": "the caller stopped the run",
    "unanswered": "a tool call never got its result",
    "silent": "stopped without answering",
}


def quantile(sorted_values, fraction):
    """Nearest-rank quantile over a pre-sorted list. Empty input has no
    quantile; callers guard."""
    return sorted_values[int(round((len(sorted_values) - 1) * fraction))]


def project_key(row):
    """Which project a row belongs to, as an opaque key.

    The first path component of the transcript's location is the harness's own
    grouping of sessions by project. It is used to divide, never printed: the
    report says how concentrated a signal is and never where it came from.
    """
    return str(row.get("transcript") or "?").split("/")[0]


def concentration(rows, key):
    """The share of a signal's occurrences held by its largest single project.

    A count is a pattern only if more than one workflow produces it. Measured on
    the corpus, two of the seven columns are more than half one project, and a
    reader given the count alone cannot tell. Returned as a percentage, or None
    when the signal did not occur.
    """
    by_project = Counter()
    for row in rows:
        count = int(row.get(key) or 0)
        if count:
            by_project[project_key(row)] += count
    total = sum(by_project.values())
    if not total:
        return None
    return by_project.most_common(1)[0][1] / total * 100


def reporting_session_ids(extra):
    """Session ids whose rows this report must not count.

    The corpus holds the transcripts of whichever session is running the report,
    still being written, and counting them measures the act of measuring: of the
    runs that looked like they never answered, most were live transcripts and
    one belonged to the reporting session itself. Subagent transcripts carry
    their PARENT session's id, so one id drops a session and everything it
    dispatched. The ids are read, matched and discarded - never printed.
    """
    ids = {value for value in (extra or []) if value}
    current = os.environ.get("CLAUDE_CODE_SESSION_ID") or ""
    if current:
        ids.add(current)
    return ids


def cmd_subagents(args):
    """Mechanical failures in subagent transcripts, over a window.

    Reads the ledger and nothing else. It never stats a file, never walks the
    corpus, and never treats a transcript's absence from the ledger as a
    signal - counting files the ledger had not caught up with, dated by their
    modification time, is what produced a 74 where the answer was 8. Windows
    are on the row's `date`, which comes from the first timestamp inside the
    transcript.
    """
    rows = [r for r in load_rows() if r.get("is_subagent")]
    if args.days:
        start = (datetime.now(timezone.utc).date()
                 - timedelta(days=args.days)).isoformat()
        rows = [r for r in rows if (r.get("date") or "") >= start]
    skip = reporting_session_ids(args.exclude_session)
    dropped = sum(1 for r in rows if r.get("session_id") in skip)
    rows = [r for r in rows if r.get("session_id") not in skip]
    window = f"last {args.days} days" if args.days else "all history"
    if not rows:
        print(f"# Subagent lens - {window}\n\nNo subagent transcripts in window.")
        return EXIT_CLEAN

    taken = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"# Subagent lens - {window}, {len(rows)} transcripts\n")
    print(f"Taken {taken}, over {len(rows)} rows of the ledger as last "
          f"extracted. A snapshot, not a settled figure: the corpus is appended "
          f"to while it is read, so a recount minutes later moves the larger "
          f"counts. Compare closed windows, not consecutive runs.")
    if dropped:
        print(f"\n{dropped} rows written by the session running this report "
              f"were left out - transcripts still being written.")

    print("\n## Mechanical failures\n")
    print("Each share divides by the population the signal could have occurred "
          "in, named per row - never by every transcript. `top project` is the "
          "share of that signal's occurrences coming from its single largest "
          "project; a high one means one workflow repeating itself rather than "
          "a general pattern. No project is named.\n")
    print("| signal | occurrences | transcripts | could occur in | share "
          "| top project | actionable |")
    print("|---|---|---|---|---|---|---|")
    flagged = 0
    tallies = []
    for key, _meaning, population, fix in SUBAGENT_SIGNALS:
        occurrences = sum(int(r.get(key) or 0) for r in rows)
        carrying = sum(1 for r in rows if int(r.get(key) or 0))
        eligible = sum(1 for r in rows if key in (r.get("eligible") or []))
        flagged += occurrences
        tallies.append((occurrences, carrying, eligible, key, population, fix))
    for occurrences, carrying, eligible, key, population, fix in sorted(
            tallies, reverse=True):
        share = f"{carrying / eligible * 100:.1f}%" if eligible else "n/a"
        top = concentration(rows, key)
        top_text = "n/a" if top is None else f"{top:.0f}%"
        print(f"| {key} | {occurrences} | {carrying} | {eligible} {population} "
              f"| {share} | {top_text} | {'yes' if fix else 'no'} |")

    print("\nWhat each one means, and what would move it:\n")
    for key, meaning, _population, fix in SUBAGENT_SIGNALS:
        print(f"- {key} - {meaning}. {fix if fix else UNACTIONABLE_NOTE}.")

    print("\nNot counted: a command that ran and returned a non-zero exit, a "
          "tool use the operator declined, and one a permission rule declined. "
          "None of the three is an agent mistake.")

    print("\n## How runs answered\n")
    print("A run answered structurally if it handed a result back through a "
          "structured-result call at any point. Asking instead which record "
          "came last put runs that had done exactly that into `text`.\n")
    print("| ending | transcripts | share |")
    print("|---|---|---|")
    endings = Counter(r.get("ending") or "?" for r in rows)
    for name in ENDINGS:
        count = endings.get(name, 0)
        print(f"| {name} - {ENDING_MEANING[name]} | {count} | "
              f"{count / len(rows) * 100:.1f}% |")
    no_answer = endings.get("unanswered", 0) + endings.get("silent", 0)
    print(f"\nFailed to answer: {no_answer}. Answering through a "
          f"structured-result call is answering, and an interrupted run is the "
          f"caller's doing. Some of what is left is a transcript that had not "
          f"finished being written when the ledger was built.")

    turns = sorted(int(r.get("turns") or 0) for r in rows)
    print(f"\n## Length\n\nturns: median {quantile(turns, 0.5)}, "
          f"p90 {quantile(turns, 0.90)}, p95 {quantile(turns, 0.95)}, "
          f"max {turns[-1]}.")
    for threshold in (100, 150, 200):
        print(f"  {sum(1 for t in turns if t >= threshold)} transcripts at "
              f"{threshold} turns or more")
    print("\nA distribution, not a failure count: no turn number in this "
          "corpus marks a boundary between a long job and a runaway one.")

    return EXIT_FLAGGED if (flagged or no_answer) else EXIT_CLEAN


def main():
    parser = argparse.ArgumentParser(prog="retro", description=__doc__)
    sub = parser.add_subparsers(required=True)

    p_extract = sub.add_parser("extract", help="measure transcripts into the ledger")
    p_extract.add_argument("--rebuild", action="store_true",
                           help="ignore prior state and re-measure everything")
    p_extract.set_defaults(func=cmd_extract)

    p_pack = sub.add_parser("pack", help="build an evidence pack for a window")
    p_pack.add_argument("--days", type=int, default=7)
    p_pack.add_argument("--sessions", type=int, default=8,
                        help="how many top-friction sessions to quote")
    p_pack.set_defaults(func=cmd_pack)

    p_skills = sub.add_parser("skills", help="which installed skills actually fire")
    p_skills.add_argument("--days", type=int, default=0,
                          help="restrict to a window; 0 means all history")
    p_skills.set_defaults(func=cmd_skills)

    p_sub = sub.add_parser("subagents",
                           help="mechanical failures in subagent transcripts")
    p_sub.add_argument("--days", type=int, default=30,
                       help="restrict to a window; 0 means all history")
    p_sub.add_argument("--exclude-session", action="append", default=[],
                       metavar="ID",
                       help="drop a session's rows, and its subagents' rows "
                            "with them. The session running the report is "
                            "dropped already, read from the environment.")
    p_sub.set_defaults(func=cmd_subagents)

    args = parser.parse_args()
    sys.exit(args.func(args) or EXIT_CLEAN)


if __name__ == "__main__":
    main()
