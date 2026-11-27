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
import subprocess
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

_SKILL_PATH_RE = re.compile(r"(?:^|/)skills/([a-z0-9-]+)/")
_PLAN_PATH_RE = re.compile(r"(?:^|/)[^/]*plan[^/]*\.md$", re.IGNORECASE)
_DEFAULT_BRANCHES = {"main", "master", "HEAD"}


def _load_contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def _require_external_dir(path: Path) -> Path:
    resolved = path.resolve()
    if _inside_repository(resolved):
        raise ValueError("sparring work directory must remain outside every Git repository")
    return resolved


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_ignored_cache_path(path: Path) -> bool:
    if "__pycache__" in path.parts:
        return True
    return path.suffix in (".pyc", ".pyo")


def _dir_total_bytes(root: Path) -> int:
    total = 0
    for item in sorted(root.rglob("*")):
        if item.is_file() and not _is_ignored_cache_path(item):
            total += item.stat().st_size
    return total


def _markdown_files(root: Path) -> list[Path]:
    return [
        item
        for item in sorted(root.rglob("*.md"))
        if item.is_file() and not _is_ignored_cache_path(item)
    ]


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


def _sanitize_slug(raw: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", raw.lower()).strip("-")
    return slug[:40] or "run"


def _suggest_external_work_dir(slug: str) -> str:
    candidate = Path(tempfile.gettempdir()) / f"skill-spar-{_sanitize_slug(slug)}"
    return str(_require_external_dir(candidate))


def _probe_git_workspace(workspace_dir: Path | None) -> dict:
    info = {
        "in_git_repo": False,
        "branch": None,
        "non_default_branch": False,
        "tracked_changes": [],
        "modified_skills": [],
        "modified_plans": [],
    }
    if workspace_dir is None or not workspace_dir.is_dir():
        return info
    check = subprocess.run(
        ["git", "-C", str(workspace_dir), "rev-parse", "--is-inside-work-tree"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if check.returncode != 0 or check.stdout.strip() != "true":
        return info
    info["in_git_repo"] = True

    branch_proc = subprocess.run(
        ["git", "-C", str(workspace_dir), "rev-parse", "--abbrev-ref", "HEAD"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if branch_proc.returncode == 0:
        branch = branch_proc.stdout.strip()
        if branch:
            info["branch"] = branch
            info["non_default_branch"] = branch not in _DEFAULT_BRANCHES

    diff_proc = subprocess.run(
        ["git", "-C", str(workspace_dir), "diff", "--name-only", "--diff-filter=ACMR", "-z", "HEAD"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if diff_proc.returncode != 0:
        diff_proc = subprocess.run(
            ["git", "-C", str(workspace_dir), "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    paths = []
    if diff_proc.returncode == 0 and diff_proc.stdout:
        paths = sorted(
            {
                raw.decode("utf-8", errors="surrogateescape")
                for raw in diff_proc.stdout.split(b"\x00")
                if raw
            }
        )
    info["tracked_changes"] = paths
    skills = sorted({m.group(1) for p in paths if (m := _SKILL_PATH_RE.search(p))})
    plans = sorted(p for p in paths if _PLAN_PATH_RE.search(p))
    info["modified_skills"] = skills
    info["modified_plans"] = plans
    return info


def _resolve_skill_dir(plugin_root: Path, skill_name: str) -> Path:
    skill_dir = (plugin_root / "skills" / skill_name).resolve()
    if not (skill_dir / "SKILL.md").is_file():
        raise FileNotFoundError(f"SKILL.md not found for skill {skill_name!r} under {plugin_root}")
    return skill_dir


def cmd_resolve_defaults(args) -> int:
    contract = _load_contract()
    cfg = contract["zero_arg_defaults"]
    plugin_root = (args.plugin_root or PLUGIN_ROOT).resolve()
    ws_info = _probe_git_workspace(args.workspace_dir.resolve() if args.workspace_dir else None)

    target_skill = (args.target_skill or "").strip() or None
    task_summary = (args.task_summary or "").strip() or None
    plan_file = args.plan_file.resolve() if args.plan_file else None
    if plan_file is not None and not plan_file.is_file():
        raise FileNotFoundError(f"plan file not found: {plan_file}")

    # Priority 1: Specific skill discussed or single skill edited in tracked diff
    if target_skill is None and len(ws_info["modified_skills"]) == 1:
        target_skill = ws_info["modified_skills"][0]
    elif target_skill is None and len(ws_info["modified_skills"]) > 1:
        skill_list = ", ".join(ws_info["modified_skills"])
        out = {
            "status": "needs_clarification",
            "route": "ambiguous_target_skills",
            "max_clarifying_questions": int(cfg["max_clarifying_questions"]),
            "question": f"Multiple skills have tracked edits ({skill_list}). Which single skill should I evaluate?",
            "workspace": ws_info,
        }
        print(json.dumps(out, indent=2))
        return 1

    if target_skill is not None:
        skill_dir = _resolve_skill_dir(plugin_root, target_skill)
        stage = cfg["skill_stage_map"].get(target_skill, cfg["fallback_skill_stage"])
        slug = args.slug or target_skill
        out = {
            "status": "resolved",
            "route": "target_skill_in_context",
            "mode": "single",
            "recursive_guard": target_skill == "sparring-skills",
            "steps": [{"stage": stage, "skill": target_skill, "skill_dir": str(skill_dir)}],
            "suggested_work_dir": _suggest_external_work_dir(slug),
            "approval_boundary": cfg["approval_boundary"],
            "workspace": ws_info,
        }
        print(json.dumps(out, indent=2))
        return 0

    # Priority 2: Existing plan in context (--plan-file, --has-plan, or single modified tracked plan)
    inferred_plan = None
    if plan_file is not None:
        inferred_plan = str(plan_file)
    elif not args.has_plan:
        if len(ws_info["modified_plans"]) == 1:
            inferred_plan = str((args.workspace_dir.resolve() / ws_info["modified_plans"][0]).resolve())
        elif len(ws_info["modified_plans"]) > 1 and not task_summary:
            plan_list = ", ".join(ws_info["modified_plans"])
            out = {
                "status": "needs_clarification",
                "route": "ambiguous_plan_files",
                "max_clarifying_questions": int(cfg["max_clarifying_questions"]),
                "question": f"Multiple tracked plan files are modified ({plan_list}). Which plan should I turn into a goal?",
                "workspace": ws_info,
            }
            print(json.dumps(out, indent=2))
            return 1

    if args.has_plan or inferred_plan is not None:
        step_cfg = cfg["existing_plan_stage"]
        skill_dir = _resolve_skill_dir(plugin_root, step_cfg["skill"])
        slug = args.slug or (Path(inferred_plan).stem if inferred_plan else "goal")
        out = {
            "status": "resolved",
            "route": "existing_plan_in_context",
            "mode": "single",
            "recursive_guard": False,
            "plan_file": inferred_plan,
            "steps": [{"stage": step_cfg["stage"], "skill": step_cfg["skill"], "skill_dir": str(skill_dir)}],
            "suggested_work_dir": _suggest_external_work_dir(slug),
            "approval_boundary": cfg["approval_boundary"],
            "workspace": ws_info,
        }
        print(json.dumps(out, indent=2))
        return 0

    # Priority 3: Identifiable task with no plan yet -> default chain
    has_task = bool(task_summary or ws_info["non_default_branch"] or ws_info["tracked_changes"])
    if has_task:
        steps = []
        for item in cfg["default_chain"]:
            s_dir = _resolve_skill_dir(plugin_root, item["skill"])
            steps.append({"stage": item["stage"], "skill": item["skill"], "skill_dir": str(s_dir)})
        slug = args.slug or (ws_info["branch"] if ws_info["non_default_branch"] else "chain")
        out = {
            "status": "resolved",
            "route": "no_plan_yet_default_chain",
            "mode": "chain",
            "recursive_guard": False,
            "steps": steps,
            "contested_chain_policy": cfg["contested_chain_policy"],
            "suggested_work_dir": _suggest_external_work_dir(slug),
            "approval_boundary": cfg["approval_boundary"],
            "workspace": ws_info,
        }
        print(json.dumps(out, indent=2))
        return 0

    # Priority 4: Zero identifiable context
    out = {
        "status": "needs_clarification",
        "route": "none",
        "max_clarifying_questions": int(cfg["max_clarifying_questions"]),
        "question": cfg["clarification_prompt"],
        "workspace": ws_info,
    }
    print(json.dumps(out, indent=2))
    return 1


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
        "skill_bytes": sum(p.stat().st_size for p in _markdown_files(skill_dir)),
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

    blinded_dir = round_dir / "blinded"
    blinded_dir.mkdir(parents=True, exist_ok=True)
    brief_src = round_dir / "brief.md"
    if brief_src.is_file():
        (blinded_dir / "brief.md").write_text(brief_src.read_text(encoding="utf-8"), encoding="utf-8")
    x_path = blinded_dir / "candidate-X.md"
    y_path = blinded_dir / "candidate-Y.md"
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
        "blinded_dir": str(blinded_dir),
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
    blinded_dir = round_dir / "blinded"
    if not blinded_dir.is_dir():
        blinded_dir = round_dir
    brief_path = blinded_dir / "brief.md"
    if not brief_path.is_file():
        brief_path = round_dir / "brief.md"
    x_path = blinded_dir / "candidate-X.md"
    y_path = blinded_dir / "candidate-Y.md"
    if not brief_path.is_file() or not x_path.is_file() or not y_path.is_file():
        raise FileNotFoundError("anonymized brief and candidate files not found")

    judges = args.judges if args.judges is not None else int(contract["defaults"]["judges"])
    if judges < 1 or judges > 5:
        raise ValueError("--judges must be between 1 and 5")

    schema_json = json.dumps(contract["judge_schema"], indent=2)
    inline_mode = bool(getattr(args, "inline", False))
    requests = []
    for idx in range(1, judges + 1):
        req_id = f"judge-{idx}-{secrets.token_hex(4)}"
        if inline_mode:
            brief_txt = brief_path.read_text(encoding="utf-8")
            x_txt = x_path.read_text(encoding="utf-8")
            y_txt = y_path.read_text(encoding="utf-8")
            body = (
                f"SHARED_BRIEF:\n{brief_txt}\n\n"
                f"=== CANDIDATE X ===\n{x_txt}\n\n"
                f"=== CANDIDATE Y ===\n{y_txt}\n\n"
            )
        else:
            body = (
                "Read ONLY these three files (do NOT list or read parent directories):\n"
                f"- Brief: {brief_path}\n"
                f"- Candidate X: {x_path}\n"
                f"- Candidate Y: {y_path}\n\n"
            )
        prompt = (
            f"{contract['judge_prompt']['preamble']}\n\n"
            f"REQUEST_ID: {req_id}\n"
            f"ARTIFACTS_SHA256: {key_data['artifacts_sha256']}\n\n"
            f"{body}"
            f"RESPONSE_SCHEMA (JSON only):\n{schema_json}"
        )
        requests.append({"judge_index": idx, "request_id": req_id, "prompt": prompt})

    packet = {
        "artifacts_sha256": key_data["artifacts_sha256"],
        "blinded_dir": str(blinded_dir),
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

    before_md = _markdown_files(before_dir)
    after_md = _markdown_files(after_dir)
    before_bytes = sum(p.stat().st_size for p in before_md)
    after_bytes = sum(p.stat().st_size for p in after_md)
    delta_bytes = after_bytes - before_bytes
    before_skill_md_bytes = (before_dir / "SKILL.md").stat().st_size
    after_skill_md_bytes = (after_dir / "SKILL.md").stat().st_size
    before_dir_bytes = _dir_total_bytes(before_dir)
    after_dir_bytes = _dir_total_bytes(after_dir)
    max_markdown_file_bytes = max((p.stat().st_size for p in after_md), default=0)
    max_bytes = args.max_bytes if args.max_bytes is not None else int(gate_cfg["max_skill_bytes"])

    diff_text = args.diff_file.resolve().read_text(encoding="utf-8") if args.diff_file else ""
    added_lines = [
        line[1:]
        for line in diff_text.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]
    if not added_lines:
        # Fallback: scan every markdown file under after_dir when no diff file supplied
        for md_path in after_md:
            added_lines.extend(md_path.read_text(encoding="utf-8").splitlines())

    violations = []

    # 1. Hard per-Markdown byte cap across SKILL.md and all auxiliary markdown files
    if gate_cfg.get("enforce_cap_per_markdown_file", True):
        for md_path in after_md:
            sz = md_path.stat().st_size
            if sz > max_bytes:
                rel = md_path.relative_to(after_dir).as_posix()
                if rel == "SKILL.md":
                    violations.append(
                        f"skill size {sz} B exceeds max_skill_bytes ceiling {max_bytes} B"
                    )
                else:
                    violations.append(
                        f"auxiliary markdown {rel} size {sz} B exceeds max_skill_bytes ceiling {max_bytes} B"
                    )
    elif after_bytes > max_bytes:
        violations.append(f"skill size {after_bytes} B exceeds max_skill_bytes ceiling {max_bytes} B")

    # 2. Delete/tighten-first + diff-verified regression test on ANY net Markdown byte growth
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
        "max_markdown_file_bytes": max_markdown_file_bytes,
        "before_skill_md_bytes": before_skill_md_bytes,
        "after_skill_md_bytes": after_skill_md_bytes,
        "before_dir_bytes": before_dir_bytes,
        "after_dir_bytes": after_dir_bytes,
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
    for key in (
        "contract_version",
        "defaults",
        "stages",
        "zero_arg_defaults",
        "worker_isolation",
        "blinding_leak_patterns",
        "judge_schema",
        "edit_gate",
    ):
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
        if rc != 0 or not (round_dir / "blinded" / "candidate-X.md").is_file():
            raise ValueError("self-check anonymize failed")
        rc = cmd_judge_packet(argparse.Namespace(round_dir=round_dir, judges=2, inline=False))
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

        # Verify resolve-defaults across all 4 routes
        rc_chain = cmd_resolve_defaults(
            argparse.Namespace(
                plugin_root=PLUGIN_ROOT,
                workspace_dir=tmp_path,
                task_summary="Implement parser refactor",
                has_plan=False,
                plan_file=None,
                target_skill=None,
                slug="check-chain",
            )
        )
        rc_plan = cmd_resolve_defaults(
            argparse.Namespace(
                plugin_root=PLUGIN_ROOT,
                workspace_dir=tmp_path,
                task_summary=None,
                has_plan=True,
                plan_file=None,
                target_skill=None,
                slug="check-plan",
            )
        )
        rc_skill = cmd_resolve_defaults(
            argparse.Namespace(
                plugin_root=PLUGIN_ROOT,
                workspace_dir=tmp_path,
                task_summary=None,
                has_plan=False,
                plan_file=None,
                target_skill="sparring-skills",
                slug="check-skill",
            )
        )
        rc_clarify = cmd_resolve_defaults(
            argparse.Namespace(
                plugin_root=PLUGIN_ROOT,
                workspace_dir=tmp_path,
                task_summary=None,
                has_plan=False,
                plan_file=None,
                target_skill=None,
                slug=None,
            )
        )
        if (rc_chain, rc_plan, rc_skill, rc_clarify) != (0, 0, 0, 1):
            raise ValueError(
                f"self-check resolve-defaults unexpected codes: {(rc_chain, rc_plan, rc_skill, rc_clarify)}"
            )

        # Verify edit-gate ignores scripts/*.py and __pycache__ while capping auxiliary .md files
        s_before = tmp_path / "skill_before"
        s_after = tmp_path / "skill_after"
        (s_before / "scripts").mkdir(parents=True)
        (s_after / "scripts" / "__pycache__").mkdir(parents=True)
        (s_after / "references").mkdir(parents=True)
        (s_before / "SKILL.md").write_text("---\nname: demo-skill\ndescription: Demo.\n---\n# Demo\nOld verbose prose.\n", encoding="utf-8")
        (s_after / "SKILL.md").write_text("---\nname: demo-skill\ndescription: Demo.\n---\n# Demo\nTighter rule.\n", encoding="utf-8")
        (s_after / "scripts" / "helper.py").write_text("# " + ("x" * 12000) + "\n", encoding="utf-8")
        (s_after / "scripts" / "__pycache__" / "helper.pyc").write_bytes(b"\x00" * 12000)
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
            raise ValueError("self-check edit-gate failed with large scripts/helper.py")
        (s_after / "references" / "oversized.md").write_text("x" * 9000 + "\n", encoding="utf-8")
        rc_over = cmd_edit_gate(
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
        if rc_over != 1:
            raise ValueError("self-check edit-gate failed to reject oversized auxiliary markdown")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="skill_spar.py", description="Blinded A/B skill sparring helper.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_def = sub.add_parser("resolve-defaults")
    p_def.add_argument("--plugin-root", type=Path)
    p_def.add_argument("--workspace-dir", type=Path)
    p_def.add_argument("--task-summary")
    p_def.add_argument("--has-plan", action="store_true")
    p_def.add_argument("--plan-file", type=Path)
    p_def.add_argument("--target-skill")
    p_def.add_argument("--slug")

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
    p_jp.add_argument("--inline", action="store_true")

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
        "resolve-defaults": cmd_resolve_defaults,
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
