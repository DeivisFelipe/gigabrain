"""What the first conversations with a real LLM showed."""

from __future__ import annotations

import unittest

from gigabrain.validation import host
from gigabrain.validation.checks.verifiability import _has_effect
from gigabrain.validation.engine import ValidationEngine
from gigabrain.validation.lexicons import OBSERVABLE_RE
from gigabrain.validation.model import Severity
from gigabrain.validation.text import language_of, normalize

from .flows import Flow
from .helpers import artifact, envelope, evidence, source

ENGLISH = "I want the system to remind pet owners when a vaccine is due and to send the email 7 days before the date."
PORTUGUESE = "Quando faltam 7 dias para a data da próxima dose de um animal, o sistema deve enviar por email um lembrete ao dono desse animal."


class TestLanguage(unittest.TestCase):
    def test_it_tells_the_two_languages_apart(self):
        self.assertEqual(language_of(ENGLISH), "English")
        self.assertEqual(language_of(PORTUGUESE), "Portuguese")

    def test_it_does_not_guess(self):
        self.assertIsNone(language_of("ok"))
        self.assertIsNone(language_of("Yes, the"))
        self.assertIsNone(language_of(f"{ENGLISH} {PORTUGUESE}"))

    def findings(self, statement: str, said: str):
        art = artifact(statement=statement, evidences=[evidence("s1")])
        report = ValidationEngine().validate_artifact(art, envelope(art, sources=[source("s1", said)]))
        return [f for f in report.findings if f.check_id == "grounding.statement_supported"]

    def test_a_statement_in_another_language_says_so(self):
        found = self.findings(PORTUGUESE, ENGLISH)
        self.assertEqual([f.severity for f in found], [Severity.MAJOR])
        self.assertIn("in Portuguese", found[0].message)
        self.assertIn("in English", found[0].message)
        self.assertIn("Write the requirement in English", found[0].suggestion)

    def test_an_invented_statement_keeps_the_usual_message(self):
        found = self.findings("The system shall print the invoices of the warehouse on thermal paper for every store.", ENGLISH)
        self.assertEqual([f.severity for f in found], [Severity.MAJOR])
        self.assertIn("support", found[0].message)

    def test_the_same_language_with_the_words_of_the_source_finds_nothing(self):
        self.assertEqual(self.findings("The system shall remind pet owners when a vaccine is due.", ENGLISH), [])


class TestActionsStatedByTheGrammar(unittest.TestCase):
    """The verbs of a technical domain are not in any word list."""

    def test_actions_of_a_technical_domain(self):
        for then in ("the system coordinates delivery between the two CDNs", "the system does not coordinate delivery between them",
                     "the messages are transported over TCP/IP", "the corresponding function is executed", "the interaction protocol is not invoked",
                     "the peering arrangement is terminated and no longer active", "the system still processes them without failure"):
            with self.subTest(then=then):
                self.assertTrue(_has_effect(then))

    def test_a_state_or_an_expectation_is_still_no_effect(self):
        for then in ("the need is met", "the requirement is considered satisfied", "the system behaves as the PO described",
                     "all pages and functions work correctly in each of the three browsers", "the system is in normal operation",
                     "the review", "the use of the degree", "the research is incomplete", "the system does what was requested"):
            with self.subTest(then=then):
                self.assertFalse(_has_effect(then))


class TestEffectsOfTheMeetings(unittest.TestCase):
    def has_effect(self, then: str) -> bool:
        return bool(OBSERVABLE_RE.search(normalize(then)))

    def test_making_something_available_is_an_effect(self):
        self.assertTrue(self.has_effect("the videos are made available to the reviewer"))
        self.assertTrue(self.has_effect("o sistema disponibiliza os vídeos ao revisor"))
        self.assertTrue(self.has_effect("os vídeos ficam disponíveis ao revisor"))

    def test_working_correctly_still_is_not(self):
        self.assertFalse(self.has_effect("all pages and functions work correctly in each of the three browsers"))


class TestIdsOfTheSameProposal(Flow):
    def ids(self, requirements, saved=0):
        ctx = self.context("data", validation=False)
        for i in range(saved):
            ctx.db.save_requirement({"title": f"t{i}", "statement": "s", "topics": ["general"]}, None)
        return ctx, host.proposal_ids(ctx.db, requirements)

    def test_ids_come_after_the_repository(self):
        _, ids = self.ids([{"statement": "a"}, {"statement": "b"}], saved=2)
        self.assertEqual(ids, {"R3": "P1", "R4": "P2"})

    def test_a_new_version_takes_no_new_id(self):
        _, ids = self.ids([{"statement": "a", "base": "R1"}, {"statement": "b"}], saved=1)
        self.assertEqual(ids, {"R2": "P2"})

    def test_a_base_that_does_not_exist_is_a_new_requirement(self):
        _, ids = self.ids([{"statement": "a", "base": "R9"}], saved=1)
        self.assertEqual(ids, {"R2": "P1"})

    def test_only_links_to_those_ids_change(self):
        requirement = {"statement": "a", "links": [{"kind": "depends_on", "target": "R1"}, {"kind": "refines", "target": "R9"}, "x"]}
        changed = host.with_run_links(requirement, {"R1": "P1"})
        self.assertEqual([l["target"] for l in changed["links"][:2]], ["P1", "R9"])
        self.assertEqual(requirement["links"][0]["target"], "R1")

    def proposal_with_link(self, target: str):
        ctx = self.context("data")
        ctx.conversation_id = ctx.db.start_conversation("after_meeting")
        requirements = [{"title": "Register the vaccine date", "statement": "The system shall register the vaccine date."},
                        {"title": "Send the reminder", "statement": "The system shall send the reminder.",
                         "links": [{"kind": "depends_on", "target": target}]}]
        candidates, sources = ctx.critic._proposal_artifacts(requirements)
        report = ctx.critic.engine.validate(host.Envelope(artifacts=candidates, sources=sources), focus={c.id for c in candidates}, document_level=False)
        return [f for f in report.all_findings() if f.check_id == "grounding.links_resolvable"]

    def test_the_id_of_the_previous_requirement_is_a_link_inside_the_run(self):
        self.assertEqual([f.severity for f in self.proposal_with_link("R1")], [Severity.INFO])

    def test_its_own_future_id_is_a_link_to_itself(self):
        self.assertEqual([f.severity for f in self.proposal_with_link("R2")], [Severity.MAJOR])

    def test_a_made_up_id_is_still_a_defect(self):
        found = self.proposal_with_link("R-new-1")
        self.assertEqual([f.severity for f in found], [Severity.MAJOR])
        self.assertIn("does not exist", found[0].message)


if __name__ == "__main__":
    unittest.main()
