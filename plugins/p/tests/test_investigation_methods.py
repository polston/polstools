"""Composition checks use authored modules and observable conflict outcomes."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retro_eval.investigation.methods import MethodRegistry
from retro_eval.workflow.errors import WorkflowError


class MethodsTests(unittest.TestCase):
    def test_domains_compose_and_unknown_domain_requires_research(self):
        registry = MethodRegistry()
        result = registry.resolve(['software', 'empirical', 'unfamiliar'])
        self.assertEqual({m['id'] for m in result['selected']}, {'general', 'software', 'empirical'})
        self.assertEqual(result['unfamiliar_domains'], ['unfamiliar'])
        self.assertIn('comparator', result['obligations'])
        self.assertIn('regression constraints', result['obligations'])

    def manifest(self, transform):
        registry = MethodRegistry()
        value = json.loads((registry.root / 'profiles/investigation-methods-v1.json').read_text())
        transform(value['modules'])
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / 'methods.json'
        path.write_text(json.dumps(value))
        return MethodRegistry(path)

    def test_new_method_requires_no_dispatch_edit(self):
        def add(modules):
            new = copy.deepcopy(modules[0])
            new.update(id='authored-extension', domains=['new-domain'])
            modules.append(new)
        result = self.manifest(add).resolve(['new-domain'])
        self.assertIn('authored-extension', {m['id'] for m in result['selected']})
        self.assertEqual(result['unfamiliar_domains'], [])

    def test_conflicts_are_visible_not_load_order_winners(self):
        def conflict(modules):
            modules[1]['incompatible'] = ['empirical']
        result = self.manifest(conflict).resolve(['software', 'empirical'])
        self.assertEqual(result['conflicts'], [['empirical', 'software']])
        self.assertEqual(result['next_action'], 'resolve method conflicts')

    def test_missing_capability_defers_method(self):
        def needs(modules):
            modules[1]['capabilities'] = ['isolated_execution']
        registry = self.manifest(needs)
        self.assertIn('software', registry.resolve(['software'])['deferred'])
        self.assertNotIn('software', registry.resolve(['software'], ['isolated_execution'])['deferred'])

    def test_cycle_and_guidance_escape_are_rejected(self):
        def cycle(modules):
            modules[0]['requires'] = ['software']
        with self.assertRaises(WorkflowError):
            self.manifest(cycle).resolve(['software'])
        def escape(modules):
            modules[0]['guidance'] = '../outside.md'
        with self.assertRaises(WorkflowError):
            self.manifest(escape)
