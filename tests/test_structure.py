"""NFRs as constraint cards, epics and the structure report (simulated provider, no network)."""

from __future__ import annotations

import tempfile
import unittest

from fastapi.testclient import TestClient

from gigabrain import requirement_format
from gigabrain.api import create_app
from gigabrain.providers import SimulatedProvider
from gigabrain.validation.engine import ValidationEngine
from gigabrain.validation.host import artifact_from_requirement

from tests.validation.helpers import envelope

MEETING = """\
Analyst: Okay, next topic: login. How should that work?
Client: Users must log in with their email and a password of at least 8 characters.
Analyst: So, let's talk about search. What do you need there?
EndUser: The search page should show results in under 2 seconds for every query.
Analyst: So, let's talk about reports. What do you need there?
Client: The system shall export monthly sales reports to PDF for the finance team.
"""

CONSTRAINT = {"quality": "performance", "scope": "every search", "measure": "results within 2 seconds"}


def findings(content: dict, check_id: str) -> list:
    artifact = artifact_from_requirement(content, "X1")
    report = ValidationEngine().validate_artifact(artifact, envelope(artifact))
    return [f for f in report.findings if f.check_id == check_id]


class TestConstraintCards(unittest.TestCase):
    def test_an_nfr_is_a_constraint_card_and_never_a_story(self):
        req = requirement_format.normalize({
            "statement": "The search shall return results within 2 seconds.", "class": "NFR", "subtype": "PE",
            "story": {"role": "user", "goal": "fast search", "benefit": "I save time"}, "constraint": CONSTRAINT,
        })
        self.assertIsNone(req["story"])
        self.assertEqual(req["constraint"], CONSTRAINT)
        quality = requirement_format.check(req)
        self.assertEqual((quality["card"], quality["card_complete"], quality["story_complete"]), ("constraint", True, None))

    def test_an_fr_keeps_its_story_and_has_no_constraint(self):
        req = requirement_format.normalize({"statement": "The system shall export reports to PDF.", "class": "FR",
                                            "story": {"role": "manager", "goal": "a PDF", "benefit": "I share it"},
                                            "constraint": CONSTRAINT})
        self.assertIsNone(req["constraint"])
        self.assertEqual(requirement_format.check(req)["card"], "story")

    def test_the_critic_asks_an_nfr_for_its_card_not_for_a_story(self):
        nfr = {"title": "Search time", "statement": "The search shall return results within 2 seconds.", "class": "NFR",
               "subtype": "PE", "kind": "business"}
        self.assertEqual(findings(nfr, "structure.story_format"), [])
        self.assertEqual([f.locator for f in findings(nfr, "structure.constraint_card")], ["constraint"])
        self.assertEqual(findings({**nfr, "constraint": CONSTRAINT}, "structure.constraint_card"), [])
        self.assertEqual([f.locator for f in findings({**nfr, "constraint": {**CONSTRAINT, "measure": ""}}, "structure.constraint_card")],
                         ["constraint.measure"])
        fr = {**nfr, "class": "FR", "subtype": None}
        self.assertEqual(findings(fr, "structure.constraint_card"), [])
        self.assertTrue(findings(fr, "structure.story_format"))  # an FR still needs its story


class TestEpicsAndReport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.client = TestClient(create_app(self.tmp.name, SimulatedProvider))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.client.post("/api/v1/projects", json={"name": "p"})
        cid = self.client.post("/api/v1/projects/p/conversations").json()["id"]
        self.client.post(f"/api/v1/projects/p/conversations/{cid}/meeting", json={"text": MEETING})
        self.saved = self.client.post(f"/api/v1/projects/p/conversations/{cid}/approve").json()["saved"]

    def test_the_meeting_nfr_is_saved_as_a_constraint_card(self):
        listed = {r["id"]: r for r in self.client.get("/api/v1/projects/p/requirements").json()}
        nfr = [r for r in listed.values() if r["class"] == "NFR"]
        self.assertEqual(sorted(r["subtype"] for r in nfr), ["PE", "SE"])  # the password rule and the search time
        for r in nfr:
            detail = self.client.get(f"/api/v1/projects/p/requirements/{r['id']}").json()
            self.assertIsNone(detail["content"]["story"])
            self.assertTrue(detail["content"]["constraint"]["quality"])
            self.assertTrue(detail["quality"]["card_complete"])

    def test_epics_hold_every_requirement_once_with_stories_and_constraint_cards(self):
        self.assertEqual(self.client.get("/api/v1/projects/p/epics").json(), [])
        epics = self.client.post("/api/v1/projects/p/epics").json()
        placed = [i for e in epics for i in e["requirements"]]
        self.assertEqual(sorted(placed), sorted(self.saved))
        self.assertTrue(any(e["constraints"] for e in epics))
        self.assertTrue(all(s["story"] for e in epics for s in e["stories"]))
        self.assertTrue(all(c["constraint"] for e in epics for c in e["constraints"]))
        self.assertEqual(self.client.get("/api/v1/projects/p/epics").json(), epics)  # stored

    def test_structure_report(self):
        report = self.client.get("/api/v1/projects/p/quality").json()
        self.assertEqual((report["requirements"], report["fr"], report["nfr"]), (3, 1, 2))
        self.assertEqual(report["rates"]["ears"], 1.0)
        self.assertEqual(report["rates"]["card"], 1.0)
        self.assertEqual(report["nfr_with_story"], [])


if __name__ == "__main__":
    unittest.main()
