# Claude Code adapter

Use only native Claude Code Agent transport. Review policy comes exclusively
from `../contract-v1.json` through `../scripts/adequacy_review.py`.

1. Run the helper's `packet` command with `--harness claude-code` and the
   invocation values. Do not add conversation history or author rationale.
2. In one parallel Agent request, spawn one read-only cold reviewer for every
   canonical review request. Use `sonnet` when available, pass each prompt
   unchanged, and wait for every reviewer result.
3. Put only the returned JSON objects in a temporary reviews file outside the
   repository. Run `distiller-packet` with that file, then pass its prompt
   unchanged to one read-only native Agent using `sonnet` when available.
4. Put the distiller's JSON object in a second temporary file. Run `distill`
   with both files and any adapter-observed unchecked behavior, then remove the
   temporary files.
5. Return `distilled` verbatim followed by `reviewer_verdicts`.
