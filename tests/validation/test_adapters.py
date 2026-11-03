"""Tests of the file adapter: reports persisted as JSONL (ReportStore port)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from gigabrain.validation.adapters.jsonl_store import JsonlReportStore
from gigabrain.validation.engine import ValidationEngine

from .helpers import artifact, envelope


class TestJsonlStore(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = JsonlReportStore(Path(tmp.name) / "reports.jsonl")

    def test_saves_and_loads_by_id(self):
        report = ValidationEngine().validate(envelope())
        self.store.save(report)
        loaded = self.store.load(report.run_id)
        self.assertEqual((loaded.verdict, loaded.envelope_hash), (report.verdict, report.envelope_hash))

    def test_keeps_findings_scores_and_notes(self):
        report = ValidationEngine().validate(envelope(artifact(criteria=[])), focus={"R1"})
        self.store.save(report)
        loaded = self.store.load(report.run_id)
        self.assertEqual(loaded.to_dict(), report.to_dict())
        self.assertFalse(loaded.item_results[0].passed)
        self.assertEqual(loaded.focus, ["R1"])

    def test_history_by_input_hash(self):
        env = envelope()
        for _ in range(2):
            self.store.save(ValidationEngine().validate(env))
        self.assertEqual(len(self.store.history(env.content_hash())), 2)

    def test_a_missing_file_gives_nothing(self):
        self.assertEqual(self.store.all(), [])
        self.assertIsNone(self.store.load("anything"))


if __name__ == "__main__":
    unittest.main()
