"""Tests of the report renderings and of the command line.

The report is what the PO and the agent itself consume. If it leaves a finding
out, validation existed for nothing.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from gigabrain.validation import cli
from gigabrain.validation.engine import ValidationEngine
from gigabrain.validation.envelope import load
from gigabrain.validation.model import DOCUMENT_DIMENSIONS, ITEM_DIMENSIONS, Severity, Verdict
from gigabrain.validation.registry import REGISTRY
from gigabrain.validation.report import to_agent_feedback, to_console, to_json, to_markdown, to_summary

from .helpers import artifact, envelope

EXAMPLES = Path(__file__).resolve().parents[2] / "gigabrain" / "validation" / "examples"

MESSY = dict(title="", statement="Everything must be fast, intuitive, user-friendly, efficient, adequate and robust.",
             story=None, criteria=[], evidences=[])


class TestMarkdownReport(unittest.TestCase):
    def setUp(self):
        self.report = ValidationEngine().validate(envelope(artifact(criteria=[])))
        self.text = to_markdown(self.report)

    def test_has_the_verdict(self):
        self.assertIn("**Verdict:** REJECTED", self.text)

    def test_names_the_competencies_that_did_not_pass(self):
        self.assertIn("**Competencies that did not pass:** Conforming", self.text)

    def test_has_the_provenance_of_the_run(self):
        for value in (self.report.envelope_hash, self.report.ruleset_version, self.report.policy_version):
            self.assertIn(value, self.text)

    def test_lists_the_ten_dimensions_with_their_gate(self):
        for dimension in ITEM_DIMENSIONS + DOCUMENT_DIMENSIONS:
            with self.subTest(dimension=dimension.value):
                self.assertIn(f"| {dimension.label} |", self.text)
        self.assertIn("| FAIL |", self.text)
        self.assertIn("| pass |", self.text)

    def test_leaves_no_finding_out(self):
        for finding in self.report.all_findings():
            with self.subTest(check=finding.check_id):
                self.assertIn(finding.check_id, self.text)

    def test_says_the_likert_score_is_a_proxy(self):
        self.assertIn("declared proxy", self.text)

    def test_item_level_only_has_no_document_section(self):
        text = to_markdown(ValidationEngine().validate(envelope(), document_level=False))
        self.assertNotIn("Document level", text)

    def test_a_dimension_nobody_looked_at_is_shown_as_not_assessed(self):
        report = ValidationEngine().validate(envelope(artifact("R1"), artifact("R2", title="Other")), focus={"R1"})
        self.assertIn("| Completeness | 1.00 | 5.00 | 0 | not assessed |", to_markdown(report))


class TestJsonReport(unittest.TestCase):
    def test_valid_json_with_the_main_keys(self):
        data = json.loads(to_json(ValidationEngine().validate(envelope())))
        for key in ("run_id", "envelope_hash", "ruleset_version", "verdict", "item_results", "document_scores", "checks_run", "focus"):
            self.assertIn(key, data)

    def test_dimensions_become_text_keys_and_carry_the_gate(self):
        data = json.loads(to_json(ValidationEngine().validate(envelope())))
        self.assertTrue(data["document_scores"]["traceability"]["passed"])


class TestSummary(unittest.TestCase):
    """The compact form that travels in the messages between agents and in the API."""

    def test_a_passing_artefact(self):
        summary = to_summary(ValidationEngine().validate(envelope(), document_level=False))
        self.assertEqual((summary["verdict"], summary["passed"], summary["failed_dimensions"]), ("approved", True, []))
        self.assertEqual(set(summary["items"]["R1"]["scores"]), {d.value for d in ITEM_DIMENSIONS})
        self.assertIsNone(summary["document"])

    def test_a_failing_artefact_says_which_competency_and_why(self):
        summary = to_summary(ValidationEngine().validate(envelope(artifact(criteria=[]))))
        self.assertFalse(summary["passed"])
        self.assertIn("conforming", summary["failed_dimensions"])
        self.assertFalse(summary["items"]["R1"]["scores"]["conforming"]["passed"])
        self.assertEqual(summary["findings"][0]["severity"], "blocker")
        self.assertEqual(set(summary["document"]), {d.value for d in DOCUMENT_DIMENSIONS})

    def test_is_plain_json(self):
        json.dumps(to_summary(ValidationEngine().validate(envelope(artifact(**MESSY)))))

    def test_the_number_of_findings_is_bounded(self):
        report = ValidationEngine().validate(envelope(artifact(**MESSY)))
        summary = to_summary(report, max_findings=2)
        self.assertEqual(len(summary["findings"]), 2)
        self.assertGreater(sum(summary["counts"].values()), 2)


class TestAgentFeedback(unittest.TestCase):
    def test_with_no_findings_it_says_there_is_nothing_to_fix(self):
        self.assertIn("nothing to fix", to_agent_feedback(ValidationEngine().validate(envelope())))

    def test_lists_the_findings_the_most_serious_first(self):
        text = to_agent_feedback(ValidationEngine().validate(envelope(artifact(criteria=[], evidences=[]))))
        self.assertIn("[blocking]", text)
        self.assertLess(text.index("[blocking]"), text.index("[major]"))

    def test_names_the_competencies_that_did_not_pass(self):
        text = to_agent_feedback(ValidationEngine().validate(envelope(artifact(criteria=[])), document_level=False))
        self.assertIn("Competencies that did not pass: Conforming.", text)

    def test_bounds_the_scope_of_the_correction(self):
        text = to_agent_feedback(ValidationEngine().validate(envelope(artifact(criteria=[]))))
        self.assertIn("Do not add new requirements", text)

    def test_respects_the_item_limit_and_says_there_is_more(self):
        report = ValidationEngine().validate(envelope(artifact(**MESSY)))
        text = to_agent_feedback(report, max_items=3)
        numbered = [line for line in text.splitlines() if line[:2] in ("1.", "2.", "3.", "4.")]
        self.assertEqual(len(numbered), 3)
        self.assertIn("more finding(s) in the full report", text)


class TestConsoleSummary(unittest.TestCase):
    def test_one_line_with_the_verdict(self):
        self.assertTrue(to_console(ValidationEngine().validate(envelope())).startswith("[APPROVED]"))

    def test_names_the_failed_competencies(self):
        self.assertIn("did not pass: conforming", to_console(ValidationEngine().validate(envelope(artifact(criteria=[])))))


class TestCommandLine(unittest.TestCase):
    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_the_good_example_exits_with_zero(self):
        code, out, _ = self.run_cli([str(EXAMPLES / "good.json")])
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("APPROVED", out)

    def test_the_bad_example_exits_with_one(self):
        code, out, _ = self.run_cli([str(EXAMPLES / "bad.json")])
        self.assertEqual(code, cli.EXIT_REJECTED)
        self.assertIn("REJECTED", out)

    def test_a_missing_file_exits_with_two(self):
        code, _, err = self.run_cli(["does-not-exist.json"])
        self.assertEqual(code, cli.EXIT_BAD_INPUT)
        self.assertIn("error", err.lower())

    def test_lists_the_checks(self):
        code, out, _ = self.run_cli(["--list-checks"])
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn(f"{len(REGISTRY)} checks registered", out)
        for check_id in REGISTRY.ids():
            self.assertIn(check_id, out)

    def test_json_format(self):
        _, out, _ = self.run_cli([str(EXAMPLES / "good.json"), "--format", "json"])
        self.assertEqual(json.loads(out)["verdict"], "approved")

    def test_feedback_format(self):
        _, out, _ = self.run_cli([str(EXAMPLES / "bad.json"), "--format", "feedback"])
        self.assertTrue(out.startswith("Validation returned the artefact: REJECTED."))

    def test_ablation_from_the_command_line(self):
        _, out, _ = self.run_cli([str(EXAMPLES / "bad.json"), "--format", "json", "--disable", "ambiguity.vague_terms"])
        self.assertIn("ambiguity.vague_terms", json.loads(out)["checks_skipped"])

    def test_policy_from_the_command_line(self):
        _, out, _ = self.run_cli([str(EXAMPLES / "good.json"), "--format", "json", "--policy", "strict"])
        self.assertEqual(json.loads(out)["policy_version"], "2.0-strict")

    def test_saves_the_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "reports.jsonl"
            self.run_cli([str(EXAMPLES / "good.json"), "--save-to", str(target)])
            self.assertEqual(len(target.read_text(encoding="utf-8").strip().splitlines()), 1)

    def test_writes_to_an_output_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "report.md"
            self.run_cli([str(EXAMPLES / "bad.json"), "--format", "markdown", "--output", str(target)])
            self.assertIn("Validation Report", target.read_text(encoding="utf-8"))


class TestRepositoryExamples(unittest.TestCase):
    """The examples are a contract: if their behaviour changes, the documentation lies."""

    def test_the_good_example_is_approved_with_no_findings_and_every_check_run(self):
        report = ValidationEngine().validate(load(EXAMPLES / "good.json"))
        self.assertIs(report.verdict, Verdict.APPROVED)
        self.assertEqual(report.all_findings(), [])
        self.assertEqual(len(report.checks_run), len(REGISTRY))

    def test_the_bad_example_is_rejected_with_blocking_findings(self):
        report = ValidationEngine().validate(load(EXAMPLES / "bad.json"))
        self.assertIs(report.verdict, Verdict.REJECTED)
        self.assertTrue(report.findings_by_severity(Severity.BLOCKER))

    def test_the_bad_example_touches_the_ten_dimensions(self):
        report = ValidationEngine().validate(load(EXAMPLES / "bad.json"))
        touched = {f.dimension for f in report.all_findings()}
        for dimension in ITEM_DIMENSIONS + DOCUMENT_DIMENSIONS:
            with self.subTest(dimension=dimension.value):
                self.assertIn(dimension, touched)

    def test_only_the_quote_changes_and_the_verdict_flips(self):
        # The illustration of the paper: nothing about the requirement changes, only
        # the passage it claims to come from, and one blocking finding appears.
        env = load(EXAMPLES / "good.json")
        target = env.artifact("R4")
        from dataclasses import replace
        target.evidence = [replace(target.evidence[0], quote="the client asked that the reporter be banned")]
        report = ValidationEngine().validate(env)
        self.assertIs(report.verdict, Verdict.REJECTED)
        self.assertEqual([(f.check_id, f.severity) for f in report.all_findings()],
                         [("grounding.quote_supported", Severity.BLOCKER)])


if __name__ == "__main__":
    unittest.main()
