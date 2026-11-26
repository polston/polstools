"""Cross-harness draft-quality evaluator for `/goal` contracts.

Evaluates candidate `/goal` contracts for Claude Code, Codex, and Antigravity
against the shared contract and rules in `skills/writing-goals/SKILL.md` and its
per-harness adapters. Uses only the Python standard library and integrates with
`retro_eval.judge.ModelJudge` and `retro_eval.catalog.load_rubric_catalogue`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re

from .catalog import load_rubric_catalogue
from .judge import JudgeCase, JudgeExecution, ModelJudge


RUBRIC_ID = "goal_contract_quality"
SUPPORTED_HARNESSES = frozenset({"claude", "codex", "antigravity"})
CLAUDE_MAX_WORDS = 220

BRITTLE_TOOL_PATTERNS = (
    r"\binvoke_subagent\b",
    r"\bArtifactMetadata\b",
    r"\bupdate_steps\b",
    r"\bmanage_task\b",
    r"\bNotificationTimeoutSeconds\b",
)
HARDCODED_MODEL_RE = re.compile(
    r"\b(?:claude-[0-9][\w.-]*|gpt-[0-9][\w.-]*|gemini-[0-9][\w.-]*|o[134]-mini)\b",
    re.IGNORECASE,
)
BOUND_RE = re.compile(
    r"\b\d+\s*(?:turns?|minutes?|mins?|hours?|hrs?)\b",
    re.IGNORECASE,
)
INVENTED_PHRASE_RE = re.compile(
    r"(?:echo|print|output|emit)\s+['\"]?(?:GOAL_DONE|ALL_PASS|DONE_TOKEN|GOAL_COMPLETE|SUCCESS_TOKEN)['\"]?",
    re.IGNORECASE,
)
VIBE_ONLY_RE = re.compile(
    r"\b(?:looks clean|works properly|looks good|all good|feels right|better|robust|cleaned up)\b",
    re.IGNORECASE,
)
CONCRETE_EVIDENCE_RE = re.compile(
    r"(?:`[^`]+`|\bexit(?:s|ed)?\s*(?:code\s*)?0\b|\b0\s+failed\b|"
    r"\b(?:pytest|unittest|cargo\s+test|npm\s+test|go\s+test|make\s+test|p_validate\.py)\b|"
    r"\b(?:cmp|diff|wc\s+-[lc]|grep|ls|sha256)\b)",
    re.IGNORECASE,
)
BOUND_AS_COMPLETION_RE = re.compile(
    r"(?:bound|limit|budget)\s+(?:is\s+)?reached[^.\n]{0,60}\b(?:mark(?:ed)?\s+complete|counts?\s+as\s+(?:complete|done)|treat(?:ed)?\s+as\s+(?:complete|done))",
    re.IGNORECASE,
)
BOUND_NEGATION_RE = re.compile(
    r"(?:does\s+not\s+(?:mark|make|mean)|without\s+(?:claiming|marking)|not\s+(?:mark|count)\s+(?:it\s+)?complete|incomplete)",
    re.IGNORECASE,
)
MULTI_FINISH_RE = re.compile(
    r"\b(?:and\s+also\s+(?:cut\s+a\s+release|rewrite|migrate|refactor\s+unrelated)|"
    r"plus\s+unrelated|in\s+addition,\s+independently)\b",
    re.IGNORECASE,
)
BLANKET_HEAVY_DELEGATION_RE = re.compile(
    r"\b(?:strongest\s+model\s+for\s+every|largest\s+model\s+for\s+all|"
    r"delegate\s+every\s+(?:single\s+)?lookup|most\s+expensive\s+model\s+for\s+every)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GoalEvalScenario:
    scenario_id: str
    harness: str
    contract_text: str
    has_prior_baseline: bool = False
    has_standing_release_rule: bool = False
    touches_host_state: bool = False
    has_open_decision: bool = False
    uses_subagents: bool = False
    expected_label: str = "complete_contract"
    expected_defects: tuple[str, ...] = ()

    def to_evidence_json(self) -> str:
        payload = {
            "scenario_id": self.scenario_id,
            "harness": self.harness,
            "has_prior_baseline": self.has_prior_baseline,
            "has_standing_release_rule": self.has_standing_release_rule,
            "touches_host_state": self.touches_host_state,
            "has_open_decision": self.has_open_decision,
            "uses_subagents": self.uses_subagents,
            "contract_text": self.contract_text,
        }
        return json.dumps(payload, sort_keys=True)


@dataclass(frozen=True)
class ContractFinding:
    code: str
    label: str
    detail: str


@dataclass(frozen=True)
class ContractEvaluation:
    scenario_id: str
    harness: str
    label: str
    passed: bool
    word_count: int
    defects: tuple[ContractFinding, ...]

    def to_dict(self) -> dict:
        return {
            "scenario_id": self.scenario_id,
            "harness": self.harness,
            "label": self.label,
            "passed": self.passed,
            "word_count": self.word_count,
            "defects": [asdict(item) for item in self.defects],
        }


def _extract_section(text: str, heading_pattern: str) -> str:
    match = re.search(
        rf"(?im)^(?:#{{1,4}}\s*)?(?:{heading_pattern})\b[:\s]*$(.*?)(?=^(?:#{{1,4}}\s*[A-Z]|(?:OBJECTIVE|BASELINE|READ FIRST|ARTIFACTS?|CONSTRAINTS(?:\s*&\s*AUTHORITY)?|PROTECTED SCOPE|EVIDENCE|EXECUTION LOOP|EXITS|PARKED|BOUNDS|HANDOFF)\b[:\s]*$)|\Z)",
        text,
        re.DOTALL,
    )
    if match:
        return match.group(1).strip()
    inline = re.search(
        rf"(?im)^(?:{heading_pattern})\s*:\s*(.+?)(?=^(?:EVIDENCE|ARTIFACT|CONSTRAINTS|PARKED|BOUNDS)\s*:|\Z)",
        text,
        re.DOTALL,
    )
    return inline.group(1).strip() if inline else ""


def _has_slot(text: str, slot_pattern: str) -> bool:
    return bool(
        re.search(
            rf"(?im)^(?:#{{1,4}}\s*|\*\*)?(?:{slot_pattern})(?:\*\*)?(?:\s*:|\s*$)",
            text,
        )
    )


def evaluate_goal_contract(scenario: GoalEvalScenario) -> ContractEvaluation:
    """Deterministically evaluate a drafted `/goal` contract."""
    text = (scenario.contract_text or "").strip()
    words = len(re.findall(r"\S+", text))
    harness = (scenario.harness or "").strip().lower()
    findings: list[ContractFinding] = []

    if harness not in SUPPORTED_HARNESSES:
        findings.append(
            ContractFinding(
                code="invalid_harness",
                label="ambiguous",
                detail=f"unsupported target harness: {scenario.harness!r}",
            )
        )
        return ContractEvaluation(
            scenario_id=scenario.scenario_id,
            harness=harness,
            label="ambiguous",
            passed=False,
            word_count=words,
            defects=tuple(findings),
        )

    if not text:
        findings.append(
            ContractFinding(
                code="empty_contract",
                label="ambiguous",
                detail="contract text is empty",
            )
        )
        return ContractEvaluation(
            scenario_id=scenario.scenario_id,
            harness=harness,
            label="ambiguous",
            passed=False,
            word_count=0,
            defects=tuple(findings),
        )

    # 1. Harness neutrality: no brittle harness-internal tool names or model IDs.
    for pat in BRITTLE_TOOL_PATTERNS:
        m = re.search(pat, text)
        if m:
            findings.append(
                ContractFinding(
                    code="brittle_tool_name",
                    label="harness_coupled",
                    detail=f"contract hardcodes harness-internal tool name {m.group(0)!r}",
                )
            )
            break
    model_match = HARDCODED_MODEL_RE.search(text)
    if model_match:
        findings.append(
            ContractFinding(
                code="hardcoded_model_id",
                label="harness_coupled",
                detail=f"contract hardcodes vendor model ID {model_match.group(0)!r}",
            )
        )

    # 2. Adapter-specific shape checks.
    if harness == "claude":
        for slot in ("EVIDENCE", "CONSTRAINTS", "PARKED", "BOUNDS"):
            if not _has_slot(text, slot):
                findings.append(
                    ContractFinding(
                        code=f"missing_claude_slot_{slot.lower()}",
                        label="incomplete_exits" if slot in {"PARKED", "BOUNDS"} else "unverifiable_evidence",
                        detail=f"Claude Code contract is missing required {slot} slot",
                    )
                )
        if words > CLAUDE_MAX_WORDS:
            findings.append(
                ContractFinding(
                    code="oversized_claude_condition",
                    label="harness_coupled",
                    detail=f"Claude Code contract is {words} words (limit {CLAUDE_MAX_WORDS})",
                )
            )
    elif harness == "antigravity":
        required_agy = (
            ("OBJECTIVE", "multi_finish_line"),
            ("READ FIRST", "missing_authority_reconciliation"),
            ("ARTIFACTS", "unverifiable_evidence"),
            (r"CONSTRAINTS\s*&\s*AUTHORITY", "missing_authority_reconciliation"),
            ("EVIDENCE", "unverifiable_evidence"),
            ("EXECUTION LOOP", "incomplete_exits"),
            ("EXITS", "incomplete_exits"),
            ("HANDOFF", "incomplete_exits"),
        )
        for slot_pat, label in required_agy:
            if not _has_slot(text, slot_pat):
                slot_key = re.sub(r"[^a-z]+", "_", slot_pat.lower()).strip("_")
                findings.append(
                    ContractFinding(
                        code=f"missing_antigravity_slot_{slot_key}",
                        label=label,
                        detail=f"Antigravity contract is missing canonical slot matching {slot_pat}",
                    )
                )
        if scenario.has_prior_baseline and not _has_slot(text, "BASELINE"):
            findings.append(
                ContractFinding(
                    code="missing_baseline",
                    label="unverifiable_evidence",
                    detail="prior measurements exist but BASELINE slot is omitted",
                )
            )
    elif harness == "codex":
        codex_checks = (
            (r"READ FIRST|Read first|Sources?", "missing_authority_reconciliation", "missing_codex_read_first"),
            (r"PROTECTED SCOPE|Protected scope|CONSTRAINTS", "missing_authority_reconciliation", "missing_codex_scope"),
            (r"EVIDENCE|Validation", "unverifiable_evidence", "missing_codex_evidence"),
            (r"EXECUTION LOOP|Execution loop|Checkpoints?", "incomplete_exits", "missing_codex_loop"),
            (r"HANDOFF|Handoff", "incomplete_exits", "missing_codex_handoff"),
        )
        for pat, label, code in codex_checks:
            if not _has_slot(text, pat):
                findings.append(
                    ContractFinding(
                        code=code,
                        label=label,
                        detail=f"Codex objective is missing section matching {pat}",
                    )
                )
        if scenario.has_prior_baseline and not re.search(r"\bBaseline\b", text, re.IGNORECASE):
            findings.append(
                ContractFinding(
                    code="missing_baseline",
                    label="unverifiable_evidence",
                    detail="prior measurements exist but Baseline is omitted",
                )
            )

    # 3. Single finish line & open-decision phasing.
    objective_text = _extract_section(text, "OBJECTIVE|Objective") or text
    if MULTI_FINISH_RE.search(objective_text):
        findings.append(
            ContractFinding(
                code="monolithic_multi_finish_line",
                label="multi_finish_line",
                detail="objective combines independent finish lines instead of phasing",
            )
        )
    if scenario.has_open_decision:
        if not re.search(
            r"\b(?:open\s+decision|decision\s+gate|operator\s+decision|review\s+needed|parked\s+until)\b",
            text,
            re.IGNORECASE,
        ):
            findings.append(
                ContractFinding(
                    code="unphased_open_decision",
                    label="multi_finish_line",
                    detail="unresolved operator decision is not surfaced as an explicit gate or phase boundary",
                )
            )

    # 4. Runnable, countable evidence (no invented success phrase, no vibe-only check).
    evidence_text = _extract_section(text, "EVIDENCE|Evidence|Validation") or text
    if INVENTED_PHRASE_RE.search(text):
        findings.append(
            ContractFinding(
                code="invented_success_phrase",
                label="unverifiable_evidence",
                detail="evidence invents a wrapper or magic success phrase",
            )
        )
    # Strip backticked invented success phrases so a fake echo/print token
    # never counts as concrete runnable verification evidence.
    cleaned_evidence = re.sub(
        r"`[^`]*(?:GOAL_DONE|ALL_PASS|DONE_TOKEN|GOAL_COMPLETE|SUCCESS_TOKEN)[^`]*`",
        "",
        evidence_text,
        flags=re.IGNORECASE,
    )
    if not CONCRETE_EVIDENCE_RE.search(cleaned_evidence):
        code = "vibe_only_evidence" if VIBE_ONLY_RE.search(cleaned_evidence) else "unverifiable_evidence"
        findings.append(
            ContractFinding(
                code=code,
                label="unverifiable_evidence",
                detail="evidence lacks a concrete runnable command, exit status, or countable check",
            )
        )

    # 5. Authority reconciliation (repo standing rules & live host state).
    authority_text = (
        _extract_section(text, r"CONSTRAINTS(?:\s*&\s*AUTHORITY)?|Protected scope|PROTECTED SCOPE")
        or text
    )
    if scenario.has_standing_release_rule:
        if not re.search(
            r"\b(?:standing|override|no\s+(?:git\s+)?push|no\s+release|local\s+branch\s+only|release\s+authorized|auto-?release)\b",
            authority_text,
            re.IGNORECASE,
        ):
            findings.append(
                ContractFinding(
                    code="unreconciled_standing_rule",
                    label="missing_authority_reconciliation",
                    detail="repo has a standing push/release rule that the contract neither affirms nor overrides",
                )
            )
    if scenario.touches_host_state:
        has_host_boundary = bool(
            re.search(
                r"\b(?:host\s+state|outside\s+(?:git|the\s+repo)|installed\s+binar|user-scope|backup|pre-mutation|verify\s+before)\b",
                authority_text,
                re.IGNORECASE,
            )
        )
        if not has_host_boundary:
            findings.append(
                ContractFinding(
                    code="unguarded_host_mutation",
                    label="missing_authority_reconciliation",
                    detail="contract mutates live host state without explicit outside-git safety/verification guards",
                )
            )

    # 6. Bounds & exit states.
    if not BOUND_RE.search(text):
        findings.append(
            ContractFinding(
                code="missing_bound",
                label="unbounded",
                detail="contract omits a primary numeric turn or time bound",
            )
        )
    if BOUND_AS_COMPLETION_RE.search(text) and not BOUND_NEGATION_RE.search(text):
        findings.append(
            ContractFinding(
                code="bound_as_completion",
                label="unbounded",
                detail="contract treats reaching a turn/time bound as completion",
            )
        )
    if not re.search(r"\bresume\b", text, re.IGNORECASE):
        findings.append(
            ContractFinding(
                code="missing_resume_check",
                label="incomplete_exits",
                detail="blocked/parked exit omits an explicit resume check",
            )
        )
    if harness in {"codex", "antigravity"} and not re.search(r"review\s+needed", text, re.IGNORECASE):
        findings.append(
            ContractFinding(
                code="missing_review_gate",
                label="incomplete_exits",
                detail="contract does not distinguish Review needed from completion and blockers",
            )
        )

    # 7. Subagent delegation right-sizing.
    if scenario.uses_subagents:
        if BLANKET_HEAVY_DELEGATION_RE.search(text) or not re.search(
            r"\b(?:lightweight|fast\s+tier|cost|speed|reasoning\s+depth|right-?siz|read-only\s+(?:worker|scan|subagent))\b",
            text,
            re.IGNORECASE,
        ):
            findings.append(
                ContractFinding(
                    code="unrightsized_delegation",
                    label="unrightsized_delegation",
                    detail="delegation does not balance worker cost, speed, and reasoning depth",
                )
            )

    if not findings:
        return ContractEvaluation(
            scenario_id=scenario.scenario_id,
            harness=harness,
            label="complete_contract",
            passed=True,
            word_count=words,
            defects=(),
        )

    return ContractEvaluation(
        scenario_id=scenario.scenario_id,
        harness=harness,
        label=findings[0].label,
        passed=False,
        word_count=words,
        defects=tuple(findings),
    )


class DeterministicGoalContractProvider:
    """Offline `ModelJudge`-compatible provider backed by `evaluate_goal_contract`."""

    provider_id = "deterministic_goal_contract_v1"
    local = True

    def judge(self, request: dict) -> dict:
        prompt = str(request.get("prompt") or "")
        marker = "EVIDENCE_JSON:"
        if marker not in prompt:
            return {
                "label": "ambiguous",
                "reason": "missing_structured_evidence_payload",
                "estimated_cost": 0.0,
            }
        raw_json = prompt.split(marker, 1)[1].strip()
        data = json.loads(raw_json)
        scenario = GoalEvalScenario(
            scenario_id=str(data.get("scenario_id") or "unknown"),
            harness=str(data.get("harness") or ""),
            contract_text=str(data.get("contract_text") or ""),
            has_prior_baseline=bool(data.get("has_prior_baseline")),
            has_standing_release_rule=bool(data.get("has_standing_release_rule")),
            touches_host_state=bool(data.get("touches_host_state")),
            has_open_decision=bool(data.get("has_open_decision")),
            uses_subagents=bool(data.get("uses_subagents")),
        )
        result = evaluate_goal_contract(scenario)
        reason = (
            "all_checks_passed"
            if result.passed
            else ",".join(item.code for item in result.defects)
        )
        return {
            "label": result.label,
            "reason": reason,
            "estimated_cost": 0.0,
        }


DEFAULT_RUBRICS_PATH = Path(__file__).resolve().parents[1] / "rubrics" / "rubrics.json"


def build_goal_contract_judge(
    *,
    provider=None,
    rubrics_path: Path | None = None,
    allow_private_external: bool = False,
) -> ModelJudge:
    """Build a `ModelJudge` wired to the official `goal_contract_quality` rubric."""
    catalogue = load_rubric_catalogue(rubrics_path or DEFAULT_RUBRICS_PATH)
    rubric = {item.id: item for item in catalogue.rubrics}[RUBRIC_ID]
    active_provider = provider if provider is not None else DeterministicGoalContractProvider()
    return ModelJudge(
        rubric_id=rubric.id,
        rubric_version=rubric.version,
        labels=rubric.labels,
        prompt_template="Evaluate rubric {rubric_id}.\nEVIDENCE_JSON:\n{evidence}",
        provider=active_provider,
        model_config={"evaluator": active_provider.provider_id, "rubric_version": rubric.version},
        allow_private_external=allow_private_external,
        abstain_label="ambiguous",
    )


def synthetic_goal_eval_suite() -> tuple[GoalEvalScenario, ...]:
    """Return 100% synthetic, neutral paired eval cases across all three harnesses."""
    agy_clean_phased = """\
### Open Decisions
- Option A (Recommended): Archive deprecated adapters; Option B: Wire them in Phase 2.

### OBJECTIVE
Fix bounded log checkpointing and host binary parity on a local feature branch without touching deprecated adapters deferred to Phase 2.

### BASELINE
- `acme-cli` on PATH is 1.4.0 while `~/.config/acme/bin/acme-cli` is 1.5.0; `~/.local/state/acme/events.wal` is 84 MB.

### READ FIRST
- `AGENTS.md`, `src/log_writer.py`, `tests/test_log_writer.py`

### ARTIFACTS
- Local branch `fix/wal-checkpoint`, updated `src/log_writer.py`, regression test in `tests/test_log_writer.py`, and `phase1_handoff.md`.

### CONSTRAINTS & AUTHORITY
- Local branch only: explicitly override the standing auto-release rule in `AGENTS.md` for Phase 1 (no `git push` or tagging).
- Live host state outside git: verify `~/.config/acme/bin/acme-cli --version` exits 0 before removing stale binaries on PATH or old backups.
- Use a lightweight read-only worker for broad call-site scanning; keep checkpoint design in the primary reasoning thread (balance cost, speed, and reasoning depth).

### EVIDENCE
1. `pytest tests/test_log_writer.py` exits 0 with 0 failed.
2. `which -a acme-cli` resolves to a single version matching `1.5.0`.

### EXECUTION LOOP
- Commit locally after each verified fix and audit background tasks clean before exit.

### EXITS
- Review needed: stop if compacting live state logs requires choosing an archive location.
- PARKED: record failing command, attempted remedies, missing authority, and exact resume check command.
- BOUNDS (proposed default): 20 turns or 30 minutes; reaching the bound stops without claiming completion.

### HANDOFF
- Write `phase1_handoff.md` with commit list, evidence outputs, before/after sizes, and open Phase 2 decisions.
"""

    agy_brittle_and_unreconciled = """\
### OBJECTIVE
Fix bounded log checkpointing and also cut a release for unrelated docs in one run.

### READ FIRST
- `src/log_writer.py`

### ARTIFACTS
- Updated `src/log_writer.py`.

### CONSTRAINTS & AUTHORITY
- Use `invoke_subagent` with `gemini-2.5-pro` and `ArtifactMetadata` for every single lookup; use the strongest model for every scan.

### EVIDENCE
- Everything looks clean and works properly; run `echo GOAL_DONE`.

### EXECUTION LOOP
- Work until done.

### EXITS
- BOUNDS: 15 turns; when the bound is reached, mark complete.

### HANDOFF
- Done.
"""

    claude_clean = """\
EVIDENCE: `pytest tests/test_parser.py` exits 0 with 0 failed and `python3 scripts/check_schema.py` exits 0.
ARTIFACT: Updated `src/parser.py` and regression test `tests/test_parser.py` on branch `fix/parser-quote`.
CONSTRAINTS: Local commits only (override standing auto-release rule); do not weaken existing tests or edit unrelated modules.
PARKED: Record exact failing command, attempted remedies, missing authority, and exact resume check (`pytest tests/test_parser.py`).
BOUNDS: 12 turns or 20 minutes; reaching the bound stops incomplete without claiming done.
"""

    claude_oversized = (
        "EVIDENCE: `pytest tests/test_parser.py` exits 0.\n"
        "CONSTRAINTS: Keep tests passing.\n"
        "PARKED: Stop on failure with resume check.\n"
        "BOUNDS: 15 turns.\n"
        + ("Detailed background prose repeating repository history and extra narration. " * 30)
    )

    claude_missing_bound_and_vibe = """\
EVIDENCE: Code looks clean and feels right after refactoring.
CONSTRAINTS: Keep existing behavior.
PARKED: Stop if blocked.
"""

    codex_clean = """\
## Objective
Establish an initial test harness and implement input validation for the greenfield `sample-lib` CLI.

## Baseline
- Repository currently contains `src/cli.py` with 0 automated tests.

## Read first
- `AGENTS.md`, `docs/design.md`, `src/cli.py`

## Protected scope
- Local commits only; never weaken assertions once tests exist.
- Delegate read-only schema checks to a lightweight fast-tier worker and reserve deeper reasoning for edge-case validation.

## Evidence
- `python3 -m unittest -v tests/test_cli.py` exits 0 with 0 failed.

## Execution loop
- Checkpoint after adding the test harness, then after implementing validation; record short progress notes.

## Exits
- Review needed: stop if `docs/design.md` leaves an exit-code contract ambiguous.
- Blocked: state exact error, attempted remedies, missing authority, and the exact resume check.
- Bound (proposed default): 20 turns or 35 minutes; reaching the bound does not mark the task complete.

## Handoff
- Summarize new test files, exact unittest summary, before/after test count, and unresolved items.
"""

    codex_missing_review_and_baseline = """\
## Objective
Add validation to `sample-lib`.

## Read first
- `src/cli.py`

## Protected scope
- Keep tests green.

## Evidence
- `python3 -m unittest tests/test_cli.py` exits 0.

## Execution loop
- Validate after edits.

## Exits
- Blocked: stop on error with exact resume check.
- Bound: 15 turns.

## Handoff
- Summarize results.
"""

    agy_host_unguarded = """\
### OBJECTIVE
Clean up stale installed binaries and user-scope skill shadows on the workstation.

### BASELINE
- `~/.local/bin/demo-tool` is 1.0.0; `~/.config/demo/bin/demo-tool` is 1.1.0.

### READ FIRST
- `AGENTS.md`, `scripts/install.sh`

### ARTIFACTS
- Clean workstation state and `cleanup_handoff.md`.

### CONSTRAINTS & AUTHORITY
- Keep changes minimal and avoid unrelated edits.

### EVIDENCE
- `which -a demo-tool` prints only `~/.config/demo/bin/demo-tool` and `demo-tool --version` exits 0.

### EXECUTION LOOP
- Verify after each step.

### EXITS
- Review needed: stop if permissions prevent removing a binary.
- PARKED: state failing command, remedies tried, missing authority, and exact resume check.
- BOUNDS: 10 turns or 15 minutes; reaching the bound does not mark complete.

### HANDOFF
- Report before/after `which -a demo-tool` output.
"""

    agy_unrightsized_subagents = """\
### OBJECTIVE
Audit module call sites and patch the logging writer bug on a local branch.

### READ FIRST
- `AGENTS.md`, `src/logger.py`

### ARTIFACTS
- Patched `src/logger.py` and test in `tests/test_logger.py`.

### CONSTRAINTS & AUTHORITY
- Local commits only; delegate every single lookup and always use the strongest model for every subtask.

### EVIDENCE
- `pytest tests/test_logger.py` exits 0 with 0 failed.

### EXECUTION LOOP
- Run tests after each edit.

### EXITS
- Review needed: stop if a public signature must change.
- PARKED: state failure, remedies, missing state, and resume check.
- BOUNDS: 15 turns or 25 minutes; reaching the bound is not complete.

### HANDOFF
- Record test output and diff summary.
"""

    return (
        GoalEvalScenario(
            scenario_id="agy_clean_phased_with_baseline_and_host_guards",
            harness="antigravity",
            contract_text=agy_clean_phased,
            has_prior_baseline=True,
            has_standing_release_rule=True,
            touches_host_state=True,
            has_open_decision=True,
            uses_subagents=True,
            expected_label="complete_contract",
            expected_defects=(),
        ),
        GoalEvalScenario(
            scenario_id="agy_monolithic_brittle_and_vibe_defective",
            harness="antigravity",
            contract_text=agy_brittle_and_unreconciled,
            has_prior_baseline=True,
            has_standing_release_rule=True,
            touches_host_state=True,
            has_open_decision=True,
            uses_subagents=True,
            expected_label="harness_coupled",
            expected_defects=(
                "brittle_tool_name",
                "hardcoded_model_id",
                "missing_baseline",
                "monolithic_multi_finish_line",
                "unphased_open_decision",
                "invented_success_phrase",
                "vibe_only_evidence",
                "unreconciled_standing_rule",
                "unguarded_host_mutation",
                "bound_as_completion",
                "missing_resume_check",
                "missing_review_gate",
                "unrightsized_delegation",
            ),
        ),
        GoalEvalScenario(
            scenario_id="claude_clean_compressed_contract",
            harness="claude",
            contract_text=claude_clean,
            has_standing_release_rule=True,
            expected_label="complete_contract",
            expected_defects=(),
        ),
        GoalEvalScenario(
            scenario_id="claude_oversized_condition",
            harness="claude",
            contract_text=claude_oversized,
            expected_label="harness_coupled",
            expected_defects=("oversized_claude_condition",),
        ),
        GoalEvalScenario(
            scenario_id="claude_vibe_evidence_unbounded_missing_resume",
            harness="claude",
            contract_text=claude_missing_bound_and_vibe,
            expected_label="incomplete_exits",
            expected_defects=(
                "missing_claude_slot_bounds",
                "vibe_only_evidence",
                "missing_bound",
                "missing_resume_check",
            ),
        ),
        GoalEvalScenario(
            scenario_id="codex_clean_greenfield_with_delegation",
            harness="codex",
            contract_text=codex_clean,
            has_prior_baseline=True,
            uses_subagents=True,
            expected_label="complete_contract",
            expected_defects=(),
        ),
        GoalEvalScenario(
            scenario_id="codex_missing_baseline_and_review_gate",
            harness="codex",
            contract_text=codex_missing_review_and_baseline,
            has_prior_baseline=True,
            expected_label="unverifiable_evidence",
            expected_defects=("missing_baseline", "missing_review_gate"),
        ),
        GoalEvalScenario(
            scenario_id="agy_unguarded_host_mutation",
            harness="antigravity",
            contract_text=agy_host_unguarded,
            has_prior_baseline=True,
            touches_host_state=True,
            expected_label="missing_authority_reconciliation",
            expected_defects=("unguarded_host_mutation",),
        ),
        GoalEvalScenario(
            scenario_id="agy_unrightsized_subagents",
            harness="antigravity",
            contract_text=agy_unrightsized_subagents,
            uses_subagents=True,
            expected_label="unrightsized_delegation",
            expected_defects=("unrightsized_delegation",),
        ),
    )


def run_goal_eval_suite(
    scenarios: tuple[GoalEvalScenario, ...] | list[GoalEvalScenario] | None = None,
    *,
    judge: ModelJudge | None = None,
) -> dict:
    """Run synthetic scenarios through both deterministic and `ModelJudge` paths."""
    active_scenarios = tuple(scenarios) if scenarios is not None else synthetic_goal_eval_suite()
    active_judge = judge if judge is not None else build_goal_contract_judge()

    results = []
    mismatches = []
    for scenario in active_scenarios:
        direct = evaluate_goal_contract(scenario)
        case = JudgeCase(
            case_id=scenario.scenario_id,
            evidence_ref=f"synthetic://goal_contracts/{scenario.scenario_id}",
            evidence=scenario.to_evidence_json(),
            data_classification="public_synthetic",
        )
        judged: JudgeExecution = active_judge.evaluate(case)
        observed_codes = tuple(item.code for item in direct.defects)
        label_ok = (
            direct.label == scenario.expected_label
            and judged.label == scenario.expected_label
        )
        defects_ok = all(code in observed_codes for code in scenario.expected_defects)
        if scenario.expected_label == "complete_contract":
            defects_ok = defects_ok and not observed_codes
        case_ok = label_ok and defects_ok
        entry = {
            "scenario_id": scenario.scenario_id,
            "harness": scenario.harness,
            "expected_label": scenario.expected_label,
            "direct_label": direct.label,
            "judge_label": judged.label,
            "observed_defects": list(observed_codes),
            "expected_defects": list(scenario.expected_defects),
            "matched": case_ok,
        }
        results.append(entry)
        if not case_ok:
            mismatches.append(entry)

    return {
        "rubric_id": RUBRIC_ID,
        "total": len(results),
        "matched": len(results) - len(mismatches),
        "mismatched": len(mismatches),
        "results": results,
        "mismatches": mismatches,
    }
