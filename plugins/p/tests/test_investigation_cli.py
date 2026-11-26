"""Exercise the actual command boundary and a complete disposable lifecycle."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from test_investigation_state import frame, action

PLUGIN = Path(__file__).resolve().parents[1]
COMMAND = [sys.executable, '-B', str(PLUGIN / 'bin' / 'investigation')]


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.store = self.path / 'store'

    def run_cli(self, command, data=None, *args, code=0):
        call = COMMAND + [command, '--store', str(self.store)]
        if command not in ('artifact', 'import', 'export'):
            call += ['--root', 'authored']
        if data is not None:
            path = self.path / 'input.json'
            path.write_text(json.dumps(data))
            call += ['--input', str(path)]
        result = subprocess.run(call + list(args), capture_output=True, text=True)
        self.assertEqual(result.returncode, code, result.stderr)
        return json.loads(result.stdout) if result.stdout else json.loads(result.stderr)

    def test_actual_cli_lifecycle_and_private_receipt(self):
        self.run_cli('begin', {'frame': frame(), 'deadline_ms': time.time_ns() // 1_000_000 + 60000,
            'limits': {}, 'authority': ['read'], 'capabilities': {
                'deadline': {'mode': 'helper_enforced', 'scope': 'admission'}}})
        self.run_cli('audit', code=1)
        artifact = self.run_cli('artifact', {'observation': 'authored passing check'})
        self.assertEqual(artifact['content_class'], 'private_raw')
        self.run_cli('record', {'criterion': 'behavior', 'status': 'pass', 'frame_seq': 0,
            'evidence': [artifact], 'inputs': [], 'procedure': 'authored check',
            'verifier': 'test', 'claim_type': 'software', 'limitations': ['authored transport test']},
            '--expected-sequence', '0', '--type', 'acceptance')
        self.run_cli('record', {'status': 'complete', 'reason': 'acceptance checked', 'next_action': 'report'},
            '--expected-sequence', '1', '--type', 'finish')
        self.assertEqual(self.run_cli('checkpoint')['status'], 'complete')
        self.assertTrue(self.run_cli('audit')['ready'])

    def test_unknown_field_and_duplicate_json_key_fail(self):
        bad = self.path / 'bad.json'
        bad.write_text('{"frame":{},"frame":{}}')
        result = subprocess.run(COMMAND + ['begin', '--store', str(self.store), '--root', 'bad',
            '--input', str(bad)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('duplicate JSON key', result.stderr)

    def test_contract_and_method_discovery(self):
        for args in (['contract'], ['methods', '--domain', 'software']):
            result = subprocess.run(COMMAND + args, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIsInstance(json.loads(result.stdout), dict)

    def test_documented_begin_example_runs_without_implementation_lookup(self):
        guide = (PLUGIN / 'skills/investigating-and-improving/references/state.md').read_text()
        data = json.loads(guide.split('```json\n', 1)[1].split('```', 1)[0])
        data['deadline_ms'] = time.time_ns() // 1_000_000 + 60000
        self.run_cli('begin', data)
        state = self.run_cli('checkpoint')
        self.assertEqual(state['grants']['root']['limits'], {'actions': 5})
        self.assertEqual(state['frame']['requester'], 'parent')

    def test_corrections_recover_in_fresh_processes_and_survive_export(self):
        definition = subprocess.run(COMMAND + ['contract'], capture_output=True, text=True)
        self.assertEqual(definition.returncode, 0, definition.stderr)
        digest = json.loads(definition.stdout)['extensions']['correction']['sha256']
        self.run_cli('begin', {'frame': frame(), 'deadline_ms': time.time_ns() // 1_000_000 + 60000,
            'limits': {'actions': 2}, 'authority': ['read'], 'capabilities': {
                'execution': {'mode': 'advisory', 'scope': 'synthetic observations'}}})
        seq = 0
        for key, wrong, right in [('comparison', 'Only B passes', 'Both pass'),
                                   ('count', 'Five errors and one failure', 'Six errors')]:
            evidence = self.run_cli('artifact', {'actual': right})
            self.run_cli('record', action(key), '--expected-sequence', str(seq), '--type', 'action')
            result = self.run_cli('record', {'action_id':key,'status':'succeeded','usage':{'actions':1},
                'evidence':[evidence],'producer':'agent','origin':'agent','observation':wrong},
                '--expected-sequence', str(seq + 1), '--type', 'result')
            target = {k: result[k] for k in ('study_scope','kind','key','revision')}
            correction = {'contract_sha256':digest,'target':target,'interpretation':right,
                'source':'reviewer','reason':'Actual output contradicts summary','evidence':[evidence]}
            self.run_cli('record', correction, '--expected-sequence', str(seq + 2), '--type','correction')
            checkpoint = self.run_cli('checkpoint')
            current = checkpoint['actions'][key]
            self.assertEqual(current['result']['observation'], wrong)
            self.assertEqual(current['current_interpretation']['interpretation'], right)
            self.assertTrue(current['current_interpretation']['applicability']['current'])
            self.run_cli('record', correction, '--expected-sequence', str(seq + 3),
                         '--type','correction', code=2)
            seq += 3
        self.assertEqual(checkpoint['grants']['root']['spent'], {'actions': 2})
        bundle = self.run_cli('export')
        self.store = self.path / 'imported'
        self.run_cli('import', bundle)
        recovered = self.run_cli('checkpoint')
        self.assertEqual(recovered['interpretations'], checkpoint['interpretations'])
        self.assertEqual(recovered['grants'], checkpoint['grants'])
