"""Drafts of a conversation: meetings sent by the PO, changing drafts, approving one by one,
attached files and conversations that go on after an approval (simulated provider, no network)."""

from __future__ import annotations

import io
import tempfile
import unittest
import zipfile

from fastapi.testclient import TestClient

from gigabrain import documents, transcript
from gigabrain.api import create_app
from gigabrain.providers import SimulatedProvider

MEETING = """\
Analyst: Okay, next topic: login. How should that work?
Client: Users must log in with their email and a password of at least 8 characters.
Analyst: So, let's talk about reports. What do you need there?
Client: The system shall export monthly sales reports to PDF for the finance team.
"""
# The next part: changes the password rule (another number), repeats the reports, adds backups.
CONTINUATION = """\
Analyst: Back to login for a moment.
Client: Users must log in with their email and a password of at least 12 characters.
Client: The system shall export monthly sales reports to PDF for the finance team.
Analyst: So, let's talk about backup. What do you need there?
TechLead: The database must be backed up every 24 hours to an external server.
"""
BACKUP = """Analyst: So, let's talk about backup. What do you need there?
TechLead: The database must be backed up every 24 hours to an external server.
"""


def docx(text: str) -> bytes:
    """A minimal .docx with one paragraph per line."""
    body = "".join(f"<w:p><w:r><w:t>{line}</w:t></w:r></w:p>" for line in text.splitlines())
    xml = ('<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
           f'wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("word/document.xml", xml)
    return buffer.getvalue()


class TestTranscript(unittest.TestCase):
    def test_speakers_times_and_continuation_lines(self):
        turns = transcript.parse("[00:01:02] Ana Souza: The system shall export PDF\nfiles every month.\n00:03 - PO: ok", start=7)
        self.assertEqual([(t["id"], t["speaker"]) for t in turns], [("T7", "Ana Souza"), ("T8", "PO")])
        self.assertEqual(turns[0]["text"], "The system shall export PDF files every month.")

    def test_no_speakers_means_one_utterance_per_paragraph(self):
        turns = transcript.parse("First paragraph\nstill the first.\n\nSecond one.")
        self.assertEqual([t["text"] for t in turns], ["First paragraph still the first.", "Second one."])

    def test_the_analyst_only_leads(self):
        self.assertTrue(transcript.leads_the_meeting("Analista"))
        self.assertFalse(transcript.leads_the_meeting("Client"))


class TestDocuments(unittest.TestCase):
    def test_reads_text_and_docx(self):
        self.assertEqual(documents.text_of("notes.md", "# Rules\n- refunds".encode()), "# Rules\n- refunds")
        self.assertEqual(documents.text_of("minutes.docx", docx("Line one\nLine two")), "Line one\nLine two")

    def test_refuses_what_it_cannot_read(self):
        with self.assertRaises(documents.DocumentError):
            documents.text_of("image.png", b"\x89PNG")
        with self.assertRaises(documents.DocumentError):
            documents.text_of("broken.docx", b"not a zip")


class ApiCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.client = self.app()
        self.client.post("/api/v1/projects", json={"name": "p"})

    def app(self) -> TestClient:
        client = TestClient(create_app(self.tmp.name, SimulatedProvider))
        client.__enter__()
        self.addCleanup(client.__exit__, None, None, None)
        return client

    def url(self, cid: str, path: str = "") -> str:
        return f"/api/v1/projects/p/conversations/{cid}{path}"

    def open(self, **body) -> str:
        return self.client.post("/api/v1/projects/p/conversations", json=body or None).json()["id"]


class TestMeetingInTheChat(ApiCase):
    def test_meeting_becomes_drafts_and_nothing_is_saved(self):
        cid = self.open()
        r = self.client.post(self.url(cid, "/meeting"), json={"text": MEETING, "title": "Kick-off"}).json()
        self.assertEqual(r["action"], "propose")
        self.assertEqual([d["draft_id"] for d in r["proposed_requirements"]], ["D1", "D2"])
        self.assertTrue(all(d["origin"] == "meeting" and d["status"] == "pending" for d in r["drafts"]))
        first = r["proposed_requirements"][0]
        self.assertEqual(first["turns"], ["T2"])
        self.assertIn({"kind": "turn", "ref": "T2", "meeting": 1}, first["sources"])
        self.assertTrue(any(s["kind"] == "expert" for s in first["sources"]))  # the expert of the topic reviewed it
        self.assertIn("I read 4 utterances: 2 new drafts", r["message"])
        self.assertEqual(self.client.get("/api/v1/projects/p/requirements").json(), [])

    def test_the_next_part_changes_a_draft_skips_a_repetition_and_adds_new_ones(self):
        cid = self.open()
        self.client.post(self.url(cid, "/meeting"), json={"text": MEETING})
        r = self.client.post(self.url(cid, "/meeting"), json={"text": CONTINUATION}).json()
        drafts = {d["draft_id"]: d for d in r["drafts"]}
        self.assertEqual(sorted(drafts), ["D1", "D2", "D3"])
        self.assertIn("12 characters", drafts["D1"]["statement"])  # the correction changed D1
        self.assertEqual({s["ref"] for s in drafts["D1"]["sources"] if s["kind"] == "turn"}, {"T2", "T6"})
        self.assertIn("backed up every 24 hours", drafts["D3"]["statement"])
        self.assertEqual(drafts["D3"]["turns"], ["T9"])  # ids go on from the first part
        self.assertIn("1 changed", r["message"])

    def test_approve_one_reject_one_restore_and_approve_the_rest(self):
        cid = self.open()
        self.client.post(self.url(cid, "/meeting"), json={"text": MEETING + BACKUP})
        r = self.client.post(self.url(cid, "/drafts/D2/approve")).json()
        self.assertEqual((r["action"], r["saved"]), ("approved", ["R1"]))
        self.assertEqual({d["draft_id"]: d["status"] for d in r["drafts"]}, {"D1": "pending", "D2": "approved", "D3": "pending"})

        r = self.client.delete(self.url(cid, "/drafts/D1")).json()
        self.assertEqual([d["draft_id"] for d in r["proposed_requirements"]], ["D3"])
        self.assertEqual(self.client.delete(self.url(cid, "/drafts/D1")).status_code, 409)  # already rejected
        self.assertEqual(self.client.delete(self.url(cid, "/drafts/D9")).status_code, 404)
        r = self.client.post(self.url(cid, "/drafts/D1/restore")).json()
        self.assertEqual([d["draft_id"] for d in r["proposed_requirements"]], ["D1", "D3"])

        r = self.client.post(self.url(cid, "/approve"), json={"draft_ids": ["D3"]}).json()
        self.assertEqual(r["saved"], ["R2"])
        r = self.client.post(self.url(cid, "/approve")).json()  # all that is left
        self.assertEqual((r["saved"], r["pending_proposal"]), (["R3"], False))
        self.assertEqual(self.client.post(self.url(cid, "/approve")).status_code, 409)

        saved = {x["id"]: x for x in self.client.get("/api/v1/projects/p/requirements").json()}
        self.assertEqual(sorted(saved), ["R1", "R2", "R3"])
        self.assertTrue(saved["R1"]["experts"])
        self.assertIn({"kind": "turn", "ref": "T4", "meeting": 1}, saved["R1"]["sources"])
        # The expert of the topic wrote the approved requirement down.
        experts = {e["id"]: e for e in self.client.get("/api/v1/projects/p/experts").json()}
        self.assertGreater(experts["reports"]["consultations"], 0)
        knowledge = self.client.get("/api/v1/projects/p/experts/reports").json()["knowledge"]
        self.assertIn("R1", knowledge)
        detail = self.client.get(self.url(cid)).json()
        self.assertGreater(detail["expert_consultations"]["reports"], 0)
        self.assertEqual(detail["messages"][0]["kind"], "meeting")


class TestTalkingAboutDrafts(ApiCase):
    def test_the_po_changes_and_rejects_drafts_by_talking(self):
        cid = self.open()
        self.client.post(self.url(cid, "/meeting"), json={"text": MEETING})
        r = self.client.post(self.url(cid, "/messages"), json={"text": "In D2 the report also goes to the board"}).json()
        drafts = {d["draft_id"]: d for d in r["drafts"]}
        self.assertIn("the report also goes to the board", drafts["D2"]["statement"])
        # The Twin only sees a summary of the draft: what it did not change stays.
        self.assertTrue(drafts["D2"]["story"] and drafts["D2"]["acceptance_criteria"])
        self.assertEqual(drafts["D2"]["turns"], ["T4"])
        self.assertEqual(len(drafts), 2)  # changed, not added

        r = self.client.post(self.url(cid, "/messages"), json={"text": "Reject D1, we use single sign-on"}).json()
        self.assertEqual({d["draft_id"]: d["status"] for d in r["drafts"]}, {"D1": "rejected", "D2": "pending"})

        r = self.client.post(self.url(cid, "/messages"), json={"text": "approved"}).json()
        self.assertEqual((r["action"], r["saved"]), ("approved", ["R1"]))
        # Then more of the meeting in the same conversation.
        r = self.client.post(self.url(cid, "/meeting"), json={"text": CONTINUATION}).json()
        self.assertEqual(r["action"], "propose")
        self.assertTrue(any("backed up" in d["statement"] for d in r["proposed_requirements"]))

    def test_a_conversation_comes_back_after_the_api_restarts(self):
        cid = self.open()
        self.client.post(self.url(cid, "/meeting"), json={"text": MEETING})
        self.client.post(self.url(cid, "/drafts/D1/approve"))

        restarted = self.app()  # a new API over the same folder: nothing in memory
        listed = {c["id"]: c for c in restarted.get("/api/v1/projects/p/conversations").json()}
        self.assertEqual((listed[cid]["active"], listed[cid]["in_memory"]), (True, False))
        self.assertEqual([d["status"] for d in restarted.get(self.url(cid)).json()["drafts"]], ["approved", "pending"])
        r = restarted.post(self.url(cid, "/drafts/D2/approve")).json()
        self.assertEqual(r["saved"], ["R2"])
        r = restarted.post(self.url(cid, "/meeting"), json={"text": CONTINUATION}).json()
        self.assertEqual([d["turns"] for d in r["proposed_requirements"]][-1], ["T9"])  # it remembers the utterances

        restarted.post(self.url(cid, "/close"))
        self.assertEqual(restarted.post(self.url(cid, "/messages"), json={"text": "hi"}).status_code, 409)


class TestAttachedFiles(ApiCase):
    def test_files_go_to_the_twin_with_the_message(self):
        cid = self.open()
        files = [("files", ("rules.md", b"Refunds only by the finance team.", "text/markdown")),
                 ("files", ("minutes.docx", docx("Invoices are emailed within 1 hour."),
                            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))]
        r = self.client.post(self.url(cid, "/messages"), data={"text": "Use these documents"}, files=files)
        self.assertEqual(r.status_code, 200)
        sent = [m for m in self.client.get(self.url(cid)).json()["messages"] if m["kind"] == "po_request"][0]["text"]
        self.assertIn("--- Attached document: rules.md ---\nRefunds only by the finance team.", sent)
        self.assertIn("Invoices are emailed within 1 hour.", sent)

    def test_files_that_cannot_be_read(self):
        cid = self.open()
        r = self.client.post(self.url(cid, "/messages"), data={"text": "x"}, files=[("files", ("a.png", b"\x89PNG", "image/png"))])
        self.assertEqual(r.status_code, 422)
        many = [("files", (f"{i}.txt", b"text", "text/plain")) for i in range(documents.MAX_FILES + 1)]
        self.assertEqual(self.client.post(self.url(cid, "/messages"), data={"text": "x"}, files=many).status_code, 422)


class TestConversationHousekeeping(ApiCase):
    def test_rename(self):
        cid = self.open(title="Kick-off")
        self.assertEqual(self.client.get(self.url(cid)).json()["title"], "Kick-off")
        self.assertEqual(self.client.patch(self.url(cid), json={"title": "  Login meeting "}).json()["title"], "Login meeting")
        self.assertIsNone(self.client.patch(self.url(cid), json={"title": ""}).json()["title"])
        self.assertEqual(self.client.patch(self.url(cid), json={"title": "x" * 81}).status_code, 422)
        self.assertEqual(self.client.patch(self.url("missing"), json={"title": "a"}).status_code, 404)

    def test_delete_empty_conversations_but_keep_history(self):
        empty, other_empty, used = self.open(), self.open(), self.open()
        self.client.post(self.url(used, "/meeting"), json={"text": MEETING})
        self.client.post(self.url(used, "/approve"))
        self.assertEqual(self.client.delete(self.url(empty)).status_code, 204)
        self.assertEqual(self.client.delete(self.url(used)).status_code, 409)  # it saved requirements
        self.assertEqual(self.client.delete("/api/v1/projects/p/conversations").json()["deleted"], [other_empty])
        self.assertEqual([c["id"] for c in self.client.get("/api/v1/projects/p/conversations").json()], [used])

    def test_health_tells_the_limits(self):
        limits = self.client.get("/api/v1/health").json()["limits"]
        self.assertEqual((limits["files_per_message"], limits["message_chars"]), (documents.MAX_FILES, 50000))


if __name__ == "__main__":
    unittest.main()
