"""Wrong-typed transcript fields, control characters in output, and the
retro/stopped-promises environment resolvers."""

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from fixtures import build_corpus, claude_assistant, claude_user, usage_row

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
BIN = PLUGIN_ROOT / "bin"
LAUNCHER = BIN / "python-launcher"
NOW = datetime.now(timezone.utc) - timedelta(hours=2)
ESCAPE = "sk\x1b]0;pwn\x07\x1b[31m"


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, BIN / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.cc, self.cx, self.agy = base / "cc", base / "cx", base / "agy"
        self.work = base / "work"
        self.userdir = base / "userdir"
        self.userdir.mkdir()
        self.env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(self.userdir), "TMPDIR": str(base),
            "CLAUDE_CONFIG_DIR": str(self.cc), "CODEX_HOME": str(self.cx),
            "RETRO_ANTIGRAVITY_HOME": str(self.agy),
            "RETRO_HOME": str(self.work), "PYTHONDONTWRITEBYTECODE": "1",
        }

    def write_session(self, rows, name="s1"):
        build_corpus(self.cc / "projects",
                     [{"project": "p1", "session": name, "rows": rows}])

    def run_tool(self, script, *argv):
        return subprocess.run(
            ["sh", str(LAUNCHER), "-B", str(BIN / script), *argv],
            env=self.env, capture_output=True, text=True, timeout=120)

    def ledger(self):
        path = self.work / "metrics.jsonl"
        return [json.loads(l) for l in path.read_text().splitlines() if l]


class WrongTypedFields(Sandbox):
    def rows_with(self, bad):
        return [claude_user("please fix it", NOW),
                bad,
                claude_assistant("done", NOW + timedelta(seconds=5))]

    def assert_survives(self, bad, reason):
        self.write_session(self.rows_with(bad))
        for command in ("extract", "label"):
            done = self.run_tool("retro.py", command)
            self.assertNotIn("Traceback", done.stderr, command)
            self.assertIn(done.returncode, (0, 1, 2), command)
            if command == "extract":
                self.assertIn(done.returncode, (0, 1), done.stderr)
        skipped = {}
        for row in self.ledger():
            for key, count in (row.get("skipped") or {}).items():
                skipped[key] = skipped.get(key, 0) + count
        self.assertGreaterEqual(skipped.get(reason, 0), 1, skipped)

    def test_message_as_string(self):
        bad = claude_assistant("x", NOW + timedelta(seconds=2))
        bad["message"] = "not an object"
        self.assert_survives(bad, "bad_message")

    def test_token_count_as_string(self):
        bad = claude_assistant("x", NOW + timedelta(seconds=2))
        bad["message"]["usage"]["input_tokens"] = "1e3"
        self.assert_survives(bad, "bad_token_count")

    def test_text_as_number(self):
        bad = claude_assistant("x", NOW + timedelta(seconds=2))
        bad["message"]["content"][0]["text"] = 7
        self.assert_survives(bad, "bad_text")

    def test_stopped_promises_selftest_holds_for_any_home_basename(self):
        for name in ("run", "a", "home", "h", "path", "data",
                     "averylongaccountnamedirectory"):
            home = self.userdir / name
            home.mkdir()
            self.env["HOME"] = str(home)
            done = self.run_tool("stopped-promises.py", "--selftest")
            self.assertEqual(0, done.returncode, (name, done.stdout[-400:]))

    def test_user_text_as_number(self):
        bad = claude_user("x", NOW + timedelta(seconds=2))
        bad["message"]["content"][0]["text"] = 7
        self.assert_survives(bad, "bad_text")

    def test_user_tool_result_body_not_a_string(self):
        bad = claude_user("x", NOW + timedelta(seconds=2))
        bad["message"]["content"] = [
            {"type": "tool_result", "tool_use_id": "t1", "content": 7}]
        self.assert_survives(bad, "bad_text")

    def test_unexpected_exception_is_one_line_exit_2(self):
        retro = _load("retro_main_under_test", "retro.py")
        err = io.StringIO()
        with mock.patch.object(retro, "cmd_skills",
                               side_effect=RuntimeError("boom")), \
                mock.patch.object(sys, "argv", ["retro.py", "skills"]), \
                redirect_stderr(err), self.assertRaises(SystemExit) as stop:
            retro.main()
        self.assertEqual(2, stop.exception.code)
        lines = err.getvalue().strip().splitlines()
        self.assertEqual(1, len(lines), lines)
        self.assertTrue(lines[0].startswith("error:"), lines)


class ControlCharacters(Sandbox):
    BAD = ("\x1b", "\x07", "\x9b")

    def assert_clean(self, text, where):
        for char in self.BAD:
            self.assertNotIn(char, text, where)

    def test_retro_skills_and_pack(self):
        asst = claude_assistant("working on it " * 20, NOW + timedelta(seconds=2))
        asst["attributionSkill"] = ESCAPE
        asst["message"]["content"][0]["text"] += ESCAPE
        self.write_session([claude_user("do " + ESCAPE, NOW), asst,
                            claude_user("no, wrong " + ESCAPE,
                                        NOW + timedelta(seconds=9))])
        self.assertIn(self.run_tool("retro.py", "extract").returncode, (0, 1))
        skills = self.run_tool("retro.py", "skills")
        self.assertEqual(0, skills.returncode, skills.stderr)
        self.assert_clean(skills.stdout, "skills stdout")
        self.assertIn("sk", skills.stdout)
        pack = self.run_tool("retro.py", "pack", "--days", "30")
        self.assert_clean(pack.stdout, "pack stdout")
        for path in self.work.rglob("*"):
            if path.is_file():
                self.assert_clean(path.read_text(errors="replace"), path.name)

    def test_cache_ttl_unpriced_model(self):
        rows = [usage_row("r%d" % i, ESCAPE, 1000, 0, 500, 10,
                          NOW + timedelta(minutes=i)) for i in range(4)]
        self.write_session(rows)
        for argv in (("report",), ("evaluate",)):
            done = self.run_tool("cache_ttl.py", *argv)
            self.assert_clean(done.stdout + done.stderr, " ".join(argv))

    def test_stopped_promises_uses_the_shared_control_stripper(self):
        sys.path.insert(0, str(PLUGIN_ROOT))
        from retro_eval import text_rules
        stopped = _load("stopped_redact_under_test", "stopped-promises.py")
        self.assertIs(text_rules.strip_controls, stopped.strip_controls)
        for fn in (text_rules.redact, stopped.redact):
            out = fn("a\x1b]0;x\x07b\x9bc\x00d\tline\nend")
            self.assert_clean(out, fn.__module__)
            self.assertNotIn("\x00", out)
            self.assertIn("\t", out)
            self.assertIn("\n", out)


class MalformedUsageRows(Sandbox):
    MODEL = "claude-sonnet-5-5"

    def good_rows(self):
        return [usage_row("ok%d" % i, self.MODEL, 100000, 0, 5000, 10,
                          NOW + timedelta(minutes=i)) for i in range(5)]

    def report(self, bad):
        self.write_session(self.good_rows() + [bad])
        done = self.run_tool("cache_ttl.py", "report", "--json")
        self.assertIn(done.returncode, (0, 1), done.stdout + done.stderr)
        return json.loads(done.stdout)

    def assert_skipped(self, bad):
        data = self.report(bad)
        self.assertEqual(1, data["skipped"].get("bad_usage"), data["skipped"])
        return data

    def test_string_token_count_is_skipped(self):
        bad = usage_row("bad", self.MODEL, "7", 0, 5000, 10, NOW)
        self.assert_skipped(bad)

    def test_unhashable_request_id_is_skipped(self):
        bad = usage_row({"a": 1}, self.MODEL, 100000, 0, 5000, 10, NOW)
        self.assert_skipped(bad)

    def test_negative_token_count_is_skipped_not_an_empty_window(self):
        bad = usage_row("neg", self.MODEL, -1000000000, 0, 5000, 10, NOW)
        data = self.assert_skipped(bad)
        self.assertNotIn("nothing to decide", json.dumps(data))

    def test_boolean_token_count_is_skipped(self):
        bad = usage_row("flag", self.MODEL, True, 0, 5000, 10, NOW)
        self.assert_skipped(bad)


class SelftestHomeName(Sandbox):
    def test_selftest_passes_with_a_one_letter_home_directory(self):
        short = Path(self.tmp.name) / "h"
        short.mkdir()
        self.env["HOME"] = str(short)
        done = self.run_tool("stopped-promises.py", "--selftest")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)


class ClaudeConfigDir(unittest.TestCase):
    def test_whitespace_value_means_default(self):
        retro = _load("retro_cfg_under_test", "retro.py")
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": "   "}):
            self.assertEqual(retro.HOME / ".claude", retro.claude_config_dir())
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": " /x/y "}):
            self.assertEqual(Path("/x/y"), retro.claude_config_dir())

    def test_session_var_table_is_the_shared_one(self):
        retro = _load("retro_vars_under_test", "retro.py")
        import skill_activation  # on sys.path once retro has loaded
        self.assertIs(skill_activation.HARNESS_SESSION_VARS,
                      retro.HARNESS_SESSION_VARS)


class FormatCtlTableMatchesLib(unittest.TestCase):
    """format-ctl keeps its own copy of the harness session-variable table and
    the agy alias; the lib owns the definition. Nothing else ties them."""

    def format_ctl(self):
        import ast
        tree = ast.parse((BIN / "format-ctl").read_text("utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                    getattr(t, "id", "") == "HARNESS_SESSION_VARS"
                    for t in node.targets):
                return ast.literal_eval(node.value)
        self.fail("format-ctl no longer defines HARNESS_SESSION_VARS")

    def test_session_variable_table_equals_the_lib_table(self):
        sys.path.insert(0, str(PLUGIN_ROOT / "lib"))
        import skill_activation
        self.assertEqual(skill_activation.HARNESS_SESSION_VARS,
                         self.format_ctl())

    def test_agy_alias_means_antigravity_in_both(self):
        sys.path.insert(0, str(PLUGIN_ROOT / "lib"))
        import skill_activation
        import ast
        # format-ctl runs main() on import, so lift the one function out.
        tree = ast.parse((BIN / "format-ctl").read_text("utf-8"))
        func = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == "harness_override")
        scope = {"os": os, "HARNESSES": tuple(h for h, _ in self.format_ctl())}
        exec(compile(ast.Module([func], []), "format-ctl", "exec"), scope)
        with mock.patch.dict(os.environ, {"P_FORMAT_HARNESS": "agy"}):
            self.assertEqual("antigravity", scope["harness_override"]())
        env = {"CLAUDE_CODE_SESSION_ID": "c1",
               "ANTIGRAVITY_CONVERSATION_ID": "a1", "P_SKILL_HARNESS": "agy"}
        self.assertEqual("a1", skill_activation.session_id_from_env(env))

    def test_the_sh_gate_names_no_session_variables_of_its_own(self):
        # format-gate decides from the toggle directory alone and hands any
        # session lookup to format-ctl, so there is no list to keep in step.
        gate = (BIN / "format-gate").read_text("utf-8")
        for _, names in self.format_ctl():
            for name in names:
                self.assertNotIn(name, gate)


if __name__ == "__main__":
    unittest.main()
