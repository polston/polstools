"""Authored transport oracles, frozen before result mapping implementation."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retro_eval.workflow.records import canonical
from retro_eval.workflow.registry import Registry
from retro_eval.workflow.sources import normalize_captured
from retro_eval.workflow.evidence import resolve_evidence
from test_eval_workflow_sources import source_fixture

T = '2026-01-01T00:00:00Z'

def codex(kind='function_call', **kw):
    return {'type': 'response_item', 'timestamp': T, 'payload': dict(type=kind, **kw)}

def pair():
    return [codex(call_id='a', name='read', arguments='{}'),
            codex('function_call_output', call_id='a', output='evidence')]

def agy():
    return [{'step_index': 2, 'type': 'PLANNER_RESPONSE', 'source': 'MODEL',
             'created_at': T, 'tool_calls': [{'name': 'view_file', 'args': {'AbsolutePath': '/fixture/source.txt'}}]},
            {'step_index': 3, 'type': 'GENERIC', 'source': 'MODEL', 'created_at': T,
             'content': 'Created At: 2026-01-01T00:00:00Z\nCompleted At: 2026-01-01T00:00:00Z\nFile Path: `file:///fixture/source.txt`\nTotal Lines: 1\n1: evidence'}]

class ResultObservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Registry(Path(self.temp.name) / 'store')
    def tearDown(self):
        self.store.close()
        self.temp.cleanup()
    def read(self, rows, source='codex', **kw):
        raw = source_fixture(source) + b''.join(canonical(r)+b'\n' for r in rows)
        capture = normalize_captured(source, 'sample/session.jsonl', raw, self.store, **kw)
        import json
        detail = json.loads(self.store.read_artifact(capture.result_observations_ref)[0])
        return capture, detail
    def association(self, rows, source='codex'):
        return self.read(rows, source)[1]['calls'][0]['association']
    def test_identity_and_exact_raw_selectors(self):
        capture, detail = self.read(pair())
        call = detail['calls'][0]
        self.assertEqual(call['association'], 'identifier_bound')
        self.assertEqual(resolve_evidence(call['call_ref'], self.store).value, pair()[0]['payload'])
        self.assertEqual(resolve_evidence(call['result_refs'][0], self.store).value, pair()[1]['payload'])
        self.assertEqual(call['content']['text_characters'], 8)
        self.assertEqual(call['content']['completeness'], 'unknown')
        self.assertEqual(call['outcome'], 'unknown')
        self.assertNotIn('evidence', canonical(detail).decode())
    def test_interleaved_and_asynchronous_ids(self):
        a,b=pair();c=copy.deepcopy(a);c['payload']['call_id']='b';d=copy.deepcopy(b);d['payload']['call_id']='b'
        detail=self.read([a,c,d,b])[1]
        self.assertEqual([x['association'] for x in detail['calls']], ['identifier_bound']*2)
    def test_wrong_missing_and_duplicate_ids(self):
        a,b=pair()
        for rows in [[a], [a,a,b], [a,b,b]]:
            with self.subTest(rows=rows):
                self.assertEqual(self.association(rows), 'unavailable' if len(rows)==1 else 'ambiguous')
        b['payload']['call_id']='b'
        self.assertEqual(self.association([a,b]), 'unavailable')
        self.assertEqual(self.read([a,b])[1]['coverage']['orphan_results'],1)
        del a['payload']['call_id']
        self.assertEqual(self.association([a,b]), 'unavailable')
    def test_conflicting_identity_and_transport_kind(self):
        a,b=pair();c=copy.deepcopy(a);c['payload']['arguments']='{"other":true}'
        self.assertEqual(self.association([a,c,b]),'ambiguous')
        b['payload']['type']='custom_tool_call_output'
        self.assertEqual(self.association([a,b]),'ambiguous')
    def test_empty_missing_and_multipart_text(self):
        a,b=pair()
        for value,want in [('',0), (None,None), ([{'type':'text','text':'abc'},{'type':'image','data':'hidden'}],3)]:
            b['payload']['output']=value;c=self.read([a,b])[1]['calls'][0]['content']
            self.assertEqual(c['text_characters'],want)
            self.assertEqual(c['completeness'],'unknown')
        del b['payload']['output']
        self.assertIsNone(self.read([a,b])[1]['calls'][0]['content']['text_characters'])
    def test_nested_text_is_not_a_tool_event(self):
        row=codex('message',role='assistant',content=[{'type':'output_text','text':canonical(pair()).decode()}])
        self.assertEqual(self.read([row])[1]['coverage']['calls'],0)
    def test_claude_identity_and_false_is_not_implicit_success(self):
        rows=[{'type':'assistant','message':{'role':'assistant','content':[{'type':'tool_use','id':'a','name':'Read','input':{}}]}},
              {'type':'user','message':{'role':'user','content':[{'type':'tool_result','tool_use_id':'a','content':'','is_error':False}]}}]
        c=self.read(rows,'claude')[1]['calls'][0]
        self.assertEqual(c['association'],'identifier_bound')
        self.assertEqual(c['content']['text_characters'],0)
        self.assertEqual(c['outcome'],'reported_non_error')
    def test_agy_path_inference_and_no_status_promotion(self):
        c=self.read(agy(),'antigravity')[1]['calls'][0]
        self.assertEqual(c['association'],'path_corroborated')
        self.assertEqual(c['outcome'],'unknown')
    def test_agy_counterexamples_abstain(self):
        for kind in ['multi','wrong','gap','reversed','naive','quoted','malformed','duplicate_header']:
            rows=agy()
            if kind=='multi':rows[0]['tool_calls']*=2
            if kind=='wrong':rows[1]['content']=rows[1]['content'].replace('/fixture/source.txt','/fixture/other.txt')
            if kind=='gap':rows[1]['step_index']=4
            if kind=='reversed':rows[1]['created_at']='2025-01-01T00:00:00Z'
            if kind=='naive':rows[1]['created_at']='2026-01-01T00:00:00'
            if kind=='quoted':rows[1]['content']='1: File Path: `file:///fixture/source.txt`'
            if kind=='malformed':rows[0]['tool_calls'][0]['args']='invalid'
            if kind=='duplicate_header':rows[1]['content']+='\nFile Path: `file:///fixture/source.txt`'
            with self.subTest(kind=kind):self.assertNotEqual(self.association(rows,'antigravity'),'path_corroborated')
    def test_agy_latest_snapshot_and_invalid_snapshot(self):
        rows=agy();later=copy.deepcopy(rows[1]);later['content']='not a file result'
        self.assertNotEqual(self.association(rows+[later],'antigravity'),'path_corroborated')
        invalid=copy.deepcopy(later);invalid['step_index']=True
        self.assertEqual(self.association(rows+[invalid],'antigravity'),'path_corroborated')
    def test_unmatched_is_not_zero_and_references_stay_private(self):
        c=self.read(pair()[:1])[1]['calls'][0]
        self.assertIsNone(c['content']['text_characters'])
        self.assertEqual(c['call_ref']['content_class'],'private_raw')
        self.assertFalse(c['result_refs'])
    def test_selector_rejects_invalid_line_and_pointer(self):
        ref=self.read(pair())[1]['calls'][0]['call_ref']
        for line in [0,True,-1,99999]:
            bad=copy.deepcopy(ref);bad['selector']['line']=line
            self.assertEqual(resolve_evidence(bad,self.store).status,'invalid')
        bad=copy.deepcopy(ref);bad['selector']['pointer']='/absent'
        self.assertEqual(resolve_evidence(bad,self.store).status,'invalid')
    def test_future_result_does_not_match_closed_capture(self):
        rows=pair();rows[1]['timestamp']='2027-01-01T00:00:00Z'
        c=self.read(rows,window_end='2026-12-01T00:00:00Z')[1]['calls'][0]
        self.assertEqual(c['association'],'unavailable')
    def test_legacy_record_bytes_preserved(self):
        from retro_eval.adapters.registry import default_registry
        for source,rows in [('codex',pair()),('antigravity',agy())]:
            captured,_=self.read(rows,source)
            raw=source_fixture(source)+b''.join(canonical(r)+b'\n' for r in rows)
            root=Path(self.temp.name)/source;path=root/'sample/session.jsonl';path.parent.mkdir(parents=True);path.write_bytes(raw)
            adapter=default_registry().get(source).create(self.store.id_salt,{})
            self.assertEqual(tuple(r.to_dict() for r in adapter.read(path,root).records),captured.records)

if __name__=='__main__':unittest.main()
