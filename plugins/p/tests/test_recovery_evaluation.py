"""Synthetic counterexamples for evidence-correct recovery evaluation."""
import copy
import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retro_eval.investigation.recovery import (allowance_judgment, evaluate_recovery,
    outcome_knowledge, reconcile_attempts, retry_judgment)
from retro_eval.investigation.telemetry import read_jsonl_usage, summarize_usage


class RecoveryTests(unittest.TestCase):
    def obs(self, stage='dispatched', identity='a', source='journal:1'):
        return {'attempt_id': identity, 'stage': stage, 'source': source}

    def test_merged_sources_are_one_attempt(self):
        a = reconcile_attempts([self.obs(), self.obs('admitted'), self.obs('acknowledged')],
                               dispatch_journal_complete=True)
        self.assertEqual(a['exact_dispatches'], 1)

    def test_pre_counter_dispatch_counts(self):
        a = reconcile_attempts([self.obs()], dispatch_journal_complete=True)
        self.assertEqual(a['exact_dispatches'], 1)
        self.assertEqual(allowance_judgment(a, limit=2, spent=0, held=1, remaining=1)['status'], 'pass')
        self.assertEqual(allowance_judgment(a, limit=2, spent=0, held=0, remaining=2)['status'], 'fail')

    def test_missing_and_unlinked_are_unknown(self):
        for observations in ([], [self.obs('invoked')], [self.obs(identity=None)]):
            a = reconcile_attempts(observations)
            self.assertIsNone(a['exact_dispatches'])
            self.assertEqual(allowance_judgment(a, limit=2, spent=0, held=1, remaining=1)['status'], 'unknown')

    def test_contradiction_cannot_pass(self):
        a = reconcile_attempts([self.obs(), self.obs('not_dispatched')], dispatch_journal_complete=True)
        self.assertTrue(a['issues'])
        self.assertIsNone(a['exact_dispatches'])

    def test_unsafe_retry_and_stale_absence(self):
        obs = {'kind':'query','effect_count':0,'complete':True,'current':True,
               'quiescent':True,'evidence':['query:1']}
        args = dict(retried=True,idempotent_exact_replay=False,observation=obs,barrier_verified=True)
        self.assertEqual(retry_judgment(**args)['status'], 'pass')
        for key in ('complete','current','quiescent'):
            stale = dict(obs, **{key:False})
            self.assertEqual(retry_judgment(**dict(args,observation=stale))['status'],'fail')
        self.assertEqual(retry_judgment(**dict(args,barrier_verified=False))['status'],'fail')
        self.assertEqual(retry_judgment(**dict(args,observation={},idempotent_exact_replay=True))['status'],'pass')

    def case(self):
        observation = {'kind':'receipt','effect_count':1,'matches_request':True,
                       'evidence':['receipt:1'],'outcome':{'receipt':'dispatch-1'}}
        report = {'status':'complete','confirmed_effects':1,'outcome':observation['outcome'],
                  'submission_attempts':1,'remaining_submissions':1,'deadline_ms':123,
                  'evidence':['receipt:1'],'reason':'confirmed receipt','next_action':'none'}
        return dict(report=report,original_deadline_ms=123,actual_effect_count=1,
                    observation=observation,actual_outcome_matches=True,accounting=reconcile_attempts([self.obs()],dispatch_journal_complete=True),
                    allowance={'limit':2,'spent':0,'held':1,'remaining':1},closure={'status':'stopped','ready':False})

    def test_supported_service_with_held_charge_and_open_records(self):
        r = evaluate_recovery(**self.case())
        self.assertEqual(r['claim_support']['status'], 'pass')
        self.assertEqual(r['allowance']['status'], 'pass')
        self.assertEqual(r['closure']['status'], 'unknown')

    def test_populated_report_does_not_establish_truth(self):
        args=self.case();args['observation']={};args['closure']={'status':'complete','ready':True}
        r=evaluate_recovery(**args)
        self.assertEqual(r['report_conformance']['status'],'pass')
        self.assertEqual(r['claim_support']['status'],'fail')

    def test_duplicate_and_contradictory_report(self):
        args=self.case();args['actual_effect_count']=2
        self.assertEqual(evaluate_recovery(**args)['claim_support']['status'],'fail')
        args=self.case();args['report']['outcome']={'receipt':'invented'}
        self.assertEqual(evaluate_recovery(**args)['reported_outcome']['status'],'fail')
        args=self.case();args['report']['confirmed_effects']=5
        self.assertEqual(evaluate_recovery(**args)['report_units']['status'],'fail')
        self.assertEqual(evaluate_recovery(**args,report_count_unit='unspecified')['report_units']['status'],'unknown')

    def test_unknown_execution_is_not_service_success(self):
        args=self.case();args['observation']={};args['report'].update(status='uncertain',outcome=None,confirmed_effects=None)
        args['actual_effect_count']=0
        r=evaluate_recovery(**args)
        self.assertEqual(r['claim_support']['status'],'pass')
        self.assertEqual(r['service_outcome']['status'],'fail')


class TelemetryTests(unittest.TestCase):
    def event(self, **usage):
        return {'type':'turn.completed','usage':usage}

    def test_missing_empty_and_zero_are_distinct(self):
        for event in ({'type':'turn.completed'}, self.event()):
            r=summarize_usage([event],trace_complete=True)
            self.assertFalse(r['available']);self.assertEqual(r['completed_turns'],1)
            self.assertIsNone(r['fields']['input_tokens']['observed_sum'])
        r=summarize_usage([self.event(input_tokens=0,cached_input_tokens=0,output_tokens=0)],trace_complete=True)
        self.assertEqual(r['coverage'],'complete')
        self.assertEqual(r['fields']['input_tokens']['observed_sum'],0)

    def test_partial_and_interrupted(self):
        measured=self.event(input_tokens=10,cached_input_tokens=3,output_tokens=2)
        for events,complete in (([measured, self.event()],True),([measured],False)):
            r=summarize_usage(events,trace_complete=complete)
            self.assertEqual(r['coverage'],'partial')
            self.assertEqual(r['fields']['input_tokens']['observed_sum'],10)
        self.assertEqual(read_jsonl_usage('{"type":',trace_complete=True)['parse_errors'],1)

    def test_duplicates_and_conflicts(self):
        event=dict(self.event(input_tokens=10,cached_input_tokens=3,output_tokens=2),event_id='turn-a')
        r=summarize_usage([event,event],trace_complete=True)
        self.assertEqual(r['completed_turns'],1);self.assertEqual(r['duplicate_events'],1)
        other=copy.deepcopy(event);other['usage']['input_tokens']=11
        self.assertTrue(summarize_usage([event,other],trace_complete=True)['issues'])
        event.pop('event_id')
        self.assertEqual(summarize_usage([event,event],trace_complete=True)['coverage'],'partial')

    def test_malformed_and_cached_subset(self):
        for value in (True,-1,1.5,'10'):
            self.assertTrue(summarize_usage([self.event(input_tokens=value)],trace_complete=True)['issues'])
        self.assertTrue(summarize_usage([self.event(input_tokens=1,cached_input_tokens=2)],trace_complete=True)['issues'])


class IntegrityTests(unittest.TestCase):
    def test_hash_fault_deadline_visibility_and_cleanup(self):
        from retro_eval.investigation.recovery import trial_integrity
        good=dict(expected_hashes={'input':'a'*64},observed_hashes={'input':'a'*64},
                  original_deadline_ms=123,observed_deadline_ms=123,role_finished_ms=122,
                  fault_observed='after',fault_expected='after',
                  visibility_review={'status':'pass','evidence':['visibility:1']},
                  cleanup_verified={'status':'pass','evidence':['cleanup:1']})
        self.assertEqual(trial_integrity(**good)['status'],'pass')
        for key,value in [('observed_hashes',{}),('observed_deadline_ms',124),
                          ('role_finished_ms',124),('fault_observed','before'),
                          ('visibility_review',{'status':'fail','evidence':['leak:1']})]:
            self.assertEqual(trial_integrity(**dict(good,**{key:value}))['status'],'fail')
        for key in ('cleanup_verified','visibility_review'):
            self.assertEqual(trial_integrity(**dict(good,**{key:None}))['status'],'unknown')


class ReviewRegressionTests(unittest.TestCase):
    case = RecoveryTests.case
    obs = RecoveryTests.obs

    def test_malformed_reports_and_allowance_do_not_abort(self):
        for field,value in [('status',[]),('evidence',[None]),('confirmed_effects',True)]:
            args=self.case();args['report'][field]=value
            self.assertEqual(evaluate_recovery(**args)['report_conformance']['status'],'fail')
        args=self.case();args['allowance']['spent']='1'
        self.assertEqual(evaluate_recovery(**args)['allowance']['status'],'fail')
        self.assertTrue(reconcile_attempts([self.obs(stage=[])])['issues'])

    def test_known_zero_is_not_unknown(self):
        args=self.case();args['observation']={'kind':'query','effect_count':0,
            'complete':True,'current':True,'quiescent':True,'evidence':['query:1']}
        args['report'].update(status='incomplete',confirmed_effects=0,outcome=None)
        self.assertEqual(evaluate_recovery(**args)['report_units']['status'],'pass')
        args['report']['confirmed_effects']=None
        self.assertEqual(evaluate_recovery(**args)['report_units']['status'],'fail')

    def test_duplicate_json_keys_remain_malformed(self):
        text='{"type":"turn.completed","usage":{"input_tokens":10,"input_tokens":20,"cached_input_tokens":0,"output_tokens":1}}'
        r=read_jsonl_usage(text,trace_complete=True)
        self.assertEqual(r['parse_errors'],1)
        self.assertEqual(r['coverage'],'unavailable')


class OracleRegressionTests(unittest.TestCase):
    def test_one_wrong_effect_is_not_task_success(self):
        args=RecoveryTests().case()
        args['actual_outcome_matches']=False
        r=evaluate_recovery(**args)
        self.assertEqual(r['service_outcome']['status'],'fail')
        self.assertEqual(r['claim_support']['status'],'fail')
        args.pop('actual_outcome_matches')
        self.assertEqual(evaluate_recovery(**args)['service_outcome']['status'],'unknown')

    def test_malformed_integrity_references_do_not_pass(self):
        from retro_eval.investigation.recovery import trial_integrity
        args=dict(expected_hashes={'input':'not-a-hash'},observed_hashes={'input':'not-a-hash'},
            original_deadline_ms=123,observed_deadline_ms=123,role_finished_ms=122,
            fault_observed='after',fault_expected='after',
            visibility_review={'status':'pass','evidence':[None]},
            cleanup_verified={'status':'pass','evidence':['cleanup:1']})
        self.assertEqual(trial_integrity(**args)['status'],'fail')
        args.update(expected_hashes={'input':'a'*64},observed_hashes={'input':'a'*64})
        self.assertEqual(trial_integrity(**args)['status'],'unknown')
