from __future__ import annotations
import json
from pathlib import Path
import tempfile
import unittest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import field_test_report


class FieldTestReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        folder=self.root / "cp-1"; folder.mkdir()
        (folder / "manifest.json").write_text(json.dumps({"checkpoint_id":"cp-1"}), encoding="utf-8")

    def tearDown(self): self.tmp.cleanup()

    def test_inconclusive_report_needs_no_game_claim_and_is_separate(self):
        report=field_test_report.record_field_outcome(self.root,"cp-1","TEST-B","INCONCLUSIVE")
        data=json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(data["operator_reported_outcome"],"INCONCLUSIVE")
        self.assertEqual(data["restore_verified_status"],"NOT_SET_BY_THIS_REPORT")

    def test_pass_requires_real_game_confirmation_and_evidence(self):
        with self.assertRaisesRegex(ValueError,"confirmation"):
            field_test_report.record_field_outcome(self.root,"cp-1","TEST-A","PASS")
        evidence=self.root / "evidence.log"; evidence.write_text("synthetic evidence",encoding="utf-8")
        report=field_test_report.record_field_outcome(self.root,"cp-1","TEST-A","PASS",[evidence],True)
        data=json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(data["evidence_file_names"],["evidence.log"])

    def test_bad_test_or_outcome_rejected(self):
        with self.assertRaises(ValueError): field_test_report.record_field_outcome(self.root,"cp-1","TEST-Z","PASS")
        with self.assertRaises(ValueError): field_test_report.record_field_outcome(self.root,"cp-1","TEST-A","MAYBE")
        with self.assertRaises(Exception): field_test_report.record_field_outcome(self.root,"..","TEST-A","INCONCLUSIVE")

    def test_report_does_not_modify_checkpoint_manifest(self):
        manifest=self.root / "cp-1" / "manifest.json"; before=manifest.read_bytes()
        field_test_report.record_field_outcome(self.root,"cp-1","TEST-A","INCONCLUSIVE")
        self.assertEqual(manifest.read_bytes(),before)


if __name__ == "__main__": unittest.main()
