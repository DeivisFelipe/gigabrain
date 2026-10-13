"""Core tests: approval, databases, knowledge files, requirement format and the PO conversation.

Run all tests (offline, simulated provider):

    python -m unittest discover tests
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from gigabrain import knowledge, requirement_format
from gigabrain.agents import Expert
from gigabrain.approval import is_approval
from gigabrain.council import Council, feed_expert, open_context
from gigabrain.database import Database
from gigabrain.providers import SimulatedProvider


def requirement(title: str, topics=("payments",)) -> dict:
    return {"title": title, "statement": f"The system shall {title}.", "kind": "business", "topics": list(topics)}


class TestApproval(unittest.TestCase):
    def test_accepts_explicit_approvals(self):
        for text in ["approved", "Approved!", "looks good", "/approve", "aprovado", "tá bom assim!", "  APROVO  "]:
            self.assertTrue(is_approval(text), text)

    def test_rejects_approval_with_reservations(self):
        for text in ["Approved, but change criterion 2", "not approved", "approved except the deadline",
                     "aprovado, mas troca o prazo", "ok", ""]:
            self.assertFalse(is_approval(text), text)


class TestDatabase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(self.tmp.name)
        self.addCleanup(self.db.close)

    def test_three_separate_files(self):
        for file in ("requirements.db", "experts.db", "log.db"):
            self.assertTrue(os.path.exists(os.path.join(self.tmp.name, file)), file)

    def test_versions_and_links(self):
        r1 = self.db.save_requirement(requirement("notify payments"), None)
        self.assertEqual((r1["requirement_id"], r1["version"]), ("R1", 1))

        r2 = self.db.save_requirement(requirement("show a report"), None, links=[{"kind": "depends_on", "target": "R1"}])
        self.assertEqual(r2["links"], [{"source": "R2", "kind": "depends_on", "target": "R1"}])

        # A new version of R1 puts R2 (which depends on it) under review.
        v2 = self.db.save_requirement(requirement("notify payments and declines"), None, base_id="R1", reason="include declines")
        self.assertEqual(v2["version"], 2)
        self.assertEqual(v2["under_review"], ["R2"])
        self.assertEqual(self.db.get_requirement("R2")["status"], "under_review")

        # Superseding R1 changes its status.
        self.db.save_requirement(requirement("notify in the app"), None, links=[{"kind": "supersedes", "target": "R1"}])
        self.assertEqual(self.db.get_requirement("R1")["status"], "superseded")

        trace = self.db.trace("R3")
        self.assertEqual([(s["requirement_id"], s["event"]) for s in trace],
                         [("R1", "version"), ("R1", "version"), ("R3", "version"), ("R3", "link")])

    def test_ignores_link_to_missing_requirement(self):
        r = self.db.save_requirement(requirement("x"), None, links=[{"kind": "refines", "target": "R99"}])
        self.assertEqual(r["links"], [])

    def test_knowledge_does_not_repeat_identical_version(self):
        self.db.create_expert("payments", "payments", "d", "file.md")
        self.assertEqual(self.db.record_knowledge("payments", "a", "creation", {}), 1)
        self.assertIsNone(self.db.record_knowledge("payments", "a", "again", {}))
        self.assertEqual(self.db.record_knowledge("payments", "b", "changed", {}), 2)


class TestKnowledge(unittest.TestCase):
    def test_add_item_removes_empty_marker_and_does_not_repeat(self):
        md = knowledge.initial_template("payments", "d")
        md = knowledge.add_item(md, "Business rules", "Instant payments are confirmed right away")
        md = knowledge.add_item(md, "Business rules", "Instant payments are confirmed right away")
        self.assertEqual(knowledge.items(md, "Business rules"), ["Instant payments are confirmed right away"])
        self.assertEqual(knowledge.items(md, "Glossary"), [])


class TestRequirementFormat(unittest.TestCase):
    def test_ears_patterns(self):
        cases = {
            "The system shall export reports to PDF.": "ubiquitous",
            "When the payment is confirmed, the system shall send an email.": "event",
            "While the user is offline, the app shall queue the orders.": "state",
            "If the card is declined, then the system shall show an error.": "unwanted",
            "Where a printer is available, the system shall print the receipt.": "optional",
            "While logged in, when the session expires, the system shall ask for the password.": "complex",
            "Quando o pagamento for confirmado, o sistema deve enviar um e-mail.": "event",
        }
        for sentence, pattern in cases.items():
            self.assertEqual(requirement_format.detect_ears(sentence), pattern, sentence)
        self.assertIsNone(requirement_format.detect_ears("Reports in PDF would be nice."))

    def test_vague_terms_only_without_numbers(self):
        self.assertEqual(requirement_format.vague_terms("The page shall load fast."), ["fast"])
        self.assertEqual(requirement_format.vague_terms("The page shall load fast, within 2 seconds."), [])

    def test_check_and_normalize(self):
        req = requirement_format.normalize({
            "statement": "When the payment is confirmed, the system shall send an email within 1 minute.",
            "story": {"role": "customer", "goal": "be told my payment went through", "benefit": "I know the order is done"},
            "acceptance_criteria": [{"scenario": "Confirmed", "given": "a pending order", "when": "the payment is confirmed",
                                     "then": "an email arrives within 1 minute"}],
            "class": "fr", "subtype": "PE",
        })
        self.assertEqual((req["class"], req["subtype"], req["ears_pattern"]), ("FR", None, "event"))
        quality = requirement_format.check(req)
        self.assertTrue(quality["ears"] and quality["story_complete"] and quality["criteria_gherkin"])
        self.assertEqual(quality["vague_terms"], [])
        self.assertFalse(requirement_format.check({"statement": "x", "acceptance_criteria": [{"given": "a"}]})["criteria_gherkin"])


class TestConversation(unittest.TestCase):
    def setUp(self):
        # addCleanup runs in reverse order: the database closes before the folder is deleted.
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def council(self, mode="after_meeting"):
        ctx = open_context(self.tmp.name, SimulatedProvider(), mode=mode)
        council = Council(ctx)
        council.start()
        self.addCleanup(ctx.db.close)
        return council

    def test_full_conversation(self):
        c = self.council()
        r = c.talk("I want to notify the customer when the payment is confirmed")
        self.assertEqual(r["action"], "ask")  # new experts send questions back to the PO
        r = c.talk("The customer gets an email within 1 minute")
        self.assertEqual(r["action"], "propose")
        proposed = r["requirements"][0]
        self.assertTrue(proposed["statement"].startswith("The system shall"))
        self.assertTrue(proposed["story"] and proposed["acceptance_criteria"])

        # An approval with reservations saves nothing.
        self.assertNotEqual(c.talk("approved, but change the deadline")["action"], "approved")
        self.assertEqual(c.talk("approved")["action"], "approved")

        db = c.ctx.db
        req = db.get_requirement("R1")
        self.assertEqual(set(req["topics"]), {"payments", "notifications"})
        self.assertIn({"kind": "conversation", "ref": c.ctx.conversation_id}, req["sources"])
        self.assertTrue(any(s["kind"] == "expert" for s in req["sources"]))

        # Both experts were created and already recorded R1.
        for id_ in ("payments", "notifications"):
            md = knowledge.read(db.get_expert(id_)["file"])
            self.assertIn("R1", "\n".join(knowledge.items(md, "Approved requirements")))

        # Everything went to the log, in the database and in the JSONL file.
        kinds = [e["kind"] for e in db.list_events(c.ctx.conversation_id)]
        for kind in ("po_request", "query", "decomposition", "expert_created", "expert_answer", "consolidated_answer",
                     "proposal", "approval", "requirement_saved", "knowledge_updated", "confirmation"):
            self.assertIn(kind, kinds)
        with open(os.path.join(self.tmp.name, "logs", f"{c.ctx.conversation_id}.jsonl"), encoding="utf-8") as f:
            lines = [json.loads(l) for l in f]
        self.assertEqual(len(lines), len(kinds))
        self.assertEqual(db.list_conversations()[0]["status"], "approved")

    def test_in_meeting_mode_does_not_create_experts(self):
        c = self.council(mode="in_meeting")
        c.talk("The customer wants a discount coupon in the cart")
        self.assertEqual(c.ctx.db.list_experts(), [])
        self.assertIn("pending", [e["kind"] for e in c.ctx.db.list_events(c.ctx.conversation_id)])

    def test_manual_edit_and_document_become_versions(self):
        c = self.council()
        exp = Expert.create(c.ctx, "payments", "Payment rules")
        with open(exp.file, "a", encoding="utf-8") as f:
            f.write("\n- note written by hand\n")
        self.assertEqual(exp.sync(), 2)

        version = feed_expert(c.ctx, "payments", "minutes.md", "- Only the finance team can issue refunds.")
        self.assertEqual(version, 3)
        reasons = [v["reason"] for v in c.ctx.db.knowledge_versions("payments")]
        self.assertEqual(reasons, ["creation", "manual edit", "document minutes.md"])


if __name__ == "__main__":
    unittest.main()
