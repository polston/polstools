"""Metadata comparison cannot turn retrieval differences into content claims."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retro_eval.workflow.inventory import compare_inventory
from retro_eval.workflow.errors import WorkflowError


def snapshot():
    return {'inventory_format': 1, 'sources': [
        {'source': 'authored', 'root': '/fixture', 'available': True,
         'truncated': False, 'reason': ''}], 'files': [
        {'source': 'authored', 'relative_path': 'one.jsonl', 'key': 'salted-one',
         'bytes': 10, 'mtime_ns': 1}]}


class InventoryTests(unittest.TestCase):
    def test_salt_changes_do_not_create_files_and_equal_metadata_is_not_content(self):
        old = snapshot()
        new = copy.deepcopy(old)
        new['files'][0]['key'] = 'different-store-salt'
        result = compare_inventory(old, new)
        self.assertEqual(result['equal_metadata_count'], 1)
        self.assertEqual(result['newly_observed'], [])
        self.assertIn('no creation, deletion, unchanged-content', result['limitation'])

    def test_same_relative_identity_in_another_root_is_incomparable(self):
        old = snapshot()
        new = copy.deepcopy(old)
        new['sources'][0]['root'] = '/another-fixture'
        result = compare_inventory(old, new)
        self.assertEqual(result['equal_metadata_count'], 0)
        self.assertEqual(result['incomparable_sources'], [
            {'source': 'authored', 'reason': 'source_root_changed'}])
        self.assertEqual(result['newly_observed'], [])
        self.assertEqual(result['not_observed_in_current'], [])

    def test_partial_views_preserve_gaps_without_implying_deletion(self):
        old = snapshot()
        new = copy.deepcopy(old)
        new['sources'][0]['truncated'] = True
        new['files'] = []
        result = compare_inventory(old, new)
        self.assertEqual(result['not_observed_in_current'], [
            {'source': 'authored', 'relative_path': 'one.jsonl'}])
        self.assertEqual(result['coverage_gaps'][0]['snapshot'], 'current')
        new['sources'][0]['truncated'] = False
        complete = compare_inventory(old, new)
        self.assertEqual(complete['not_observed_in_current'], result['not_observed_in_current'])
        self.assertNotIn('deleted', complete)

    def test_missing_sources_are_incomparable_and_change_details_are_exact(self):
        old = snapshot()
        new = copy.deepcopy(old)
        new['files'][0]['bytes'] = 11
        result = compare_inventory(old, new)
        self.assertEqual(result['metadata_changed'][0]['previous'], {'bytes': 10, 'mtime_ns': 1})
        self.assertEqual(result['metadata_changed'][0]['current'], {'bytes': 11, 'mtime_ns': 1})
        absent = compare_inventory(old, {'inventory_format': 1, 'sources': [], 'files': []})
        self.assertEqual(absent['incomparable_sources'][0]['reason'], 'source_not_in_both_snapshots')
        self.assertEqual(absent['not_observed_in_current'], [])

    def test_invalid_or_duplicate_identities_and_metadata_are_rejected(self):
        variants = []
        for field, invalid in (('bytes', -1), ('bytes', True), ('mtime_ns', 1.5),
                               ('relative_path', '../escape'), ('relative_path', '/absolute'),
                               ('source', 'undeclared')):
            item = snapshot();item['files'][0][field] = invalid;variants.append(item)
        for field, invalid in (('available', 'false'), ('truncated', 0), ('reason', None)):
            item = snapshot();item['sources'][0][field] = invalid;variants.append(item)
        item = snapshot();item['files'].append(copy.deepcopy(item['files'][0]));variants.append(item)
        item = snapshot();item['sources'].append(copy.deepcopy(item['sources'][0]));variants.append(item)
        for item in variants:
            with self.subTest(snapshot=item), self.assertRaises(WorkflowError):
                compare_inventory(item, snapshot())


if __name__ == '__main__':
    unittest.main()
