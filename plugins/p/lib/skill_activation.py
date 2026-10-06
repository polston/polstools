"""Shared activation-profile policy, state, and native adapter support."""

from __future__ import annotations

import codecs
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import time


SCHEMA_VERSION = 1
STALE_SECONDS = 14 * 24 * 3600
REFRESH_SECONDS = 24 * 3600
HARNESS_SESSION_VARS = (
    ("claude", ("CLAUDE_CODE_SESSION_ID",)),
    ("codex", ("CODEX_SESSION_ID", "CODEX_THREAD_ID")),
    ("antigravity", ("ANTIGRAVITY_CONVERSATION_ID",)),
)
SESSION_ENV_VARS = tuple(name for _, names in HARNESS_SESSION_VARS for name in names)
IDENTIFIER_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
NATIVE_BEGIN = "# p-skill-activation begin"
NATIVE_END = "# p-skill-activation end"


class PolicyError(ValueError):
    """A policy source could not be evaluated safely."""


def _read_json(path, label):
    try:
        with Path(path).open(encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError, UnicodeError) as error:
        raise PolicyError(label + " is unreadable or malformed") from error
    if not isinstance(value, dict):
        raise PolicyError(label + " must contain a JSON object")
    return value


def load_manifest(path):
    value = _read_json(path, "activation manifest")
    validate_manifest(value)
    return value


def _identifier(value, label):
    if not isinstance(value, str) or not IDENTIFIER_RE.fullmatch(value):
        raise PolicyError(label + " is not a valid identifier")


def validate_manifest(manifest, plugin_root=None):
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise PolicyError("activation manifest has an unsupported schema version")
    capabilities = manifest.get("capabilities")
    profiles = manifest.get("profiles")
    components = manifest.get("components")
    if not isinstance(capabilities, dict) or not capabilities:
        raise PolicyError("activation manifest capabilities must be a non-empty object")
    if not isinstance(profiles, dict) or not profiles:
        raise PolicyError("activation manifest profiles must be a non-empty object")
    if not isinstance(components, dict) or not components:
        raise PolicyError("activation manifest components must be a non-empty object")
    for capability, details in capabilities.items():
        _identifier(capability, "capability")
        if not isinstance(details, dict):
            raise PolicyError("capability details must be objects")
    if "control-plane" not in capabilities:
        raise PolicyError("activation manifest is missing control-plane")
    for profile, details in profiles.items():
        _identifier(profile, "profile")
        if not isinstance(details, dict):
            raise PolicyError("profile details must be objects")
        enabled = details.get("capabilities")
        if enabled == "all":
            enabled_set = set(capabilities)
        elif isinstance(enabled, list) and all(isinstance(x, str) for x in enabled):
            enabled_set = set(enabled)
            unknown = enabled_set - set(capabilities)
            if unknown:
                raise PolicyError("profile references an unknown capability")
        else:
            raise PolicyError("profile capabilities must be 'all' or a string array")
        if "control-plane" not in enabled_set:
            raise PolicyError("every profile must include control-plane")
    default = manifest.get("default_profile")
    if default not in profiles:
        raise PolicyError("activation manifest default_profile is unknown")
    for component, details in components.items():
        _identifier(component, "component")
        if not isinstance(details, dict):
            raise PolicyError("component details must be objects")
        if details.get("source") not in {"skill", "command"}:
            raise PolicyError("component source must be skill or command")
        required = details.get("requires")
        conditional = details.get("conditional_capabilities", [])
        if not isinstance(required, list) or not required or not all(
            isinstance(x, str) for x in required
        ):
            raise PolicyError("every component requires at least one capability")
        if not isinstance(conditional, list) or not all(
            isinstance(x, str) for x in conditional
        ):
            raise PolicyError("conditional capabilities must be a string array")
        if (set(required) | set(conditional)) - set(capabilities):
            raise PolicyError("component references an unknown capability")
        if len(required) != len(set(required)) or len(conditional) != len(
            set(conditional)
        ):
            raise PolicyError("component capability lists contain duplicates")
    if plugin_root is not None:
        root = Path(plugin_root)
        skill_ids = {
            path.parent.name for path in (root / "skills").glob("*/SKILL.md")
        }
        command_ids = {path.stem for path in (root / "commands").glob("*.md")}
        sources = skill_ids | command_ids
        declared = set(components)
        if sources != declared:
            missing = sorted(sources - declared)
            stale = sorted(declared - sources)
            details = []
            if missing:
                details.append("unclassified: " + ", ".join(missing))
            if stale:
                details.append("missing source: " + ", ".join(stale))
            raise PolicyError("component coverage mismatch (" + "; ".join(details) + ")")
        for component, details in components.items():
            expected = "skill" if component in skill_ids else "command"
            if details["source"] != expected:
                raise PolicyError("component source kind does not match the repository")


def session_id_from_env(env=None, required=False):
    """The current session id. A harness started inside another inherits the
    outer session's variable; when more than one harness is visible the
    caller must name its own with P_SKILL_HARNESS, because guessing would
    read or write the other session's state."""
    env = os.environ if env is None else env
    visible = []
    for harness, names in HARNESS_SESSION_VARS:
        for name in names:
            if env.get(name):
                visible.append((harness, env[name]))
                break
    named = env.get("P_SKILL_HARNESS")
    if named == "agy":
        named = "antigravity"
    if named in dict(HARNESS_SESSION_VARS):
        visible = [entry for entry in visible if entry[0] == named]
    if len(visible) > 1:
        raise PolicyError(
            "session ids for "
            + " and ".join(harness for harness, _ in visible)
            + " are all set; set P_SKILL_HARNESS to this session's harness"
        )
    if visible:
        return visible[0][1]
    if required:
        raise PolicyError("no supported harness session id is set")
    return None


def _state_dir(env=None):
    env = os.environ if env is None else env
    override = env.get("P_SKILL_STATE_DIR")
    if override:
        return Path(override)
    runtime = env.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / "p-skill-activation"
    suffix = str(os.getuid()) if hasattr(os, "getuid") else (env.get("USERNAME") or "default")
    return Path(tempfile.gettempdir()) / ("p-skill-activation-" + suffix)


def global_state_path(env=None):
    env = os.environ if env is None else env
    override = env.get("P_SKILL_CONFIG_FILE")
    if override:
        return Path(override)
    if env.get("LOCALAPPDATA"):
        return Path(env["LOCALAPPDATA"]) / "polstools" / "skill-activation.json"
    base = (
        Path(env["XDG_CONFIG_HOME"])
        if env.get("XDG_CONFIG_HOME")
        else Path.home() / ".config"
    )
    return base / "polstools" / "skill-activation.json"


def codex_config_path(env=None):
    env = os.environ if env is None else env
    override = env.get("P_CODEX_CONFIG_FILE")
    if override:
        return Path(override)
    codex_home = env.get("CODEX_HOME")
    return (Path(codex_home) if codex_home else Path.home() / ".codex") / "config.toml"


def _private_dir(directory, create):
    """Refuse a session directory another user could have planted: it must
    be a real directory owned by this user. With create, make it (0700) or
    tighten a looser mode. A missing directory is fine when only reading."""
    if create:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        info = os.lstat(directory)
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(info.st_mode) or (
        hasattr(os, "getuid") and info.st_uid != os.getuid()
    ):
        raise PolicyError(
            "session state directory is not a private directory owned by this user"
        )
    if create and hasattr(os, "getuid") and stat.S_IMODE(info.st_mode) != 0o700:
        os.chmod(directory, 0o700)


def session_state_path(session_id, env=None, create=False):
    directory = _state_dir(env)
    _private_dir(directory, create)
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return directory / (digest + ".json")


def _refresh(path):
    # Reading session state is use: refresh it (at most daily) so pruning
    # removes only state no session has read for STALE_SECONDS.
    now = time.time()
    try:
        if now - path.stat().st_mtime > REFRESH_SECONDS:
            os.utime(path, (now, now))
    except OSError:
        pass


def prune_session_state(env=None, now=None):
    directory = _state_dir(env)
    cutoff = (time.time() if now is None else now) - STALE_SECONDS
    try:
        for entry in directory.iterdir():
            if (
                re.fullmatch(r"[0-9a-f]{64}\.json", entry.name)
                and entry.stat().st_mtime < cutoff
            ):
                entry.unlink()
    except OSError:
        pass


def _validate_state(value, manifest, label):
    if value.get("schema_version") != SCHEMA_VERSION:
        raise PolicyError(label + " has an unsupported schema version")
    profile = value.get("profile")
    if profile not in manifest["profiles"]:
        raise PolicyError(label + " selects an unknown profile")
    overrides = value.get("overrides", {})
    if not isinstance(overrides, dict) or not all(
        isinstance(key, str) and isinstance(enabled, bool)
        for key, enabled in overrides.items()
    ):
        raise PolicyError(label + " overrides must map component IDs to booleans")
    unexpected = set(value) - {"schema_version", "profile", "overrides"}
    if unexpected:
        raise PolicyError(label + " contains unknown fields")
    stale = sorted(set(overrides) - set(manifest["components"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "profile": profile,
        "overrides": dict(overrides),
    }, stale


def _load_state(path, manifest, label):
    return _validate_state(_read_json(path, label), manifest, label)


def _profile_capabilities(manifest, profile):
    value = manifest["profiles"][profile]["capabilities"]
    return set(manifest["capabilities"]) if value == "all" else set(value)


def _resolution(manifest, profile, overrides, source, stale=None):
    return {
        "manifest": manifest,
        "profile": profile,
        "overrides": overrides,
        "source": source,
        "capabilities": _profile_capabilities(manifest, profile),
        "stale_overrides": list(stale or []),
    }


def resolve(manifest, session_id=None, env=None):
    env = os.environ if env is None else env
    locked = env.get("P_SKILL_PROFILE")
    if locked is not None:
        if locked not in manifest["profiles"]:
            raise PolicyError("P_SKILL_PROFILE selects an unknown profile")
        return _resolution(manifest, locked, {}, "environment")
    if session_id is None:
        session_id = session_id_from_env(env)
    if session_id:
        path = session_state_path(session_id, env)
        if path.exists():
            state, stale = _load_state(path, manifest, "session policy")
            _refresh(path)
            return _resolution(
                manifest, state["profile"], state["overrides"], "session", stale
            )
    path = global_state_path(env)
    if path.exists():
        state, stale = _load_state(path, manifest, "global policy")
        return _resolution(
            manifest, state["profile"], state["overrides"], "global", stale
        )
    return _resolution(manifest, manifest["default_profile"], {}, "default")


def resolve_global(manifest, env=None):
    path = global_state_path(env)
    if path.exists():
        state, stale = _load_state(path, manifest, "global policy")
        return _resolution(
            manifest, state["profile"], state["overrides"], "global", stale
        )
    return _resolution(manifest, manifest["default_profile"], {}, "default")


def capability_enabled(resolution, capability, component=None):
    manifest = resolution["manifest"]
    if capability not in manifest["capabilities"]:
        raise PolicyError("unknown capability")
    if component is not None:
        if component not in manifest["components"]:
            raise PolicyError("unknown component")
        override = resolution["overrides"].get(component)
        if override is not None:
            return override
    return capability in resolution["capabilities"]


def component_state(resolution, component):
    manifest = resolution["manifest"]
    if component not in manifest["components"]:
        raise PolicyError("unknown component")
    override = resolution["overrides"].get(component)
    if override is False:
        return "disabled"
    if override is True:
        return "enabled"
    details = manifest["components"][component]
    if any(
        not capability_enabled(resolution, capability, component)
        for capability in details["requires"]
    ):
        return "disabled"
    if any(
        not capability_enabled(resolution, capability, component)
        for capability in details.get("conditional_capabilities", [])
    ):
        return "limited"
    return "enabled"


def component_buckets(resolution):
    buckets = {"enabled": [], "disabled": [], "limited": []}
    for component in sorted(resolution["manifest"]["components"]):
        buckets[component_state(resolution, component)].append(component)
    return buckets


def label(resolution):
    return {"home": "p:h", "work": "p:w"}.get(resolution["profile"], "p:?")


def _atomic_write(path, payload):
    path = Path(path)
    parent_existed = path.parent.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not parent_existed:
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
    descriptor, temporary = tempfile.mkstemp(
        prefix="." + path.name + ".", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            shutil.copymode(path, temporary)
        else:
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def write_state(path, profile, overrides):
    value = {
        "schema_version": SCHEMA_VERSION,
        "profile": profile,
        "overrides": dict(sorted(overrides.items())),
    }
    _atomic_write(
        path, (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    )


def current_scope_state(manifest, scope, session_id=None, env=None):
    env = os.environ if env is None else env
    if scope == "session":
        session_id = session_id or session_id_from_env(env, required=True)
        path = session_state_path(session_id, env)
        if path.exists():
            return _load_state(path, manifest, "session policy")[0], path
        inherited = resolve_global(manifest, env)
        return {
            "schema_version": SCHEMA_VERSION,
            "profile": inherited["profile"],
            "overrides": {},
        }, path
    if scope == "global":
        path = global_state_path(env)
        if path.exists():
            return _load_state(path, manifest, "global policy")[0], path
        return {
            "schema_version": SCHEMA_VERSION,
            "profile": manifest["default_profile"],
            "overrides": {},
        }, path
    raise PolicyError("scope must be session or global")


def set_profile(manifest, profile, scope, session_id=None, env=None):
    if profile not in manifest["profiles"]:
        raise PolicyError("unknown profile")
    try:
        state, path = current_scope_state(manifest, scope, session_id, env)
    except PolicyError:
        if scope == "session":
            session_id = session_id or session_id_from_env(env, required=True)
            path = session_state_path(session_id, env)
        elif scope == "global":
            path = global_state_path(env)
        else:
            raise
        state = {
            "schema_version": SCHEMA_VERSION,
            "profile": manifest["default_profile"],
            "overrides": {},
        }
    if scope == "session":
        _private_dir(path.parent, create=True)
    write_state(path, profile, state["overrides"])
    if scope == "session":
        prune_session_state(env)


def set_override(manifest, component, enabled, scope, session_id=None, env=None):
    if component not in manifest["components"]:
        raise PolicyError("unknown component")
    if "control-plane" in manifest["components"][component]["requires"]:
        raise PolicyError("control-plane components cannot be overridden")
    state, path = current_scope_state(manifest, scope, session_id, env)
    state["overrides"][component] = bool(enabled)
    if scope == "session":
        _private_dir(path.parent, create=True)
    write_state(path, state["profile"], state["overrides"])
    if scope == "session":
        prune_session_state(env)


def reset_scope(manifest, scope, session_id=None, env=None):
    if scope == "session":
        session_id = session_id or session_id_from_env(env, required=True)
        path = session_state_path(session_id, env)
    elif scope == "global":
        path = global_state_path(env)
    else:
        raise PolicyError("scope must be session or global")
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _native_bounds(text):
    def marker_lines(marker):
        return list(
            re.finditer(
                r"(?m)^" + re.escape(marker) + r"[ \t]*(?:\r?\n|$)", text
            )
        )

    begin = marker_lines(NATIVE_BEGIN)
    end = marker_lines(NATIVE_END)
    if not begin and not end:
        return None
    if len(begin) != 1 or len(end) != 1 or begin[0].start() >= end[0].start():
        raise PolicyError("Codex config has malformed p-owned skill entries")
    return begin[0].start(), end[0].end()


def _native_region(plugin_root, resolution):
    disabled = []
    for component, details in resolution["manifest"]["components"].items():
        if details["source"] != "skill" or component_state(resolution, component) != "disabled":
            continue
        skill_path = Path(plugin_root) / "skills" / component / "SKILL.md"
        if skill_path.is_file():
            disabled.append((component, skill_path.resolve()))
    if not disabled:
        return ""
    lines = [NATIVE_BEGIN]
    for component, path in sorted(disabled):
        lines.extend(
            [
                "# component: " + component,
                "[[skills.config]]",
                "path = " + json.dumps(str(path)),
                "enabled = false",
                "",
            ]
        )
    while lines and not lines[-1]:
        lines.pop()
    lines.append(NATIVE_END)
    return "\n".join(lines) + "\n"


def update_native_text(text, plugin_root, resolution):
    bounds = _native_bounds(text)
    if bounds:
        text = text[: bounds[0]] + text[bounds[1] :]
    region = _native_region(plugin_root, resolution)
    if not region:
        return text
    newline = "\r\n" if "\r\n" in text else "\n"
    region = region.replace("\n", newline)
    if text and not text.endswith(("\n", "\r")):
        text += newline
    return text + region


def _read_native(env):
    """(target, text, bom). The target is the symlink-resolved file so a
    write replaces the real config, not the link. Bytes are decoded without
    newline translation so CRLF files round-trip unchanged."""
    target = Path(os.path.realpath(codex_config_path(env)))
    try:
        raw = target.read_bytes() if target.exists() else b""
        bom = raw.startswith(codecs.BOM_UTF8)
        return target, raw[len(codecs.BOM_UTF8) if bom else 0 :].decode("utf-8"), bom
    except (OSError, UnicodeError) as error:
        raise PolicyError("Codex config is unreadable") from error


def native_out_of_date(manifest, plugin_root, env=None):
    """True when sync-native has written p-owned entries that no longer
    match the global profile. Never raises: this only informs a notice."""
    try:
        _, text, _ = _read_native(env)
        if _native_bounds(text) is None:
            return False
        return update_native_text(text, plugin_root, resolve_global(manifest, env)) != text
    except PolicyError:
        return False


def sync_native(manifest, plugin_root, env=None):
    target, original, bom = _read_native(env)
    updated = update_native_text(original, plugin_root, resolve_global(manifest, env))
    if updated != original:
        try:
            _atomic_write(
                target, (codecs.BOM_UTF8 if bom else b"") + updated.encode("utf-8")
            )
        except OSError as error:
            raise PolicyError("Codex config could not be updated") from error
    return updated != original
