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
import shutil
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
_RESERVED_CONTEXT_NAMES = {
    "control.md",
    "treatment.md",
    "brief.md",
    "candidate-x.md",
    "candidate-y.md",
}


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


def _validate_context_source(raw_path: Path, round_dir: Path) -> tuple[Path, bytes, str]:
    raw_base = Path(raw_path).name
    if raw_base.lower() == "sealed-key.json":
        raise ValueError("refusing to stage sealed-key.json as a context file")
    resolved = raw_path.resolve()
    if resolved.name.lower() == "sealed-key.json":
        raise ValueError("refusing to stage sealed-key.json as a context file")
    if resolved == (round_dir / "sealed-key.json").resolve():
        raise ValueError("refusing to stage round sealed-key.json as a context file")
    if not resolved.is_file():
        raise FileNotFoundError(f"context file not found or not a regular file: {raw_path}")
    data = resolved.read_bytes()
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"context file is not valid UTF-8 text: {raw_path}") from exc
    return resolved, data, _sha256_bytes(data)


def _safe_context_name(raw_name: str, used_lower: set[str], neutral_counter: list[int]) -> str:
    base = Path(raw_name).name
    if not base or base in (".", ".."):
        base = "context.md"
    if base.lower() in _RESERVED_CONTEXT_NAMES:
        suffix = Path(base).suffix or ".md"
        while True:
            neutral_counter[0] += 1
            candidate = f"context-{neutral_counter[0]}{suffix}"
            if candidate.lower() not in used_lower:
                used_lower.add(candidate.lower())
                return candidate
    if base.lower() not in used_lower:
        used_lower.add(base.lower())
        return base
    idx = 2
    while True:
        candidate = f"{idx:02d}-{base}"
        if candidate.lower() not in used_lower:
            used_lower.add(candidate.lower())
            return candidate
        idx += 1


def _stage_context_files(
    sources: list[Path],
    dest_dir: Path,
    round_dir: Path,
    *,
    clear_dest: bool = False,
    existing_records: list[dict] | None = None,
) -> list[dict]:
    records: list[dict] = []
    seen_paths: set[Path] = set()
    seen_shas: set[str] = set()
    used_lower: set[str] = set()
    neutral_counter = [0]

    if clear_dest and dest_dir.is_dir():
        shutil.rmtree(dest_dir)

    for rec in existing_records or []:
        rec_name = rec["name"]
        rec_path = Path(rec["path"]).resolve()
        rec_sha = rec["sha256"]
        records.append({"name": rec_name, "path": rec_path.as_posix(), "sha256": rec_sha})
        seen_paths.add(rec_path)
        seen_shas.add(rec_sha)
        used_lower.add(rec_name.lower())

    pending: list[tuple[str, bytes, str]] = []
    for raw in sources:
        resolved, data, sha = _validate_context_source(raw, round_dir)
        if resolved in seen_paths or sha in seen_shas:
            continue
        safe_name = _safe_context_name(raw.name, used_lower, neutral_counter)
        seen_paths.add(resolved)
        seen_shas.add(sha)
        pending.append((safe_name, data, sha))

    if pending:
        dest_dir.mkdir(parents=True, exist_ok=True)
        for safe_name, data, sha in pending:
            target = (dest_dir / safe_name).resolve()
            target.write_bytes(data)
            records.append({"name": safe_name, "path": target.as_posix(), "sha256": sha})
    return records


def _verify_blinded_context_files(key_data: dict, blinded_dir: Path) -> list[dict]:
    verified: list[dict] = []
    for rec in key_data.get("context_files") or []:
        name = rec["name"]
        expected_sha = rec["sha256"]
        target = (blinded_dir / "context" / name).resolve()
        if not target.is_file():
            raise FileNotFoundError(f"blinded context file missing: {target}")
        actual_sha = _sha256_bytes(target.read_bytes())
        if actual_sha != expected_sha:
            raise ValueError(
                f"blinded context file tampered ({name}): expected {expected_sha}, got {actual_sha}"
            )
        verified.append({"name": name, "path": target.as_posix(), "sha256": actual_sha})
    return verified


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

    context_sources = list(getattr(args, "context_file", None) or [])
    context_records = _stage_context_files(
        context_sources,
        round_dir / "context",
        round_dir,
        clear_dest=True,
    )
    context_block = ""
    if context_records:
        lines = "\n".join(f"- {rec['name']}: {rec['path']}" for rec in context_records)
        context_block = f"CONTEXT_FILES:\n{lines}\n\n"

    iso = contract["worker_isolation"]
    control_prompt = (
        f"{iso['control_preamble']}\n{iso['leak_ban']}\n\n"
        f"OUTPUT_PATH: {round_dir / 'control.md'}\n\n"
        f"{context_block}"
        f"SHARED_BRIEF:\n{brief_text}"
    )
    treatment_prompt = (
        f"{iso['treatment_preamble'].format(skill_path=skill_md)}\n{iso['leak_ban']}\n\n"
        f"OUTPUT_PATH: {round_dir / 'treatment.md'}\n\n"
        f"{context_block}"
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
    if context_records:
        meta["context_files"] = context_records
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

    # Auto-inherit round.json context files plus any CLI --context-file inputs
    inherited_sources: list[Path] = []
    round_meta_path = round_dir / "round.json"
    if round_meta_path.is_file():
        round_meta = json.loads(round_meta_path.read_text(encoding="utf-8"))
        for rec in round_meta.get("context_files") or []:
            inherited_sources.append(Path(rec["path"]))
    cli_sources = list(getattr(args, "context_file", None) or [])
    context_records = _stage_context_files(
        inherited_sources + cli_sources,
        blinded_dir / "context",
        round_dir,
        clear_dest=True,
    )

    x_sha = _sha256_bytes(x_txt.encode("utf-8"))
    y_sha = _sha256_bytes(y_txt.encode("utf-8"))
    preimage = f"X:{x_sha}\nY:{y_sha}"
    if context_records:
        ctx_lines = "\n".join(
            f"C:{rec['name']}:{rec['sha256']}"
            for rec in sorted(context_records, key=lambda r: r["name"])
        )
        preimage = f"{preimage}\n{ctx_lines}"
    artifacts_sha = _sha256_bytes(preimage.encode("utf-8"))

    key_data = {
        "mapping": mapping,
        "candidate_x_sha256": x_sha,
        "candidate_y_sha256": y_sha,
        "artifacts_sha256": artifacts_sha,
        "blinding_sanitized": sanitized,
        "leak_warnings": {"control": control_leaks, "treatment": treatment_leaks},
    }
    if context_records:
        key_data["context_files"] = context_records
    key_path = round_dir / "sealed-key.json"
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        fh = os.fdopen(fd, "w", encoding="utf-8")
    except Exception:
        os.close(fd)
        raise
    with fh:
        fh.write(json.dumps(key_data, indent=2) + "\n")
    if not hasattr(os, "fchmod"):
        try:
            os.chmod(key_path, 0o600)
        except OSError:
            pass

    out = {
        "status": "anonymized",
        "blinded_dir": str(blinded_dir),
        "candidate_x": str(x_path),
        "candidate_y": str(y_path),
        "artifacts_sha256": artifacts_sha,
        "blinding_sanitized": sanitized,
    }
    if context_records:
        out["context_files"] = context_records
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

    verified_context = _verify_blinded_context_files(key_data, blinded_dir)
    late_sources = list(getattr(args, "context_file", None) or [])
    all_context = _stage_context_files(
        late_sources,
        blinded_dir / "context",
        round_dir,
        clear_dest=False,
        existing_records=verified_context,
    )

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
            ctx_chunks = []
            for rec in all_context:
                c_txt = Path(rec["path"]).read_text(encoding="utf-8")
                ctx_chunks.append(f"=== CONTEXT: {rec['name']} ===\n{c_txt}\n\n")
            ctx_inline = "".join(ctx_chunks)
            body = (
                f"SHARED_BRIEF:\n{brief_txt}\n\n"
                f"{ctx_inline}"
                f"=== CANDIDATE X ===\n{x_txt}\n\n"
                f"=== CANDIDATE Y ===\n{y_txt}\n\n"
            )
        else:
            file_lines = [
                f"- Brief: {brief_path}",
                f"- Candidate X: {x_path}",
                f"- Candidate Y: {y_path}",
            ]
            for rec in all_context:
                file_lines.append(f"- Context ({rec['name']}): {rec['path']}")
            header = (
                "Read ONLY these listed files (do NOT list or read parent directories):"
                if all_context
                else "Read ONLY these three files (do NOT list or read parent directories):"
            )
            body = f"{header}\n" + "\n".join(file_lines) + "\n\n"
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
    if all_context:
        packet["context_files"] = all_context
    out_path = round_dir / "judge-packets.json"
    out_path.write_text(json.dumps(packet, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"judge_packets_file": str(out_path), "request_ids": [r["request_id"] for r in requests]}, indent=2))
    return 0


def _map_winner(label: str, mapping: dict) -> str:
    if label == "tie":
        return "tie"
    return mapping[label]


def _majority_consensus(votes: dict) -> str:
    if votes["treatment"] > votes["control"]:
        return "treatment"
    if votes["control"] > votes["treatment"]:
        return "control"
    return "tie"


def cmd_unblind_score(args) -> int:
    round_dir = _require_external_dir(args.round_dir)
    key_data = json.loads((round_dir / "sealed-key.json").read_text(encoding="utf-8"))
    packets = json.loads((round_dir / "judge-packets.json").read_text(encoding="utf-8"))
    responses = json.loads(args.responses_file.resolve().read_text(encoding="utf-8-sig"))
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

    consensus = _majority_consensus(overall_votes)
    axis_a_consensus = _majority_consensus(axis_a_votes)
    axis_b_consensus = _majority_consensus(axis_b_votes)
    axis_divergence = {axis_a_consensus, axis_b_consensus} == {"control", "treatment"}

    if overall_votes["control"] > 0 and overall_votes["treatment"] > 0:
        contested = True
        contested_reason = "split_overall_votes"
    elif (overall_votes["control"] > 0 or overall_votes["treatment"] > 0) and overall_votes["tie"] > 0:
        contested = True
        contested_reason = "mixed_tie_and_arm_votes"
    elif consensus == "tie":
        contested = True
        contested_reason = "unanimous_tie"
    else:
        contested = False
        contested_reason = None

    verdict = {
        "artifacts_sha256": expected_sha,
        "judges": len(unblinded),
        "consensus_winner": consensus,
        "contested": contested,
        "contested_reason": contested_reason,
        "axis_divergence": axis_divergence,
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
        work_dir = tmp_path / "work"
        brief = tmp_path / "brief.md"
        brief.write_text("Synthetic brief for self-check.\n", encoding="utf-8")
        promoted = tmp_path / "promoted-plan.md"
        promoted.write_text("# Promoted Plan\nVerify context handoff.\n", encoding="utf-8")

        rc = cmd_init(
            argparse.Namespace(
                work_dir=work_dir,
                stage="goal",
                skill_dir=PLUGIN_ROOT / "skills" / "writing-goals",
                brief=brief,
                harness="codex",
                round_id="round1",
                context_file=[promoted],
            )
        )
        if rc != 0:
            raise ValueError("self-check init failed")
        round_dir = work_dir / "round1"
        ctrl = round_dir / "control.md"
        treat = round_dir / "treatment.md"
        ctrl.write_text("# Goal One\nRun unit tests.\n", encoding="utf-8")
        treat.write_text("# Goal Two\nRun unit tests and check rollback.\n", encoding="utf-8")

        rc = cmd_anonymize(
            argparse.Namespace(
                round_dir=round_dir,
                control_file=ctrl,
                treatment_file=treat,
                seed=42,
                strip_leaks=False,
                context_file=[],
            )
        )
        if rc != 0 or not (round_dir / "blinded" / "context" / "promoted-plan.md").is_file():
            raise ValueError("self-check anonymize failed")
        rc = cmd_judge_packet(argparse.Namespace(round_dir=round_dir, judges=2, inline=False, context_file=[]))
        if rc != 0:
            raise ValueError("self-check judge-packet failed")
        packets = json.loads((round_dir / "judge-packets.json").read_text(encoding="utf-8"))
        responses = [
            {
                "request_id": req["request_id"],
                "artifacts_sha256": packets["artifacts_sha256"],
                "axis_a_winner": "X",
                "axis_b_winner": "Y",
                "overall_winner": "tie",
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
        verdict = json.loads((round_dir / "verdict.json").read_text(encoding="utf-8"))
        if not verdict.get("contested") or verdict.get("contested_reason") != "unanimous_tie" or not verdict.get("axis_divergence"):
            raise ValueError(f"self-check unexpected tie verdict: {verdict}")

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
    fmt = argparse.RawDescriptionHelpFormatter
    parser = argparse.ArgumentParser(
        prog="skill_spar.py",
        description="Blinded A/B skill sparring helper (contract-v1.json).",
        formatter_class=fmt,
        epilog=(
            "Typical chained workflow:\n"
            "  1. resolve-defaults --workspace-dir <repo> --task-summary '...'\n"
            "  2. init --work-dir <tmp> --stage plan --skill-dir <dir> --brief <brief> --harness <h> [--context-file <path>]\n"
            "  3. anonymize --round-dir <tmp>/<round> --control-file <c> --treatment-file <t> [--context-file <path>]\n"
            "  4. judge-packet --round-dir <tmp>/<round> [--judges 2] [--inline] [--context-file <path>]\n"
            "  5. unblind-score --round-dir <tmp>/<round> --responses-file <responses.json>\n"
            "  6. chain-advance --work-dir <tmp> --stage plan --next-stage goal --selected-arm <arm> --promoted-artifact <path>\n"
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_def = sub.add_parser(
        "resolve-defaults",
        help="Infer zero-argument sparring route from conversation and read-only git context.",
        formatter_class=fmt,
    )
    p_def.add_argument("--plugin-root", type=Path, help="Plugin root containing skills/ (default: auto-detected).")
    p_def.add_argument("--workspace-dir", type=Path, help="Workspace path to inspect read-only via git.")
    p_def.add_argument("--task-summary", help="Concise task summary inferred from conversation.")
    p_def.add_argument("--has-plan", action="store_true", help="Indicate an active plan exists in conversation.")
    p_def.add_argument("--plan-file", type=Path, help="Path to an existing plan file on disk.")
    p_def.add_argument("--target-skill", help="Specific skill name discussed or edited.")
    p_def.add_argument("--slug", help="Optional work-directory slug override.")

    p_init = sub.add_parser(
        "init",
        help="Initialize a sparring round and emit worker-packets.json.",
        formatter_class=fmt,
        epilog=(
            "Outputs: <round-dir>/round.json and <round-dir>/worker-packets.json.\n"
            "Pass --context-file (repeatable) to stage promoted upstream artifacts for workers and blinded judges."
        ),
    )
    p_init.add_argument("--work-dir", type=Path, required=True, help="External work directory outside every Git repo.")
    p_init.add_argument("--stage", required=True, help="Stage name from contract-v1.json (e.g., plan, goal, review, skill).")
    p_init.add_argument("--skill-dir", type=Path, required=True, help="Candidate skill directory containing SKILL.md.")
    p_init.add_argument("--brief", type=Path, required=True, help="Shared task brief markdown file.")
    p_init.add_argument("--harness", required=True, help="Active harness name (claude-code, codex, antigravity).")
    p_init.add_argument("--round-id", help="Optional explicit round directory name.")
    p_init.add_argument("--context-file", action="append", type=Path, default=[], help="Repeatable shared reference/promoted artifact to stage for workers and blinded judges.")

    p_anon = sub.add_parser(
        "anonymize",
        help="Scan for blinding leaks, randomize X/Y into blinded/, and seal key.",
        formatter_class=fmt,
        epilog=(
            "Outputs: <round-dir>/blinded/{brief.md,candidate-X.md,candidate-Y.md,context/*} and 0o600 sealed-key.json.\n"
            "Auto-inherits context_files from round.json; pass --context-file here for full artifacts_sha256 binding."
        ),
    )
    p_anon.add_argument("--round-dir", type=Path, required=True, help="Round directory created by init.")
    p_anon.add_argument("--control-file", type=Path, required=True, help="Control arm output markdown file.")
    p_anon.add_argument("--treatment-file", type=Path, required=True, help="Treatment arm output markdown file.")
    p_anon.add_argument("--seed", type=int, help="Optional deterministic RNG seed (testing only).")
    p_anon.add_argument("--strip-leaks", action="store_true", help="Redact blinding leak patterns instead of failing with exit 1.")
    p_anon.add_argument("--context-file", action="append", type=Path, default=[], help="Repeatable shared context artifact to copy into blinded/context/ and bind in artifacts_sha256.")

    p_jp = sub.add_parser(
        "judge-packet",
        help="Generate compact blinded judge prompts in judge-packets.json.",
        formatter_class=fmt,
        epilog=(
            "Outputs: <round-dir>/judge-packets.json with requests[{judge_index, request_id, prompt}].\n"
            "Verifies sealed blinded/context/ hashes; prefer passing --context-file at init or anonymize for full hash binding."
        ),
    )
    p_jp.add_argument("--round-dir", type=Path, required=True, help="Round directory containing sealed-key.json and blinded/.")
    p_jp.add_argument("--judges", type=int, help="Number of cold comparative judges (1-5; default from contract).")
    p_jp.add_argument("--inline", action="store_true", help="Embed brief, context files, and X/Y inline instead of blinded paths.")
    p_jp.add_argument("--context-file", action="append", type=Path, default=[], help="Optional late shared context file to stage into blinded/context/ without mutating sealed-key.json.")

    p_un = sub.add_parser(
        "unblind-score",
        help="Validate judge JSON array, unblind X/Y, and write verdict.json.",
        formatter_class=fmt,
        epilog=(
            "Input: --responses-file is a JSON array of judge response objects in judge-packets.json request order.\n"
            "Outputs: verdict.json with consensus_winner, contested, contested_reason, axis_divergence, votes, and defects_by_arm."
        ),
    )
    p_un.add_argument("--round-dir", type=Path, required=True, help="Round directory containing sealed-key.json and judge-packets.json.")
    p_un.add_argument("--responses-file", type=Path, required=True, help="JSON array of judge responses matching canonical request order.")

    p_eg = sub.add_parser(
        "edit-gate",
        help="Enforce per-Markdown 8 KB cap, tighten-first growth gate, and privacy checks.",
        formatter_class=fmt,
        epilog="Exit 0 when passed=true; exit 1 with violations[] when rejected.",
    )
    p_eg.add_argument("--before-skill-dir", type=Path, required=True, help="Baseline skill directory snapshot before edits.")
    p_eg.add_argument("--after-skill-dir", type=Path, required=True, help="Edited skill directory to validate.")
    p_eg.add_argument("--diff-file", type=Path, help="Unified git diff covering skill edits and regression test.")
    p_eg.add_argument("--regression-test", default="", help="Test file path verified as touched in --diff-file on net growth.")
    p_eg.add_argument("--tightened-clause", default="", help="Description of prose compressed or evicted first on net growth.")
    p_eg.add_argument("--rationale", default="", help="General failure class addressed by the edit.")
    p_eg.add_argument("--max-bytes", type=int, help="Optional per-Markdown byte ceiling override.")

    p_ch = sub.add_parser(
        "chain-advance",
        help="Record promoted winning artifact SHA-256 in chain-state.json (and pilot draft).",
        formatter_class=fmt,
        epilog="Outputs: <work-dir>/chain-state.json (and pilot-handoff-draft.json when --next-stage pilot-handoff).",
    )
    p_ch.add_argument("--work-dir", type=Path, required=True, help="External work directory outside every Git repo.")
    p_ch.add_argument("--stage", required=True, help="Completed stage name.")
    p_ch.add_argument("--next-stage", required=True, help="Next stage name from contract-v1.json.")
    p_ch.add_argument("--selected-arm", required=True, choices=("control", "treatment", "synthesized"), help="Selected winning arm.")
    p_ch.add_argument("--promoted-artifact", type=Path, required=True, help="Path to promoted artifact file.")
    p_ch.add_argument("--proposal-id", help="Optional custom proposal identity for pilot-handoff.")

    sub.add_parser("self-check", help="Run deterministic offline self-test of all sparring helper operations.", formatter_class=fmt)

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
