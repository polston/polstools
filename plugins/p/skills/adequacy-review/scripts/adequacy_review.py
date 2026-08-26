#!/usr/bin/env python3
"""Render and distill the versioned portable adequacy-review contract."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import string
import sys


SKILL_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PATH = SKILL_ROOT / "contract-v1.json"
ADAPTERS = {
    "claude-code": "references/claude-code.md",
    "codex": "references/codex.md",
}


class ContractError(ValueError):
    pass


def _validate_template(value, expected_fields, label):
    if not isinstance(value, str) or not value.strip():
        raise ContractError(label + " template is invalid")
    fields = []
    try:
        parsed = string.Formatter().parse(value)
        for unused_literal, field, format_spec, conversion in parsed:
            if field is None:
                continue
            if format_spec or conversion or "." in field or "[" in field:
                raise ContractError(label + " template is invalid")
            fields.append(field)
    except ValueError:
        raise ContractError(label + " template is invalid") from None
    if set(fields) != set(expected_fields):
        raise ContractError(label + " template placeholders are invalid")


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
    if type(defaults.get("reviewers")) is not int or defaults["reviewers"] < 1:
        raise ContractError("default reviewer count must be positive")
    if set(inputs) != {"target", "spec", "repo", "exclusions", "reviewers"} or not all(
        isinstance(value, str) and value.strip() for value in inputs.values()
    ):
        raise ContractError("contract inputs are invalid")
    try:
        reviewer_properties = reviewer_schema["properties"]
        findings_schema = reviewer_properties["findings"]
        finding_schema = findings_schema["items"]
        unchecked_schema = reviewer_properties["unchecked"]
        distiller_properties = distiller_schema["properties"]
        clusters_schema = distiller_properties["clusters"]
        cluster_schema = clusters_schema["items"]
        cluster_properties = cluster_schema["properties"]
        finding_refs_schema = cluster_properties["finding_refs"]
        reference_schema = finding_refs_schema["items"]
    except (KeyError, TypeError):
        raise ContractError("reviewer or distiller schema is incomplete") from None
    if not all(
        isinstance(value, dict)
        for value in (
            reviewer_properties,
            findings_schema,
            finding_schema,
            unchecked_schema,
            distiller_properties,
            clusters_schema,
            cluster_schema,
            cluster_properties,
            finding_refs_schema,
            reference_schema,
        )
    ):
        raise ContractError("reviewer or distiller schema containers are invalid")
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
    if (
        distiller_schema.get("type") != "object"
        or distiller_schema.get("required") != ["clusters"]
        or clusters_schema.get("type") != "array"
    ):
        raise ContractError("distiller schema root is invalid")
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
        "target_line",
        "target_instruction",
        "spec_present",
        "spec_absent",
        "repo_present",
        "repo_absent",
        "exclusions_present",
        "exclusions_absent",
        "steps_heading",
        "step_line_template",
        "required_steps",
        "report",
        "reviewer_schema_heading",
    }
    if not isinstance(review, dict) or not required_review_fields.issubset(review):
        raise ContractError("review prompt contract is incomplete")
    steps = review["required_steps"]
    if (
        not isinstance(steps, list)
        or not all(isinstance(step, dict) for step in steps)
        or [step.get("label") for step in steps]
        != [
        "GROUND",
        "TRACE",
        ]
    ):
        raise ContractError("review steps are invalid")
    for field in required_review_fields - {"required_steps"}:
        if not isinstance(review[field], str) or not review[field].strip():
            raise ContractError("review prompt contract is invalid")
    if not all(isinstance(step.get("instruction"), str) and step["instruction"].strip() for step in steps):
        raise ContractError("review steps are invalid")
    _validate_template(review["spec_present"], {"spec"}, "spec_present")
    _validate_template(review["repo_present"], {"repo"}, "repo_present")
    _validate_template(review["target_line"], {"target"}, "target_line")
    _validate_template(
        review["exclusions_present"], {"exclusions"}, "exclusions_present"
    )
    _validate_template(
        review["step_line_template"],
        {"index", "label", "instruction"},
        "step_line",
    )
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
        "heading_template",
        "finding_line_template",
        "location_suffix_template",
        "unchecked_item_template",
        "empty_findings_line",
        "empty_unchecked_line",
        "clean_line",
        "semantic_instruction",
        "reviews_heading",
        "distiller_schema_heading",
        "reviewer_verdicts_heading",
        "verdict_line_template",
    ):
        if not isinstance(distillation.get(field), str) or not distillation[field].strip():
            raise ContractError("distillation text is invalid")
    if distillation.get("clean_blocking_severities") != ["critical", "important"]:
        raise ContractError("clean blocking severities are invalid")
    if distillation.get("section_order") != [
        "stable",
        "contested",
        "unchecked",
        "verdicts",
    ]:
        raise ContractError("distillation section order is invalid")
    if distillation.get("ranking_tiebreakers") != [
        "severity",
        "agreement_desc",
        "first_seen",
    ]:
        raise ContractError("distillation ranking policy is invalid")
    _validate_template(
        distillation["stable_omitted_line"], {"count"}, "stable_omitted_line"
    )
    _validate_template(distillation["heading_template"], {"heading"}, "heading")
    _validate_template(
        distillation["finding_line_template"],
        {"index", "severity", "issue", "agreement", "location_suffix"},
        "finding_line",
    )
    _validate_template(
        distillation["location_suffix_template"], {"where"}, "location_suffix"
    )
    _validate_template(
        distillation["unchecked_item_template"], {"item"}, "unchecked_item"
    )
    _validate_template(
        distillation["verdict_line_template"], {"index", "verdict"}, "verdict_line"
    )
    return contract


def contract_sha256(path=CONTRACT_PATH):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _required_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ContractError(label + " must be non-empty")
    if "\n" in value or "\r" in value:
        raise ContractError(label + " must be a single line")
    return value.strip()


def _optional_text(value, label):
    if not isinstance(value, str):
        raise ContractError(label + " must be a string")
    if "\n" in value or "\r" in value:
        raise ContractError(label + " must be a single line")
    return value.strip()


def _normalize_exclusions(exclusions):
    if exclusions is None:
        return []
    if not isinstance(exclusions, list):
        raise ContractError("exclusions must be an array of non-empty strings")
    return [_required_text(item, "exclusion") for item in exclusions]


def _reviewer_prompt(contract, target, spec, repo, exclusions):
    review = contract["review"]
    lines = [
        review["preamble"],
        "",
        review["target_line"].format(target=target),
        review["target_instruction"],
    ]
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
    lines.append(
        review["exclusions_present"].format(
            exclusions=json.dumps(exclusions, ensure_ascii=False)
        )
        if exclusions
        else review["exclusions_absent"]
    )
    lines.extend(["", review["steps_heading"]])
    for index, step in enumerate(review["required_steps"], start=1):
        lines.append(
            review["step_line_template"].format(
                index=index,
                label=step["label"],
                instruction=step["instruction"],
            )
        )
    lines.extend(
        [
            "",
            review["report"],
            "",
            review["reviewer_schema_heading"],
            json.dumps(contract["reviewer_schema"], sort_keys=True),
        ]
    )
    return "\n".join(lines)


def build_packet(harness, target, spec="", repo="", reviewers=None, exclusions=None):
    if harness not in ADAPTERS:
        raise ContractError("unsupported harness: " + str(harness))
    contract = load_contract()
    target = _required_text(target, "target")
    spec = _optional_text(spec, "spec")
    repo = _optional_text(repo, "repo")
    exclusions = _normalize_exclusions(exclusions)
    count = contract["defaults"]["reviewers"] if reviewers is None else reviewers
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise ContractError("reviewer count must be a positive integer")
    prompt = _reviewer_prompt(
        contract, target, spec, repo, exclusions
    )
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
            "spec": spec,
            "repo": repo,
            "exclusions": exclusions,
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
    if not isinstance(unchecked, list):
        raise ContractError("review %d unchecked must be an array of strings" % (index + 1))
    unchecked = [_optional_text(item, "unchecked item") for item in unchecked]
    severities = set(contract["distillation"]["severity_order"])
    normalized = []
    for finding_index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise ContractError("review finding must be an object")
        issue = _required_text(finding.get("issue"), "finding issue")
        severity = finding.get("severity")
        if severity not in severities:
            raise ContractError("finding severity is invalid")
        where = _optional_text(finding.get("where", ""), "finding where")
        agreement_key = _optional_text(
            finding.get("agreement_key", ""), "finding agreement_key"
        )
        normalized.append(
            {
                "issue": issue,
                "severity": severity,
                "where": where,
                "agreement_key": agreement_key,
                "reviewer": index,
                "order": finding_index,
            }
        )
    return {
        "verdict": review["verdict"],
        "findings": normalized,
        "unchecked": [item for item in unchecked if item],
    }


def _packet_reviewer_count(packet, contract):
    if not isinstance(packet, dict) or not isinstance(packet.get("canonical"), dict):
        raise ContractError("packet must contain canonical review data")
    canonical = packet["canonical"]
    if (
        canonical.get("contract_version") != contract["contract_version"]
        or canonical.get("contract_sha256") != contract_sha256()
    ):
        raise ContractError("packet contract does not match the active contract")
    invocation = canonical.get("input")
    requests = canonical.get("review_requests")
    if not isinstance(invocation, dict) or not isinstance(requests, list):
        raise ContractError("packet canonical review data is invalid")
    reviewers = invocation.get("reviewers")
    if type(reviewers) is not int or reviewers < 1 or len(requests) != reviewers:
        raise ContractError("packet reviewer count is invalid")
    for index, request in enumerate(requests, start=1):
        if (
            not isinstance(request, dict)
            or request.get("label") != "review:%d" % index
            or request.get("schema") != contract["reviewer_schema"]
            or not isinstance(request.get("prompt"), str)
        ):
            raise ContractError("packet review request is invalid")
    return reviewers


def _require_reviewer_count(reviews, packet, contract):
    reviewers = _packet_reviewer_count(packet, contract)
    if not isinstance(reviews, list) or len(reviews) != reviewers:
        raise ContractError(
            "expected %d reviewer results, received %d"
            % (reviewers, len(reviews) if isinstance(reviews, list) else 0)
        )
    return reviewers


def build_distiller_request(reviews, packet):
    contract = load_contract()
    _require_reviewer_count(reviews, packet, contract)
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
            contract["distillation"]["reviews_heading"],
            json.dumps(public_reviews, ensure_ascii=False, sort_keys=True),
            "",
            contract["distillation"]["distiller_schema_heading"],
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


def _render_findings(distillation, title, findings):
    lines = [distillation["heading_template"].format(heading=title)]
    if not findings:
        lines.append(distillation["empty_findings_line"])
        return lines
    for index, finding in enumerate(findings, start=1):
        location = (
            distillation["location_suffix_template"].format(where=finding["where"])
            if finding["where"]
            else ""
        )
        lines.append(
            distillation["finding_line_template"].format(
                index=index,
                severity=finding["severity"],
                issue=finding["issue"],
                agreement=finding["agreement"],
                location_suffix=location,
            )
        )
    return lines


def distill_reviews(reviews, clusters, unchecked=None, packet=None):
    contract = load_contract()
    _require_reviewer_count(reviews, packet, contract)
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
    if not isinstance(unchecked, list):
        raise ContractError("unchecked must be an array of strings")
    unchecked = [_optional_text(item, "unchecked item") for item in unchecked]
    combined_unchecked = [item for review in checked for item in review["unchecked"]]
    combined_unchecked.extend(item.strip() for item in unchecked if item.strip())
    unchecked = []
    seen_unchecked = set()
    for item in combined_unchecked:
        if item not in seen_unchecked:
            seen_unchecked.add(item)
            unchecked.append(item)
    important = any(
        item["severity"] in distillation["clean_blocking_severities"]
        for item in stable
    )
    stable_omitted = max(0, len(stable_groups) - stable_cap)
    sections = []
    if not important:
        sections.append(distillation["clean_line"])
    stable_section = _render_findings(
        distillation, distillation["stable_heading"], stable
    )
    if stable_omitted:
        stable_section.append(
            distillation["stable_omitted_line"].format(count=stable_omitted)
        )
    section_lines = {
        "stable": stable_section,
        "contested": _render_findings(
            distillation, distillation["contested_heading"], contested
        ),
        "unchecked": [
            distillation["heading_template"].format(
                heading=distillation["unchecked_heading"]
            ),
            *(
                [
                    distillation["unchecked_item_template"].format(item=item)
                    for item in unchecked
                ]
                or [distillation["empty_unchecked_line"]]
            ),
        ],
        "verdicts": [
            distillation["heading_template"].format(
                heading=distillation["reviewer_verdicts_heading"]
            ),
            *[
                distillation["verdict_line_template"].format(
                    index=index,
                    verdict=review["verdict"],
                )
                for index, review in enumerate(checked, start=1)
            ],
        ],
    }
    for index, section in enumerate(distillation["section_order"]):
        if index:
            sections.append("")
        sections.extend(section_lines[section])
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
    packet = build_packet(harness, **invocation)
    build_distiller_request(reviews, packet)
    return distill_reviews(reviews, clusters, unchecked, packet)


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
    packet.add_argument("--exclude", dest="exclusions", action="append", default=[])
    packet.add_argument("-k", "--reviewers", type=int)
    distiller_packet = subparsers.add_parser("distiller-packet")
    distiller_packet.add_argument("--reviews-file", type=Path, required=True)
    distiller_packet.add_argument("--packet-file", type=Path, required=True)
    distill = subparsers.add_parser("distill")
    distill.add_argument("--reviews-file", type=Path, required=True)
    distill.add_argument("--clusters-file", type=Path, required=True)
    distill.add_argument("--packet-file", type=Path, required=True)
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
                exclusions=args.exclusions,
            )
        elif args.command == "distiller-packet":
            reviews = json.loads(args.reviews_file.read_text(encoding="utf-8"))
            packet = json.loads(args.packet_file.read_text(encoding="utf-8"))
            result = build_distiller_request(reviews, packet)
        elif args.command == "distill":
            reviews = json.loads(args.reviews_file.read_text(encoding="utf-8"))
            clusters = json.loads(args.clusters_file.read_text(encoding="utf-8"))
            packet = json.loads(args.packet_file.read_text(encoding="utf-8"))
            result = distill_reviews(reviews, clusters, args.unchecked, packet)
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
            sample_packet = build_packet("codex", "main...HEAD", reviewers=1)
            build_distiller_request(sample_reviews, sample_packet)
            distill_reviews(
                sample_reviews, {"clusters": []}, packet=sample_packet
            )
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
