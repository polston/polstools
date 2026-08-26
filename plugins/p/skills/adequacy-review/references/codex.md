# Codex adapter

Use only native Codex subagent transport. Review policy comes exclusively from
`../contract-v1.json` through `../scripts/adequacy_review.py`.

1. Run the helper's `packet` command with `--harness codex` and the invocation
   values. Do not add conversation history or author rationale.
2. Call `spawn_agent` once per canonical review request with `fork_turns` set
   to `none`. Pass each prompt and schema unchanged, retain the parent model,
   and wait for every reviewer result.
3. Put only the returned JSON objects in a temporary reviews file outside the
   repository. Run the helper's `distill` command with that file and any honest
   unchecked-behavior statements, then remove the temporary file.
4. Return `distilled` verbatim followed by `reviewer_verdicts`.
