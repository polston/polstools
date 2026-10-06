#!/usr/bin/env python3
"""Validate the p source package and a relocated copy of it.

The relocated copy proves the package runs from a path other than the
checkout; it is not a harness installation and never touches one.

Exit: 0 all checks passed, 1 validation found drift, 2 validation could not run.
"""

from __future__ import annotations

import argparse
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


FRONTMATTER_LINE_RE = re.compile(r"^([a-z][a-z0-9-]*): (\S.*)$")
PLAIN_SCALAR_FORBIDDEN_START = set("[]{}>|&*!%@`#,?:-'\"")


def _frontmatter_scalar(raw):
    if raw.startswith('"'):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            raise ValueError("malformed double-quoted value") from None
        if not isinstance(value, str):
            raise ValueError("malformed double-quoted value")
        return value
    if raw.startswith("'"):
        if len(raw) < 2 or not raw.endswith("'") or "'" in raw[1:-1].replace("''", ""):
            raise ValueError("malformed single-quoted value")
        return raw[1:-1].replace("''", "'")
    if raw[0] in PLAIN_SCALAR_FORBIDDEN_START:
        raise ValueError("value starts with a YAML indicator; quote it")
    if ": " in raw or " #" in raw or raw.endswith(":"):
        raise ValueError("plain value contains ': ' or ' #'; quote it")
    return raw.rstrip()


def parse_frontmatter(text):
    """Parse the SKILL.md frontmatter subset: one ``key: value`` per line.

    Values are plain, single-quoted, or JSON-compatible double-quoted scalars.
    Anything else -- nesting, continuation lines, block scalars, duplicate
    keys -- raises ValueError rather than being guessed at.
    """
    if not text.startswith("---\n"):
        raise ValueError("has no YAML frontmatter")
    end = text.find("\n---\n", 3)
    if end == -1 and text.endswith("\n---"):
        end = len(text) - 4
    if end == -1:
        raise ValueError("has unclosed YAML frontmatter")
    fields = {}
    for number, line in enumerate(text[4:end].split("\n"), start=2):
        match = FRONTMATTER_LINE_RE.match(line)
        if match is None:
            raise ValueError("frontmatter line %d is not 'key: value'" % number)
        key, raw = match.groups()
        if key in fields:
            raise ValueError("frontmatter repeats key %s" % key)
        try:
            fields[key] = _frontmatter_scalar(raw)
        except ValueError as error:
            raise ValueError("frontmatter %s: %s" % (key, error)) from None
    return fields


def _validate_skill(skill_path, errors):
    label = skill_path.parent.name
    try:
        fields = parse_frontmatter(skill_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        errors.append("skill %s is unreadable" % label)
        return
    except ValueError as error:
        errors.append("skill %s %s" % (label, error))
        return
    if fields.get("name") != label:
        errors.append("skill %s has a mismatched frontmatter name" % label)
    if not _non_empty(fields.get("description")):
        errors.append("skill %s has no frontmatter description" % label)


PLUGIN_REFERENCE_RE = re.compile(
    r"(<plugin-root>|<skill-root>|\$\{CLAUDE_PLUGIN_ROOT\}|`)"
    r"(/?)((?:bin|lib|style|profiles|renderer|hooks|skills|scripts|references)"
    r"/[A-Za-z0-9_./-]*[A-Za-z0-9_-])"
)


def _missing_references(text, plugin_root, skill_root):
    missing = set()
    for prefix, slash, relative in PLUGIN_REFERENCE_RE.findall(text):
        if prefix == "`" and slash:
            continue
        if prefix == "`" and not relative.startswith("bin/"):
            continue
        base = skill_root if prefix == "<skill-root>" else plugin_root
        if not (base / relative).exists():
            missing.add(relative)
    return sorted(missing)


def _validate_references(plugin_root, errors):
    documents = sorted((plugin_root / "skills").glob("*/**/*.md"))
    documents += sorted((plugin_root / "commands").glob("*.md"))
    for document in documents:
        try:
            text = document.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            errors.append("%s is unreadable" % document.relative_to(plugin_root).as_posix())
            continue
        parts = document.relative_to(plugin_root).parts
        skill_root = plugin_root / parts[0] / parts[1] if parts[0] == "skills" else plugin_root
        for relative in _missing_references(text, plugin_root, skill_root):
            errors.append(
                "%s references missing plugin file %s"
                % (document.relative_to(plugin_root).as_posix(), relative)
            )


def _validate_adequacy_review(plugin_root, errors):
    skill_root = plugin_root / "skills" / "adequacy-review"
    skill_path = skill_root / "SKILL.md"
    contract_path = skill_root / "contract-v1.json"
    helper_path = skill_root / "scripts" / "adequacy_review.py"
    adapters = {
        "Claude Code": skill_root / "references" / "claude-code.md",
        "Codex": skill_root / "references" / "codex.md",
        "Antigravity": skill_root / "references" / "antigravity.md",
    }
    if (plugin_root / "workflows" / "adequacy-review.js").exists():
        errors.append("legacy Claude-only adequacy-review workflow still exists")
    try:
        skill = skill_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        errors.append("adequacy-review canonical skill is unreadable")
    else:
        for expected in (
            "contract-v1.json",
            "references/claude-code.md",
            "references/codex.md",
            "references/antigravity.md",
            "Read exactly one adapter",
        ):
            if expected not in skill:
                errors.append("adequacy-review canonical skill does not route through " + expected)
        if "workflows/adequacy-review.js" in skill:
            errors.append("adequacy-review canonical skill still routes to the legacy workflow")
    for label, adapter_path in adapters.items():
        try:
            adapter = adapter_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            errors.append(label + " adequacy-review adapter is unreadable")
            continue
        if "contract-v1.json" not in adapter:
            errors.append(label + " adequacy-review adapter does not load contract-v1.json")
        if "scripts/adequacy_review.py" not in adapter:
            errors.append(label + " adequacy-review adapter does not use the canonical helper")
        duplicated = [
            token
            for token in ("agreement_threshold", "stable_cap", "GROUND", "TRACE")
            if token in adapter
        ]
        if duplicated:
            errors.append(label + " adequacy-review adapter duplicates review policy")
    if not contract_path.is_file() or not helper_path.is_file():
        errors.append("adequacy-review contract or helper is missing")
        return
    try:
        result = subprocess.run(
            [sys.executable, "-B", str(helper_path), "self-check"],
            cwd=plugin_root,
            text=True,
            encoding="utf-8",
            capture_output=True,
        )
    except (OSError, UnicodeError):
        errors.append("adequacy-review contract self-check could not run")
    else:
        if result.returncode != 0:
            errors.append("adequacy-review contract self-check failed")


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
    agy = _read_json(
        plugin_root / "plugin.json",
        "Antigravity plugin manifest",
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
    if agy is not None:
        for field in ("name", "version", "description"):
            if not _non_empty(agy.get(field)):
                errors.append("Antigravity plugin manifest field %s must be non-empty" % field)
        if not SEMVER_RE.fullmatch(str(agy.get("version", ""))):
            errors.append("Antigravity plugin version must be strict semver")
        author = agy.get("author")
        if not isinstance(author, dict) or not _non_empty(author.get("name")):
            errors.append("Antigravity plugin author.name must be non-empty")
    manifests = [("Claude", claude), ("universal", codex), ("Antigravity", agy)]
    for field in ("name", "version", "description", "keywords"):
        values = [(label, m.get(field)) for label, m in manifests if m is not None]
        if len({json.dumps(v, sort_keys=True) for _, v in values}) > 1:
            differs = ", ".join(label for label, _ in values)
            errors.append("Plugin manifests differ for %s across %s" % (field, differs))

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

    for command_path in sorted((plugin_root / "commands").glob("*.md")):
        name = command_path.stem
        skill_path = plugin_root / "skills" / name / "SKILL.md"
        if not command_path.is_file() or not skill_path.is_file():
            errors.append("canonical skill or Claude adapter is missing for " + name)
            continue
        command = command_path.read_text(encoding="utf-8")
        expected_path = "${CLAUDE_PLUGIN_ROOT}/skills/%s/SKILL.md" % name
        if expected_path not in command or "$ARGUMENTS" not in command:
            errors.append("Claude command does not forward to canonical skill " + name)

    _validate_adequacy_review(plugin_root, errors)
    _validate_references(plugin_root, errors)
    for contract in HOOK_CONTRACTS:
        _validate_hook_contract(plugin_root, contract, errors)
    return errors


# One entry per hook catalog a harness reads. Claude Code and Codex share
# hooks/hooks.json; a harness with its own catalog adds its own entry.
HOOK_CONTRACTS = (
    {
        "label": "Claude Code and Codex hook manifest",
        "path": ("hooks", "hooks.json"),
        "events": frozenset({"SessionStart", "UserPromptSubmit"}),
    },
    {
        "label": "Antigravity hook manifest",
        "path": ("hooks.json",),
        "events": frozenset({"PreInvocation"}),
        "shape": "antigravity",
    },
)
# Antigravity reads <plugin-root>/hooks.json: hook name -> event -> a flat
# handler list, run with the plugin root as the working directory.
AGY_HOOK_NAME = "p-format"
AGY_HOOK_COMMAND = "sh bin/agy-format-hook"
AGY_HOOKS_LOADED_RE = re.compile(r"^\s*\S*\s*hooks\s*:\s*1 processed\s*$", re.M)
HOOK_ROOT_TOKEN_RE = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/([^\"'\s]+)")


def _validate_hook_contract(plugin_root, contract, errors):
    label = contract["label"]
    manifest = _read_json(plugin_root.joinpath(*contract["path"]), label, errors)
    if manifest is None:
        return
    if contract.get("shape") == "antigravity":
        _validate_antigravity_hooks(manifest, contract, errors)
        return
    events = manifest.get("hooks")
    if not isinstance(events, dict):
        errors.append(label + " must map hook events to handler lists")
        return
    if set(events) != set(contract["events"]):
        errors.append(
            "%s declares %s; expected %s"
            % (label, ", ".join(sorted(events)), ", ".join(sorted(contract["events"])))
        )
    for event, groups in sorted(events.items()):
        commands = []
        if isinstance(groups, list):
            for group in groups:
                handlers = group.get("hooks") if isinstance(group, dict) else None
                for handler in handlers if isinstance(handlers, list) else []:
                    if isinstance(handler, dict) and _non_empty(handler.get("command")):
                        commands.append(handler["command"])
        if not commands:
            errors.append("%s %s has no command handler" % (label, event))
        for command in commands:
            for relative in HOOK_ROOT_TOKEN_RE.findall(command):
                if not (plugin_root / relative).exists():
                    errors.append(
                        "%s %s references missing plugin file %s" % (label, event, relative)
                    )


def _validate_antigravity_hooks(manifest, contract, errors):
    """One named hook whose single flat handler runs the format entry."""
    spec = manifest.get(AGY_HOOK_NAME) if isinstance(manifest, dict) else None
    handlers = spec.get("PreInvocation") if isinstance(spec, dict) else None
    handler = handlers[0] if isinstance(handlers, list) and len(handlers) == 1 else None
    timeout = handler.get("timeout", 30) if isinstance(handler, dict) else None
    if (
        set(manifest) != {AGY_HOOK_NAME}
        or set(spec) != set(contract["events"])
        or not isinstance(handler, dict)
        or handler.get("type", "command") != "command"
        or handler.get("command") != AGY_HOOK_COMMAND
        or not isinstance(timeout, int) or isinstance(timeout, bool)
        or not 0 < timeout <= 30
    ):
        errors.append(contract["label"] + " does not wire the format gate")


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
    tracked = _tracked_entries(repo_root)
    if tracked is not None:
        _validate_file_modes(repo_root, tracked, errors)
        _validate_root_hygiene(repo_root, errors)
    return errors


def _git(repo_root, *args):
    """Return git stdout for repo_root, or None when it is not a git checkout."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            text=True, encoding="utf-8", capture_output=True,
        )
    except OSError:
        return None
    return result.stdout if result.returncode == 0 else None


def _tracked_entries(repo_root):
    """Map tracked path to index mode, or None outside a git checkout."""
    if _git(repo_root, "rev-parse", "--show-toplevel") is None:
        return None
    output = _git(repo_root, "ls-files", "-s", "-z")
    if output is None:
        return None
    entries = {}
    for record in output.split("\0"):
        if "\t" in record:
            meta, path = record.split("\t", 1)
            entries[path] = meta.split()[0]
    return entries


def _validate_file_modes(repo_root, tracked, errors):
    """A tracked file is executable in the index exactly when it has a shebang.

    The index mode is what every clone receives, including Windows clones
    whose filesystem has no executable bit, so the filesystem is not read.
    """
    for path, mode in sorted(tracked.items()):
        if mode not in {"100644", "100755"}:
            continue
        try:
            with (Path(repo_root) / path).open("rb") as handle:
                shebang = handle.read(2) == b"#!"
        except OSError:
            continue
        if shebang and mode != "100755":
            errors.append("%s has a shebang but is not executable in the index" % path)
        elif not shebang and mode == "100755":
            errors.append("%s is executable in the index but has no shebang" % path)


def _validate_root_hygiene(repo_root, errors):
    output = _git(repo_root, "ls-files", "--others", "--exclude-standard", "--directory",
                  "--no-empty-directory", "-z")
    for path in sorted(item for item in (output or "").split("\0") if item):
        if "/" not in path.rstrip("/"):
            errors.append("untracked, unignored file at the repository root: " + path)


def _semver_key(version):
    match = SEMVER_RE.fullmatch(version or "")
    return tuple(int(part) for part in match.groups()) if match else None


def resolve_base(repo_root, base):
    """Return the commit id for base, or raise RuntimeError saying why not."""
    commit = None
    if not base.startswith("-"):
        commit = _git(repo_root, "rev-parse", "--verify", "--quiet", base + "^{commit}")
    if not commit or not commit.strip():
        raise RuntimeError(
            "base revision %s is not a commit in this clone; fetch it first "
            "(a shallow or detached clone may lack it)" % base
        )
    return commit.strip()


def validate_version_bump(repo_root, base):
    """Flag plugin content that changed since base without a version increase."""
    errors = []
    shown = _git(repo_root, "show", "%s:plugins/p/.claude-plugin/plugin.json" % base)
    if shown is None:
        raise RuntimeError("base revision %s has no readable plugin manifest" % base)
    base_version = json.loads(shown).get("version")
    current = _read_json(
        Path(repo_root) / "plugins" / "p" / ".claude-plugin" / "plugin.json",
        "Claude plugin manifest", errors,
    )
    if current is None:
        return errors
    version = current.get("version")
    changed = _git(repo_root, "diff", "--name-only", base, "--", "plugins/p")
    untracked = _git(repo_root, "ls-files", "--others", "--exclude-standard", "--", "plugins/p")
    if changed is None or untracked is None:
        raise RuntimeError("git could not compare the plugin tree with " + base)
    if (changed.strip() or untracked.strip()) and version == base_version:
        errors.append(
            "plugin content changed since %s but the version is still %s" % (base, version)
        )
    base_key, key = _semver_key(base_version), _semver_key(version)
    if base_key and key and key < base_key:
        errors.append("plugin version %s is lower than %s at %s" % (version, base_version, base))
    return errors


def smoke_relocated_copy(plugin_root):
    with tempfile.TemporaryDirectory(prefix="p-validate-") as tmp:
        copy_root = Path(tmp) / "relocated" / "p"
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
                errors.append(
                    "relocated-copy command failed: " + Path(command[2]).name
                )
        return errors


def antigravity_validate(plugin_root):
    """Run Antigravity's own plugin validator on a relocated copy, if agy exists."""
    agy_bin = shutil.which("agy")
    if not agy_bin and os.name != "nt":
        candidate = Path.home() / ".local" / "bin" / "agy"
        agy_bin = str(candidate) if candidate.is_file() else None
    if not agy_bin:
        return None
    with tempfile.TemporaryDirectory(prefix="p-validate-agy-") as tmp:
        copy_root = Path(tmp) / "p"
        shutil.copytree(
            plugin_root, copy_root,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        # agy must never see the operator's configuration: give it a scratch
        # home that goes away with the copy.
        scratch_home = Path(tmp) / "home"
        scratch_home.mkdir()
        env = dict(os.environ, HOME=str(scratch_home), USERPROFILE=str(scratch_home))
        result = subprocess.run(
            [agy_bin, "plugin", "validate", str(copy_root)],
            text=True, encoding="utf-8", capture_output=True, env=env,
        )
    if result.returncode != 0:
        return ["agy plugin validate rejected the package"]
    if not AGY_HOOKS_LOADED_RE.search(re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)):
        return ["agy plugin validate did not load hooks.json"]
    return []


def _report(label, errors):
    if errors is None:
        print("SKIP %s - agy is not installed" % label)
        return True
    if errors:
        for error in errors:
            print("FAIL %s - %s" % (label, error))
        return False
    print("PASS " + label)
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base",
        help="also require a version increase when plugins/p differs from this git revision",
    )
    args = parser.parse_args(argv)
    try:
        if args.base:
            resolve_base(REPO_ROOT, args.base)
        checks = [
            _report("source package", validate_repository()),
            _report("relocated copy", smoke_relocated_copy(PLUGIN_ROOT)),
            _report("Antigravity validator", antigravity_validate(PLUGIN_ROOT)),
        ]
        if args.base:
            checks.append(_report(
                "version bump since " + args.base,
                validate_version_bump(REPO_ROOT, args.base),
            ))
    except Exception as error:  # exit 2 is the contract for "could not run"
        # A RuntimeError here is one of this module's own messages, which name
        # revisions and never paths; any other exception is reported by class.
        detail = str(error) if type(error) is RuntimeError else type(error).__name__
        print("ERROR plugin validation could not run: %s" % detail, file=sys.stderr)
        return 2
    passed = sum(checks)
    print("RESULT: %d passed, %d failed" % (passed, len(checks) - passed))
    return 0 if all(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
