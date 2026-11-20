"""Runs a whole conversation between the PO and GigaBrain.

Flow of one PO message:

    PO ──message──▶ Twin ──(consult?)──▶ Leader ──▶ Experts
                      ▲                      │
                      └── consolidated answer┘
                      │
                      ├─ ask ─────▶ PO
                      └─ propose ─▶ Critic ──(a competency failed: back to the Twin)
                                      │
                                      └─ drafts + validation report ─▶ PO

The PO may also send a meeting (or the next part of one). It is read one utterance at a time,
as in the evaluation (meeting.py): the Twin drafts, the expert of the topic and the quality
expert recommend, and the Twin votes. What it keeps becomes a draft; nothing is saved yet.

Drafts (D1, D2...) are the working list of the conversation. The PO changes them by talking
("in D3 the limit is 5"), by sending more of the meeting, or one by one: approve, reject,
restore. Approving saves a draft in the Repository and the conversation goes on, so the PO can
keep talking, send more meetings and approve again.

When a draft is approved:
1. it is saved (new requirement or new version), with its sources (conversation, utterances,
   experts consulted, the Critic's report) and links
2. requirements that depended on one that changed become "under_review"
3. the experts of the topics involved update their knowledge file
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from . import requirement_format, transcript
from .agents import Context, DigitalTwin, Expert, Leader
from .agents.base import as_json
from .agents.twin import DRAFTS_NOTE
from .approval import is_approval
from .database import Database, slug
from .language import phrase, spoken_language
from .log import Recorder
from .messages import LEADER, PO, REGISTRY, REPOSITORY, SYSTEM, TWIN, Message

if TYPE_CHECKING:
    from .agents.critic import ValidationSettings

MAX_CONSULTATIONS_PER_MESSAGE = 2
REQUIREMENT_FIELDS = ("title", "statement", "ears_pattern", "story", "constraint", "acceptance_criteria", "class", "subtype", "kind", "topics",
                      "turns")
DRAFT_STATUSES = ("pending", "approved", "rejected")
# What the Twin sees of each draft: enough to change it, short enough for a long list.
DRAFT_SUMMARY = ("draft_id", "title", "statement", "class", "subtype", "topics", "origin")


def open_context(data_folder: str, provider, mode: str = "after_meeting", show_agents: bool = False,
                 validation: ValidationSettings | bool = True) -> Context:
    """`validation`: True for the Critic with its defaults, settings to adjust it, False to run without it."""
    os.makedirs(data_folder, exist_ok=True)
    db = Database(data_folder)
    recorder = Recorder(db, os.path.join(data_folder, "logs"), show_agents=show_agents)
    ctx = Context(db, recorder, provider, os.path.join(data_folder, "knowledge"), mode)
    if validation:
        from .agents.critic import Critic, ValidationSettings  # here so that an error in validation/ cannot stop a run without it
        ctx.critic = Critic(ctx, validation if isinstance(validation, ValidationSettings) else None)
    return ctx


def _record(ctx: Context, sender: str, recipient: str, kind: str, content: dict) -> None:
    ctx.recorder.record(Message(sender=sender, recipient=recipient, kind=kind, content=content, conversation_id=ctx.conversation_id))


class Council:
    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.twin = DigitalTwin(ctx)
        self.leader = Leader(ctx)
        self.drafts: list[dict] = []
        self.turns: list[dict] = []  # every utterance of the meetings sent in this conversation
        self.sources: list[dict] = []
        self.related: list[str] = []

    def start(self) -> str:
        self.ctx.conversation_id = self.ctx.db.start_conversation(self.ctx.mode)
        return self.ctx.conversation_id

    def end(self, status: str = "closed") -> None:
        if self.ctx.conversation_id:
            self.ctx.db.end_conversation(self.ctx.conversation_id, status)

    @classmethod
    def resume(cls, ctx: Context, conversation_id: str) -> Council:
        """Bring back a conversation from the log (e.g. after the API restarted).

        The drafts come from the last board, the utterances from the meetings sent, and the
        Twin gets back the messages it exchanged with the PO.
        """
        council = cls(ctx)
        ctx.conversation_id = conversation_id
        for event in ctx.db.list_events(conversation_id, limit=100000):
            c = event["content"]
            if event["kind"] == "po_request":
                council.twin.history.append({"role": "user", "content": as_json({"from": "po", "text": c.get("text", "")})})
                council.twin.language = spoken_language(c.get("text", "")) or council.twin.language
            elif event["kind"] in ("question_to_po", "proposal") and event["sender"] == TWIN:
                action = "propose" if event["kind"] == "proposal" else "ask"
                council.twin.history.append({"role": "assistant", "content": as_json({"action": action, "message_to_po": c.get("text", "")})})
            elif event["kind"] == "meeting":
                council.turns += c.get("turns") or []
            elif event["kind"] == "draft_board":
                council.drafts = c.get("drafts") or []
        return council

    # ------------------------------------------------------------------ drafts

    @property
    def pending_proposal(self) -> list[dict] | None:
        """The requirements waiting for the PO's approval (None when there are none)."""
        pending = [d["requirement"] for d in self.drafts if d["status"] == "pending"]
        return pending or None

    def board(self) -> list[dict]:
        """Every draft of the conversation, as the PO sees it."""
        return [{**d["requirement"], "draft_id": d["draft_id"], "status": d["status"], "origin": d["origin"],
                 "saved_as": d.get("saved_as"), "sources": d.get("sources", []),
                 "quality": requirement_format.check(d["requirement"])} for d in self.drafts]

    def _draft(self, draft_id: str | None) -> dict | None:
        return next((d for d in self.drafts if d["draft_id"] == draft_id), None)

    def _put(self, requirement: dict, origin: str, sources: list[dict], draft_id: str | None = None) -> tuple[str, bool]:
        """Add a draft, or change a pending one. Returns (draft id, whether it changed an existing one)."""
        existing = self._draft(draft_id)
        if existing and existing["status"] == "pending":
            requirement["draft_id"] = existing["draft_id"]
            existing["requirement"] = requirement
            existing["sources"] = existing["sources"] + [s for s in sources if s not in existing["sources"]]
            existing["revision"] = existing.get("revision", 1) + 1
            return existing["draft_id"], True
        new_id = f"D{len(self.drafts) + 1}"
        requirement["draft_id"] = new_id
        self.drafts.append({"draft_id": new_id, "status": "pending", "origin": origin, "requirement": requirement,
                            "sources": sources, "saved_as": None, "revision": 1})
        return new_id, False

    def _record_board(self) -> None:
        _record(self.ctx, TWIN, PO, "draft_board", {"drafts": self.drafts})

    def _for_the_twin(self) -> list[dict]:
        return [{k: d["requirement"].get(k) for k in DRAFT_SUMMARY} | {"draft_id": d["draft_id"], "origin": d["origin"]}
                for d in self.drafts if d["status"] == "pending"]

    def reject(self, draft_id: str) -> dict:
        return self._set_status(draft_id, "pending", "rejected")

    def restore(self, draft_id: str) -> dict:
        """Undo a rejection: the draft is pending again."""
        return self._set_status(draft_id, "rejected", "pending")

    def _set_status(self, draft_id: str, before: str, after: str) -> dict:
        draft = self._draft(draft_id)
        if not draft:
            raise KeyError(draft_id)
        if draft["status"] != before:
            raise ValueError(f"{draft_id} is {draft['status']}, not {before}")
        draft["status"] = after
        self._record_board()
        return draft

    # ------------------------------------------------------------------ conversation

    def talk(self, text: str) -> dict:
        """Receive a PO message and return what to show to the PO.

        Returns {"action": "ask"|"propose"|"approved", "message_to_po": ..., "requirements": [...]}
        """
        if self.pending_proposal and is_approval(text):
            _record(self.ctx, PO, TWIN, "approval", {"text": text})
            return self.approve()

        _record(self.ctx, PO, TWIN, "po_request", {"text": text})
        decision = self.twin.hear_po(text, self._for_the_twin())

        consultations = 0
        while decision["action"] == "consult":
            if consultations >= MAX_CONSULTATIONS_PER_MESSAGE:
                decision = self.twin.hear_system("Consultation limit reached for this message. Ask the PO or propose with what you know.")
                if decision["action"] == "consult":
                    decision = {"action": "ask", "message_to_po": decision.get("message_to_po", ""), "requirements": []}
                break
            query = decision.get("query") or {}
            answer = self.leader.consult(query.get("question") or text, query.get("topics") or [], self.twin.language)
            self._keep_sources(answer)
            consultations += 1
            decision = self.twin.hear_leader(answer)

        # Only a proposal changes the drafts: if the Twin answers a question in between,
        # the drafts stay as they were and can still be approved.
        if decision["action"] == "propose":
            sources = [*self.sources, *({"kind": "requirement", "ref": rid} for rid in self.related)]
            for requirement in decision["requirements"]:
                self._put(requirement, "chat", list(sources), requirement.get("draft_id"))
            for draft_id in decision.get("remove_drafts") or []:
                draft = self._draft(draft_id)
                if draft and draft["status"] == "pending":
                    draft["status"] = "rejected"
            self._record_board()
        return decision

    def _keep_sources(self, answer: dict) -> None:
        for source in (answer.get("sources") or []):
            if source not in self.sources:
                self.sources.append(source)
        for rid in (answer.get("related_requirements") or []):
            if rid not in self.related:
                self.related.append(rid)

    # ------------------------------------------------------------------ meeting

    def hear_meeting(self, text: str, title: str | None = None) -> dict:
        """Read a meeting (or its next part) one utterance at a time and turn it into drafts.

        As in the evaluation: the Twin drafts, the Consultative Council recommends, the Critic
        scores and the Twin votes. What the Twin keeps becomes a draft for the PO to approve; a
        draft that corrects a pending one changes it instead of creating another.
        """
        first = len(self.turns)
        new_turns = transcript.parse(text, start=first + 1)
        part = sum(1 for e in self.ctx.db.list_events(self.ctx.conversation_id, limit=100000) if e["kind"] == "meeting") + 1
        language = spoken_language(" ".join(t["text"] for t in new_turns[:40])) or self.twin.language
        self.twin.language = language or self.twin.language
        _record(self.ctx, PO, TWIN, "meeting", {"text": f"{title or 'Meeting'} (part {part}): {len(new_turns)} utterances",
                                                 "title": title, "part": part, "turns": new_turns})
        self.turns += new_turns
        code = {"English": "en", "Portuguese": "pt"}.get(language or "", "en")
        project = {"id": self.ctx.conversation_id, "title": title or "Meeting", "language": code, "turns": self.turns}
        utterances = {t["id"]: t["text"] for t in self.turns}
        critic = self.ctx.critic
        new, changed, discarded = [], [], 0

        for i in range(first, len(self.turns)):
            turn = self.turns[i]
            if transcript.leads_the_meeting(turn["speaker"]):  # the analyst only leads; it is context
                continue
            _record(self.ctx, SYSTEM, TWIN, "utterance", {"turn": turn["id"], "speaker": turn["speaker"], "text": turn["text"]})
            for draft in self.twin.draft(project, i, self._known(), len(self.drafts), DRAFTS_NOTE):
                cited = {t: utterances[t] for t in draft["turns"] if t in utterances}
                if critic:  # gate of the Twin: the five item competencies of its draft
                    draft = critic.gate_draft(self.twin, draft, project)
                self.twin.send(LEADER, "query", {"question": f"draft from {turn['id']}: {draft['statement'][:100]}", "draft": draft})
                recommendation = self.leader.consult_draft(draft, cited, code)
                guidance = None
                if critic:
                    recommendation = critic.gate_recommendation(self.leader, draft, recommendation, cited, project)
                    guidance = critic.guide(draft, recommendation, project)
                final = self.twin.decide(draft, recommendation, cited, code, guidance)
                if not final["save"]:
                    discarded += 1
                    continue
                requirement = {**requirement_format.normalize({**draft, "statement": final["statement"], "class": final["class"],
                                                               "subtype": final["subtype"]}),
                               "topics": [draft["topic"]], "turns": draft["turns"], "links": final.get("links") or [],
                               "reason": final.get("motive") or "drafted from the meeting"}
                if guidance:
                    requirement["validation"] = {k: guidance.get(k) for k in ("report_id", "verdict", "passed", "score", "scores",
                                                                              "failed_dimensions") if k in guidance}
                sources = [{"kind": "turn", "ref": t, "meeting": part} for t in draft["turns"]]
                if recommendation.get("expert_id"):
                    sources.append({"kind": "expert", "ref": recommendation["expert_id"],
                                    "version": recommendation.get("knowledge_version"), "action": recommendation.get("action")})
                replaces = draft.get("replaces") if (self._draft(draft.get("replaces")) or {}).get("status") == "pending" else None
                if not replaces and self._repeats_a_draft(requirement["statement"]):
                    discarded += 1  # said again in a later part: the draft is already there
                    continue
                draft_id, was_changed = self._put(requirement, "meeting", sources, replaces)
                (changed if was_changed else new).append(draft_id)

        self._record_board()
        pending = self.pending_proposal or []
        message = phrase("meeting_read", language, utterances=len(new_turns), new=len(new), changed=len(changed),
                         discarded=discarded, pending=len(pending))
        action = "propose" if pending else "ask"
        if pending:
            self.twin.send(PO, "proposal", {"text": message, "requirements": pending})
        else:
            self.twin.send(PO, "question_to_po", {"text": message})
        # The Twin keeps the meeting in its conversation, so the PO can talk about it afterwards.
        self.twin.history.append({"role": "user", "content": as_json({"from": "system", "notice": message})})
        return {"action": action, "message_to_po": message, "requirements": pending, "new": new, "changed": changed,
                "discarded": discarded}

    def _repeats_a_draft(self, statement: str) -> bool:
        from .evaluation import similarity
        return any(similarity(statement, d["requirement"].get("statement") or "") >= 0.9
                   for d in self.drafts if d["status"] == "pending")

    def _known(self) -> list[dict]:
        """What the Twin must not draft again: the Repository and the pending drafts."""
        saved = [{"id": r["id"], "statement": r["content"].get("statement") or r["title"]}
                 for r in self.ctx.db.list_requirements(only_active=True)]
        return saved + [{"id": d["draft_id"], "statement": d["requirement"].get("statement")}
                        for d in self.drafts if d["status"] == "pending"]

    # ------------------------------------------------------------------ approval

    def approve(self, draft_ids: list[str] | None = None) -> dict:
        """Save the pending drafts (all of them, or only `draft_ids`). The conversation goes on."""
        chosen = [d for d in self.drafts if d["status"] == "pending" and (draft_ids is None or d["draft_id"] in draft_ids)]
        if not chosen:
            return {"action": "ask", "message_to_po": phrase("nothing_to_approve", self.twin.language), "requirements": []}

        saved_as = {d["draft_id"]: d["saved_as"] for d in self.drafts if d.get("saved_as")}
        resolve = lambda ref: saved_as.get(ref) if isinstance(ref, str) and ref.startswith("D") else ref
        saved = []
        for draft in chosen:
            proposed = draft["requirement"]
            content = {k: proposed[k] for k in REQUIREMENT_FIELDS if k in proposed}
            sources = [{"kind": "conversation", "ref": self.ctx.conversation_id}, *draft.get("sources", [])]
            if proposed.get("validation") and proposed["validation"].get("report_id"):  # which report of the Critic it was approved with
                sources.append({"kind": "validation", "ref": proposed["validation"]["report_id"],
                                "verdict": proposed["validation"].get("verdict")})
            # A link to another draft only holds once that draft is a requirement.
            links = [{**l, "target": resolve(l.get("target"))} for l in (proposed.get("links") or [])]
            links = [l for l in links if l["target"]]
            result = self.ctx.db.save_requirement(content, self.ctx.conversation_id, base_id=resolve(proposed.get("base")),
                                                  reason=proposed.get("reason"), links=links, sources=sources)
            draft["status"], draft["saved_as"] = "approved", result["requirement_id"]
            saved_as[draft["draft_id"]] = result["requirement_id"]
            _record(self.ctx, TWIN, REPOSITORY, "requirement_saved", {**result, "draft_id": draft["draft_id"],
                                                                      "decided_by": "po (explicit approval)"})
            for rid in result["under_review"]:
                _record(self.ctx, REPOSITORY, TWIN, "requirement_under_review", {"requirement_id": rid, "because_of": result["requirement_id"]})
            if draft["origin"] == "meeting":
                self._note_in_expert(result, proposed)
            else:
                self._update_experts(result)
            saved.append(result)

        text = phrase("saved", self.twin.language, ids=", ".join(f"{s['requirement_id']} v{s['version']}" for s in saved))
        _record(self.ctx, TWIN, PO, "confirmation", {"text": text, "saved": [s["requirement_id"] for s in saved]})
        self._record_board()
        self.ctx.db.set_conversation_status(self.ctx.conversation_id, "approved")
        return {"action": "approved", "message_to_po": text, "saved": saved, "requirements": self.pending_proposal or []}

    def _note_in_expert(self, result: dict, proposed: dict) -> None:
        """A draft read from a meeting: the expert of its topic writes it down without the LLM, as in meeting.py."""
        for topic in proposed.get("topics") or []:
            exp = Expert.load(self.ctx, slug(topic))
            if exp:
                exp.note_requirement({"id": result["requirement_id"], "statement": proposed["statement"],
                                      "class": proposed.get("class"), "reason": proposed.get("reason")},
                                     result["links"], result["under_review"])

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
                Expert.create(self.ctx, topic, f"Business rules and requirements about {topic}.", created_by=SYSTEM,
                              language=self.twin.language)
                continue
            exp.update(trigger, f"{req['id']} v{req['current_version']} approved", self.twin.language)


def feed_expert(ctx: Context, expert_id: str, document_name: str, text: str) -> int | None:
    """Give a project document (minutes, business rules, glossary) to an expert."""
    exp = Expert.load(ctx, expert_id)
    if not exp:
        raise ValueError(f"expert not found: {expert_id}")
    return exp.update({"kind": "document", "name": document_name, "text": text}, f"document {document_name}")
