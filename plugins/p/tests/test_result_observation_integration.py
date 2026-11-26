"""Result observation privacy, accounting and public report regression checks."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retro_eval.workflow.errors import BudgetExhausted
from retro_eval.workflow.evidence import resolve_evidence, judge_input
from retro_eval.workflow.investigation_records import evidence
from retro_eval.workflow.records import canonical
from retro_eval.workflow.registry import Registry
from retro_eval.workflow.service import run_automatic
from retro_eval.workflow.sources import normalize_captured
from retro_eval.workflow.tool_results import observe_results, summarize_result_coverage
from test_eval_workflow_sources import source_fixture
from test_result_observations import pair, agy, T

class ResultIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.store=Registry(self.root/'store')
    def tearDown(self):
        self.store.close();self.temp.cleanup()
    def capture(self,rows=None,source='codex',**kw):
        raw=source_fixture(source)+b''.join(canonical(r)+b'\n' for r in (pair() if rows is None else rows))
        return normalize_captured(source,'sample/session.jsonl',raw,self.store,**kw)
    def details(self,c):return json.loads(self.store.read_artifact(c.result_observations_ref)[0])
    def test_observation_bytes_count_against_allowance(self):
        c=self.capture();size=sum(len(self.store.read_artifact(ref)[0]) for ref in [c.normalized_ref,c.excerpts_ref,c.result_observations_ref])
        self.capture(derived_limit=size)
        with self.assertRaises(BudgetExhausted):self.capture(derived_limit=size-1)
    def test_excluded_source_is_unavailable_not_zero_calls(self):
        raw=source_fixture('codex').replace(b'"thread_source":"user"',b'"id":"reporting","thread_source":"user"')
        c=normalize_captured('codex','sample/session.jsonl',raw,self.store,exclude_session_ids={'reporting'})
        self.assertFalse(c.included);d=self.details(c)
        self.assertEqual(d['availability'],'not_observed');self.assertIsNone(d['coverage'])
    def test_real_report_exposes_content_free_counts_and_detail_reference(self):
        source=self.root/'source';source.mkdir();(source/'rollout-authored.jsonl').write_bytes(source_fixture('codex')+b''.join(canonical(r)+b'\n' for r in pair()))
        result=run_automatic(self.root/'run-store',roots={'codex':source},captured_at='2026-01-02T00:00:00Z')
        report=json.loads(Path(result['report_path']).read_text())
        coverage=report['coverage']['codex']['tool_results']
        self.assertEqual(coverage['mapped_sources'],1)
        self.assertEqual(coverage['counts']['identifier_bound'],1)
        self.assertEqual(coverage['counts']['path_corroborated'],0)
        self.assertIn('result_observations_ref',report['outputs'][0])
    def test_old_reports_do_not_become_zero(self):
        result=summarize_result_coverage([{'observations':{}}])
        self.assertEqual(result['unmapped_sources'],1);self.assertIsNone(result['counts'])
    def test_raw_selectors_validate_and_cannot_be_external_judge_input(self):
        from retro_eval.workflow.errors import PolicyDenied
        c=self.capture();ref=self.details(c)['calls'][0]['result_refs'][0]
        evidence([ref]);self.assertEqual(resolve_evidence(ref,self.store).status,'resolved')
        with self.assertRaises(PolicyDenied):judge_input(ref,self.store,external=True)
        bad=copy.deepcopy(ref);bad['selector']['line']=False
        with self.assertRaises(Exception):evidence([bad])
    def test_selector_reads_original_lines_past_invalid_rows(self):
        raw=source_fixture('codex')+b'not json\n'+b''.join(canonical(r)+b'\n' for r in pair())
        c=normalize_captured('codex','sample/session.jsonl',raw,self.store)
        d=self.details(c)
        self.assertEqual(resolve_evidence(d['calls'][0]['result_refs'][0],self.store).value,pair()[1]['payload'])
        self.assertIn('malformed_json_record',{r['reason'] for r in c.diagnostics})
    def test_result_like_text_does_not_certify_complete_or_success(self):
        rows=pair();rows[1]['payload']['output']='Warning: truncated output (original token count: 123)\nsuccess'
        c=self.details(self.capture(rows))['calls'][0]
        self.assertEqual(c['content']['truncation'],'reported_notice')
        self.assertEqual(c['content']['completeness'],'unknown');self.assertEqual(c['outcome'],'unknown')
    def test_missing_null_and_nontext_have_distinct_reasons(self):
        values=[]
        for variant in ['missing','null','nontext']:
            rows=pair()
            if variant=='missing':del rows[1]['payload']['output']
            else:rows[1]['payload']['output']=None if variant=='null' else {'unsupported':'shape'}
            values.append(self.details(self.capture(rows))['calls'][0]['content']['reason'])
        self.assertEqual(len(set(values)),3)
    def test_path_header_in_file_body_does_not_match(self):
        rows=agy();rows[1]['content']='Created At: '+T+'\nCompleted At: '+T+'\n1: file text\n2: file text\n3: file text\n4: file text\nFile Path: `file:///fixture/source.txt`'
        self.assertNotEqual(self.details(self.capture(rows,'antigravity'))['calls'][0]['association'],'path_corroborated')
    def test_non_model_generic_is_not_matched(self):
        rows=agy();rows[1]['source']='USER_EXPLICIT'
        self.assertEqual(self.details(self.capture(rows,'antigravity'))['calls'][0]['association'],'unavailable')
    def test_transport_version_is_not_guessed_and_conflicts_are_visible(self):
        self.assertIsNone(self.details(self.capture())['transport_version'])
        rows=[{'type':'session_meta','payload':{'thread_source':'user','cli_version':'authored-a'}},{'type':'session_meta','payload':{'thread_source':'user','cli_version':'authored-b'}}]+pair()
        d=self.details(self.capture(rows));self.assertIsNone(d['transport_version']);self.assertEqual(d['transport_version_state'],'conflicting')
    def test_malformed_call_still_competes_for_its_identity(self):
        for source in ['codex','claude']:
            if source=='codex':
                a,b=pair();bad=copy.deepcopy(a);del bad['payload']['name'];rows=[a,bad,b]
            else:
                a={'type':'assistant','message':{'role':'assistant','content':[{'type':'tool_use','id':'a','name':'Read','input':{}}]}}
                bad=copy.deepcopy(a);del bad['message']['content'][0]['name']
                b={'type':'user','message':{'role':'user','content':[{'type':'tool_result','tool_use_id':'a','content':'data'}]}}
                rows=[a,bad,b]
            d=self.details(self.capture(rows,source))
            self.assertEqual(d['coverage']['malformed_calls'],1)
            self.assertEqual(d['calls'][0]['association'],'ambiguous')
    def test_duplicate_ambiguity_does_not_expand_quadratically(self):
        a,b=pair();d=self.details(self.capture([a]*50+[b]*50))
        self.assertEqual(d['coverage']['ambiguous'],50)
        self.assertTrue(all(len(c['candidate_result_refs'])==4 and c['candidate_results_omitted']==46 for c in d['calls']))
    def test_unknown_transport_is_not_measured_zero(self):
        from retro_eval.schema import LocalIdFactory
        raw=self.store.publish_artifact(b'{}','private_raw')
        d=observe_results('new_source',[],raw,LocalIdFactory(self.store.id_salt))
        self.assertEqual(d['availability'],'not_supported');self.assertIsNone(d['coverage'])

if __name__=='__main__':unittest.main()
