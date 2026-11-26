# Codex adapter

Use only native Codex subagent transport. Policy and schemas come exclusively
from `../contract-v1.json` through `../scripts/skill_spar.py`.

1. Run `init` with `--harness codex` and an external `--work-dir` outside every
   Git repository to generate `worker-packets.json`.
2. Call `spawn_agent` twice in parallel with `fork_turns` set to `none` for
   `control_prompt` and `treatment_prompt` unchanged. Retain the parent model
   and wait for both outputs.
3. Run `anonymize` and `judge-packet` on the round directory.
4. Call `spawn_agent` with `fork_turns` set to `none` once per request in
   `judge-packets.json`, passing each prompt unchanged.
5. Save the returned JSON objects in canonical request order, run
   `unblind-score`, and optionally run `edit-gate` and `chain-advance`. Clean
   up temporary directories when finished.
