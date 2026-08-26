# Claude Code adapter

Use only native Claude Code Agent transport. Review policy comes exclusively
from `../contract-v1.json` through `../scripts/adequacy_review.py`.

1. Run the helper's `packet` command with `--harness claude-code` and the
   invocation values. Do not add conversation history or author rationale.
2. In one parallel Agent request, spawn one read-only cold reviewer for every
   canonical review request. Use `sonnet` when available and pass each prompt
   and schema unchanged. Wait for every reviewer result.
3. Put only the returned JSON objects in a temporary reviews file outside the
   repository. Run the helper's `distill` command with that file and any honest
   unchecked-behavior statements, then remove the temporary file.
4. Return `distilled` verbatim followed by `reviewer_verdicts`.
