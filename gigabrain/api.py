"""REST API of GigaBrain for the web application where the PO talks to the system.

Run:  python main.py api               (DeepSeek, port 8000)
      python main.py api --simulated   (no key, rule-based answers)
Interactive docs: http://127.0.0.1:8000/docs   (OpenAPI at /openapi.json)

Conversation flow:

    POST /api/v1/conversations                     open a conversation -> {id}
    POST /api/v1/conversations/{id}/messages       the PO writes -> answer of the Twin
         (a question, a proposal of requirements or a confirmation of what was saved)
    POST /api/v1/conversations/{id}/approve        approve the pending proposal (button)
    POST /api/v1/conversations/{id}/close          close without approving

History (read only):

    GET  /api/v1/conversations[/{id}[/events]]     conversations, messages and behind the scenes
    GET  /api/v1/requirements[/{id}]               requirements, versions, links and trace
    GET  /api/v1/links, /api/v1/experts[/{id}]

Each active conversation lives in the server's memory (the Twin keeps its history). If
the API restarts, open conversations stop accepting messages (409) and the PO opens a
new one; the history is still available.
"""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Callable, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import knowledge, requirement_format
from .council import Council, open_context
from .database import Database
from .log import summary as message_summary
from .messages import PO, TWIN, Message

# Messages the PO sees on screen; the rest happens behind the scenes (agents talking).
CONVERSATION_KINDS = ("po_request", "approval", "question_to_po", "proposal", "confirmation")
CONVERSATION_MODES = ("after_meeting", "in_meeting")


# ---------------------------------------------------------------------------- models

class NewConversation(BaseModel):
    mode: Literal["after_meeting", "in_meeting"] = Field(
        "after_meeting", description="after_meeting: may create new experts. in_meeting: faster, only uses existing experts.")


class NewMessage(BaseModel):
    text: str = Field(..., min_length=1, max_length=8000, description="What the PO wrote.",
                      examples=["I want to notify the customer when the payment is confirmed"])


class BehindTheScenes(BaseModel):
    id: int
    timestamp: str
    sender: str
    recipient: str
    kind: str
    summary: str


class TwinAnswer(BaseModel):
    action: Literal["ask", "propose", "approved"] = Field(
        ..., description="ask: the Twin needs more information. propose: there are requirements for the PO to approve. "
                         "approved: the requirements were saved and the conversation ended.")
    message: str = Field(..., description="Text to show to the PO.")
    proposed_requirements: list[dict] = Field(
        default_factory=list,
        description="With action=propose: title, statement (EARS), story, acceptance_criteria (Given/When/Then), "
                    "class, subtype, topics, base, links, and 'quality' (automatic checks).")
    saved: list[str] = Field(default_factory=list, description="With action=approved: saved ids (e.g. R3).")
    pending_proposal: bool = Field(..., description="Whether a proposal is waiting for approval (enables the Approve button).")
    conversation_active: bool
    behind_the_scenes: list[BehindTheScenes] = Field(default_factory=list, description="What the agents did to reach this answer.")


# ---------------------------------------------------------------------------- state

@dataclass
class ActiveConversation:
    council: Council
    lock: threading.Lock = field(default_factory=threading.Lock)


def _behind(event: dict) -> dict:
    msg = SimpleNamespace(kind=event["kind"], content=event["content"])
    return {k: event[k] for k in ("id", "timestamp", "sender", "recipient", "kind")} | {"summary": message_summary(msg)}


def _with_quality(requirements: list[dict]) -> list[dict]:
    return [{**r, "quality": requirement_format.check(r)} for r in requirements]


def _conversation_message(event: dict) -> dict:
    c = event["content"]
    return {
        "id": event["id"],
        "timestamp": event["timestamp"],
        "author": "po" if event["sender"] == PO else "twin",
        "kind": event["kind"],
        "text": c.get("text", ""),
        "proposed_requirements": _with_quality(c.get("requirements", [])) if event["kind"] == "proposal" else [],
        "saved": c.get("saved", []),
    }


def _requirement_summary(r: dict) -> dict:
    c = r["content"]
    return {
        "id": r["id"], "title": r["title"], "status": r["status"], "current_version": r["current_version"],
        "kind": r["kind"], "topics": r["topics"], "statement": c.get("statement"), "story": c.get("story"),
        "acceptance_criteria": len(c.get("acceptance_criteria") or []), "class": c.get("class"),
        "subtype": c.get("subtype"), "created_at": r["created_at"], "updated_at": r["updated_at"],
    }


# ---------------------------------------------------------------------------- application

def create_app(data_folder: str, create_provider: Callable[[], object], origins: list[str] | None = None,
               token: str | None = None) -> FastAPI:
    active: dict[str, ActiveConversation] = {}
    active_lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(_app):
        yield
        for conversation_id in list(active):  # on shutdown, close the connections of open conversations
            finish(conversation_id)

    app = FastAPI(lifespan=lifespan, title="GigaBrain API", version="1.0.0",
                  description="Conversation of the PO with the Digital Twin and history of the Requirements Repository.")
    app.add_middleware(CORSMiddleware, allow_origins=origins or ["*"], allow_methods=["*"], allow_headers=["*"])

    def authorize(authorization: str | None = Header(None)) -> None:
        if token and authorization != f"Bearer {token}":
            raise HTTPException(401, "Missing or invalid token (header Authorization: Bearer <token>).")

    def get_db():
        db = Database(data_folder)
        try:
            yield db
        finally:
            db.close()

    protected = [Depends(authorize)]

    def get_active(conversation_id: str) -> ActiveConversation:
        with active_lock:
            session = active.get(conversation_id)
        if not session:
            raise HTTPException(409, "This conversation is not active (it was closed, approved or the API restarted). Open a new one.")
        return session

    def finish(conversation_id: str) -> None:
        with active_lock:
            session = active.pop(conversation_id, None)
        if session:
            session.council.ctx.db.close()

    def reply(session: ActiveConversation, answer: dict, since: int) -> dict:
        ctx = session.council.ctx
        events = [e for e in ctx.db.list_events(ctx.conversation_id, limit=5000) if e["id"] > since]
        body = {
            "action": answer["action"],
            "message": answer.get("message_to_po", ""),
            "proposed_requirements": _with_quality(answer.get("requirements", [])) if answer["action"] == "propose" else [],
            "saved": [s["requirement_id"] for s in answer.get("saved", [])],
            "pending_proposal": bool(session.council.pending_proposal),
            "conversation_active": answer["action"] != "approved",
            "behind_the_scenes": [_behind(e) for e in events if e["kind"] not in CONVERSATION_KINDS],
        }
        if answer["action"] == "approved":
            finish(ctx.conversation_id)
        return body

    def last_event(session: ActiveConversation) -> int:
        row = session.council.ctx.db.con.execute(
            "SELECT COALESCE(MAX(id), 0) FROM event WHERE conversation_id = ?", (session.council.ctx.conversation_id,)).fetchone()
        return row[0]

    # ------------------------------------------------------------------ health

    @app.get("/api/v1/health", tags=["system"])
    def health() -> dict:
        return {"status": "ok", "provider": getattr(create_provider(), "name", "?"), "active_conversations": len(active)}

    # ------------------------------------------------------------------ conversation

    @app.post("/api/v1/conversations", tags=["conversation"], status_code=201, dependencies=protected)
    def open_conversation(data: NewConversation | None = None) -> dict:
        """Open a conversation of the PO with the Digital Twin."""
        mode = (data or NewConversation()).mode
        council = Council(open_context(data_folder, create_provider(), mode=mode))
        conversation_id = council.start()
        with active_lock:
            active[conversation_id] = ActiveConversation(council)
        return {"id": conversation_id, "mode": mode, "status": "open", "active": True}

    @app.post("/api/v1/conversations/{conversation_id}/messages", tags=["conversation"], response_model=TwinAnswer,
              dependencies=protected)
    def send_message(conversation_id: str, data: NewMessage) -> dict:
        """The PO sends a message. It may take from a few seconds to ~1 minute: the Twin may consult
        the Leader and the experts before answering. Writing "approved" also approves the pending proposal."""
        session = get_active(conversation_id)
        with session.lock:  # one message at a time per conversation
            since = last_event(session)
            try:
                answer = session.council.talk(data.text.strip())
            except Exception as exc:  # LLM failure: the conversation is still usable
                raise HTTPException(502, f"The language model failed: {exc}") from exc
            return reply(session, answer, since)

    @app.post("/api/v1/conversations/{conversation_id}/approve", tags=["conversation"], response_model=TwinAnswer,
              dependencies=protected)
    def approve(conversation_id: str) -> dict:
        """Approve the pending proposal (same as the PO writing "approved")."""
        session = get_active(conversation_id)
        with session.lock:
            if not session.council.pending_proposal:
                raise HTTPException(409, "There is no pending proposal to approve.")
            since = last_event(session)
            session.council.ctx.recorder.record(
                Message(PO, TWIN, "approval", {"text": "approved (button)"}, session.council.ctx.conversation_id))
            return reply(session, session.council.approve(), since)

    @app.post("/api/v1/conversations/{conversation_id}/close", tags=["conversation"], dependencies=protected)
    def close(conversation_id: str) -> dict:
        """Close the conversation without approving anything."""
        session = get_active(conversation_id)
        with session.lock:
            session.council.end("closed")
        finish(conversation_id)
        return {"id": conversation_id, "status": "closed", "active": False}

    # ------------------------------------------------------------------ conversation history

    @app.get("/api/v1/conversations", tags=["history"], dependencies=protected)
    def list_conversations(only_po: bool = Query(True, description="false also returns meetings processed in batch"),
                           db: Database = Depends(get_db)) -> list[dict]:
        conversations = db.list_conversations()
        if only_po:
            conversations = [c for c in conversations if c["mode"] in CONVERSATION_MODES]
        return [{**c, "active": c["id"] in active} for c in conversations]

    @app.get("/api/v1/conversations/{conversation_id}", tags=["history"], dependencies=protected)
    def get_conversation(conversation_id: str, db: Database = Depends(get_db)) -> dict:
        """The conversation as the PO sees it: their messages and the Twin's answers."""
        conversation = next((c for c in db.list_conversations() if c["id"] == conversation_id), None)
        if not conversation:
            raise HTTPException(404, "Conversation not found.")
        events = db.list_events(conversation_id, limit=5000)
        session = active.get(conversation_id)
        return {
            **conversation,
            "active": session is not None,
            "pending_proposal": bool(session and session.council.pending_proposal),
            "messages": [_conversation_message(e) for e in events if e["kind"] in CONVERSATION_KINDS],
            "saved_requirements": [e["content"]["requirement_id"] for e in events if e["kind"] == "requirement_saved"],
        }

    @app.get("/api/v1/conversations/{conversation_id}/events", tags=["history"], dependencies=protected)
    def conversation_events(conversation_id: str, since: int = Query(0, description="only events with a larger id"),
                            full: bool = Query(False, description="true includes the full JSON of each message"),
                            db: Database = Depends(get_db)) -> list[dict]:
        """Behind the scenes: every JSON message between the agents, in order."""
        events = [e for e in db.list_events(conversation_id, limit=5000) if e["id"] > since]
        return [_behind(e) | ({"content": e["content"]} if full else {}) for e in events]

    # ------------------------------------------------------------------ requirements

    @app.get("/api/v1/requirements", tags=["history"], dependencies=protected)
    def list_requirements(status: str | None = Query(None, description="active, under_review, superseded, split, merged"),
                          topic: str | None = None, search: str | None = Query(None, description="free text"),
                          db: Database = Depends(get_db)) -> list[dict]:
        requirements = [_requirement_summary(r) for r in db.list_requirements()]
        if status:
            requirements = [r for r in requirements if r["status"] == status]
        if topic:
            requirements = [r for r in requirements if topic in r["topics"]]
        if search:
            s = search.lower()
            requirements = [r for r in requirements if s in " ".join([r["id"], r["title"], r["statement"] or "", *r["topics"]]).lower()]
        return requirements

    @app.get("/api/v1/requirements/{requirement_id}", tags=["history"], dependencies=protected)
    def get_requirement(requirement_id: str, db: Database = Depends(get_db)) -> dict:
        """Current content, all versions, links, sources, quality checks and the trace ("how did we get here")."""
        req = db.get_requirement(requirement_id)
        if not req:
            raise HTTPException(404, "Requirement not found.")
        return {**req, "quality": requirement_format.check(req["content"]), "versions": db.requirement_versions(requirement_id),
                "links": db.list_links(requirement_id), "trace": db.trace(requirement_id)}

    @app.get("/api/v1/links", tags=["history"], dependencies=protected)
    def list_links(db: Database = Depends(get_db)) -> list[dict]:
        return db.list_links()

    # ------------------------------------------------------------------ experts

    @app.get("/api/v1/experts", tags=["history"], dependencies=protected)
    def list_experts(db: Database = Depends(get_db)) -> list[dict]:
        return db.list_experts()

    @app.get("/api/v1/experts/{expert_id}", tags=["history"], dependencies=protected)
    def get_expert(expert_id: str, db: Database = Depends(get_db)) -> dict:
        """The current knowledge file and all its versions."""
        exp = db.get_expert(expert_id)
        if not exp:
            raise HTTPException(404, "Expert not found.")
        return {**exp, "knowledge": knowledge.read(exp["file"]), "versions": db.knowledge_versions(expert_id)}

    return app
