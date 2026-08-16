#!/usr/bin/env python3
"""retro — derive workflow-friction metrics from Claude Code session history.

Two subcommands:

    extract   walk session transcripts, append one metrics row per session
    pack      build an evidence pack (trends + redacted moments) for a window

Counts only. Message text leaves this script in exactly one place — the
`moments` section of a pack — and only after passing through redact().

Stdlib only. Every field access is guarded: transcript shape varies by CLI
version, and a KeyError partway through a 900MB corpus loses the whole run.
"""

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

HOME = Path.home()
CLAUDE_DIR = HOME / ".claude"
PROJECTS_DIR = CLAUDE_DIR / "projects"
HISTORY_FILE = CLAUDE_DIR / "history.jsonl"
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

# Deliberately absent: an "abandoned session" metric. The obvious definition —
# the transcript ends on an assistant turn — matches 0.3% of session files
# literally, and 59% if you ignore the trailing bookkeeping records, which is
# just the shape of a session that ended after a reply. Neither number
# separates abandoned from finished, so no metric is emitted rather than one
# that would be read as meaningful.


# --- Redaction -------------------------------------------------------------

def _redaction_patterns():
    """Built at call time so the home path never becomes a module constant."""
    home = str(HOME)
    user = HOME.name
    pats = [
        (re.compile(re.escape(home), re.I), "~"),
        (re.compile(re.escape(home.replace("\\", "/")), re.I), "~"),
        (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"), "<email>"),
        (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "<ip>"),
        (re.compile(r"\b[A-Za-z0-9_-]{32,}\b"), "<long-token>"),
    ]
    if len(user) > 2:
        pats.insert(0, (re.compile(r"\b" + re.escape(user) + r"\b", re.I), "<user>"))
    return pats


_PATTERNS = None


def redact(text):
    """Strip machine-identifying and credential-shaped values from text.

    Runs before anything is written to a pack. A pack file on disk must already
    be safe to read aloud — redacting at read time would be too late.
    """
    global _PATTERNS
    if _PATTERNS is None:
        _PATTERNS = _redaction_patterns()
    if not text:
        return ""
    for pattern, replacement in _PATTERNS:
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


def signature(tool_input):
    """A hash that survives trivial edits, so a retried command with a tweaked
    number or reflowed whitespace still matches its predecessor."""
    if tool_input is None:
        return ""
    raw = json.dumps(tool_input, sort_keys=True, default=str)
    raw = re.sub(r"\d+", "#", raw)
    raw = re.sub(r"\s+", " ", raw).strip().lower()
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:16]


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


def read_records(path):
    """Yield parsed records, skipping malformed lines. A live session is being
    appended to while we read it; a truncated final line is normal, not an
    error."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
    except OSError:
        return


def measure(path):
    """Reduce one transcript to a metrics row."""
    m = Counter()
    session_id = project = branch = version = None
    first_ts = last_ts = None
    seen_sigs = set()
    skills = set()
    prior_assistant_chars = 0
    tokens_in = tokens_out = cache_read = 0
    prev_mode = None
    prev_skill = None

    for rec in read_records(path):
        if not isinstance(rec, dict):
            continue
        rtype = rec.get("type")
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
            low = body.lower()
            if "[request interrupted" in low:
                m["interrupts"] += 1
            elif rec.get("toolUseResult") is None and body.strip():
                m["user_prompts"] += 1
                if (len(body) <= CORRECTION_MAX_CHARS
                        and prior_assistant_chars >= CORRECTION_MIN_PRIOR_CHARS):
                    m["correction_turns"] += 1
                prior_assistant_chars = 0

        if is_error_record(rec):
            m["tool_errors"] += 1

    if session_id is None and not m:
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


# --- extract ---------------------------------------------------------------

def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def cmd_extract(args):
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    if not PROJECTS_DIR.is_dir():
        sys.exit(f"no session directory at {PROJECTS_DIR}")

    state = {} if args.rebuild else load_state()
    rows = {}
    if not args.rebuild and METRICS_FILE.exists():
        for line in METRICS_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
                rows[row["transcript"]] = row
            except (ValueError, KeyError):
                continue

    transcripts = sorted(PROJECTS_DIR.rglob("*.jsonl"))
    processed = skipped = failed = 0
    for path in transcripts:
        try:
            stat = path.stat()
        except OSError:
            continue
        # A live session grows; re-measure when size or mtime moved.
        fingerprint = f"{stat.st_size}:{int(stat.st_mtime)}"
        if state.get(str(path)) == fingerprint:
            skipped += 1
            continue
        row = measure(path)
        if row is None:
            failed += 1
        else:
            rows[row["transcript"]] = row
            processed += 1
        state[str(path)] = fingerprint

    with open(METRICS_FILE, "w", encoding="utf-8") as fh:
        for row in sorted(rows.values(), key=lambda r: (r.get("date", ""), r["transcript"])):
            fh.write(json.dumps(row) + "\n")
    STATE_FILE.write_text(json.dumps(state), encoding="utf-8")

    print(f"transcripts: {len(transcripts)}  measured: {processed}  "
          f"unchanged: {skipped}  unreadable: {failed}")
    print(f"sessions in ledger: {len(rows)}")
    print(f"ledger: {METRICS_FILE}")


# --- pack ------------------------------------------------------------------

COUNTERS = ["turns", "user_prompts", "tool_calls", "tool_errors", "tool_retries",
            "correction_turns", "interrupts", "permission_mode_changes",
            "queued_prompts", "skill_runs"]


def load_rows():
    if not METRICS_FILE.exists():
        sys.exit(f"no metrics ledger at {METRICS_FILE} - run `extract` first")
    out = []
    for line in METRICS_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def totals(rows):
    agg = Counter()
    for row in rows:
        for key in COUNTERS:
            agg[key] += int(row.get(key) or 0)
        agg["tokens_out"] += int(row.get("tokens_out") or 0)
        # Subagent transcripts are spend, not sessions — counting them as
        # sessions would deflate every per-session rate.
        if row.get("is_subagent"):
            agg["subagent_transcripts"] += 1
        else:
            agg["sessions"] += 1
    return agg


def friction_score(row):
    """Rank sessions for which ones are worth quoting. Weighted toward signals
    that mean a human had to intervene, over ones that just mean a long session."""
    return (int(row.get("correction_turns") or 0) * 4
            + int(row.get("interrupts") or 0) * 4
            + int(row.get("permission_mode_changes") or 0) * 3
            + int(row.get("tool_retries") or 0) * 2
            + int(row.get("tool_errors") or 0))


def moments(row, limit=3):
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
            low = body.lower()
            interrupted = "[request interrupted" in low
            corrected = (0 < len(body) <= CORRECTION_MAX_CHARS
                         and len(prior) >= CORRECTION_MIN_PRIOR_CHARS)
            if interrupted or corrected:
                out.append({
                    "at": rec.get("timestamp") or "",
                    "kind": "interrupt" if interrupted else "correction",
                    "said": redact(body.strip())[:400],
                    "after": redact(prior.strip()[-300:]),
                })
            prior = ""
        if len(out) >= limit:
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

    now_t, prev_t = totals(window), totals(prior)
    lines = [f"# Evidence pack — last {args.days} days",
             f"Window: {start} to {now}. Sessions: {now_t['sessions']} "
             f"(prior window: {prev_t['sessions']}).", "",
             "## Trends", "",
             "| signal | this window | prior | delta |", "|---|---|---|---|"]
    for key in ["sessions", "subagent_transcripts"] + COUNTERS:
        a, b = now_t[key], prev_t[key]
        delta = "n/a" if not b else f"{(a - b) / b * 100:+.0f}%"
        lines.append(f"| {key} | {a} | {b} | {delta} |")

    per_session = []
    for key in COUNTERS:
        if now_t["sessions"]:
            per_session.append(f"{key} {now_t[key] / now_t['sessions']:.1f}/session")
    lines += ["", "Per session: " + ", ".join(per_session), "", "## Moments", ""]

    ranked = sorted([r for r in window if not r.get("is_subagent")],
                    key=friction_score, reverse=True)[:args.sessions]
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


# --- skills ----------------------------------------------------------------

def installed_skills():
    """Every skill available to this machine, by directory name."""
    roots = [CLAUDE_DIR / "skills"]
    plugins = CLAUDE_DIR / "plugins" / "cache"
    if plugins.is_dir():
        roots += [p.parent for p in plugins.rglob("skills/*/SKILL.md")]
    found = set()
    for root in roots:
        if root.name == "skills" and root.is_dir():
            found |= {d.name for d in root.iterdir() if (d / "SKILL.md").is_file()}
        elif root.is_dir():
            found.add(root.name)
    return found


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


def main():
    parser = argparse.ArgumentParser(prog="retro", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

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
    args.func(args)


if __name__ == "__main__":
    main()
