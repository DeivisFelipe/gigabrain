"""Tests of the document-level checks.

These look at the set: contradiction, repetition, coverage gaps and version
chains. They support the part of validation that deals with "everything
together", not with the single artefact.
"""

from __future__ import annotations

import unittest

from gigabrain.validation.engine import ValidationEngine
from gigabrain.validation.model import (
    Criterion,
    DocumentSection,
    GlossaryEntry,
    LinkRef,
    RequirementType,
    Severity,
    SpecificationDocument,
)
from gigabrain.validation.registry import CheckConfig

from .helpers import CLEAN_CRITERIA, artifact, check_ids, envelope, full_document, need, source, user_story

OTHER_STATEMENT = "When the scheduled time arrives, the system shall publish the post."
OTHER_CRITERIA = [
    Criterion("On time", "a post scheduled for 19:00", "the clock reaches 19:00", "the system publishes the post"),
    Criterion("Failure", "a post scheduled for 19:00", "publishing fails", "the system notifies the editor"),
]


def other(artifact_id="R2", **kwargs):
    """A requirement about something else, so that it never looks like the clean one."""
    defaults = dict(title="Publish scheduled posts", statement=OTHER_STATEMENT, criteria=OTHER_CRITERIA, evidences=[])
    return artifact(artifact_id, **{**defaults, **kwargs})


class DocumentCheckCase(unittest.TestCase):
    def findings_for(self, env, *, config=None, trace=None, focus=None):
        engine = ValidationEngine(config=config or CheckConfig(), trace=trace)
        return engine.validate(env, focus=focus).document_findings

    def severities(self, findings, check_id):
        return [f.severity for f in findings if f.check_id == check_id]


class TestConsistency(DocumentCheckCase):
    def test_a_repeated_id_is_blocking(self):
        env = envelope(artifact("R1"), artifact("R1", title="Another title"))
        self.assertEqual(self.severities(self.findings_for(env), "consistency.unique_ids"), [Severity.BLOCKER])

    def test_distinct_ids_do_not_fire(self):
        self.assertNotIn("consistency.unique_ids", check_ids(self.findings_for(envelope(artifact("R1"), other("R2")))))

    def test_contradiction_by_negation(self):
        states = artifact("R1", statement="The system shall notify the moderator of every report.", ears_pattern="ubiquitous")
        denies = artifact("R2", statement="The system shall not notify the moderator of every report.", ears_pattern="ubiquitous")
        findings = self.findings_for(envelope(states, denies))
        self.assertEqual(self.severities(findings, "consistency.negation_conflict"), [Severity.MINOR])
        # Design decision: a pair that negates each other is a contradiction, not a repetition.
        self.assertNotIn("redundancy.near_duplicate", check_ids(findings))

    def test_contradiction_in_portuguese(self):
        states = user_story("REQ-001", story="Como moderador do fórum, quero agrupar denúncias do mesmo tópico, para que eu decida uma vez.")
        denies = user_story("REQ-002", story="Como moderador do fórum, quero não agrupar denúncias do mesmo tópico, para que eu decida uma vez.")
        self.assertIn("consistency.negation_conflict", check_ids(self.findings_for(envelope(states, denies))))

    def test_same_requirement_with_another_value(self):
        fast = artifact("R1", statement="The review queue shall load the first page within 2 seconds.", ears_pattern="ubiquitous")
        slow = artifact("R2", statement="The review queue shall load the first page within 4 seconds.", ears_pattern="ubiquitous")
        findings = self.findings_for(envelope(fast, slow))
        self.assertEqual(self.severities(findings, "consistency.value_conflict"), [Severity.MINOR])
        self.assertNotIn("redundancy.near_duplicate", check_ids(findings))  # reported once

    def test_a_pair_the_agents_already_linked_is_not_reported_again(self):
        fast = artifact("R1", statement="The review queue shall load the first page within 2 seconds.", ears_pattern="ubiquitous")
        slow = artifact("R2", statement="The review queue shall load the first page within 4 seconds.", ears_pattern="ubiquitous",
                        links=[LinkRef("supersedes", "R1")])
        ids = check_ids(self.findings_for(envelope(fast, slow)))
        self.assertFalse({"consistency.value_conflict", "redundancy.near_duplicate"} & ids)

    def test_synonym_declared_in_the_glossary(self):
        env = envelope(
            artifact("R1", statement="When a topic receives the third report, the system shall create one item in the moderation queue."),
            glossary=[
                GlossaryEntry(term="review queue", definition="List of the reported topics."),
                GlossaryEntry(term="moderation queue", definition="synonym of review queue"),
            ],
        )
        self.assertIn("consistency.terminology_drift", check_ids(self.findings_for(env)))


class TestRedundancy(DocumentCheckCase):
    def test_almost_identical_requirements(self):
        findings = self.findings_for(envelope(artifact("R1"), artifact("R2", title="Merge repeated reports")))
        self.assertEqual(self.severities(findings, "redundancy.near_duplicate"), [Severity.MAJOR])

    def test_different_requirements_do_not_fire(self):
        self.assertNotIn("redundancy.near_duplicate", check_ids(self.findings_for(envelope(artifact("R1"), other("R2")))))

    def test_identical_criterion_in_different_artefacts(self):
        findings = self.findings_for(envelope(artifact("R1"), other("R2", criteria=[CLEAN_CRITERIA[0], OTHER_CRITERIA[0]])))
        self.assertIn("redundancy.duplicate_criteria", check_ids(findings))


class TestFocus(DocumentCheckCase):
    """One candidate against the saved repository: only what involves the candidate is reported."""

    def env(self):
        # R1 and R2 repeat each other; R3 is a new candidate that repeats nothing.
        return envelope(artifact("R1"), artifact("R2", title="Merge repeated reports"), other("R3"))

    def test_without_focus_the_saved_duplicate_is_reported(self):
        self.assertIn("redundancy.near_duplicate", check_ids(self.findings_for(self.env())))

    def test_a_candidate_is_not_blamed_for_the_defects_of_the_repository(self):
        self.assertNotIn("redundancy.near_duplicate", check_ids(self.findings_for(self.env(), focus={"R3"})))

    def test_a_candidate_that_repeats_a_saved_requirement_is_reported(self):
        findings = self.findings_for(self.env(), focus={"R2"})
        duplicates = [f for f in findings if f.check_id == "redundancy.near_duplicate"]
        self.assertEqual([f.target_id for f in duplicates], ["R1|R2"])

    def test_item_results_only_for_the_focus(self):
        report = ValidationEngine().validate(self.env(), focus={"R3"})
        self.assertEqual([i.artifact_id for i in report.item_results], ["R3"])
        self.assertEqual(report.focus, ["R3"])
        self.assertIn("completeness.nfr_presence", report.checks_skipped)  # about the whole set


class TestCompleteness(DocumentCheckCase):
    def test_required_section_missing(self):
        document = SpecificationDocument(title="Partial", sections=[DocumentSection(id="introduction", title="Introduction", text="Text.")])
        findings = [f for f in self.findings_for(envelope(artifact(), document=document)) if f.check_id == "completeness.required_sections"]
        self.assertEqual({f.target_id for f in findings}, {"stakeholders", "use_cases", "glossary"})

    def test_complete_document_does_not_fire(self):
        env = envelope(artifact(), document=full_document())
        self.assertNotIn("completeness.required_sections", check_ids(self.findings_for(env)))

    def test_empty_section(self):
        document = full_document()
        document.sections[2].text = ""
        self.assertIn("completeness.empty_sections", check_ids(self.findings_for(envelope(artifact(), document=document))))

    def test_orphan_need(self):
        env = envelope(user_story(), sources=[need("NEED-001"), need("NEED-002", text="Outra necessidade do cliente.")])
        findings = [f for f in self.findings_for(env) if f.check_id == "completeness.orphan_needs"]
        self.assertEqual([f.target_id for f in findings], ["NEED-002"])

    def test_stakeholder_turns_nobody_cites_are_listed_but_do_not_fail(self):
        silent = source("T4", text="We also need the moderators to export the review queue to a spreadsheet every week.")
        report = ValidationEngine().validate(envelope(artifact(), sources=[source(), silent]))
        listed = [f for f in report.document_findings if f.check_id == "completeness.uncited_turns"]
        self.assertEqual([(f.severity, f.target_id) for f in listed], [(Severity.INFO, "T4")])
        self.assertTrue(report.passed)

    def test_short_turns_are_not_listed(self):
        env = envelope(artifact(), sources=[source(), source("T3", text="Sorry, I was on mute.")])
        self.assertNotIn("completeness.uncited_turns", check_ids(self.findings_for(env)))

    def test_specification_with_functional_requirements_only(self):
        findings = self.findings_for(envelope(artifact(req_type=RequirementType.FUNCTIONAL)))
        self.assertEqual(self.severities(findings, "completeness.nfr_presence"), [Severity.INFO])


class TestConciseness(DocumentCheckCase):
    def test_filler_expression(self):
        art = artifact(statement="When a topic receives the third report, the system shall basically create one item in the review queue.")
        self.assertIn("conciseness.filler_terms", check_ids(self.findings_for(envelope(art))))

    def test_artefact_too_long(self):
        config = CheckConfig(max_words_per_artifact=10)
        self.assertIn("conciseness.artifact_length", check_ids(self.findings_for(envelope(artifact()), config=config)))


class _Provider:
    def __init__(self, *known):
        self.known = set(known)

    def knows(self, requirement_id):
        return requirement_id in self.known

    def predecessors(self, requirement_id):
        return []

    def impacted_by(self, requirement_id):
        return []


class TestTraceability(DocumentCheckCase):
    def test_incomplete_forward_coverage(self):
        findings = [f for f in self.findings_for(envelope(artifact("R1"), other("R2"))) if f.check_id == "traceability.forward_coverage"]
        self.assertEqual([f.target_id for f in findings], ["R2"])

    def test_incomplete_backward_coverage(self):
        env = envelope(user_story(), sources=[need("NEED-001"), need("NEED-002", text="Necessidade não atendida.")])
        self.assertIn("traceability.backward_coverage", check_ids(self.findings_for(env)))

    def test_source_that_does_not_say_where_it_came_from(self):
        self.assertIn("traceability.source_declared", check_ids(self.findings_for(envelope(artifact(), sources=[source(origin="")]))))

    def test_version_chain_without_a_provider_is_informative(self):
        # With no traceability data plugged in, the check says it could not look.
        # Failing here would punish the requirement for a gap of the architecture.
        findings = self.findings_for(envelope(artifact("R2", supersedes="R0")))
        self.assertEqual(self.severities(findings, "traceability.version_chain"), [Severity.INFO])

    def test_version_chain_with_a_provider_that_knows(self):
        findings = self.findings_for(envelope(artifact("R2", supersedes="R0")), trace=_Provider("R0"))
        self.assertNotIn("traceability.version_chain", check_ids(findings))

    def test_version_chain_with_a_provider_that_does_not_know(self):
        findings = self.findings_for(envelope(artifact("R2", supersedes="R0")), trace=_Provider())
        self.assertEqual(self.severities(findings, "traceability.version_chain"), [Severity.MAJOR])

    def test_predecessor_in_the_envelope_itself(self):
        env = envelope(other("R0"), artifact("R2", supersedes="R0"))
        self.assertNotIn("traceability.version_chain", check_ids(self.findings_for(env)))


if __name__ == "__main__":
    unittest.main()
