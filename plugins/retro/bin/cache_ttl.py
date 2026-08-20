#!/usr/bin/env python3
"""cache_ttl -- decide the prompt-cache TTL from measured session history.

Answers one question: should Claude Code's prompt cache use the one-hour TTL
or the five-minute one? It replays the request timeline of every session,
prices it under the policy that actually ran and under the counterfactual,
and prints the difference.

Counts and prices only. No message text leaves this script, and project
identifiers are reduced to a stable hash before anything is printed.

Stdlib only. Every field access is guarded: transcript shape varies by CLI
version, and a KeyError partway through a 1GB corpus loses the whole run.

Exit codes match the sibling scripts in plugins/core/bin:
    0  ran clean, the TTL in force is the right one
    1  ran clean, the TTL should change
    2  could not run (no projects directory, no readable transcripts)
"""

EXIT_CLEAN, EXIT_FLAGGED, EXIT_CANNOT_RUN = 0, 1, 2

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

# retro.py is a sibling in this directory. It is import-safe: every statement
# at module level is an assignment, and its writes live inside functions.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import retro  # noqa: E402

HOME = Path.home()
PROJECTS_DIR = HOME / ".claude" / "projects"

FIVE_MINUTES = 300.0
ONE_HOUR = 3600.0


def is_main_thread(path, projects_dir):
    """Main-thread transcripts sit at <projects>/<project>/<session>.jsonl.

    Subagent transcripts sit deeper, under <session>/subagents/. The split is
    positional; isSidechain confirms it but never contradicts it, because the
    flag is absent from every top-level file.
    """
    try:
        return path.parent.parent == projects_dir
    except Exception:
        return False


def _rows(path, main, skipped):
    """Yield one record per assistant row carrying a usage block."""
    try:
        handle = open(path, encoding="utf-8", errors="replace")
    except OSError:
        skipped["unreadable_file"] += 1
        return
    with handle:
        for line in handle:
            if '"usage"' not in line:
                continue
            try:
                raw = json.loads(line)
            except ValueError:
                skipped["bad_json"] += 1
                continue
            if not isinstance(raw, dict) or raw.get("type") != "assistant":
                continue
            message = raw.get("message")
            if not isinstance(message, dict):
                continue
            usage = message.get("usage")
            if not isinstance(usage, dict):
                continue
            rid = raw.get("requestId")
            if not rid:
                # Some rows carry only message.id. Tallied, because if a future
                # CLI writes some rows of one request with a requestId and some
                # without, the id-less rows become a phantom second request.
                rid = message.get("id")
                if rid:
                    skipped["request_id_fallback"] += 1
            if not rid:
                skipped["no_request_id"] += 1
                continue
            start = retro.parse_ts(raw.get("timestamp"))
            if start is None:
                skipped["no_timestamp"] += 1
                continue
            if start.tzinfo is None:
                # A timestamp without Z or an offset compares as naive and
                # raises against every aware one, aborting the whole run.
                skipped["naive_timestamp"] += 1
                continue
            creation = usage.get("cache_creation")
            if not isinstance(creation, dict):
                creation = {}
            read = usage.get("cache_read_input_tokens") or 0
            w1 = creation.get("ephemeral_1h_input_tokens") or 0
            w5 = creation.get("ephemeral_5m_input_tokens") or 0
            out = usage.get("output_tokens") or 0
            model = message.get("model")
            if not isinstance(model, str):
                # A non-string model makes sorted() on the unpriced bucket
                # raise at the very end of an otherwise complete run.
                model = "<unknown>"
            yield {
                "rid": rid,
                "model": model,
                "read": read,
                "w1": w1,
                "w5": w5,
                "out": out,
                "tokens": read + w1 + w5 + out,
                "start": start,
                "source": path,
                "main": main,
            }


def collect(projects_dir):
    """Walk every transcript and return globally deduplicated requests.

    Deduplication is global rather than per file. Resuming or forking a
    session copies recent rows, request id and usage intact, into the new
    transcript; per-file counting double-counts those requests.

    Where rows of one request disagree, the settled row wins -- the one with
    the largest total token count. A request's start is the earliest
    timestamp across its rows, and its owning transcript is the first one it
    appeared in.
    """
    requests = {}
    skipped = Counter()
    for path in sorted(projects_dir.rglob("*.jsonl")):
        main = is_main_thread(path, projects_dir)
        for record in _rows(path, main, skipped):
            rid = record["rid"]
            previous = requests.get(rid)
            if previous is None:
                requests[rid] = record
                continue
            earliest = min(previous["start"], record["start"])
            if record["tokens"] > previous["tokens"]:
                record["start"] = earliest
                record["source"] = previous["source"]
                record["main"] = previous["main"]
                requests[rid] = record
            else:
                previous["start"] = earliest
    return requests, skipped


BANDS = (
    (0.0, 60.0, "0-1m"),
    (60.0, 300.0, "1-5m"),
    (300.0, 600.0, "5-10m"),
    (600.0, 900.0, "10-15m"),
    (900.0, 3600.0, "15-60m"),
    (3600.0, float("inf"), ">60m"),
)


def chains(requests, main_only=True):
    """Group requests into per-transcript chains ordered by start time.

    Gaps are measured within one transcript. Grouping by project directory
    instead moves the decisive band's zero-read share by an order of
    magnitude, so the choice is explicit: the conversation history dominates
    the cached prefix and is specific to one session.
    """
    grouped = {}
    for record in requests.values():
        if bool(record["main"]) != bool(main_only):
            continue
        grouped.setdefault(record["source"], []).append(record)
    for rows in grouped.values():
        rows.sort(key=lambda item: item["start"])
    return grouped


def gap_seconds(chain):
    """Pair each record with the seconds since the previous request started.

    The first request of a chain has no previous request and yields None.
    Rows are ordered by timestamp rather than file position, because a few
    rows appear out of order within their own transcript and trusting file
    order yields negative gaps.
    """
    paired = []
    for index, record in enumerate(chain):
        if index == 0:
            paired.append((record, None))
            continue
        delta = (record["start"] - chain[index - 1]["start"]).total_seconds()
        paired.append((record, max(0.0, delta)))
    return paired


def band_of(seconds):
    for low, high, name in BANDS:
        if low <= seconds < high:
            return name
    return ">60m"


def band_table(paired):
    """Summarise gap bands. Openers carry no gap and are not banded."""
    table = {name: {"n": 0, "zero_read": 0, "read": 0, "write": 0}
             for _, _, name in BANDS}
    for record, gap in paired:
        if gap is None:
            continue
        bucket = table[band_of(gap)]
        bucket["n"] += 1
        bucket["read"] += record["read"]
        bucket["write"] += record["w1"] + record["w5"]
        if record["read"] == 0:
            bucket["zero_read"] += 1
    return table
