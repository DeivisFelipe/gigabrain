"""Tests of the adapters between GigaBrain's data and the validation model (validation/host.py).

What they protect: a requirement is read the same way whether an agent has just
written it or the repository stores it, and its sources come from where they
live (the transcript, the log), never from the agent that wrote it.
"""

from __future__ import annotations

import unittest

from gigabrain import meeting
from gigabrain.agents import ValidationSettings
from gigabrain.validation import ValidationEngine, host
from gigabrain.validation.model import ArtifactKind, Envelope, RequirementType, Verdict

from .flows import BACKUP, DETAIL, LOGIN, MEETING, REQUEST, Flow, events, project

REQUIREMENT = {
    "title": "Confirm the payment by email",
    "statement": "When the payment is confirmed, the system shall send an email to the customer within 1 minute.",
    "ears_pattern": "event",
    "story": {"role": "customer", "goal": "to be told my payment went through", "benefit": "I know the order is done"},
    "acceptance_criteria": [
        {"scenario": "Confirmed", "given": "an order waiting for payment", "when": "the payment is confirmed",
         "then": "the system sends an email to the customer within 1 minute"},
        {"scenario": "Declined", "given": "an order waiting for payment", "when": "the payment is declined",
         "then": "the system sends no confirmation email"},
    ],
    "class": "NFR", "subtype": "PE", "kind": "business", "topics": ["payments"],
}


class TestArtifactFromRequirement(unittest.TestCase):
    def test_reads_the_structure_the_agents_emit(self):
        a = host.artifact_from_requirement(REQUIREMENT, "P1")
        self.assertEqual((a.id, a.kind, a.statement, a.ears_pattern), ("P1", ArtifactKind.REQUIREMENT, REQUIREMENT["statement"], "event"))
        self.assertEqual((a.story_parts.role, a.story_parts.benefit), ("customer", "I know the order is done"))
        self.assertEqual([c.scenario for c in a.criteria], ["Confirmed", "Declined"])
        self.assertEqual(len(a.acceptance_criteria), 2)  # the same criteria as text, for the checks that read text
        self.assertEqual((a.req_type, a.nfr_subtype), (RequirementType.NON_FUNCTIONAL, "performance"))  # PE is a code
        self.assertEqual(a.topics, ["payments"])

    def test_a_draft_of_a_meeting_has_one_topic_and_a_reason(self):
        draft = {**REQUIREMENT, "kind": None, "topic": "payments", "topics": None, "reason": "said in the meeting"}
        a = host.artifact_from_requirement(draft, "D1")
        self.assertEqual((a.kind, a.topics, a.rationale), (ArtifactKind.REQUIREMENT, ["payments"], "said in the meeting"))

    def test_an_enabler_keeps_its_kind(self):
        a = host.artifact_from_requirement({**REQUIREMENT, "kind": "enabler"}, "P1")
        self.assertIs(a.kind, ArtifactKind.TECHNICAL_ENABLER)

    def test_links_and_base_come_inside_the_content_or_beside_it(self):
        inside = host.artifact_from_requirement(
            {**REQUIREMENT, "base": "R3", "links": [{"kind": "depends_on", "target": "R2", "reason": "needs it"}]}, "P1")
        beside = host.artifact_from_requirement(REQUIREMENT, "P1", base="R3", links=[{"kind": "depends_on", "target": "R2"}])
        for a in (inside, beside):
            self.assertEqual([(l.kind, l.target) for l in a.links], [("base", "R3"), ("depends_on", "R2")])
            self.assertEqual(a.linked_ids(), {"R2", "R3"})

    def test_what_an_llm_wrote_never_breaks_it(self):
        odd = {"statement": "The system shall export reports.", "kind": ["feature"], "class": "both", "subtype": "ZZ",
               "story": "As a manager I want reports", "acceptance_criteria": ["it exports", {"then": "a file"}],
               "links": "R1", "topics": "reports"}
        a = host.artifact_from_requirement(odd, "P1")
        self.assertEqual((a.kind, a.req_type), (ArtifactKind.REQUIREMENT, RequirementType.UNKNOWN))
        self.assertEqual((a.story, a.story_parts, a.criteria), ("As a manager I want reports", None, []))
        self.assertEqual((len(a.acceptance_criteria), a.links, a.topics), (2, [], ["reports"]))
        self.assertEqual(host.artifact_from_requirement({}, "P2").statement, "")
        ValidationEngine().validate(Envelope(artifacts=[a]))  # and the checks run on it


class TestConversationSources(Flow):
    def test_the_po_words_come_from_the_log(self):
        council = self.council()
        self.propose(council)
        evidence, sources = host.conversation_evidence(council.ctx.db, council.ctx.conversation_id)
        spoken = sources[0]
        self.assertEqual((spoken.id, spoken.kind), (council.ctx.conversation_id, "conversation"))
        self.assertEqual(spoken.text, f"{REQUEST}\n{DETAIL}")
        # The experts the Leader consulted are sources too, read from their knowledge files.
        dialogue = host.dialogue_id(council.ctx.conversation_id)
        self.assertEqual({s.id for s in sources[1:]}, {"expert:payments", "expert:notifications", dialogue})
        # Everything is cited except the dialogue, which only the LLM judge reads.
        self.assertEqual([e.source_id for e in evidence], [s.id for s in sources if s.id != dialogue])

    def test_a_conversation_where_the_po_said_nothing_has_no_source(self):
        council = self.council()
        self.assertIsNone(host.conversation_source(council.ctx.db, council.ctx.conversation_id))
        self.assertEqual(host.conversation_evidence(council.ctx.db, council.ctx.conversation_id), ([], []))

    def test_turns_become_sources_that_name_the_speaker(self):
        sources = host.sources_from_turns(project()["turns"], "meeting test-project")
        self.assertEqual([(s.id, s.kind) for s in sources], [("T1", "turn"), ("T2", "turn"), ("T3", "turn")])
        self.assertEqual((sources[1].text, sources[1].origin), (LOGIN, "meeting test-project, Client"))


class TestRepository(Flow):
    def setUp(self):
        super().setUp()
        self.ctx = self.context()
        self.result = meeting.process(self.ctx, project(), "council")
        self.db = self.ctx.db

    def test_each_requirement_comes_with_the_turn_it_cites(self):
        envelope = host.repository_envelope(self.db)
        self.assertEqual([a.id for a in envelope.artifacts], ["R1", "R2"])
        self.assertEqual([[e.source_id for e in a.evidence][0] for a in envelope.artifacts], ["T2", "T3"])
        # The utterances are read from the log (the Analyst only leads: it is not one of them).
        turns = {s.id: s.text for s in envelope.sources if s.kind == "turn"}
        self.assertEqual(turns, {"T2": LOGIN, "T3": BACKUP})
        self.assertIn("expert:accounts", {s.id for s in envelope.sources})
        self.assertIs(ValidationEngine().validate(envelope).verdict, Verdict.APPROVED_WITH_REMARKS)

    def test_as_context_the_artefacts_come_alone(self):
        envelope = host.repository_envelope(self.db, with_sources=False, exclude={"R1"})
        self.assertEqual(([a.id for a in envelope.artifacts], envelope.sources), (["R2"], []))
        self.assertEqual(envelope.artifacts[0].evidence, [])

    def test_only_the_active_requirements_unless_asked(self):
        self.db.save_requirement({"title": "Login", "statement": "The system shall accept a passkey.", "topics": ["accounts"]},
                                 None, links=[{"kind": "supersedes", "target": "R1"}])
        self.assertEqual([a.id for a in host.repository_envelope(self.db).artifacts], ["R2", "R3"])
        everything = host.repository_envelope(self.db, only_active=False)
        self.assertEqual([a.id for a in everything.artifacts], ["R1", "R2", "R3"])
        self.assertEqual([(l.kind, l.target) for l in everything.artifact("R3").links], [("supersedes", "R1")])

    def test_a_turn_id_two_meetings_reuse_is_qualified(self):
        other = [MEETING[0], ("Client", "The system shall export the monthly sales report to PDF."), MEETING[2]]
        second = meeting.process(self.ctx, project(other, "other-project"), "council")
        envelope = host.repository_envelope(self.db)
        cited = {a.id: [e.source_id for e in a.evidence if "T" in e.source_id.split("@")[0]] for a in envelope.artifacts}
        first_id, second_id = self.result["conversation_id"], second["conversation_id"]
        # T2 says one thing in each meeting; T3 says the same in both, so it needs no qualifier.
        self.assertEqual(cited["R1"], [f"T2@{first_id}"])
        self.assertEqual(cited["R3"], [f"T2@{second_id}"])
        self.assertEqual(cited["R2"], ["T3"])
        texts = {s.id: s.text for s in envelope.sources}
        self.assertEqual(texts[f"T2@{first_id}"], LOGIN)
        self.assertIn("monthly sales report", texts[f"T2@{second_id}"])

    def test_a_cited_turn_the_log_does_not_have_is_reported(self):
        self.db.save_requirement({"title": "Export", "statement": "The system shall export reports to PDF.", "topics": ["reports"]},
                                 self.result["conversation_id"], sources=[{"kind": "turn", "ref": "T99"}])
        report = ValidationEngine().validate(host.repository_envelope(self.db), focus={"R3"})
        missing = [f for f in report.all_findings() if f.check_id == "grounding.evidence_resolvable"]
        self.assertEqual([(f.target_id, "T99" in f.message) for f in missing], [("R3", True)])

    def test_traceability_reads_the_links(self):
        self.db.save_requirement({"title": "Passkey", "statement": "The system shall accept a passkey.", "topics": ["accounts"]},
                                 None, links=[{"kind": "refines", "target": "R1"}, {"kind": "depends_on", "target": "R2"}])
        trace = host.RepositoryTraceability(self.db)
        self.assertTrue(trace.knows("R1"))
        self.assertFalse(trace.knows("R99"))
        self.assertEqual(trace.predecessors("R3"), ["R1"])  # evolution links only
        self.assertEqual(trace.impacted_by("R2"), ["R3"])   # who depends on it
        self.assertEqual((trace.predecessors("R1"), trace.impacted_by("R1")), ([], []))


class TestValidationOf(Flow):
    def test_scores_of_a_saved_requirement_computed_on_demand(self):
        ctx = self.context()
        result = meeting.process(ctx, project(), "council")
        check = host.validation_of(ctx.db, "R1")
        self.assertEqual(set(check["scores"]), {"conforming", "unambiguous", "verifiable", "correctness", "understandable"})
        self.assertEqual(set(check["document"]), {"internal_consistency", "non_redundancy", "completeness", "conciseness", "traceability"})
        self.assertTrue(check["passed"])
        self.assertEqual({f["target"] for f in check["findings"]}, {"R1"})
        # The report it was saved with is a message of the Critic in the log.
        self.assertEqual(check["saved_with"]["report_id"], result["predicted"][0]["validation"]["report_id"])
        self.assertIsNone(host.validation_of(ctx.db, "R99"))

    def test_it_does_not_need_the_critic_to_have_been_on(self):
        ctx = self.context(validation=False)
        meeting.process(ctx, project(), "council")
        check = host.validation_of(ctx.db, "R1")
        self.assertTrue(check["passed"])
        self.assertIsNone(check["saved_with"])
        self.assertEqual(events(ctx, None, "validation_report"), [])  # computing it writes nothing


class TestEventReportStore(Flow):
    def test_a_report_is_a_message_of_the_critic(self):
        ctx = self.context()
        result = meeting.process(ctx, project(), "council")
        store = host.EventReportStore(ctx.db)
        report_id = result["predicted"][0]["validation"]["report_id"]
        content = store.find(report_id)
        self.assertEqual((content["stage"], content["artefacts"], content["verdict"]), ("consolidated", ["D1"], "approved_with_remarks"))
        self.assertIsInstance(content["checks_run"], int)
        self.assertIn(content, store.runs_of(content["envelope_hash"]))  # every report of the same input
        # The compact form is what is stored by default: enough to reproduce the run, not the whole report.
        self.assertNotIn("report", content)
        self.assertIsNone(store.load(report_id))
        self.assertIsNone(store.find("run-missing"))

    def test_full_reports_can_be_loaded_back(self):
        ctx = self.context(validation=ValidationSettings(full_reports=True))
        result = meeting.process(ctx, project(), "council")
        store = host.EventReportStore(ctx.db)
        report = store.load(result["validation"]["report_id"])
        self.assertEqual((report.run_id, report.verdict.value), (result["validation"]["report_id"], result["validation"]["verdict"]))
        self.assertEqual([i.artifact_id for i in report.item_results], ["R1", "R2"])
        self.assertEqual([r.run_id for r in store.history(report.envelope_hash)], [report.run_id])

    def test_saving_through_the_store_goes_to_the_log(self):
        ctx = self.context()
        ctx.conversation_id = ctx.db.start_conversation("after_meeting")
        report = ValidationEngine().validate(Envelope(artifacts=[host.artifact_from_requirement(REQUIREMENT, "P1")]))
        with self.assertRaises(RuntimeError):
            host.EventReportStore(ctx.db).save(report)  # reading needs no recorder, saving does
        store = host.EventReportStore(ctx.db, ctx.recorder, ctx.conversation_id)
        self.assertEqual(store.save(report, summary="checked by hand"), report.run_id)
        saved = events(ctx, None, "validation_report")[0]
        self.assertEqual((saved["sender"], saved["recipient"], saved["content"]["stage"]), ("critic", "repository", "stored"))
        self.assertEqual(store.load(report.run_id).verdict, report.verdict)


if __name__ == "__main__":
    unittest.main()
