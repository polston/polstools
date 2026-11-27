# Antigravity adapter

Use only native Antigravity subagent transport. Policy and schemas come
exclusively from `../contract-v1.json` through `../scripts/skill_spar.py`.

1. For a bare invocation, run `resolve-defaults` first to obtain the stage(s),
   skill directory, and external `--work-dir`. Run `init` with
   `--harness antigravity` and an external `--work-dir` outside every Git
   repository to generate `worker-packets.json`.
2. In parallel using fresh isolated `invoke_subagent` calls (inheriting the
   parent model), pass `control_prompt` and `treatment_prompt` unchanged. Wait
   for both outputs and retire both workers immediately after completion.
3. Run `anonymize` and `judge-packet` on the round directory.
4. Spawn one fresh cold `invoke_subagent` call per request in
   `judge-packets.json`, passing each prompt unchanged, then retire the judges.
5. Save the returned JSON objects in canonical request order, run
   `unblind-score`, and optionally run `edit-gate` and `chain-advance`. Clean
   up temporary directories and verify zero active subagents remain.
