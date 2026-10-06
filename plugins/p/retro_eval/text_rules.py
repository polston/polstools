"""Text rules shared by bin/retro.py and the evaluation layer.

Redaction and the user-turn classifier live here so both the stdlib script and
the importable evaluation package call one implementation. bin/retro.py
re-exports every name below; edit the rule here, not there.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path


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


# --- Redaction -------------------------------------------------------------

@lru_cache(maxsize=1)
def _redaction_patterns():
    """Compile once.

    Covers the account's home directory, email addresses, IPv4 addresses, MAC
    addresses, long token-shaped strings, large dollar amounts, billing and
    plan field names, and, last, the account name on its own.
    """
    home_path = Path.home()
    home = str(home_path)
    user = home_path.name
    pats = [
        (re.compile(re.escape(home), re.I), "~"),
        (re.compile(re.escape(home.replace("\\", "/")), re.I), "~"),
        (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"), "<email>"),
        (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "<ip>"),
        (re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b"), "<mac>"),
        (re.compile(r"\b[A-Za-z0-9_-]{32,}\b"), "<long-token>"),
        # Spend and plan state: dollar amounts of four or more digits or with
        # thousands separators. A measured spend figure is confidential and
        # is shaped like nothing else here, so every identity pattern above
        # is structurally blind to it.
        (re.compile(r"\$\d{1,3}(,\d{3})+(\.\d{2})?|\$\d{4,}(\.\d{2})?"), "<amount>"),
        # Literals split across adjacent string pieces so this file does not
        # match the pattern it defines; Python rejoins them at parse time.
        # Account billing and plan field names. The pattern avoids \b and \w,
        # using explicit character classes instead.
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


# C0 controls except tab, newline and carriage return, DEL, and the C1
# controls. A transcript field printed or written verbatim could otherwise
# carry a terminal escape sequence that runs when the output is shown.
_CONTROL_CHARS = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def strip_controls(text):
    """`text` with terminal control characters replaced by `?`."""
    return _CONTROL_CHARS.sub("?", str(text))


def redact(text):
    """Strip machine-identifying and credential-shaped values from text.

    Runs before anything is written to a pack. A pack file on disk must already
    be safe to read aloud — redacting at read time would be too late.
    """
    if not text:
        return ""
    text = strip_controls(text)
    for pattern, replacement in _redaction_patterns():
        text = pattern.sub(replacement, text)
    return text


_INTERRUPT = re.compile(r"\[request interrupted", re.I)


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
    head = re.split(r"[,;.!]", stripped, maxsplit=1)[0].strip()
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


def content_text(content, block_types, bare_strings=False, skipped=None):
    """The text-bearing pieces of a `content` field, as an unjoined list of
    strings -- callers decide how to filter and join, since the two shapes
    that flatten through here disagree about both.

    A bare string in a content list passes through only when `bare_strings`
    is set (Claude content mixes plain strings and typed blocks; Codex
    content never does). A `tool_result` block, when its type is in
    `block_types`, contributes its own string `content` field instead of a
    `text` key -- the one shape neither format's other block types use.

    A text piece that is not a string is dropped, and tallied under `bad_text`
    in `skipped` when the caller passes a Counter: one forward-incompatible
    block must not cost the whole transcript.
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
    good = [p for p in parts if isinstance(p, str)]
    if skipped is not None and len(good) != len(parts):
        skipped["bad_text"] += len(parts) - len(good)
    return good


def text_of(message, skipped=None):
    """Flatten a message's content to plain text. Content is a string on some
    records and a list of typed blocks on others."""
    if not isinstance(message, dict):
        return ""
    parts = content_text(message.get("content"), ("text", "tool_result"),
                         bare_strings=True, skipped=skipped)
    return "\n".join(p for p in parts if p)


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
