#!/usr/bin/env python3
"""retro — derive workflow-friction metrics from Claude Code and Codex session
history.

Seven subcommands:

    extract    walk session transcripts, append one metrics row per session
    pack       build an evidence pack (trends + redacted moments) for a window
    skills     which installed skills actually fire
    subagents  mechanical failures in subagent transcripts, over a window
    label      sample turns and retry candidates for hand labelling, and report
               precision, recall and a threshold sweep from the marked file
    effect     metrics before and after a date, to check whether an edit moved
               the thing it was aimed at
    rules      whether the standing instructions are versioned at all, and
               whether their edits are being committed as they are made

Message text leaves this script in exactly two places: the `moments` section of
a pack, and the labelling file written by `label`. Both pass through redact()
first, and both land in the work directory, which must sit outside every git
repository -- redact() strips machine and credential shapes, not the names and
paths of whatever the sessions were about.

The ledger is not "counts only", though it long claimed to be: a row carries the
working directory it was measured from. That is why the work directory is placed
outside repositories rather than merely kept out of one. Rows are labelled by
harness and population; three counters are declared ineligible on Codex rows
rather than reported as zeros.

Stdlib only. Every field access is guarded: transcript shape varies by CLI
version, and a KeyError partway through a 5GB corpus loses the whole run.

Exit codes match the sibling scripts in plugins/p/bin:
    0  ran clean, nothing flagged
    1  ran clean, something was flagged (a transcript that would not read,
       friction in the window, dormant skills)
    2  could not run (no session directory at any root, no ledger)
"""

EXIT_CLEAN, EXIT_FLAGGED, EXIT_CANNOT_RUN = 0, 1, 2

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

HOME = Path.home()
CLAUDE_DIR = HOME / ".claude"

# The two harnesses this tool ingests, Claude first (spec D1.3 dedup order).
HARNESSES = ("claude", "codex")


def claude_projects_dir():
    """The Claude transcript root, resolved at call time so tests can
    inject it and so CLAUDE_CONFIG_DIR is honoured — retiring the
    documented asymmetry with stopped-promises (spec D1.1)."""
    config = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(config) if config else HOME / ".claude") / "projects"


def codex_home_dir():
    """The Codex home directory, resolved at call time so tests can inject
    it and so CODEX_HOME is honoured."""
    home = os.environ.get("CODEX_HOME")
    return Path(home) if home else HOME / ".codex"


def codex_sessions_dir():
    return codex_home_dir() / "sessions"


_TRANSCRIPT_ROOT_DIRS = {"claude": claude_projects_dir, "codex": codex_sessions_dir}


def transcript_roots():
    """(harness, root) pairs, Claude first: a file reachable from two
    roots is owned by the first (spec D1.3)."""
    return tuple((h, _TRANSCRIPT_ROOT_DIRS[h]()) for h in HARNESSES)


WORK_DIR = Path(os.environ.get("RETRO_HOME", HOME / ".retro"))
METRICS_FILE = WORK_DIR / "metrics.jsonl"
STATE_FILE = WORK_DIR / "state.json"
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from retro_eval.catalog import ensure_rubric_use, load_rubric_catalogue

RUBRICS_FILE = PLUGIN_ROOT / "rubrics" / "rubrics.json"
LEGACY_TURN_RUBRIC = "turn_friction_legacy"
LEGACY_TURN_COUNTERS = frozenset(("correction_candidates", "interrupts"))


@lru_cache(maxsize=None)
def legacy_turn_labels_allow(use):
    """Resolve the legacy classifier's allowed output roles from catalogue data."""
    catalogue = load_rubric_catalogue(RUBRICS_FILE)
    rubric = next((item for item in catalogue.rubrics
                   if item.id == LEGACY_TURN_RUBRIC), None)
    if rubric is None:
        return False
    try:
        ensure_rubric_use(rubric, use)
    except ValueError:
        return False
    return True

# --- Tuning constants ------------------------------------------------------
# These define what counts as friction. They are the knobs worth arguing about;
# everything else in this file is bookkeeping.

# A user prompt shorter than this, arriving right after a long assistant turn,
# reads as a correction ("no", "stop", "I said X") rather than a new request.
CORRECTION_MAX_CHARS = 200
# ...and the assistant turn it follows has to have been substantial, or every
# short back-and-forth in a fast exchange scores as a correction.
CORRECTION_MIN_PRIOR_CHARS = 200

# A short reply that only agrees is the process working, not friction. The whole
# reply has to be one of these, ignoring case and trailing punctuation: "yes" is
# an approval, "yes, but drop the cache" is a correction.
#
# A seed list of unambiguous whole-reply affirmatives, deliberately short. The
# `label` subcommand exists to settle this list from marked turns rather than
# from a guess - add a phrase when the marks show it is being missed.
APPROVAL_PHRASES = (
    "yes", "yep", "yeah", "yup", "ok", "okay", "k", "kk", "sure", "correct",
    "agreed", "go ahead", "go for it", "go", "do it", "sounds good",
    "looks good", "lgtm", "seems right", "seems ok", "seems okay", "seems good",
    "lets go", "let's go", "perfect", "exactly", "approved", "please do",
    "ship it", "fine", "yes please",
)
# A negation anywhere means the reply is doing more than agreeing, so the
# leading-affirmative rule below must not claim it: "sure, but that is wrong" is
# a correction wearing an approval's first word.
_NEGATION = re.compile(
    r"(no|not|n't|never|stop|wrong|instead|revert|undo|but|however|except)", re.I)
# A reply may open with a list marker and still be nothing but agreement --
# "1. sure" is an answer to a numbered question, not a new instruction.
_LIST_PREFIX = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")
# Wording that marks a reply as pushing back, wherever it sits in the reply.
# Assembled from 300 hand-marked turns, not from imagination: every entry here
# appeared in a turn a human marked as a correction.
_CORRECTIVE = re.compile(
    r"\b(no|nope|not|isn'?t|aren'?t|doesn'?t|don'?t|didn'?t|can'?t|won'?t|never"
    r"|wrong|stop|instead|revert|undo|disregard|ignore"
    r"|broken|broke|fail(?:s|ed|ing)?|terrible|worse|awful|missing|still|again"
    r"|reword|rewrite|redo|shorter|concise(?:ly)?|simplif"
    r"|why (?:are|did|would|is)|you'?re|are you|do you really)\b", re.I)
# A reply longer than this is a fresh request, not a reaction to the turn before.
CANDIDATE_MAX_CHARS = 600
_APPROVAL_TAIL = re.compile(r"[\s.!,]+$")
_APPROVAL = re.compile(
    r"^(?:%s)$" % "|".join(re.escape(p) for p in APPROVAL_PHRASES), re.I)

# Row schema. Every counter here is a column. Bump SCHEMA_VERSION whenever this
# list changes OR a counter's definition changes, because a ledger holding two
# definitions at once is worse than no ledger: it reports a number belonging to
# neither, and nothing in the output says so. `extract` rebuilds on a mismatch
# rather than trusting prose to prevent it.
SCHEMA_VERSION = 7
COUNTERS = ["turns", "user_prompts", "tool_calls", "tool_errors", "repeat_calls",
            "correction_candidates", "approval_turns", "interrupts",
            "permission_mode_changes", "queued_prompts", "skill_runs"]


def row_harness(row):
    """A row with no harness field predates schema 7 and defaults to claude."""
    return row.get("harness") or "claude"


# How a run answered, in precedence order. One value per row, in the `ending`
# column. The first two are a result delivered; only the last two are the agent
# failing to answer, and `interrupted` is the caller's doing, not the agent's.
ENDINGS = ("structured", "text", "interrupted", "unanswered", "silent")

# The subagent lens: one entry per column, mapping the column to the population
# each signal could have occurred in. A share divides by THIS,
# never by every row. A workspace-guard refusal cannot arise in a run that had
# no isolated workspace, and a schema rejection cannot arise in a run that never
# made a structured-result call. Measured on the corpus: the workspace signals
# were possible in 465 of 1,492 subagent rows, so dividing them by 1,492 states
# them at a third of their real rate. Dividing every signal by every row is the
# same defect this lens was rebuilt to remove, with the sign flipped.
#
# ISOLATED_WORKSPACE means the run worked inside an isolated workspace. Any
# other value is the set of tools that emit the refusal; an empty set means the
# run only had to call some tool.
ISOLATED_WORKSPACE = "isolated-workspace"
SIGNAL_POPULATION = {
    "schema_rejected": frozenset(("StructuredOutput",)),
    "unread_before_write": frozenset(("Write", "Edit")),
    "missing_path_target": frozenset(("Read", "Grep")),
    "search_pattern_rejected": frozenset(("Grep",)),
    "invalid_tool_input": frozenset(),
    "workspace_target_outside": ISOLATED_WORKSPACE,
    "workspace_shape_unverifiable": ISOLATED_WORKSPACE,
}

# Same ledger contract as COUNTERS - each name is a column, and adding one means
# an extract --rebuild - but a separate list, so the pack's trend table and
# per-session line, which iterate COUNTERS, are untouched. Derived from the map
# above rather than written out again, so a column cannot exist with no
# population to divide it by.
#
# Every one was re-earned by counting the corpus, not carried over from an
# earlier attempt whose categories a recount disproved. The measurement and the
# checks each category had to pass are in
# docs/plans/2026-08-20-plan-subagent-lens-rebuild.md.
SUBAGENT_COUNTERS = list(SIGNAL_POPULATION)

# No "abandoned session" counter, deliberately. See the metric-definitions
# section of docs/plans/2026-08-12-retro-design.md for the measurement that
# ruled it out.

# --- Labelling -------------------------------------------------------------
# The sample the thresholds above are argued from. 150 a side is enough to
# separate a precision of 0.9 from one of 0.7 and small enough to mark in a
# sitting.
LABEL_SAMPLE_SIZE = 150
LABEL_SEED = "retro-label-v1"
LABEL_AFTER_CHARS = 600     # of the assistant turn before, for context
LABEL_SAID_CHARS = 400      # of the reply itself
LABEL_INPUT_CHARS = 300     # of a tool input, for a retry candidate
TURN_LABELS = ("interrupt", "question", "approval", "correction", "none")
RETRY_LABELS = ("wasteful", "legitimate")
# Swept against the marks. The top of the reply sweep stays under
# LABEL_SAID_CHARS, or the stored reply would be truncated where the rule reads.
SWEEP_MAX_CHARS = (60, 90, 120, 160, 200, 300)
SWEEP_MIN_PRIOR = (0, 200, 400, 800, 1600)


# --- Redaction -------------------------------------------------------------

@lru_cache(maxsize=1)
def _redaction_patterns():
    """Compile once.

    THREE lists now hold redaction categories and none is a superset:
      - this one,
      - plugins/p/bin/repo-privacy-audit (generic home-path forms this lacks,
        private-range addresses only),
      - plugins/p/bin/stopped-promises.py (adds absolute paths belonging to
        anywhere else, which neither of the other two catch).
    Keep them in step deliberately rather than assuming they agree; the previous
    wording said "the two lists" and was already stale.
    """
    home = str(HOME)
    user = HOME.name
    pats = [
        (re.compile(re.escape(home), re.I), "~"),
        (re.compile(re.escape(home.replace("\\", "/")), re.I), "~"),
        (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"), "<email>"),
        (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "<ip>"),
        (re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b"), "<mac>"),
        (re.compile(r"\b[A-Za-z0-9_-]{32,}\b"), "<long-token>"),
        # Spend and plan state. Kept in step with repo-privacy-audit's
        # money_amount and account_billing_field categories -- a measured
        # spend figure is confidential and is shaped like nothing else here,
        # so every identity pattern above is structurally blind to it.
        (re.compile(r"\$\d{1,3}(,\d{3})+(\.\d{2})?|\$\d{4,}(\.\d{2})?"), "<amount>"),
        # Literals split across adjacent string pieces so this file does not
        # match the pattern it defines; Python rejoins them at parse time.
        # Kept in the subset of regex syntax both grep -E and Python re
        # accept -- no \b (not POSIX-guaranteed) and an explicit character
        # class rather than \w -- so this stays identical to
        # repo-privacy-audit's account_billing_field pattern.
        (re.compile(r"(hasExtra" r"Usage[A-Za-z0-9_]*|subscription" r"Type"
                    r"|billing" r"Type|organizationRateLimit" r"Tier"
                    r"|userRateLimit" r"Tier|seat" r"Tier)"),
         "<billing-field>"),
    ]
    # The account-name rule goes LAST, and the position is load-bearing. Running
    # it first rewrote the name inside the home path, after which neither
    # home-path pattern could ever match: a path came back as drive + Users +
    # placeholder + every directory below it, instead of collapsing to "~".
    # Identity was removed, the directory structure was not.
    if len(user) > 2:
        pats.append((re.compile(r"\b" + re.escape(user) + r"\b", re.I), "<user>"))
    return pats


def redact(text):
    """Strip machine-identifying and credential-shaped values from text.

    Runs before anything is written to a pack. A pack file on disk must already
    be safe to read aloud — redacting at read time would be too late.
    """
    if not text:
        return ""
    for pattern, replacement in _redaction_patterns():
        text = pattern.sub(replacement, text)
    return text


# --- Transcript parsing ----------------------------------------------------

def _content_text(content, block_types, bare_strings=False):
    """The text-bearing pieces of a `content` field, as an unjoined list of
    strings -- callers decide how to filter and join, since the two shapes
    that flatten through here disagree about both.

    A bare string in a content list passes through only when `bare_strings`
    is set (Claude content mixes plain strings and typed blocks; Codex
    content never does). A `tool_result` block, when its type is in
    `block_types`, contributes its own string `content` field instead of a
    `text` key -- the one shape neither format's other block types use.
    """
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    parts = []
    for block in content:
        if bare_strings and isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") in block_types:
            if block.get("type") == "tool_result":
                inner = block.get("content")
                if isinstance(inner, str):
                    parts.append(inner)
            else:
                parts.append(block.get("text") or "")
    return parts


def text_of(message):
    """Flatten a message's content to plain text. Content is a string on some
    records and a list of typed blocks on others."""
    if not isinstance(message, dict):
        return ""
    parts = _content_text(message.get("content"), ("text", "tool_result"),
                          bare_strings=True)
    return "\n".join(p for p in parts if p)


def tool_calls_of(message):
    """Yield (block_id, tool_name, input_signature) for each tool use.

    The id is the only link between a call and the result it produced: a
    tool_result block carries the id and never the name. Attribution by name is
    what the mechanical-failure columns need, because the same refusal text is
    emitted by more than one tool.
    """
    if not isinstance(message, dict):
        return
    content = message.get("content")
    if not isinstance(content, list):
        return
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            yield (block.get("id") or "", block.get("name") or "?",
                   signature(block.get("input")))


_INTERRUPT = re.compile(r"\[request interrupted", re.I)

def signature(tool_input):
    """An exact digest of a tool call's input.

    It used to normalise digits and whitespace away and hash only the first 2KB,
    on the theory that a retry is the same command with a tweaked number. Counted
    against the corpus, that theory cost more than it bought: of 1,387 calls it
    flagged as repeats, 1,157 had genuinely different inputs -- 637 were one file
    read at successive offsets, 314 were updates to a task list. Truncation also
    let two long writes to different paths collide, because sorted keys put the
    content ahead of the path. Exact is both honest and, measured, faster.
    """
    if tool_input is None:
        return ""
    raw = json.dumps(tool_input, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:16]


# --- Mechanical failures ---------------------------------------------------
#
# A failure is counted when the refusal text STARTS the result body and the tool
# that produced it is one that emits that refusal. Both halves were measured:
#
#   - Starts-with, because a command's own output can quote a refusal. One
#     failed result in the subagent corpus is a command that printed a workspace
#     refusal it had read out of a file. A substring test counts it as a real
#     refusal; this rule does not. The anchor is also what keeps successful
#     results out: 73 successful results in the corpus contain one of these
#     texts somewhere - agents read and search transcripts - and exactly 0 begin
#     with one.
#   - Tool-attributed, because the same text means a different mistake
#     depending on which tool emitted it: the unread-file refusal comes from
#     Write and from Edit, the missing-path refusal from Read and from Grep.
#
# The is_error gate below is NOT what makes these specific - measured, it
# excludes nothing the anchor has not already excluded. It is here because it is
# the harness's own record that the call failed, and a counter of failures
# should read that field rather than infer failure from text alone.

TOOL_ERROR_PREFIX = "<tool_use_error>"

# The workspace guard's two refusal families share an opening sentence and are
# told apart by what follows. They are DIFFERENT phenomena and never share a
# counter: one names a target outside the workspace, the other is the guard
# declining because it could not statically verify the command's shape. The
# second is not evidence that anything left the workspace.
ISOLATION_HEADS = ("This session is isolated in the worktree",
                   "This agent is isolated in the worktree")
SHARED_CHECKOUT = ("shared checkout", "shared-checkout")

# (column, tools that emit it or None for any, text that must start the body)
FAILURE_MARKERS = (
    ("schema_rejected", ("StructuredOutput",),
     "Output does not match required schema"),
    ("unread_before_write", ("Write", "Edit"), "File has not been read yet."),
    ("missing_path_target", ("Read",), "File does not exist."),
    ("missing_path_target", ("Grep",), "Path does not exist"),
    ("invalid_tool_input", None, "InputValidationError"),
    ("search_pattern_rejected", ("Grep",), "Search failed"),
)

# The tools through which a result is handed back instead of written as prose.
# An agent that called one of these did answer; it just did not answer in text.
RESULT_TOOLS = ("StructuredOutput", "ReportFindings")

# An isolated workspace is a checkout of its own, and lives under a directory
# with this name. It is the gate on the two workspace signals: a run that was
# never in one could not have been refused by the guard, and counting it in
# their denominator would state the rate as a third of what it is.
WORKTREE_SEGMENT = "worktrees"


def in_isolated_workspace(cwd):
    """Was this working directory inside an isolated workspace?

    A path segment test, not a substring one, so a repository that merely has
    the word in its name is not swept in. Measured over the subagent corpus,
    464 of 1,492 rows match and every one of them matches on this exact segment.
    """
    if not cwd:
        return False
    return WORKTREE_SEGMENT in str(cwd).replace("\\", "/").lower().split("/")


def strip_error_wrapper(body):
    """A tool result body with the harness's error wrapper removed."""
    body = body.lstrip()
    if body.startswith(TOOL_ERROR_PREFIX):
        body = body[len(TOOL_ERROR_PREFIX):].lstrip()
    return body


def failure_body(block):
    """The refusal text of a failed tool_result block, or None.

    is_error is compared to True exactly: measured corpus-wide it is only ever
    True, False or absent, so an identity test loses nothing and cannot be
    surprised by a truthy string later. A non-string body is skipped rather
    than serialised - JSON-dumping it would invent text for a marker to match.
    """
    if not isinstance(block, dict) or block.get("type") != "tool_result":
        return None
    if block.get("is_error") is not True:
        return None
    body = block.get("content")
    if not isinstance(body, str):
        return None
    return strip_error_wrapper(body)


def guard_spoke(block):
    """Did the workspace guard address this run, refusing or not?

    One row in the corpus was refused by the guard while its recorded working
    directory was not inside an isolated workspace. Without this, that row would
    contribute to a numerator whose denominator excluded it.
    """
    if not isinstance(block, dict) or block.get("type") != "tool_result":
        return False
    body = block.get("content")
    if not isinstance(body, str):
        return False
    return strip_error_wrapper(body).startswith(ISOLATION_HEADS)


def classify_failure(tool, body):
    """Which mechanical-failure column a failed result belongs in, or "".

    `tool` is the name resolved from the result's tool-use id; an id with no
    matching call yields "" and therefore matches no tool-scoped category.
    """
    for column, tools, marker in FAILURE_MARKERS:
        if body.startswith(marker) and (tools is None or tool in tools):
            return column
    if body.startswith(ISOLATION_HEADS):
        if any(phrase in body for phrase in SHARED_CHECKOUT):
            return "workspace_target_outside"
        if "verif" in body:
            return "workspace_shape_unverifiable"
    return ""


def prose_of(message):
    """A message's text blocks only.

    Deliberately not text_of(), which also flattens tool_result bodies into the
    string. That is right for quoting a turn and wrong for asking whether the
    agent itself said anything: a transcript that merely read a file mentioning
    the interrupt marker would otherwise read as interrupted.
    """
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(block.get("text") or "" for block in content
                     if isinstance(block, dict) and block.get("type") == "text")


def eligible_signals(tools_used, isolated):
    """The signals this run could have produced, as a sorted list.

    Stored on the row so a report can divide each signal by the population it
    could have arisen in. Recomputing it needs the set of tools the run called,
    which the ledger does not carry, so it is derived once here.
    """
    out = []
    for column, need in SIGNAL_POPULATION.items():
        if need == ISOLATED_WORKSPACE:
            ok = isolated
        elif need:
            ok = bool(tools_used & need)
        else:
            ok = bool(tools_used)
        if ok:
            out.append(column)
    return sorted(out)


def is_approval(reply):
    """Is this whole reply nothing but agreement? `reply` is already stripped.

    Shared with the label report's threshold sweep, so the sweep cannot drift
    from the rule the ledger was built with.
    """
    stripped = _APPROVAL_TAIL.sub("", _LIST_PREFIX.sub("", reply)).strip()
    if _APPROVAL.match(stripped):
        return True
    # Widened from evidence: 300 turns read and marked by hand showed the
    # whole-reply rule catching 24% of real approvals. The misses were an
    # affirmative followed by a qualifier -- agreeing and adding a preference.
    # Requiring no negation is what keeps "sure, but not that way" out.
    head = re.split(r"[,;.!]", stripped, 1)[0].strip()
    return bool(_APPROVAL.match(head)) and not _NEGATION.search(stripped)


def classify_user_turn(body, prior_assistant_chars,
                       max_chars=None, min_prior=None):
    """The pack's central definition, in one place: what a user turn means.

    Returns "interrupt", "question", "approval", "correction" or "". Precedence
    is fixed in that order, so a turn that could read as two things is always
    the earlier one. `measure` counts the result and `moments` quotes it, so
    both read the same rule, and the threshold sweep calls it too rather than
    keeping a second copy that drifts.

    A correction is deliberately over-flagged. Measured against turns read and
    marked by hand -- 300 originally, of which 144 survived a later correction to
    the sampler, which had been drawing from a population including records the
    ledger does not count -- no wording rule got past about 0.63 precision, because whether a reply
    is a correction is a judgment about intent and every missed one was a
    correction phrased as a question. Four content rules were tried and none beat
    the length rule. So this aims for recall instead -- 0.93 against the marks,
    at 0.60 precision, measured over 144 marked turns drawn from the population
    the ledger actually counts -- and the column is named `correction_candidates`
    because
    that is what it holds. The model reading a pack does the judging; a regex
    cannot, and pretending otherwise put a number nobody should trust at the top
    of the ranking.

    Also measured and NOT adopted: whether the next assistant turn concedes
    ("you're right", "my mistake") is a sharp signal on its own -- 0.85 precision
    -- but it rescues only 2 more points of recall on top of the rule below, and
    it would need the classifier to see the following turn. Not worth the
    machinery; recorded so nobody re-derives it.
    """
    if _INTERRUPT.search(body):
        return "interrupt"
    reply = body.strip()
    # The thresholds are arguments so the sweep can ask this same function what
    # a different pair would have produced. They were a second copy of this rule
    # for a while, and it drifted: the copy still answered "question" where this
    # answers "correction", so a sweep table disagreed with the settled row
    # printed directly above it.
    max_chars = CORRECTION_MAX_CHARS if max_chars is None else max_chars
    min_prior = CORRECTION_MIN_PRIOR_CHARS if min_prior is None else min_prior
    short_reply = (0 < len(reply) <= max_chars
                   and prior_assistant_chars >= min_prior)
    if short_reply:
        # A corrective signal wins every tie here, and the order was chosen by
        # measurement rather than taste: it beat the alternative on two classes
        # and lost on none. Both losing orderings misfiled the same shape --
        # agreement wrapped around a complaint, and a complaint wearing a
        # question mark. On the corrected sample the settled order measures
        # approval 1.00/0.70, question 0.96/0.71, correction 0.60/0.93.
        if _CORRECTIVE.search(reply):
            return "correction"
        if is_approval(reply):
            return "approval"
        if reply.endswith("?"):
            return "question"
        return "correction"
    # Not short, but carries a corrective signal after a substantial turn: the
    # class the length rule was blindest to. A question mark does not exclude it
    # here -- "do all the tests still pass?" is a challenge, and treating every
    # question as merely a question is what cost the most recall.
    if (prior_assistant_chars >= CORRECTION_MIN_PRIOR_CHARS
            and len(reply) <= CANDIDATE_MAX_CHARS
            and _CORRECTIVE.search(reply)
            and not is_approval(reply)):
        return "correction"
    return ""


def is_error_record(rec):
    """Did this record carry a failed tool result?

    Measured across the corpus: `is_error` on the tool_result content block is
    the dominant marker (519 in a 373-session sample), a string-valued
    `toolUseResult` beginning with an error word is next (489), and a
    `toolUseResult.error` key appears once. Both of the first two can be present
    for the same failure, so this returns a boolean and the caller counts once.
    """
    result = rec.get("toolUseResult")
    if isinstance(result, dict) and result.get("error"):
        return True
    if isinstance(result, str) and result.lower().startswith("error"):
        return True
    message = rec.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), list):
        for block in message["content"]:
            if isinstance(block, dict) and block.get("type") == "tool_result" \
                    and block.get("is_error"):
                return True
    return False


def is_tool_generated(rec):
    """Was this user-role record written by a tool rather than typed?

    `sourceToolAssistantUUID` names the assistant turn whose tool call produced
    the text. Measured over the corpus 2026-08-19: the key is on 49,516 records,
    every one of them a user record, never with an empty value. The older
    `toolUseResult` guard misses 6,787 of them, all in subagent transcripts.

    A precision instrument, not a complete one: unmarked records carrying
    machine wrapper tags exist and this does not find them. On older transcripts
    the marker coincides exactly with `toolUseResult`, so this is a no-op there
    and the ledger will show that as a step in the trend rather than a change in
    behaviour.
    That gap is what HUMAN_PROMPT_SOURCES closes, below.
    """
    return "sourceToolAssistantUUID" in rec


# The harness records where a user-role prompt came from. Only these three are a
# person typing; `system` is the harness injecting a reminder, a notification or
# a skill preamble, and `sdk` is a programmatic caller.
#
# Measured over main-session transcripts 2026-08-20, against 300 turns read and
# marked by hand: of 985 records marked `system`, 981 carry machine wrapper text.
# Of 1,933 marked `typed`, 1,885 are a person writing. Excluding `system` and
# `sdk` drops 1,379 of 4,148 counted prompts -- a third of what the ledger has
# been calling a user prompt, on top of what the tool-output guard already
# removed.
HUMAN_PROMPT_SOURCES = frozenset(("typed", "queued", "suggestion_accepted"))

# Older transcripts predate the field. 630 records have no `promptSource` at all
# and are roughly half machine, so they get a narrow text test instead: a body
# that OPENS with one of these is a harness wrapper, not a person. Anchored at
# the start deliberately -- a person quoting one of these phrases mid-message is
# still a person.
#
# One list for both harnesses (spec D3). Codex wrapper tags whose every
# occurrence carries attributes enter as bracket-less prefixes; bare tags
# keep the closed form so a person merely quoting a tag name mid-sentence
# still counts. Anchored at the start deliberately. Measured over the
# rollout corpus: codex_internal_context, in-app-browser-context and image
# only ever appear as <tag attr...>.
MACHINE_PROMPT_OPENERS = (
    "<task-notification>", "<system-reminder>", "<command-name>",
    "<local-command-stdout>", "Base directory for this skill:",
    "This session is being continued", "Caveat: The messages below were generated",
    # Codex-only wrapper tags (spec Grounding 5).
    "<environment_context>", "<recommended_plugins>", "<skill>",
    "<turn_aborted>", "<codex_delegation>",
    "<codex_internal_context", "<in-app-browser-context", "<image",
)


def is_human_prompt(rec, body):
    """Did a person write this, or did the harness?"""
    if is_tool_generated(rec):
        return False
    source = rec.get("promptSource")
    if source:
        return source in HUMAN_PROMPT_SOURCES
    return not body.lstrip().startswith(MACHINE_PROMPT_OPENERS)


def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


class TranscriptUnreadable(Exception):
    """The bytes could not be read.

    Distinct from a file that read fine and holds no conversation: one is a
    fault worth retrying, the other is a settled fact about the file. Reporting
    both as "unreadable" is what put 22 workflow journals in that bucket.
    """


def read_records(path):
    """Yield parsed records, skipping malformed lines. A live session is being
    appended to while we read it; a truncated final line is normal, not an
    error.

    Raises TranscriptUnreadable if the file cannot be opened, or if reading
    fails partway through. Returning an empty stream instead is what made a
    read failure indistinguishable from a file with no conversation in it.

    DUPLICATED, on purpose: plugins/p/bin/stopped-promises.py carries its own
    copy of this reader rather than importing it, so a measurement tool's parser
    cannot shift underneath it while this file is being rewritten. A fix to the
    parsing rules here needs applying there too. Both readers honour the
    config-directory variable; the remaining differences are gzip support and
    missing-root behaviour, which are that script's own, and a read failure
    raises here where that copy returns silently.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                # json.loads tolerates surrounding whitespace, so testing for
                # blankness beats copying every line of a gigabyte corpus.
                if not line or line.isspace():
                    continue
                try:
                    yield json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
    except OSError as exc:
        raise TranscriptUnreadable(path) from exc


# The three ways a walked file can end up. `extract` prints them as three
# separate counts, and they must sum to the number of files walked.
MEASURED, NOT_TRANSCRIPT, UNREADABLE = "measured", "not-transcript", "unreadable"

# Structural harness detection (spec D1.4). Every observed rollout opens
# with session_meta, so record 1 decides in practice; the 20-record cap
# guards a future leading sidecar. Claude transcripts never carry these
# types in their first 20 records (0 of 2,362 measured), and anything
# that is not a rollout falls through to measure(), whose whole-file
# conversation test keeps deciding NOT_TRANSCRIPT exactly as before.
ROLLOUT_TYPES = frozenset((
    "session_meta", "response_item", "event_msg", "turn_context",
    "world_state", "compacted", "inter_agent_communication_metadata"))


def is_rollout(path):
    for count, rec in enumerate(read_records(path)):
        if isinstance(rec, dict) and rec.get("type") in ROLLOUT_TYPES:
            return True
        if count >= 19:
            return False
    return False


def measure(path, harness="claude", root=None):
    """Reduce one transcript to a metrics row.

    Returns None for a file that read fine and holds no conversation. Raises
    TranscriptUnreadable for one whose bytes would not read. Callers wanting
    the three outcomes as a single value call measure_outcome() instead.
    """
    m = Counter()
    session_id = project = branch = version = None
    first_ts = last_ts = None
    seen_sigs = set()
    skills = set()
    prior_assistant_chars = 0
    conversation = 0
    tokens_in = tokens_out = cache_read = 0
    prev_mode = None
    prev_skill = None
    tool_by_id = {}
    tools_used = set()
    open_tool_ids = set()
    last_assistant = None
    caller_interrupted = False
    answered_structured = False
    isolated = False

    for rec in read_records(path):
        if not isinstance(rec, dict):
            continue
        rtype = rec.get("type")
        # Structural test for "is this a transcript at all". Filenames would
        # need a new rule for every sidecar format the CLI adds; record types
        # do not.
        if rtype in ("user", "assistant"):
            conversation += 1
        ts = parse_ts(rec.get("timestamp"))
        if ts:
            first_ts = first_ts or ts
            last_ts = ts

        session_id = session_id or rec.get("sessionId") or rec.get("session_id")
        project = project or rec.get("cwd")
        # Whether the run had an isolated workspace decides whether the two
        # workspace signals could have happened in it at all, so it gates their
        # denominator rather than being reported on its own.
        isolated = isolated or in_isolated_workspace(rec.get("cwd"))
        branch = branch or rec.get("gitBranch")
        version = version or rec.get("version")

        # attributionSkill is stamped on EVERY assistant record produced while a
        # skill is active, so counting records counts turns, not invocations.
        # A run is one contiguous stretch of the same skill.
        skill = rec.get("attributionSkill")
        if skill:
            skills.add(str(skill))
            if skill != prev_skill:
                m["skill_runs"] += 1
        prev_skill = skill

        if rtype == "permission-mode":
            # These records are a repeated snapshot of the current mode, not a
            # change event: 5,645 records across the corpus hold 68 actual
            # transitions. Count transitions.
            mode = rec.get("mode") or rec.get("permissionMode")
            if prev_mode is not None and mode != prev_mode:
                m["permission_mode_changes"] += 1
            prev_mode = mode
        elif rtype == "queue-operation":
            # enqueue/dequeue/remove/popAll are paired; a single total would
            # count one queued prompt up to three times.
            if (rec.get("subtype") or rec.get("operation")) == "enqueue":
                m["queued_prompts"] += 1
        elif rtype == "assistant":
            m["turns"] += 1
            msg = rec.get("message") or {}
            last_assistant = msg
            usage = msg.get("usage") or {}
            tokens_in += int(usage.get("input_tokens") or 0)
            tokens_out += int(usage.get("output_tokens") or 0)
            cache_read += int(usage.get("cache_read_input_tokens") or 0)
            body = text_of(msg)
            # Accumulate, do not replace. One assistant turn is often several
            # records -- text, then a tool call, then more text -- and taking
            # only the last one asks "was the final fragment long" instead of
            # "was the turn long". Measured over main-session transcripts, that
            # discarded 79 short replies which the stated rule would have
            # classified, 56 of them corrections. The counter resets when a user
            # turn is counted, below, which is what ends a turn.
            prior_assistant_chars += len(body)
            for block_id, name, sig in tool_calls_of(msg):
                tool_by_id[block_id] = name
                tools_used.add(name)
                open_tool_ids.add(block_id)
                if name in RESULT_TOOLS:
                    answered_structured = True
                m["tool_calls"] += 1
                key = (name, sig)
                if sig and key in seen_sigs:
                    m["repeat_calls"] += 1
                seen_sigs.add(key)
        elif rtype == "user":
            body = text_of(rec.get("message") or {})
            # Gate before classifying. Roughly 50,000 of 59,000 user records are
            # written by a tool or the harness, and flattening then classifying
            # each one only to discard the answer was 8% of a rebuild.
            #
            # Nested rather than `continue`: the tool-error count below reads the
            # very records this gate rejects, and skipping the rest of the loop
            # here silently zeroed it.
            kind = (classify_user_turn(body, prior_assistant_chars)
                    if is_human_prompt(rec, body) else None)
            if kind == "interrupt":
                m["interrupts"] += 1
            elif kind is not None and rec.get("toolUseResult") is None and body.strip():
                m["user_prompts"] += 1
                if kind == "correction":
                    m["correction_candidates"] += 1
                elif kind == "approval":
                    m["approval_turns"] += 1
                prior_assistant_chars = 0

        # Only user records carry tool results; checking the rest re-walks
        # message content for nothing.
        if rtype == "user":
            if is_error_record(rec):
                m["tool_errors"] += 1
            user_msg = rec.get("message") or {}
            # An interrupt is the caller stopping the run. Read from the text
            # blocks only - a tool result that happens to quote the marker is
            # not the caller interrupting anything.
            if _INTERRUPT.search(prose_of(user_msg)):
                caller_interrupted = True
            user_content = user_msg.get("content")
            if isinstance(user_content, list):
                for block in user_content:
                    if not (isinstance(block, dict)
                            and block.get("type") == "tool_result"):
                        continue
                    open_tool_ids.discard(block.get("tool_use_id"))
                    if guard_spoke(block):
                        isolated = True
                    # Mechanical failures the harness refused on its own terms.
                    # A non-zero command exit is NOT one of these: the command
                    # ran, and the exit code is information, not an agent
                    # mistake. Neither is a tool use the operator declined, nor
                    # one a permission rule declined - both are decisions about
                    # the environment.
                    body = failure_body(block)
                    if body is None:
                        continue
                    column = classify_failure(
                        tool_by_id.get(block.get("tool_use_id"), ""), body)
                    if column:
                        m[column] += 1

    if not conversation:
        return None

    # Subagent transcripts live under <session>/subagents/ and carry the PARENT
    # session's id. Keying rows by session id would let them overwrite the
    # parent's row — one row per transcript, tagged, is what aggregates right.
    base = root if root is not None else claude_projects_dir()
    try:
        rel = path.relative_to(base).as_posix()
    except ValueError:
        rel = path.name

    row = {
        "transcript": rel,
        "harness": harness,
        "population": "subagent" if "subagents/" in rel else "main",
        "parent_session_id": "",
        "project_key": rel.split("/")[0] if "/" in rel else (rel or "?"),
        "ineligible": [],
        "compacted": False,
        "session_id": session_id or path.stem,
        "project": redact(project or ""),
        "git_branch": branch or "",
        "cc_version": version or "",
        "date": first_ts.date().isoformat() if first_ts else "",
        "duration_s": int((last_ts - first_ts).total_seconds()) if first_ts and last_ts else 0,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cache_read": cache_read,
        "skills_used": sorted(skills),
    }
    row["schema"] = SCHEMA_VERSION
    for key in COUNTERS:
        row[key] = m[key]

    # How the run answered, in ENDINGS precedence order. A run that handed a
    # result back through a structured-result call answered, whatever prose sits
    # beside it: asking instead whether the LAST record carried prose made the
    # value turn on which record happened to come last, and measured, 43 rows
    # made a structured-result call and still read as text. Only `unanswered`
    # and `silent` are the agent failing to answer.
    if answered_structured:
        ending = "structured"
    elif prose_of(last_assistant).strip():
        ending = "text"
    elif caller_interrupted:
        ending = "interrupted"
    elif open_tool_ids:
        ending = "unanswered"
    else:
        ending = "silent"
    row["ending"] = ending
    for key in SUBAGENT_COUNTERS:
        row[key] = m[key]
    # Which signals could have occurred here at all. The report divides each
    # count by the rows carrying its name, never by every row.
    row["eligible"] = eligible_signals(tools_used, isolated)
    return row


# Codex counter mapping (spec D3). Three counters have no rollout signal:
# no queue or permission-mode record exists, and a nonzero exec exit is
# information, not a harness refusal — matching the Claude rule.
CODEX_INELIGIBLE = ("tool_errors", "queued_prompts", "permission_mode_changes")
# Polls repeat identical arguments as their normal operation; counting them
# as repeats is the defect signature()'s docstring records for Claude,
# re-measured for rollouts: the naive rule flags 14.3% of calls (7,295 /
# 51,099 checked), this exclusion brings it to 10.0% (4,637 / 46,301), and
# the residual is real repetition. The spec names exactly these two polls —
# the ones present in the corpus.
CODEX_POLL_TOOLS = frozenset(("wait", "wait_agent"))
# A call item is answered only by its own pair's output type (spec D3);
# note tool_search's output name drops "_call".
CODEX_CALL_PAIRS = {"function_call": "function_call_output",
                    "custom_tool_call": "custom_tool_call_output",
                    "tool_search_call": "tool_search_output"}
# Measured event-by-event (each event's own payload["call_id"], which sits
# at the payload TOP LEVEL, checked against that file's custom_tool_call/
# function_call call_ids — the first pass here mistakenly compared file-
# level id sets, which understated pairing): patch_apply_end (4,118
# occurrences, 62 files) and mcp_tool_call_end (1,043 occurrences, 22
# files) are PAIRED — 1,170 of the patch events and 138 of the mcp events
# carry a call_id that exactly matches a same-file call item, so the event
# is a second record of a call already counted there. Both are excluded.
# web_search_end and image_generation_end show essentially no such overlap
# (1,008 of 1,032, and all 18 of 18, carry a call_id matching no same-file
# call item) and stay in as the only record of their calls.
CODEX_TOOL_EVENTS = frozenset(("web_search_end", "image_generation_end"))
# The only fields the row schema banks. Probed across the full corpus (317
# rollouts, 65,845 token_count events): total_token_usage carries exactly six
# key names, all numeric, no stray key ever observed. The fifth,
# cache_write_input_tokens, is real and unbanked - no column reads it. The
# list is pinned to these four anyway: the row schema banks only these four,
# and if a future CLI version adds a non-numeric sibling, iterating
# current.items() blindly would raise inside measure_outcome - which
# promises never to raise - and lose the whole extract pass with no ledger
# written.
CODEX_TOKEN_FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens",
                      "reasoning_output_tokens")
_SKILL_NAME = re.compile(r"<name>\s*([^<]+?)\s*</name>")


def _codex_text(payload):
    """Visible text of a rollout message payload."""
    parts = _content_text(payload.get("content"),
                          ("input_text", "output_text", "text"))
    return "\n".join(str(p) for p in parts)


def _codex_population(meta):
    """(population, how it was decided). Absent thread_source maps to
    subagent when a parent id proves it, else to unknown — never to main:
    an unclassified file must not enter the population every per-session
    rate divides by. (All 50 absent-thread_source corpus files come from a
    single alpha CLI build on one day, none with a parent id.)"""
    source = meta.get("thread_source")
    if source == "user":
        return "main", "thread_source"
    if source in ("subagent", "automation"):
        return source, "thread_source"
    if source is None:
        if meta.get("parent_thread_id"):
            return "subagent", "fallback"
        return "unknown", "fallback"
    return "unknown", "thread_source"


def measure_codex(path, root):
    """Reduce one Codex rollout to a metrics row.

    Same row schema as measure(); the judgment helpers are shared, the
    record walk is not — the two formats disagree on which signals exist,
    so the schema, not the record stream, is the contract (spec D3).
    """
    m = Counter()
    meta = None
    meta_disagrees = False
    first_ts = last_ts = None
    seen_sigs = set()
    skills = set()
    prior_assistant_chars = 0
    saw_message = False
    # Probed over the real corpus (spec D2.6): of 77 rollouts with a
    # top-level `compacted` record, the response_item count before it never
    # drops below 26 (median 259) and 47 of 77 files keep appending at least
    # as many records after it as came before — append-preserving, not a
    # rewrite. Compaction changes what the model is shown, not what the
    # rollout file holds, so this flag is just a marker.
    compacted = False
    aborted = False
    open_calls = set()   # (expected_output_type, call_id) pairs
    last_assistant_text = ""
    # Cumulative token totals reset mid-file in 5 of 317 corpus rollouts;
    # bank the running total at each reset and add the final run.
    bank = Counter()
    current = {}

    for rec in read_records(path):
        if not isinstance(rec, dict):
            continue
        ts = parse_ts(rec.get("timestamp"))
        if ts:
            first_ts = first_ts or ts
            last_ts = ts
        rtype = rec.get("type")
        payload = rec.get("payload")
        if rtype == "compacted":
            compacted = True
            continue
        if not isinstance(payload, dict):
            continue
        if rtype == "session_meta":
            if meta is None:
                meta = payload   # first meta wins (spec D2.2)
            elif payload.get("thread_source") != meta.get("thread_source"):
                meta_disagrees = True
            continue
        if rtype == "event_msg":
            etype = payload.get("type")
            if etype == "token_count":
                info = payload.get("info")
                total = (info or {}).get("total_token_usage") \
                    if isinstance(info, dict) else None
                if isinstance(total, dict):
                    if current:
                        try:
                            reset = int(total.get("total_tokens") or 0) < \
                                    int(current.get("total_tokens") or 0)
                        except (TypeError, ValueError):
                            reset = False
                        if reset:
                            for key in CODEX_TOKEN_FIELDS:
                                try:
                                    bank[key] += int(current.get(key) or 0)
                                except (TypeError, ValueError):
                                    pass
                    current = total
            elif etype == "turn_aborted":
                m["interrupts"] += 1
                aborted = True
            elif etype in CODEX_TOOL_EVENTS:
                m["tool_calls"] += 1
            continue
        if rtype != "response_item":
            continue
        itype = payload.get("type")
        if itype == "message":
            role = payload.get("role")
            body = _codex_text(payload)
            if role == "assistant":
                m["turns"] += 1
                saw_message = True
                last_assistant_text = body
                prior_assistant_chars += len(body)
            elif role == "user":
                saw_message = True
                opener = body.lstrip().startswith(MACHINE_PROMPT_OPENERS)
                if opener:
                    stripped = body.lstrip()
                    if stripped.startswith("<skill"):
                        match = _SKILL_NAME.search(body)
                        if match:
                            skills.add(match.group(1).split(":")[-1])
                            m["skill_runs"] += 1
                elif body.strip():
                    kind = classify_user_turn(body, prior_assistant_chars)
                    if kind == "interrupt":
                        # Matches the Claude reducer: an interrupt is not a
                        # prompt. The marker is Claude-shaped and expected
                        # to be absent from rollouts.
                        m["interrupts"] += 1
                    else:
                        m["user_prompts"] += 1
                        if kind == "correction":
                            m["correction_candidates"] += 1
                        elif kind == "approval":
                            m["approval_turns"] += 1
                    prior_assistant_chars = 0
            # developer-role messages never count (spec D3)
        elif itype in CODEX_CALL_PAIRS:
            m["turns"] += 1
            m["tool_calls"] += 1
            name = str(payload.get("name") or payload.get("tool_name") or "")
            call_id = str(payload.get("call_id") or payload.get("id") or "")
            if call_id:
                open_calls.add((CODEX_CALL_PAIRS[itype], call_id))
            raw = payload.get("arguments")
            if raw is None:
                raw = payload.get("input")
            parsed = None
            if isinstance(raw, str) and raw.strip():
                try:
                    parsed = json.loads(raw)
                except ValueError:
                    parsed = raw
            elif raw:
                parsed = raw
            if parsed and name not in CODEX_POLL_TOOLS:
                sig = signature(parsed)
                key = (name, sig)
                if key in seen_sigs:
                    m["repeat_calls"] += 1
                seen_sigs.add(key)
        elif itype in CODEX_CALL_PAIRS.values():
            open_calls.discard(
                (itype, str(payload.get("call_id") or payload.get("id") or "")))

    if not saw_message:
        return None

    if current:
        for key in CODEX_TOKEN_FIELDS:
            try:
                bank[key] += int(current.get(key) or 0)
            except (TypeError, ValueError):
                pass
    meta = meta or {}
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        rel = path.name
    project = redact(str(meta.get("cwd") or ""))
    tokens_in = max(0, bank["input_tokens"] - bank["cached_input_tokens"])

    if last_assistant_text.strip():
        ending = "text"
    elif aborted:
        ending = "interrupted"
    elif open_calls:
        ending = "unanswered"
    else:
        ending = "silent"

    population, population_source = _codex_population(meta)
    row = {
        "transcript": rel,
        # harness names the transcript FORMAT; the walk's root decides
        # ownership and dedup only (spec D1.3).
        "harness": "codex",
        "population": population,
        "population_source": population_source,
        "parent_session_id": str(meta.get("parent_thread_id") or ""),
        "project_key": "cx-" + hashlib.sha1(
            project.encode("utf-8")).hexdigest()[:8],
        "ineligible": list(CODEX_INELIGIBLE),
        "compacted": compacted,
        "session_id": str(meta.get("session_id") or meta.get("id") or "")
                      or path.stem,
        "project": project,
        "git_branch": str(((meta.get("git") or {}).get("branch")) or "")
                      if isinstance(meta.get("git"), dict) else "",
        "cc_version": str(meta.get("cli_version") or ""),
        "date": first_ts.date().isoformat() if first_ts else "",
        "duration_s": int((last_ts - first_ts).total_seconds())
                      if first_ts and last_ts else 0,
        "tokens_in": tokens_in,
        # Visible and reasoning output are both spend (spec D3).
        "tokens_out": bank["output_tokens"] + bank["reasoning_output_tokens"],
        "cache_read": bank["cached_input_tokens"],
        "skills_used": sorted(skills),
        "schema": SCHEMA_VERSION,
        "ending": ending,
        # The seven mechanical-failure signals are Claude harness-refusal
        # markers a rollout never emits.
        "eligible": [],
        "meta_disagrees": meta_disagrees,
    }
    for key in COUNTERS:
        row[key] = m[key]
    for key in SUBAGENT_COUNTERS:
        row[key] = 0
    return row


def measure_outcome(path, harness, root):
    """One file's outcome: (MEASURED, row), (NOT_TRANSCRIPT, None) or
    (UNREADABLE, None). Never raises for an unreadable file - the thread pool
    in cmd_extract abandons its whole result stream on the first exception."""
    try:
        if is_rollout(path):
            row = measure_codex(path, root)
        else:
            row = measure(path, harness, root)
    except TranscriptUnreadable:
        return UNREADABLE, None
    return (MEASURED, row) if row is not None else (NOT_TRANSCRIPT, None)


# --- extract ---------------------------------------------------------------

def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def ensure_work_dir(what):
    """Create the work directory, refusing if it sits inside a repository.

    Every file this tool writes lands here and none of them belong in a
    repository: a pack and the labelling file carry message text, and a ledger
    row carries the working directory the session ran in. The check was applied
    per output file and `extract` never called it -- so the ledger, the one file
    written on every single run, was the one thing not covered.
    """
    refuse_inside_repo(WORK_DIR / what)
    WORK_DIR.mkdir(parents=True, exist_ok=True)


def cmd_extract(args):
    ensure_work_dir(METRICS_FILE.name)
    roots = list(transcript_roots())
    live = [(harness, root) for harness, root in roots if root.is_dir()]
    if not live:
        print("no session directory at any root: "
              + ", ".join(str(root) for _, root in roots), file=sys.stderr)
        sys.exit(EXIT_CANNOT_RUN)
    absent = [harness for harness, root in roots if not root.is_dir()]

    rebuild = args.rebuild
    rows = {}
    if not rebuild:
        existing = load_rows(required=False, check_schema=False)
        stale = [r for r in existing if r.get("schema") != SCHEMA_VERSION]
        if stale:
            # Rebuilding is the only correct response. Keeping the old rows and
            # measuring the rest would mix two definitions in one file, which is
            # the failure this version exists to make impossible.
            print(f"schema changed since this ledger was written "
                  f"({len(stale)} of {len(existing)} rows) - rebuilding all of it")
            rebuild = True
        else:
            rows = {(row_harness(r), r["transcript"]): r
                    for r in existing if "transcript" in r}
    state = {} if rebuild else load_state()

    candidates = {}
    duplicates = 0
    for harness, root in live:
        for path in sorted(root.rglob("*.jsonl")):
            key = str(path.resolve())
            if key in candidates:
                duplicates += 1
                continue
            candidates[key] = (path, harness, root)
    transcripts = list(candidates.values())

    stale = []
    unchanged = 0
    unreadable = 0
    for path, harness, root in transcripts:
        try:
            stat = path.stat()
        except OSError:
            # Nothing to compare and nothing to record: this is a read failure
            # like any other, and belongs in a bucket rather than in none.
            unreadable += 1
            continue
        # A live session grows; re-measure when size or mtime moved.
        fingerprint = f"{stat.st_size}:{int(stat.st_mtime)}"
        if state.get(str(path)) == fingerprint:
            unchanged += 1
            continue
        stale.append((path, harness, root, fingerprint))

    # measure() shares no state, and the work is dominated by reading a
    # gigabyte off disk, so a thread pool converts most of the wall clock into
    # concurrent I/O. Warm the redaction patterns first so the cache is not
    # raced by the workers.
    _redaction_patterns()
    measured = not_transcripts = 0
    measured_by_harness = Counter()
    with ThreadPoolExecutor(max_workers=min(8, (os.cpu_count() or 4))) as pool:
        for (path, harness, root, fingerprint), (outcome, row) in zip(
                stale, pool.map(
                    lambda item: measure_outcome(item[0], item[1], item[2]),
                    stale)):
            if outcome == UNREADABLE:
                # Deliberately not fingerprinted. Recording one would retire
                # the file until it changes, so a live transcript that was
                # briefly locked would be dropped for good.
                unreadable += 1
                continue
            if outcome == MEASURED:
                rows[(row_harness(row), row["transcript"])] = row
                measured += 1
                measured_by_harness[row.get("harness") or harness] += 1
            else:
                not_transcripts += 1
            state[str(path)] = fingerprint

    with open(METRICS_FILE, "w", encoding="utf-8") as fh:
        for row in sorted(rows.values(), key=lambda r: (r.get("date", ""), r["transcript"])):
            fh.write(json.dumps(row) + "\n")
    STATE_FILE.write_text(json.dumps(state), encoding="utf-8")

    print(f"transcripts: {len(transcripts)}  measured: {measured}  "
          f"unchanged: {unchanged}  not-transcripts: {not_transcripts}  "
          f"unreadable: {unreadable}")
    for harness in HARNESSES:
        if harness in absent:
            print(f"  {harness}: root absent")
            continue
        in_ledger = sum(1 for r in rows.values()
                        if row_harness(r) == harness)
        print(f"  {harness}: {measured_by_harness[harness]} measured, "
              f"{in_ledger} in ledger")
    codex_rows = [r for r in rows.values() if r.get("harness") == "codex"]
    if codex_rows:
        populations = Counter(r.get("population") for r in codex_rows)
        print("  codex populations: " + ", ".join(
            f"{name} {populations[name]}" for name in
            ("main", "subagent", "automation", "unknown") if populations[name]))
        fallbacks = sum(1 for r in codex_rows
                        if r.get("population_source") == "fallback")
        if fallbacks:
            print(f"  codex rows classified by fallback "
                  f"(no thread_source): {fallbacks}")
        disagreements = sum(1 for r in codex_rows if r.get("meta_disagrees"))
        if disagreements:
            print(f"  codex files whose metas disagree on thread_source: "
                  f"{disagreements}")
    if duplicates:
        print(f"  duplicate paths skipped: {duplicates}")
    print(f"sessions in ledger: {len(rows)}")
    print(f"ledger: {METRICS_FILE}")
    return EXIT_FLAGGED if unreadable else EXIT_CLEAN


# --- pack ------------------------------------------------------------------

def load_rows(required=True, check_schema=True):
    """Read the ledger. `required` is False for extract, which is allowed to
    start from an empty ledger and build one.

    A reader refuses a ledger that was not written by this version of the schema.
    That is deliberately blunt: measured on a real ledger, running new code over
    old rows without rebuilding left 1,923 of 1,938 rows on the previous schema
    and still exited 0, so every total was a sum across two definitions with
    nothing in the output admitting it.
    """
    if not METRICS_FILE.exists():
        if not required:
            return []
        print(f"no metrics ledger at {METRICS_FILE} - run `extract` first",
              file=sys.stderr)
        sys.exit(EXIT_CANNOT_RUN)
    out = []
    for line in METRICS_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    if check_schema:
        stale = sum(1 for r in out if r.get("schema") != SCHEMA_VERSION)
        if stale:
            print(f"ledger holds {stale} of {len(out)} rows from an older schema "
                  f"(current is {SCHEMA_VERSION}) - run `extract --rebuild`; "
                  f"totals across two definitions mean nothing", file=sys.stderr)
            sys.exit(EXIT_CANNOT_RUN)
    return out


def totals(rows):
    """Aggregate the rows handed in, and nothing else — plus, per counter,
    how many of those rows could observe it. The caller chooses the
    population; a rate divides each counter by its own eligible-row count,
    never by the row count (spec D4.1)."""
    agg = Counter()
    eligible_rows = Counter()
    for row in rows:
        ineligible = set(row.get("ineligible") or ())
        for key in COUNTERS:
            agg[key] += int(row.get(key) or 0)
            if key not in ineligible:
                eligible_rows[key] += 1
        agg["tokens_out"] += int(row.get("tokens_out") or 0)
        # tokens_out is observable on every harness; without this line a
        # consumer dividing by its eligible count would drop the row.
        eligible_rows["tokens_out"] += 1
    return agg, eligible_rows


def split_population(rows):
    """Rows bucketed by population. Only `main` carries per-session rates;
    the rest are spend. A row with no population field counts as main —
    unreachable after a schema-7 rebuild, but a reader must not crash."""
    split = {"main": [], "subagent": [], "automation": [], "unknown": []}
    for row in rows:
        split.get(row.get("population") or "main", split["unknown"]).append(row)
    return split


def friction_score(row):
    """Rank sessions using only signals permitted for decision support.

    Legacy turn guesses remain available to sample review moments but do not
    affect this score while their rubric is candidate-sampler-only.

    `repeat_calls` is deliberately unscored. It was 42% of this score and the
    largest single input, and a recount showed most of what it counted was not a
    retry at all. Measured after removing it: no window's top 8 changes when the
    replacement counters are added back, so the improvement came from taking the
    bad signal OUT, not from finding a better one. The column is still reported;
    it just no longer decides what a human is shown.
    """
    score = (int(row.get("permission_mode_changes") or 0) * 3
             + int(row.get("tool_errors") or 0))
    if legacy_turn_labels_allow("decision_support"):
        score += (int(row.get("correction_candidates") or 0) * 4
                  + int(row.get("interrupts") or 0) * 4)
    return score


MOMENTS_PER_SESSION = 3


def moments(row):
    """Redacted evidence for one session, or nothing at all if its
    transcript will not read or its harness has no reader."""
    try:
        if row_harness(row) == "codex":
            return _moments_codex(row)
        return _moments(row)
    except TranscriptUnreadable:
        return []


def _moment_of(rec, body, prior):
    """The redacted moment dict for this user turn, or None if the turn is
    not one of the kinds a pack quotes. Shared by _moments and
    _moments_codex, whose classify-and-render step was byte-identical."""
    kind = classify_user_turn(body, len(prior))
    if kind not in ("interrupt", "correction", "approval"):
        return None
    return {
        "at": rec.get("timestamp") or "",
        "kind": kind,
        "said": redact(body.strip())[:400],
        "after": redact(prior.strip()[-300:]),
    }


def _moments(row):
    """Pull the user turns that scored this session as frictional, plus the
    approvals — the operator's liked behavior, by example — with the
    assistant text immediately before each. Redacted."""
    path = claude_projects_dir() / row.get("transcript", "")
    if not path.is_file():
        return []
    out = []
    prior = ""
    for rec in read_records(path):
        if not isinstance(rec, dict):
            continue
        if rec.get("type") == "assistant":
            prior += text_of(rec.get("message") or {})
        elif rec.get("type") == "user" and rec.get("toolUseResult") is None:
            body = text_of(rec.get("message") or {})
            if not is_human_prompt(rec, body):
                continue
            moment = _moment_of(rec, body, prior)
            if moment:
                out.append(moment)
            prior = ""
        if len(out) >= MOMENTS_PER_SESSION:
            break
    return out


def _moments_codex(row):
    """The Codex counterpart of _moments: wrapper-tagged and empty user
    messages skipped with the same opener list, prior accumulated from
    assistant messages and reset on each counted user turn."""
    # Always relative to the Codex root: harness names the transcript
    # FORMAT, and the shipped roots never nest, so a codex row's
    # transcript is never rooted anywhere else.
    path = codex_sessions_dir() / row.get("transcript", "")
    if not path.is_file():
        return []
    out = []
    prior = ""
    for rec in read_records(path):
        if not isinstance(rec, dict) or rec.get("type") != "response_item":
            continue
        payload = rec.get("payload")
        if not isinstance(payload, dict) or payload.get("type") != "message":
            continue
        role = payload.get("role")
        body = _codex_text(payload)
        if role == "assistant":
            prior += body
        elif role == "user":
            if body.lstrip().startswith(MACHINE_PROMPT_OPENERS) \
                    or not body.strip():
                continue
            moment = _moment_of(rec, body, prior)
            if moment:
                out.append(moment)
            prior = ""
        if len(out) >= MOMENTS_PER_SESSION:
            break
    return out


def _candidate_signal(row):
    """The candidate-sampler's ranking signal for a row with no friction
    score: correction candidates, interrupts and approval turns, summed."""
    return (int(row.get("correction_candidates") or 0)
            + int(row.get("interrupts") or 0)
            + int(row.get("approval_turns") or 0))


def _append_moment_lines(lines, evidence):
    """Render each moment in `evidence` and append its block lines to
    `lines`, in place. Shared by cmd_pack's ranked and candidate-sampled
    sections, which rendered the identical block twice."""
    for moment in evidence:
        lines.append("")
        lines.append(f"- **{moment['kind']}** at {moment['at']}")
        lines.append(f"  - assistant, just before: _{moment['after']}_")
        lines.append(f"  - user said: **{moment['said']}**")


def cmd_pack(args):
    rows = load_rows()
    now = datetime.now(timezone.utc).date()
    start = now - timedelta(days=args.days)
    prior_start = start - timedelta(days=args.days)

    window = [r for r in rows if r.get("date") and start.isoformat() <= r["date"]]
    prior = [r for r in rows if r.get("date")
             and prior_start.isoformat() <= r["date"] < start.isoformat()]

    split = split_population(window)
    prior_split = split_population(prior)
    main, sub = split["main"], split["subagent"]
    prior_main, prior_sub = prior_split["main"], prior_split["subagent"]
    compacted = sum(1 for r in window if r.get("compacted"))
    compacted_note = (f" {compacted} compacted rollouts in window."
                      if compacted else "")

    lines = [f"# Evidence pack — last {args.days} days",
             f"Window: {start} to {now}. Main sessions: {len(main)} "
             f"(prior window: {len(prior_main)}).{compacted_note}", "",
             "## Trends", ""]
    lines += [
        "Per-harness blocks; token and turn columns are never summed or "
        "compared across harnesses (the usage-accounting profile forbids "
        "cross-source token statistics, and a Codex `turn` is a structural "
        "analogue, not the same unit).", "",
        "Legacy correction and interrupt labels are candidate-sampler "
        "output only. They do not affect ranking and cannot justify a "
        "prompt, skill, rule, hook, agent, or process change.", ""]
    for harness in HARNESSES:
        h_main = [r for r in main if row_harness(r) == harness]
        h_prior = [r for r in prior_main
                   if row_harness(r) == harness]
        if not h_main and not h_prior:
            continue
        h_now, h_eligible = totals(h_main)
        h_prev, _ = totals(h_prior)
        lines += [f"### {harness}", "",
                  "| signal | this window | prior | delta |",
                  "|---|---|---|---|"]
        table = [("sessions", len(h_main), len(h_prior))]
        table += [(key, h_now[key], h_prev[key])
                  for key in ["tokens_out"] + COUNTERS]
        for key, a, b in table:
            delta = "n/a" if not b else f"{(a - b) / b * 100:+.0f}%"
            label = key + " (candidate only)" if key in LEGACY_TURN_COUNTERS else key
            if key in COUNTERS and h_eligible[key] < len(h_main):
                label += f" ({h_eligible[key]} of {len(h_main)} rows observable)"
            lines.append(f"| {label} | {a} | {b} | {delta} |")
        lines.append("")
        if h_main and any(h_eligible[key] for key in COUNTERS):
            lines.append("Per session: " + ", ".join(
                f"{key} {h_now[key] / h_eligible[key]:.1f}"
                for key in COUNTERS if h_eligible[key]))
            lines.append("")
        h_sub = [r for r in sub if row_harness(r) == harness]
        h_prior_sub = [r for r in prior_sub
                       if row_harness(r) == harness]
        if h_sub or h_prior_sub:
            h_sub_t, h_sub_eligible = totals(h_sub)
            lines.append(f"Subagent spend — {len(h_sub)} transcripts "
                         f"(prior window: {len(h_prior_sub)}): "
                         + ", ".join(f"{key} {h_sub_t[key]}"
                                     for key in ["tokens_out"] + COUNTERS
                                     if h_sub_eligible[key]))
        for extra_name in ("automation", "unknown"):
            extra = [r for r in split[extra_name]
                     if row_harness(r) == harness]
            if extra:
                lines.append(f"{extra_name.capitalize()} spend — "
                             f"{len(extra)} transcripts.")
        lines.append("")

    rankable = [r for r in main if row_harness(r) == "claude"]
    lines += ["## Candidate moments", ""]
    if len(main) > len(rankable):
        lines.append(f"_{len(main) - len(rankable)} main sessions are not "
                     f"friction-ranked - their harness emits no ranking "
                     f"signal. See the candidate-sampled section below._")
        lines.append("")
    ranked = sorted(rankable, key=friction_score, reverse=True)[:args.sessions]
    if not ranked:
        lines.append("_No sessions in window._")
    for row in ranked:
        score = friction_score(row)
        if score == 0:
            continue
        if not (claude_projects_dir() / row.get("transcript", "")).is_file():
            continue   # moved or unreadable: no headed block with nothing under it
        lines.append(f"### {row['date']} · {row.get('project') or '?'} · "
                     f"branch `{row.get('git_branch') or '-'}` · score {score}")
        lines.append(f"correction candidates {row.get('correction_candidates')}, "
                     f"interrupt candidates {row.get('interrupts')}, "
                     f"permission-mode changes {row.get('permission_mode_changes')}, "
                     f"repeat calls {row.get('repeat_calls')}, "
                     f"tool errors {row.get('tool_errors')}, "
                     f"queued prompts {row.get('queued_prompts')}")
        _append_moment_lines(lines, moments(row))
        lines.append("")

    codex_main = [r for r in main if r.get("harness") == "codex"]
    sampled = sorted((r for r in codex_main if _candidate_signal(r)),
                     key=_candidate_signal, reverse=True)[:args.sessions]
    if sampled:
        lines += ["## Codex moments — candidate-sampled, not ranked", "",
                  "Selected by candidate signals (a use the legacy rubric "
                  "allows); nothing here is a friction ranking.", ""]
    for row in sampled:
        evidence = moments(row)
        if not evidence:
            # A moved or unreadable rollout must not print a headed block
            # with nothing under it (spec D4.3).
            continue
        lines.append(f"### {row['date']} · {row.get('project') or '?'} · "
                     f"branch `{row.get('git_branch') or '-'}`")
        _append_moment_lines(lines, evidence)
        lines.append("")

    out_path = WORK_DIR / f"pack-{now.isoformat()}-{args.days}d.md"
    # A pack quotes real conversation. It got this check late: `label` refused to
    # write inside a repository from the start while this one wrote wherever it
    # was pointed, and the module docstring claimed the constraint for both.
    refuse_inside_repo(out_path)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(out_path)
    return EXIT_FLAGGED if any(friction_score(r) for r in ranked) else EXIT_CLEAN


# --- skills ----------------------------------------------------------------

def installed_skills():
    """Per-harness skill inventories, by directory name. glob on a missing
    directory yields nothing, so no existence guards. The Claude half keeps
    CLAUDE_DIR deliberately — the spec sanctions adding Codex roots, not
    moving the Claude one."""
    codex_home = codex_home_dir()
    claude = ({p.parent.name for p in (CLAUDE_DIR / "skills").glob("*/SKILL.md")}
              | {p.parent.name for p in
                 (CLAUDE_DIR / "plugins" / "cache").rglob("skills/*/SKILL.md")})
    # rglob from the plugin store root, not a "cache" subdirectory: the
    # Codex store nests differently from Claude's, and rglob covers
    # whichever layout a version uses.
    codex = ({p.parent.name for p in (codex_home / "skills").glob("*/SKILL.md")}
             | {p.parent.name for p in
                (codex_home / "plugins").rglob("skills/*/SKILL.md")}
             | {p.parent.name for p in
                (HOME / ".agents" / "skills").glob("*/SKILL.md")})
    return {"claude": claude, "codex": codex}


def cmd_skills(args):
    rows = load_rows()
    if args.days:
        start = (datetime.now(timezone.utc).date() - timedelta(days=args.days)).isoformat()
        rows = [r for r in rows if (r.get("date") or "") >= start]
    used = {"claude": Counter(), "codex": Counter()}
    signal_files = Counter()
    any_codex = False
    for row in rows:
        harness = row_harness(row)
        if harness == "codex":
            any_codex = True
        if row.get("skills_used"):
            signal_files[harness] += 1
        for name in row.get("skills_used") or []:
            used[harness][name.split(":")[-1]] += 1

    installed = installed_skills()
    window = f"last {args.days} days" if args.days else "all history"
    print(f"# Skill firing - {window}, {len(rows)} transcripts\n")
    print("## Fired\n")
    print("| skill | claude | codex | note |")
    print("|---|---|---|---|")
    every = Counter()
    for harness_counts in used.values():
        every.update(harness_counts)
    for name, _total in every.most_common():
        in_claude = name in installed["claude"]
        in_codex = name in installed["codex"]
        note = "" if (in_claude or in_codex) else "built-in command, or renamed"
        print(f"| {name} | {used['claude'][name] or ''} "
              f"| {used['codex'][name] or ''} | {note} |")
    if any_codex:
        print(f"\nCodex denominator: {signal_files['codex']} transcripts "
              f"carried any skill signal — a heuristic count, not "
              f"authoritative attribution (the source profile declares "
              f"none exists).")
    dormant_total = 0
    for harness in HARNESSES:
        dormant = sorted(installed[harness] - set(used[harness]))
        dormant_total += len(dormant)
        print(f"\n## Never fired — {harness} "
              f"({len(dormant)} of {len(installed[harness])} installed)")
        for name in dormant:
            print(f"         {name}")
    return EXIT_FLAGGED if dormant_total else EXIT_CLEAN


# --- subagents -------------------------------------------------------------

# One entry per column: what it means when it fires, the population it could
# have fired in, and what a person could change to move it. The wording matters
# for the two workspace signals - one is a target outside the workspace, the
# other is a command the guard could not check - because reading the second as
# the first is the mistake this report exists to stop.
#
# The last field is the honest answer to "what would I do about this?". Six of
# the seven are reachable by editing an instruction or a schema. The seventh is
# not, and says so rather than sitting in the table implying otherwise.
SUBAGENT_SIGNALS = [
    ("workspace_shape_unverifiable",
     "command shape the workspace guard could not verify (NOT proof it left)",
     "runs in an isolated workspace",
     "a standing rule asking for plain single commands"),
    ("workspace_target_outside",
     "named a target outside its workspace",
     "runs in an isolated workspace",
     "a dispatch instruction to stay inside the workspace"),
    ("schema_rejected",
     "structured output rejected against its own schema",
     "runs that made a structured-result call",
     "the schema, or the instruction that describes it"),
    ("missing_path_target",
     "read or searched a path that does not exist",
     "runs that read or searched",
     "an instruction to confirm a path before using it"),
    ("unread_before_write",
     "wrote or edited a file it had not read",
     "runs that wrote or edited a file",
     "an instruction to read before writing"),
    ("search_pattern_rejected",
     "search pattern, glob or file type rejected",
     "runs that searched",
     "an instruction about search syntax"),
    ("invalid_tool_input",
     "tool call rejected before it ran, on its input",
     "runs that called any tool",
     ""),
]

# Why the one blank above is blank, kept beside it so the two cannot drift:
# measured over the corpus, 23 of 28 are a tool input that would not parse. That
# is the call itself being malformed, and no instruction and no schema reaches
# it. The column is kept because the count is real and worth watching; it is
# marked unactionable because pretending otherwise sends someone editing
# instructions at a signal instructions cannot move.
UNACTIONABLE_NOTE = ("no instruction or schema edit reaches this one - the "
                     "call itself was malformed")

# The table above and the schema list name the same seven columns. Keeping them
# in step by hand is how a column quietly stops being reported, so a mismatch is
# an error the first time the file is imported rather than a silent gap.
assert {key for key, _, _, _ in SUBAGENT_SIGNALS} == set(SUBAGENT_COUNTERS)

ENDING_MEANING = {
    "structured": "handed a result back through a structured-result call",
    "text": "answered in text",
    "interrupted": "the caller stopped the run",
    "unanswered": "a tool call never got its result",
    "silent": "stopped without answering",
}


def quantile(sorted_values, fraction):
    """Nearest-rank quantile over a pre-sorted list. Empty input has no
    quantile; callers guard."""
    return sorted_values[int(round((len(sorted_values) - 1) * fraction))]


def project_key(row):
    """Which project a row belongs to, as an opaque key. Stored on the row
    since schema 7 (Codex rows hash their redacted cwd — spec D2.4); the
    first path component stays as the fallback for the Claude layout."""
    stored = row.get("project_key")
    if stored:
        return str(stored)
    return str(row.get("transcript") or "?").split("/")[0]


def concentration(rows, key):
    """The share of a signal's occurrences held by its largest single project.

    A count is a pattern only if more than one workflow produces it. Measured on
    the corpus, two of the seven columns are more than half one project, and a
    reader given the count alone cannot tell. Returned as a percentage, or None
    when the signal did not occur.
    """
    by_project = Counter()
    for row in rows:
        count = int(row.get(key) or 0)
        if count:
            by_project[project_key(row)] += count
    total = sum(by_project.values())
    if not total:
        return None
    return by_project.most_common(1)[0][1] / total * 100


def reporting_session_ids(extra):
    """Session ids whose rows this report must not count.

    The corpus holds the transcripts of whichever session is running the report,
    still being written, and counting them measures the act of measuring: of the
    runs that looked like they never answered, most were live transcripts and
    one belonged to the reporting session itself. Subagent transcripts carry
    their PARENT session's id, so one id drops a session and everything it
    dispatched. The ids are read, matched and discarded - never printed.
    """
    ids = {value for value in (extra or []) if value}
    current = os.environ.get("CLAUDE_CODE_SESSION_ID") or ""
    if current:
        ids.add(current)
    # The same tuple format-ctl reads (plugins/p/bin/format-ctl); adding a
    # harness means editing both.
    for name in ("CODEX_SESSION_ID", "CODEX_THREAD_ID"):
        value = os.environ.get(name) or ""
        if value:
            ids.add(value)
    return ids


def cmd_subagents(args):
    """Mechanical failures in subagent transcripts, over a window.

    Reads the ledger and nothing else. It never stats a file, never walks the
    corpus, and never treats a transcript's absence from the ledger as a
    signal - counting files the ledger had not caught up with, dated by their
    modification time, is what produced a 74 where the answer was 8. Windows
    are on the row's `date`, which comes from the first timestamp inside the
    transcript.
    """
    rows = [r for r in load_rows()
            if (r.get("population") or "") == "subagent"]
    if args.days:
        start = (datetime.now(timezone.utc).date()
                 - timedelta(days=args.days)).isoformat()
        rows = [r for r in rows if (r.get("date") or "") >= start]
    skip = reporting_session_ids(args.exclude_session)
    dropped = sum(1 for r in rows
                  if r.get("session_id") in skip
                  or (r.get("parent_session_id") or "") in skip)
    rows = [r for r in rows
            if r.get("session_id") not in skip
            and (r.get("parent_session_id") or "") not in skip]
    window = f"last {args.days} days" if args.days else "all history"
    if not rows:
        print(f"# Subagent lens - {window}\n\nNo subagent transcripts in window.")
        return EXIT_CLEAN

    taken = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"# Subagent lens - {window}, {len(rows)} transcripts\n")
    print(f"Taken {taken}, over {len(rows)} rows of the ledger as last "
          f"extracted. A snapshot, not a settled figure: the corpus is appended "
          f"to while it is read, so a recount minutes later moves the larger "
          f"counts. Compare closed windows, not consecutive runs.")
    if dropped:
        print(f"\n{dropped} rows written by the session running this report "
              f"were left out - transcripts still being written.")

    claude_rows = [r for r in rows if row_harness(r) == "claude"]
    codex_rows = [r for r in rows if r.get("harness") == "codex"]

    print("\n## Mechanical failures\n")
    print("Each share divides by the population the signal could have occurred "
          "in, named per row - never by every transcript. `top project` is the "
          "share of that signal's occurrences coming from its single largest "
          "project; a high one means one workflow repeating itself rather than "
          "a general pattern. No project is named.\n")
    if codex_rows:
        print(f"{len(codex_rows)} codex subagent rows are excluded from this "
              f"table - their harness emits none of these signals.\n")
    print("| signal | occurrences | transcripts | could occur in | share "
          "| top project | actionable |")
    print("|---|---|---|---|---|---|---|")
    flagged = 0
    tallies = []
    for key, _meaning, population, fix in SUBAGENT_SIGNALS:
        occurrences = sum(int(r.get(key) or 0) for r in claude_rows)
        carrying = sum(1 for r in claude_rows if int(r.get(key) or 0))
        eligible = sum(1 for r in claude_rows if key in (r.get("eligible") or []))
        flagged += occurrences
        tallies.append((occurrences, carrying, eligible, key, population, fix))
    for occurrences, carrying, eligible, key, population, fix in sorted(
            tallies, reverse=True):
        share = f"{carrying / eligible * 100:.1f}%" if eligible else "n/a"
        top = concentration(claude_rows, key)
        top_text = "n/a" if top is None else f"{top:.0f}%"
        print(f"| {key} | {occurrences} | {carrying} | {eligible} {population} "
              f"| {share} | {top_text} | {'yes' if fix else 'no'} |")

    print("\nWhat each one means, and what would move it:\n")
    for key, meaning, _population, fix in SUBAGENT_SIGNALS:
        print(f"- {key} - {meaning}. {fix if fix else UNACTIONABLE_NOTE}.")

    print("\nNot counted: a command that ran and returned a non-zero exit, a "
          "tool use the operator declined, and one a permission rule declined. "
          "None of the three is an agent mistake.")

    print("\nA run answered structurally if it handed a result back through a "
          "structured-result call at any point. Asking instead which record "
          "came last put runs that had done exactly that into `text`.")

    all_endings = Counter(r.get("ending") or "?" for r in rows)
    no_answer = all_endings.get("unanswered", 0) + all_endings.get("silent", 0)
    print("\nOn the counts below: answering through a structured-result "
          "call is answering, and an interrupted run is the caller's "
          "doing. Some of what is left is a transcript that had not "
          "finished being written when the ledger was built.")
    for harness, h_rows in (("claude", claude_rows), ("codex", codex_rows)):
        if not h_rows:
            continue
        print(f"\n## How {harness} runs answered\n")
        print("| ending | transcripts | share |")
        print("|---|---|---|")
        endings = Counter(r.get("ending") or "?" for r in h_rows)
        for name in ENDINGS:
            count = endings.get(name, 0)
            print(f"| {name} - {ENDING_MEANING[name]} | {count} | "
                  f"{count / len(h_rows) * 100:.1f}% |")
        turns = sorted(int(r.get("turns") or 0) for r in h_rows)
        print(f"\n## {harness} length\n\nturns: median "
              f"{quantile(turns, 0.5)}, p90 {quantile(turns, 0.90)}, "
              f"p95 {quantile(turns, 0.95)}, max {turns[-1]}.")
        for threshold in (100, 150, 200):
            print(f"  {sum(1 for t in turns if t >= threshold)} transcripts "
                  f"at {threshold} turns or more")
        print("\nA distribution, not a failure count: no turn number in this "
              "corpus marks a boundary between a long job and a runaway one.")
        h_no_answer = endings.get("unanswered", 0) + endings.get("silent", 0)
        print(f"\nFailed to answer ({harness}): {h_no_answer}.")

    return EXIT_FLAGGED if (flagged or no_answer) else EXIT_CLEAN
# --- label -----------------------------------------------------------------

def labels_file():
    """The labelling file, under the work directory. A function rather than a
    constant so a run with RETRO_HOME pointed elsewhere lands there."""
    return WORK_DIR / "labels.jsonl"


def refuse_inside_repo(path):
    """The labelling file carries message text. redact() mirrors the mechanical
    categories of the privacy audit and cannot recognise what a project is
    called, so the file carries identifiers redaction will not catch. It lives
    in the work directory and never inside a repository."""
    for parent in [path] + list(path.parents):
        if (parent / ".git").exists():
            print(f"refusing to write {path.name}: {parent} is a repository - "
                  "point RETRO_HOME outside every repo", file=sys.stderr)
            sys.exit(EXIT_CANNOT_RUN)


def _rank(sample_id):
    """Deterministic order over candidates. A seeded shuffle draws different
    turns once the corpus grows; hashing each candidate's own id keeps a rerun
    on the same turns."""
    return hashlib.sha1(f"{LABEL_SEED}|{sample_id}".encode("utf-8")).hexdigest()


def _sample_id(rel, index, extra=""):
    """Opaque and stable. The transcript path is hashed rather than stored: the
    file needs to identify a sample across reruns, not to name a session."""
    raw = f"{rel}#{index}#{extra}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def label_candidates():
    """Walk main-session transcripts once for three candidate pools: turns the
    classifier fires on, turns it does not, and flagged tool retries.

    Main sessions only - the thresholds exist to rank sessions, and subagent
    transcripts are excluded from that ranking.
    """
    projects = claude_projects_dir()
    fires, quiet, retries = [], [], []
    for path in sorted(projects.rglob("*.jsonl")):
        try:
            rel = path.relative_to(projects).as_posix()
        except ValueError:
            rel = path.name
        if "subagents/" in rel:
            continue
        prior = ""
        date = ""
        seen = {}
        first_input = {}
        for index, rec in enumerate(read_records(path)):
            if not isinstance(rec, dict):
                continue
            date = date or str(rec.get("timestamp") or "")[:10]
            rtype = rec.get("type")
            if rtype == "assistant":
                msg = rec.get("message") or {}
                # Accumulate across a turn's records, exactly as `measure` does.
                # Replacing here meant the sweep argued from a different length
                # than the ledger recorded for the same turn.
                prior += text_of(msg)
                content = msg.get("content")
                for block in content if isinstance(content, list) else []:
                    if not (isinstance(block, dict)
                            and block.get("type") == "tool_use"):
                        continue
                    name = block.get("name") or "?"
                    sig = signature(block.get("input"))
                    shown = redact(json.dumps(block.get("input"), sort_keys=True,
                                              default=str))[:LABEL_INPUT_CHARS]
                    key = (name, sig)
                    if sig and key in seen:
                        seen[key] += 1
                        retries.append({
                            "id": _sample_id(rel, index, name),
                            "kind": "retry", "predicted": "retry", "label": "",
                            "date": date, "tool": name, "repeat": seen[key],
                            "first_input": first_input.get(key, ""),
                            "repeat_input": shown,
                        })
                    else:
                        seen[key] = 1
                        first_input[key] = shown
            elif rtype == "user" and rec.get("toolUseResult") is None:
                body = text_of(rec.get("message") or {})
                if not body.strip():
                    continue
                # The same gate `measure` applies. Without it the sample is drawn
                # from a population the ledger does not count -- a third of these
                # records are the harness talking to itself -- and precision
                # measured on that sample describes a rule nobody runs.
                if not is_human_prompt(rec, body):
                    continue
                kind = classify_user_turn(body, len(prior))
                sample = {
                    "id": _sample_id(rel, index),
                    "kind": "turn", "predicted": kind or "none", "label": "",
                    "date": date,
                    # unredacted lengths: redact() shortens the reply and the
                    # stored context is truncated, so a sweep over the stored
                    # text would be measuring the wrong lengths.
                    "reply_chars": len(body.strip()),
                    "prior_chars": len(prior),
                    "after": redact(prior.strip()[-LABEL_AFTER_CHARS:]),
                    "said": redact(body.strip())[:LABEL_SAID_CHARS],
                }
                (fires if kind else quiet).append(sample)
                prior = ""
    return fires, quiet, retries


def draw_sample(pools):
    """Take LABEL_SAMPLE_SIZE from each pool, tagging every sample with the pool
    it came from and how big that pool was. The report needs both: 150 a side is
    not proportional to the corpus, so an unweighted precision would be an
    artefact of the sampling."""
    out = []
    for stratum, pool in pools.items():
        chosen = sorted(pool, key=lambda s: _rank(s["id"]))[:LABEL_SAMPLE_SIZE]
        for sample in chosen:
            sample["stratum"] = stratum
            sample["stratum_population"] = len(pool)
            sample["stratum_sampled"] = len(chosen)
        out += chosen
    return out


def write_labels(samples, path):
    with open(path, "w", encoding="utf-8") as fh:
        for sample in samples:
            fh.write(json.dumps(sample) + "\n")


def read_labels(path):
    if not path.exists():
        print(f"no labelling file at {path} - run `label` first", file=sys.stderr)
        sys.exit(EXIT_CANNOT_RUN)
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _predict_at(sample, max_chars, min_prior):
    """What the classifier would have said at a different pair of thresholds.

    Calls the real rule rather than restating it. It restated it once, and the
    copy went stale the next time the rule changed -- a sweep that disagrees
    with the row it is meant to explain is worse than no sweep.

    The stored lengths are used rather than the stored text because the text is
    redacted and truncated while the numbers are the reply's real lengths.
    """
    if sample["predicted"] == "interrupt":
        return "interrupt"
    if not (0 < sample["reply_chars"] <= max_chars
            and sample["prior_chars"] >= min_prior):
        return "none"
    # The stored text is redacted, which can only shorten it, so the length gate
    # above is applied from the stored numbers and the wording rules from the
    # text. A threshold pair is exactly those two numbers.
    return classify_user_turn(sample["said"].strip(),
                              sample["prior_chars"],
                              max_chars=max_chars,
                              min_prior=min_prior) or "none"


def _weighted(marked, predict):
    """Precision and recall per class, weighted back to corpus scale."""
    out = {}
    for cls in TURN_LABELS:
        tp = fp = fn = 0.0
        for sample in marked:
            weight = sample["stratum_population"] / sample["stratum_sampled"]
            got, want = predict(sample), sample["label"]
            if got == cls and want == cls:
                tp += weight
            elif got == cls:
                fp += weight
            elif want == cls:
                fn += weight
        out[cls] = (tp / (tp + fp) if tp + fp else float("nan"),
                    tp / (tp + fn) if tp + fn else float("nan"),
                    tp + fn)
    return out


def report_labels(samples):
    turns = [s for s in samples if s.get("kind") == "turn" and s.get("label")]
    retries = [s for s in samples if s.get("kind") == "retry" and s.get("label")]
    total_turns = sum(1 for s in samples if s.get("kind") == "turn")
    total_retries = sum(1 for s in samples if s.get("kind") == "retry")
    print(f"# Labelled {len(turns)} of {total_turns} turns, "
          f"{len(retries)} of {total_retries} retry candidates\n")
    if not turns:
        print("nothing marked yet", file=sys.stderr)
        return EXIT_CANNOT_RUN

    print(f"## Turn classifier at the settled thresholds "
          f"(reply <= {CORRECTION_MAX_CHARS}, "
          f"prior >= {CORRECTION_MIN_PRIOR_CHARS})\n")
    print("| class | precision | recall | corpus turns |")
    print("|---|---|---|---|")
    for cls, (p, r, n) in _weighted(
        turns,
        lambda s: _predict_at(s, CORRECTION_MAX_CHARS, CORRECTION_MIN_PRIOR_CHARS),
    ).items():
        print(f"| {cls} | {p:.2f} | {r:.2f} | {n:.0f} |")

    print("\n## Threshold sweep, correction class\n")
    print("| reply <= | prior >= | precision | recall | corpus corrections |")
    print("|---|---|---|---|---|")
    for max_chars in SWEEP_MAX_CHARS:
        for min_prior in SWEEP_MIN_PRIOR:
            p, r, n = _weighted(
                turns,
                lambda s, a=max_chars, b=min_prior: _predict_at(s, a, b),
            )["correction"]
            print(f"| {max_chars} | {min_prior} | {p:.2f} | {r:.2f} | {n:.0f} |")

    if retries:
        wasteful = sum(1 for s in retries if s["label"] == "wasteful")
        print(f"\n## tool_retries\n\nprecision {wasteful / len(retries):.2f} "
              f"over {len(retries)} marked candidates. Recall is not estimable "
              "from this sample: only flagged retries were drawn.")
        by_tool = Counter(s["tool"] for s in retries if s["label"] == "wasteful")
        print("\n| tool | marked wasteful |\n|---|---|")
        for name, count in by_tool.most_common():
            print(f"| {name} | {count} |")
    return EXIT_CLEAN


def cmd_label(args):
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    path = labels_file()
    refuse_inside_repo(path)
    if args.report:
        return report_labels(read_labels(path))
    projects = claude_projects_dir()
    if not projects.is_dir():
        print(f"no session directory at {projects}", file=sys.stderr)
        sys.exit(EXIT_CANNOT_RUN)
    if path.exists() and not args.resample:
        print(f"{path} exists - mark it, then run `label --report` "
              "(or `label --resample` to redraw)", file=sys.stderr)
        return EXIT_CANNOT_RUN

    kept = {s["id"]: s.get("label", "") for s in
            (read_labels(path) if path.exists() else [])}
    fires, quiet, retries = label_candidates()
    samples = draw_sample({"fires": fires, "quiet": quiet, "retries": retries})
    carried = 0
    for sample in samples:
        if kept.get(sample["id"]):
            sample["label"] = kept[sample["id"]]
            carried += 1
    write_labels(samples, path)
    print(f"pools: {len(fires)} firing, {len(quiet)} quiet, "
          f"{len(retries)} retry candidates "
          "(Claude transcripts only - classifier calibrated on Claude turns)")
    print(f"sampled {len(samples)} into {path}" +
          (f", {carried} marks carried over" if carried else ""))
    print('mark each line\'s "label": turns take one of '
          f"{'/'.join(TURN_LABELS)}, retries one of {'/'.join(RETRY_LABELS)}. "
          "Then: retro label --report")
    print("This file holds message text and stays in the work directory. "
          "Only aggregates from --report go anywhere tracked.")
    return EXIT_CLEAN


# --- effect ----------------------------------------------------------------

# Below this many sessions on either side, a difference is not worth reading.
# Not a significance test: the ledger is a census of what happened, not a sample
# from a population, and the sessions either side of a date differ in what they
# were about as much as in how they went. This is a floor for "do not bother",
# chosen because a handful of sessions can swing any per-session rate by half.
EFFECT_MIN_SESSIONS = 12


# The files that change how a session behaves. Their git history is a list of
# dates on which something was deliberately changed, which is exactly what
# `effect` needs and what nobody remembers unaided.
# Where the standing instructions live. Each is a thing whose edits should carry
# a date, because `effect` can only compare either side of a date that exists.
RULE_SOURCES = (
    ("standing instructions", CLAUDE_DIR / "CLAUDE.md"),
    ("memory store", CLAUDE_DIR / "memory"),
    ("skills", CLAUDE_DIR / "skills"),
)
# Past this many days of uncommitted edits, the trail is broken badly enough to
# say so: a fortnight of changes squashed into one commit is one date for many
# different decisions, which is no better than none.
RULES_STALE_DAYS = 7


def _git(repo, *args):
    import subprocess
    try:
        out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def cmd_rules(args):
    """Is every source of standing instruction under version control, and are its
    edits actually being committed?

    This exists because `effect` was built, run, and found exactly one date to
    offer -- not because nothing had changed, but because eleven days of changes
    were sitting uncommitted. A comparison tool is worth nothing if the thing it
    compares against is never recorded.
    """
    print("# Rule sources\n")
    print("| source | in a repo | uncommitted | last change |")
    print("|---|---|---|---|")
    problems = []
    for name, path in RULE_SOURCES:
        if not path.exists():
            print(f"| {name} | absent | - | - |")
            continue
        repo = _git(path if path.is_dir() else path.parent, "rev-parse", "--show-toplevel")
        if not repo:
            print(f"| {name} | **no** | - | - |")
            problems.append((name, path, "not under version control"))
            continue
        dirty = _git(repo, "status", "--short", "--", str(path)) or ""
        n_dirty = len([x for x in dirty.splitlines() if x.strip()])
        last = _git(repo, "log", "-1", "--format=%ad", "--date=short", "--", str(path)) or "never"
        stale = ""
        if last != "never":
            try:
                age = (datetime.now(timezone.utc).date()
                       - datetime.fromisoformat(last).date()).days
                stale = f" ({age}d ago)"
                if n_dirty and age >= RULES_STALE_DAYS:
                    problems.append((name, path,
                                     f"{n_dirty} uncommitted change(s), nothing "
                                     f"committed for {age} days"))
                elif n_dirty:
                    problems.append((name, path, f"{n_dirty} uncommitted change(s)"))
            except ValueError:
                pass
        print(f"| {name} | yes | {n_dirty or '-'} | {last}{stale} |")

    if not problems:
        print("\nEvery rule source is versioned and current. `effect` has dates to "
              "work with.")
        return EXIT_CLEAN

    print("\n## What is wrong with this\n")
    for name, path, why in problems:
        print(f"- **{name}** - {why}")
    print(f"""
An edit with no commit has no date, and `effect` compares metrics either side of
a date. Uncommitted changes are not merely untidy here: they are the reason a
rule change cannot be checked afterwards, and the reason nobody can tell a rule
that worked from one that was ignored.

Two ways to fix it, and the second is the one that lasts:

1. Commit what is outstanding now. Each rule change wants its own commit -- a
   fortnight of edits in one commit is a single date for many decisions.
2. Have the edits commit themselves. `plugins/p/bin/commit-rule-change`
   takes an edited path and commits it to whichever repository owns it, honours
   that repository's ignore rules so an excluded credentials file stays
   excluded, touches only the path it was given, and never fails its caller.
   Wire it to a PostToolUse hook on Edit and Write.""")
    return EXIT_FLAGGED


# Derived from RULE_SOURCES so the two cannot disagree. They did: one carried
# the settings file and the other did not, so a settings change could hand
# `effect` a date while `rules` never checked whether it was recorded at all.
RULE_PATHS = tuple(path.name for _, path in RULE_SOURCES)


def rule_change_dates(limit=25):
    """Dates on which the machine's standing instructions changed.

    Reads the git history of the configuration directory. Returns
    [(date, count, [subjects])], newest first. Empty if it is not a repository,
    which is not an error -- it just means this shortcut is unavailable.
    """
    out = _git(CLAUDE_DIR, "log", "--date=short", "--format=%ad\t%s", "--",
               *RULE_PATHS)
    if out is None:
        return []
    by_date = {}
    for line in out.splitlines():
        date, _, subject = line.partition("\t")
        if len(date) == 10:
            by_date.setdefault(date, []).append(subject.strip())
    return [(d, len(s), s) for d, s in sorted(by_date.items(), reverse=True)][:limit]


def print_candidates():
    """No date given: show the ones the machine knows about."""
    dates = rule_change_dates()
    print("# Dates something was deliberately changed\n")
    if not dates:
        print(f"No history found under {CLAUDE_DIR}. Either it is not a git "
              "repository, or the rule files have never been committed there.\n"
              "Pass a date yourself: retro effect --since YYYY-MM-DD")
        return EXIT_CANNOT_RUN
    rows = load_rows()
    dated = [r["date"] for r in rows if r.get("date")]
    first, last = (min(dated), max(dated)) if dated else ("", "")
    print(f"The ledger covers {first} to {last}. A date outside that has nothing "
          "to compare on one side.\n")
    print("| date | changes | in range | what changed |")
    print("|---|---|---|---|")
    for date, count, subjects in dates:
        ok = "yes" if first < date < last else "no"
        # Terminal only. A commit subject can name anything the operator was
        # working on, and this is why none of it is written to a file.
        print(f"| {date} | {count} | {ok} | {subjects[0][:56]} |")
    print("\nThen: retro effect --since YYYY-MM-DD [--harness codex|all]")
    print("Bear in mind the counters only see tool use, prompts, interrupts and "
          "permission changes. A rule about something else will not show up here "
          "however well it worked.")
    return EXIT_CLEAN


def cmd_effect(args):
    """Metrics before and after a date, so an edit can be checked against what
    followed it.

    This is the step the rest of the tool was missing. `pack` compares the last
    N days to the N before, anchored to today, which answers "how is it going"
    and cannot answer "did the thing I changed on the 12th do anything".
    """
    if not args.since:
        return print_candidates()
    try:
        cut = datetime.fromisoformat(args.since).date()
    except ValueError:
        print(f"not a date: {args.since} - use YYYY-MM-DD", file=sys.stderr)
        return EXIT_CANNOT_RUN

    rows = [r for r in load_rows() if r.get("date")]
    if args.days:
        lo = (cut - timedelta(days=args.days)).isoformat()
        hi = (cut + timedelta(days=args.days)).isoformat()
        rows = [r for r in rows if lo <= r["date"] <= hi]
    if args.harness != "all":
        rows = [r for r in rows if row_harness(r) == args.harness]

    before_rows = [r for r in rows if r["date"] < cut.isoformat()]
    after_rows = [r for r in rows if r["date"] >= cut.isoformat()]
    before_split = split_population(before_rows)
    after_split = split_population(after_rows)
    before, after = before_split["main"], after_split["main"]

    span = f", within {args.days} days either side" if args.days else ""
    print(f"# Effect around {cut}{span}\n")
    print(f"population: harness={args.harness}, main sessions only; "
          f"subagent, automation and unknown rows are spend and excluded\n")
    print(f"Before: {len(before)} sessions, {min((r['date'] for r in before), default='-')} "
          f"to {max((r['date'] for r in before), default='-')}")
    print(f"After:  {len(after)} sessions, {min((r['date'] for r in after), default='-')} "
          f"to {max((r['date'] for r in after), default='-')}\n")
    before_spend = (len(before_split["subagent"]) + len(before_split["automation"])
                    + len(before_split["unknown"]))
    after_spend = (len(after_split["subagent"]) + len(after_split["automation"])
                   + len(after_split["unknown"]))
    print(f"Spend (subagent + automation + unknown): before {before_spend}, "
          f"after {after_spend}\n")

    if not before or not after:
        print("Nothing to compare on one side of that date.", file=sys.stderr)
        return EXIT_CANNOT_RUN

    thin = min(len(before), len(after)) < EFFECT_MIN_SESSIONS
    if thin:
        print(f"**Too thin to read.** Fewer than {EFFECT_MIN_SESSIONS} sessions on one "
              "side. The numbers are printed because hiding them would be worse, "
              "but a handful of sessions swings any per-session rate by half.\n")

    b, b_elig = totals(before)
    a, a_elig = totals(after)
    # Two normalisations, because per session lies on its own. It moves whenever
    # sessions get longer or shorter: on the first real run of this command every
    # signal fell by half or more, INCLUDING turns and tokens, which is a change
    # in how the work was done rather than an effect of any edit. Per hundred
    # turns holds session length still, so a signal that moves there moved
    # relative to the work. When the two disagree, the second answers the
    # question and the first is telling you sessions changed shape.
    turns_b, turns_a = max(b["turns"], 1), max(a["turns"], 1)
    mixed = args.harness == "all"
    print("Main-session rows only, one population throughout.\n")
    if not legacy_turn_labels_allow("decision_support"):
        print("Legacy correction and interrupt guesses are omitted: their "
              "rubric allows candidate sampling, not decision support.\n")
    if mixed:
        print("turn-normalised rows omitted for mixed harnesses - a Codex "
              "turn is a structural analogue, not the same unit, and "
              "tokens are omitted too - Codex's tokens_out includes "
              "reasoning tokens and Claude's does not, and the "
              "usage-accounting profile forbids cross-source token "
              "statistics. tool_calls still appears, but it spans "
              "tool-call mappings that differ per harness.\n")
        print("| signal | /session before | after | change |")
        print("|---|---|---|---|")
    else:
        print("| signal | /session before | after | /100 turns before | after | change |")
        print("|---|---|---|---|---|---|")
    for key in COUNTERS + ([] if mixed else ["tokens_out"]):
        if key == "turns":
            continue
        if key in LEGACY_TURN_COUNTERS \
                and not legacy_turn_labels_allow("decision_support"):
            continue
        if not b_elig[key] or not a_elig[key]:
            continue
        sb, sa = b[key] / b_elig[key], a[key] / a_elig[key]
        if sb == 0 and sa == 0:
            continue
        fmt = "{:.0f}" if key == "tokens_out" else "{:.2f}"
        if mixed:
            delta = "n/a" if not sb else f"{(sa - sb) / sb * 100:+.0f}%"
            print(f"| {key} | {fmt.format(sb)} | {fmt.format(sa)} | {delta} |")
        else:
            tb, ta = b[key] / turns_b * 100, a[key] / turns_a * 100
            delta = "n/a" if not tb else f"{(ta - tb) / tb * 100:+.0f}%"
            print(f"| {key} | {fmt.format(sb)} | {fmt.format(sa)} | "
                  f"{fmt.format(tb)} | {fmt.format(ta)} | {delta} |")
    if not mixed:
        sb, sa = b["turns"] / len(before), a["turns"] / len(after)
        print(f"| **turns per session** | {sb:.1f} | {sa:.1f} | - | - | "
              f"{(sa - sb) / sb * 100:+.0f}% |")

    if mixed:
        print("\nThe change column compares the per-session figures. With no "
              "common turn unit across harnesses there is no turn-normalised "
              "column to check them against, so a change here may be nothing "
              "more than sessions changing shape.")
    else:
        print("\nThe change column compares the per-hundred-turn figures. Read "
              "the turns-per-session row first: if it moved a lot, every "
              "per-session column moved with it and means little on its own.")
    print("\nA change here is not proof the edit caused it. Sessions either side "
          "of a date differ in what they were about, and everything moves at "
          "once. Read it as: did the thing you targeted move at all, and did "
          "anything else move with it.")
    return EXIT_FLAGGED if thin else EXIT_CLEAN


def main():
    parser = argparse.ArgumentParser(prog="retro", description=__doc__)
    sub = parser.add_subparsers(required=True)

    p_extract = sub.add_parser("extract", help="measure transcripts into the ledger")
    p_extract.add_argument("--rebuild", action="store_true",
                           help="ignore prior state and re-measure everything")
    p_extract.set_defaults(func=cmd_extract)

    p_pack = sub.add_parser("pack", help="build an evidence pack for a window")
    p_pack.add_argument("--days", type=int, default=7)
    p_pack.add_argument("--sessions", type=int, default=8,
                        help="how many top-friction sessions to quote")
    p_pack.set_defaults(func=cmd_pack)

    p_skills = sub.add_parser("skills", help="which installed skills actually fire")
    p_skills.add_argument("--days", type=int, default=0,
                          help="restrict to a window; 0 means all history")
    p_skills.set_defaults(func=cmd_skills)

    p_sub = sub.add_parser("subagents",
                           help="mechanical failures in subagent transcripts")
    p_sub.add_argument("--days", type=int, default=30,
                       help="restrict to a window; 0 means all history")
    p_sub.add_argument("--exclude-session", action="append", default=[],
                       metavar="ID",
                       help="drop a session's rows, and its subagents' rows "
                            "with them. The session running the report is "
                            "dropped already, read from the environment.")
    p_sub.set_defaults(func=cmd_subagents)
    p_label = sub.add_parser("label",
                             help="sample turns and retries for hand labelling")
    p_label.add_argument("--report", action="store_true",
                         help="read the marked file back and report")
    p_label.add_argument("--resample", action="store_true",
                         help="redraw the sample, carrying existing marks over")
    p_label.set_defaults(func=cmd_label)

    p_effect = sub.add_parser("effect",
                              help="metrics before and after a date, to check an edit")
    p_effect.add_argument("--since", metavar="YYYY-MM-DD",
                          help="the date the change was made; omit to list the "
                               "dates the machine's own rule files changed")
    p_effect.add_argument("--days", type=int, default=0,
                          help="limit to this many days either side; 0 means all")
    p_effect.add_argument("--harness", choices=HARNESSES + ("all",),
                          default="claude",
                          help="population to compare; defaults to claude "
                               "because the rule-change dates this command "
                               "anchors on come from Claude config history")
    p_effect.set_defaults(func=cmd_effect)

    p_rules = sub.add_parser("rules",
                             help="are the standing instructions versioned, and "
                                  "are their edits being committed")
    p_rules.set_defaults(func=cmd_rules)

    args = parser.parse_args()
    sys.exit(args.func(args) or EXIT_CLEAN)


if __name__ == "__main__":
    main()
