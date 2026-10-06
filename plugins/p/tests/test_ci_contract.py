"""Properties of the CI workflow, read with the standard library only.

The workflow uses a small subset of YAML (block mappings, one flow list per
matrix axis, block scalars for `run`), so a line reader is enough. The tests
assert properties that stay true as steps are added, not the YAML text.
"""

import re
from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "validate.yml"
README = REPO_ROOT / "README.md"
FULL_SHA = re.compile(r"^[\w.-]+/[\w.-]+(?:/[\w./-]+)?@[0-9a-f]{40}$")
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _indent(line):
    return len(line) - len(line.lstrip(" "))


def _meaningful(text):
    return [
        line.rstrip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _block(lines, header, indent):
    """Lines nested under the first `header` line at `indent`."""
    for position, line in enumerate(lines):
        if _indent(line) == indent and line.strip() == header:
            body = []
            for following in lines[position + 1 :]:
                if _indent(following) <= indent:
                    break
                body.append(following)
            return body
    return []


def _split_pair(line):
    key, _, value = line.strip().partition(":")
    value = re.sub(r"\s+#.*$", "", value.strip())
    if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return key.strip(), value


def _flow_list(value):
    return [item.strip().strip("\"'") for item in value.strip("[]").split(",") if item.strip()]


def parse_jobs(text):
    """Return {job: {"matrix": {axis: [...]}, "steps": [{...}], "raw": str}}."""
    lines = _meaningful(text)
    jobs = {}
    jobs_body = _block(lines, "jobs:", 0)
    names = [i for i, line in enumerate(jobs_body) if _indent(line) == 2 and line.rstrip().endswith(":")]
    for number, start in enumerate(names):
        end = names[number + 1] if number + 1 < len(names) else len(jobs_body)
        body = jobs_body[start + 1 : end]
        matrix = {}
        for line in _block(body, "matrix:", 6):
            key, value = _split_pair(line)
            matrix[key] = _flow_list(value)
        jobs[jobs_body[start].strip().rstrip(":")] = {
            "matrix": matrix,
            "steps": _parse_steps(_block(body, "steps:", 4)),
            "raw": "\n".join(body),
        }
    return jobs


def _parse_steps(lines):
    steps, current = [], None
    position = 0
    while position < len(lines):
        line = lines[position]
        if _indent(line) == 6 and line.strip().startswith("- "):
            current = {"with": {}, "env": {}}
            steps.append(current)
            line = line.replace("- ", "  ", 1)
        if current is not None and _indent(line) == 8:
            key, value = _split_pair(line)
            if value in {"|", ">"}:
                block = []
                while position + 1 < len(lines) and _indent(lines[position + 1]) > 8:
                    position += 1
                    block.append(lines[position].strip())
                current[key] = "\n".join(block)
            elif value == "":
                current[key] = {}
                while position + 1 < len(lines) and _indent(lines[position + 1]) > 8:
                    position += 1
                    inner_key, inner_value = _split_pair(lines[position])
                    current[key][inner_key] = inner_value
            else:
                current[key] = value
        position += 1
    return steps


def top_level_permissions(text):
    return dict(_split_pair(line) for line in _block(_meaningful(text), "permissions:", 0))


def readme_validation_commands(text):
    section = text.split("## Validate a checkout", 1)[1].split("\n## ", 1)[0]
    fence = section.split("```sh\n", 1)[1].split("```", 1)[0]
    return [line.strip() for line in fence.splitlines() if line.strip()]


class ContinuousIntegrationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.jobs = parse_jobs(cls.text)
        cls.steps = [step for job in cls.jobs.values() for step in job["steps"]]

    def commands(self, job):
        return [
            line
            for step in self.jobs[job]["steps"]
            for line in step.get("run", "").splitlines()
        ]

    def test_runs_on_pull_requests_main_pushes_and_manual_dispatch(self):
        triggers = _block(_meaningful(self.text), "on:", 0)
        names = {_split_pair(line)[0] for line in triggers if _indent(line) == 2}
        self.assertEqual({"pull_request", "push", "workflow_dispatch"}, names)
        self.assertIn("branches: [main]", self.text)

    def test_the_plugin_job_covers_three_systems_and_the_python_floor(self):
        job = self.jobs["plugin"]
        self.assertEqual(
            {"ubuntu-latest", "macos-latest", "windows-latest"},
            set(job["matrix"]["os"]),
        )
        self.assertIn("3.9", job["matrix"]["python"])
        self.assertGreater(len(job["matrix"]["python"]), 1)
        self.assertIn("fail-fast: false", job["raw"])
        self.assertIn("runs-on: ${{ matrix.os }}", job["raw"])

    def test_workflow_token_is_read_only(self):
        permissions = top_level_permissions(self.text)
        self.assertTrue(permissions)
        self.assertEqual({"read"}, set(permissions.values()))
        for name, job in self.jobs.items():
            with self.subTest(job=name):
                self.assertNotIn("permissions:", job["raw"])

    def test_every_action_is_pinned_to_a_full_commit(self):
        used = [step["uses"] for step in self.steps if "uses" in step]
        self.assertTrue(used)
        for reference in used:
            with self.subTest(uses=reference):
                self.assertRegex(reference, FULL_SHA)

    def test_every_checkout_has_full_history_for_the_privacy_audit(self):
        checkouts = [s for s in self.steps if s.get("uses", "").startswith("actions/checkout@")]
        self.assertTrue(checkouts)
        for step in checkouts:
            self.assertEqual("0", step["with"].get("fetch-depth"))

    def test_the_readme_validation_contract_runs_in_the_plugin_job(self):
        ran = self.commands("plugin")
        documented = readme_validation_commands(README.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(documented), 5)
        for command in documented:
            if command.startswith("git diff --check"):
                continue  # CI checks the committed tree; see the next test
            with self.subTest(command=command):
                self.assertIn(command, ran)
        self.assertIn(
            "sh plugins/p/bin/python-launcher -B plugins/p/bin/stopped-promises.py --selftest",
            ran,
        )

    def test_diff_check_compares_two_revisions(self):
        checks = [c.split() for c in self.commands("plugin") if c.startswith("git diff --check")]
        self.assertEqual(1, len(checks))
        self.assertEqual([EMPTY_TREE, "HEAD"], checks[0][3:])

    def test_posix_scripts_are_syntax_checked(self):
        script = "\n".join(self.commands("plugin"))
        self.assertIn("sh -n", script)
        self.assertIn("plugins/p/bin/*", script)

    def test_shell_steps_that_need_posix_tools_use_bash(self):
        for step in self.jobs["plugin"]["steps"]:
            run = step.get("run", "")
            if "git diff" in run or "repo-privacy-audit" in run or "sh -n" in run:
                with self.subTest(step=step.get("name")):
                    self.assertEqual("bash", step.get("shell"))

    def test_dependency_installs_are_confined(self):
        for name, job in self.jobs.items():
            run = "\n".join(step.get("run", "") for step in job["steps"])
            with self.subTest(job=name):
                self.assertNotIn("npm install", run)
                self.assertNotIn("uv sync", run)
                if name != "optional-analytics":
                    self.assertNotIn("pip install", run)
                for line in run.splitlines():
                    if "pip install" in line:
                        self.assertIn("--only-binary :all:", line)


class OptionalAnalyticsJobTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.jobs = parse_jobs(WORKFLOW.read_text(encoding="utf-8"))

    def test_optional_paths_run_where_they_can_never_skip(self):
        job = self.jobs["optional-analytics"]
        self.assertIn("runs-on: ubuntu-latest", job["raw"])
        setup = [s for s in job["steps"] if s.get("uses", "").startswith("actions/setup-python@")]
        self.assertEqual(["3.14"], [s["with"]["python-version"] for s in setup])
        runs = "\n".join(step.get("run", "") for step in job["steps"])
        self.assertIn(
            "pip install --only-binary :all: -r plugins/p/requirements-eval.txt", runs
        )
        tests = [s for s in job["steps"] if "unittest" in s.get("run", "")]
        self.assertEqual(1, len(tests))
        self.assertEqual("1", tests[0]["env"].get("RETRO_EVAL_REQUIRE_OPTIONAL"))
        self.assertIn('-p "test_eval_*.py"', tests[0]["run"])

    def test_version_bump_is_checked_against_the_pull_request_base(self):
        steps = [s for s in self.jobs["plugin"]["steps"] if "--base" in s.get("run", "")]
        self.assertEqual(1, len(steps))
        self.assertIn("pull_request", steps[0].get("if", ""))
        self.assertEqual(
            "${{ github.event.pull_request.base.sha }}", steps[0]["env"].get("BASE_SHA")
        )
        self.assertEqual('sh plugins/p/bin/p-validate --base "$BASE_SHA"', steps[0]["run"])


class PythonFloorTests(unittest.TestCase):
    def test_the_matrix_runs_the_python_floor_the_readme_states(self):
        readme = README.read_text(encoding="utf-8")
        floor = re.search(r"Python (\d+\.\d+) or newer", readme)
        self.assertIsNotNone(floor)
        job = parse_jobs(WORKFLOW.read_text(encoding="utf-8"))["plugin"]
        self.assertIn(floor.group(1), job["matrix"]["python"])


class WorkflowReaderTests(unittest.TestCase):
    def test_reader_sees_run_blocks_with_and_lists(self):
        text = (
            "jobs:\n"
            "  one:\n"
            "    strategy:\n"
            "      matrix:\n"
            "        os: [a, b]\n"
            "    steps:\n"
            "      - name: x\n"
            "        uses: owner/repo@" + "a" * 40 + "\n"
            "        with:\n"
            "          fetch-depth: 0\n"
            "      - name: y\n"
            "        run: |\n"
            "          first\n"
            "          second\n"
        )
        job = parse_jobs(text)["one"]
        self.assertEqual({"os": ["a", "b"]}, job["matrix"])
        self.assertEqual("0", job["steps"][0]["with"]["fetch-depth"])
        self.assertEqual("first\nsecond", job["steps"][1]["run"])


if __name__ == "__main__":
    unittest.main()
