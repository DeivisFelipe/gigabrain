"""Runs a whole conversation between the PO and GigaBrain.

Flow of one PO message:

    PO ──message──▶ Twin ──(consult?)──▶ Leader ──▶ Experts
                      ▲                      │
                      └── consolidated answer┘
                      │
                      ├─ ask ─────▶ PO
                      └─ propose ─▶ PO ──"approved"──▶ save in the Repository
                                                        and update the experts

When the PO approves:
1. each proposed requirement is saved (new or new version), with its sources
   (conversation, experts consulted) and links
2. requirements that depended on one that changed become "under_review"
3. the experts of the topics involved update their knowledge file
"""

from __future__ import annotations

import os

from .agents import Context, DigitalTwin, Expert, Leader
from .approval import is_approval
from .database import Database, slug
from .log import Recorder
from .messages import LEADER, PO, REGISTRY, REPOSITORY, SYSTEM, TWIN, Message

MAX_CONSULTATIONS_PER_MESSAGE = 2
REQUIREMENT_FIELDS = ("title", "statement", "ears_pattern", "story", "acceptance_criteria", "class", "subtype", "kind", "topics")


def open_context(data_folder: str, provider, mode: str = "after_meeting", show_agents: bool = False) -> Context:
    os.makedirs(data_folder, exist_ok=True)
    db = Database(data_folder)
    recorder = Recorder(db, os.path.join(data_folder, "logs"), show_agents=show_agents)
    return Context(db, recorder, provider, os.path.join(data_folder, "knowledge"), mode)


def _record(ctx: Context, sender: str, recipient: str, kind: str, content: dict) -> None:
    ctx.recorder.record(Message(sender=sender, recipient=recipient, kind=kind, content=content, conversation_id=ctx.conversation_id))


class Council:
    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.twin = DigitalTwin(ctx)
        self.leader = Leader(ctx)
        self.pending_proposal: list[dict] | None = None
        self.sources: list[dict] = []
        self.related: list[str] = []

    def start(self) -> str:
        self.ctx.conversation_id = self.ctx.db.start_conversation(self.ctx.mode)
        return self.ctx.conversation_id

    def end(self, status: str = "closed") -> None:
        if self.ctx.conversation_id:
            self.ctx.db.end_conversation(self.ctx.conversation_id, status)

    # ------------------------------------------------------------------ conversation

    def talk(self, text: str) -> dict:
        """Receive a PO message and return what to show to the PO.

        Returns {"action": "ask"|"propose"|"approved", "message_to_po": ..., "requirements": [...]}
        """
        if self.pending_proposal and is_approval(text):
            _record(self.ctx, PO, TWIN, "approval", {"text": text})
            return self.approve()

        _record(self.ctx, PO, TWIN, "po_request", {"text": text})
        decision = self.twin.hear_po(text)

        consultations = 0
        while decision["action"] == "consult":
            if consultations >= MAX_CONSULTATIONS_PER_MESSAGE:
                decision = self.twin.hear_system("Consultation limit reached for this message. Ask the PO or propose with what you know.")
                if decision["action"] == "consult":
                    decision = {"action": "ask", "message_to_po": decision.get("message_to_po", ""), "requirements": []}
                break
            query = decision.get("query") or {}
            answer = self.leader.consult(query.get("question") or text, query.get("topics") or [])
            self._keep_sources(answer)
            consultations += 1
            decision = self.twin.hear_leader(answer)

        # A proposal is only replaced by another proposal: if the Twin answers a question
        # in between, the last proposal can still be approved.
        if decision["action"] == "propose":
            self.pending_proposal = decision["requirements"]
        return decision

    def _keep_sources(self, answer: dict) -> None:
        for source in answer.get("sources", []):
            if source not in self.sources:
                self.sources.append(source)
        for rid in answer.get("related_requirements", []):
            if rid not in self.related:
                self.related.append(rid)

    # ------------------------------------------------------------------ approval

    def approve(self) -> dict:
        if not self.pending_proposal:
            return {"action": "ask", "message_to_po": "There is no proposal to approve yet.", "requirements": []}

        saved = []
        for proposed in self.pending_proposal:
            content = {k: proposed[k] for k in REQUIREMENT_FIELDS if k in proposed}
            sources = [{"kind": "conversation", "ref": self.ctx.conversation_id}, *self.sources]
            sources += [{"kind": "requirement", "ref": rid} for rid in self.related]
            result = self.ctx.db.save_requirement(content, self.ctx.conversation_id, base_id=proposed.get("base"),
                                                  reason=proposed.get("reason"), links=proposed.get("links"), sources=sources)
            _record(self.ctx, TWIN, REPOSITORY, "requirement_saved", {**result, "decided_by": "po (explicit approval)"})
            for rid in result["under_review"]:
                _record(self.ctx, REPOSITORY, TWIN, "requirement_under_review", {"requirement_id": rid, "because_of": result["requirement_id"]})
            self._update_experts(result)
            saved.append(result)

        self.pending_proposal = None
        text = "Saved: " + ", ".join(f"{s['requirement_id']} v{s['version']}" for s in saved) + "."
        _record(self.ctx, TWIN, PO, "confirmation", {"text": text, "saved": [s["requirement_id"] for s in saved]})
        self.end("approved")
        return {"action": "approved", "message_to_po": text, "saved": saved, "requirements": []}

    def _update_experts(self, result: dict) -> None:
        """Tell the experts of every topic touched by the saved requirement."""
        db = self.ctx.db
        req = db.get_requirement(result["requirement_id"])
        topics = list(req["topics"])
        for link in result["links"]:
            target = db.get_requirement(link["target"])
            topics += [t for t in target["topics"] if t not in topics]

        trigger = {
            "kind": "requirement_approved",
            "requirement": {"id": req["id"], "version": req["current_version"],
                            "reason": db.requirement_versions(req["id"])[-1]["reason"], **req["content"]},
            "links": result["links"],
            "under_review": result["under_review"],
        }
        for topic in topics:
            exp = Expert.load(self.ctx, slug(topic))
            if not exp:
                if self.ctx.mode == "in_meeting":
                    _record(self.ctx, LEADER, REGISTRY, "pending", {"motive": "approved requirement in a topic without an expert", "topic": topic})
                    continue
                # A new expert reads the topic requirements when it is born, including this one.
                Expert.create(self.ctx, topic, f"Business rules and requirements about {topic}.", created_by=SYSTEM)
                continue
            exp.update(trigger, f"{req['id']} v{req['current_version']} approved")


def feed_expert(ctx: Context, expert_id: str, document_name: str, text: str) -> int | None:
    """Give a project document (minutes, business rules, glossary) to an expert."""
    exp = Expert.load(ctx, expert_id)
    if not exp:
        raise ValueError(f"expert not found: {expert_id}")
    return exp.update({"kind": "document", "name": document_name, "text": text}, f"document {document_name}")
