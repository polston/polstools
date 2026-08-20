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

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
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


# USD per token, keyed by the exact message.model string in transcripts.
# Read from the model pricing table on the page below. Re-check the date
# before trusting a dollar figure: prices change and this table does not.
PRICES_VERIFIED_ON = "2026-08-19"
PRICES_SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing"
PRICES = {
    # model id:                    (write_5m,  write_1h,  read)
    "claude-fable-5":              (12.50e-6, 20.00e-6, 1.00e-6),
    "claude-opus-5":               (6.25e-6, 10.00e-6, 0.50e-6),
    "claude-opus-4-8":             (6.25e-6, 10.00e-6, 0.50e-6),
    "claude-opus-4-7":             (6.25e-6, 10.00e-6, 0.50e-6),
    "claude-sonnet-5":             (2.50e-6, 4.00e-6, 0.20e-6),
    "claude-sonnet-4-6":           (3.75e-6, 6.00e-6, 0.30e-6),
    "claude-sonnet-4-5-20250929":  (3.75e-6, 6.00e-6, 0.30e-6),
    "claude-haiku-4-5-20251001":   (1.25e-6, 2.00e-6, 0.10e-6),
}


def evaluate(chained):
    """Price every request under the policy that ran and the counterfactual.

    Observed policy, per request:
        w1 * price_1h + w5 * price_5m + read * price_read

    Counterfactual, forcing the five-minute TTL: every write becomes a
    five-minute write, and whether the request still hits depends on the gap.
    Within five minutes it hits and the read stands. Past five minutes the
    prefix is gone, so the tokens it would have read are rewritten instead --
    the rewrite subsumes both the read and the increment, which is why the
    miss branch has no read term.

    Session openers take the unchanged branch. A request whose model has no
    price is counted into `unpriced` and contributes no cost, but stays in
    the chain so it cannot invent a longer gap for its successor.
    """
    result = {
        "observed": 0.0,
        "counterfactual": 0.0,
        "openers": 0,
        "decisive_read": 0,
        "neutral_read": 0,
        "bands": Counter(),
        "unpriced": Counter(),
    }
    for chain in chained.values():
        for record, gap in gap_seconds(chain):
            price = PRICES.get(record["model"])
            if price is None:
                result["unpriced"][record["model"]] += 1
                continue
            write_5m, write_1h, read_price = price
            read, w1, w5 = record["read"], record["w1"], record["w5"]
            result["observed"] += w1 * write_1h + w5 * write_5m + read * read_price
            hit_cost = (w1 + w5) * write_5m + read * read_price
            if gap is None:
                result["openers"] += 1
                result["counterfactual"] += hit_cost
            elif gap <= FIVE_MINUTES:
                result["counterfactual"] += hit_cost
                result["neutral_read"] += read
                result["bands"]["0-5m"] += 1
            else:
                result["counterfactual"] += (read + w1 + w5) * write_5m
                if gap <= ONE_HOUR:
                    result["bands"]["5-60m"] += 1
                    result["decisive_read"] += read
                else:
                    result["bands"][">60m"] += 1
    result["delta"] = result["counterfactual"] - result["observed"]
    result["ratio"] = (result["counterfactual"] / result["observed"]
                       if result["observed"] else 0.0)
    return result


def project_label(path, projects_dir):
    """A stable, non-reversible label for a project directory.

    Never the directory name. On a real machine those names are mangled
    absolute paths that embed the account name, other projects' names, and
    sometimes a session id -- all of which are forbidden in this repository
    and in anything this script prints. retro.redact() is not enough on its
    own, because it rewrites the home path and username but passes other
    path segments through verbatim.
    """
    try:
        raw = path.relative_to(projects_dir).parts[0]
    except (ValueError, IndexError):
        raw = "unknown"
    return "project-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]


def within_window(requests, days, now=None):
    """Keep requests whose start falls inside the last `days` days (UTC)."""
    if days is None:
        return requests
    if days <= 0:
        return {}
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    return {rid: record for rid, record in requests.items()
            if record["start"] >= cutoff}


def _money(value):
    return "$%s" % format(round(value, 2), ",.2f")


def _early_result(stream, days, as_json, reason, message, exit_code,
                   extra=None):
    """Emit one of the early, no-verdict outcomes in either output shape.

    A caller passing --json must always get valid JSON back, including on
    the "ordinary empty result" and "cannot run" paths -- an exit code
    without parseable output on the machine-readable path is the same
    silent-wrong-result failure this script exists to prevent. `reason` is
    a stable machine-readable code; `message` is the plain-text sentence(s)
    unchanged from before this existed. `keep_current_ttl` is always present
    in the JSON payload, set to null, because no verdict was computed --
    that is the same key a full report carries, just without a value to put
    in it.
    """
    if as_json:
        payload = {
            "window_days": days,
            "reason": reason,
            "keep_current_ttl": None,
        }
        if extra:
            payload.update(extra)
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
    else:
        stream.write(message)
    return exit_code


def report(projects_dir, days, project, as_json, stream):
    """Measure the corpus and print the verdict. Returns an exit code."""
    if not projects_dir.is_dir():
        return _early_result(
            stream, days, as_json, "no_session_directory",
            "cannot run: no session directory at %s\n" % projects_dir.name,
            EXIT_CANNOT_RUN)

    requests, skipped = collect(projects_dir)
    if not requests and not skipped:
        return _early_result(
            stream, days, as_json, "no_readable_transcripts",
            "cannot run: no readable transcripts\n", EXIT_CANNOT_RUN)

    requests = within_window(requests, days)
    if project:
        requests = {rid: record for rid, record in requests.items()
                    if project in str(record["source"])}

    main_chains = chains(requests, main_only=True)
    sub_chains = chains(requests, main_only=False)
    result = evaluate(main_chains)

    main_records = [r for c in main_chains.values() for r in c]
    sub_records = [r for c in sub_chains.values() for r in c]
    if not main_records:
        # A window or project filter that matches nothing is an ordinary
        # result, not a failure. Exit code 2 is for absent input only.
        return _early_result(
            stream, days, as_json, "no_main_thread_requests",
            "no main-thread requests in this window; nothing to decide\n",
            EXIT_CLEAN)
    main_read = sum(r["read"] for r in main_records)
    sub_read = sum(r["read"] for r in sub_records)
    w1_total = sum(r["w1"] for r in main_records)
    w5_total = sum(r["w5"] for r in main_records)

    # A model counts as pinned to five minutes only if it never received a
    # one-hour write anywhere in the window. Testing one request at a time
    # would flag any model that merely happened to write nothing that turn.
    wrote_1h, wrote_5m = set(), set()
    for record in main_records:
        if record["w1"]:
            wrote_1h.add(record["model"])
        if record["w5"]:
            wrote_5m.add(record["model"])
    pinned = wrote_5m - wrote_1h
    pinned_read = sum(r["read"] for r in main_records if r["model"] in pinned)
    governed = main_read - pinned_read
    all_read = main_read + sub_read
    governed_share = (100.0 * governed / all_read) if all_read else 0.0

    ttl_in_force = "one hour" if w1_total >= w5_total else "five minutes"
    if result["observed"] <= 0.0:
        # Guarding an empty record list is not enough: observed also reaches
        # zero when every main-thread model is missing from the price table,
        # and the ratio then reads 0.0, which renders as a confident "switch
        # the TTL" produced from no priced data at all.
        message = ("no priced main-thread requests in this window; "
                   "nothing to decide\n")
        extra = None
        if result["unpriced"]:
            message += ("every main-thread request used a model with no "
                        "price row: %s\n"
                        % ", ".join(sorted(result["unpriced"])))
            extra = {"unpriced_requests": dict(result["unpriced"])}
        return _early_result(
            stream, days, as_json, "no_priced_main_thread_requests",
            message, EXIT_CLEAN, extra)
    keep_current = result["ratio"] >= 1.0
    verdict_code = EXIT_CLEAN if keep_current else EXIT_FLAGGED

    # Unpriced models are counted across BOTH splits, with token volume. The
    # cost model runs on main chains only, so a subagent-only unknown model
    # would otherwise never surface -- and those models feed the subagent
    # table the skill calls the validation.
    unpriced_all = Counter()
    unpriced_tokens = Counter()
    for record in main_records + sub_records:
        if record["model"] not in PRICES:
            unpriced_all[record["model"]] += 1
            unpriced_tokens[record["model"]] += record["tokens"]

    by_project = Counter()
    for record in main_records:
        by_project[project_label(record["source"], projects_dir)] += 1

    # Sensitivity 1: group gaps by project directory instead of by transcript.
    dir_chains = {}
    for record in main_records:
        try:
            key = record["source"].relative_to(projects_dir).parts[0]
        except (ValueError, IndexError):
            key = "unknown"
        dir_chains.setdefault(key, []).append(record)
    for rows in dir_chains.values():
        rows.sort(key=lambda item: item["start"])
    dir_result = evaluate(dir_chains)

    # Sensitivity 2: force every session opener to miss.
    openers_forced = 0.0
    for chain in main_chains.values():
        if not chain:
            continue
        first = chain[0]
        price = PRICES.get(first["model"])
        if price is None:
            continue
        write_5m, _, read_price = price
        hit = (first["w1"] + first["w5"]) * write_5m + first["read"] * read_price
        miss = (first["read"] + first["w1"] + first["w5"]) * write_5m
        openers_forced += miss - hit

    if as_json:
        json.dump({
            "window_days": days,
            "prices_verified_on": PRICES_VERIFIED_ON,
            "prices_source": PRICES_SOURCE,
            "ttl_in_force": ttl_in_force,
            "main_requests": len(main_records),
            "subagent_requests": len(sub_records),
            "main_read_tokens": main_read,
            "subagent_read_tokens": sub_read,
            "write_tokens_1h": w1_total,
            "write_tokens_5m": w5_total,
            "governed_share_of_read_tokens": round(governed_share, 1),
            "observed_cost": round(result["observed"], 2),
            "counterfactual_cost": round(result["counterfactual"], 2),
            "delta": round(result["delta"], 2),
            "ratio": round(result["ratio"], 3),
            "decisive_band_requests": result["bands"]["5-60m"],
            "decisive_read_tokens": result["decisive_read"],
            "neutral_read_tokens": result["neutral_read"],
            "session_openers": result["openers"],
            "unpriced_requests": dict(unpriced_all),
            "unpriced_tokens": dict(unpriced_tokens),
            "pinned_to_5m_models": sorted(pinned),
            "requests_by_project": dict(by_project),
            "snapshot_earliest": min(r["start"] for r in main_records).isoformat(),
            "snapshot_latest": max(r["start"] for r in main_records).isoformat(),
            "sensitivity_ratio_grouped_by_directory": round(dir_result["ratio"], 3),
            "sensitivity_openers_forced_to_miss": round(openers_forced, 2),
            "skipped": dict(skipped),
            "keep_current_ttl": keep_current,
        }, stream, indent=2, sort_keys=True)
        stream.write("\n")
        return verdict_code

    stream.write("prompt-cache TTL economics\n")
    stream.write("window: %s   prices verified %s\n\n"
                 % ("all history" if days is None else "last %d days" % days,
                    PRICES_VERIFIED_ON))

    stream.write("corpus\n")
    stream.write("  main-thread requests   %12d\n" % len(main_records))
    stream.write("  subagent requests      %12d   (pinned to 5m, unaffected)\n"
                 % len(sub_records))
    stream.write("  main read tokens       %12d\n" % main_read)
    stream.write("  1h write tokens        %12d\n" % w1_total)
    stream.write("  5m write tokens        %12d\n" % w5_total)
    stream.write("  TTL in force           %12s\n" % ttl_in_force)
    stream.write("  snapshot               %s .. %s\n"
                 % (min(r["start"] for r in main_records).date(),
                    max(r["start"] for r in main_records).date()))
    if pinned:
        # Names, not a count: the spec's risk mitigation is that the pinned
        # set is computed rather than hardcoded, and a bare count cannot be
        # read as evidence of that.
        stream.write("  pinned to 5m           %12s   (unaffected by the setting)\n"
                     % ", ".join(sorted(pinned)))
    stream.write("  setting governs        %11.1f%%  of all cache-read tokens\n\n"
                 % governed_share)

    stream.write("validation: subagents run on the 5m TTL, so their gap bands\n")
    stream.write("show the counterfactual directly rather than modelled.\n")
    stream.write("  %-8s %9s %9s %12s %12s\n"
                 % ("band", "requests", "zero read", "mean read", "mean write"))
    sub_bands = band_table([pair for chain in sub_chains.values()
                            for pair in gap_seconds(chain)])
    for _, _, name in BANDS:
        bucket = sub_bands[name]
        if not bucket["n"]:
            continue
        stream.write("  %-8s %9d %8.1f%% %12d %12d\n"
                     % (name, bucket["n"],
                        100.0 * bucket["zero_read"] / bucket["n"],
                        bucket["read"] // bucket["n"],
                        bucket["write"] // bucket["n"]))
    stream.write("\n")

    stream.write("main-thread gap bands\n")
    stream.write("  %-8s %9s %9s %12s %12s\n"
                 % ("band", "requests", "zero read", "mean read", "mean write"))
    main_bands = band_table([pair for chain in main_chains.values()
                             for pair in gap_seconds(chain)])
    for _, _, name in BANDS:
        bucket = main_bands[name]
        if not bucket["n"]:
            continue
        stream.write("  %-8s %9d %8.1f%% %12d %12d\n"
                     % (name, bucket["n"],
                        100.0 * bucket["zero_read"] / bucket["n"],
                        bucket["read"] // bucket["n"],
                        bucket["write"] // bucket["n"]))
    stream.write("\n")

    stream.write("cost, cache-related only (not total spend)\n")
    stream.write("  observed, %-14s %14s\n" % (ttl_in_force, _money(result["observed"])))
    stream.write("  counterfactual, 5m       %14s\n" % _money(result["counterfactual"]))
    stream.write("  difference               %14s   ratio %.2fx\n\n"
                 % (_money(result["delta"]), result["ratio"]))

    stream.write("the decision lives in the 5-60 minute band\n")
    stream.write("  requests there         %12d\n" % result["bands"]["5-60m"])
    stream.write("  their read tokens      %12d\n" % result["decisive_read"])
    stream.write("  reads costing the same %12d   (gaps under 5m)\n"
                 % result["neutral_read"])
    stream.write("  session openers        %12d   (unchanged either way)\n\n"
                 % result["openers"])

    stream.write("requests by project (labels are hashes, never names)\n")
    for label, count in sorted(by_project.items()):
        stream.write("  %-22s %12d\n" % (label, count))
    stream.write("\n")

    stream.write("sensitivities, so the modelling choices are not buried\n")
    stream.write("  gaps grouped by project dir  ratio %.2fx (vs %.2fx by transcript)\n"
                 % (dir_result["ratio"], result["ratio"]))
    stream.write("  openers all forced to miss   %s on top of the delta\n\n"
                 % _money(openers_forced))

    if unpriced_all:
        stream.write("unpriced models (no price row; not defaulted)\n")
        for model, count in sorted(unpriced_all.items()):
            stream.write("  %-30s %6d requests %12d tokens\n"
                         % (model, count, unpriced_tokens[model]))
        stream.write("\n")
    if skipped:
        stream.write("skipped rows: %s\n\n"
                     % ", ".join("%s=%d" % kv for kv in sorted(skipped.items())))

    if keep_current:
        stream.write("VERDICT: keep the %s TTL. Forcing five minutes would cost\n"
                     % ttl_in_force)
        stream.write("         %s more, %.2fx, over this window.\n"
                     % (_money(result["delta"]), result["ratio"]))
    else:
        stream.write("VERDICT: the five-minute TTL would be cheaper here, by %s.\n"
                     % _money(-result["delta"]))
        stream.write("         Set FORCE_PROMPT_CACHING_5M=1 to switch.\n")
    return verdict_code


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="cache_ttl",
        description="Decide the prompt-cache TTL from measured session history.")
    sub = parser.add_subparsers(required=True, dest="command")
    p_report = sub.add_parser("report", help="measure the corpus and decide")
    p_report.add_argument("--days", type=int, default=None,
                          help="restrict to the last N days (UTC); "
                               "default is the whole corpus")
    p_report.add_argument("--project", default=None,
                          help="restrict to transcripts whose path contains "
                               "this substring; only the hashed label is printed")
    p_report.add_argument("--json", action="store_true",
                          help="emit the same figures machine-readably")
    args = parser.parse_args(argv)
    try:
        return report(PROJECTS_DIR, args.days, args.project, args.json,
                      sys.stdout)
    except Exception as error:
        # Exit 1 is reserved for "ran clean and flagged something". A crash
        # that exits 1 is indistinguishable from a verdict to an automated
        # caller, so every unexpected failure lands on 2.
        sys.stderr.write("cannot run: %s: %s\n"
                         % (type(error).__name__, error))
        return EXIT_CANNOT_RUN


if __name__ == "__main__":
    sys.exit(main())
