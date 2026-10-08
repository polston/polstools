#!/usr/bin/env python3
"""repo-privacy-audit -- read-only sweep of every place a git repository can
hold private data. Reports COUNTS AND LOCATIONS ONLY; it never prints a matched
value, because its output lands in a CI log or an agent transcript.

Usage:
  repo-privacy-audit [-C <repo>] [-p <pattern>]... [-k] [-v]

  -C  repository to audit (default: current directory)
  -p  additional pattern to sweep for; repeatable. Python `re` syntax, matched
      per line; POSIX bracket classes such as [[:space:]] are accepted. Pass
      the identifiers you care about here rather than committing them -- a
      pattern written into a tracked file embeds the thing it hunts.
  -k  also sweep for secret-ish assignments (password=, api_key: ...).
      Off by default: config-handling code matches it legitimately.
  -v  list where each category hit: repository paths and commit ids. A path
      whose own text matches a category is withheld, because printing the
      name would carry the value into the log.

Places, one column each:
  msgs     commit message lines
  trees    distinct files, across every commit's tree, whose content matches
  patches  patch-hunk lines, added AND removed
  tags     tag message lines
  idents   author, committer and tagger names
  names    every path that ever existed in history, the index or the working
           tree, swept as text
  work     index and working-tree files, tracked or untracked-but-not-ignored
Plus: sensitive filenames ever present, and the largest blobs.

Accepted, not findings: author, committer and tagger addresses, and email in a
well-formed Co-authored-by trailer (key, name, one <address>, in the message's
last paragraph). Both are counted on their own line.

Not looked at, and said so when present: commits reachable only from a reflog,
submodule content, embedded repositories. Ignored files are never read. A
shallow clone is refused: its history is not there to audit.

Exit: 0 = every category read zero. 1 = at least one hit. 2 = could not run.
"""

import bisect
import os
import re
import shutil
import subprocess
import sys
import threading

# --- Categories ---------------------------------------------------------------
# Kept generic: no person, host, or project is named in this file, and every
# literal that would match its own category is split so this file cannot trip
# the audit it implements.
#
# Each category has a PRECISE pattern, matched against one line, and TRIGGERS:
# plain lowercase literals searched over the lowercased bytes of a whole file.
# Only lines holding a trigger reach the precise pattern. That keeps the cost
# of a pass near one literal scan per trigger instead of one backtracking
# regex per byte, so adding a category costs little.

def lit(*words):
    """Triggers that are plain lowercase literals."""
    return tuple(re.escape(w) for w in words)


# The lookbehind lets a match start only at the head of a run of local-part
# characters, and the domain is bounded, so one line of any length costs a
# linear scan rather than one per character.
_EMAIL = (r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@"
          r"[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,}")

_CREDENTIAL = "|".join((
    r"gh[pousr]_[A-Za-z0-9]{36,255}",
    r"github_pat_[A-Za-z0-9_]{22,255}",
    r"\b(?:AKIA|ASIA|ABIA|ACCA)[A-Z0-9]{16}\b",
    r"(?i:aws_?secret_?access_?key)[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9/+=]{40}",
    r"\bsk-ant-[A-Za-z0-9_-]{20,}",
    # The digit lookahead is bounded: unbounded, every "sk-" start on a long
    # line rescanned the rest of the line.
    r"\bsk-(?:proj-|svcacct-|admin-)?(?=[A-Za-z_-]{0,255}[0-9])"
    r"[A-Za-z0-9_-]{20,}",
    r"\bxox[abprse]-[A-Za-z0-9-]{10,}",
    r"hooks\.slack\.com/services/T[A-Za-z0-9]+/",
    "-----BEGIN[ A-Z0-9]*" "PRIVATE " "KEY(?: BLOCK)?-----",
    r"\bAIza[0-9A-Za-z_-]{35}",
    r"\b[rs]k_live_[0-9A-Za-z]{20,}",
    r"\bnpm_[A-Za-z0-9]{36}\b",
    r"\bglpat-[A-Za-z0-9_-]{20,}",
    r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
    r"(?i:authorization)[\"']?\s*[:=]\s*[\"']?(?i:bearer|basic|token)\s+"
    r"[A-Za-z0-9._~+/=-]{12,}",
    r"(?i:\bbearer)\s+[A-Za-z0-9._~+/-]{20,}",
    r"\b[a-z][a-z0-9+.-]{0,30}://[^\s/:@\"'<>${}]+:[^\s/@\"'<>${}]+@[A-Za-z0-9.-]+",
))
_CREDENTIAL_TRIGGERS = lit(
    "ghp_", "gho_", "ghu_", "ghs_", "ghr_", "github_pat_", "akia", "asia",
    "abia", "acca", "sk-", "xox", "hooks.slack", "private " "key", "aiza",
    "k_live_", "npm_", "glpat-", "eyj", "authorization", "bearer", "://",
    "aws",
)

_SEP = r"(?:\\|/|%5[Cc]|%2[Ff])+"
_WINDOWS_HOME = (
    r"\b[A-Za-z](?::|%3[Aa])" + _SEP
    + r"(?i:Users|home|Documents and Settings)" + _SEP + r"[A-Za-z0-9_.-]+"
)
# Not after a path segment (a repository directory named "home" is not a home
# directory) and not after a drive letter, which windows_home_path counts.
_NOT_MID_PATH = r"(?<![A-Za-z0-9_.-])(?<!(?<![A-Za-z0-9])[A-Za-z]:)"
_UNIX_HOME = "|".join((
    _NOT_MID_PATH + r"/(?:home|Users)/[A-Za-z0-9_.-]+",
    _NOT_MID_PATH + r"/root/[A-Za-z0-9_.-]+",
    r"(?<![A-Za-z0-9_.-])/[A-Za-z]/(?:Users|home)/[A-Za-z0-9_.-]+",
    r"/mnt/[A-Za-z]/(?:Users|home)/[A-Za-z0-9_.-]+",
))
_PRIVATE_IP = "|".join((
    r"(?<![0-9.])(?:10\.[0-9]{1,3}|192\.168|172\.(?:1[6-9]|2[0-9]|3[01])"
    r"|169\.254|100\.(?:6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7]))"
    r"\.[0-9]{1,3}\.[0-9]{1,3}(?!\.?[0-9])",
    r"(?<![0-9A-Za-z:])(?i:f[cd][0-9a-f]{2}|fe[89ab][0-9a-f])"
    r"(?::(?i:[0-9a-f]{0,4})){2,7}(?![0-9A-Za-z:])",
))
_MAC = "|".join((
    r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f:])",
    r"(?<![0-9A-Fa-f-])(?:[0-9A-Fa-f]{2}-){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f-])",
    r"(?<![0-9A-Fa-f.])[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}"
    r"(?![0-9A-Fa-f.])",
))
# A dotted name under a private-use suffix. `self.x.local` is attribute access
# in code, not a host, so a chain starting at a method receiver is skipped.
_HOSTNAME = (
    r"(?<![A-Za-z0-9_.-])(?!(?:self|this|cls|threading)\.)"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"(?i:local|lan|internal|localdomain|home\.arpa)"
    r"(?![A-Za-z0-9_(-]|\.[A-Za-z0-9])"
)
# Dollar amounts only. What someone spent or is billed is confidential, and identity-shaped patterns
# are structurally blind to it. Thresholded at a thousand so small published
# per-token list rates do not bury a four-figure total.
_MONEY = r"\$[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]{2})?|\$[0-9]{4,}(?:\.[0-9]{2})?"
# Account and plan state pasted out of a harness config. Field names, split so
# this file cannot match itself; identical to retro.py's account_billing_field.
_BILLING = (
    r"(hasExtra" r"Usage[A-Za-z0-9_]*|subscription" r"Type|billing" r"Type"
    r"|organizationRateLimit" r"Tier|userRateLimit" r"Tier|seat" r"Tier)"
)
# Session, account and device ids. The nil UUID is a placeholder, not an id.
_UUID = (
    r"(?<![0-9A-Fa-f])(?!0{8}-0{4}-0{4}-0{4}-0{12})"
    r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}"
    r"-[0-9A-Fa-f]{12}(?![0-9A-Fa-f])"
)
_SECRETISH = (
    r"(?i:password|passwd|secret|api[_-]?key|access[_-]?token|bearer|webhook)"
    r"\s*[:=]"
)

DEFAULT_CATEGORIES = (
    ("email", _EMAIL, lit("@")),
    ("credential", _CREDENTIAL, _CREDENTIAL_TRIGGERS),
    ("windows_home_path", _WINDOWS_HOME,
     lit("users", "home", "documents and settings")),
    ("unix_home_path", _UNIX_HOME, lit("/home/", "/users/", "/root/")),
    ("private_ip", _PRIVATE_IP,
     lit("10.", "192.168", "172.", "169.254", "100.", "fc", "fd", "fe")),
    ("mac_address", _MAC,
     (r":[0-9a-f]{2}:", r"-[0-9a-f]{2}-", r"\.[0-9a-f]{4}\.")),
    ("internal_hostname", _HOSTNAME,
     lit(".local", ".lan", ".internal", ".home.arpa")),
    ("uuid", _UUID, (r"-[0-9a-f]{4}-[0-9a-f]{4}-",)),
    ("money_amount", _MONEY, lit("$")),
    ("account_billing_field", _BILLING,
     lit("hasextra" "usage", "subscription" "type", "billing" "type",
      "ratelimit" "tier", "seat" "tier")),
)
SECRETISH = ("secretish_assignment", _SECRETISH,
             lit("password", "passwd", "secret", "api", "access", "bearer",
              "webhook"))

SENSITIVE_NAME = re.compile(
    r"(?:^|/)(?:"
    r"\.env(?:\.(?!(?:example|sample|template|dist|defaults?)$)[^/]+)?"
    r"|\.?netrc|_netrc|\.git-credentials|\.htpasswd|\.pgpass|\.npmrc|\.pypirc"
    r"|\.dockercfg|\.histfile|fish_history"
    r"|\.(?:bash|zsh|sh|python|psql|mysql|node_repl|sqlite)_history"
    r"|id_(?:rsa|dsa|ecdsa|ed25519)(?:_sk)?"
    r"|credentials(?:\.(?:json|ya?ml|toml|xml|ini|csv))?"
    r"|secrets(?:\.(?:json|ya?ml|toml|env|ini|txt))?"
    r"|[^/]*\.(?:pem|key|p12|pfx|p8|jks|keystore|kdbx|ovpn|ppk|tfstate"
    r"|tfstate\.backup|jsonl|sqlite3?|db)"
    r")$",
    re.IGNORECASE,
)

TRAILER = re.compile(
    r"^co-authored-by:[ \t]*[^<>@\s][^<>@\n]*<" + _EMAIL + r">[ \t]*$",
    re.IGNORECASE,
)

POSIX_CLASSES = {
    "[:alpha:]": "A-Za-z", "[:digit:]": "0-9", "[:alnum:]": "A-Za-z0-9",
    "[:upper:]": "A-Z", "[:lower:]": "a-z", "[:space:]": r"\s",
    "[:blank:]": r" \t", "[:xdigit:]": "0-9A-Fa-f",
    "[:punct:]": r"!-/:-@\[-`{-~",
}

COLUMNS = ("msgs", "trees", "patches", "tags", "idents", "names", "work")
BIG_BLOB = 262144


class CannotRun(Exception):
    pass


class Category:
    def __init__(self, name, precise, triggers=None):
        self.name = name
        self.precise = re.compile(precise)
        self.triggers = None
        if triggers is not None:
            self.triggers = [re.compile(t.encode()) for t in triggers]


def scan(data, categories):
    """Map category name -> set of byte offsets of the lines in `data` that
    match it. Lines are newline-separated; offsets identify them uniquely."""
    low = data.lower()  # bytes.lower() is ASCII-only, so offsets are kept
    found = {}
    for cat in categories:
        lines = set()
        if cat.triggers is None:
            start = 0
            for raw in data.split(b"\n"):
                if cat.precise.search(raw.decode("utf-8", "replace")):
                    lines.add(start)
                start += len(raw) + 1
        else:
            tried = set()
            for trigger in cat.triggers:
                # Resume after the line just tried: a long line holding many
                # trigger hits is looked up once, not once per hit.
                pos = 0
                while True:
                    m = trigger.search(low, pos)
                    if m is None:
                        break
                    a = data.rfind(b"\n", 0, m.start()) + 1
                    b = data.find(b"\n", m.start())
                    pos = b + 1 if b >= 0 else len(data) + 1
                    if a in tried:
                        continue
                    tried.add(a)
                    line = data[a:b if b >= 0 else len(data)]
                    if cat.precise.search(line.decode("utf-8", "replace")):
                        lines.add(a)
        if lines:
            found[cat.name] = lines
    return found


def usage_error(message):
    sys.stderr.write("repo-privacy-audit: %s\n" % message)
    sys.exit(2)


def parse_args(argv):
    opts = {"repo": ".", "extra": [], "keywords": False, "verbose": False}
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-C", "-p"):
            if i + 1 >= len(argv) or argv[i + 1] == "":
                usage_error("%s needs a non-empty argument" % arg)
            if arg == "-C":
                opts["repo"] = argv[i + 1]
            else:
                opts["extra"].append(argv[i + 1])
            i += 2
            continue
        if arg == "-k":
            opts["keywords"] = True
        elif arg == "-v":
            opts["verbose"] = True
        elif arg in ("-h", "--help"):
            sys.stdout.write(__doc__)
            sys.exit(0)
        else:
            usage_error("unknown argument: %s" % arg)
        i += 1
    return opts


def custom_pattern(patterns):
    parts = []
    for pattern in patterns:
        for posix, python in POSIX_CLASSES.items():
            pattern = pattern.replace(posix, python)
        try:
            re.compile(pattern)
        except re.error as exc:
            usage_error("-p pattern is not a valid regular expression: %s" % exc)
        parts.append("(?:%s)" % pattern)
    return "|".join(parts)


class Git:
    def __init__(self, repo):
        self.repo = repo
        self.env = dict(os.environ)
        self.env["GIT_LITERAL_PATHSPECS"] = "1"
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
            self.env.pop(name, None)
        # Resolve git the way a shell does. Windows process creation looks
        # only for git.exe, and in its own system directories before PATH,
        # so a git.cmd earlier on PATH would otherwise be passed over.
        self.git = shutil.which("git", path=self.env.get("PATH")) or "git"
        state = self._probe()
        if state is None:
            # A restricted sandbox can make the global config unreadable.
            self.env.setdefault("GIT_CONFIG_GLOBAL", os.devnull)
            state = self._probe()
            if state is None:
                raise CannotRun("not a git repository: %s" % repo)
        self.bare, self.shallow = (v == b"true" for v in state.split()[:2])

    def _probe(self):
        try:
            proc = subprocess.run(
                [self.git, "-C", self.repo, "rev-parse", "--is-bare-repository",
                 "--is-shallow-repository"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=self.env)
        except OSError:
            raise CannotRun("git not found")
        return proc.stdout if proc.returncode == 0 else None

    def run(self, *args, stdin=None):
        proc = subprocess.run(
            [self.git, "-C", self.repo, "-c", "core.quotePath=false", *args],
            input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=self.env)
        if proc.returncode != 0:
            # Not git's own message: a partial clone's lazy fetch error can
            # carry the remote URL, credentials included, into a CI log.
            raise CannotRun("git %s failed (exit status %d)" % (
                args[0], proc.returncode))
        return proc.stdout

    def blobs(self, shas):
        """Yield (sha, bytes) for each blob from one `cat-file --batch`."""
        if not shas:
            return
        proc = subprocess.Popen(
            [self.git, "-C", self.repo, "cat-file", "--batch"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=self.env)

        def feed():
            try:
                proc.stdin.write("".join(s + "\n" for s in shas).encode())
            finally:
                proc.stdin.close()

        feeder = threading.Thread(target=feed)
        feeder.start()
        for sha in shas:
            header = proc.stdout.readline().split()
            if len(header) < 3 or header[1] == b"missing":
                raise CannotRun("object %s could not be read" % sha)
            data = proc.stdout.read(int(header[2]))
            proc.stdout.read(1)
            yield sha, data
        feeder.join()
        if proc.wait() != 0:
            raise CannotRun("git cat-file failed")


def searchable(data):
    """Searchable bytes of a file. Text is used as-is. Anything with a NUL
    byte -- a binary, or UTF-16 text -- is read the way `strings` reads it:
    runs of printable ASCII, and of UTF-16 in either byte order."""
    if b"\0" not in data[:8000]:
        return data, False
    runs = re.findall(rb"[\x20-\x7e\t]{4,}", data)
    for pattern in (rb"(?:[\x20-\x7e\t]\x00){4,}", rb"(?:\x00[\x20-\x7e\t]){4,}"):
        runs.extend(m.replace(b"\x00", b"") for m in re.findall(pattern, data))
    return b"\n".join(runs), True


def history_paths(git):
    """Every path that ever existed, and which of them were gitlinks. Each
    commit is diffed against every parent (-m) with renames off, so a path is
    reported under each name it had, including one introduced by a merge."""
    raw = git.run("log", "--all", "--format=", "--raw", "--root", "-m",
                  "--no-renames", "--no-abbrev", "-z")
    paths, gitlinks = set(), set()
    tokens = raw.split(b"\0")
    i = 0
    while i < len(tokens) - 1:
        token = tokens[i].lstrip(b"\n")
        if token.startswith(b":"):
            path = tokens[i + 1].decode("utf-8", "replace")
            paths.add(path)
            if b"160000" in token[1:].split()[:2]:
                gitlinks.add(path)
            i += 2
        else:
            i += 1
    return paths, gitlinks


def patch_hunks(git):
    """Hunk-body lines of every commit's patch, joined, plus (offset, path)
    marks. Diff and file headers are excluded: they hold repository paths,
    which the names column sweeps separately."""
    data = git.run("log", "--all", "--format=", "-p", "--no-color",
                   "--no-ext-diff", "--no-textconv")
    body, marks, size = [], [], 0
    path, in_hunk = "", False
    for line in data.split(b"\n"):
        if line.startswith(b"diff --git "):
            in_hunk = False
            continue
        if not in_hunk:
            if line.startswith(b"+++ ") and line != b"+++ /dev/null":
                path = line[6:].decode("utf-8", "replace")
            elif line.startswith(b"--- ") and line != b"--- /dev/null":
                path = line[6:].decode("utf-8", "replace")
            elif line.startswith(b"@@ "):
                in_hunk = True
                marks.append((size, path))
            continue
        if line.startswith(b"@@ ") or line == b"\\ No newline at end of file":
            continue
        body.append(line)
        size += len(line) + 1
    return b"\n".join(body), marks


def main(argv):
    # A path that is not valid UTF-8 is decoded with U+FFFD; a Windows console
    # code page cannot encode that, and the audit must not die on a name.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    opts = parse_args(argv)
    specs = list(DEFAULT_CATEGORIES)
    if opts["keywords"]:
        specs.append(SECRETISH)
    categories = [Category(*spec) for spec in specs]
    if opts["extra"]:
        categories.append(Category("custom", custom_pattern(opts["extra"])))
    names = [c.name for c in categories]
    email_re = re.compile(_EMAIL)

    git = Git(opts["repo"])
    if git.shallow:
        raise CannotRun("shallow clone: history before the shallow boundary "
                        "is not present to audit; run git fetch --unshallow")
    bare = git.bare
    reflog_only = len(git.run("rev-list", "--reflog", "--not", "--all").split())

    # hits[category][column] is a set: line keys for line-counted places,
    # paths for file-counted ones, so a file in many commits counts once.
    hits = {n: {col: set() for col in COLUMNS} for n in names}
    where = {n: set() for n in names}

    def add(column, found, label):
        for name, lines in found.items():
            hits[name][column].update((label, line) for line in lines)
            where[name].add(label)

    def add_file(column, found, label):
        for name in found:
            hits[name][column].add(label)
            where[name].add(label)

    # 1. commit messages (and, from the same read, identities for place 5);
    # a well-formed trailer is accepted for email only
    accepted_trailers = 0
    commits = 0
    idents = []
    log = git.run("log", "--all", "-z",
                  "--format=%h%x00%an <%ae>%n%cn <%ce>%x00%B").split(b"\0")
    for k in range(0, len(log) - 2, 3):
        short = log[k].lstrip(b"\n")
        commits += 1
        idents.append(log[k + 1])
        body = log[k + 2].rstrip(b"\n")
        found = scan(body, categories)
        if "email" in found:
            # A subject line is never a trailer, so a body with no blank line
            # has no trailer paragraph at all.
            last_para = (body.rfind(b"\n\n") + 2 if b"\n\n" in body
                         else len(body) + 1)
            for offset in sorted(found["email"]):
                end = body.find(b"\n", offset)
                line = body[offset:end if end >= 0 else len(body)]
                text = line.decode("utf-8", "replace")
                if (offset >= last_para and TRAILER.match(text)
                        and len(email_re.findall(text)) == 1):
                    accepted_trailers += 1
                    found["email"].discard(offset)
            if not found["email"]:
                del found["email"]
        add("msgs", found, "commit " + short.decode())

    # The index's blobs are read in the same pass as history's; a blob that
    # is both is scanned once.
    index_blobs, gitlinks = {}, set()
    if not bare:
        for entry in git.run("ls-files", "-s", "-z").split(b"\0"):
            if not entry:
                continue
            meta, _, path = entry.partition(b"\t")
            mode, sha = meta.split()[:2]
            path = path.decode("utf-8", "replace")
            if mode == b"160000":
                gitlinks.add(path)
            else:
                index_blobs.setdefault(sha.decode(), []).append(path)

    # 2. every commit's tree: each distinct blob read once, all categories
    blob_path, sizes = {}, {}
    listing = git.run("rev-list", "--objects", "--all")
    checked = git.run(
        "cat-file", "--batch-check=%(objectname) %(objecttype) "
        "%(objectsize) %(rest)", stdin=listing)
    for line in checked.decode("utf-8", "replace").splitlines():
        parts = line.split(" ", 3)
        if len(parts) >= 3 and parts[1] == "blob":
            blob_path[parts[0]] = parts[3] if len(parts) > 3 else parts[0]
            sizes[parts[0]] = int(parts[2])
    binaries = 0
    work_hits = {n: set() for n in names}
    wanted = list(blob_path) + [s for s in index_blobs if s not in blob_path]
    for sha, data in git.blobs(wanted):
        data, binary = searchable(data)
        found = scan(data, categories)
        if sha in blob_path:
            binaries += binary
            add_file("trees", found, blob_path[sha])
        for name in found:
            work_hits[name].update(index_blobs.get(sha, ()))

    # 3. patch hunk bodies, added and removed lines
    body, marks = patch_hunks(git)
    offsets = [m[0] for m in marks]
    for name, lines in scan(body, categories).items():
        hits[name]["patches"].update(lines)
        for offset in lines:
            where[name].add(marks[bisect.bisect_right(offsets, offset) - 1][1])

    # 4. tag messages (and tagger identities for place 5)
    tags = git.run("for-each-ref", "refs/tags", "--format=%(refname:short)%00"
                   "%(taggername) %(taggeremail)%00%(contents)%00")
    items = tags.split(b"\0")
    for k in range(0, len(items) - 2, 3):
        tag = items[k].lstrip(b"\n").decode("utf-8", "replace")
        idents.append(items[k + 1])
        add("tags", scan(items[k + 2], categories), "tag " + tag)

    # 5. identities: names are swept; addresses are publication metadata
    unique = set()
    for record in idents:
        unique.update(record.decode("utf-8", "replace").splitlines())
    unique = sorted(u for u in unique if u.strip())
    addresses = set()
    for ident in unique:
        addresses.update(a.lower() for a in email_re.findall(ident))
    stripped = "\n".join(email_re.sub("", i) for i in unique).encode("utf-8")
    found = scan(stripped, categories)
    found.pop("email", None)
    add("idents", found, "identity record")

    # 6. names, the index, and the working tree
    paths, history_links = history_paths(git)
    gitlinks |= history_links
    work_paths, embedded, unreadable = [], 0, 0
    if not bare:
        top = git.run("rev-parse", "--show-toplevel").decode().strip()
        listed = git.run("ls-files", "-z", "--cached", "--others",
                         "--exclude-standard").decode("utf-8", "replace")
        for path in sorted(set(p for p in listed.split("\0") if p)):
            if path.endswith("/"):
                embedded += 1
            elif path not in gitlinks:
                work_paths.append(path)
        for path in work_paths:
            full = os.path.join(top, path)
            try:
                if os.path.islink(full):
                    data = os.readlink(full).encode("utf-8", "replace")
                elif os.path.isfile(full):
                    with open(full, "rb") as handle:
                        data = handle.read()
                else:
                    continue
            except OSError:
                unreadable += 1
                continue
            for name in scan(searchable(data)[0], categories):
                work_hits[name].add(path)
        for name, found_paths in work_hits.items():
            hits[name]["work"].update(found_paths)
            where[name].update(found_paths)
        paths.update(work_paths)
    for path in sorted(paths):
        add_file("names", scan(path.encode("utf-8"), categories), path)

    # --- Report ---------------------------------------------------------------
    def shown(location):
        for cat in categories:
            if cat.precise.search(location):
                return "<name withheld: it matches %s>" % cat.name
        # A name is attacker-controlled bytes: no escape sequence reaches the
        # terminal.
        return re.sub(r"[\x00-\x1f\x7f-\x9f]", "?", location)

    print("repo-privacy-audit")
    print("commits: %d   blobs: %d   index and working-tree files: %d"
          % (commits, len(blob_path), len(work_paths)))
    print()
    row = "%-22s" + " %7s" * len(COLUMNS)
    print(row % (("category",) + COLUMNS))
    print(row % (("-" * 22,) + tuple("-" * len(c) for c in COLUMNS)))
    total = 0
    for name in names:
        counts = tuple(len(hits[name][col]) for col in COLUMNS)
        total += 1 if sum(counts) else 0
        print(row % ((name,) + counts))
        if opts["verbose"] and sum(counts):
            for location in sorted(set(shown(w) for w in where[name]))[:15]:
                print("      " + location)
    print()
    print("accepted, not findings: %d author/committer/tagger address(es); "
          "%d co-author trailer(s)." % (len(addresses), accepted_trailers))
    print("  Published as identity metadata by this tool's policy. If this")
    print("  repository should not publish them, treat them as findings.")

    print()
    print("--- sensitive filenames ever present (history, index, working tree) ---")
    flagged = sorted(set(shown(p) for p in paths if SENSITIVE_NAME.search(p)))
    if flagged:
        total += 1
        for path in flagged[:20]:
            print("      " + path)
        if len(flagged) > 20:
            print("      ... and %d more" % (len(flagged) - 20))
    else:
        print("      none")

    print()
    print("--- largest blobs ever committed (harvested data is usually the "
          "biggest thing) ---")
    big = sorted(((sizes[s], blob_path[s]) for s in sizes if sizes[s] > BIG_BLOB),
                 reverse=True)[:10]
    for size, path in big:
        print("      %6.1f MB  %s" % (size / 1048576.0, shown(path)))
    if not big:
        print("      none over 256 KB")

    notes = []
    if binaries:
        notes.append("%d binary or UTF-16 blob(s) were swept as extracted text "
                     "runs." % binaries)
    if reflog_only:
        notes.append("NOT SCANNED: %d commit(s) reachable only from a reflog. "
                     "They are not pushed; expire the reflog if a scrub must "
                     "also clear local copies." % reflog_only)
    if gitlinks:
        notes.append("NOT SCANNED: submodule content at %d path(s); audit each "
                     "submodule repository on its own." % len(gitlinks))
    if embedded:
        notes.append("NOT SCANNED: %d embedded repository(ies) in the working "
                     "tree." % embedded)
    if unreadable:
        notes.append("NOT SCANNED: %d working-tree file(s) could not be read."
                     % unreadable)
    if bare:
        notes.append("bare repository: there is no index or working tree.")
    if notes:
        print()
        for note in notes:
            print("note: " + note)

    print()
    if total == 0:
        print("RESULT: every category read zero across all places.")
        return 0
    print("RESULT: %d categor%s with hits." % (total, "y" if total == 1 else "ies"))
    print("A match is not a leak. Open the files and read them before concluding")
    print("anything -- a filename is not evidence.")
    print()
    print("Reminder: a remote copy is not retracted by rewriting local history.")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except CannotRun as exc:
        sys.stderr.write("repo-privacy-audit: %s\n" % exc)
        sys.exit(2)
