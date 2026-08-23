"""Cross-harness extraction into content-free normalized traces."""

from __future__ import annotations

import json
import os
import argparse
import hashlib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .adapters.registry import AdapterRegistry, default_registry
from .instruction_manifest import load_instruction_manifest
from .storage import JsonlTraceStore


def _file_sha256(path: Path) -> bytes:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.digest()


def _inside_repository(path: Path) -> bool:
    resolved = path.resolve()
    return any((parent / ".git").exists() for parent in (resolved, *resolved.parents))


class WorkStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        if _inside_repository(self.root):
            raise ValueError("evaluation work directory must be outside a repository")

    def id_salt(self) -> bytes:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / "id-salt.bin"
        try:
            value = path.read_bytes()
        except OSError:
            value = os.urandom(32)
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(value)
        if len(value) != 32:
            raise ValueError("local id salt must be exactly 32 bytes")
        return value

    def public_metadata(self) -> dict[str, object]:
        return {"work_format": 1, "ids": "installation-scoped keyed hashes"}


@dataclass(frozen=True)
class ExtractionSummary:
    included_traces: int
    excluded_traces: int
    normalized_spans: int
    sources: dict[str, dict[str, object]]
    exclusion_reasons: dict[str, int]
    instruction_manifest_sha256: str = ""
    instruction_manifest_coverage: dict[str, int] | None = None

    def __post_init__(self):
        value = self.instruction_manifest_sha256
        if value and (len(value) != 64
                      or any(character not in "0123456789abcdef"
                             for character in value)):
            raise ValueError("instruction manifest must be lowercase SHA-256")
        coverage = self.instruction_manifest_coverage
        if coverage is not None:
            if not value:
                raise ValueError("instruction coverage requires a manifest")
            if (set(coverage) != {"population", "resolved", "unresolved"}
                    or any(int(item) < 0 for item in coverage.values())
                    or int(coverage["population"]) != (
                        int(coverage["resolved"]) + int(coverage["unresolved"]))):
                raise ValueError("invalid instruction manifest coverage")

    def to_public_dict(self) -> dict[str, object]:
        result = {
            "schema_version": (3 if self.instruction_manifest_coverage is not None
                               else 2 if self.instruction_manifest_sha256 else 1),
            "included_traces": self.included_traces,
            "excluded_traces": self.excluded_traces,
            "normalized_spans": self.normalized_spans,
            "sources": self.sources,
            "exclusion_reasons": self.exclusion_reasons,
        }
        if self.instruction_manifest_sha256:
            result["instruction_manifest_sha256"] = self.instruction_manifest_sha256
        if self.instruction_manifest_coverage is not None:
            result["instruction_manifest_coverage"] = dict(
                self.instruction_manifest_coverage)
        return result


class EvaluationPipeline:
    def __init__(self, work_dir: Path, *, registry: AdapterRegistry | None = None,
                 trace_store=None, instruction_manifest_path: Path | None = None):
        self.work = WorkStore(work_dir)
        self.registry = registry or default_registry()
        self.trace_store = trace_store or JsonlTraceStore(self.work.root / "traces.jsonl")
        self.instruction_manifest_sha256 = ""
        self.instruction_manifest = None
        if instruction_manifest_path is not None:
            self.instruction_manifest = load_instruction_manifest(
                instruction_manifest_path)
            self.instruction_manifest_sha256 = self.instruction_manifest.manifest_sha256

    def extract(self, *, roots: dict[str, Path] | None = None,
                claude_root: Path | None = None, codex_root: Path | None = None,
                exclude_session_ids=(), adapter_options=None) -> ExtractionSummary:
        salt = self.work.id_salt()
        roots = dict(roots or {})
        if claude_root is not None:
            roots["claude"] = claude_root
        if codex_root is not None:
            roots["codex"] = codex_root
        adapter_options = dict(adapter_options or {})
        if exclude_session_ids:
            adapter_options.setdefault("codex", {})["excluded_session_ids"] = exclude_session_ids
        all_records = []
        excluded = Counter()
        sources = {}
        included_total = excluded_total = 0
        for registration in self.registry:
            name = registration.name
            if name not in roots:
                continue
            root = roots[name]
            adapter = registration.create(salt, adapter_options.get(name))
            paths = sorted(registration.discover(root))
            source_hashes = []
            source_fingerprint_failures = 0
            included = excluded_count = prompts = tool_calls = tool_results = 0
            main = subagents = skill_traces = 0
            for path in paths:
                try:
                    source_hashes.append(_file_sha256(path))
                except OSError:
                    source_fingerprint_failures += 1
                result = adapter.read(path, root)
                if result.included:
                    included += 1
                    main += not result.is_subagent
                    subagents += result.is_subagent
                    prompts += result.human_prompt_count
                    tool_calls += result.tool_call_count
                    tool_results += result.tool_result_count
                    skill_traces += bool(result.skills)
                    all_records.extend(result.records)
                else:
                    excluded_count += 1
                    excluded[result.exclusion_reason or "unspecified"] += 1
            included_total += included
            excluded_total += excluded_count
            capabilities = {
                key: value.to_dict() for key, value in adapter.capabilities.items()
            }
            sources[name] = {
                "files": len(paths),
                "source_set_sha256": hashlib.sha256(
                    b"".join(sorted(source_hashes))).hexdigest(),
                "source_fingerprint_failures": source_fingerprint_failures,
                "included": included,
                "excluded": excluded_count,
                "main": main,
                "subagents": subagents,
                "human_prompts": prompts,
                "tool_calls": tool_calls,
                "tool_results": tool_results,
                "skill_attributed_traces": skill_traces,
                **capabilities,
            }

        instruction_coverage = None
        if self.instruction_manifest is not None:
            trace_times = {}
            for record in all_records:
                if record.started_at is None:
                    continue
                current = trace_times.get(record.trace_id)
                if current is None or record.started_at < current:
                    trace_times[record.trace_id] = record.started_at
            trace_ids = {record.trace_id for record in all_records}
            resolved = sum(
                trace_id in trace_times
                and trace_times[trace_id] >= self.instruction_manifest.activated_at
                for trace_id in trace_ids)
            instruction_coverage = {
                "population": len(trace_ids),
                "resolved": resolved,
                "unresolved": len(trace_ids) - resolved,
            }
            if instruction_coverage["unresolved"]:
                raise ValueError("evaluated sessions fall outside manifest coverage")

        self.trace_store.write(all_records)
        summary = ExtractionSummary(
            included_traces=included_total,
            excluded_traces=excluded_total,
            normalized_spans=len(all_records),
            sources=sources,
            exclusion_reasons=dict(sorted(excluded.items())),
            instruction_manifest_sha256=self.instruction_manifest_sha256,
            instruction_manifest_coverage=instruction_coverage,
        )
        (self.work.root / "extraction.json").write_text(
            json.dumps(summary.to_public_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        return summary


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--root", action="append", required=True,
                        help="registered source and root as name=path")
    parser.add_argument("--exclude-session-id", action="append", default=[])
    parser.add_argument("--instruction-manifest", type=Path)
    args = parser.parse_args(argv)
    roots = {}
    for value in args.root:
        try:
            name, path = value.split("=", 1)
        except ValueError as exc:
            raise ValueError("roots must use name=path") from exc
        roots[name] = Path(path)
    summary = EvaluationPipeline(
        args.work_dir,
        instruction_manifest_path=args.instruction_manifest).extract(
        roots=roots, exclude_session_ids=args.exclude_session_id)
    print(json.dumps(summary.to_public_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
