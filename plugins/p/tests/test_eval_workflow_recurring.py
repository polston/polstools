import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retro_eval.workflow.recurring import disposition, select_cards
from retro_eval.workflow.registry import Registry
from retro_eval.workflow.packs import diagnosis
from test_eval_workflow_records import TIME


def candidate(key, positive=False, severe=False):
    return {'key': key, 'priority': 1, 'positive_practice': positive, 'severe_verified': severe}


class RecurringTests(unittest.TestCase):
    def test_due_once_and_severe_escapes_ordinary_suppression(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Registry(Path(temp))
            try:
                record = disposition(store, 'old', 'snoozed', reason='Wait for follow-up', due_at=TIME,
                    next_action='Inspect the newly mature outcome evidence.')
                rows = [candidate('old'), candidate('new-severe', severe=True)]
                first = select_cards(rows, [record], now=TIME, limit=2)
                self.assertEqual(first['cards'][0]['key'], 'new-severe')
                self.assertEqual(first['cards'][1]['category'], 'due_followup')
                second = select_cards(rows, [record], now=TIME, limit=2,
                    seen_candidates=['new-severe'], seen_followups=first['followup_tokens'])
                self.assertEqual(second['cards'], [])
            finally:
                store.close()

    def test_dispositions_are_versioned_idempotent_and_not_global_adoption(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Registry(Path(temp))
            try:
                first = disposition(store, 'one', 'provisional', reason='Scoped preference')
                same = disposition(store, 'one', 'provisional', reason='Scoped preference')
                last = disposition(store, 'one', 'retired', reason='No useful result')
                self.assertEqual(first.ref, same.ref)
                self.assertEqual(last.ref.revision, 2)
                self.assertEqual(last.payload['benefit_status'], 'unknown')
                self.assertFalse(last.payload['global_workflow_changed'])
                self.assertEqual(select_cards([candidate('one')], [last], now=TIME)['cards'], [])
            finally:
                store.close()

    def test_only_boolean_verified_severity_can_override_dismissal(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Registry(Path(temp))
            try:
                record = disposition(store, 'one', 'dismissed', reason='No current action')
                for flag in (False, None, 'false', 'true', 0, 1, [], [False], {}):
                    with self.subTest(flag=flag):
                        row = candidate('one', severe=flag)
                        self.assertEqual(select_cards([row], [record], now=TIME)['cards'], [])
                        # The uncertain declaration does not erase ordinary
                        # candidates when no suppression decision applies.
                        self.assertEqual(select_cards([row], [], now=TIME)['cards'], [row])
                verified = candidate('one', severe=True)
                self.assertEqual(select_cards([verified], [record], now=TIME)['cards'], [verified])
                self.assertEqual(select_cards([verified], [record], now=TIME,
                                              seen_candidates=['one'])['cards'], [])
            finally:
                store.close()

    def test_unchanged_event_does_not_realert_when_transcript_container_grows(self):
        context = {'source': 'authored', 'source_key': 'one', 'records': [],
            'excerpts': {'prompts': {}, 'tools': {'check': {'tool_input': 'run unittest',
                                                         'tool_result': 'Ran 4 tests\nOK'}}},
            'excerpts_ref': {'sha256': 'a' * 64, 'content_class': 'private_redacted'}}
        first = diagnosis(context)['candidates'][0]
        context['excerpts_ref']['sha256'] = 'b' * 64
        second = diagnosis(context)['candidates'][0]
        self.assertEqual(first['key'], second['key'])
        self.assertEqual(select_cards([second], [], now=TIME, seen_candidates=[first['key']])['cards'], [])
        context['excerpts']['tools']['check']['tool_result'] = 'Ran 8 tests\nOK'
        self.assertNotEqual(first['key'], diagnosis(context)['candidates'][0]['key'])


if __name__ == '__main__':
    unittest.main()
