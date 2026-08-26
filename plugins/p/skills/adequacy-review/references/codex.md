# Codex adapter

Use only native Codex subagent transport. Review policy comes exclusively from
`../contract-v1.json` through `../scripts/adequacy_review.py`.

1. Run the helper's `packet` command with `--harness codex`, the invocation
   values, and one `--exclude` per exclusion. Save its complete JSON result in
   a temporary packet file outside the repository. Do not add conversation
   history or author rationale.
2. Call `spawn_agent` once per canonical review request with `fork_turns` set
   to `none`. Pass each prompt unchanged, retain the parent model, and wait for
   every reviewer result.
3. Put returned JSON objects in canonical request order, not completion order,
   in a temporary reviews file outside the repository. Run `distiller-packet`
   with that file and `--packet-file`, then pass its prompt unchanged to one
   `spawn_agent` call with `fork_turns` set to `none`.
4. Put the distiller's JSON object in a second temporary file. Run `distill`
   with both files, the same `--packet-file`, and any adapter-observed unchecked
   behavior, then remove all temporary files.
5. Return `distilled` verbatim.
