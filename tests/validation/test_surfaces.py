"""Tests of where validation shows outside the agents: the REST API, the dashboard and the command line."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

from gigabrain import meeting
from gigabrain.agents import ValidationSettings
from gigabrain.dashboard import server
from gigabrain.providers import SimulatedProvider

from .flows import DETAIL, REQUEST, VAGUE_MEETING, Flow, project

try:
    from fastapi.testclient import TestClient

    from gigabrain.api import create_app
except ImportError:  # the API is optional: its dependencies may not be installed
    TestClient = None

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(TestClient, "fastapi is not installed")
class TestApi(Flow):
    def client(self, **options):
        client = TestClient(create_app(self.folder(), SimulatedProvider, **options))
        client.__enter__()
        self.addCleanup(client.__exit__, None, None, None)
        client.post("/api/v1/projects", json={"name": "p"})
        return client

    def propose(self, client) -> tuple[str, dict]:
        conversation = client.post("/api/v1/projects/p/conversations", json={"mode": "after_meeting"}).json()["id"]
        client.post(f"/api/v1/projects/p/conversations/{conversation}/messages", json={"text": REQUEST})
        return conversation, client.post(f"/api/v1/projects/p/conversations/{conversation}/messages", json={"text": DETAIL}).json()

    def test_the_proposal_comes_with_the_report(self):
        client = self.client()
        conversation, answer = self.propose(client)
        self.assertEqual((answer["action"], answer["validation"]["verdict"]), ("propose", "rejected"))
        self.assertEqual(answer["validation"]["failed_dimensions"], ["verifiable"])
        proposed = answer["proposed_requirements"][0]
        self.assertIn("ears", proposed["quality"])                              # the format check of the project, as before
        self.assertEqual(proposed["validation"]["failed_dimensions"], ["verifiable"])  # and the Critic's scores next to it
        # What the Critic did is behind the scenes, with everything else the agents did.
        self.assertTrue({"validation_report", "validation_return"} <= {b["kind"] for b in answer["behind_the_scenes"]})

        saved = client.post(f"/api/v1/projects/p/conversations/{conversation}/approve").json()
        self.assertEqual(saved["validation"], None)  # an approval is not a proposal
        detail = client.get("/api/v1/projects/p/requirements/R1").json()
        self.assertEqual(detail["validation"]["saved_with"]["report_id"], answer["validation"]["report_id"])
        self.assertEqual(len(detail["validation"]["scores"]), 5)

    def test_a_question_has_no_report(self):
        client = self.client()
        conversation = client.post("/api/v1/projects/p/conversations").json()["id"]
        answer = client.post(f"/api/v1/projects/p/conversations/{conversation}/messages", json={"text": REQUEST}).json()
        self.assertEqual((answer["action"], answer["validation"]), ("ask", None))

    def test_validation_can_be_off_or_adjusted(self):
        _, off = self.propose(self.client(validation=False))
        self.assertEqual((off["action"], off["validation"]), ("propose", None))
        self.assertNotIn("validation", off["proposed_requirements"][0])

        client = TestClient(create_app(self.folder("other"), SimulatedProvider, validation=ValidationSettings(rounds=0)))
        client.__enter__()
        self.addCleanup(client.__exit__, None, None, None)
        client.post("/api/v1/projects", json={"name": "p"})
        _, scored = self.propose(client)
        self.assertEqual(scored["validation"]["rounds"], 0)
        self.assertNotIn("validation_return", {b["kind"] for b in scored["behind_the_scenes"]})


class TestDashboard(Flow):
    def test_the_requirement_page_has_the_scores(self):
        ctx = self.context()
        meeting.process(ctx, project(), "council")
        detail = server._routes(ctx.db, self.folder(), ["requirement", "R1"], {})
        self.assertIn("ears", detail["quality"])
        self.assertEqual((detail["validation"]["passed"], len(detail["validation"]["scores"]), len(detail["validation"]["document"])),
                         (True, 5, 5))
        self.assertIsNone(server._routes(ctx.db, self.folder(), ["requirement", "R99"], {}))

    def test_the_messages_of_the_critic_are_events_like_the_others(self):
        ctx = self.context()
        result = meeting.process(ctx, project(), "council")
        listed = server._routes(ctx.db, self.folder(), ["events"], {"conversation": [result["conversation_id"]]})
        self.assertIn(("critic", "validation_report"), {(e["sender"], e["kind"]) for e in listed})
        json.dumps(listed)  # and they serialise as they are


class TestCommandLine(Flow):
    def run_main(self, *arguments: str) -> tuple[int, str, str]:
        """Run main.py with the arguments. Returns (exit code, stdout, stderr)."""
        spec = importlib.util.spec_from_file_location("gigabrain_main", ROOT / "main.py")
        main = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(main)
        out, err, code = io.StringIO(), io.StringIO(), 0
        with mock.patch.object(sys, "argv", ["main.py", "--data", self.folder(), *arguments]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                main.main()
            except SystemExit as exit_:
                code = exit_.code or 0
        return code, out.getvalue(), err.getvalue()

    def process(self, utterances=None) -> None:
        ctx = self.context()
        meeting.process(ctx, project(utterances) if utterances else project(), "council")
        ctx.db.close()

    def test_validate_scores_the_repository(self):
        self.process()
        code, out, _ = self.run_main("validate")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("[APPROVED WITH REMARKS] items="))
        code, out, _ = self.run_main("validate", "--format", "markdown")
        self.assertEqual(code, 0)
        for name in ("Conforming", "Unambiguous", "Verifiable", "Understandable", "Non-redundancy", "Traceability"):
            self.assertIn(f"| {name} |", out)  # one row per competency, items and document

    def test_a_rejected_repository_exits_with_one(self):
        self.process(VAGUE_MEETING)
        code, out, _ = self.run_main("validate")
        self.assertEqual((code, "REJECTED" in out), (1, True))
        # One requirement on its own, in the format an agent receives.
        code, out, _ = self.run_main("validate", "R2", "--format", "feedback")
        self.assertEqual(code, 0)
        code, out, _ = self.run_main("validate", "R1", "--format", "json")
        self.assertEqual((code, json.loads(out)["focus"]), (1, ["R1"]))
        # The lenient ruler only fails on a blocking finding; a check can be switched off.
        self.assertEqual(self.run_main("validate", "--policy", "lenient")[0], 0)
        code, out, _ = self.run_main("validate", "R1", "--format", "json", "--disable", "ambiguity.vague_terms")
        self.assertNotIn("ambiguity.vague_terms", {f["check_id"] for f in json.loads(out)["item_results"][0]["findings"]})

    def test_bad_input_exits_with_two(self):
        code, _, err = self.run_main("validate")
        self.assertEqual(code, 2)
        self.assertIn("No requirements repository", err)
        self.process()
        code, _, err = self.run_main("validate", "R99")
        self.assertEqual((code, "Requirement not found: R99" in err), (2, True))

    def test_list_checks_needs_no_repository(self):
        code, out, _ = self.run_main("validate", "--list-checks")
        self.assertEqual(code, 0)
        self.assertIn("grounding.links_resolvable", out)

    def test_the_options_of_the_critic_reach_it(self):
        spec = importlib.util.spec_from_file_location("gigabrain_main", ROOT / "main.py")
        main = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(main)
        Args = type("Args", (), {})
        off, tuned = Args(), Args()
        off.no_validation = True
        tuned.no_validation, tuned.validation_rounds, tuned.validation_strict = False, 3, True
        tuned.validation_judge = True
        self.assertIs(main._validation(off), False)
        settings = main._validation(tuned)
        self.assertEqual((settings.rounds, settings.strict, settings.judge), (3, True, True))
        default = main._validation(Args())
        self.assertEqual((default.rounds, default.strict, default.judge), (1, False, False))


if __name__ == "__main__":
    unittest.main()
