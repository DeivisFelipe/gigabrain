"""Builders for the integration tests: a data folder, a meeting to process and
providers that follow a script.

The simulated provider answers with fixed rules, so it cannot improve a
requirement the Critic returns. To test the way back (the Critic returns, the
agent fixes), the providers below change one answer of it and leave the rest as
it is. No test calls an LLM.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from gigabrain.council import Council, open_context
from gigabrain.providers import SimulatedProvider, _structured

# ---------------------------------------------------------------- a meeting

LOGIN = "Users must log in with their email and a password of at least 8 characters."
BACKUP = "The database must be backed up every 24 hours to an external server."
#: What the end user says about search: plain, nothing vague in it.
SEARCH = "The search page should list the results for everyone who searches."
#: The draft the Twin makes of it (MeetingTwin): two subjective words the end user never said.
#: Vagueness the source itself has is only information; the defect these
#: tests need, the one that goes back to the writer, has to be the writer's.
VAGUE = "The search page should be fast and user-friendly for everyone."

MEETING = [
    ("Analyst", "Okay, next topic: accounts. How should that work?"),
    ("Client", LOGIN),
    ("TechLead", BACKUP),
]
VAGUE_MEETING = [("Analyst", "Okay, next topic: search. How should that work?"), ("EndUser", SEARCH), *MEETING[1:]]


def project(utterances=MEETING, project_id: str = "test-project") -> dict:
    """A project as dataset.load_project returns it, built in memory."""
    turns = [{"id": f"T{i}", "speaker": speaker, "text": text, "context": False}
             for i, (speaker, text) in enumerate(utterances, start=1)]
    gold = [{"id": f"{project_id}:G{i}", "text": t["text"], "class": "FR", "subtype": None, "turns": [t["id"]],
             "context_turns": [t["id"]]} for i, t in enumerate(turns, start=1) if t["speaker"] != "Analyst"]
    return {"language": "en", "id": project_id, "folder": project_id, "title": "Test project", "turns": turns, "gold": gold}


# ---------------------------------------------------------------- a conversation

REQUEST = "I want to notify the customer when the payment is confirmed"
DETAIL = "The customer gets an email within 1 minute"


class Flow(unittest.TestCase):
    """A temporary data folder; contexts and councils opened on it close by themselves."""

    def setUp(self):
        # addCleanup runs in reverse order: the databases close before the folder is deleted.
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def folder(self, name: str = "data") -> str:
        return os.path.join(self.tmp.name, name)

    def context(self, name: str = "data", provider=None, validation=True):
        ctx = open_context(self.folder(name), provider or MeetingTwin(), validation=validation)
        self.addCleanup(ctx.db.close)
        return ctx

    def council(self, name: str = "data", provider=None, validation=True) -> Council:
        council = Council(self.context(name, provider, validation))
        council.start()
        return council

    def propose(self, council: Council) -> dict:
        """The two PO messages after which the simulated Twin proposes a requirement."""
        council.talk(REQUEST)
        return council.talk(DETAIL)


def events(ctx, conversation_id: str | None = None, *kinds: str) -> list[dict]:
    """The messages of a conversation, oldest first, optionally only of some kinds."""
    found = ctx.db.list_events(conversation_id or ctx.conversation_id, limit=5000)
    return [e for e in found if not kinds or e["kind"] in kinds]


def validation_trail(ctx, conversation_id: str | None = None) -> list[tuple[str, str, str]]:
    """(kind, stage, recipient) of every message of the Critic, in order."""
    return [(e["kind"], e["content"].get("stage"), e["recipient"])
            for e in events(ctx, conversation_id, "validation_report", "validation_return")]


# ---------------------------------------------------------------- providers that follow a script


def _last(messages: list[dict]) -> dict:
    return json.loads(messages[-1]["content"])


class TwinFixesProposal(SimulatedProvider):
    """The Twin fixes a proposal the Critic returned: a concrete role and two criteria that can be observed."""

    def _twin(self, messages):
        last = _last(messages)
        if last.get("from") != "system":
            return super()._twin(messages)
        self.notice = last["notice"]
        answer = super()._twin(messages[:-1])  # what it had proposed, to be fixed
        requirement = answer["requirements"][0]
        requirement["story"]["role"] = "customer"
        requirement["acceptance_criteria"] = [
            {"scenario": "Confirmed", "given": "an order waiting for payment", "when": "the payment is confirmed",
             "then": "the system sends an email to the customer within 1 minute"},
            {"scenario": "Declined", "given": "an order waiting for payment", "when": "the payment is declined",
             "then": "the system sends no confirmation email"},
        ]
        return answer


class TwinAsksInstead(SimulatedProvider):
    """The Twin answers a returned proposal by asking the PO what it cannot know."""

    QUESTION = "What tells you the notification arrived?"

    def _twin(self, messages):
        if _last(messages).get("from") == "system":
            return {"action": "ask", "message_to_po": self.QUESTION, "query": None, "requirements": []}
        return super()._twin(messages)


class MeetingTwin(SimulatedProvider):
    """The simulator, except that it drafts the search utterance as VAGUE, words its speaker never said."""

    def _twin_draft(self, messages):
        answer = super()._twin_draft(messages)
        if SEARCH in _last(messages).get("utterance", ""):
            topic = answer["requirements"][0]["topic"] if answer["requirements"] else "search"
            answer["requirements"] = [_structured(VAGUE, "end user", topic)]
        return answer


class TwinFixesDraft(MeetingTwin):
    """The Twin fixes the draft of the vague utterance when the Critic returns it."""

    FIXED = "The search page shall show the results within 2 seconds."

    def _twin_revise(self, messages):
        self.received = _last(messages)
        return {"requirement": {
            **self.received["draft"],
            "statement": self.FIXED,
            "story": {"role": "shop customer", "goal": "to get the results of a search within 2 seconds", "benefit": "I do not wait"},
            "acceptance_criteria": [
                {"scenario": "Search", "given": "a catalogue with 10000 items", "when": "the customer searches for a word",
                 "then": "the search page shows the results within 2 seconds"},
                {"scenario": "No result", "given": "a word that matches no item", "when": "the customer searches for it",
                 "then": "the search page shows the message 'no results' within 2 seconds"},
            ],
        }}


class ExpertLinksToMissing(SimulatedProvider):
    """The expert links the draft to a requirement that does not exist, and takes it back when told."""

    insists = False

    def _expert_review(self, messages):
        self.feedback = _last(messages).get("validation_feedback")
        if self.feedback and not self.insists:
            return {"action": "keep", "duplicate_of": None, "links": [], "motive": "no link after all"}
        return {"action": "keep", "duplicate_of": None, "motive": "depends on R99",
                "links": [{"kind": "depends_on", "target": "R99", "reason": "needs it"}]}


class ExpertInsists(ExpertLinksToMissing):
    insists = True


class ExpertCallsItARepetition(SimulatedProvider):
    """The expert says the backup draft repeats R1 (the login requirement), which it does not."""

    def _expert_review(self, messages):
        if "backed up" in _last(messages)["draft"]["statement"]:
            return {"action": "discard", "duplicate_of": "R1", "links": [], "motive": "repeats R1"}
        return {"action": "keep", "duplicate_of": None, "links": [], "motive": "nothing like it in the topic"}


class TwinOverrules(ExpertCallsItARepetition):
    """The Twin reads the Critic's report and keeps a draft whose repetition could not be confirmed."""

    def _twin_decide(self, messages):
        data = _last(messages)
        self.validation = data.get("validation")
        answer = super()._twin_decide(messages)
        if any(f["check_id"] == "grounding.duplicate_claim" for f in (self.validation or {}).get("findings", [])):
            answer.update(save=True, motive="the Critic could not confirm the repetition")
        return answer


class TwinDecideRecorder(MeetingTwin):
    """Keeps what the Twin received for each vote that went to the LLM."""

    def __init__(self):
        super().__init__()
        self.votes: list[dict] = []
        self.systems: list[str] = []

    def complete_json(self, role, system, messages, **options):
        if role == "twin_decide":
            self.votes.append(_last(messages))
            self.systems.append(system)
        return super().complete_json(role, system, messages, **options)
