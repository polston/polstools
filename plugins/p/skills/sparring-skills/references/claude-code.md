# Claude Code adapter

Use only native Claude Code Agent transport. Policy and schemas come
exclusively from `../contract-v1.json` through `../scripts/skill_spar.py`.

1. For a bare invocation, run `resolve-defaults` first to obtain the stage(s),
   skill directory, and external `--work-dir`. Run `init` with
   `--harness claude-code` and an external `--work-dir` outside every Git
   repository to generate `worker-packets.json`.
2. In one parallel Agent request, spawn two fresh cold read-only workers using
   `control_prompt` and `treatment_prompt` unchanged. Wait for both outputs.
3. Run `anonymize` and `judge-packet` on the round directory.
4. In one parallel Agent request, spawn one fresh cold read-only judge per
   request in `judge-packets.json`, passing each prompt unchanged.
5. Write the returned JSON objects in canonical request order to a temporary
   responses file, run `unblind-score`, and optionally run `edit-gate` and
   `chain-advance`. Clean up temporary directories when finished.
