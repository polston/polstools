# Codex adapter

Use only native Codex subagent transport. Review policy comes exclusively from
`../contract-v1.json` through `../scripts/adequacy_review.py`.

1. Run the helper's `packet` command with `--harness codex` and the invocation
   values. Do not add conversation history or author rationale.
2. Call `spawn_agent` once per canonical review request with `fork_turns` set
   to `none`. Pass each prompt unchanged, retain the parent model, and wait for
   every reviewer result.
3. Put only the returned JSON objects in a temporary reviews file outside the
   repository. Run `distiller-packet` with that file, then pass its prompt
   unchanged to one `spawn_agent` call with `fork_turns` set to `none`.
4. Put the distiller's JSON object in a second temporary file. Run `distill`
   with both files and any adapter-observed unchecked behavior, then remove the
   temporary files.
5. Return `distilled` verbatim followed by `reviewer_verdicts`.
