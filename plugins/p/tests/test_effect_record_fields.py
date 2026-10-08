"""A rejected effect record names the field that failed."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from test_effect_records import (checker, synthetic_bundle, synthetic_record,
                                 write_packet)


class FieldNames(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bundle = synthetic_bundle()
        self.record = synthetic_record(self.bundle)

    def rejected_field(self, mutate_record=None, mutate_index=None):
        if mutate_record:
            mutate_record(self.record)
        record_path, index_path = write_packet(self.root, self.bundle, self.record)
        if mutate_index:
            index = json.loads(index_path.read_text(encoding="utf-8"))
            mutate_index(index)
            index_path.write_text(json.dumps(index), encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = checker.main(["--record", str(record_path),
                                 "--evidence-index", str(index_path)])
        self.assertEqual(2, code)
        return json.loads(err.getvalue()).get("field")

    def test_missing_nested_text_names_its_path(self):
        self.assertEqual("next_action.owner", self.rejected_field(
            lambda r: r["next_action"].pop("owner")))

    def test_blank_top_level_text_names_the_field(self):
        self.assertEqual("rationale", self.rejected_field(
            lambda r: r.update(rationale="   ")))

    def test_bad_string_list_names_the_field(self):
        self.assertEqual("quality_constraints", self.rejected_field(
            lambda r: r.update(quality_constraints=[""])))

    def test_evidence_index_entries_name_their_position(self):
        self.assertEqual("evidence[0].artifact", self.rejected_field(
            mutate_index=lambda i: i["evidence"][0].pop("artifact")))

    def test_observation_errors_name_signal_and_phase(self):
        def drop(record):
            del record["observations"]["later_use"]["followup"]["value"]
        self.assertEqual("observations.later_use.followup.value",
                         self.rejected_field(drop))


if __name__ == "__main__":
    unittest.main()
