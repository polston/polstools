import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


AUDITOR = Path(__file__).resolve().parents[1] / "bin" / "repo-privacy-audit"

# Every planted value is assembled from parts, so this file never holds a
# value its own audit would flag. (file, text, category)
DETECTION_MATRIX = (
    ("cred-github.txt", "gh" + "p_" + "x" * 36, "credential"),
    ("cred-github-pat.txt", "github" + "_pat_" + "x" * 30, "credential"),
    ("cred-aws.txt", "AKIA" + "X" * 16, "credential"),
    ("cred-anthropic.txt", "sk-" + "ant-" + "api03-" + "x" * 30, "credential"),
    ("cred-slack.txt", "xox" + "b-" + "1" * 12 + "-" + "x" * 10, "credential"),
    ("cred-header.txt", "Authorization: " + "Bearer " + "x" * 30, "credential"),
    ("cred-key.txt", "-----BEGIN OPENSSH " + "PRIVATE KEY-----", "credential"),
    ("cred-url.txt", "https://" + "fixture:" + "hunter22" + "@" + "dbhost/x",
     "credential"),
    ("win-json.txt", "C:" + "\\\\" + "Users" + "\\\\" + "fixture",
     "windows_home_path"),
    ("win-percent.txt", "C%3A%5C" + "Users%5Cfixture", "windows_home_path"),
    ("win-forward.txt", "C:/" + "Users/fixture", "windows_home_path"),
    ("unix-users.txt", "/" + "Users/fixture/x", "unix_home_path"),
    ("unix-root.txt", "/" + "root/.ssh", "unix_home_path"),
    ("unix-gitbash.txt", "/c/" + "Users/fixture", "unix_home_path"),
    ("unix-scp.txt", "host:/" + "home/fixture/x", "unix_home_path"),
    ("ip-ten.txt", "10." + "1.2.3", "private_ip"),
    ("ip-cgnat.txt", "100." + "64.1.2", "private_ip"),
    ("ip-linklocal.txt", "169." + "254.1.1", "private_ip"),
    ("ip-ula.txt", "fd12:" + "3456:789a::1", "private_ip"),
    ("ip-v6-linklocal.txt", "fe80:" + ":1", "private_ip"),
    ("mac-colon.txt", "aa:bb:" + "cc:dd:ee:ff", "mac_address"),
    ("mac-dash.txt", "aa-bb-" + "cc-dd-ee-ff", "mac_address"),
    ("mac-dotted.txt", "aabb." + "ccdd.eeff", "mac_address"),
    ("host-local.txt", "nas" + ".local", "internal_hostname"),
    ("host-lan.txt", "printer" + ".lan", "internal_hostname"),
    ("host-internal.txt", "db" + ".internal", "internal_hostname"),
    ("uuid.txt", "123e4567-" + "e89b-12d3-a456-426614174000", "uuid"),
    ("uuid-hyphen-prefix.txt", "sess-" + "123e4567-" + "e89b-12d3-a456-426614174000",
     "uuid"),
    ("uuid-rollout.txt", "rollout-2025-05-07T17-24-21-" + "123e4567-"
     + "e89b-12d3-a456-426614174000" + ".jsonl", "uuid"),
    ("cred-aws-secret.txt", "aws_secret" + "_access_key = " + "A" * 40,
     "credential"),
    ("email.txt", "someone" + "@" + "example.com", "email"),
    ("money.txt", "$" + "1234", "money_amount"),
    ("billing.txt", "subscription" + "Type", "account_billing_field"),
)

# Shapes that resemble a category and are not private data.
NEGATIVE_MATRIX = (
    ("neg-attribute.txt", "if self.provider" + ".local:"),
    ("neg-threading.txt", "state = threading" + ".local()"),
    ("neg-version.txt", "macOS 10." + "15.7"),
    ("neg-nil-uuid.txt", "00000000-" + "0000-0000-0000-000000000000"),
    ("neg-price.txt", "$" + "5.00 per million"),
    ("neg-header-var.txt", "Authorization: " + "Bearer $TOKEN"),
    ("neg-url-var.txt", "https://" + "user:${PASSWORD}" + "@host/x"),
    ("neg-mid-path.txt", "skills/" + "home/profile.md"),
    ("neg-short-sk.txt", "sk-" + "learn"),
)


def git_env():
    env = dict(os.environ)
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


class AuditCase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.repo = self.root / "repo"
        self.init(self.repo)

    def tearDown(self):
        self.tempdir.cleanup()

    @staticmethod
    def email(local_part):
        return local_part + "@" + "example" + ".test"

    def init(self, path, *extra):
        path.mkdir(parents=True, exist_ok=True)
        self.git(path, "init", "-q", *extra)
        if "--bare" not in extra:
            self.git(path, "config", "user.name", "Test Author")
            self.git(path, "config", "user.email", self.email("author"))

    def git(self, cwd, *args):
        return subprocess.run(
            ["git", *args], cwd=cwd, check=True, capture_output=True,
            text=True, env=git_env())

    def write(self, name, content, repo=None):
        path = (repo or self.repo) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content + "\n", encoding="utf-8")
        return path

    def commit(self, message="change", repo=None):
        repo = repo or self.repo
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-q", "--allow-empty", "-m", message)

    def audit(self, *args, cwd=None, locale="C"):
        env = git_env()
        env["LC_ALL"] = locale
        return subprocess.run(
            ["sh", str(AUDITOR), *args], cwd=cwd or self.repo,
            capture_output=True, text=True, env=env)

    @staticmethod
    def rows(stdout):
        """category -> list of seven column counts."""
        table = {}
        for line in stdout.splitlines():
            parts = line.split()
            if len(parts) == 8 and all(p.isdigit() for p in parts[1:]):
                table[parts[0]] = [int(p) for p in parts[1:]]
        return table

    @staticmethod
    def locations(stdout):
        """category -> locations listed under it by -v."""
        listed, current = {}, None
        for line in stdout.splitlines():
            parts = line.split()
            if len(parts) == 8 and all(p.isdigit() for p in parts[1:]):
                current = parts[0]
                listed[current] = []
            elif line.startswith("      ") and current:
                listed[current].append(line.strip())
            elif not line.strip():
                current = None
        return listed


class DetectionTests(AuditCase):
    def test_every_planted_item_is_found_in_its_own_category(self):
        for name, text, _ in DETECTION_MATRIX:
            self.write(name, text)
        self.commit("plant fixtures")

        result = self.audit("-v")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        listed = self.locations(result.stdout)
        expected = {}
        for name, _, category in DETECTION_MATRIX:
            expected.setdefault(category, []).append(name)
        for category, names in expected.items():
            with self.subTest(category=category):
                self.assertEqual(sorted(listed.get(category, [])), sorted(names))
                self.assertEqual(self.rows(result.stdout)[category][1], len(names))

    def test_lookalikes_read_zero(self):
        for name, text in NEGATIVE_MATRIX:
            self.write(name, text)
        self.commit("plant lookalikes")

        result = self.audit("-v")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("RESULT: every category read zero", result.stdout)

    def test_value_in_a_filename_is_found_and_the_name_withheld(self):
        name = "someone" + "@" + "example.com-10." + "0.0.5.txt"
        self.write(name, "clean")
        self.commit("add")

        result = self.audit("-v")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        rows = self.rows(result.stdout)
        self.assertEqual(rows["email"][5], 1)
        self.assertEqual(rows["private_ip"][5], 1)
        self.assertNotIn("someone", result.stdout)
        self.assertIn("<name withheld: it matches email>", result.stdout)

    def test_output_carries_no_absolute_path(self):
        self.write("a.txt", "clean")
        self.commit("add")

        result = self.audit()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(str(self.root), result.stdout)
        self.assertNotIn(self.root.resolve().as_posix(), result.stdout)


class RobustnessTests(AuditCase):
    def test_long_single_line_files_are_scanned_in_bounded_time(self):
        # One line, no newline: an address-shaped run, a longer one, and a
        # line with a trigger at every other byte.
        self.write("a.txt", "a" * 20000 + "@" + "b" * 20000)
        self.write("b.txt", "a" * 200000 + "@" + "b" * 200000)
        self.write("c.txt", "$1" * 400000)
        # Every start of a key prefix has a run of key characters to look
        # through for a digit.
        self.write("d.txt", "sk-" * 100000)
        self.commit("long lines")
        env = git_env()
        env["LC_ALL"] = "C"

        result = subprocess.run(
            ["sh", str(AUDITOR), "-C", str(self.repo)], capture_output=True,
            text=True, env=env, timeout=60)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipIf(os.name == "nt", "Windows file names cannot hold control characters")
    def test_a_name_with_terminal_control_sequences_is_printed_inert(self):
        name = "\x1b]0;title\x07\x1b[31m red.pem"
        self.write(name, "x")
        self.commit("hostile name")

        result = self.audit()

        self.assertNotIn("\x1b", result.stdout)
        self.assertNotIn("\x07", result.stdout)
        self.assertIn("red.pem", result.stdout)

    def test_a_git_failure_does_not_echo_git_stderr(self):
        stub_dir = self.root / "stub"
        stub_dir.mkdir()
        stub = stub_dir / "git"
        secret_url = "https://" + "user:" + "tokq9z" + "@host.example/r.git"
        stub.write_text(
            "#!/bin/sh\n"
            'case " $* " in *" log "*) echo "fatal: ' + secret_url
            + '" >&2; exit 128;; esac\n'
            'exec "' + shutil.which("git") + '" "$@"\n', encoding="utf-8")
        stub.chmod(0o755)
        if os.name == "nt":
            # Windows runs only PATHEXT names; a .cmd shim is how a script
            # stands in for git.exe there.
            (stub_dir / "git.cmd").write_text('@sh "%~dp0git" %*\r\n', encoding="utf-8")
        self.commit("base")
        env = git_env()
        env["PATH"] = str(stub_dir) + os.pathsep + env["PATH"]

        result = subprocess.run(
            ["sh", str(AUDITOR), "-C", str(self.repo)], capture_output=True,
            text=True, env=env)

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertNotIn("tokq9z", result.stdout + result.stderr)
        self.assertIn("git log failed", result.stderr)


class PlacesTests(AuditCase):
    def test_commit_identity_email_is_accepted_and_counted(self):
        self.commit("ordinary commit")

        result = self.audit()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("accepted, not findings: 1 author/committer/tagger "
                      "address(es); 0 co-author trailer(s).", result.stdout)
        self.assertIn("RESULT: every category read zero", result.stdout)

    def test_well_formed_coauthor_trailer_is_accepted(self):
        self.commit("ordinary commit\n\nCo-Authored-By: Automation <"
                    + self.email("automation") + ">")

        result = self.audit()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("1 author/committer/tagger address(es); "
                      "1 co-author trailer(s).", result.stdout)

    def test_malformed_coauthor_line_is_a_finding(self):
        self.commit("subject\n\nCo-Authored-By: ping " + self.email("leak"))

        result = self.audit()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.rows(result.stdout)["email"][0], 1)

    def test_trailer_shape_outside_the_last_paragraph_is_a_finding(self):
        self.commit("subject\n\nCo-Authored-By: A <" + self.email("leak")
                    + ">\n\nbody after it")

        result = self.audit()

        self.assertEqual(self.rows(result.stdout)["email"][0], 1)

    def test_trailer_shaped_subject_line_is_a_finding(self):
        self.commit("Co-Authored-By: A <" + self.email("leak") + ">")

        result = self.audit()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.rows(result.stdout)["email"][0], 1)

    def test_commit_message_email_remains_a_finding(self):
        self.commit("contact " + self.email("private"))

        result = self.audit()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.rows(result.stdout)["email"], [1, 0, 0, 0, 0, 0, 0])

    def test_generic_home_directory_in_a_path_is_not_content(self):
        self.write("skills/" + "home" + "/SKILL.md", "generic profile")
        self.commit("add generic home profile")

        result = self.audit()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_history_only_content_is_found_in_trees_and_patches(self):
        self.write("marker.txt", "/" + "Users" + "/fixture-account/private.txt")
        self.commit("add marker")
        self.write("marker.txt", "removed")
        self.commit("remove marker")

        result = self.audit()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.rows(result.stdout)["unix_home_path"],
                         [0, 1, 1, 0, 0, 0, 0])

    def test_tag_message_is_scanned(self):
        self.commit("base")
        self.git(self.repo, "tag", "-a", "v1", "-m",
                 "built on " + "10." + "0.0.7")

        result = self.audit()

        self.assertEqual(self.rows(result.stdout)["private_ip"][3], 1)

    def test_staged_and_untracked_files_are_scanned_and_ignored_files_are_not(self):
        self.write(".gitignore", "ignored.txt")
        self.commit("base")
        self.write("staged.txt", "/" + "Users/fixture/a")
        self.git(self.repo, "add", "staged.txt")
        self.write("untracked.txt", "/" + "Users/fixture/b")
        self.write("ignored.txt", "/" + "Users/fixture/c")

        result = self.audit("-v")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.rows(result.stdout)["unix_home_path"][6], 2)
        self.assertEqual(sorted(self.locations(result.stdout)["unix_home_path"]),
                         ["staged.txt", "untracked.txt"])

    def test_utf16_and_nul_byte_files_are_scanned(self):
        self.write("wide.txt", ("/" + "Users/fixture/wide\n").encode("utf-16"))
        self.write("blob.bin", b"\x00\x01head " + ("/" + "Users/fixture/bin").encode())
        self.commit("binary")

        result = self.audit()

        self.assertEqual(self.rows(result.stdout)["unix_home_path"][1], 2)
        self.assertIn("2 binary or UTF-16 blob(s)", result.stdout)

    def test_non_utf8_byte_does_not_hide_a_later_value(self):
        self.write("latin1.txt", b"caf\xe9 " + ("/" + "Users/fixture/x").encode())
        self.commit("latin1")

        result = self.audit(locale="en_US.UTF-8")

        self.assertEqual(self.rows(result.stdout)["unix_home_path"][:3], [0, 1, 1])

    def test_secretish_assignments_need_the_flag(self):
        self.write("conf.txt", "pass" + "word = hunter22")
        self.commit("conf")

        self.assertNotIn("secretish_assignment", self.audit().stdout)
        flagged = self.audit("-k")
        self.assertEqual(self.rows(flagged.stdout)["secretish_assignment"][1], 1)


class SensitiveNameTests(AuditCase):
    def flagged(self, result):
        lines = result.stdout.split("--- sensitive filenames", 1)[1]
        lines = lines.split("--- largest blobs", 1)[0].splitlines()[1:]
        return sorted(line.strip() for line in lines if line.strip())

    def test_names_ever_present_are_checked_under_every_name(self):
        self.write("config.txt", "k=v")
        self.commit("base")
        self.git(self.repo, "mv", "config.txt", ".env")
        self.commit("rename")
        self.git(self.repo, "checkout", "-q", "-b", "side")
        self.write("side.txt", "s")
        self.commit("side")
        self.git(self.repo, "checkout", "-q", "-")
        self.git(self.repo, "merge", "-q", "--no-ff", "--no-commit", "side")
        self.write("merged.pem", "x")
        self.commit("merge")
        for name in (".netrc", "id_ed25519", ".bash_history", ".zsh_history",
                     ".git-credentials", ".htpasswd", "vault.kdbx",
                     "infra.tfstate", "vpn.ovpn", "key.ppk", "auth.p8",
                     "store.jks", "x.PEM", "history.jsonl"):
            self.write(name, "x")
        self.commit("names")
        self.git(self.repo, "rm", "-q", "x.PEM")
        self.commit("delete one")

        result = self.audit()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.flagged(result), sorted([
            ".bash_history", ".env", ".git-credentials", ".htpasswd",
            ".netrc", ".zsh_history", "auth.p8", "history.jsonl",
            "id_ed25519", "infra.tfstate", "key.ppk", "merged.pem",
            "store.jks", "vault.kdbx", "vpn.ovpn", "x.PEM"]))

    def test_uppercase_env_and_untracked_names_are_checked(self):
        self.commit("base")
        self.write(".ENV", "x")

        self.assertEqual(self.flagged(self.audit()), [".ENV"])

    def test_lookalike_names_are_not_flagged(self):
        for name in ("secretsanta.txt", ".env.example", ".env.sample",
                     "credentials.md", "id_rsa.pub"):
            self.write(name, "x")
        self.commit("lookalikes")

        result = self.audit()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.flagged(result), ["none"])


class ReachTests(AuditCase):
    def test_reflog_only_commit_is_reported(self):
        self.commit("base")
        self.write("gone.txt", "/" + "Users/fixture/gone")
        self.commit("later dropped")
        self.git(self.repo, "reset", "-q", "--hard", "HEAD~1")

        result = self.audit()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("NOT SCANNED: 1 commit(s) reachable only from a reflog",
                      result.stdout)

    def test_submodule_is_reported(self):
        inner = self.root / "inner"
        self.init(inner)
        self.write("x.txt", "x", repo=inner)
        self.commit("inner", repo=inner)
        self.commit("base")
        self.git(self.repo, "-c", "protocol.file.allow=always", "submodule",
                 "add", "-q", inner.as_uri(), "sub")
        self.commit("add submodule")

        result = self.audit()

        self.assertIn("NOT SCANNED: submodule content at 1 path(s)", result.stdout)

    def test_bare_repository_is_audited(self):
        self.write("a.txt", "/" + "Users/fixture/a")
        self.commit("add")
        bare = self.root / "bare.git"
        self.git(self.root, "clone", "-q", "--bare", str(self.repo), str(bare))

        result = self.audit("-C", str(bare))

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.rows(result.stdout)["unix_home_path"][1], 1)
        self.assertIn("bare repository", result.stdout)

    def test_empty_repository_still_scans_the_working_tree(self):
        self.write("draft.txt", "/" + "Users/fixture/draft")

        result = self.audit()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("commits: 0", result.stdout)
        self.assertEqual(self.rows(result.stdout)["unix_home_path"][6], 1)


class ArgumentTests(AuditCase):
    def setUp(self):
        super().setUp()
        self.write("a.txt", "marker-one -dash-marker")
        self.commit("add")

    def test_custom_pattern_counts_and_accepts_posix_classes(self):
        result = self.audit("-p", "marker-[[:alpha:]]+")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.rows(result.stdout)["custom"][1], 1)

    def test_pattern_starting_with_a_dash_is_a_pattern(self):
        result = self.audit("-p", "-dash-marker")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.rows(result.stdout)["custom"][1], 1)

    def test_unusable_arguments_exit_2(self):
        cases = {
            "invalid pattern": ("-p", "a("),
            "empty pattern": ("-p", ""),
            "missing path": ("-C",),
            "unknown flag": ("--nope",),
            "not a repository": ("-C", str(self.root)),
        }
        for label, args in cases.items():
            with self.subTest(label):
                result = self.audit(*args)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertNotIn("RESULT", result.stdout)

    def test_shallow_clone_is_refused(self):
        self.write("b.txt", "b")
        self.commit("second")
        shallow = self.root / "shallow"
        self.git(self.root, "clone", "-q", "--depth", "1", self.repo.as_uri(),
                 str(shallow))

        result = self.audit("-C", str(shallow))

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("shallow", result.stderr)

    def test_help_prints_usage_and_exits_0(self):
        result = self.audit("--help")

        self.assertEqual(result.returncode, 0)
        self.assertIn("Usage:", result.stdout)
        self.assertIn("Exit: 0", result.stdout)


if __name__ == "__main__":
    unittest.main()
