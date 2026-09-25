"""measure_codex over synthetic rollouts — every counter mapping."""

import tempfile
import unittest
from pathlib import Path

import fixtures as fx
from test_retro_extract import load_retro, write_rollout

T = "2026-08-01T12:00:00.000Z"
T2 = "2026-08-01T12:30:00.000Z"


class CodexReducer(unittest.TestCase):
    def setUp(self):
        self.retro = load_retro()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def measure(self, recs):
        path = write_rollout(self.root, "2026/08/01/rollout-a.jsonl", recs)
        return self.retro.measure_codex(path, self.root)

    def test_identity_population_and_ineligible(self):
        row = self.measure([fx.rollout_meta(T), fx.rollout_user("hi", T),
                            fx.rollout_assistant("yo", T2)])
        self.assertEqual("codex", row["harness"])
        self.assertEqual("main", row["population"])
        self.assertEqual("sess-cx-1", row["session_id"])
        self.assertEqual("main", row["git_branch"])
        self.assertEqual("2026-08-01", row["date"])
        self.assertEqual(1800, row["duration_s"])
        self.assertEqual(sorted(["tool_errors", "queued_prompts",
                                 "permission_mode_changes"]),
                         sorted(row["ineligible"]))
        self.assertEqual("thread_source", row["population_source"])
        self.assertEqual([], row["eligible"])
        self.assertTrue(row["project_key"].startswith("cx-"))
        self.assertEqual("2026/08/01/rollout-a.jsonl", row["transcript"])

    def test_subagent_and_automation_populations(self):
        row = self.measure([fx.rollout_meta(T, thread_source="subagent",
                                            parent="parent-1"),
                            fx.rollout_user("go", T),
                            fx.rollout_assistant("done", T)])
        self.assertEqual("subagent", row["population"])
        self.assertEqual("parent-1", row["parent_session_id"])
        row = self.measure([fx.rollout_meta(T, thread_source="automation"),
                            fx.rollout_user("cron", T),
                            fx.rollout_assistant("ran", T)])
        self.assertEqual("automation", row["population"])

    def test_absent_thread_source_falls_back(self):
        row = self.measure([fx.rollout_meta(T, thread_source=None,
                                            parent="parent-2"),
                            fx.rollout_user("x", T),
                            fx.rollout_assistant("y", T)])
        self.assertEqual("subagent", row["population"])
        self.assertEqual("fallback", row["population_source"])
        # Absent with no parent id: unknown, NEVER main — an unclassified
        # file must not enter the per-session-rate population (spec D2.2).
        row = self.measure([fx.rollout_meta(T, thread_source=None),
                            fx.rollout_user("x", T),
                            fx.rollout_assistant("y", T)])
        self.assertEqual("unknown", row["population"])
        row = self.measure([fx.rollout_meta(T, thread_source="new-kind"),
                            fx.rollout_user("x", T),
                            fx.rollout_assistant("y", T)])
        self.assertEqual("unknown", row["population"])

    def test_every_machine_opener_filters_on_the_codex_path(self):
        retro = self.retro
        for opener in retro.MACHINE_PROMPT_OPENERS:
            if not opener.startswith("<"):
                continue   # the prose openers are Claude-transcript shapes
            body = opener + ' x="1">' if not opener.endswith(">") else opener
            row = self.measure([fx.rollout_meta(T),
                                fx.rollout_user(body + "\npayload", T),
                                fx.rollout_user("a real prompt", T),
                                fx.rollout_assistant("ok", T)])
            self.assertEqual(1, row["user_prompts"],
                             "opener not filtered: %r" % opener)

    def test_wrapper_tagged_messages_are_not_prompts(self):
        row = self.measure([
            fx.rollout_meta(T),
            fx.rollout_user("<task-notification>done</task-notification>", T),
            fx.rollout_user('<codex_internal_context kind="x">y', T),
            fx.rollout_user("a real prompt", T),
            fx.rollout_assistant("ok", T),
        ])
        self.assertEqual(1, row["user_prompts"])

    def test_correction_and_approval_classification(self):
        row = self.measure([
            fx.rollout_meta(T),
            fx.rollout_user("do the thing", T),
            fx.rollout_assistant("x" * 400, T),
            fx.rollout_user("no, wrong file", T),
            # Long enough to clear CORRECTION_MIN_PRIOR_CHARS (200); a
            # short prior turn makes "sure" classify as nothing.
            fx.rollout_assistant("z" * 400, T),
            fx.rollout_user("sure", T),
            fx.rollout_assistant("finished", T),
        ])
        self.assertEqual(1, row["correction_candidates"])
        self.assertEqual(1, row["approval_turns"])
        self.assertEqual(3, row["user_prompts"])

    def test_turns_and_tool_calls(self):
        row = self.measure([
            fx.rollout_meta(T),
            fx.rollout_user("go", T),
            fx.rollout_assistant("working", T),
            fx.rollout_call("exec", '{"cmd": "ls"}', T, call_id="c1"),
            fx.rollout_output(T, call_id="c1"),
            fx.rollout_event("web_search_end", T),
        ])
        # assistant message + call item = 2 turns; call + web_search = 2 tools
        self.assertEqual(2, row["turns"])
        self.assertEqual(2, row["tool_calls"])
        self.assertEqual("text", row["ending"])

    def test_patch_apply_end_is_paired_not_double_counted(self):
        # patch_apply_end is excluded from CODEX_TOOL_EVENTS wholesale, by
        # event name, based on the measured pairing overlap recorded above
        # that constant - the reducer never matches call_ids at runtime.
        # This fixture's shared call_id only documents the record shape that
        # made that measurement true; it is not what the code checks.
        row = self.measure([
            fx.rollout_meta(T),
            fx.rollout_user("go", T),
            fx.rollout_call("apply_patch", '{"patch": "..."}', T,
                            call_id="c1"),
            fx.rollout_output(T, call_id="c1"),
            {"type": "event_msg", "timestamp": T,
             "payload": {"type": "patch_apply_end", "call_id": "c1"}},
        ])
        self.assertEqual(1, row["tool_calls"])

    def test_repeat_calls_parse_args_and_skip_polls(self):
        row = self.measure([
            fx.rollout_meta(T), fx.rollout_user("go", T),
            fx.rollout_call("exec", '{"a": 1, "b": 2}', T, call_id="c1"),
            fx.rollout_output(T, call_id="c1"),
            fx.rollout_call("exec", '{"b": 2, "a": 1}', T, call_id="c2"),
            fx.rollout_output(T, call_id="c2"),
            fx.rollout_call("wait", '{"a": 1}', T, call_id="c3"),
            fx.rollout_call("wait", '{"a": 1}', T, call_id="c4"),
            fx.rollout_call("exec", "", T, call_id="c5"),
            fx.rollout_call("exec", "", T, call_id="c6"),
            fx.rollout_assistant("done", T),
        ])
        # key-order-insensitive repeat counted once; polls and empty args never
        self.assertEqual(1, row["repeat_calls"])

    def test_interrupts_and_endings(self):
        row = self.measure([fx.rollout_meta(T), fx.rollout_user("go", T),
                            fx.rollout_event("turn_aborted", T)])
        self.assertEqual(1, row["interrupts"])
        self.assertEqual("interrupted", row["ending"])
        row = self.measure([
            fx.rollout_meta(T), fx.rollout_user("go", T),
            fx.rollout_call("exec", '{"a": 1}', T, call_id="dangling"),
        ])
        self.assertEqual("unanswered", row["ending"])

    def test_tokens_with_reset_banking(self):
        row = self.measure([
            fx.rollout_meta(T), fx.rollout_user("go", T),
            fx.token_count_event(T, 100, 40, 50, 10),
            fx.token_count_event(T, 20, 5, 8, 2),   # reset: totals dropped
            fx.token_count_event(T, 30, 10, 12, 3),
            fx.rollout_assistant("done", T),
        ])
        # banked (100-40) + final (30-10) = 80
        self.assertEqual(80, row["tokens_in"])
        # banked (50+10) + final (12+3) = 75
        self.assertEqual(75, row["tokens_out"])
        self.assertEqual(50, row["cache_read"])   # banked 40 + final 10

    def test_tokens_with_extra_field_does_not_raise(self):
        # No stray key has ever been observed in total_token_usage (probed
        # across the full corpus: 65,845 events, six key names, all
        # numeric). This is a defensive test for a version that has not
        # shipped: iterating current.items() blindly would bank a future
        # non-numeric sibling too and int() would raise inside
        # measure_outcome, which promises never to raise, losing the whole
        # extract pass with no ledger written. CODEX_TOKEN_FIELDS pins the
        # reducer to the four known-numeric fields so that can't happen.
        extra = fx.rollout_event("token_count", T, info={"total_token_usage": {
            "input_tokens": 100, "cached_input_tokens": 40,
            "output_tokens": 50, "reasoning_output_tokens": 10,
            "total_tokens": 160, "model": "x"}})
        reset = fx.rollout_event("token_count", T2, info={"total_token_usage": {
            "input_tokens": 20, "cached_input_tokens": 5,
            "output_tokens": 8, "reasoning_output_tokens": 2,
            "total_tokens": 30, "model": "x"}})   # reset: totals dropped
        row = self.measure([
            fx.rollout_meta(T), fx.rollout_user("go", T),
            extra, reset, fx.rollout_assistant("done", T2),
        ])
        # banked (100-40) + final (20-5) = 75
        self.assertEqual(75, row["tokens_in"])
        # banked (50+10) + final (8+2) = 70
        self.assertEqual(70, row["tokens_out"])

    def test_skills_from_wrapper_blocks(self):
        row = self.measure([
            fx.rollout_meta(T), fx.rollout_user("go", T),
            fx.rollout_user("<skill>\n<name>p:doctor</name>\n<path>x</path>"
                            "\n</skill>", T),
            fx.rollout_assistant("ok", T),
        ])
        self.assertEqual(["doctor"], row["skills_used"])
        self.assertEqual(1, row["skill_runs"])

    def test_compacted_flag_and_first_meta_wins(self):
        row = self.measure([
            fx.rollout_meta(T, thread_source="user"),
            {"type": "compacted", "timestamp": T, "payload": {}},
            fx.rollout_meta(T, thread_source="subagent"),
            fx.rollout_user("go", T), fx.rollout_assistant("ok", T),
        ])
        self.assertTrue(row["compacted"])
        self.assertEqual("main", row["population"])

    def test_cwd_redaction(self):
        home_cwd = str(Path.home() / "work" / "thing")
        row = self.measure([fx.rollout_meta(T, cwd=home_cwd),
                            fx.rollout_user("go", T),
                            fx.rollout_assistant("ok", T)])
        self.assertNotIn(str(Path.home()), row["project"])
        self.assertIn("~", row["project"])

    def test_no_message_records_is_not_a_transcript(self):
        self.assertIsNone(self.measure([fx.rollout_meta(T)]))


if __name__ == "__main__":
    unittest.main()
