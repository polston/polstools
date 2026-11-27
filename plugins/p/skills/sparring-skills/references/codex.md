# Codex adapter

Use only native Codex subagent transport. Policy and schemas come exclusively
from `../contract-v1.json` through `../scripts/skill_spar.py`.

1. For a bare invocation, run `resolve-defaults` first to obtain the stage(s),
   skill directory, and external `--work-dir`. Run `init` with
   `--harness codex` and an external `--work-dir` outside every Git repository
   to generate `worker-packets.json`.
2. Call `spawn_agent` twice in parallel with `fork_turns` set to `none` for
   `control_prompt` and `treatment_prompt` unchanged, retaining the parent
   model. Wait via `wait_agent` using a long timeout (`timeout_ms >= 60000`,
   avoiding short repeated polls) and never send mid-flight `send_message`
   nudges to running workers. Call `close_agent` on both workers immediately
   after collecting outputs.
3. Run `anonymize` and `judge-packet` on the round directory.
4. Call `spawn_agent` with `fork_turns` set to `none` once per request in
   `judge-packets.json`, passing each prompt unchanged. Wait via `wait_agent`
   (`timeout_ms >= 60000`, no mid-flight `send_message` nudges), then call
   `close_agent` on all judges immediately after collecting JSON outputs.
5. Save the returned JSON objects in canonical request order, run
   `unblind-score`, and optionally run `edit-gate` and `chain-advance`. Remove
   temporary directories and verify zero lingering Codex subagents remain open.
