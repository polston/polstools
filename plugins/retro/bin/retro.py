#!/usr/bin/env python3
"""retro — derive workflow-friction metrics from Claude Code session history.

Two subcommands:

    extract   walk session transcripts, append one metrics row per session
    pack      build an evidence pack (trends + redacted moments) for a window

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

# Row schema. Also the ledger contract: every counter here is a column, and
# adding one means an extract --rebuild before trends over it mean anything.
COUNTERS = ["turns", "user_prompts", "tool_calls", "tool_errors", "tool_retries",
            "correction_turns", "approval_turns", "interrupts",
            "permission_mode_changes", "queued_prompts", "skill_runs"]

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
    """Yield (tool_name, input_signature) for each tool use in a message."""
    if not isinstance(message, dict):
        return
    content = message.get("content")
    if not isinstance(content, list):
        return
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            yield block.get("name") or "?", signature(block.get("input"))


_DIGITS = re.compile(r"\d+")
_SPACES = re.compile(r"\s+")
_INTERRUPT = re.compile(r"\[request interrupted", re.I)

# A tool input can carry a whole file body. The leading bytes discriminate one
# call from another just as well as the whole thing, and hashing the rest is
# most of this function's cost.
SIGNATURE_MAX_CHARS = 2048


def signature(tool_input):
    """A hash that survives trivial edits, so a retried command with a tweaked
    number or reflowed whitespace still matches its predecessor."""
    if tool_input is None:
        return ""
    raw = json.dumps(tool_input, sort_keys=True, default=str)[:SIGNATURE_MAX_CHARS]
    raw = _DIGITS.sub("#", raw)
    raw = _SPACES.sub(" ", raw).strip().lower()
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:16]


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
            usage = msg.get("usage") or {}
            tokens_in += int(usage.get("input_tokens") or 0)
            tokens_out += int(usage.get("output_tokens") or 0)
            cache_read += int(usage.get("cache_read_input_tokens") or 0)
            body = text_of(msg)
            prior_assistant_chars = len(body)
            for name, sig in tool_calls_of(msg):
                m["tool_calls"] += 1
                key = (name, sig)
                if sig and key in seen_sigs:
                    m["tool_retries"] += 1
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
        if rtype == "user" and is_error_record(rec):
            m["tool_errors"] += 1

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
    for key in COUNTERS:
        row[key] = m[key]
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

    state = {} if args.rebuild else load_state()
    rows = {}
    if not args.rebuild:
        rows = {r["transcript"]: r for r in load_rows(required=False)
                if "transcript" in r}

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

def load_rows(required=True):
    """Read the ledger. `required` is False for extract, which is allowed to
    start from an empty ledger and build one."""
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
    that mean a human had to intervene, over ones that just mean a long session."""
    return (int(row.get("correction_turns") or 0) * 4
            + int(row.get("interrupts") or 0) * 4
            + int(row.get("permission_mode_changes") or 0) * 3
            + int(row.get("tool_retries") or 0) * 2
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
                     f"tool retries {row.get('tool_retries')}, "
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

    args = parser.parse_args()
    sys.exit(args.func(args) or EXIT_CLEAN)


if __name__ == "__main__":
    main()
