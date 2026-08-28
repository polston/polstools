"""Build a synthetic Claude Code transcript corpus for tests.

Mirrors the real layout: main-thread transcripts at
<root>/<project>/<session>.jsonl, subagent transcripts one level deeper at
<root>/<project>/<session>/subagents/agent-<n>.jsonl.
"""

import json
from collections import Counter


def usage_row(rid, model, read, w1, w5, out, when):
    """One assistant JSONL record carrying a usage block."""
    return {
        "type": "assistant",
        "requestId": rid,
        "timestamp": when.isoformat().replace("+00:00", "Z"),
        "message": {
            "model": model,
            "usage": {
                "input_tokens": 0,
                "cache_read_input_tokens": read,
                "output_tokens": out,
                "cache_creation": {
                    "ephemeral_1h_input_tokens": w1,
                    "ephemeral_5m_input_tokens": w5,
                },
            },
        },
    }


def build_corpus(root, sessions):
    """Write `sessions` to disk under `root`. Returns the root Path."""
    counters = Counter()
    for spec in sessions:
        project = root / spec["project"]
        name = spec["session"]
        if spec.get("subagent"):
            directory = project / name / "subagents"
            if spec.get("workflow"):
                # 909 of ~1,540 real subagent transcripts sit two levels
                # deeper than a plain subagent, under a workflow directory.
                # A fixture that cannot build that layout cannot pin the
                # classifier against the shape most of the corpus has.
                directory = directory / "workflows" / spec["workflow"]
            index = counters[(spec["project"], name)]
            counters[(spec["project"], name)] += 1
            path = directory / ("agent-%d.jsonl" % index)
        else:
            directory = project
            path = directory / (name + ".jsonl")
        directory.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            for row in spec["rows"]:
                handle.write(json.dumps(row) + "\n")
    return root


def claude_user(text, when, source="typed"):
    """One typed user record, minimal but shaped like the real thing."""
    return {
        "type": "user",
        "timestamp": when.isoformat().replace("+00:00", "Z"),
        "sessionId": "sess-claude-1",
        "cwd": "/tmp/proj",
        "gitBranch": "main",
        "version": "9.9.9",
        "promptSource": source,
        "message": {"role": "user",
                    "content": [{"type": "text", "text": text}]},
    }


def claude_assistant(text, when, tools=()):
    """One assistant record; `tools` is (name, input_dict) pairs."""
    content = [{"type": "text", "text": text}]
    for index, (name, tool_input) in enumerate(tools):
        content.append({"type": "tool_use", "id": "tu-%d" % index,
                        "name": name, "input": tool_input})
    return {
        "type": "assistant",
        "timestamp": when.isoformat().replace("+00:00", "Z"),
        "sessionId": "sess-claude-1",
        "cwd": "/tmp/proj",
        "message": {"role": "assistant", "content": content,
                    "usage": {"input_tokens": 10, "output_tokens": 20,
                              "cache_read_input_tokens": 5}},
    }
