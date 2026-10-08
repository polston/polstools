"""In-process stand-ins for the claude, codex, and agy CLIs.

Each CLI is a real empty file, so executable discovery behaves as it does for
an installed harness on every platform (a script fake cannot be executed
directly on Windows). Calls reach the module's runner, are recorded, and are
answered from per-command handlers that can read and change fixture state.
"""

import json
import os
from pathlib import Path
import subprocess
import sys

from home_env import home_vars


class FakeHarness:
    def __init__(self, root):
        self.root = Path(root)
        self.bin = self.root / "bin"
        self.bin.mkdir(parents=True, exist_ok=True)
        self.home = self.root / "home"
        self.home.mkdir(exist_ok=True)
        self.calls = []
        self._handlers = {}

    def cli(self, name):
        path = self.bin / name
        path.touch()
        return str(path)

    def on(self, name, args, result):
        """Answer `name args...` with (returncode, stdout) or a callable returning it."""
        self._handlers[(name, tuple(args))] = result

    def codex_installs_p(self, version, source_path, marketplace="polstools"):
        """Answer `codex plugin list --json` in the shape the real CLI emits.

        There is no installed-path field: `source.path` is where the plugin came
        from (a local marketplace's plugin directory), not what Codex loaded.
        """
        self.on("codex", ["plugin", "list", "--json"], (0, {"installed": [{
            "authPolicy": "ON_USE", "enabled": True, "installPolicy": "AVAILABLE",
            "installed": True, "marketplaceName": marketplace,
            "marketplaceSource": {"source": str(source_path), "sourceType": "local"},
            "name": "p", "pluginId": "p@" + marketplace,
            "source": {"path": str(source_path), "source": "local"},
            "version": version,
        }]}))
        self.on("codex", ["plugin", "marketplace", "list", "--json"], (0, {"marketplaces": []}))

    def codex_cache_dir(self, version, marketplace="polstools"):
        return self.home / ".codex" / "plugins" / "cache" / marketplace / "p" / version

    def env(self, prefix, names=("claude", "codex", "agy")):
        values = {
            **home_vars(str(self.home)),
            "USERPROFILE": str(self.home),
            "CODEX_HOME": str(self.home / ".codex"),
            "P_CODEX_CONFIG_FILE": str(self.home / ".codex" / "config.toml"),
            "P_UPDATE_STATE_DIR": str(self.home / ".codex" / "p-update"),
        }
        for name in ("claude", "codex", "agy"):
            key = prefix + name.upper()
            values[key] = self.cli(name) if name in names else str(self.root / "absent" / name)
        return values

    def __call__(self, argv, **kwargs):
        name = Path(str(argv[0])).name
        args = tuple(str(item) for item in argv[1:])
        self.calls.append((name,) + args)
        result = self._handlers.get((name, args), (0, ""))
        if callable(result):
            result = result()
        code, stdout = result
        if not isinstance(stdout, str):
            stdout = json.dumps(stdout)
        return subprocess.CompletedProcess(list(argv), code, stdout, "")


class patched_env:
    """Set environment variables for the duration of a with-block."""

    def __init__(self, values):
        self.values = values
        self.saved = {}

    def __enter__(self):
        for key, value in self.values.items():
            self.saved[key] = os.environ.get(key)
            os.environ[key] = value
        return self

    def __exit__(self, *exc):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return False


def garbled_cli(directory):
    """Write an executable that prints bytes that are not UTF-8; return its path.

    Windows cannot execute a shebang script directly, and a real harness CLI
    there is an .exe or a .cmd shim, so the Windows fake is a .cmd shim.
    """
    if os.name == "nt":
        path = Path(directory) / "garbled-cli.cmd"
        code = "import sys; sys.stdout.buffer.write(bytes([255, 254]) + b' not utf-8')"
        path.write_text('@"%s" -c "%s"\r\n' % (sys.executable, code), encoding="utf-8")
    else:
        path = Path(directory) / "garbled-cli"
        path.write_text("#!/bin/sh\nprintf '\\377\\376 not utf-8\\n'\n", encoding="utf-8")
        path.chmod(0o755)
    return str(path)


def write_plugin(root, version, extra=None):
    """Write a minimal plugin tree whose three manifests declare version."""
    root = Path(root)
    for relative in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json", "plugin.json"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"name": "p", "version": version}), encoding="utf-8")
    for relative, text in (extra or {}).items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root
