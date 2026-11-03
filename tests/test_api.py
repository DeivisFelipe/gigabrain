"""Tests of the REST API (simulated provider, no network)."""

from __future__ import annotations

import tempfile
import unittest

from fastapi.testclient import TestClient

from gigabrain.api import create_app
from gigabrain.providers import SimulatedProvider


class TestApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.client = TestClient(create_app(self.tmp.name, SimulatedProvider))
        self.client.__enter__()
        self.client.post("/api/v1/projects", json={"name": "p"})
        self.addCleanup(self.client.__exit__, None, None, None)

    def test_full_conversation_and_history(self):
        c = self.client
        cid = c.post("/api/v1/projects/p/conversations", json={"mode": "after_meeting"}).json()["id"]

        r = c.post(f"/api/v1/projects/p/conversations/{cid}/messages", json={"text": "I want to notify the customer when the payment is confirmed"}).json()
        self.assertEqual(r["action"], "ask")  # new experts send questions back to the PO
        self.assertFalse(r["pending_proposal"])
        kinds = {b["kind"] for b in r["behind_the_scenes"]}
        self.assertTrue({"query", "expert_created", "consolidated_answer"} <= kinds)

        # Without a proposal, the Approve button has nothing to approve.
        self.assertEqual(c.post(f"/api/v1/projects/p/conversations/{cid}/approve").status_code, 409)

        r = c.post(f"/api/v1/projects/p/conversations/{cid}/messages", json={"text": "The customer gets an email within 1 minute"}).json()
        self.assertEqual(r["action"], "propose")
        self.assertTrue(r["pending_proposal"])
        self.assertEqual(len(r["proposed_requirements"]), 1)
        self.assertTrue(r["proposed_requirements"][0]["quality"]["ears"])

        r = c.post(f"/api/v1/projects/p/conversations/{cid}/approve").json()
        self.assertEqual((r["action"], r["saved"], r["conversation_active"]), ("approved", ["R1"], False))
        self.assertIn("requirement_saved", {b["kind"] for b in r["behind_the_scenes"]})

        # After approval, the conversation accepts no more messages.
        self.assertEqual(c.post(f"/api/v1/projects/p/conversations/{cid}/messages", json={"text": "hi"}).status_code, 409)

        # Conversation history, as the PO sees it.
        h = c.get(f"/api/v1/projects/p/conversations/{cid}").json()
        self.assertEqual(h["status"], "approved")
        self.assertEqual([m["author"] for m in h["messages"]], ["po", "twin", "po", "twin", "po", "twin"])
        self.assertEqual(h["messages"][-1]["kind"], "confirmation")
        self.assertEqual(h["saved_requirements"], ["R1"])

        # Requirements repository and behind the scenes.
        self.assertEqual([r["id"] for r in c.get("/api/v1/projects/p/requirements").json()], ["R1"])
        self.assertEqual(c.get("/api/v1/projects/p/requirements", params={"search": "payment"}).json()[0]["id"], "R1")
        detail = c.get("/api/v1/projects/p/requirements/R1").json()
        self.assertEqual(len(detail["versions"]), 1)
        self.assertTrue(detail["trace"])
        self.assertIn("ears", detail["quality"])
        self.assertEqual(c.get("/api/v1/projects/p/requirements/R99").status_code, 404)
        events = c.get(f"/api/v1/projects/p/conversations/{cid}/events", params={"full": True}).json()
        self.assertTrue(all("content" in e for e in events))
        experts = c.get("/api/v1/projects/p/experts").json()
        self.assertTrue(experts)
        self.assertIn("knowledge", c.get(f"/api/v1/projects/p/experts/{experts[0]['id']}").json())

    def test_approve_by_writing_and_close(self):
        c = self.client
        cid = c.post("/api/v1/projects/p/conversations").json()["id"]
        c.post(f"/api/v1/projects/p/conversations/{cid}/messages", json={"text": "Monthly sales report"})
        c.post(f"/api/v1/projects/p/conversations/{cid}/messages", json={"text": "For the sales manager"})
        self.assertEqual(c.post(f"/api/v1/projects/p/conversations/{cid}/messages", json={"text": "approved"}).json()["action"], "approved")

        other = c.post("/api/v1/projects/p/conversations").json()["id"]
        self.assertEqual(c.post(f"/api/v1/projects/p/conversations/{other}/close").json()["status"], "closed")
        conversations = {x["id"]: x for x in c.get("/api/v1/projects/p/conversations").json()}
        self.assertEqual((conversations[other]["status"], conversations[other]["active"]), ("closed", False))

    def test_validation_and_token(self):
        self.assertEqual(self.client.post("/api/v1/projects/p/conversations/x/messages", json={"text": ""}).status_code, 422)
        self.assertEqual(self.client.post("/api/v1/projects/p/conversations/missing/messages", json={"text": "hi"}).status_code, 409)

        protected = TestClient(create_app(self.tmp.name, SimulatedProvider, token="secret"))
        self.assertEqual(protected.get("/api/v1/projects/p/requirements").status_code, 401)
        self.assertEqual(protected.get("/api/v1/projects/p/requirements", headers={"Authorization": "Bearer secret"}).status_code, 200)
        self.assertEqual(protected.get("/api/v1/health").status_code, 200)  # health stays open


class TestProviderKey(unittest.TestCase):
    """The app can send the provider key (X-LLM-Key) when it opens a conversation."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.keys = []

        def factory(key=None):
            self.keys.append(key)
            if key is None:
                raise RuntimeError("KEY is not set")
            return SimulatedProvider()

        self.client = TestClient(create_app(self.tmp.name, factory))
        self.client.__enter__()
        self.client.post("/api/v1/projects", json={"name": "p"})
        self.addCleanup(self.client.__exit__, None, None, None)

    def test_conversation_needs_a_key_from_the_server_or_the_app(self):
        r = self.client.post("/api/v1/projects/p/conversations")
        self.assertEqual(r.status_code, 400)
        self.assertIn("KEY is not set", r.json()["detail"])

        r = self.client.post("/api/v1/projects/p/conversations", headers={"X-LLM-Key": " sk-abc "})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.keys[-1], "sk-abc")

    def test_health_works_without_a_server_key(self):
        r = self.client.get("/api/v1/health").json()
        self.assertEqual((r["status"], r["provider"], r["needs_key"]), ("ok", None, True))


class TestProjects(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.client = TestClient(create_app(self.tmp.name, SimulatedProvider))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def test_create_list_and_rename(self):
        c = self.client
        self.assertEqual(c.get("/api/v1/projects").json(), [])
        a = c.post("/api/v1/projects", json={"name": "Loja Virtual", "description": "e-commerce"})
        self.assertEqual(a.status_code, 201)
        self.assertEqual(a.json()["id"], "loja-virtual")
        self.assertEqual(c.post("/api/v1/projects", json={"name": "loja virtual"}).status_code, 422)  # same name
        self.assertEqual(c.post("/api/v1/projects", json={"name": "   "}).status_code, 422)
        self.assertEqual(c.patch("/api/v1/projects/loja-virtual", json={"name": "Loja"}).json()["name"], "Loja")
        self.assertEqual([p["name"] for p in c.get("/api/v1/projects").json()], ["Loja"])
        self.assertEqual(c.get("/api/v1/projects/nope").status_code, 404)
        self.assertEqual(c.get("/api/v1/projects/nope/requirements").status_code, 404)

    def test_projects_do_not_share_requirements_or_conversations(self):
        c = self.client
        a = c.post("/api/v1/projects", json={"name": "A"}).json()["id"]
        b = c.post("/api/v1/projects", json={"name": "B"}).json()["id"]
        cid = c.post(f"/api/v1/projects/{a}/conversations").json()["id"]
        for text in ("I want to notify the customer when the payment is confirmed", "The customer gets an email within 1 minute"):
            c.post(f"/api/v1/projects/{a}/conversations/{cid}/messages", json={"text": text})
        self.assertEqual(c.post(f"/api/v1/projects/{a}/conversations/{cid}/approve").json()["saved"], ["R1"])

        self.assertEqual(len(c.get(f"/api/v1/projects/{a}/requirements").json()), 1)
        self.assertEqual(c.get(f"/api/v1/projects/{b}/requirements").json(), [])
        self.assertEqual(c.get(f"/api/v1/projects/{b}/conversations").json(), [])
        self.assertEqual(c.get(f"/api/v1/projects/{b}/experts").json(), [])
        self.assertEqual(c.post(f"/api/v1/projects/{b}/conversations/{cid}/messages", json={"text": "hi"}).status_code, 409)


if __name__ == "__main__":
    unittest.main()
