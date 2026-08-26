#!/usr/bin/env python3
"""Render and distill the versioned portable adequacy-review contract."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import sys


SKILL_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PATH = SKILL_ROOT / "contract-v1.json"
HARNESSES = {
    "claude-code": {
        "adapter": "references/claude-code.md",
        "model": "sonnet",
        "parallel": True,
    },
    "codex": {
        "adapter": "references/codex.md",
        "model": "inherit",
        "parallel": True,
    },
}


class ContractError(ValueError):
    pass


def load_contract(path=CONTRACT_PATH):
    try:
        contract = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError("adequacy-review contract is unreadable") from exc
    required = {"contract_version", "defaults", "reviewer_schema", "review", "distillation"}
    if not isinstance(contract, dict) or not required.issubset(contract):
        raise ContractError("adequacy-review contract is incomplete")
    if contract["contract_version"] != 1:
        raise ContractError("unsupported adequacy-review contract version")
    defaults = contract["defaults"]
    distillation = contract["distillation"]
    if not isinstance(defaults.get("reviewers"), int) or defaults["reviewers"] < 1:
        raise ContractError("default reviewer count must be positive")
    if distillation.get("agreement_threshold") != 2:
        raise ContractError("agreement threshold must be 2")
    if distillation.get("stable_cap") != 5:
        raise ContractError("stable finding cap must be 5")
    if distillation.get("severity_order") != ["critical", "important", "suggestion"]:
        raise ContractError("severity order is invalid")
    return contract


def contract_sha256(path=CONTRACT_PATH):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _required_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ContractError(label + " must be non-empty")
    return value.strip()


def _reviewer_prompt(contract, target, spec, repo):
    review = contract["review"]
    lines = [review["preamble"], "", "TARGET: " + target, review["target_instruction"]]
    lines.append(
        review["spec_present"].format(spec=spec)
        if spec
        else review["spec_absent"]
    )
    lines.append(
        review["repo_present"].format(repo=repo)
        if repo
        else review["repo_absent"]
    )
    lines.extend(["", "Two non-negotiable steps:"])
    for index, step in enumerate(review["required_steps"], start=1):
        lines.append("%d. %s - %s" % (index, step["label"], step["instruction"]))
    lines.extend(["", review["report"]])
    return "\n".join(lines)


def build_packet(harness, target, spec="", repo="", reviewers=None):
    if harness not in HARNESSES:
        raise ContractError("unsupported harness: " + str(harness))
    contract = load_contract()
    target = _required_text(target, "target")
    if not isinstance(spec, str) or not isinstance(repo, str):
        raise ContractError("spec and repo must be strings")
    count = contract["defaults"]["reviewers"] if reviewers is None else reviewers
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise ContractError("reviewer count must be a positive integer")
    prompt = _reviewer_prompt(contract, target, spec.strip(), repo.strip())
    requests = []
    for index in range(count):
        requests.append(
            {
                "label": "review:%d" % (index + 1),
                "prompt": prompt,
                "schema": copy.deepcopy(contract["reviewer_schema"]),
            }
        )
    canonical = {
        "contract_version": contract["contract_version"],
        "contract_sha256": contract_sha256(),
        "input": {
            "target": target,
            "spec": spec.strip(),
            "repo": repo.strip(),
            "reviewers": count,
        },
        "review_requests": requests,
    }
    return {"transport": copy.deepcopy(HARNESSES[harness]), "canonical": canonical}


def _validate_review(review, index, contract):
    if not isinstance(review, dict):
        raise ContractError("review %d must be an object" % (index + 1))
    if review.get("verdict") not in ("good", "has-issues"):
        raise ContractError("review %d has an invalid verdict" % (index + 1))
    findings = review.get("findings")
    if not isinstance(findings, list):
        raise ContractError("review %d findings must be an array" % (index + 1))
    severities = set(contract["distillation"]["severity_order"])
    normalized = []
    for finding_index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise ContractError("review finding must be an object")
        issue = _required_text(finding.get("issue"), "finding issue")
        severity = finding.get("severity")
        if severity not in severities:
            raise ContractError("finding severity is invalid")
        where = finding.get("where", "")
        agreement_key = finding.get("agreement_key", "")
        if not isinstance(where, str) or not isinstance(agreement_key, str):
            raise ContractError("finding where and agreement_key must be strings")
        normalized.append(
            {
                "issue": issue,
                "severity": severity,
                "where": where.strip(),
                "agreement_key": agreement_key.strip(),
                "reviewer": index,
                "order": finding_index,
            }
        )
    return {"verdict": review["verdict"], "findings": normalized}


def _fallback_key(finding):
    raw = finding["where"] + " " + finding["issue"]
    return re.sub(r"[^a-z0-9]+", " ", raw.lower()).strip()


def _display_finding(group, total, severity_rank):
    representative = min(
        group,
        key=lambda item: (severity_rank[item["severity"]], item["reviewer"], item["order"]),
    )
    result = {
        "issue": representative["issue"],
        "severity": representative["severity"],
        "where": representative["where"],
        "agreement": "%d/%d" % (len({item["reviewer"] for item in group}), total),
    }
    return result


def _render_findings(title, findings):
    lines = ["## " + title]
    if not findings:
        lines.append("None.")
        return lines
    for index, finding in enumerate(findings, start=1):
        location = " - " + finding["where"] if finding["where"] else ""
        lines.append(
            "%d. [%s] %s (%s)%s"
            % (
                index,
                finding["severity"],
                finding["issue"],
                finding["agreement"],
                location,
            )
        )
    return lines


def distill_reviews(reviews, unchecked=None):
    contract = load_contract()
    if not isinstance(reviews, list) or not reviews:
        raise ContractError("reviews must be a non-empty array")
    checked = [_validate_review(review, index, contract) for index, review in enumerate(reviews)]
    groups = {}
    first_seen = {}
    serial = 0
    for review in checked:
        seen_in_review = set()
        for finding in review["findings"]:
            key = finding["agreement_key"] or _fallback_key(finding)
            if not key:
                raise ContractError("finding agreement key is empty")
            if key in seen_in_review:
                continue
            seen_in_review.add(key)
            groups.setdefault(key, []).append(finding)
            first_seen.setdefault(key, serial)
            serial += 1
    distillation = contract["distillation"]
    severity_rank = {
        severity: index for index, severity in enumerate(distillation["severity_order"])
    }
    ranked = []
    for key, group in groups.items():
        reviewers = len({item["reviewer"] for item in group})
        severity = min(severity_rank[item["severity"]] for item in group)
        ranked.append((severity, -reviewers, first_seen[key], key, group))
    ranked.sort()
    threshold = distillation["agreement_threshold"]
    stable_groups = [item for item in ranked if -item[1] >= threshold]
    contested_groups = [item for item in ranked if -item[1] < threshold]
    stable_cap = distillation["stable_cap"]
    stable = [
        _display_finding(item[4], len(checked), severity_rank)
        for item in stable_groups[:stable_cap]
    ]
    contested = [
        _display_finding(item[4], len(checked), severity_rank)
        for item in contested_groups
    ]
    if unchecked is None:
        unchecked = []
    if not isinstance(unchecked, list) or not all(isinstance(item, str) for item in unchecked):
        raise ContractError("unchecked must be an array of strings")
    unchecked = [item.strip() for item in unchecked if item.strip()]
    important = any(item["severity"] in ("critical", "important") for item in stable)
    sections = []
    if not important:
        sections.append(distillation["clean_line"])
    sections.extend(_render_findings(distillation["stable_heading"], stable))
    sections.append("")
    sections.extend(_render_findings(distillation["contested_heading"], contested))
    sections.extend(["", "## " + distillation["unchecked_heading"]])
    sections.extend(["- " + item for item in unchecked] or ["None disclosed."])
    return {
        "stable": stable,
        "stable_omitted": max(0, len(stable_groups) - stable_cap),
        "contested": contested,
        "unchecked": unchecked,
        "reviewer_verdicts": [review["verdict"] for review in checked],
        "raw_finding_counts": [len(review["findings"]) for review in checked],
        "distilled": "\n".join(sections),
    }


def run_fixture(harness, invocation, reviews, unchecked):
    build_packet(harness, **invocation)
    return distill_reviews(reviews, unchecked)


def _write_json(value):
    json.dump(value, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    packet = subparsers.add_parser("packet")
    packet.add_argument("--harness", choices=sorted(HARNESSES), required=True)
    packet.add_argument("--target", required=True)
    packet.add_argument("--spec", default="")
    packet.add_argument("--repo", default="")
    packet.add_argument("-k", "--reviewers", type=int)
    distill = subparsers.add_parser("distill")
    distill.add_argument("--reviews-file", type=Path, required=True)
    distill.add_argument("--unchecked", action="append", default=[])
    fixture = subparsers.add_parser("fixture")
    fixture.add_argument("--harness", choices=sorted(HARNESSES), required=True)
    fixture.add_argument("--fixture", type=Path, required=True)
    subparsers.add_parser("self-check")
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        if args.command == "packet":
            result = build_packet(
                args.harness,
                args.target,
                spec=args.spec,
                repo=args.repo,
                reviewers=args.reviewers,
            )
        elif args.command == "distill":
            reviews = json.loads(args.reviews_file.read_text(encoding="utf-8"))
            result = distill_reviews(reviews, args.unchecked)
        elif args.command == "fixture":
            value = json.loads(args.fixture.read_text(encoding="utf-8"))
            result = run_fixture(
                args.harness,
                value["invocation"],
                value["reviews"],
                value["unchecked"],
            )
        else:
            contract = load_contract()
            for harness in HARNESSES:
                build_packet(harness, "main...HEAD")
            result = {
                "contract_version": contract["contract_version"],
                "contract_sha256": contract_sha256(),
                "harnesses": sorted(HARNESSES),
            }
    except (ContractError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print("error: " + str(exc), file=sys.stderr)
        return 2
    _write_json(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
