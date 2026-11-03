"""Tests of the engine: verdict, scores, ablation, failure isolation and determinism.

Determinism is what backs the promise of reproducibility.
If this file passes, two researchers with the same input and the same rule-set
version get the same report.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from gigabrain.validation.engine import ValidationEngine
from gigabrain.validation.model import Criterion, Dimension, Level, Severity, Story, Verdict
from gigabrain.validation.policy import LENIENT_POLICY, STRICT_POLICY, Policy
from gigabrain.validation.ports import FixedClock
from gigabrain.validation.registry import REGISTRY, CheckConfig
from gigabrain.validation.scoring import RULESET_VERSION, to_likert

from .helpers import CLEAN_CRITERIA, artifact, envelope, evidence, source

EXAMPLES = Path(__file__).resolve().parents[2] / "gigabrain" / "validation" / "examples"

VAGUE = "When a topic receives the third report, the system shall create one item fast."
OPTIONAL = "When a topic receives the third report, the system shall create one item if possible."
#: A minor defect: "user" while the default turn names the moderator.
GENERIC_ROLE = Story(role="user", goal="to see the reports of a topic as one item", benefit="I decide once per topic")


class TestVerdict(unittest.TestCase):
    def test_a_clean_artefact_is_approved(self):
        self.assertIs(ValidationEngine().validate(envelope()).verdict, Verdict.APPROVED)

    def test_a_blocking_finding_rejects(self):
        self.assertIs(ValidationEngine().validate(envelope(artifact(criteria=[]))).verdict, Verdict.REJECTED)

    def test_a_minor_finding_approves_with_remarks(self):
        report = ValidationEngine().validate(envelope(artifact(story=GENERIC_ROLE)))
        self.assertIs(report.verdict, Verdict.APPROVED_WITH_REMARKS)

    def test_an_information_finding_approves(self):
        # One criterion is information only; it neither remarks nor rejects.
        report = ValidationEngine().validate(envelope(artifact(criteria=CLEAN_CRITERIA[:1])))
        self.assertIs(report.verdict, Verdict.APPROVED)

    def test_the_strict_policy_rejects_what_the_default_one_remarks(self):
        env = envelope(artifact(story=GENERIC_ROLE))
        self.assertIs(ValidationEngine(policy=STRICT_POLICY).validate(env).verdict, Verdict.REJECTED)
        self.assertIs(ValidationEngine().validate(env).verdict, Verdict.APPROVED_WITH_REMARKS)

    def test_the_lenient_policy_tolerates_a_failed_competency(self):
        env = envelope(artifact(statement=VAGUE))
        self.assertIs(ValidationEngine().validate(env).verdict, Verdict.REJECTED)
        self.assertIsNot(ValidationEngine(policy=LENIENT_POLICY).validate(env).verdict, Verdict.REJECTED)

    def test_the_lenient_policy_still_rejects_a_blocking_finding(self):
        # Blocking means an unusable artefact, not a poor one: no ruler lets it through.
        env = envelope(artifact(criteria=[]))
        self.assertIs(ValidationEngine(policy=LENIENT_POLICY).validate(env).verdict, Verdict.REJECTED)

    def test_the_overall_verdict_is_the_worst_of_the_partial_ones(self):
        env = envelope(artifact("R1"), artifact("R2", title="Bad", statement="When a payment is declined, the system shall retry it.",
                                                criteria=[], evidences=[]))
        report = ValidationEngine().validate(env)
        self.assertIs(report.item_result("R1").verdict, Verdict.APPROVED)
        self.assertIs(report.item_result("R2").verdict, Verdict.REJECTED)
        self.assertIs(report.verdict, Verdict.REJECTED)


class TestCompetencyGate(unittest.TestCase):
    """The rule of the gate: an artefact passes only if every competency passes."""

    def item(self, art):
        return ValidationEngine().validate(envelope(art), document_level=False).item_results[0]

    def test_a_clean_artefact_passes_the_five_competencies(self):
        item = self.item(artifact())
        self.assertEqual(len(item.scores), 5)
        self.assertTrue(all(score.passed for score in item.scores.values()))
        self.assertEqual(item.failed_dimensions, [])

    def test_one_failed_competency_rejects_the_artefact_and_is_named(self):
        item = self.item(artifact(statement=VAGUE))
        self.assertEqual(item.failed_dimensions, [Dimension.UNAMBIGUOUS])
        self.assertFalse(item.passed)

    def test_a_finding_that_keeps_the_competency_above_the_minimum_does_not_reject(self):
        # One major finding of weight 1.0 leaves the dimension at 0.75, above the 0.70 minimum.
        item = self.item(artifact(statement=OPTIONAL))
        self.assertEqual(item.scores[Dimension.UNAMBIGUOUS].score, 0.75)
        self.assertTrue(item.passed)
        self.assertIs(item.verdict, Verdict.APPROVED_WITH_REMARKS)

    def test_the_decision_is_per_competency_not_by_the_number_of_findings(self):
        # Findings spread over three dimensions, none of which falls below the minimum.
        art = artifact(statement=OPTIONAL, criteria=CLEAN_CRITERIA[:1], req_type=__import__("gigabrain.validation.model", fromlist=["x"]).RequirementType.UNKNOWN)
        item = self.item(art)
        self.assertGreaterEqual(len(item.findings), 3)
        self.assertTrue(item.passed)

    def test_a_blocking_finding_fails_its_competency_whatever_the_score(self):
        policy = Policy(penalties={Severity.BLOCKER: 0.0, Severity.MAJOR: 0.25, Severity.MINOR: 0.1, Severity.INFO: 0.02})
        report = ValidationEngine(policy=policy).validate(envelope(artifact(criteria=[])), document_level=False)
        conforming = report.item_results[0].scores[Dimension.CONFORMING]
        self.assertGreaterEqual(conforming.score, 0.7)
        self.assertFalse(conforming.passed)

    def test_the_minimum_is_configurable(self):
        env = envelope(artifact(statement=OPTIONAL))
        demanding = Policy(version="test", min_dimension_score=0.80)
        self.assertIs(ValidationEngine(policy=demanding).validate(env, document_level=False).verdict, Verdict.REJECTED)

    def test_the_report_lists_the_failed_competencies_of_both_levels(self):
        twin = artifact("R2", title="Merge repeated reports")
        report = ValidationEngine().validate(envelope(artifact("R1", statement=VAGUE), twin))
        self.assertEqual(set(report.failed_dimensions()), {Dimension.UNAMBIGUOUS, Dimension.NON_REDUNDANCY})


class TestScores(unittest.TestCase):
    def test_a_clean_artefact_has_the_top_score(self):
        report = ValidationEngine().validate(envelope())
        for score in report.item_results[0].scores.values():
            self.assertEqual((score.score, score.likert), (1.0, 5.0))

    def test_the_score_falls_only_in_the_affected_dimension(self):
        scores = ValidationEngine().validate(envelope(artifact(statement=VAGUE))).item_results[0].scores
        self.assertLess(scores[Dimension.UNAMBIGUOUS].score, 1.0)
        self.assertEqual(scores[Dimension.CORRECTNESS].score, 1.0)

    def test_the_score_has_a_floor_at_zero(self):
        art = artifact(title="", statement="Everything must be fast, intuitive, user-friendly, efficient, adequate and robust.",
                       story=None, criteria=[], evidences=[])
        scores = ValidationEngine().validate(envelope(art)).item_results[0].scores
        self.assertGreaterEqual(min(s.score for s in scores.values()), 0.0)

    def test_conversion_to_likert(self):
        self.assertEqual((to_likert(0.0), to_likert(0.5), to_likert(1.0)), (1.0, 3.0, 5.0))

    def test_all_ten_dimensions_are_scored(self):
        report = ValidationEngine().validate(envelope())
        self.assertEqual(len(report.item_results[0].scores), 5)
        self.assertEqual(len(report.document_scores), 5)

    def test_item_level_only_gives_five_scores(self):
        report = ValidationEngine().validate(envelope(), document_level=False)
        self.assertEqual(len(report.item_results[0].scores), 5)
        self.assertEqual(report.document_scores, {})
        self.assertFalse(report.document_assessed)
        self.assertFalse(any(REGISTRY.get(c).level is Level.DOCUMENT for c in report.checks_run))

    def test_a_dimension_no_check_looked_at_is_not_assessed(self):
        # In a focused run the completeness checks, which are about the whole set, do not run.
        report = ValidationEngine().validate(envelope(artifact("R1"), artifact("R2", title="Other")), focus={"R1"})
        completeness = report.document_scores[Dimension.COMPLETENESS]
        self.assertEqual((completeness.checks, completeness.assessed), (0, False))
        self.assertTrue(report.document_scores[Dimension.NON_REDUNDANCY].assessed)


class TestAblation(unittest.TestCase):
    def test_switching_a_check_off_removes_its_findings(self):
        env = envelope(artifact(statement=VAGUE))
        on = ValidationEngine().validate(env)
        off = ValidationEngine(config=CheckConfig(disabled_checks=frozenset({"ambiguity.vague_terms"}))).validate(env)
        self.assertIn("ambiguity.vague_terms", {f.check_id for f in on.all_findings()})
        self.assertNotIn("ambiguity.vague_terms", {f.check_id for f in off.all_findings()})
        self.assertIn("ambiguity.vague_terms", off.checks_skipped)

    def test_the_report_records_what_ran(self):
        from gigabrain.validation.envelope import load

        report = ValidationEngine().validate(load(EXAMPLES / "good.json"))
        self.assertEqual(len(report.checks_run), len(REGISTRY))
        self.assertEqual(report.checks_skipped, {})

    def test_a_severity_can_be_overridden(self):
        config = CheckConfig(severity_overrides={"structure.acceptance_criteria": Severity.INFO})
        report = ValidationEngine(config=config).validate(envelope(artifact(criteria=[])))
        findings = [f for f in report.all_findings() if f.check_id == "structure.acceptance_criteria"]
        self.assertTrue(findings)
        self.assertTrue(all(f.severity is Severity.INFO for f in findings))
        self.assertIsNot(report.verdict, Verdict.REJECTED)


class TestApplicability(unittest.TestCase):
    """Material absent from the run is "I do not know", never "it does not exist".

    Regression of a real defect: validating the raw output of the Twin **without**
    its sources, the grounding check reported the cited source as missing and
    rejected the artefact, punishing the requirement for a gap of whoever built
    the input and not for a defect of its own.
    """

    def test_without_sources_grounding_is_skipped_not_failed(self):
        report = ValidationEngine().validate(envelope(artifact(evidences=[evidence(source_id="T42")]), sources=[]))
        ids = {f.check_id for f in report.all_findings()}
        self.assertFalse({"grounding.evidence_resolvable", "grounding.statement_supported"} & ids)
        self.assertIn("grounding.evidence_resolvable", report.checks_skipped)
        self.assertIsNot(report.verdict, Verdict.REJECTED)

    def test_with_sources_a_missing_source_still_rejects(self):
        # The other side of the coin: with material present, citing what is not in
        # it is a defect of the artefact, and a blocking one.
        report = ValidationEngine().validate(envelope(artifact(evidences=[evidence(source_id="T42")])))
        self.assertIn("grounding.evidence_resolvable", {f.check_id for f in report.all_findings()})
        self.assertIs(report.verdict, Verdict.REJECTED)

    def test_the_reason_for_skipping_is_recorded_in_words(self):
        report = ValidationEngine().validate(envelope(sources=[]))
        self.assertIn("no sources", report.checks_skipped["traceability.forward_coverage"])

    def test_without_a_document_the_section_checks_are_skipped(self):
        report = ValidationEngine().validate(envelope())
        self.assertIn("completeness.required_sections", report.checks_skipped)
        self.assertNotIn("completeness.required_sections", {f.check_id for f in report.all_findings()})

    def test_without_a_glossary_the_term_checks_are_skipped(self):
        report = ValidationEngine().validate(envelope())
        self.assertIn("consistency.terminology_drift", report.checks_skipped)
        self.assertIn("readability.glossary_usage", report.checks_skipped)

    def test_a_skipped_check_is_not_in_checks_run(self):
        report = ValidationEngine().validate(envelope(sources=[]))
        self.assertFalse(set(report.checks_run) & set(report.checks_skipped))


class TestFailureIsolation(unittest.TestCase):
    def test_a_check_that_breaks_does_not_bring_validation_down(self):
        from gigabrain.validation.registry import Check, CheckRegistry

        def explode(artifact, ctx):
            raise RuntimeError("deliberate failure")

        registry = CheckRegistry()
        for check in REGISTRY.all():
            registry.register(check)
        registry.register(Check(id="test.explode", level=Level.ITEM, dimension=Dimension.CONFORMING,
                                default_severity=Severity.MINOR, description="A test check that always breaks.", fn=explode))

        report = ValidationEngine(registry=registry).validate(envelope())
        failures = [f for f in report.all_findings() if f.check_id == "test.explode"]
        self.assertEqual(len(failures), 1)
        self.assertIn("failed to run", failures[0].message)


class TestDeterminism(unittest.TestCase):
    def test_the_same_input_gives_the_same_report(self):
        env = envelope(artifact(), artifact("R2", title="Other", statement=VAGUE))
        clock = FixedClock()
        a = ValidationEngine(clock=clock).validate(env)
        b = ValidationEngine(clock=clock).validate(env)
        a.run_id = b.run_id = "fixed"
        self.assertEqual(a.to_dict(), b.to_dict())

    def test_the_input_hash_ignores_the_id_of_the_run(self):
        self.assertEqual(envelope(run_id="run-a").content_hash(), envelope(run_id="run-b").content_hash())

    def test_the_hash_changes_when_the_content_changes(self):
        self.assertNotEqual(envelope(artifact()).content_hash(), envelope(artifact(title="Another title")).content_hash())

    def test_the_report_carries_the_rule_set_and_policy_versions(self):
        report = ValidationEngine(policy=Policy(version="2.0-test")).validate(envelope())
        self.assertEqual(report.ruleset_version, RULESET_VERSION)
        self.assertEqual(report.policy_version, "2.0-test")


class TestNotes(unittest.TestCase):
    def notes(self, report):
        return " ".join(report.notes)

    def test_says_the_llm_judge_is_off(self):
        self.assertIn("LLM judge", self.notes(ValidationEngine().validate(envelope())))

    def test_says_there_is_no_traceability_provider(self):
        self.assertIn("traceability provider", self.notes(ValidationEngine().validate(envelope())))

    def test_says_the_run_has_no_sources(self):
        self.assertIn("without sources", self.notes(ValidationEngine().validate(envelope(sources=[]))))

    def test_says_the_run_is_focused(self):
        report = ValidationEngine().validate(envelope(artifact("R1"), artifact("R2", title="Other")), focus={"R1"})
        self.assertIn("1 artefact(s) assessed against 1 artefact(s) of context", self.notes(report))


if __name__ == "__main__":
    unittest.main()
