"""CLI for deterministic owned-hook lifecycle evaluation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .annotation import _external
from .hook_lifecycle import evaluate_owned_hook_events


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--expected-invocations", type=int, required=True)
    parser.add_argument("--baseline-normalized-bytes", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    _external(args.events)
    _external(args.output)
    report = evaluate_owned_hook_events(
        args.events, expected_invocations=args.expected_invocations,
        baseline_normalized_bytes=args.baseline_normalized_bytes)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
