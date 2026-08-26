#!/usr/bin/env python3
"""Validate universal source packaging and isolated installed plugin copies.

Exit: 0 all checks passed, 1 validation found drift, 2 validation could not run.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


PLUGIN_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PLUGIN_ROOT.parents[1]
COMMAND_NAMES = (
    "adequacy-review",
    "statusline-apply",
    "statusline-check",
    "statusline-preview",
    "statusline-restore",
)
SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
CODEX_MANIFEST_KEYS = {
    "id",
    "name",
    "version",
    "description",
    "skills",
    "apps",
    "mcpServers",
    "interface",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
}
INTERFACE_FIELDS = {
    "displayName",
    "shortDescription",
    "longDescription",
    "developerName",
    "category",
}


def _read_json(path, label, errors):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        errors.append(label + " is missing or malformed")
        return None
    if not isinstance(value, dict):
        errors.append(label + " must contain a JSON object")
        return None
    return value


def _non_empty(value):
    return isinstance(value, str) and bool(value.strip())


def _validate_skill(skill_path, errors):
    try:
        text = skill_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        errors.append("skill %s is unreadable" % skill_path.parent.name)
        return
    if not text.startswith("---\n"):
        errors.append("skill %s has no YAML frontmatter" % skill_path.parent.name)
        return
    end = text.find("\n---", 4)
    if end == -1:
        errors.append("skill %s has unclosed YAML frontmatter" % skill_path.parent.name)
        return
    frontmatter = text[4:end]
    name = re.search(r"(?m)^name:\s*(\S.*?)\s*$", frontmatter)
    description = re.search(r"(?m)^description:\s*(\S.*?)\s*$", frontmatter)
    if name is None or name.group(1) != skill_path.parent.name:
        errors.append("skill %s has a mismatched frontmatter name" % skill_path.parent.name)
    if description is None:
        errors.append("skill %s has no frontmatter description" % skill_path.parent.name)


def validate_package(plugin_root):
    plugin_root = Path(plugin_root)
    errors = []
    claude = _read_json(
        plugin_root / ".claude-plugin" / "plugin.json",
        "Claude plugin manifest",
        errors,
    )
    codex = _read_json(
        plugin_root / ".codex-plugin" / "plugin.json",
        "universal plugin manifest",
        errors,
    )
    if claude is not None:
        for field in ("name", "version", "description"):
            if not _non_empty(claude.get(field)):
                errors.append("Claude plugin manifest field %s must be non-empty" % field)
    if codex is not None:
        unknown = sorted(set(codex) - CODEX_MANIFEST_KEYS)
        if unknown:
            errors.append("universal plugin manifest has unsupported fields: " + ", ".join(unknown))
        for field in ("name", "version", "description"):
            if not _non_empty(codex.get(field)):
                errors.append("universal plugin manifest field %s must be non-empty" % field)
        if not SEMVER_RE.fullmatch(str(codex.get("version", ""))):
            errors.append("universal plugin version must be strict semver")
        author = codex.get("author")
        if not isinstance(author, dict) or not _non_empty(author.get("name")):
            errors.append("universal plugin author.name must be non-empty")
        if codex.get("skills") != "./skills/":
            errors.append("universal plugin skills must be ./skills/")
        interface = codex.get("interface")
        if not isinstance(interface, dict):
            errors.append("universal plugin interface must be an object")
        else:
            for field in sorted(INTERFACE_FIELDS):
                if not _non_empty(interface.get(field)):
                    errors.append("universal plugin interface.%s must be non-empty" % field)
            capabilities = interface.get("capabilities")
            if not isinstance(capabilities, list) or not all(_non_empty(x) for x in capabilities):
                errors.append("universal plugin interface.capabilities must be a string array")
            prompt = interface.get("defaultPrompt", interface.get("default_prompt"))
            if not _non_empty(prompt) and not (
                isinstance(prompt, list)
                and 1 <= len(prompt) <= 3
                and all(_non_empty(item) for item in prompt)
            ):
                errors.append("universal plugin interface.defaultPrompt must be non-empty")
    if claude is not None and codex is not None:
        for field in ("name", "version", "description"):
            if claude.get(field) != codex.get(field):
                errors.append("Claude and universal plugin %s differ" % field)

    skills_root = plugin_root / "skills"
    skill_ids = set()
    if not skills_root.is_dir():
        errors.append("plugin skills directory is missing")
    else:
        for skill_path in sorted(skills_root.glob("*/SKILL.md")):
            skill_ids.add(skill_path.parent.name)
            _validate_skill(skill_path, errors)
    command_ids = {path.stem for path in (plugin_root / "commands").glob("*.md")}
    activation = _read_json(
        plugin_root / "profiles" / "skill-activation-v1.json",
        "skill activation manifest",
        errors,
    )
    if activation is not None:
        components = activation.get("components")
        if not isinstance(components, dict):
            errors.append("skill activation components must be an object")
        else:
            sources = skill_ids | command_ids
            if set(components) != sources:
                errors.append("skill activation manifest does not cover every source")
            for component, details in components.items():
                expected = "skill" if component in skill_ids else "command"
                if not isinstance(details, dict) or details.get("source") != expected:
                    errors.append("skill activation source differs for " + component)

    for name in COMMAND_NAMES:
        command_path = plugin_root / "commands" / (name + ".md")
        skill_path = plugin_root / "skills" / name / "SKILL.md"
        if not command_path.is_file() or not skill_path.is_file():
            errors.append("canonical skill or Claude adapter is missing for " + name)
            continue
        command = command_path.read_text(encoding="utf-8")
        expected_path = "${CLAUDE_PLUGIN_ROOT}/skills/%s/SKILL.md" % name
        if expected_path not in command or "$ARGUMENTS" not in command:
            errors.append("Claude command does not forward to canonical skill " + name)

    hooks = _read_json(plugin_root / "hooks" / "hooks.json", "hook manifest", errors)
    if hooks is not None and set(hooks.get("hooks", {})) != {
        "SessionStart",
        "UserPromptSubmit",
    }:
        errors.append("hook manifest does not preserve both format events")
    return errors


def validate_repository(repo_root=REPO_ROOT, plugin_root=PLUGIN_ROOT):
    repo_root = Path(repo_root)
    plugin_root = Path(plugin_root)
    errors = validate_package(plugin_root)
    claude = _read_json(
        repo_root / ".claude-plugin" / "marketplace.json",
        "Claude marketplace",
        errors,
    )
    universal = _read_json(
        repo_root / ".agents" / "plugins" / "marketplace.json",
        "universal marketplace",
        errors,
    )
    manifest = _read_json(
        plugin_root / ".claude-plugin" / "plugin.json",
        "Claude plugin manifest",
        errors,
    )
    if claude is not None and manifest is not None:
        entries = claude.get("plugins")
        entry = next(
            (item for item in entries or [] if isinstance(item, dict) and item.get("name") == "p"),
            None,
        )
        if entry is None:
            errors.append("Claude marketplace has no p entry")
        else:
            for field in ("version", "description", "keywords"):
                if entry.get(field) != manifest.get(field):
                    errors.append("Claude marketplace and manifest %s differ" % field)
    if universal is not None:
        if universal.get("name") != "polstools":
            errors.append("universal marketplace name must be polstools")
        interface = universal.get("interface")
        if not isinstance(interface, dict) or interface.get("displayName") != "polstools":
            errors.append("universal marketplace displayName must be polstools")
        entries = universal.get("plugins")
        entry = next(
            (item for item in entries or [] if isinstance(item, dict) and item.get("name") == "p"),
            None,
        )
        if entry is None:
            errors.append("universal marketplace has no p entry")
        else:
            if entry.get("source") != {"source": "local", "path": "./plugins/p"}:
                errors.append("universal marketplace p source must be ./plugins/p")
            if entry.get("policy") != {
                "installation": "AVAILABLE",
                "authentication": "ON_INSTALL",
            }:
                errors.append("universal marketplace p policy is invalid")
            if entry.get("category") != "Productivity":
                errors.append("universal marketplace p category must be Productivity")
    return errors


def smoke_installed_copy(plugin_root, label):
    with tempfile.TemporaryDirectory(prefix="p-validate-") as tmp:
        copy_root = Path(tmp) / label.lower() / "p"
        shutil.copytree(
            plugin_root,
            copy_root,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        errors = validate_package(copy_root)
        env = dict(os.environ)
        env.update({
            "POLSTOOLS_PYTHON": sys.executable,
            "PYTHONDONTWRITEBYTECODE": "1",
            "P_SKILL_CONFIG_FILE": str(Path(tmp) / "global.json"),
            "P_SKILL_STATE_DIR": str(Path(tmp) / "sessions"),
            "P_CODEX_CONFIG_FILE": str(Path(tmp) / "config.toml"),
            "P_SKILL_SKIP_STATUS_SYNC": "1",
            "CODEX_THREAD_ID": "p-validate-smoke",
        })
        commands = (
            [sys.executable, "-B", str(copy_root / "bin" / "skill-profile-ctl"), "validate"],
            [sys.executable, "-B", str(copy_root / "bin" / "format-e2e")],
        )
        for command in commands:
            result = subprocess.run(
                command,
                cwd=copy_root,
                env=env,
                text=True,
                encoding="utf-8",
                capture_output=True,
            )
            if result.returncode != 0:
                errors.append("installed-copy smoke command failed")
        return errors


def _report(label, errors):
    if errors:
        for error in errors:
            print("FAIL %s - %s" % (label, error))
        return False
    print("PASS " + label)
    return True


def main():
    try:
        checks = [
            _report("source package", validate_repository()),
            _report("Claude installed copy", smoke_installed_copy(PLUGIN_ROOT, "Claude")),
            _report("Codex installed copy", smoke_installed_copy(PLUGIN_ROOT, "Codex")),
        ]
    except (OSError, UnicodeError, json.JSONDecodeError, subprocess.SubprocessError):
        print("ERROR plugin validation could not run", file=sys.stderr)
        return 2
    passed = sum(checks)
    print("RESULT: %d passed, %d failed" % (passed, len(checks) - passed))
    return 0 if all(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
