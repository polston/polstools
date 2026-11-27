#!/usr/bin/env python3
"""Compute and optionally apply automated semantic version bumps for plugin p.

Usage:
  release_bump.py [--repo PATH] [--base auto|REV] [--head REV]
                  [--bump auto|patch|minor|major] [--apply | --dry-run]
                  [--json] [--github-output PATH]

Exit codes:
  0  ran cleanly (whether a bump was applied/planned or skipped)
  2  invalid arguments, inconsistent manifests, duplicate changelog heading,
     or git failure
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


MANIFEST_PATHS = (
    ".claude-plugin/marketplace.json",
    "plugins/p/.claude-plugin/plugin.json",
    "plugins/p/.codex-plugin/plugin.json",
    "plugins/p/plugin.json",
)
PRIMARY_MANIFEST = "plugins/p/.claude-plugin/plugin.json"
CHANGELOG_PATH = "CHANGELOG.md"
SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
CONVENTIONAL_RE = re.compile(
    r"^(?P<type>[a-zA-Z]+)(?:\([^)]+\))?(?P<bang>!)?:\s*(?P<desc>.+)$"
)
BREAKING_FOOTER_RE = re.compile(r"(?m)^BREAKING[ -]CHANGE:")
TRAILING_P_TAG_RE = re.compile(
    r"\s*\(p\s+\d+\.\d+\.\d+\)(?=\s*(?:\(#\d+\))?\s*$)"
)
SKIP_MARKERS = ("[skip release]", "[skip ci]")
LOW_IMPACT_TYPES = frozenset({"docs", "test", "ci", "style"})
BUMP_RANK = {"none": 0, "patch": 1, "minor": 2, "major": 3}


class ReleaseBumpError(Exception):
    """Fail-closed error mapped to exit status 2."""


def run_git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or f"exit status {proc.returncode}"
        raise ReleaseBumpError(f"git {' '.join(args)} failed: {detail}")
    return proc.stdout


def extract_manifest_version(rel_path: str, payload: dict) -> str:
    if rel_path == ".claude-plugin/marketplace.json":
        plugins = payload.get("plugins")
        if not isinstance(plugins, list):
            raise ReleaseBumpError(f"{rel_path} is missing plugins list")
        matches = [entry for entry in plugins if isinstance(entry, dict) and entry.get("name") == "p"]
        if len(matches) != 1:
            raise ReleaseBumpError(f"{rel_path} must contain exactly one 'p' plugin entry")
        version = matches[0].get("version")
    else:
        version = payload.get("version")
    if not isinstance(version, str) or not SEMVER_RE.fullmatch(version):
        raise ReleaseBumpError(f"{rel_path} has invalid semver version: {version!r}")
    return version


def read_manifests(repo: Path) -> tuple[str, dict[str, str]]:
    versions: dict[str, str] = {}
    raw_texts: dict[str, str] = {}
    for rel_path in MANIFEST_PATHS:
        full_path = repo / rel_path
        try:
            text = full_path.read_text(encoding="utf-8")
            payload = json.loads(text)
        except (OSError, json.JSONDecodeError) as exc:
            raise ReleaseBumpError(f"cannot read {rel_path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ReleaseBumpError(f"{rel_path} must contain a JSON object")
        versions[rel_path] = extract_manifest_version(rel_path, payload)
        raw_texts[rel_path] = text
    unique = set(versions.values())
    if len(unique) != 1:
        summary = ", ".join(f"{path}={ver}" for path, ver in versions.items())
        raise ReleaseBumpError(f"manifest version mismatch: {summary}")
    return next(iter(unique)), raw_texts


def discover_base_sha(repo: Path, head: str) -> str:
    """Find the most recent commit where plugins/p/.claude-plugin/plugin.json changed version."""
    shas = [
        line.strip()
        for line in run_git(repo, "log", "--format=%H", head, "--", PRIMARY_MANIFEST).splitlines()
        if line.strip()
    ]
    if not shas:
        raise ReleaseBumpError(f"no git history found for {PRIMARY_MANIFEST} at {head}")
    latest_version = None
    last_same_sha = shas[0]
    for sha in shas:
        raw = run_git(repo, "show", f"{sha}:{PRIMARY_MANIFEST}")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ReleaseBumpError(f"invalid JSON in {sha}:{PRIMARY_MANIFEST}: {exc}") from exc
        ver = extract_manifest_version(PRIMARY_MANIFEST, payload)
        if latest_version is None:
            latest_version = ver
            last_same_sha = sha
        elif ver == latest_version:
            last_same_sha = sha
        else:
            return last_same_sha
    return last_same_sha


def list_commits(repo: Path, base_sha: str, head_sha: str) -> list[dict[str, str]]:
    raw = run_git(repo, "log", "--format=%H%x1f%s%x1f%b%x1e", f"{base_sha}..{head_sha}")
    commits = []
    for record in raw.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        parts = record.split("\x1f", 2)
        if len(parts) != 3:
            continue
        sha, subject, body = parts
        commits.append({"sha": sha.strip(), "subject": subject.strip(), "body": body.strip()})
    return commits


def is_skipped_commit(commit: dict[str, str]) -> bool:
    subject_low = commit["subject"].lower()
    if subject_low.startswith("chore(release):"):
        return True
    combined_low = (commit["subject"] + "\n" + commit["body"]).lower()
    return any(marker in combined_low for marker in SKIP_MARKERS)


def changed_plugin_paths_between(repo: Path, base_sha: str, head_sha: str) -> list[str]:
    raw = run_git(repo, "diff", "--name-only", base_sha, head_sha, "--", "plugins/p")
    return sorted({line.strip() for line in raw.splitlines() if line.strip()})


def changed_plugin_paths_for_commit(repo: Path, sha: str) -> set[str]:
    raw = run_git(
        repo,
        "diff-tree",
        "--no-commit-id",
        "--name-only",
        "-r",
        "-m",
        "--first-parent",
        sha,
        "--",
        "plugins/p",
    )
    return {line.strip() for line in raw.splitlines() if line.strip()}


def classify_commit(commit: dict[str, str], tests_only: bool) -> str:
    subject = commit["subject"]
    body = commit["body"]
    match = CONVENTIONAL_RE.match(subject)
    if match:
        if match.group("bang") or BREAKING_FOOTER_RE.search(body):
            return "major"
        commit_type = match.group("type").lower()
        if commit_type == "feat":
            return "minor"
        if commit_type in LOW_IMPACT_TYPES:
            return "none" if tests_only else "patch"
        return "patch"
    if BREAKING_FOOTER_RE.search(body):
        return "major"
    return "patch"


def next_version(current: str, bump: str) -> str:
    match = SEMVER_RE.fullmatch(current)
    if not match:
        raise ReleaseBumpError(f"invalid semver version: {current!r}")
    major, minor, patch = (int(part) for part in match.groups())
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    if bump == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ReleaseBumpError(f"unsupported bump kind: {bump!r}")


def format_changelog_bullet(subject: str) -> str:
    cleaned = TRAILING_P_TAG_RE.sub("", subject).strip()
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return f"- {cleaned}"


def update_manifest_text(rel_path: str, text: str, current: str, target: str) -> str:
    pattern = re.compile(r'("version"\s*:\s*")' + re.escape(current) + r'(")')
    updated, count = pattern.subn(lambda m: m.group(1) + target + m.group(2), text)
    if count != 1:
        raise ReleaseBumpError(
            f"expected 1 version field matching {current!r} in {rel_path}, found {count}"
        )
    try:
        parsed = json.loads(updated)
    except json.JSONDecodeError as exc:
        raise ReleaseBumpError(f"updated {rel_path} is not valid JSON: {exc}") from exc
    if extract_manifest_version(rel_path, parsed) != target:
        raise ReleaseBumpError(f"post-update version check failed for {rel_path}")
    return updated


def update_changelog_text(text: str, target: str, subjects: list[str]) -> str:
    duplicate_re = re.compile(r"(?m)^## \[?" + re.escape(target) + r"\]?(?:\s|$)")
    if duplicate_re.search(text):
        raise ReleaseBumpError(f"CHANGELOG.md already contains heading for {target}")
    bullets: list[str] = []
    seen: set[str] = set()
    for subject in subjects:
        bullet = format_changelog_bullet(subject)
        if bullet not in seen:
            seen.add(bullet)
            bullets.append(bullet)
    if not bullets:
        bullets = [f"- Release {target}."]
    section = f"## [{target}]\n\n" + "\n".join(bullets) + "\n\n"
    first_heading = re.search(r"(?m)^## \[", text)
    if first_heading:
        pos = first_heading.start()
        return text[:pos] + section + text[pos:]
    if text and not text.endswith("\n"):
        text += "\n"
    if text and not text.endswith("\n\n"):
        text += "\n"
    return text + section


def atomic_write_text(path: Path, content: str) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def write_github_output(output_path: Path, result: dict) -> None:
    lines = [
        f"bumped={'true' if result['bumped'] else 'false'}",
        f"current_version={result['current_version']}",
        f"new_version={result['new_version'] or ''}",
        f"bump={result['bump']}",
        f"base_sha={result['base_sha']}",
        f"head_sha={result['head_sha']}",
    ]
    with output_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")


def compute_bump(repo: Path, base: str, head: str, bump_mode: str) -> tuple[dict, dict[str, str]]:
    current, raw_manifests = read_manifests(repo)
    head_sha = run_git(repo, "rev-parse", "--verify", f"{head}^{{commit}}").strip()
    if base == "auto":
        base_sha = discover_base_sha(repo, head_sha)
    else:
        base_sha = run_git(repo, "rev-parse", "--verify", f"{base}^{{commit}}").strip()

    all_commits = list_commits(repo, base_sha, head_sha)
    releasable = [c for c in all_commits if not is_skipped_commit(c)]
    range_plugin_paths = changed_plugin_paths_between(repo, base_sha, head_sha)

    releasable_plugin_paths: set[str] = set()
    for commit in releasable:
        releasable_plugin_paths.update(changed_plugin_paths_for_commit(repo, commit["sha"]))

    effective_plugin_paths = sorted(releasable_plugin_paths) if releasable else []

    if bump_mode != "auto":
        chosen_bump = bump_mode
        bumped = True
        subjects = [c["subject"] for c in releasable] or [
            c["subject"] for c in all_commits if not c["subject"].lower().startswith("chore(release):")
        ]
    else:
        if not releasable or not range_plugin_paths or not effective_plugin_paths:
            chosen_bump = "none"
            bumped = False
            subjects = []
        else:
            tests_only = all(path.startswith("plugins/p/tests/") for path in effective_plugin_paths)
            chosen_bump = "none"
            for commit in releasable:
                candidate = classify_commit(commit, tests_only=tests_only)
                if BUMP_RANK[candidate] > BUMP_RANK[chosen_bump]:
                    chosen_bump = candidate
            bumped = chosen_bump != "none"
            subjects = [c["subject"] for c in releasable]

    target_version = next_version(current, chosen_bump) if bumped else None
    result = {
        "bumped": bumped,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "current_version": current,
        "new_version": target_version,
        "bump": chosen_bump,
        "commits": subjects,
        "changed_plugin_paths": effective_plugin_paths,
    }
    return result, raw_manifests


def apply_bump(repo: Path, result: dict, raw_manifests: dict[str, str]) -> None:
    current = result["current_version"]
    target = result["new_version"]
    if not target:
        return
    changelog_file = repo / CHANGELOG_PATH
    try:
        changelog_text = changelog_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReleaseBumpError(f"cannot read {CHANGELOG_PATH}: {exc}") from exc

    updated_manifests = {
        rel_path: update_manifest_text(rel_path, raw_manifests[rel_path], current, target)
        for rel_path in MANIFEST_PATHS
    }
    updated_changelog = update_changelog_text(changelog_text, target, result["commits"])

    for rel_path, content in updated_manifests.items():
        atomic_write_text(repo / rel_path, content)
    atomic_write_text(changelog_file, updated_changelog)

    verified_version, _ = read_manifests(repo)
    if verified_version != target:
        raise ReleaseBumpError(
            f"post-write manifest verification mismatch: expected {target}, got {verified_version}"
        )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compute and optionally apply automated semver bumps for plugin p."
    )
    parser.add_argument("--repo", default=".", help="Path to the git repository (default: .)")
    parser.add_argument(
        "--base",
        default="auto",
        help="Base revision or 'auto' to find the last manifest version-change commit (default: auto)",
    )
    parser.add_argument("--head", default="HEAD", help="Head revision to evaluate (default: HEAD)")
    parser.add_argument(
        "--bump",
        choices=("auto", "patch", "minor", "major"),
        default="auto",
        help="Bump level or 'auto' from Conventional Commits (default: auto)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Write updated manifests and CHANGELOG.md")
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute the bump without modifying files (default)",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON to stdout")
    parser.add_argument(
        "--github-output",
        default=None,
        help="Append key=value outputs to the given GitHub Actions output file",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    repo = Path(args.repo).resolve()
    result, raw_manifests = compute_bump(repo, args.base, args.head, args.bump)
    if args.apply and result["bumped"]:
        apply_bump(repo, result, raw_manifests)
    if args.github_output:
        write_github_output(Path(args.github_output), result)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    elif result["bumped"]:
        action = "Applied" if args.apply else "Planned"
        print(
            f"{action} {result['bump']} bump: {result['current_version']} -> {result['new_version']}"
        )
    else:
        print(f"No bump required (version remains {result['current_version']})")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except ReleaseBumpError as exc:
        sys.stderr.write(f"release-bump: {exc}\n")
        sys.exit(2)
