"""Tests of tier 2: the LLM judge (validation/judge.py).

No test calls a model. The judge receives a scripted function in place of the
provider, which is what lets each property be pinned: off by default, a claim
the judge makes up is dropped, a judge that breaks leaves a note and never a
finding, an artefact that comes back unchanged is not judged twice.
"""

from __future__ import annotations

import json
import unittest

from gigabrain.agents import ValidationSettings
from gigabrain.providers import SimulatedProvider
from gigabrain.validation import host
from gigabrain.validation.engine import ValidationEngine
from gigabrain.validation.judge import CONTEXT_TURNS, UNSUPPORTED_CONTENT, LLMJudge, material
from gigabrain.validation.model import Dimension, EvidenceKind, Severity, Verdict
from gigabrain.validation.ports import NullTraceabilityProvider
from gigabrain.validation.registry import CheckConfig, CheckContext

from .flows import Flow, events
from .helpers import artifact, envelope, evidence, source

JUDGE = UNSUPPORTED_CONTENT.id
#: An excerpt of the clean artefact's statement (tests/validation/helpers.py).
IN_THE_STATEMENT = "one item in the review queue"


class Scripted:
    """Stands for the provider: answers in order and records what it was asked."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, role, system, messages, **options):
        self.calls.append({"role": role, "system": system, "messages": messages, **options})
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


def unsupported(*claims, where="statement", why="", verdict="invented", kind="rule"):
    return {"claims": [{"claim": c, "where": where, "sources_say": why, "verdict": verdict, "kind": kind}
                       for c in claims]}


class JudgeCase(unittest.TestCase):
    def run_judge(self, answer, art=None, config=None, judge=None):
        self.call = Scripted(answer)
        self.judge = judge or LLMJudge(self.call, name="fake-model")
        engine = ValidationEngine(judge=self.judge, config=config)
        return engine.validate(envelope(art or artifact()), document_level=False)

    def judge_findings(self, report):
        return [f for f in report.all_findings() if f.check_id == JUDGE]


class TestOffByDefault(JudgeCase):
    def test_without_a_judge_nothing_is_asked_and_the_report_says_so(self):
        report = ValidationEngine().validate(envelope(artifact()), document_level=False)
        self.assertEqual(self.judge_findings(report), [])
        self.assertEqual(report.judge, "")
        self.assertNotIn(JUDGE, report.checks_run)
        self.assertTrue(any(n.startswith("Tier 2 (LLM judge) is off") for n in report.notes))

    def test_the_settings_of_the_critic_keep_it_off(self):
        self.assertFalse(ValidationSettings().judge)


class TestFindings(JudgeCase):
    def test_a_claim_the_sources_do_not_support_fails_correctness(self):
        report = self.run_judge(unsupported(IN_THE_STATEMENT, why="The utterance mentions no queue."))
        [finding] = self.judge_findings(report)
        self.assertEqual((finding.severity, finding.dimension, finding.evidence_kind, finding.locator),
                         (Severity.MAJOR, Dimension.CORRECTNESS, EvidenceKind.LLM_JUDGMENT, "statement"))
        self.assertTrue(finding.message.startswith("LLM judge:"))
        self.assertIn("Sources: The utterance mentions no queue.", finding.message)
        # One finding of weight 1.5 is enough to fail the competency, as a deterministic one would.
        score = report.item_results[0].scores[Dimension.CORRECTNESS]
        self.assertEqual((score.score, score.passed), (0.625, False))
        self.assertEqual(report.verdict, Verdict.REJECTED)
        self.assertIn(JUDGE, report.checks_run)
        self.assertEqual(report.judge, "fake-model")
        self.assertTrue(any("Tier 2 (LLM judge, fake-model) is on" in n for n in report.notes))

    def test_a_claim_that_is_not_in_the_requirement_is_dropped(self):
        # The judge must quote the requirement; a quote of something else is the judge inventing.
        report = self.run_judge(unsupported("only administrators may see the queue"))
        self.assertEqual(self.judge_findings(report), [])
        self.assertEqual([d["claim"] for d in self.judge.discarded], ["only administrators may see the queue"])

    def test_nothing_unsupported(self):
        report = self.run_judge({"claims": []})
        self.assertEqual(self.judge_findings(report), [])
        self.assertEqual(report.item_results[0].scores[Dimension.CORRECTNESS].score, 1.0)

    def test_a_null_list_is_nothing_unsupported(self):
        self.assertEqual(self.judge_findings(self.run_judge({"claims": None})), [])

    def test_what_the_judge_calls_inferred_is_never_reported(self):
        # 20/10/2026: the judge reported pieces its own justification called supported.
        # Giving it the "inferred" verdict, and dropping those, is the way out it was missing.
        report = self.run_judge(unsupported(IN_THE_STATEMENT, verdict="inferred"))
        self.assertEqual(self.judge_findings(report), [])
        self.assertEqual([c["claim"] for c in self.judge.inferred], [IN_THE_STATEMENT])

    def test_an_invented_detail_is_minor_and_does_not_fail_alone(self):
        report = self.run_judge(unsupported(IN_THE_STATEMENT, kind="detail"))
        [finding] = self.judge_findings(report)
        self.assertEqual(finding.severity, Severity.MINOR)
        self.assertIn("(detail)", finding.message)
        self.assertTrue(report.item_results[0].scores[Dimension.CORRECTNESS].passed)

    def test_without_a_verdict_a_claim_counts_as_invented(self):
        answer = {"claims": [{"claim": IN_THE_STATEMENT, "where": "statement"}]}
        self.assertEqual([f.severity for f in self.judge_findings(self.run_judge(answer))], [Severity.MAJOR])

    def test_the_criterion_a_claim_is_in(self):
        report = self.run_judge(unsupported(IN_THE_STATEMENT, where="criterion 2"))
        self.assertEqual(self.judge_findings(report)[0].locator, "acceptance_criteria[2]")


class TestFailures(JudgeCase):
    def test_an_answer_that_does_not_answer_is_a_note_not_a_finding(self):
        report = self.run_judge({"verdict": "fine"})
        self.assertEqual(self.judge_findings(report), [])
        self.assertTrue(any(n.startswith("The LLM judge failed on R1") for n in report.notes))

    def test_a_judge_that_breaks_does_not_stop_validation(self):
        report = self.run_judge(RuntimeError("timeout"))
        self.assertEqual(self.judge_findings(report), [])
        self.assertEqual(report.verdict, Verdict.APPROVED)  # the clean artefact, judged by tier 1 only
        self.assertTrue(any("timeout" in n for n in report.notes))


class TestWhenItAsks(JudgeCase):
    def test_without_sources_the_question_is_not_asked(self):
        # "I do not know" is not "unsupported".
        call = Scripted(unsupported(IN_THE_STATEMENT))
        engine = ValidationEngine(judge=LLMJudge(call))
        engine.validate(envelope(artifact(evidences=[]), sources=[]), document_level=False)
        self.assertEqual(call.calls, [])

    def test_an_artefact_that_comes_back_unchanged_is_judged_once(self):
        call = Scripted({"claims": []})
        engine = ValidationEngine(judge=LLMJudge(call))
        for _ in range(3):
            engine.validate(envelope(artifact()), document_level=False)
        self.assertEqual(len(call.calls), 1)

    def test_it_can_be_switched_off_like_a_check(self):
        config = CheckConfig(disabled_checks=frozenset({JUDGE}))
        report = self.run_judge(unsupported(IN_THE_STATEMENT), config=config)
        self.assertEqual(self.call.calls, [])
        self.assertEqual(report.checks_skipped[JUDGE], "switched off in the configuration")

    def test_the_prompt_asks_for_json(self):
        # The JSON mode of the provider requires the word in the prompt.
        self.run_judge({"claims": []})
        self.assertIn("JSON", self.call.calls[0]["system"])
        self.assertEqual(self.call.calls[0]["role"], "critic_judge")
        self.assertEqual(self.call.calls[0]["temperature"], 0.0)


class TestMaterial(unittest.TestCase):
    def test_the_cited_turn_and_the_turns_before_it(self):
        turns = [source(f"T{i}", f"utterance number {i}") for i in range(1, 11)]
        art = artifact(evidences=[evidence("T9")])
        ctx = CheckContext(envelope=envelope(art, sources=turns), config=CheckConfig(),
                           trace=NullTraceabilityProvider(), focus=frozenset())
        sent = material(art, ctx)["sources"]
        context = [f"T{i}" for i in range(9 - CONTEXT_TURNS, 9)]
        self.assertEqual([(s["id"], s["role"]) for s in sent], [(t, "context") for t in context] + [("T9", "cited")])

    def test_the_requirement_is_not_its_own_source(self):
        # Regression, 20/10/2026: the knowledge of a cited expert records the saved requirement.
        knowledge = source("expert:search", kind="expert", origin="knowledge",
                           text="- Business rule: one item per topic.\n- R1 (FR) — the statement of R1 itself")
        art = artifact("R1", evidences=[evidence("expert:search")])
        ctx = CheckContext(envelope=envelope(art, sources=[knowledge]), config=CheckConfig(),
                           trace=NullTraceabilityProvider(), focus=frozenset())
        [sent] = material(art, ctx)["sources"]
        self.assertIn("one item per topic", sent["text"])
        self.assertNotIn("R1", sent["text"])

    def test_the_requirement_goes_whole(self):
        art = artifact()
        ctx = CheckContext(envelope=envelope(art), config=CheckConfig(), trace=NullTraceabilityProvider(), focus=frozenset())
        requirement = material(art, ctx)["requirement"]
        self.assertEqual(set(requirement), {"title", "statement", "story", "acceptance_criteria"})
        self.assertTrue(requirement["acceptance_criteria"][0].startswith("1. "))


# ============================================================ inside the system


class JudgeFlagsTheFirstProposal(SimulatedProvider):
    """The judge finds the statement unsupported once; after that, nothing."""

    def __init__(self):
        super().__init__()
        self.judged = 0

    def _critic_judge(self, messages):
        self.judged += 1
        if self.judged > 1:
            return {"claims": []}
        statement = json.loads(messages[-1]["content"])["requirement"]["statement"]
        return unsupported(statement[:40], why="The PO did not say it.")


class TestInsideTheCritic(Flow):
    def test_the_judge_is_called_through_the_critic_and_logged(self):
        council = self.council(validation=ValidationSettings(judge=True))
        self.propose(council)
        calls = [e for e in events(council.ctx, None, "llm_call") if e["content"]["role"] == "critic_judge"]
        self.assertTrue(calls)
        self.assertEqual({e["sender"] for e in calls}, {"critic"})
        report = events(council.ctx, None, "validation_report")[0]["content"]
        self.assertEqual(report["judge"], "simulated")

    def test_what_the_judge_finds_goes_back_to_the_twin(self):
        council = self.council(provider=JudgeFlagsTheFirstProposal(), validation=ValidationSettings(judge=True))
        self.propose(council)
        first = events(council.ctx, None, "validation_report")[0]["content"]
        judged = [f for f in first["findings"] if f["check_id"] == JUDGE]
        self.assertEqual([(f["severity"], f["evidence"]) for f in judged], [("major", "llm_judgment")])
        returned = events(council.ctx, None, "validation_return")[0]["content"]
        self.assertIn("LLM judge:", returned["text"])
        self.assertIn("correctness", returned["failed_dimensions"])

    def test_in_a_conversation_the_judge_reads_the_dialogue(self):
        # 20/10/2026: "1. automatic" meant nothing to a judge that did not see the question.
        council = self.council(provider=JudgeFlagsTheFirstProposal(), validation=ValidationSettings(judge=True))
        self.propose(council)
        evidence, sources = host.conversation_evidence(council.ctx.db, council.ctx.conversation_id)
        dialogue = next(s for s in sources if s.kind == "dialogue")
        self.assertEqual(dialogue.id, host.dialogue_id(council.ctx.conversation_id))
        self.assertIn("PO: ", dialogue.text)
        self.assertIn("Twin (question): ", dialogue.text)
        # Read by the judge, never cited: nothing can be grounded in the Twin's own words.
        self.assertNotIn(dialogue.id, {ref.source_id for ref in evidence})
        artifacts, _ = council.ctx.critic._proposal_artifacts(council.pending_proposal)
        from gigabrain.validation.model import Envelope
        ctx = CheckContext(envelope=Envelope(artifacts=artifacts, sources=sources), config=CheckConfig(),
                           trace=NullTraceabilityProvider(), focus=frozenset())
        kinds = [s["kind"] for s in material(artifacts[0], ctx)["sources"]]
        self.assertIn("dialogue", kinds)
        self.assertNotIn("conversation", kinds)

    def test_without_the_option_the_judge_is_never_called(self):
        provider = JudgeFlagsTheFirstProposal()
        self.propose(self.council(provider=provider))
        self.assertEqual(provider.judged, 0)


if __name__ == "__main__":
    unittest.main()
