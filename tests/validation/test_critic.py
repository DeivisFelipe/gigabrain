"""Tests of the Critic agent: the gate of each agent and the report that reaches the Twin and the PO.

Two flows are covered, the same two the project has:

- the conversation with the PO, where the gate is on what the Twin proposes;
- the meeting processed in batch, where there is one gate on the Twin's draft,
  one on what the Consultative Council adds to it, and the report that guides
  the Twin's vote.

Everything runs on the simulated provider or on a provider that scripts one
answer of it (see flows.py). No LLM is called.
"""

from __future__ import annotations

import os
import unittest

from gigabrain import meeting
from gigabrain.agents import ValidationSettings
from gigabrain.agents.critic import STAGES
from gigabrain.agents.twin import DECIDE_PROMPT, GUIDANCE_NOTE
from gigabrain.validation import host

from .flows import (
    BACKUP,
    LOGIN,
    MEETING,
    REQUEST,
    VAGUE_MEETING,
    ExpertCallsItARepetition,
    ExpertInsists,
    ExpertLinksToMissing,
    Flow,
    TwinAsksInstead,
    TwinDecideRecorder,
    TwinFixesDraft,
    TwinFixesProposal,
    TwinOverrules,
    events,
    project,
    validation_trail,
)

ITEM = {"conforming", "unambiguous", "verifiable", "correctness", "understandable"}
DOCUMENT = {"internal_consistency", "non_redundancy", "completeness", "conciseness", "traceability"}


def boom(*args, **kwargs):
    raise RuntimeError("the validation engine broke")


# ============================================================ conversation with the PO


class TestProposalGate(Flow):
    def test_the_po_sees_one_proposal_with_its_report(self):
        council = self.council()
        answer = self.propose(council)
        self.assertEqual(answer["action"], "propose")

        # The proposal is scored, returned once, and then assessed together with the repository.
        # The simulated Twin cannot fix it, so it comes back as it was and goes on with its report.
        self.assertEqual(validation_trail(council.ctx), [
            ("validation_report", "proposal", "twin"),
            ("validation_return", "proposal", "twin"),
            ("validation_report", "consolidated", "twin"),
        ])
        kinds = [e["kind"] for e in events(council.ctx)]
        self.assertEqual(kinds.count("proposal"), 1)  # the PO never sees the version that was returned
        self.assertEqual(kinds[-2:], ["validation_report", "proposal"])

        check = answer["validation"]
        self.assertEqual((check["verdict"], check["passed"], check["failed_dimensions"]), ("rejected", False, ["verifiable"]))
        self.assertEqual((check["rounds"], check["unchanged"]), (1, True))
        self.assertEqual(set(check["items"]["P1"]["scores"]), ITEM)       # five scores for the item
        self.assertEqual(set(check["document"]), DOCUMENT)                 # five for the set it joins
        self.assertIn("criterion", check["feedback"])                      # what to tell the PO it still lacks
        # Each requirement carries its own scores, which is what is shown next to it.
        own = answer["requirements"][0]["validation"]
        self.assertEqual((set(own["scores"]), own["failed_dimensions"], own["report_id"]), (ITEM, ["verifiable"], check["report_id"]))
        self.assertTrue(all(f["target"] == "P1" for f in own["findings"]))

    def test_the_twin_fixes_what_was_returned(self):
        provider = TwinFixesProposal()
        council = self.council(provider=provider)
        answer = self.propose(council)

        self.assertEqual(validation_trail(council.ctx), [
            ("validation_report", "proposal", "twin"),
            ("validation_return", "proposal", "twin"),
            ("validation_report", "proposal", "twin"),      # the fixed version, scored again
            ("validation_report", "consolidated", "twin"),
        ])
        check = answer["validation"]
        self.assertEqual((check["verdict"], check["passed"], check["rounds"], check["unchanged"]), ("approved", True, 1, False))
        self.assertIsNone(check["feedback"])
        self.assertEqual(answer["requirements"][0]["story"]["role"], "customer")  # the PO sees the fixed one
        self.assertEqual(council.pending_proposal, answer["requirements"])
        # What the Twin received: the competency that failed, the points to fix, and the way out.
        self.assertIn("Competencies that did not pass: Verifiable", provider.notice)
        self.assertIn("acceptance_criteria[1].then", provider.notice)
        self.assertIn("ask the PO instead of inventing it", provider.notice)
        self.assertIn("The PO has not seen the returned version", provider.notice)
        returned = events(council.ctx, None, "validation_return")[0]["content"]
        self.assertEqual((returned["round"], returned["failed_dimensions"], returned["artefacts"]), (1, ["verifiable"], ["P1"]))
        # The version that went back stays in the log, so the correction can be compared with it.
        self.assertEqual([r["story"]["role"] for r in returned["returned"]], ["user"])

    def test_the_twin_may_answer_a_return_by_asking_the_po(self):
        council = self.council(provider=TwinAsksInstead())
        answer = self.propose(council)
        # Asking for what only the PO knows is a decision of the Twin, and it stands.
        self.assertEqual((answer["action"], answer["message_to_po"]), ("ask", TwinAsksInstead.QUESTION))
        self.assertNotIn("validation", answer)
        self.assertIsNone(council.pending_proposal)
        kinds = [e["kind"] for e in events(council.ctx)]
        self.assertNotIn("proposal", kinds)
        self.assertEqual(kinds[-1], "question_to_po")
        self.assertEqual(validation_trail(council.ctx), [("validation_report", "proposal", "twin"), ("validation_return", "proposal", "twin")])

    def test_with_no_rounds_the_proposal_is_scored_and_never_returned(self):
        council = self.council(validation=ValidationSettings(rounds=0))
        answer = self.propose(council)
        self.assertEqual(validation_trail(council.ctx), [("validation_report", "proposal", "twin"), ("validation_report", "consolidated", "twin")])
        self.assertEqual((answer["validation"]["rounds"], answer["validation"]["passed"]), (0, False))

    def test_without_validation_nothing_changes(self):
        council = self.council(validation=False)
        self.assertIsNone(council.ctx.critic)
        answer = self.propose(council)
        self.assertEqual(answer["action"], "propose")
        self.assertNotIn("validation", answer)
        self.assertNotIn("validation", answer["requirements"][0])
        self.assertEqual(validation_trail(council.ctx), [])
        council.talk("approved")
        self.assertNotIn("validation", {s["kind"] for s in council.ctx.db.get_requirement("R1")["sources"]})

    def test_the_po_decides_and_the_requirement_keeps_its_report(self):
        council = self.council()
        check = self.propose(council)["validation"]
        # A proposal that did not pass is still the PO's to approve: the Critic informs, the PO decides.
        self.assertEqual(council.talk("approved")["action"], "approved")
        sources = council.ctx.db.get_requirement("R1")["sources"]
        self.assertIn({"kind": "validation", "ref": check["report_id"], "verdict": "rejected"}, sources)
        stored = host.EventReportStore(council.ctx.db).find(check["report_id"])
        self.assertEqual((stored["stage"], stored["verdict"], stored["artefacts"]), ("consolidated", "rejected", ["P1"]))
        self.assertEqual(host.validation_of(council.ctx.db, "R1")["saved_with"]["report_id"], check["report_id"])

    def test_a_repetition_of_a_saved_requirement_fails_non_redundancy(self):
        first = self.council()
        self.propose(first)
        first.talk("approved")

        second = self.council()  # another conversation over the same repository
        answer = second.talk(REQUEST)
        self.assertEqual(answer["action"], "propose")
        check = answer["validation"]
        self.assertIn("non_redundancy", check["failed_dimensions"])
        self.assertFalse(check["document"]["non_redundancy"]["passed"])
        repeated = next(f for f in check["findings"] if f["check_id"] == "redundancy.near_duplicate")
        self.assertEqual(repeated["target"], "P1|R1")
        # The item gate did not see it (one requirement alone repeats nothing); the set did.
        item_report = events(second.ctx, None, "validation_report")[0]["content"]
        self.assertEqual((item_report["stage"], item_report["failed_dimensions"]), ("proposal", ["verifiable"]))

    def test_a_link_to_a_requirement_that_does_not_exist_is_reported(self):
        council = self.council()
        council.talk("I want to export the monthly sales report to PDF, it depends on R99")
        answer = council.talk("The sales manager downloads the file")
        self.assertEqual(answer["requirements"][0]["links"][0]["target"], "R99")
        self.assertIn("correctness", answer["validation"]["failed_dimensions"])
        finding = next(f for f in answer["validation"]["findings"] if f["check_id"] == "grounding.links_resolvable")
        self.assertIn("R99 does not exist", finding["message"])

    def test_a_failure_of_the_critic_does_not_stop_the_conversation(self):
        council = self.council()
        council.ctx.critic.engine.validate = boom
        answer = self.propose(council)
        self.assertEqual(answer["action"], "propose")
        self.assertNotIn("validation", answer)
        error = events(council.ctx, None, "error")[0]
        self.assertEqual((error["sender"], error["recipient"]), ("critic", "system"))
        self.assertEqual((error["content"]["role"], error["content"]["stage"]), ("critic", "proposal"))
        self.assertIn("the validation engine broke", error["content"]["error"])
        self.assertEqual(council.talk("approved")["action"], "approved")  # and the PO can still approve


# ============================================================ meeting processed in batch


class TestMeetingGates(Flow):
    def run_meeting(self, utterances=MEETING, mode="council", provider=None, validation=True, name="data"):
        ctx = self.context(name, provider, validation)
        return ctx, meeting.process(ctx, project(utterances), mode)

    def test_what_is_saved_carries_its_scores_and_its_report(self):
        ctx, result = self.run_meeting()
        self.assertEqual([p["text"] for p in result["predicted"]], [LOGIN, BACKUP])
        for saved in result["predicted"]:
            check = saved["validation"]
            self.assertEqual((set(check["scores"]), check["passed"], check["failed_dimensions"], check["rounds"]), (ITEM, True, [], 0))
            sources = ctx.db.get_requirement(saved["id"])["sources"]
            self.assertIn({"kind": "validation", "ref": check["report_id"], "verdict": check["verdict"]}, sources)
        # One gate per draft, then the draft together with the repository; at the end, the repository.
        self.assertEqual(validation_trail(ctx, result["conversation_id"]), [
            ("validation_report", "draft", "twin"), ("validation_report", "consolidated", "twin"),
            ("validation_report", "draft", "twin"), ("validation_report", "consolidated", "twin"),
            ("validation_report", "repository", "repository"),
        ])
        repository = result["validation"]
        self.assertEqual((repository["requirements"], repository["requirements_passed"], repository["passed"]), (2, 2, True))
        self.assertEqual(set(repository["document"]), DOCUMENT)
        self.assertTrue(all(score["assessed"] for score in repository["document"].values()))

    def test_the_evaluation_reports_the_share_that_passes(self):
        ctx = self.context()
        report = meeting.process_and_evaluate(ctx, project(VAGUE_MEETING), self.folder(), "council")
        metrics = report["metrics"]
        self.assertEqual(metrics["valid"], round(2 / 3, 4))
        self.assertEqual((metrics["valid_unambiguous"], metrics["valid_verifiable"], metrics["valid_correctness"]),
                         (round(2 / 3, 4), round(2 / 3, 4), 1.0))
        self.assertEqual(report["validation"]["requirements_passed"], 2)
        self.assertTrue(os.path.exists(os.path.join(self.folder(), "evaluation.json")))

    def test_a_draft_that_fails_goes_back_to_the_twin(self):
        ctx, result = self.run_meeting(VAGUE_MEETING)
        trail = validation_trail(ctx, result["conversation_id"])
        self.assertEqual(trail[:3], [("validation_report", "draft", "twin"), ("validation_return", "draft", "twin"),
                                     ("validation_report", "consolidated", "twin")])
        returned = events(ctx, result["conversation_id"], "validation_return")[0]["content"]
        self.assertEqual((returned["round"], returned["failed_dimensions"]), (1, ["unambiguous", "verifiable"]))
        self.assertIn("'fast'", returned["text"])
        self.assertIn("'user-friendly'", returned["text"])
        self.assertNotIn("'friendly'", returned["text"])  # a word inside a longer term is one point to fix, not two
        # The simulated Twin cannot fix it. Without strict mode it is saved, and its report says it did not pass.
        vague = result["predicted"][0]["validation"]
        self.assertEqual((vague["passed"], vague["failed_dimensions"], vague["rounds"]), (False, ["unambiguous", "verifiable"], 1))
        self.assertEqual(len(result["predicted"]), 3)
        self.assertEqual((result["validation"]["requirements_passed"], result["validation"]["passed"]), (2, False))

    def test_the_twin_fixes_the_draft(self):
        provider = TwinFixesDraft()
        ctx, result = self.run_meeting(VAGUE_MEETING, provider=provider)
        self.assertEqual(validation_trail(ctx, result["conversation_id"])[:4], [
            ("validation_report", "draft", "twin"), ("validation_return", "draft", "twin"),
            ("validation_report", "draft", "twin"), ("validation_report", "consolidated", "twin"),
        ])
        fixed = result["predicted"][0]
        self.assertEqual(fixed["text"], TwinFixesDraft.FIXED)
        self.assertEqual((fixed["validation"]["passed"], fixed["validation"]["rounds"]), (True, 1))
        self.assertEqual(ctx.db.get_requirement("R1")["content"]["statement"], TwinFixesDraft.FIXED)
        # The Twin got its draft, the utterance it cites and the points to fix; the revision is in the log.
        self.assertEqual(provider.received["utterances"], {"T2": VAGUE_MEETING[1][1]})
        self.assertIn("Competencies that did not pass: Unambiguous, Verifiable", provider.received["validation_feedback"])
        revisions = [e for e in events(ctx, result["conversation_id"], "drafts") if e["content"].get("revision")]
        self.assertEqual([r["content"]["requirements"][0]["statement"] for r in revisions], [TwinFixesDraft.FIXED])
        self.assertTrue(result["validation"]["passed"])

    def test_strict_mode_discards_what_still_fails(self):
        ctx, result = self.run_meeting(VAGUE_MEETING, validation=ValidationSettings(strict=True))
        self.assertEqual([p["text"] for p in result["predicted"]], [LOGIN, BACKUP])
        self.assertEqual([d["motive"] for d in result["discarded"]], ["validation: did not pass unambiguous, verifiable"])
        closed = events(ctx, result["conversation_id"], "validation_return")[-1]["content"]
        self.assertEqual(closed["stage"], "consolidated")
        self.assertTrue(closed["text"].startswith("Not saved: in strict mode"))
        self.assertEqual([r["id"] for r in ctx.db.list_requirements()], ["R1", "R2"])
        self.assertTrue(result["validation"]["passed"])

    def test_the_baselines_are_scored_and_never_returned(self):
        for mode in ("single_agent", "plain_llm"):
            ctx, result = self.run_meeting(VAGUE_MEETING, mode=mode, validation=ValidationSettings(strict=True), name=mode)
            trail = validation_trail(ctx, result["conversation_id"])
            # A baseline has to stay what it is: nothing goes back, nothing is discarded, not even in strict mode.
            self.assertNotIn("validation_return", [kind for kind, _, _ in trail], mode)
            self.assertEqual({stage for _, stage, _ in trail}, {"consolidated", "repository"}, mode)
            self.assertEqual((len(result["predicted"]), result["discarded"]), (3, []), mode)
            self.assertFalse(result["predicted"][0]["validation"]["passed"], mode)
            self.assertEqual(result["predicted"][0]["validation"]["rounds"], 0, mode)
        # The plain LLM returns a sentence, not a requirement in the project's structure: it does not conform.
        self.assertEqual({tuple(p["validation"]["failed_dimensions"]) for p in result["predicted"][1:]}, {("conforming",)})

    def test_the_council_takes_back_a_link_to_a_requirement_that_does_not_exist(self):
        provider = ExpertLinksToMissing()
        ctx, result = self.run_meeting(MEETING[:2], provider=provider)
        self.assertEqual(validation_trail(ctx, result["conversation_id"])[:4], [
            ("validation_report", "draft", "twin"),
            ("validation_report", "recommendation", "leader"),   # the draft as the council recommends it
            ("validation_return", "recommendation", "leader"),   # back to the Leader, and through it to the expert
            ("validation_report", "consolidated", "twin"),
        ])
        self.assertIn("R99 does not exist", provider.feedback)
        self.assertNotIn("acceptance criterion", provider.feedback)  # only what the council added, not the draft's own remarks
        saved = result["predicted"][0]["validation"]
        self.assertEqual((saved["passed"], saved["rounds"]), (True, 1))
        self.assertEqual(ctx.db.list_links("R1"), [])

    def test_an_expert_that_insists_is_reported_to_the_twin(self):
        class Provider(ExpertInsists, TwinDecideRecorder):
            pass

        provider = Provider()
        ctx, result = self.run_meeting(MEETING[:2], provider=provider)
        self.assertEqual(validation_trail(ctx, result["conversation_id"])[1:4], [
            ("validation_report", "recommendation", "leader"), ("validation_return", "recommendation", "leader"),
            ("validation_report", "consolidated", "twin"),
        ])
        vote = provider.votes[0]
        self.assertEqual(vote["validation"]["failed_dimensions"], ["correctness"])
        self.assertIn("grounding.links_resolvable", {f["check_id"] for f in vote["validation"]["findings"]})
        self.assertNotIn("validation", vote["recommendation"])  # the recommendation goes as the expert wrote it
        self.assertEqual(provider.systems[0], DECIDE_PROMPT + GUIDANCE_NOTE)
        # The simulated Twin follows the expert anyway. What it decided is saved with the report that says so.
        self.assertEqual(result["predicted"][0]["validation"]["failed_dimensions"], ["correctness"])

    def test_a_repetition_the_critic_confirms_is_discarded_without_a_return(self):
        ctx, result = self.run_meeting([*MEETING[:2], ("EndUser", LOGIN)])
        self.assertEqual((len(result["predicted"]), len(result["discarded"])), (1, 1))
        trail = validation_trail(ctx, result["conversation_id"])
        self.assertEqual(trail[2:], [
            ("validation_report", "draft", "twin"),
            ("validation_report", "recommendation", "leader"),  # "repeats R1": checked, and it does
            ("validation_report", "repository", "repository"),
        ])

    def test_a_repetition_the_critic_cannot_confirm_reaches_the_twin(self):
        provider = ExpertCallsItARepetition()
        ctx, result = self.run_meeting(provider=provider)
        self.assertEqual(validation_trail(ctx, result["conversation_id"])[2:6], [
            ("validation_report", "draft", "twin"),
            ("validation_report", "recommendation", "leader"),
            ("validation_return", "recommendation", "leader"),
            ("validation_report", "consolidated", "twin"),
        ])
        claim = events(ctx, result["conversation_id"], "validation_return")[0]["content"]["text"]
        self.assertIn("said to repeat R1", claim)
        # The simulated Twin follows the expert: the draft is discarded, by the Twin's vote.
        self.assertEqual([d["motive"] for d in result["discarded"]], ["followed the recommendation (discard): repeats R1"])

    def test_the_twin_can_keep_a_draft_whose_repetition_was_not_confirmed(self):
        provider = TwinOverrules()
        ctx, result = self.run_meeting(provider=provider)
        self.assertEqual(provider.validation["failed_dimensions"], ["correctness"])
        self.assertEqual([p["text"] for p in result["predicted"]], [LOGIN, BACKUP])
        self.assertEqual(result["discarded"], [])
        # What is saved is assessed as it was decided: without the claim, it passes.
        kept = result["predicted"][1]["validation"]
        self.assertEqual((kept["passed"], kept["verdict"]), (True, "approved_with_remarks"))
        self.assertEqual(validation_trail(ctx, result["conversation_id"])[-3:], [
            ("validation_report", "consolidated", "twin"),   # what reached the vote
            ("validation_report", "consolidated", "twin"),   # what the Twin decided to save
            ("validation_report", "repository", "repository"),
        ])

    def test_the_vote_is_guided_only_when_something_did_not_pass(self):
        clean = TwinDecideRecorder()
        self.run_meeting(provider=clean, name="clean")
        self.assertEqual(clean.votes, [])  # the expert agreed and the report is clean: no LLM call, as before

        vague = TwinDecideRecorder()
        self.run_meeting(VAGUE_MEETING, provider=vague, name="vague")
        self.assertEqual(len(vague.votes), 1)
        guidance = vague.votes[0]["validation"]
        self.assertEqual(set(guidance), {"verdict", "failed_dimensions", "scores", "document", "findings"})
        self.assertEqual((guidance["verdict"], guidance["failed_dimensions"]), ("rejected", ["unambiguous", "verifiable"]))
        self.assertTrue(vague.systems[0].endswith(GUIDANCE_NOTE))

    def test_validation_does_not_change_what_is_extracted(self):
        reports = {}
        for name, validation in (("on", True), ("off", False)):
            ctx = self.context(name, validation=validation)
            reports[name] = meeting.process_and_evaluate(ctx, project(VAGUE_MEETING), self.folder(name), "council")
        for key in ("predicted", "hits", "precision", "recall", "f1", "class", "trace", "ears", "gherkin"):
            self.assertEqual(reports["on"]["metrics"][key], reports["off"]["metrics"][key], key)
        self.assertEqual([p["text"] for p in reports["on"]["predicted"]], [p["text"] for p in reports["off"]["predicted"]])
        self.assertNotIn("valid", reports["off"]["metrics"])
        self.assertIsNone(reports["off"]["validation"])
        self.assertNotIn("validation", reports["off"]["predicted"][0])

    def test_a_failure_of_the_critic_does_not_stop_the_meeting(self):
        ctx = self.context()
        ctx.critic.engine.validate = boom
        result = meeting.process(ctx, project(), "council")
        self.assertEqual([p["text"] for p in result["predicted"]], [LOGIN, BACKUP])
        self.assertNotIn("validation", result["predicted"][0])
        self.assertIsNone(result["validation"])
        errors = [e["content"] for e in events(ctx, result["conversation_id"], "error")]
        self.assertEqual({e["role"] for e in errors}, {"critic"})
        self.assertEqual({e["stage"] for e in errors}, {"draft", "consolidated", "repository"})
        self.assertTrue(set(STAGES) >= {e["stage"] for e in errors})


# ============================================================ the repository as a whole


class TestRepositoryReview(Flow):
    def test_a_requirement_is_assessed_against_the_others(self):
        ctx = self.context()
        meeting.process(ctx, project(), "council")
        report = ctx.critic.validate_repository({"R2"})
        self.assertEqual((report.focus, [i.artifact_id for i in report.item_results]), (["R2"], ["R2"]))
        self.assertEqual(len(ctx.critic.validate_repository().item_results), 2)

    def test_a_requirement_that_is_no_longer_active_can_still_be_looked_at(self):
        ctx = self.context()
        meeting.process(ctx, project(), "council")
        ctx.db.save_requirement({"title": "Passkey", "statement": "The system shall accept a passkey.", "topics": ["accounts"]},
                                None, links=[{"kind": "supersedes", "target": "R1"}])
        self.assertEqual(ctx.db.get_requirement("R1")["status"], "superseded")
        self.assertEqual([i.artifact_id for i in ctx.critic.validate_repository().item_results], ["R2", "R3"])
        self.assertEqual([i.artifact_id for i in ctx.critic.validate_repository({"R1"}).item_results], ["R1"])

    def test_looking_at_the_repository_writes_nothing(self):
        ctx = self.context()
        result = meeting.process(ctx, project(), "council")
        before = len(events(ctx, result["conversation_id"]))
        ctx.critic.validate_repository()
        self.assertEqual(len(events(ctx, result["conversation_id"])), before)

    def test_each_session_ends_with_the_repository_as_it_left_it(self):
        ctx = self.context()
        first = meeting.process(ctx, project(MEETING[:2]), "council")
        second = meeting.process(ctx, project([MEETING[0], ("TechLead", BACKUP)], "second"), "council")
        self.assertEqual((first["validation"]["requirements"], second["validation"]["requirements"]), (1, 2))


if __name__ == "__main__":
    unittest.main()
