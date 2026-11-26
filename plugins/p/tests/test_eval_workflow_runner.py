"""Durable runner boundaries: deadlines, budgets, replay, and billing ambiguity."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retro_eval.workflow.registry import Registry
from retro_eval.workflow.runner import Limits, Runner
from retro_eval.workflow.records import canonical
from retro_eval.workflow.errors import WorkflowError


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Registry(Path(self.temp.name))
        self.now = 100
        self.runner = Runner(self.store, clock=lambda: self.now)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_F19_invalid_runtime_before_run_write(self):
        for value in (0, -1, True, 1.5):
            with self.assertRaises(WorkflowError):
                self.runner.begin({'limits': {'runtime_seconds': value}})
        self.assertEqual(self.runner.list_runs(), [])

    def test_F07_committed_unit_replay_has_no_second_call(self):
        run = self.runner.begin({})['run_id']
        callback = Mock(return_value={'delivered': 'unknown'})
        first = self.runner.unit(run, 'one', 'inputs-v1', callback)
        self.runner.finish(run)
        second = self.runner.unit(run, 'one', 'inputs-v1', callback)
        self.assertEqual(first, second)
        self.assertEqual(callback.call_count, 1)
        self.assertEqual(self.runner.begin({})['state'], 'completed')

    def test_F36_expired_resume_keeps_original_deadline(self):
        run = self.runner.begin({'limits': {'runtime_seconds': 10}})['run_id']
        self.runner.unit(run, 'one', 'one-v1', lambda: {'value': 1})
        self.now = 104
        self.runner.finish(run, 'partial', 'interrupted')
        self.now = 112
        callback = Mock(return_value={})
        resumed = self.runner.begin({'limits': {'runtime_seconds': 10}})
        self.assertEqual(resumed['deadline'], 110)
        result = self.runner.unit(run, 'two', 'two-v1', callback)
        self.assertEqual(result['status'], 'budget_exhausted')
        callback.assert_not_called()
        self.assertEqual(len(self.runner.status(run)['operations']), 1)
        self.assertEqual(self.runner.status(run)['operations'][0]['state'], 'completed')

    def test_A10_provider_failure_reservation_is_not_free_retry(self):
        run = self.runner.begin({'limits': {'provider_calls': 1}})['run_id']
        provider = Mock(side_effect=ConnectionError('response missing after request'))
        with self.assertRaises(ConnectionError):
            self.runner.unit(run, 'provider', 'v1', provider, provider=True)
        result = self.runner.unit(run, 'provider', 'v1', provider, provider=True)
        self.assertEqual(result, {'status': 'ambiguous_operation', 'charge_status': 'unknown'})
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(self.runner.status(run)['used']['provider_calls'], 1)

    def test_crash_after_provider_before_result_persistence(self):
        run = self.runner.begin({'limits': {'provider_calls': 1}})['run_id']
        provider = Mock(return_value={'label': 'unknown'})
        with patch.object(self.store, 'stage_artifact', side_effect=RuntimeError('crash')):
            with self.assertRaises(RuntimeError):
                self.runner.unit(run, 'provider', 'v1', provider, provider=True)
        self.runner.unit(run, 'provider', 'v1', provider, provider=True)
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(self.runner.status(run)['operations'][0]['charge_status'], 'unknown')

    def test_default_budget_disallows_provider_calls(self):
        run = self.runner.begin({})['run_id']
        provider = Mock()
        result = self.runner.unit(run, 'provider', 'v1', provider, provider=True)
        self.assertEqual(result['status'], 'budget_exhausted')
        provider.assert_not_called()

    def test_budget_exhaustion_preserves_completed_work(self):
        run = self.runner.begin({'limits': {'cases': 1}})['run_id']
        self.runner.unit(run, 'one', 'v1', lambda: {}, reservation={'cases': 1})
        callback = Mock()
        result = self.runner.unit(run, 'two', 'v1', callback, reservation={'cases': 1})
        callback.assert_not_called()
        self.assertEqual(result['status'], 'budget_exhausted')
        self.assertEqual(self.runner.status(run)['state'], 'partial')
        self.assertEqual(len(self.runner.status(run)['operations']), 1)

    def test_callback_usage_cannot_refund_provider_admission(self):
        run = self.runner.begin({'limits': {'provider_calls': 3}})['run_id']
        provider = Mock(return_value={'_usage': {'provider_calls': 0, 'captured_bytes': 9}})
        first = self.runner.unit(run, 'first', 'v1', provider, provider=True,
            reservation={'provider_calls': 3, 'captured_bytes': 100})
        self.assertEqual(first['status'], 'completed')
        status = self.runner.status(run)
        self.assertEqual(status['used']['provider_calls'], 3)
        self.assertEqual(status['used']['captured_bytes'], 9)
        self.assertEqual(status['operations'][0]['charge_status'], 'unknown')
        self.assertEqual(self.runner.unit(run, 'first', 'v1', provider, provider=True), first)
        result = self.runner.unit(run, 'second', 'v1', provider, provider=True)
        self.assertEqual(result, {'status': 'budget_exhausted', 'reason': 'provider_calls_exhausted'})
        provider.assert_called_once()
        evidence = self.store.publish_artifact(b'authored no-charge receipt', 'public_synthetic')
        self.runner.reconcile_charge(run, 'first', status='not_billed', evidence_ref=evidence)
        self.assertEqual(self.runner.status(run)['used']['provider_calls'], 3)

    def test_reported_provider_overuse_retains_uncertainty_and_reservation(self):
        run = self.runner.begin({'limits': {'provider_calls': 1}})['run_id']
        provider = Mock(return_value={'_usage': {'provider_calls': 2}})
        with self.assertRaisesRegex(WorkflowError, 'exceeds reserved resources'):
            self.runner.unit(run, 'first', 'v1', provider, provider=True)
        status = self.runner.status(run)
        self.assertEqual(status['used']['provider_calls'], 1)
        self.assertEqual(status['operations'][0]['state'], 'uncertain')
        self.assertEqual(status['operations'][0]['charge_status'], 'unknown')
        self.assertEqual(self.runner.unit(run, 'first', 'v1', provider, provider=True)['status'],
            'ambiguous_operation')
        self.assertEqual(self.runner.unit(run, 'second', 'v1', provider, provider=True)['status'],
            'budget_exhausted')
        provider.assert_called_once()

    def test_legacy_provider_accounting_blocks_new_calls_without_rewriting_history(self):
        for recorded in (0, 1):
            with self.subTest(recorded=recorded):
                run = self.runner.begin({'limits': {'provider_calls': 3}},
                                        run_id='legacy-' + str(recorded))['run_id']
                first = self.runner.unit(run, 'first', 'v1', lambda: {}, provider=True,
                                         reservation={'provider_calls': 3})
                row = self.runner.status(run)['operations'][0]
                legacy_result = {k: v for k, v in first.items() if k != 'provider_admission'}
                reservation = {**row['reservation'], 'provider_calls': recorded}
                with self.store.transaction():
                    self.store.db.execute('UPDATE operations SET reservation=?,result=? WHERE run_id=?',
                        (canonical(reservation), canonical(legacy_result), run))
                status = self.runner.status(run)
                self.assertEqual(status['used']['provider_calls'], recorded)
                callback = Mock(return_value={})
                self.assertEqual(self.runner.unit(run, 'first', 'v1', callback, provider=True),
                                 legacy_result)
                with self.assertRaisesRegex(WorkflowError, 'original provider admission'):
                    self.runner.unit(run, 'second', 'v1', callback, provider=True)
                callback.assert_not_called()
                self.assertEqual(status['provider_accounting']['unverified_operations'], ['first'])
                self.assertEqual(self.runner.unit(run, 'local', 'v1', lambda: {})['status'], 'completed')
                evidence = self.store.publish_artifact(b'authored no-charge evidence', 'public_synthetic')
                self.runner.reconcile_charge(run, 'first', status='not_billed', evidence_ref=evidence)
                self.assertEqual(self.runner.status(run)['provider_accounting']['unverified_operations'],
                                 ['first'])
                restored = Registry(Path(self.temp.name) / ('restored-' + str(recorded)))
                try:
                    restored.import_bundle(self.store.export_bundle())
                    resumed = Runner(restored, clock=lambda: self.now)
                    self.assertEqual(resumed.status(run)['deadline'], status['deadline'])
                    with self.assertRaisesRegex(WorkflowError, 'original provider admission'):
                        resumed.unit(run, 'third', 'v1', callback, provider=True)
                    callback.assert_not_called()
                finally:
                    restored.close()

    def test_provider_admission_receipt_survives_export_and_rejects_inconsistent_markers(self):
        run = self.runner.begin({'limits': {'provider_calls': 2}})['run_id']
        first = self.runner.unit(run, 'first', 'v1', lambda: {}, provider=True)
        self.assertEqual(first['provider_admission'], {'version': 1, 'reserved_calls': 1})
        restored = Registry(Path(self.temp.name) / 'provider-restored')
        try:
            restored.import_bundle(self.store.export_bundle())
            resumed = Runner(restored, clock=lambda: self.now)
            self.assertEqual(resumed.status(run)['provider_accounting']['unverified_operations'], [])
            self.assertEqual(resumed.unit(run, 'second', 'v1', lambda: {}, provider=True)['status'],
                             'completed')
            self.assertEqual(resumed.status(run)['used']['provider_calls'], 2)
        finally:
            restored.close()
        callback = Mock(return_value={})
        for marker in (None, {'version': True, 'reserved_calls': 1},
                       {'version': 1, 'reserved_calls': True},
                       {'version': 2, 'reserved_calls': 1},
                       {'version': 1, 'reserved_calls': 0},
                       {'version': 1, 'reserved_calls': 2}):
            with self.subTest(marker=marker), self.store.transaction():
                self.store.db.execute('UPDATE operations SET result=? WHERE run_id=? AND unit_key=?',
                    (canonical({**first, 'provider_admission': marker}), run, 'first'))
            with self.assertRaisesRegex(WorkflowError, 'original provider admission'):
                self.runner.unit(run, 'next', 'v1', callback, provider=True)
        callback.assert_not_called()

    def test_imported_uncertain_provider_requires_positive_retained_admission(self):
        for calls in (0, 1):
            with self.subTest(calls=calls):
                run = self.runner.begin({'limits': {'provider_calls': 2}},
                                        run_id='uncertain-' + str(calls))['run_id']
                with self.assertRaises(ValueError):
                    self.runner.unit(run, 'first', 'v1', Mock(side_effect=ValueError('interrupted')),
                                     provider=True)
                with self.store.transaction():
                    self.store.db.execute('UPDATE operations SET reservation=? WHERE run_id=?',
                                          (canonical({'provider_calls': calls}), run))
                restored = Registry(Path(self.temp.name) / ('uncertain-copy-' + str(calls)))
                try:
                    restored.import_bundle(self.store.export_bundle())
                    resumed = Runner(restored, clock=lambda: self.now)
                    callback = Mock(return_value={})
                    self.assertEqual(resumed.unit(run, 'first', 'v1', callback, provider=True)['status'],
                                     'ambiguous_operation')
                    if calls == 0:
                        with self.assertRaisesRegex(WorkflowError, 'original provider admission'):
                            resumed.unit(run, 'second', 'v1', callback, provider=True)
                        callback.assert_not_called()
                    else:
                        self.assertEqual(resumed.unit(run, 'second', 'v1', callback,
                                                      provider=True)['status'], 'completed')
                        callback.assert_called_once()
                    self.assertEqual(resumed.status(run)['deadline'], self.runner.status(run)['deadline'])
                finally:
                    restored.close()

    def test_invalid_provider_inputs_are_rejected_before_reservation_or_call(self):
        run = self.runner.begin({'limits': {'provider_calls': 3}})['run_id']
        callback = Mock()
        for value in (-1, True, False, 0.5, '1', None):
            with self.subTest(reserved_calls=value), self.assertRaises(WorkflowError):
                self.runner.unit(run, 'invalid', 'v1', callback, provider=True,
                    reservation={'provider_calls': value})
        for value in ('false', None, 0, 1, []):
            with self.subTest(provider=value), self.assertRaises(WorkflowError):
                self.runner.unit(run, 'invalid', 'v1', callback, provider=value)
        callback.assert_not_called()
        self.assertEqual(self.runner.status(run)['operations'], [])

    def test_known_local_failure_settles_only_measured_usage(self):
        run = self.runner.begin({'limits': {'derived_bytes': 20000}})['run_id']
        with self.assertRaises(ValueError):
            self.runner.unit(run, 'failed', 'v1', Mock(side_effect=ValueError('invalid source')),
                reservation={'derived_bytes': 19000, 'captured_bytes': 1000},
                failure_usage=lambda: {'derived_bytes': 0, 'captured_bytes': 700})
        status = self.runner.status(run)
        self.assertEqual(status['used']['derived_bytes'], 0)
        self.assertEqual(status['used']['captured_bytes'], 700)
        result = self.runner.unit(run, 'valid', 'v1', lambda: {})
        self.assertEqual(result['status'], 'completed')

    def test_review_failed_local_attempt_cannot_retry_for_free(self):
        run = self.runner.begin({'limits': {'captured_bytes': 1000}})['run_id']
        callback = Mock(side_effect=ValueError('source failed'))
        with self.assertRaises(ValueError):
            self.runner.unit(run, 'one', 'v1', callback, reservation={'captured_bytes': 1000})
        result = self.runner.unit(run, 'one', 'v1', callback, reservation={'captured_bytes': 1000})
        self.assertEqual(result['reason'], 'new_attempt_identity_required')
        self.assertEqual(callback.call_count, 1)
        self.assertEqual(self.runner.status(run)['used']['captured_bytes'], 1000)
        result = self.runner.unit(run, 'retry-two', 'v1', callback, reservation={'captured_bytes': 1000})
        self.assertEqual(result['status'], 'budget_exhausted')
        self.assertEqual(callback.call_count, 1)

    def test_successful_provider_call_still_needs_billing_evidence(self):
        run = self.runner.begin({'limits': {'provider_calls': 1}})['run_id']
        self.runner.unit(run, 'one', 'v1', lambda: {}, provider=True)
        self.assertEqual(self.runner.status(run)['operations'][0]['charge_status'], 'unknown')
        evidence = self.store.publish_artifact(b'authored billing receipt', 'public_synthetic')
        self.runner.reconcile_charge(run, 'one', status='billed', evidence_ref=evidence)
        self.assertEqual(self.runner.status(run)['operations'][0]['charge_status'], 'billed')


if __name__ == '__main__':
    unittest.main()
