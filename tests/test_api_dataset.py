"""Automated test of the whole flow through the REST API, with meetings of the test dataset.

The PO's messages are real turns of the dataset (EntradasGigabrain). By default the API runs in the
test process with the simulated provider (no network, no key). To test an API that is already running:

    GIGABRAIN_API_URL=http://127.0.0.1:8000/api/v1 [GIGABRAIN_API_TOKEN=...] [GIGABRAIN_LLM_KEY=...] \\
        python -m unittest tests.test_api_dataset

Against a running API, the test creates its own projects (names start with "e2e-") and leaves them there.
"""

from __future__ import annotations

import os
import tempfile
import unittest
import uuid

import httpx

from gigabrain import dataset
from gigabrain.api import create_app
from gigabrain.providers import SimulatedProvider

LIVE_URL = os.environ.get("GIGABRAIN_API_URL")
VARIANTS = ("v1-original", "rules-hard")
PROJECT = "2008-keepass"
MAX_TURNS = 8  # turns of the meeting the PO says
NUDGES = 4  # then the PO says it is enough, up to this many times, before giving up on a proposal
NUDGE = "That covers it. Please write the requirements now, with what you already know."


def _dataset_available() -> bool:
    try:
        dataset.load_project(PROJECT, variant=VARIANTS[0])
        return True
    except (OSError, ValueError, KeyError):
        return False


def _po_messages(variant: str) -> list[str]:
    """Turns of the first meeting that a PO could say: no context turns, no empty ones."""
    project = dataset.load_project(PROJECT, variant=variant)
    turns = project["meetings"][0]["turns"] if project.get("meetings") else project["turns"]
    return [t["text"][:1500] for t in turns if t["text"] and not t["context"]][:MAX_TURNS]


@unittest.skipUnless(_dataset_available(), "dataset EntradasGigabrain not found (set GIGABRAIN_DATASET)")
class TestApiWithDataset(unittest.TestCase):
    def setUp(self):
        if LIVE_URL:
            headers = {}
            if os.environ.get("GIGABRAIN_API_TOKEN"):
                headers["Authorization"] = f"Bearer {os.environ['GIGABRAIN_API_TOKEN']}"
            if os.environ.get("GIGABRAIN_LLM_KEY"):
                headers["X-LLM-Key"] = os.environ["GIGABRAIN_LLM_KEY"]
            self.client = httpx.Client(base_url=LIVE_URL.rstrip("/"), headers=headers, timeout=300)
        else:
            from fastapi.testclient import TestClient
            tmp = tempfile.TemporaryDirectory()
            self.addCleanup(tmp.cleanup)
            self.client = TestClient(create_app(tmp.name, SimulatedProvider), base_url="http://test/api/v1")
            self.client.__enter__()
        self.addCleanup(self.client.__exit__ if not LIVE_URL else self.client.close, *(() if LIVE_URL else (None, None, None)))

    def new_project(self, label: str) -> str:
        r = self.client.post("/projects", json={"name": f"e2e-{label}-{uuid.uuid4().hex[:6]}"})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["id"]

    def talk_until_proposal(self, project: str, messages: list[str]) -> tuple[str, dict]:
        """Opens a conversation and sends the PO's messages until the Twin proposes."""
        r = self.client.post(f"/projects/{project}/conversations", json={"mode": "after_meeting"})
        self.assertEqual(r.status_code, 201, r.text)
        cid = r.json()["id"]
        for text in [*messages, *[NUDGE] * NUDGES]:
            r = self.client.post(f"/projects/{project}/conversations/{cid}/messages", json={"text": text})
            self.assertEqual(r.status_code, 200, r.text)
            answer = r.json()
            self.assertIn(answer["action"], ("ask", "propose", "approved"))
            self.assertTrue(answer["message"])
            if answer["action"] == "propose":
                return cid, answer
        self.fail(f"the Twin never proposed in {len(messages) + NUDGES} messages")

    def test_dataset_meeting_becomes_approved_requirements(self):
        for variant in VARIANTS:
            with self.subTest(variant=variant):
                project = self.new_project(variant)
                cid, proposal = self.talk_until_proposal(project, _po_messages(variant))

                self.assertTrue(proposal["pending_proposal"])
                self.assertTrue(proposal["proposed_requirements"])
                for req in proposal["proposed_requirements"]:
                    self.assertTrue(req["statement"])
                    self.assertTrue(req["acceptance_criteria"])
                    self.assertTrue(req["quality"]["ears"])
                self.assertIsNotNone(proposal["validation"])  # the Critic looked at it
                self.assertIn("verdict", proposal["validation"])

                approved = self.client.post(f"/projects/{project}/conversations/{cid}/approve").json()
                self.assertEqual(approved["action"], "approved")
                self.assertTrue(approved["saved"])
                self.assertFalse(approved["conversation_active"])

                # Repository: the saved requirements, each with versions, trace and validation.
                listed = self.client.get(f"/projects/{project}/requirements").json()
                self.assertEqual(sorted(r["id"] for r in listed), sorted(approved["saved"]))
                detail = self.client.get(f"/projects/{project}/requirements/{approved['saved'][0]}").json()
                self.assertEqual(len(detail["versions"]), 1)
                self.assertTrue(detail["trace"])
                self.assertIn("validation", detail)

                # History and behind the scenes.
                history = self.client.get(f"/projects/{project}/conversations/{cid}").json()
                self.assertEqual(history["status"], "approved")
                self.assertEqual(history["saved_requirements"], approved["saved"])
                events = self.client.get(f"/projects/{project}/conversations/{cid}/events").json()
                kinds = {e["kind"] for e in events}
                self.assertIn("requirement_saved", kinds)
                if "query" in kinds:  # a real model does not always consult the experts
                    self.assertTrue(self.client.get(f"/projects/{project}/experts").json())

                # A finished conversation takes no more messages or approvals.
                self.assertEqual(self.client.post(f"/projects/{project}/conversations/{cid}/messages", json={"text": "hi"}).status_code, 409)
                self.assertEqual(self.client.post(f"/projects/{project}/conversations/{cid}/approve").status_code, 409)

    def test_projects_stay_apart(self):
        a, b = self.new_project("a"), self.new_project("b")
        cid, _ = self.talk_until_proposal(a, _po_messages(VARIANTS[0]))
        self.client.post(f"/projects/{a}/conversations/{cid}/approve")

        self.assertTrue(self.client.get(f"/projects/{a}/requirements").json())
        for what in ("requirements", "conversations", "experts", "links"):
            self.assertEqual(self.client.get(f"/projects/{b}/{what}").json(), [], what)
        self.assertEqual(self.client.post(f"/projects/{b}/conversations/{cid}/messages", json={"text": "hi"}).status_code, 409)

    def test_two_meetings_in_the_same_project_share_the_repository(self):
        project = self.new_project("two")
        messages = _po_messages(VARIANTS[0])
        saved = []
        for _ in range(2):
            cid, _ = self.talk_until_proposal(project, messages)
            saved += self.client.post(f"/projects/{project}/conversations/{cid}/approve").json()["saved"]
        listed = {r["id"] for r in self.client.get(f"/projects/{project}/requirements").json()}
        self.assertTrue(set(saved) <= listed)
        self.assertEqual(len(self.client.get(f"/projects/{project}/conversations").json()), 2)


if __name__ == "__main__":
    unittest.main()
