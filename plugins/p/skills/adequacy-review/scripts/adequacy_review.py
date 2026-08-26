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


SCHEMA_KEYWORDS = {
    "additionalProperties",
    "const",
    "description",
    "enum",
    "items",
    "minLength",
    "minimum",
    "properties",
    "required",
    "type",
}
SCHEMA_TYPES = {"array", "integer", "object", "string"}
LINE_BOUNDARIES = "\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029"


def _validate_schema_keywords(schema, label):
    if not isinstance(schema, dict):
        raise ContractError(label + " schema must be an object")
    if set(schema) - SCHEMA_KEYWORDS:
        raise ContractError(label + " schema uses unsupported constraints")
    schema_type = schema.get("type")
    if schema_type not in SCHEMA_TYPES:
        raise ContractError(label + " schema type is invalid")
    if "additionalProperties" in schema and (
        schema_type != "object" or type(schema["additionalProperties"]) is not bool
    ):
        raise ContractError(label + " schema additionalProperties is invalid")
    if "required" in schema and (
        schema_type != "object"
        or not isinstance(schema["required"], list)
        or not all(isinstance(item, str) for item in schema["required"])
        or len(schema["required"]) != len(set(schema["required"]))
    ):
        raise ContractError(label + " schema required fields are invalid")
    if "enum" in schema and (
        not isinstance(schema["enum"], list)
        or not schema["enum"]
        or any(not _schema_type_matches(item, schema_type) for item in schema["enum"])
        or len({json.dumps(item, sort_keys=True) for item in schema["enum"]})
        != len(schema["enum"])
    ):
        raise ContractError(label + " schema enum is invalid")
    if "const" in schema and not _schema_type_matches(schema["const"], schema_type):
        raise ContractError(label + " schema const is invalid")
    if "minLength" in schema and (
        schema_type != "string"
        or type(schema["minLength"]) is not int
        or schema["minLength"] < 0
    ):
        raise ContractError(label + " schema minLength is invalid")
    if "minimum" in schema and (
        schema_type != "integer"
        or type(schema["minimum"]) is not int
    ):
        raise ContractError(label + " schema minimum is invalid")
    properties = schema.get("properties")
    if properties is not None:
        if not isinstance(properties, dict):
            raise ContractError(label + " schema properties are invalid")
        if schema_type != "object":
            raise ContractError(label + " schema properties are invalid")
        required = schema.get("required", [])
        if set(required) - set(properties):
            raise ContractError(label + " schema required fields are invalid")
        for name, nested in properties.items():
            _validate_schema_keywords(nested, label + "." + name)
    if "items" in schema:
        if schema_type != "array":
            raise ContractError(label + " schema items are invalid")
        _validate_schema_keywords(schema["items"], label + ".items")


def _schema_type_matches(value, schema_type):
    return {
        "array": isinstance(value, list),
        "integer": type(value) is int,
        "object": isinstance(value, dict),
        "string": isinstance(value, str),
    }.get(schema_type, False)


def _validate_schema_value(value, schema, label):
    schema_type = schema["type"]
    if not _schema_type_matches(value, schema_type):
        raise ContractError(label + " has an invalid type")
    if "enum" in schema and value not in schema["enum"]:
        raise ContractError(label + " is outside the contract enum")
    if "const" in schema and value != schema["const"]:
        if label.endswith(".request_id"):
            raise ContractError(
                label.rsplit(".", 1)[0] + " request identity does not match contract"
            )
        if label.endswith(".reviews_sha256"):
            raise ContractError("distiller reviews digest does not match review results")
        raise ContractError(label + " does not match the contract identity")
    if schema_type == "string" and len(value) < schema.get("minLength", 0):
        raise ContractError(label + " is shorter than the contract minimum")
    if schema_type == "integer" and value < schema.get("minimum", value):
        raise ContractError(label + " is below the contract minimum")
    if schema_type == "object":
        properties = schema.get("properties", {})
        missing = [name for name in schema.get("required", []) if name not in value]
        if missing:
            raise ContractError(
                label + " is missing required fields: " + ", ".join(missing)
            )
        if schema.get("additionalProperties") is False and set(value) - set(properties):
            raise ContractError(label + " contains excess fields")
        for name, nested in properties.items():
            if name in value:
                _validate_schema_value(value[name], nested, label + "." + name)
    if schema_type == "array":
        for index, item in enumerate(value):
            _validate_schema_value(item, schema["items"], "%s[%d]" % (label, index))


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
    if not isinstance(contract, dict) or set(contract) != required:
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
    if (
        set(defaults) != {"reviewers"}
        or type(defaults.get("reviewers")) is not int
        or defaults["reviewers"] < 1
    ):
        raise ContractError("default reviewer count must be positive")
    if set(inputs) != {"target", "spec", "repo", "exclusions", "reviewers"} or not all(
        isinstance(value, str) and value.strip() for value in inputs.values()
    ):
        raise ContractError("contract inputs are invalid")
    _validate_schema_keywords(reviewer_schema, "reviewer")
    _validate_schema_keywords(distiller_schema, "distiller")
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
    if (
        reviewer_schema.get("type") != "object"
        or reviewer_schema.get("additionalProperties") is not False
        or reviewer_schema.get("required")
        != ["request_id", "verdict", "findings", "unchecked"]
        or set(reviewer_properties)
        != {"request_id", "verdict", "findings", "unchecked"}
    ):
        raise ContractError("reviewer schema root is invalid")
    if reviewer_properties.get("request_id", {}).get("type") != "string":
        raise ContractError("reviewer request identity schema is invalid")
    verdict_schema = reviewer_properties.get("verdict", {})
    if (
        verdict_schema.get("type") != "string"
        or not isinstance(verdict_schema.get("enum"), list)
        or not verdict_schema["enum"]
    ):
        raise ContractError("reviewer verdict schema is invalid")
    if (
        findings_schema.get("type") != "array"
        or finding_schema.get("type") != "object"
        or finding_schema.get("additionalProperties") is not False
        or finding_schema.get("required") != ["issue", "severity"]
    ):
        raise ContractError("reviewer finding schema is invalid")
    finding_properties = finding_schema.get("properties", {})
    if not isinstance(finding_properties, dict):
        raise ContractError("reviewer finding schema is invalid")
    if set(finding_properties) != {"issue", "severity", "where", "agreement_key"}:
        raise ContractError("reviewer finding schema is invalid")
    if finding_properties.get("issue", {}).get("type") != "string":
        raise ContractError("reviewer issue schema is invalid")
    severity_schema = finding_properties.get("severity", {})
    if (
        severity_schema.get("type") != "string"
        or not isinstance(severity_schema.get("enum"), list)
        or not severity_schema["enum"]
    ):
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
        or distiller_schema.get("additionalProperties") is not False
        or distiller_schema.get("required") != ["clusters", "reviews_sha256"]
        or set(distiller_properties) != {"clusters", "reviews_sha256"}
        or clusters_schema.get("type") != "array"
    ):
        raise ContractError("distiller schema root is invalid")
    if distiller_properties.get("reviews_sha256", {}).get("type") != "string":
        raise ContractError("distiller reviews digest schema is invalid")
    if (
        cluster_schema.get("type") != "object"
        or cluster_schema.get("additionalProperties") is not False
        or cluster_schema.get("required") != ["finding_refs"]
        or set(cluster_properties) != {"finding_refs"}
        or finding_refs_schema.get("type") != "array"
    ):
        raise ContractError("distiller cluster schema is invalid")
    if (
        reference_schema.get("type") != "object"
        or reference_schema.get("additionalProperties") is not False
        or reference_schema.get("required") != ["review", "finding"]
        or set(reference_schema.get("properties", {})) != {"review", "finding"}
    ):
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
    if not isinstance(review, dict) or set(review) != required_review_fields:
        raise ContractError("review prompt contract is incomplete")
    steps = review["required_steps"]
    if (
        not isinstance(steps, list)
        or not steps
        or not all(
            isinstance(step, dict) and set(step) == {"label", "instruction"}
            for step in steps
        )
        or not all(
            isinstance(step["label"], str) and step["label"].strip()
            for step in steps
        )
        or len({step["label"] for step in steps}) != len(steps)
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
    required_distillation_fields = {
        "agreement_threshold",
        "stable_cap",
        "severity_order",
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
        "clean_blocking_severities",
        "section_order",
        "ranking_tiebreakers",
    }
    if set(distillation) != required_distillation_fields:
        raise ContractError("distillation contract fields are invalid")
    if (
        type(distillation["agreement_threshold"]) is not int
        or distillation["agreement_threshold"] < 1
    ):
        raise ContractError("agreement threshold must be positive")
    if type(distillation["stable_cap"]) is not int or distillation["stable_cap"] < 1:
        raise ContractError("stable finding cap must be positive")
    severity_order = distillation["severity_order"]
    if (
        not isinstance(severity_order, list)
        or not severity_order
        or not all(isinstance(item, str) and item.strip() for item in severity_order)
        or len(severity_order) != len(set(severity_order))
        or severity_schema["enum"] != severity_order
    ):
        raise ContractError("severity order is invalid")
    for field in required_distillation_fields - {
        "agreement_threshold",
        "stable_cap",
        "severity_order",
        "clean_blocking_severities",
        "section_order",
        "ranking_tiebreakers",
    }:
        if not isinstance(distillation.get(field), str) or not distillation[field].strip():
            raise ContractError("distillation text is invalid")
    blocking = distillation["clean_blocking_severities"]
    if (
        not isinstance(blocking, list)
        or not all(item in severity_order for item in blocking)
        or len(blocking) != len(set(blocking))
    ):
        raise ContractError("clean blocking severities are invalid")
    section_order = distillation["section_order"]
    if (
        not isinstance(section_order, list)
        or len(section_order) != 4
        or set(section_order) != {"stable", "contested", "unchecked", "verdicts"}
    ):
        raise ContractError("distillation section order is invalid")
    ranking_tiebreakers = distillation["ranking_tiebreakers"]
    if (
        not isinstance(ranking_tiebreakers, list)
        or len(ranking_tiebreakers) != 3
        or set(ranking_tiebreakers)
        != {"severity", "agreement_desc", "first_seen"}
    ):
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


def _json_sha256(value):
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _required_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ContractError(label + " must be non-empty")
    if any(boundary in value for boundary in LINE_BOUNDARIES):
        raise ContractError(label + " must be a single line")
    return value.strip()


def _optional_text(value, label):
    if not isinstance(value, str):
        raise ContractError(label + " must be a string")
    if any(boundary in value for boundary in LINE_BOUNDARIES):
        raise ContractError(label + " must be a single line")
    return value.strip()


def _normalize_exclusions(exclusions):
    if exclusions is None:
        return []
    if not isinstance(exclusions, list):
        raise ContractError("exclusions must be an array of non-empty strings")
    return [_required_text(item, "exclusion") for item in exclusions]


def _reviewer_prompt(contract, target, spec, repo, exclusions, reviewer_schema):
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
            json.dumps(reviewer_schema, sort_keys=True),
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
    invocation = {
        "target": target,
        "spec": spec,
        "repo": repo,
        "exclusions": exclusions,
        "reviewers": count,
    }
    run_id = _json_sha256(
        {"contract_sha256": contract_sha256(), "input": invocation}
    )
    requests = []
    for index in range(count):
        request_id = _json_sha256(
            {"run_id": run_id, "review": index + 1}
        )
        schema = copy.deepcopy(contract["reviewer_schema"])
        schema["properties"]["request_id"]["const"] = request_id
        prompt = _reviewer_prompt(
            contract, target, spec, repo, exclusions, schema
        )
        requests.append(
            {
                "label": "review:%d" % (index + 1),
                "request_id": request_id,
                "prompt": prompt,
                "schema": schema,
            }
        )
    canonical = {
        "contract_version": contract["contract_version"],
        "contract_sha256": contract_sha256(),
        "run_id": run_id,
        "input": invocation,
        "review_requests": requests,
    }
    return {
        "transport": {"harness": harness, "adapter": ADAPTERS[harness]},
        "canonical": canonical,
    }


def _validate_review(review, index, contract, request_id):
    schema = copy.deepcopy(contract["reviewer_schema"])
    schema["properties"]["request_id"]["const"] = request_id
    _validate_schema_value(review, schema, "review %d" % (index + 1))
    findings = review["findings"]
    unchecked = [_required_text(item, "unchecked item") for item in review["unchecked"]]
    normalized = []
    for finding_index, finding in enumerate(findings):
        issue = _required_text(finding.get("issue"), "finding issue")
        severity = finding.get("severity")
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
        "request_id": request_id,
        "verdict": review["verdict"],
        "findings": normalized,
        "unchecked": unchecked,
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
    transport = packet.get("transport")
    if not isinstance(invocation, dict) or not isinstance(transport, dict):
        raise ContractError("packet canonical review data is invalid")
    if set(invocation) != {"target", "spec", "repo", "exclusions", "reviewers"}:
        raise ContractError("packet canonical review data is invalid")
    harness = transport.get("harness")
    try:
        expected = build_packet(
            harness,
            invocation["target"],
            spec=invocation["spec"],
            repo=invocation["repo"],
            exclusions=invocation["exclusions"],
            reviewers=invocation["reviewers"],
        )
    except (ContractError, KeyError, TypeError):
        raise ContractError("packet canonical review data is invalid") from None
    if packet != expected:
        raise ContractError("packet is not the canonical rendered packet")
    return invocation["reviewers"]


def _require_reviewer_count(reviews, packet, contract):
    reviewers = _packet_reviewer_count(packet, contract)
    if not isinstance(reviews, list) or len(reviews) != reviewers:
        raise ContractError(
            "expected %d reviewer results, received %d"
            % (reviewers, len(reviews) if isinstance(reviews, list) else 0)
        )
    return reviewers


def _checked_reviews(reviews, packet, contract):
    _require_reviewer_count(reviews, packet, contract)
    requests = packet["canonical"]["review_requests"]
    return [
        _validate_review(review, index, contract, requests[index]["request_id"])
        for index, review in enumerate(reviews)
    ]


def _public_reviews(checked):
    return [
        {
            "request_id": review["request_id"],
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
        for review in checked
    ]


def build_distiller_request(reviews, packet):
    contract = load_contract()
    checked = _checked_reviews(reviews, packet, contract)
    public_reviews = _public_reviews(checked)
    reviews_sha256 = _json_sha256(public_reviews)
    schema = copy.deepcopy(contract["distiller_schema"])
    schema["properties"]["reviews_sha256"]["const"] = reviews_sha256
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
    return {
        "prompt": prompt,
        "schema": schema,
        "reviews_sha256": reviews_sha256,
    }


def _cluster_groups(checked, cluster_result, reviews_sha256, contract):
    schema = copy.deepcopy(contract["distiller_schema"])
    schema["properties"]["reviews_sha256"]["const"] = reviews_sha256
    _validate_schema_value(cluster_result, schema, "distiller result")
    expected = {
        (review_index + 1, finding_index + 1)
        for review_index, review in enumerate(checked)
        for finding_index, unused in enumerate(review["findings"])
    }
    used = set()
    groups = []
    for cluster in cluster_result["clusters"]:
        if not cluster["finding_refs"]:
            raise ContractError("distiller cluster cannot be empty")
        group = []
        for reference in cluster["finding_refs"]:
            review_number = reference.get("review")
            finding_number = reference.get("finding")
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
    checked = _checked_reviews(reviews, packet, contract)
    reviews_sha256 = _json_sha256(_public_reviews(checked))
    groups = _cluster_groups(checked, clusters, reviews_sha256, contract)
    distillation = contract["distillation"]
    severity_rank = {
        severity: index for index, severity in enumerate(distillation["severity_order"])
    }
    ranked = []
    for group in groups:
        reviewers = len({item["reviewer"] for item in group})
        severity = min(severity_rank[item["severity"]] for item in group)
        first_seen = min((item["reviewer"], item["order"]) for item in group)
        ranked.append(
            {
                "agreement": reviewers,
                "group": group,
                "ranking": {
                    "severity": severity,
                    "agreement_desc": -reviewers,
                    "first_seen": first_seen,
                },
            }
        )
    ranked.sort(
        key=lambda item: tuple(
            item["ranking"][name]
            for name in distillation["ranking_tiebreakers"]
        )
    )
    threshold = distillation["agreement_threshold"]
    stable_groups = [item for item in ranked if item["agreement"] >= threshold]
    contested_groups = [item for item in ranked if item["agreement"] < threshold]
    stable_cap = distillation["stable_cap"]
    stable = [
        _display_finding(item["group"], len(checked), severity_rank)
        for item in stable_groups[:stable_cap]
    ]
    contested = [
        _display_finding(item["group"], len(checked), severity_rank)
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
    reviews = copy.deepcopy(reviews)
    for review, request in zip(reviews, packet["canonical"]["review_requests"]):
        review["request_id"] = request["request_id"]
    distiller_request = build_distiller_request(reviews, packet)
    clusters = copy.deepcopy(clusters)
    clusters["reviews_sha256"] = distiller_request["reviews_sha256"]
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
            sample_packet = build_packet("codex", "main...HEAD", reviewers=1)
            sample_reviews = [
                {
                    "request_id": sample_packet["canonical"]["review_requests"][0][
                        "request_id"
                    ],
                    "verdict": "good",
                    "findings": [],
                    "unchecked": [],
                }
            ]
            sample_distiller = build_distiller_request(sample_reviews, sample_packet)
            distill_reviews(
                sample_reviews,
                {
                    "clusters": [],
                    "reviews_sha256": sample_distiller["reviews_sha256"],
                },
                packet=sample_packet,
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
