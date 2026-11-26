#!/usr/bin/env python3
"""Deterministic helper for blinded A/B skill sparring and anti-bloat edit gating."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import re
import secrets
import sys
import tempfile

SKILL_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = SKILL_ROOT / "contract-v1.json"

for _p in (str(PLUGIN_ROOT), str(PLUGIN_ROOT / "bin")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from p_validate import parse_frontmatter
from retro_eval.instruction_manifest import hash_instruction_source
from retro_eval.proposal_report import _inside_repository


def _load_contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def _require_external_dir(path: Path) -> Path:
    resolved = path.resolve()
    if _inside_repository(resolved):
        raise ValueError("sparring work directory must remain outside every Git repository")
    return resolved


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _dir_total_bytes(root: Path) -> int:
    total = 0
    for item in sorted(root.rglob("*")):
        if item.is_file():
            total += item.stat().st_size
    return total


def _scan_blinding_leaks(text: str, patterns: list[str]) -> list[dict]:
    hits = []
    for line_no, line in enumerate(text.splitlines(), 1):
        for pat in patterns:
            m = re.search(pat, line, re.IGNORECASE)
            if m:
                hits.append({"line": line_no, "pattern": pat, "match": m.group(0)})
    return hits


def _strip_blinding_leaks(text: str, patterns: list[str]) -> str:
    cleaned = text
    for pat in patterns:
        cleaned = re.sub(pat, "[REDACTED_META]", cleaned, flags=re.IGNORECASE)
    return cleaned


def _validate_judge_payload(payload: dict, expected_request_id: str, expected_sha: str) -> None:
    required = {
        "request_id",
        "artifacts_sha256",
        "axis_a_winner",
        "axis_b_winner",
        "overall_winner",
        "candidate_x_defects",
        "candidate_y_defects",
        "concrete_skill_gaps",
        "unchecked",
    }
    if not isinstance(payload, dict):
        raise ValueError("judge response must be a JSON object")
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"judge response missing required fields: {missing}")
    if payload["request_id"] != expected_request_id:
        raise ValueError(
            f"request_id mismatch: expected {expected_request_id!r}, got {payload['request_id']!r}"
        )
    if payload["artifacts_sha256"] != expected_sha:
        raise ValueError(
            f"artifacts_sha256 mismatch: expected {expected_sha!r}, got {payload['artifacts_sha256']!r}"
        )
    for key in ("axis_a_winner", "axis_b_winner", "overall_winner"):
        if payload[key] not in ("X", "Y", "tie"):
            raise ValueError(f"invalid {key}: {payload[key]!r}")


def cmd_init(args) -> int:
    contract = _load_contract()
    if args.stage not in contract["stages"]:
        raise ValueError(f"unsupported stage {args.stage!r}; expected one of {contract['stages']}")
    work_dir = _require_external_dir(args.work_dir)
    skill_dir = args.skill_dir.resolve()
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        raise FileNotFoundError(f"SKILL.md not found in {skill_dir}")
    brief_text = args.brief.resolve().read_text(encoding="utf-8")
    work_dir.mkdir(parents=True, exist_ok=True)
    round_id = args.round_id or f"{args.stage}-{secrets.token_hex(4)}"
    round_dir = work_dir / round_id
    round_dir.mkdir(parents=True, exist_ok=True)
    brief_copy = round_dir / "brief.md"
    brief_copy.write_text(brief_text, encoding="utf-8")

    iso = contract["worker_isolation"]
    control_prompt = (
        f"{iso['control_preamble']}\n{iso['leak_ban']}\n\n"
        f"OUTPUT_PATH: {round_dir / 'control.md'}\n\n"
        f"SHARED_BRIEF:\n{brief_text}"
    )
    treatment_prompt = (
        f"{iso['treatment_preamble'].format(skill_path=skill_md)}\n{iso['leak_ban']}\n\n"
        f"OUTPUT_PATH: {round_dir / 'treatment.md'}\n\n"
        f"SHARED_BRIEF:\n{brief_text}"
    )
    meta = {
        "contract_version": contract["contract_version"],
        "round_id": round_id,
        "stage": args.stage,
        "harness": args.harness,
        "skill_dir": str(skill_dir),
        "skill_sha256": hash_instruction_source(skill_dir),
        "skill_bytes": _dir_total_bytes(skill_dir),
        "brief_sha256": _sha256_bytes(brief_text.encode("utf-8")),
        "control_output": str(round_dir / "control.md"),
        "treatment_output": str(round_dir / "treatment.md"),
    }
    (round_dir / "round.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    packet = {
        **meta,
        "round_dir": str(round_dir),
        "control_prompt": control_prompt,
        "treatment_prompt": treatment_prompt,
    }
    (round_dir / "worker-packets.json").write_text(json.dumps(packet, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(packet, indent=2))
    return 0


def cmd_anonymize(args) -> int:
    contract = _load_contract()
    round_dir = _require_external_dir(args.round_dir)
    control_path = args.control_file.resolve()
    treatment_path = args.treatment_file.resolve()
    control_txt = control_path.read_text(encoding="utf-8")
    treatment_txt = treatment_path.read_text(encoding="utf-8")
    if not control_txt.strip() or not treatment_txt.strip():
        raise ValueError("both control and treatment artifacts must be non-empty")

    patterns = contract["blinding_leak_patterns"]
    control_leaks = _scan_blinding_leaks(control_txt, patterns)
    treatment_leaks = _scan_blinding_leaks(treatment_txt, patterns)
    sanitized = False
    if control_leaks or treatment_leaks:
        if not args.strip_leaks:
            print(
                json.dumps(
                    {
                        "status": "blinding_leak_detected",
                        "control_leaks": control_leaks,
                        "treatment_leaks": treatment_leaks,
                    },
                    indent=2,
                )
            )
            return 1
        control_txt = _strip_blinding_leaks(control_txt, patterns)
        treatment_txt = _strip_blinding_leaks(treatment_txt, patterns)
        sanitized = True

    rng = random.Random(args.seed) if args.seed is not None else secrets.SystemRandom()
    if rng.choice([0, 1]) == 0:
        mapping = {"X": "control", "Y": "treatment"}
        x_txt, y_txt = control_txt, treatment_txt
    else:
        mapping = {"X": "treatment", "Y": "control"}
        x_txt, y_txt = treatment_txt, control_txt

    x_path = round_dir / "candidate-X.md"
    y_path = round_dir / "candidate-Y.md"
    x_path.write_text(x_txt, encoding="utf-8")
    y_path.write_text(y_txt, encoding="utf-8")

    x_sha = _sha256_bytes(x_txt.encode("utf-8"))
    y_sha = _sha256_bytes(y_txt.encode("utf-8"))
    artifacts_sha = _sha256_bytes(f"X:{x_sha}\nY:{y_sha}".encode("utf-8"))

    key_data = {
        "mapping": mapping,
        "candidate_x_sha256": x_sha,
        "candidate_y_sha256": y_sha,
        "artifacts_sha256": artifacts_sha,
        "blinding_sanitized": sanitized,
        "leak_warnings": {"control": control_leaks, "treatment": treatment_leaks},
    }
    key_path = round_dir / "sealed-key.json"
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(key_data, indent=2) + "\n")
    except Exception:
        os.close(fd)
        raise

    out = {
        "status": "anonymized",
        "candidate_x": str(x_path),
        "candidate_y": str(y_path),
        "artifacts_sha256": artifacts_sha,
        "blinding_sanitized": sanitized,
    }
    print(json.dumps(out, indent=2))
    return 0


def cmd_judge_packet(args) -> int:
    contract = _load_contract()
    round_dir = _require_external_dir(args.round_dir)
    key_data = json.loads((round_dir / "sealed-key.json").read_text(encoding="utf-8"))
    brief_txt = (round_dir / "brief.md").read_text(encoding="utf-8")
    x_txt = (round_dir / "candidate-X.md").read_text(encoding="utf-8")
    y_txt = (round_dir / "candidate-Y.md").read_text(encoding="utf-8")
    judges = args.judges if args.judges is not None else int(contract["defaults"]["judges"])
    if judges < 1 or judges > 5:
        raise ValueError("--judges must be between 1 and 5")

    schema_json = json.dumps(contract["judge_schema"], indent=2)
    requests = []
    for idx in range(1, judges + 1):
        req_id = f"judge-{idx}-{secrets.token_hex(4)}"
        prompt = (
            f"{contract['judge_prompt']['preamble']}\n\n"
            f"REQUEST_ID: {req_id}\n"
            f"ARTIFACTS_SHA256: {key_data['artifacts_sha256']}\n\n"
            f"SHARED_BRIEF:\n{brief_txt}\n\n"
            f"=== CANDIDATE X ===\n{x_txt}\n\n"
            f"=== CANDIDATE Y ===\n{y_txt}\n\n"
            f"RESPONSE_SCHEMA (JSON only):\n{schema_json}"
        )
        requests.append({"judge_index": idx, "request_id": req_id, "prompt": prompt})

    packet = {
        "artifacts_sha256": key_data["artifacts_sha256"],
        "judges": judges,
        "requests": requests,
    }
    out_path = round_dir / "judge-packets.json"
    out_path.write_text(json.dumps(packet, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"judge_packets_file": str(out_path), "request_ids": [r["request_id"] for r in requests]}, indent=2))
    return 0


def _map_winner(label: str, mapping: dict) -> str:
    if label == "tie":
        return "tie"
    return mapping[label]


def cmd_unblind_score(args) -> int:
    round_dir = _require_external_dir(args.round_dir)
    key_data = json.loads((round_dir / "sealed-key.json").read_text(encoding="utf-8"))
    packets = json.loads((round_dir / "judge-packets.json").read_text(encoding="utf-8"))
    responses = json.loads(args.responses_file.resolve().read_text(encoding="utf-8"))
    if not isinstance(responses, list) or len(responses) != len(packets["requests"]):
        raise ValueError("judge responses must be a JSON array matching the canonical judge request count")

    mapping = key_data["mapping"]
    expected_sha = key_data["artifacts_sha256"]
    unblinded = []
    overall_votes = {"control": 0, "treatment": 0, "tie": 0}
    axis_a_votes = {"control": 0, "treatment": 0, "tie": 0}
    axis_b_votes = {"control": 0, "treatment": 0, "tie": 0}
    defects_by_arm = {"control": [], "treatment": []}
    skill_gaps = []
    unchecked = []

    for req, resp in zip(packets["requests"], responses):
        _validate_judge_payload(resp, req["request_id"], expected_sha)
        ov = _map_winner(resp["overall_winner"], mapping)
        aa = _map_winner(resp["axis_a_winner"], mapping)
        ab = _map_winner(resp["axis_b_winner"], mapping)
        overall_votes[ov] += 1
        axis_a_votes[aa] += 1
        axis_b_votes[ab] += 1
        defects_by_arm[mapping["X"]].extend(resp["candidate_x_defects"])
        defects_by_arm[mapping["Y"]].extend(resp["candidate_y_defects"])
        skill_gaps.extend(resp["concrete_skill_gaps"])
        unchecked.extend(resp["unchecked"])
        unblinded.append({
            "request_id": req["request_id"],
            "axis_a_winner": aa,
            "axis_b_winner": ab,
            "overall_winner": ov,
        })

    non_zero_arms = [k for k in ("control", "treatment") if overall_votes[k] > 0]
    contested = len(non_zero_arms) > 1 or (overall_votes["tie"] > 0 and len(non_zero_arms) >= 1)
    if overall_votes["treatment"] > overall_votes["control"]:
        consensus = "treatment"
    elif overall_votes["control"] > overall_votes["treatment"]:
        consensus = "control"
    else:
        consensus = "tie"

    verdict = {
        "artifacts_sha256": expected_sha,
        "judges": len(unblinded),
        "consensus_winner": consensus,
        "contested": contested,
        "overall_votes": overall_votes,
        "axis_a_votes": axis_a_votes,
        "axis_b_votes": axis_b_votes,
        "per_judge": unblinded,
        "defects_by_arm": defects_by_arm,
        "concrete_skill_gaps": skill_gaps,
        "unchecked": unchecked,
    }
    (round_dir / "verdict.json").write_text(json.dumps(verdict, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(verdict, indent=2))
    return 0


def _diff_mentions_path(diff_text: str, rel_or_name: str) -> bool:
    norm = rel_or_name.replace("\\", "/").lstrip("./")
    for line in diff_text.splitlines():
        if line.startswith("+++ b/") or line.startswith("diff --git "):
            if norm in line:
                return True
    return False


def cmd_edit_gate(args) -> int:
    contract = _load_contract()
    gate_cfg = contract["edit_gate"]
    before_dir = args.before_skill_dir.resolve()
    after_dir = args.after_skill_dir.resolve()
    if not (before_dir / "SKILL.md").is_file() or not (after_dir / "SKILL.md").is_file():
        raise FileNotFoundError("both before and after skill directories must contain SKILL.md")

    before_bytes = _dir_total_bytes(before_dir)
    after_bytes = _dir_total_bytes(after_dir)
    delta_bytes = after_bytes - before_bytes
    max_bytes = args.max_bytes if args.max_bytes is not None else int(gate_cfg["max_skill_bytes"])

    diff_text = args.diff_file.resolve().read_text(encoding="utf-8") if args.diff_file else ""
    added_lines = [
        line[1:]
        for line in diff_text.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]
    if not added_lines:
        # Fallback: scan full after SKILL.md when no diff file supplied
        added_lines = (after_dir / "SKILL.md").read_text(encoding="utf-8").splitlines()

    violations = []

    # 1. Hard byte cap
    if after_bytes > max_bytes:
        violations.append(f"skill size {after_bytes} B exceeds max_skill_bytes ceiling {max_bytes} B")

    # 2. Delete/tighten-first + diff-verified regression test on ANY net byte growth
    if delta_bytes > 0:
        if gate_cfg["require_tightened_clause_on_growth"] and not (args.tightened_clause or "").strip():
            violations.append(
                f"net skill growth (+{delta_bytes} B) requires --tightened-clause documenting what was compressed or evicted first"
            )
        if gate_cfg["require_diff_verified_regression_on_growth"]:
            if not (args.regression_test or "").strip():
                violations.append(f"net skill growth (+{delta_bytes} B) requires --regression-test")
            elif not diff_text or not _diff_mentions_path(diff_text, args.regression_test):
                violations.append(
                    f"regression test {args.regression_test!r} was not verified as added/modified in --diff-file"
                )

    # 3. Generalizability & no-private-data scan on added lines
    if not (args.rationale or "").strip():
        violations.append("missing --rationale describing the general class of failure addressed")
    for line in added_lines:
        for pat in gate_cfg["forbidden_added_line_patterns"]:
            m = re.search(pat, line)
            if m:
                violations.append(f"non-generalizable or private pattern in added line: {m.group(0)!r}")
                break

    # 4. Frontmatter validation via p_validate.parse_frontmatter
    after_skill_text = (after_dir / "SKILL.md").read_text(encoding="utf-8")
    try:
        fm = parse_frontmatter(after_skill_text)
    except ValueError as err:
        violations.append(f"frontmatter error: {err}")
    else:
        if not fm.get("name") or not fm.get("description"):
            violations.append("frontmatter must include non-empty name and description")

    receipt = {
        "passed": not violations,
        "before_sha256": hash_instruction_source(before_dir),
        "after_sha256": hash_instruction_source(after_dir),
        "before_bytes": before_bytes,
        "after_bytes": after_bytes,
        "delta_bytes": delta_bytes,
        "max_skill_bytes": max_bytes,
        "violations": violations,
    }
    print(json.dumps(receipt, indent=2))
    return 0 if not violations else 1


def cmd_chain_advance(args) -> int:
    contract = _load_contract()
    if args.next_stage not in contract["stages"]:
        raise ValueError(f"unsupported stage {args.next_stage!r}")
    work_dir = _require_external_dir(args.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    promoted = args.promoted_artifact.resolve()
    if not promoted.is_file():
        raise FileNotFoundError(f"promoted artifact not found: {promoted}")
    art_sha = _sha256_bytes(promoted.read_bytes())

    state_path = work_dir / "chain-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {"stages": []}
    entry = {
        "stage": args.stage,
        "next_stage": args.next_stage,
        "selected_arm": args.selected_arm,
        "promoted_artifact": str(promoted),
        "promoted_sha256": art_sha,
    }
    state["stages"].append(entry)
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    result = {"chain_state": str(state_path), "entry": entry}
    if args.next_stage == "pilot-handoff":
        pilot_draft = {
            "registration_format": 1,
            "proposal_identity": args.proposal_id or f"sparring-{args.stage}-{art_sha[:8]}",
            "promoted_artifact_sha256": art_sha,
            "quality_guardrails": [
                "No regression on Axis A (Safety / Root-Cause Rigor)",
                "No regression on Axis B (Proportionality / Anti-Waste)",
            ],
            "initial_disposition": "insufficient_evidence",
            "next_step": "Register via reviewing-improvement-effects custom-pilot workflow after live follow-up observation.",
        }
        draft_path = work_dir / "pilot-handoff-draft.json"
        draft_path.write_text(json.dumps(pilot_draft, indent=2) + "\n", encoding="utf-8")
        result["pilot_draft"] = str(draft_path)
    print(json.dumps(result, indent=2))
    return 0


def cmd_self_check(_args) -> int:
    contract = _load_contract()
    for key in ("contract_version", "defaults", "stages", "worker_isolation", "blinding_leak_patterns", "judge_schema", "edit_gate"):
        if key not in contract:
            raise ValueError(f"contract-v1.json missing required key: {key}")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        round_dir = tmp_path / "round1"
        round_dir.mkdir()
        (round_dir / "brief.md").write_text("Synthetic brief for self-check.\n", encoding="utf-8")
        ctrl = round_dir / "control.md"
        treat = round_dir / "treatment.md"
        ctrl.write_text("# Plan One\nRun unit tests.\n", encoding="utf-8")
        treat.write_text("# Plan Two\nRun unit tests and check rollback.\n", encoding="utf-8")

        rc = cmd_anonymize(
            argparse.Namespace(round_dir=round_dir, control_file=ctrl, treatment_file=treat, seed=42, strip_leaks=False)
        )
        if rc != 0:
            raise ValueError("self-check anonymize failed")
        rc = cmd_judge_packet(argparse.Namespace(round_dir=round_dir, judges=2))
        if rc != 0:
            raise ValueError("self-check judge-packet failed")
        packets = json.loads((round_dir / "judge-packets.json").read_text(encoding="utf-8"))
        responses = [
            {
                "request_id": req["request_id"],
                "artifacts_sha256": packets["artifacts_sha256"],
                "axis_a_winner": "X",
                "axis_b_winner": "X",
                "overall_winner": "X",
                "candidate_x_defects": [],
                "candidate_y_defects": [{"axis": "rigor", "severity": "minor", "locator": "L2", "issue": "missing rollback"}],
                "concrete_skill_gaps": [],
                "unchecked": [],
            }
            for req in packets["requests"]
        ]
        resp_file = round_dir / "responses.json"
        resp_file.write_text(json.dumps(responses), encoding="utf-8")
        rc = cmd_unblind_score(argparse.Namespace(round_dir=round_dir, responses_file=resp_file))
        if rc != 0:
            raise ValueError("self-check unblind-score failed")

        s_before = tmp_path / "skill_before"
        s_after = tmp_path / "skill_after"
        s_before.mkdir()
        s_after.mkdir()
        (s_before / "SKILL.md").write_text("---\nname: demo-skill\ndescription: Demo.\n---\n# Demo\nOld verbose prose.\n", encoding="utf-8")
        (s_after / "SKILL.md").write_text("---\nname: demo-skill\ndescription: Demo.\n---\n# Demo\nTighter rule.\n", encoding="utf-8")
        rc = cmd_edit_gate(
            argparse.Namespace(
                before_skill_dir=s_before,
                after_skill_dir=s_after,
                diff_file=None,
                regression_test="",
                tightened_clause="",
                rationale="General tightening.",
                max_bytes=8192,
            )
        )
        if rc != 0:
            raise ValueError("self-check edit-gate failed")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="skill_spar.py", description="Blinded A/B skill sparring helper.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init")
    p_init.add_argument("--work-dir", type=Path, required=True)
    p_init.add_argument("--stage", required=True)
    p_init.add_argument("--skill-dir", type=Path, required=True)
    p_init.add_argument("--brief", type=Path, required=True)
    p_init.add_argument("--harness", required=True)
    p_init.add_argument("--round-id")

    p_anon = sub.add_parser("anonymize")
    p_anon.add_argument("--round-dir", type=Path, required=True)
    p_anon.add_argument("--control-file", type=Path, required=True)
    p_anon.add_argument("--treatment-file", type=Path, required=True)
    p_anon.add_argument("--seed", type=int)
    p_anon.add_argument("--strip-leaks", action="store_true")

    p_jp = sub.add_parser("judge-packet")
    p_jp.add_argument("--round-dir", type=Path, required=True)
    p_jp.add_argument("--judges", type=int)

    p_un = sub.add_parser("unblind-score")
    p_un.add_argument("--round-dir", type=Path, required=True)
    p_un.add_argument("--responses-file", type=Path, required=True)

    p_eg = sub.add_parser("edit-gate")
    p_eg.add_argument("--before-skill-dir", type=Path, required=True)
    p_eg.add_argument("--after-skill-dir", type=Path, required=True)
    p_eg.add_argument("--diff-file", type=Path)
    p_eg.add_argument("--regression-test", default="")
    p_eg.add_argument("--tightened-clause", default="")
    p_eg.add_argument("--rationale", default="")
    p_eg.add_argument("--max-bytes", type=int)

    p_ch = sub.add_parser("chain-advance")
    p_ch.add_argument("--work-dir", type=Path, required=True)
    p_ch.add_argument("--stage", required=True)
    p_ch.add_argument("--next-stage", required=True)
    p_ch.add_argument("--selected-arm", required=True, choices=("control", "treatment", "synthesized"))
    p_ch.add_argument("--promoted-artifact", type=Path, required=True)
    p_ch.add_argument("--proposal-id")

    sub.add_parser("self-check")

    args = parser.parse_args(argv)
    dispatch = {
        "init": cmd_init,
        "anonymize": cmd_anonymize,
        "judge-packet": cmd_judge_packet,
        "unblind-score": cmd_unblind_score,
        "edit-gate": cmd_edit_gate,
        "chain-advance": cmd_chain_advance,
        "self-check": cmd_self_check,
    }
    try:
        return dispatch[args.cmd](args)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
