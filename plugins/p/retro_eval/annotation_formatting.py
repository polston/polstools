"""Safe, client-neutral structure recovery for flattened annotation excerpts."""

from __future__ import annotations

import re


REFERENCE = r"(?:(?:[A-Za-z][A-Za-z0-9_/-]*\.)?\d+(?:\.\d+)*(?:/\d+)*)"
REFERENCE_POINT = re.compile(
    r"^(?P<reference>%s)\s*-\s+(?P<text>.+)$" % REFERENCE)
STRUCTURAL_BREAK = re.compile(
    r"\s+(?=(?:#{1,4}\s|\*\*[A-Z][^*]{0,32}:\*\*|"
    r"\d{1,2}(?:\.\d+)*[.)]\s+|%s\s*-\s+|---\s+))" % REFERENCE)
SENTENCE = re.compile(r"[^.!?]+[.!?]+(?:\s+|$)|[^.!?]+$")


def _paragraph_chunks(text, limit=420):
    if len(text) <= limit:
        return (text,)
    chunks = []
    current = ""
    for sentence in SENTENCE.findall(text) or (text,):
        candidate = (current + " " + sentence.strip()).strip()
        if current and len(candidate) > limit:
            chunks.append(current)
            current = sentence.strip()
        else:
            current = candidate
    if current:
        chunks.append(current)
    return tuple(chunks)


def evidence_blocks(text):
    """Recover headings, lists, and reference-labelled points without HTML."""
    blocks = []
    lines = [line.strip() for line in STRUCTURAL_BREAK.sub("\n", str(text)).splitlines()
             if line.strip()]
    for line in lines:
        heading = re.match(r"^#{1,4}\s+(.+)$", line)
        named_heading = re.match(r"^\*\*([A-Z][^*]{0,40}:)\*\*$", line)
        ordered = re.match(r"^\d{1,2}(?:\.\d+)*[.)]\s+(.+)$", line)
        unordered = re.match(r"^[-*]\s+(.+)$", line)
        reference = REFERENCE_POINT.match(line)
        if heading:
            blocks.append({"type": "heading", "text": heading.group(1)})
        elif named_heading:
            blocks.append({"type": "heading", "text": named_heading.group(1)})
        elif ordered:
            blocks.append({"type": "ordered", "text": ordered.group(1)})
        elif unordered:
            blocks.append({"type": "unordered", "text": unordered.group(1)})
        elif reference:
            blocks.append({
                "type": "reference", "reference": reference.group("reference"),
                "text": reference.group("text"),
            })
        elif line.startswith("|") and line.endswith("|"):
            blocks.append({"type": "table", "text": line})
        elif line.startswith("---"):
            blocks.append({"type": "divider", "text": line[3:].strip()})
        else:
            blocks.extend({"type": "paragraph", "text": chunk}
                          for chunk in _paragraph_chunks(line))
    return blocks
