#!/usr/bin/env python3
"""Render and distill the versioned portable adequacy-review contract."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys


SKILL_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PATH = SKILL_ROOT / "contract-v1.json"
ADAPTERS = {
    "claude-code": "references/claude-code.md",
    "codex": "references/codex.md",
}


class ContractError(ValueError):
    pass


def load_contract(path=CONTRACT_PATH):
    try:
        contract = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError("adequacy-review contract is unreadable") from exc
    required = {
        "contract_version",
        "defaults",
        "inputs",
        "reviewer_schema",
        "distiller_schema",
        "review",
        "distillation",
    }
    if not isinstance(contract, dict) or not required.issubset(contract):
        raise ContractError("adequacy-review contract is incomplete")
    if contract["contract_version"] != 1:
        raise ContractError("unsupported adequacy-review contract version")
    defaults = contract["defaults"]
    inputs = contract["inputs"]
    reviewer_schema = contract["reviewer_schema"]
    distiller_schema = contract["distiller_schema"]
    review = contract["review"]
    distillation = contract["distillation"]
    if not all(
        isinstance(value, dict)
        for value in (
            defaults,
            inputs,
            reviewer_schema,
            distiller_schema,
            review,
            distillation,
        )
    ):
        raise ContractError("adequacy-review contract containers are invalid")
    if not isinstance(defaults.get("reviewers"), int) or defaults["reviewers"] < 1:
        raise ContractError("default reviewer count must be positive")
    if not isinstance(inputs, dict) or set(inputs) != {"target", "spec", "repo"}:
        raise ContractError("contract inputs are invalid")
    try:
        reviewer_properties = reviewer_schema["properties"]
        findings_schema = reviewer_properties["findings"]
        finding_schema = findings_schema["items"]
        unchecked_schema = reviewer_properties["unchecked"]
        distiller_properties = distiller_schema["properties"]
        cluster_schema = distiller_properties["clusters"]["items"]
        reference_schema = cluster_schema["properties"]["finding_refs"]["items"]
    except (KeyError, TypeError):
        raise ContractError("reviewer or distiller schema is incomplete") from None
    if reviewer_schema.get("type") != "object" or reviewer_schema.get("required") != [
        "verdict",
        "findings",
        "unchecked",
    ]:
        raise ContractError("reviewer schema root is invalid")
    verdict_schema = reviewer_properties.get("verdict", {})
    if verdict_schema.get("type") != "string" or verdict_schema.get("enum") != [
        "good",
        "has-issues",
    ]:
        raise ContractError("reviewer verdict schema is invalid")
    if (
        findings_schema.get("type") != "array"
        or finding_schema.get("type") != "object"
        or finding_schema.get("required") != ["issue", "severity"]
    ):
        raise ContractError("reviewer finding schema is invalid")
    finding_properties = finding_schema.get("properties", {})
    if not isinstance(finding_properties, dict):
        raise ContractError("reviewer finding schema is invalid")
    if finding_properties.get("issue", {}).get("type") != "string":
        raise ContractError("reviewer issue schema is invalid")
    severity_schema = finding_properties.get("severity", {})
    if severity_schema.get("type") != "string" or severity_schema.get("enum") != [
        "critical",
        "important",
        "suggestion",
    ]:
        raise ContractError("reviewer severity schema is invalid")
    for field in ("where", "agreement_key"):
        if finding_properties.get(field, {}).get("type") != "string":
            raise ContractError("reviewer optional field schema is invalid")
    if unchecked_schema.get("type") != "array" or unchecked_schema.get("items", {}).get(
        "type"
    ) != "string":
        raise ContractError("reviewer unchecked schema is invalid")
    clusters_schema = distiller_properties.get("clusters", {})
    if (
        distiller_schema.get("type") != "object"
        or distiller_schema.get("required") != ["clusters"]
        or clusters_schema.get("type") != "array"
    ):
        raise ContractError("distiller schema root is invalid")
    finding_refs_schema = cluster_schema.get("properties", {}).get("finding_refs", {})
    if (
        cluster_schema.get("type") != "object"
        or cluster_schema.get("required") != ["finding_refs"]
        or finding_refs_schema.get("type") != "array"
    ):
        raise ContractError("distiller cluster schema is invalid")
    if reference_schema.get("type") != "object" or reference_schema.get("required") != [
        "review",
        "finding",
    ]:
        raise ContractError("distiller finding reference schema is invalid")
    for field in ("review", "finding"):
        value = reference_schema.get("properties", {}).get(field, {})
        if value.get("type") != "integer" or value.get("minimum") != 1:
            raise ContractError("distiller finding reference schema is invalid")
    required_review_fields = {
        "preamble",
        "target_instruction",
        "spec_present",
        "spec_absent",
        "repo_present",
        "repo_absent",
        "required_steps",
        "report",
    }
    if not isinstance(review, dict) or not required_review_fields.issubset(review):
        raise ContractError("review prompt contract is incomplete")
    steps = review["required_steps"]
    if not isinstance(steps, list) or [step.get("label") for step in steps] != [
        "GROUND",
        "TRACE",
    ]:
        raise ContractError("review steps are invalid")
    for field in required_review_fields - {"required_steps"}:
        if not isinstance(review[field], str) or not review[field].strip():
            raise ContractError("review prompt contract is invalid")
    if not all(isinstance(step.get("instruction"), str) and step["instruction"].strip() for step in steps):
        raise ContractError("review steps are invalid")
    if distillation.get("agreement_threshold") != 2:
        raise ContractError("agreement threshold must be 2")
    if distillation.get("stable_cap") != 5:
        raise ContractError("stable finding cap must be 5")
    if distillation.get("severity_order") != ["critical", "important", "suggestion"]:
        raise ContractError("severity order is invalid")
    for field in (
        "stable_heading",
        "contested_heading",
        "unchecked_heading",
        "stable_omitted_line",
        "clean_line",
        "semantic_instruction",
    ):
        if not isinstance(distillation.get(field), str) or not distillation[field].strip():
            raise ContractError("distillation text is invalid")
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
    lines.extend(
        [
            "",
            review["report"],
            "",
            "RESPONSE SCHEMA (return JSON only):",
            json.dumps(contract["reviewer_schema"], sort_keys=True),
        ]
    )
    return "\n".join(lines)


def build_packet(harness, target, spec="", repo="", reviewers=None):
    if harness not in ADAPTERS:
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
    return {
        "transport": {"harness": harness, "adapter": ADAPTERS[harness]},
        "canonical": canonical,
    }


def _validate_review(review, index, contract):
    if not isinstance(review, dict):
        raise ContractError("review %d must be an object" % (index + 1))
    if review.get("verdict") not in ("good", "has-issues"):
        raise ContractError("review %d has an invalid verdict" % (index + 1))
    findings = review.get("findings")
    if not isinstance(findings, list):
        raise ContractError("review %d findings must be an array" % (index + 1))
    unchecked = review.get("unchecked")
    if not isinstance(unchecked, list) or not all(isinstance(item, str) for item in unchecked):
        raise ContractError("review %d unchecked must be an array of strings" % (index + 1))
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
    return {
        "verdict": review["verdict"],
        "findings": normalized,
        "unchecked": [item.strip() for item in unchecked if item.strip()],
    }


def build_distiller_request(reviews):
    contract = load_contract()
    if not isinstance(reviews, list) or not reviews:
        raise ContractError("reviews must be a non-empty array")
    checked = [_validate_review(review, index, contract) for index, review in enumerate(reviews)]
    public_reviews = []
    for review in checked:
        public_reviews.append(
            {
                "verdict": review["verdict"],
                "findings": [
                    {
                        key: finding[key]
                        for key in ("issue", "severity", "where", "agreement_key")
                    }
                    for finding in review["findings"]
                ],
                "unchecked": review["unchecked"],
            }
        )
    schema = copy.deepcopy(contract["distiller_schema"])
    prompt = "\n".join(
        [
            contract["distillation"]["semantic_instruction"],
            "",
            "REVIEWS:",
            json.dumps(public_reviews, ensure_ascii=False, sort_keys=True),
            "",
            "DISTILLER RESPONSE SCHEMA (return JSON only):",
            json.dumps(schema, sort_keys=True),
        ]
    )
    return {"prompt": prompt, "schema": schema}


def _cluster_groups(checked, cluster_result):
    if not isinstance(cluster_result, dict) or not isinstance(
        cluster_result.get("clusters"), list
    ):
        raise ContractError("distiller result must contain a clusters array")
    expected = {
        (review_index + 1, finding_index + 1)
        for review_index, review in enumerate(checked)
        for finding_index, unused in enumerate(review["findings"])
    }
    used = set()
    groups = []
    for cluster in cluster_result["clusters"]:
        if not isinstance(cluster, dict) or not isinstance(cluster.get("finding_refs"), list):
            raise ContractError("distiller cluster must contain finding_refs")
        if not cluster["finding_refs"]:
            raise ContractError("distiller cluster cannot be empty")
        group = []
        for reference in cluster["finding_refs"]:
            if not isinstance(reference, dict):
                raise ContractError("distiller finding reference must be an object")
            review_number = reference.get("review")
            finding_number = reference.get("finding")
            if type(review_number) is not int or type(finding_number) is not int:
                raise ContractError("distiller finding reference must use integers")
            identity = (review_number, finding_number)
            if identity not in expected:
                raise ContractError("distiller finding reference is out of range")
            if identity in used:
                raise ContractError("distiller finding reference is duplicated")
            used.add(identity)
            group.append(checked[review_number - 1]["findings"][finding_number - 1])
        groups.append(group)
    if used != expected:
        raise ContractError("distiller must assign every finding exactly once")
    return groups


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


def distill_reviews(reviews, clusters, unchecked=None):
    contract = load_contract()
    if not isinstance(reviews, list) or not reviews:
        raise ContractError("reviews must be a non-empty array")
    checked = [_validate_review(review, index, contract) for index, review in enumerate(reviews)]
    groups = _cluster_groups(checked, clusters)
    distillation = contract["distillation"]
    severity_rank = {
        severity: index for index, severity in enumerate(distillation["severity_order"])
    }
    ranked = []
    for group in groups:
        reviewers = len({item["reviewer"] for item in group})
        severity = min(severity_rank[item["severity"]] for item in group)
        first_seen = min((item["reviewer"], item["order"]) for item in group)
        ranked.append((severity, -reviewers, first_seen, group))
    ranked.sort()
    threshold = distillation["agreement_threshold"]
    stable_groups = [item for item in ranked if -item[1] >= threshold]
    contested_groups = [item for item in ranked if -item[1] < threshold]
    stable_cap = distillation["stable_cap"]
    stable = [
        _display_finding(item[3], len(checked), severity_rank)
        for item in stable_groups[:stable_cap]
    ]
    contested = [
        _display_finding(item[3], len(checked), severity_rank)
        for item in contested_groups
    ]
    if unchecked is None:
        unchecked = []
    if not isinstance(unchecked, list) or not all(isinstance(item, str) for item in unchecked):
        raise ContractError("unchecked must be an array of strings")
    combined_unchecked = [item for review in checked for item in review["unchecked"]]
    combined_unchecked.extend(item.strip() for item in unchecked if item.strip())
    unchecked = []
    seen_unchecked = set()
    for item in combined_unchecked:
        if item not in seen_unchecked:
            seen_unchecked.add(item)
            unchecked.append(item)
    important = any(item["severity"] in ("critical", "important") for item in stable)
    stable_omitted = max(0, len(stable_groups) - stable_cap)
    sections = []
    if not important:
        sections.append(distillation["clean_line"])
    sections.extend(_render_findings(distillation["stable_heading"], stable))
    if stable_omitted:
        sections.append(
            distillation["stable_omitted_line"].format(count=stable_omitted)
        )
    sections.append("")
    sections.extend(_render_findings(distillation["contested_heading"], contested))
    sections.extend(["", "## " + distillation["unchecked_heading"]])
    sections.extend(["- " + item for item in unchecked] or ["None disclosed."])
    return {
        "stable": stable,
        "stable_omitted": stable_omitted,
        "contested": contested,
        "unchecked": unchecked,
        "reviewer_verdicts": [review["verdict"] for review in checked],
        "raw_finding_counts": [len(review["findings"]) for review in checked],
        "distilled": "\n".join(sections),
    }


def run_fixture(harness, invocation, reviews, clusters, unchecked):
    build_packet(harness, **invocation)
    build_distiller_request(reviews)
    return distill_reviews(reviews, clusters, unchecked)


def _write_json(value):
    json.dump(value, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    packet = subparsers.add_parser("packet")
    packet.add_argument("--harness", choices=sorted(ADAPTERS), required=True)
    packet.add_argument("--target", required=True)
    packet.add_argument("--spec", default="")
    packet.add_argument("--repo", default="")
    packet.add_argument("-k", "--reviewers", type=int)
    distiller_packet = subparsers.add_parser("distiller-packet")
    distiller_packet.add_argument("--reviews-file", type=Path, required=True)
    distill = subparsers.add_parser("distill")
    distill.add_argument("--reviews-file", type=Path, required=True)
    distill.add_argument("--clusters-file", type=Path, required=True)
    distill.add_argument("--unchecked", action="append", default=[])
    fixture = subparsers.add_parser("fixture")
    fixture.add_argument("--harness", choices=sorted(ADAPTERS), required=True)
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
        elif args.command == "distiller-packet":
            reviews = json.loads(args.reviews_file.read_text(encoding="utf-8"))
            result = build_distiller_request(reviews)
        elif args.command == "distill":
            reviews = json.loads(args.reviews_file.read_text(encoding="utf-8"))
            clusters = json.loads(args.clusters_file.read_text(encoding="utf-8"))
            result = distill_reviews(reviews, clusters, args.unchecked)
        elif args.command == "fixture":
            value = json.loads(args.fixture.read_text(encoding="utf-8"))
            result = run_fixture(
                args.harness,
                value["invocation"],
                value["reviews"],
                value["clusters"],
                value["unchecked"],
            )
        else:
            contract = load_contract()
            for harness in ADAPTERS:
                build_packet(harness, "main...HEAD")
            sample_reviews = [{"verdict": "good", "findings": [], "unchecked": []}]
            build_distiller_request(sample_reviews)
            distill_reviews(sample_reviews, {"clusters": []})
            result = {
                "contract_version": contract["contract_version"],
                "contract_sha256": contract_sha256(),
                "harnesses": sorted(ADAPTERS),
            }
    except (ContractError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print("error: " + str(exc), file=sys.stderr)
        return 2
    _write_json(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
