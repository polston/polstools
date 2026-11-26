"""Independent synthetic oracles for recovery view correctness and staleness."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retro_eval.investigation.recovery_local import ADAPTER, capture, check_current, from_sources, summarize
from retro_eval.investigation.recovery_summary import render


def raw(value):
    return json.dumps(value).encode()


class SummaryTests(unittest.TestCase):
    def case(self, family='opaque'):
        request = {'key': 'item', **({'delta': 8} if family == 'ledger' else {'content': 'edition'} if family == 'publish' else {'units': 4})}
        return {'task.json': raw({'recovery_transport': ADAPTER, 'family': family, 'request': request,
                                 'submission_allowance': 2, 'deadline_ms': 1000}),
                'client-journal.jsonl': b'', 'recovery-barrier.json': raw({'verified': True, 'at_ns': 10})}

    def event(self, sources, identity='a', mode='submit', at=5, response=None, request=None):
        task = json.loads(sources['task.json'])
        event = {'attempt_id': identity, 'mode': mode, 'stage': 'invoked', 'at_ns': at}
        sources['client-journal.jsonl'] += raw(event) + b'\n'
        envelope = {'attempt_id': identity, 'mode': mode}
        if mode == 'submit': envelope['request'] = task['request'] if request is None else request
        sources['transport/' + identity + '.sent'] = raw(envelope)
        if response is not None: sources['transport/' + identity + '.response'] = raw(response)

    def receipt(self, family='opaque'):
        return {'confirmed': True, 'effect': {'key': 'item', **({'delta': 8, 'entry': 1} if family == 'ledger' else {'sha256': hashlib.sha256(b'edition').hexdigest()} if family == 'publish' else {'units': 4, 'receipt': 'dispatch-1'})}}

    def view(self, s, now=20): return from_sources(s, now_ms=now)
    def step(self, s): return self.view(s)['next_step']['code']

    def test_receipt_linked_by_identity_not_file_order(self):
        s = self.case(); self.event(s, 'z', response=self.receipt())
        self.event(s, 'a', 'inspect', 15, {'available': False})
        self.assertEqual(self.step(dict(reversed(list(s.items())))), 'record_completion')
        view = self.view(s)
        self.assertEqual(view['supported_outcome']['attempt_id'], 'z')
        self.assertTrue(any('transport/z.response@' in r for r in view['knowledge']['evidence']))
        self.assertEqual(view['accounting']['charged'], 1)

    def test_invocation_without_receipt_is_held_not_completion(self):
        s = self.case(); self.event(s)
        v = self.view(s)
        self.assertEqual(v['accounting']['held'], 1)
        self.assertEqual(v['accounting']['remaining'], 1)
        self.assertIsNone(v['supported_outcome'])
        self.assertEqual(self.step(s), 'preserve_uncertainty')

    def test_queryable_unknown_requires_inspection(self):
        s = self.case('ledger'); self.event(s)
        self.assertEqual(self.step(s), 'inspect')

    def test_current_absence_allows_consideration_not_authorization(self):
        s = self.case('ledger'); self.event(s)
        self.event(s, 'q', 'inspect', 15, {'available': True, 'entries': [], 'balance': 100})
        v = self.view(s)
        self.assertEqual(v['next_step']['code'], 'consider_submission')
        self.assertIn('never authorizes', v['next_step']['authority'])
        self.assertEqual(v['accounting']['held'], 1)

    def test_stale_absence_does_not_permit_retry(self):
        s = self.case('ledger')
        self.event(s, 'q', 'inspect', 5, {'available': True, 'entries': [], 'balance': 100})
        self.event(s, 'a', at=15)
        s['recovery-barrier.json'] = raw({'verified': True, 'at_ns': 18})
        self.assertEqual(self.step(s), 'inspect')

    def test_query_before_barrier_is_not_current(self):
        s = self.case('ledger'); self.event(s, at=2)
        self.event(s, 'q', 'inspect', 5, {'available': True, 'entries': [], 'balance': 100})
        self.assertEqual(self.step(s), 'inspect')

    def test_query_outcome_and_duplicate_detection(self):
        for n, expected in [(1, 'record_completion'), (2, 'review_evidence')]:
            s = self.case('ledger'); self.event(s)
            self.event(s, 'q', 'inspect', 15, {'available': True, 'entries': [{'key': 'item', 'delta': 8}] * n, 'balance': 100 + 8*n})
            self.assertEqual(self.step(s), expected)

    def test_publish_exact_replay(self):
        s = self.case('publish'); self.event(s)
        # Query is preferred when available; idempotency is not a reason to skip it.
        self.assertEqual(self.step(s), 'inspect')
        self.event(s, 'q', 'inspect', 15, {'available': True, 'published': []})
        self.assertEqual(self.step(s), 'consider_submission')

    def test_missing_or_false_barrier_blocks_completion(self):
        for barrier in (None, {'verified': False, 'at_ns': 10}, {'verified': True, 'at_ns': 'bad'}):
            s = self.case(); self.event(s, response=self.receipt())
            if barrier is None: del s['recovery-barrier.json']
            else: s['recovery-barrier.json'] = raw(barrier)
            self.assertEqual(self.step(s), 'review_evidence')

    def test_expired_bound_stops(self):
        s = self.case(); self.event(s, response=self.receipt())
        self.assertEqual(self.view(s, 1000)['next_step']['code'], 'stop')

    def test_exhausted_allowance_is_not_restored(self):
        s = self.case(); self.event(s); self.event(s, 'b', at=15)
        s['recovery-barrier.json'] = raw({'verified': True, 'at_ns': 18})
        self.assertEqual(self.view(s)['accounting']['remaining'], 0)
        self.assertEqual(self.step(s), 'preserve_uncertainty')

    def test_extra_unresolved_attempt_prevents_receipt_completion(self):
        s = self.case(); self.event(s, response=self.receipt()); self.event(s, 'b', at=15)
        s['recovery-barrier.json'] = raw({'verified': True, 'at_ns': 18})
        self.assertEqual(self.step(s), 'preserve_uncertainty')
        self.assertIsNone(self.view(s)['supported_outcome'])

    def test_reused_identity_rejected_even_same_payload(self):
        s = self.case(); self.event(s, response=self.receipt()); self.event(s, at=15)
        self.assertEqual(self.step(s), 'review_evidence')

    def test_unlinked_response_is_not_a_receipt(self):
        s = self.case(); self.event(s)
        s['transport/unknown.response'] = raw(self.receipt())
        self.assertEqual(self.step(s), 'review_evidence')
        self.assertIsNone(self.view(s)['supported_outcome'])

    def test_wrong_mode_and_wrong_request(self):
        for mode in ('inspect', 'submit'):
            s = self.case(); self.event(s, response=self.receipt())
            envelope = json.loads(s['transport/a.sent']); envelope['mode'] = mode
            if mode == 'submit': envelope['request']['units'] = 999
            s['transport/a.sent'] = raw(envelope)
            self.assertEqual(self.step(s), 'review_evidence')

    def test_false_receipt_fields(self):
        for value in ({'confirmed': True}, {'available': False}, {'confirmed': True, 'effect': {'key': 'other', 'units': 4, 'receipt': 'fake'}}):
            s = self.case(); self.event(s, response=value)
            self.assertEqual(self.step(s), 'review_evidence')

    def test_bad_json_and_duplicate_keys(self):
        for value in (b'{', b'{"verified":true,"verified":false}', b'[]'):
            s = self.case(); self.event(s, response=self.receipt()); s['recovery-barrier.json'] = value
            self.assertEqual(self.step(s), 'review_evidence')

    def test_missing_journal_is_not_empty_journal(self):
        s = self.case(); del s['client-journal.jsonl']
        self.assertEqual(self.step(s), 'review_evidence')
        self.assertIsNone(self.view(s)['accounting']['remaining'])
        self.assertEqual(self.step(self.case()), 'consider_submission')

    def test_unsupported_adapter(self):
        s = self.case(); task = json.loads(s['task.json']); task['recovery_transport'] = 'arbitrary'; s['task.json'] = raw(task)
        self.assertEqual(self.step(s), 'review_evidence')

    def test_pending_transport_blocks_snapshot(self):
        s = self.case(); self.event(s, response=self.receipt()); s['transport/a.request'] = s['transport/a.sent']
        self.assertEqual(self.step(s), 'review_evidence')

    def test_duplicate_receipts_are_duplicate_effects(self):
        s = self.case(); self.event(s, response=self.receipt()); self.event(s, 'b', at=15, response=self.receipt())
        self.assertEqual(self.step(s), 'review_evidence')

    def test_contradictory_query_cannot_erase_receipt(self):
        s = self.case('ledger'); self.event(s, response=self.receipt('ledger'))
        self.event(s, 'q', 'inspect', 15, {'available': True, 'entries': [], 'balance': 100})
        self.assertEqual(self.step(s), 'review_evidence')

    def test_readonly_and_changed_added_deleted_sources(self):
        s = self.case(); self.event(s, response=self.receipt())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, data in s.items():
                p = root / name; p.parent.mkdir(exist_ok=True); p.write_bytes(data)
            view = summarize(root, now_ms=20)
            self.assertEqual(capture(root), s)
            self.assertEqual(view, summarize(root, now_ms=20))
            self.assertTrue(check_current(root, view, now_ms=21))
            self.assertFalse(check_current(root, view, now_ms=1000))
            p = root / 'transport/a.response'; p.write_bytes(raw({'confirmed': False}))
            self.assertFalse(check_current(root, view, now_ms=20))
            p.write_bytes(s['transport/a.response']); p.unlink()
            self.assertFalse(check_current(root, view, now_ms=20))
            p.write_bytes(s['transport/a.response']); (root / 'transport/new.response').write_bytes(b'{}')
            self.assertFalse(check_current(root, view, now_ms=20))
            self.assertIn('transport/a.response@', render(view))

    def test_symlink_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'task.json').symlink_to(root / 'absent')
            with self.assertRaises(ValueError): capture(root)

    def test_barrier_must_cover_unresolved_submissions(self):
        s = self.case('ledger'); self.event(s, at=15)
        self.event(s, 'q', 'inspect', 20, {'available': True, 'entries': [], 'balance': 100})
        self.assertEqual(self.step(s), 'review_evidence')

    def test_boolean_quantities_never_match_integers(self):
        for location in ('envelope', 'receipt'):
            s = self.case(); task = json.loads(s['task.json']); task['request']['units'] = 1
            s['task.json'] = raw(task); receipt = self.receipt(); receipt['effect']['units'] = 1
            self.event(s, response=receipt)
            path = 'transport/a.' + ('sent' if location == 'envelope' else 'response')
            obj = json.loads(s[path]); obj['request' if location == 'envelope' else 'effect']['units'] = True
            s[path] = raw(obj)
            self.assertEqual(self.step(s), 'review_evidence')

    def test_malformed_family_is_a_review_not_exception(self):
        for value in ([], {}, None, 1):
            s = self.case(); task = json.loads(s['task.json']); task['family'] = value
            s['task.json'] = raw(task)
            self.assertEqual(self.step(s), 'review_evidence')

    def test_explicit_partial_query_not_complete(self):
        s = self.case('ledger'); self.event(s)
        self.event(s, 'q', 'inspect', 15, {'available': True, 'entries': [], 'balance': 100, 'complete': False})
        self.assertEqual(self.step(s), 'review_evidence')


if __name__ == '__main__': unittest.main()
