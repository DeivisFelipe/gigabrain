"""REST API of GigaBrain for the web application where the PO talks to the system.

Run:  python main.py api               (DeepSeek, port 8000)
      python main.py api --simulated   (no key, rule-based answers)
Interactive docs: http://127.0.0.1:8000/docs   (OpenAPI at /openapi.json)

Conversation flow:

    GET/POST /api/v1/projects, GET/PATCH /api/v1/projects/{project}      the PO's projects

Everything below lives inside a project: /api/v1/projects/{project}/...

    POST   /conversations                         open a conversation -> {id}
    POST   /conversations/{id}/messages           the PO writes (JSON, or a form with attached files)
    POST   /conversations/{id}/meeting            the PO sends a meeting, or the next part of it
    POST   /conversations/{id}/approve            approve the pending drafts (all, or some)
    POST   /conversations/{id}/drafts/{d}/approve approve one draft
    DELETE /conversations/{id}/drafts/{d}         reject one draft
    POST   /conversations/{id}/drafts/{d}/restore undo a rejection
    PATCH  /conversations/{id}                    rename the conversation
    POST   /conversations/{id}/close              close it (it stops accepting messages)
    DELETE /conversations/{id}                    delete a conversation that saved nothing

Every answer brings the drafts of the conversation: what the Twin proposed or read from a
meeting and the PO has not approved yet (D1, D2...). Approving saves a draft in the
Repository and the conversation goes on: the PO may keep talking, send more of the meeting
and approve again. Only closing ends it.

History (read only):

    GET  /conversations[/{id}[/events]]     conversations, messages, drafts and behind the scenes
    GET  /requirements[/{id}]               requirements, versions, links, sources, checks and validation
    GET  /links, /experts[/{id}]

An open conversation lives in the server's memory (the Twin keeps its history). If the API
restarts, it is brought back from the log on the next message.
"""

from __future__ import annotations

import os
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Callable, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from . import documents, epics, knowledge, requirement_format
from .council import Council, open_context
from .database import Database
from .log import summary as message_summary
from .messages import PO, TWIN, Message
from .projects import ProjectError, Projects
from .validation_read import validation_of

# Messages the PO sees on screen; the rest happens behind the scenes (agents talking).
CONVERSATION_KINDS = ("po_request", "meeting", "approval", "question_to_po", "proposal", "confirmation")
CONVERSATION_MODES = ("after_meeting", "in_meeting")
# Longest PO message, in characters. A whole meeting of the dataset has up to ~21 thousand,
# so the default fits two long meetings pasted at once.
MAX_MESSAGE_CHARS = int(os.environ.get("GIGABRAIN_MAX_MESSAGE_CHARS", "50000"))
# A meeting is read one utterance at a time, so it may be longer than a message.
MAX_MEETING_CHARS = int(os.environ.get("GIGABRAIN_MAX_MEETING_CHARS", "200000"))
MAX_TITLE_CHARS = 80


# ---------------------------------------------------------------------------- models

class NewProject(BaseModel):
    name: str = Field(..., min_length=1, max_length=80, description="Name of the project.")
    description: str = Field("", max_length=2000, description="What the project is about (optional).")


class ProjectChanges(BaseModel):
    name: str | None = Field(None, max_length=80)
    description: str | None = Field(None, max_length=2000)


class NewConversation(BaseModel):
    mode: Literal["after_meeting", "in_meeting"] = Field(
        "after_meeting", description="after_meeting: may create new experts. in_meeting: faster, only uses existing experts.")
    title: str | None = Field(None, max_length=MAX_TITLE_CHARS, description="Name of the conversation (optional).")


class ConversationChanges(BaseModel):
    title: str | None = Field(..., max_length=MAX_TITLE_CHARS, description="New name; empty or null removes it.")


class NewMessage(BaseModel):
    text: str = Field(..., min_length=1, max_length=MAX_MESSAGE_CHARS,
                      description=f"What the PO wrote (up to {MAX_MESSAGE_CHARS} characters).",
                      examples=["I want to notify the customer when the payment is confirmed"])


class NewMeeting(BaseModel):
    text: str = Field(..., min_length=1, max_length=MAX_MEETING_CHARS,
                      description="The transcript, one utterance per line: 'Speaker: what was said'. "
                                  "Lines without a speaker continue the utterance above.",
                      examples=["Analyst: How should login work?\nClient: Users must log in with their email."])
    title: str | None = Field(None, max_length=MAX_TITLE_CHARS, description="Name of the meeting (optional).")


class ApproveDrafts(BaseModel):
    draft_ids: list[str] | None = Field(None, description="Which drafts to approve (e.g. [\"D1\", \"D3\"]). Null: all pending.")


class BehindTheScenes(BaseModel):
    id: int
    timestamp: str
    sender: str
    recipient: str
    kind: str
    summary: str


class TwinAnswer(BaseModel):
    action: Literal["ask", "propose", "approved"] = Field(
        ..., description="ask: the Twin needs more information. propose: there are drafts for the PO to review. "
                         "approved: drafts were saved (the conversation goes on).")
    message: str = Field(..., description="Text to show to the PO.")
    proposed_requirements: list[dict] = Field(
        default_factory=list,
        description="The pending drafts: title, statement (EARS), story, acceptance_criteria (Given/When/Then), "
                    "class, subtype, topics, turns, base, links, draft_id, 'quality' (automatic checks), "
                    "'sources' (experts and utterances that contributed) and 'validation' (the Critic's scores).")
    drafts: list[dict] = Field(default_factory=list, description="Every draft of the conversation, with its status: "
                                                                 "pending, approved (saved_as: R<n>) or rejected.")
    validation: dict | None = Field(
        None, description="With action=propose from a message: the Critic's report on the proposal taken together with "
                          "the repository. Null when validation is off.")
    saved: list[str] = Field(default_factory=list, description="With action=approved: saved ids (e.g. R3).")
    pending_proposal: bool = Field(..., description="Whether drafts are waiting for approval (enables the Approve buttons).")
    conversation_active: bool
    behind_the_scenes: list[BehindTheScenes] = Field(default_factory=list, description="What the agents did to reach this answer.")


# ---------------------------------------------------------------------------- state

@dataclass
class ActiveConversation:
    council: Council
    key: str = ""
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


def _contributions(sources: list[dict]) -> list[dict]:
    """Who contributed to a requirement: the experts consulted, and whether one of them changed it."""
    return [{"expert_id": s["ref"], "knowledge_version": s.get("version"), "action": s.get("action"),
             "edited": s.get("action") == "fix"} for s in sources if s.get("kind") == "expert"]


def _requirement_summary(r: dict) -> dict:
    c = r["content"]
    sources = r.get("sources") or []
    return {
        "id": r["id"], "title": r["title"], "status": r["status"], "current_version": r["current_version"],
        "kind": r["kind"], "topics": r["topics"], "statement": c.get("statement"), "story": c.get("story"),
        "acceptance_criteria": len(c.get("acceptance_criteria") or []), "class": c.get("class"),
        "subtype": c.get("subtype"), "created_at": r["created_at"], "updated_at": r["updated_at"],
        "quality": requirement_format.check(c), "sources": sources, "experts": _contributions(sources),
    }


def _board_from_log(events: list[dict]) -> list[dict]:
    """The drafts as the last board in the log left them (a conversation not in memory)."""
    boards = [e["content"].get("drafts") or [] for e in events if e["kind"] == "draft_board"]
    return [{**d["requirement"], "draft_id": d["draft_id"], "status": d["status"], "origin": d["origin"],
             "saved_as": d.get("saved_as"), "sources": d.get("sources", []), "quality": requirement_format.check(d["requirement"])}
            for d in (boards[-1] if boards else [])]


def _expert_consultations(events: list[dict]) -> dict[str, int]:
    """How many times each expert answered in a conversation."""
    counts: dict[str, int] = {}
    for e in events:
        if e["kind"] == "expert_answer" and e["sender"].startswith("expert:"):
            expert_id = e["sender"].split(":", 1)[1]
            counts[expert_id] = counts.get(expert_id, 0) + 1
    return counts


# ---------------------------------------------------------------------------- application

def create_app(data_folder: str, create_provider: Callable[..., object], origins: list[str] | None = None,
               token: str | None = None, validation=True) -> FastAPI:
    active: dict[str, ActiveConversation] = {}
    active_lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(_app):
        yield
        for key in list(active):  # on shutdown, close the connections of open conversations
            finish(key)

    app = FastAPI(lifespan=lifespan, title="GigaBrain API", version="1.1.0",
                  description="Conversation of the PO with the Digital Twin and history of the Requirements Repository.")
    app.add_middleware(CORSMiddleware, allow_origins=origins or ["*"], allow_methods=["*"], allow_headers=["*"])

    def authorize(authorization: str | None = Header(None)) -> None:
        if token and authorization != f"Bearer {token}":
            raise HTTPException(401, "Missing or invalid token (header Authorization: Bearer <token>).")

    projects = Projects(data_folder)

    def folder_of(project_id: str) -> str:
        try:
            return projects.folder(project_id)
        except ProjectError as exc:
            raise HTTPException(404, str(exc)) from exc

    def get_db(project_id: str):
        db = Database(folder_of(project_id))
        try:
            yield db
        finally:
            db.close()

    protected = [Depends(authorize)]

    def provider_for(x_llm_key: str | None):
        try:
            return create_provider(x_llm_key.strip()) if x_llm_key and x_llm_key.strip() else create_provider()
        except Exception as exc:
            raise HTTPException(400, str(exc))

    def get_active(project_id: str, conversation_id: str, x_llm_key: str | None = None) -> ActiveConversation:
        """The conversation in memory, or brought back from the log if the API restarted."""
        key = f"{project_id}:{conversation_id}"
        with active_lock:
            session = active.get(key)
        if session:
            return session
        folder = folder_of(project_id)
        db = Database(folder)
        try:
            conversation = db.get_conversation(conversation_id)
        finally:
            db.close()
        if not conversation:
            raise HTTPException(409, "This conversation does not exist. Open a new one.")
        if conversation["status"] == "closed" or conversation["mode"] not in CONVERSATION_MODES:
            raise HTTPException(409, "This conversation is closed. Open a new one.")
        council = Council.resume(open_context(folder, provider_for(x_llm_key), mode=conversation["mode"],
                                              validation=validation), conversation_id)
        with active_lock:
            session = active.setdefault(key, ActiveConversation(council, key))
        return session

    def finish(key: str) -> None:
        with active_lock:
            session = active.pop(key, None)
        if session:
            session.council.ctx.db.close()

    def reply(session: ActiveConversation, answer: dict, since: int) -> dict:
        ctx = session.council.ctx
        events = [e for e in ctx.db.list_events(ctx.conversation_id, limit=100000) if e["id"] > since]
        board = session.council.board()
        return {
            "action": answer["action"],
            "message": answer.get("message_to_po", ""),
            "proposed_requirements": [d for d in board if d["status"] == "pending"],
            "drafts": board,
            "validation": answer.get("validation") if answer["action"] == "propose" else None,
            "saved": [s["requirement_id"] for s in answer.get("saved", [])],
            "pending_proposal": bool(session.council.pending_proposal),
            "conversation_active": True,
            "behind_the_scenes": [_behind(e) for e in events if e["kind"] not in CONVERSATION_KINDS],
        }

    def last_event(session: ActiveConversation) -> int:
        row = session.council.ctx.db.con.execute(
            "SELECT COALESCE(MAX(id), 0) FROM event WHERE conversation_id = ?", (session.council.ctx.conversation_id,)).fetchone()
        return row[0]

    def run(session: ActiveConversation, work: Callable[[], dict]) -> dict:
        """One thing at a time per conversation; an LLM failure leaves the conversation usable."""
        with session.lock:
            since = last_event(session)
            try:
                answer = work()
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(502, f"The language model failed: {exc}") from exc
            return reply(session, answer, since)

    async def read_message(request: Request) -> str:
        """The PO's text, from JSON or from a form with attached files (their text goes after it)."""
        if request.headers.get("content-type", "").startswith("multipart/form-data"):
            form = await request.form()
            text = str(form.get("text") or "")
            uploads = [f for f in form.getlist("files") if hasattr(f, "read")]
            if len(uploads) > documents.MAX_FILES:
                raise HTTPException(422, f"At most {documents.MAX_FILES} files per message.")
            files = []
            for upload in uploads:
                try:
                    content = documents.text_of(upload.filename or "file", await upload.read())
                except documents.DocumentError as exc:
                    raise HTTPException(422, str(exc)) from exc
                if not content:
                    raise HTTPException(422, f"{upload.filename}: no text could be read (a scanned PDF?).")
                files.append((upload.filename or "file", content))
            text = documents.with_attachments(text, files)
        else:
            try:
                text = NewMessage.model_validate(await request.json()).text
            except (ValidationError, ValueError) as exc:
                raise HTTPException(422, f"Invalid body: {exc}") from exc
        text = text.strip()
        if not text:
            raise HTTPException(422, "The message is empty.")
        if len(text) > MAX_MESSAGE_CHARS:
            raise HTTPException(422, f"The message has {len(text)} characters; the limit is {MAX_MESSAGE_CHARS}. "
                                     "To send a whole meeting, use the meeting route.")
        return text

    # ------------------------------------------------------------------ health

    @app.get("/api/v1/health", tags=["system"])
    def health() -> dict:
        try:
            provider = getattr(create_provider(), "name", "?")
        except Exception:  # no key on the server: the app sends its own when it opens a conversation
            provider = None
        return {"status": "ok", "provider": provider, "needs_key": provider is None, "active_conversations": len(active),
                "limits": {"message_chars": MAX_MESSAGE_CHARS, "meeting_chars": MAX_MEETING_CHARS,
                           "files_per_message": documents.MAX_FILES, "file_bytes": documents.MAX_FILE_BYTES,
                           "file_types": list(documents.EXTENSIONS)}}

    # ------------------------------------------------------------------ projects

    @app.get("/api/v1/projects", tags=["projects"], dependencies=protected)
    def list_projects() -> list[dict]:
        return projects.list()

    @app.post("/api/v1/projects", tags=["projects"], status_code=201, dependencies=protected)
    def create_project(data: NewProject) -> dict:
        """Create a project. Its requirements, conversations and experts are kept apart from the other projects'."""
        try:
            return projects.create(data.name, data.description)
        except ProjectError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/v1/projects/{project_id}", tags=["projects"], dependencies=protected)
    def get_project(project_id: str) -> dict:
        folder_of(project_id)
        return projects.get(project_id)

    @app.patch("/api/v1/projects/{project_id}", tags=["projects"], dependencies=protected)
    def update_project(project_id: str, data: ProjectChanges) -> dict:
        folder_of(project_id)
        try:
            return projects.update(project_id, data.name, data.description)
        except ProjectError as exc:
            raise HTTPException(422, str(exc)) from exc

    # ------------------------------------------------------------------ conversation

    @app.post("/api/v1/projects/{project_id}/conversations", tags=["conversation"], status_code=201, dependencies=protected)
    def open_conversation(project_id: str, data: NewConversation | None = None, x_llm_key: str | None = Header(None)) -> dict:
        """Open a conversation of the PO with the Digital Twin. The optional X-LLM-Key header carries the
        provider key for this conversation only (it is never stored); without it the server's own key is used."""
        data = data or NewConversation()
        council = Council(open_context(folder_of(project_id), provider_for(x_llm_key), mode=data.mode, validation=validation))
        conversation_id = council.start()
        title = (data.title or "").strip() or None
        if title:
            council.ctx.db.rename_conversation(conversation_id, title)
        with active_lock:
            active[f"{project_id}:{conversation_id}"] = ActiveConversation(council, f"{project_id}:{conversation_id}")
        return {"id": conversation_id, "mode": data.mode, "status": "open", "title": title, "active": True}

    @app.post("/api/v1/projects/{project_id}/conversations/{conversation_id}/messages", tags=["conversation"], response_model=TwinAnswer,
              dependencies=protected, openapi_extra={"requestBody": {"content": {
                  "application/json": {"schema": NewMessage.model_json_schema()},
                  "multipart/form-data": {"schema": {"type": "object", "properties": {
                      "text": {"type": "string"}, "files": {"type": "array", "items": {"type": "string", "format": "binary"}}}}},
              }}})
    async def send_message(project_id: str, conversation_id: str, request: Request, x_llm_key: str | None = Header(None)) -> dict:
        """The PO sends a message: JSON {"text": ...} or a form with "text" and up to 5 "files"
        (.txt, .md, .csv, .json, .docx, .pdf), whose text goes to the Twin with the message.
        It may take from a few seconds to ~1 minute: the Twin may consult the Leader and the experts.
        Writing "approved" approves every pending draft."""
        text = await read_message(request)
        session = get_active(project_id, conversation_id, x_llm_key)
        return await run_in_threadpool(run, session, lambda: session.council.talk(text))

    @app.post("/api/v1/projects/{project_id}/conversations/{conversation_id}/meeting", tags=["conversation"], response_model=TwinAnswer,
              dependencies=protected)
    def send_meeting(project_id: str, conversation_id: str, data: NewMeeting, x_llm_key: str | None = Header(None)) -> dict:
        """The PO sends a meeting transcript, or the next part of one. It is read one utterance at a time:
        each requirement becomes a draft after the experts' review; a correction of a pending draft
        changes it. Nothing is saved until the PO approves. Long meetings take several minutes."""
        session = get_active(project_id, conversation_id, x_llm_key)
        return run(session, lambda: session.council.hear_meeting(data.text, (data.title or "").strip() or None))

    @app.post("/api/v1/projects/{project_id}/conversations/{conversation_id}/approve", tags=["conversation"], response_model=TwinAnswer,
              dependencies=protected)
    def approve(project_id: str, conversation_id: str, data: ApproveDrafts | None = None, x_llm_key: str | None = Header(None)) -> dict:
        """Approve the pending drafts: all of them, or only `draft_ids`."""
        session = get_active(project_id, conversation_id, x_llm_key)
        ids = (data or ApproveDrafts()).draft_ids

        def work() -> dict:
            pending = {d["draft_id"] for d in session.council.drafts if d["status"] == "pending"}
            if not pending or (ids is not None and not set(ids) & pending):
                raise HTTPException(409, "There is no pending draft to approve.")
            session.council.ctx.recorder.record(Message(PO, TWIN, "approval", {
                "text": "approved (button)" if ids is None else f"approved (button): {', '.join(ids)}"},
                session.council.ctx.conversation_id))
            return session.council.approve(ids)
        return run(session, work)

    @app.post("/api/v1/projects/{project_id}/conversations/{conversation_id}/drafts/{draft_id}/approve", tags=["conversation"],
              response_model=TwinAnswer, dependencies=protected)
    def approve_draft(project_id: str, conversation_id: str, draft_id: str, x_llm_key: str | None = Header(None)) -> dict:
        """Approve one draft; the others stay as they are."""
        return approve(project_id, conversation_id, ApproveDrafts(draft_ids=[draft_id]), x_llm_key)

    def change_draft(project_id: str, conversation_id: str, draft_id: str, x_llm_key: str | None, change: str) -> dict:
        session = get_active(project_id, conversation_id, x_llm_key)

        def work() -> dict:
            try:
                getattr(session.council, change)(draft_id)
            except KeyError as exc:
                raise HTTPException(404, f"Draft {draft_id} not found.") from exc
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
            pending = bool(session.council.pending_proposal)
            return {"action": "propose" if pending else "ask", "message_to_po": ""}
        return run(session, work)

    @app.delete("/api/v1/projects/{project_id}/conversations/{conversation_id}/drafts/{draft_id}", tags=["conversation"],
                response_model=TwinAnswer, dependencies=protected)
    def reject_draft(project_id: str, conversation_id: str, draft_id: str, x_llm_key: str | None = Header(None)) -> dict:
        """Reject one pending draft. It stays in the list as rejected and can be restored."""
        return change_draft(project_id, conversation_id, draft_id, x_llm_key, "reject")

    @app.post("/api/v1/projects/{project_id}/conversations/{conversation_id}/drafts/{draft_id}/restore", tags=["conversation"],
              response_model=TwinAnswer, dependencies=protected)
    def restore_draft(project_id: str, conversation_id: str, draft_id: str, x_llm_key: str | None = Header(None)) -> dict:
        """Undo the rejection of a draft: it is pending again."""
        return change_draft(project_id, conversation_id, draft_id, x_llm_key, "restore")

    @app.patch("/api/v1/projects/{project_id}/conversations/{conversation_id}", tags=["conversation"], dependencies=protected)
    def rename_conversation(project_id: str, conversation_id: str, data: ConversationChanges,
                            db: Database = Depends(get_db)) -> dict:
        """Give the conversation a name (or remove it with an empty one)."""
        if not db.get_conversation(conversation_id):
            raise HTTPException(404, "Conversation not found.")
        db.rename_conversation(conversation_id, (data.title or "").strip() or None)
        return {**db.get_conversation(conversation_id), "active": f"{project_id}:{conversation_id}" in active}

    @app.post("/api/v1/projects/{project_id}/conversations/{conversation_id}/close", tags=["conversation"], dependencies=protected)
    def close(project_id: str, conversation_id: str, x_llm_key: str | None = Header(None)) -> dict:
        """Close the conversation: it keeps its history and stops accepting messages."""
        session = get_active(project_id, conversation_id, x_llm_key)
        with session.lock:
            session.council.end("closed")
        finish(session.key)
        return {"id": conversation_id, "status": "closed", "active": False}

    @app.delete("/api/v1/projects/{project_id}/conversations/{conversation_id}", tags=["conversation"], status_code=204,
                response_class=Response, dependencies=protected)
    def delete_conversation(project_id: str, conversation_id: str, db: Database = Depends(get_db)) -> Response:
        """Delete a conversation that saved no requirement (e.g. an empty one). One that saved
        requirements is part of their history: close it instead (409)."""
        if not db.get_conversation(conversation_id):
            raise HTTPException(404, "Conversation not found.")
        if any(e["kind"] == "requirement_saved" for e in db.list_events(conversation_id, limit=100000)):
            raise HTTPException(409, "This conversation saved requirements; it is part of their history and cannot be deleted.")
        finish(f"{project_id}:{conversation_id}")
        db.delete_conversation(conversation_id)
        return Response(status_code=204)

    @app.delete("/api/v1/projects/{project_id}/conversations", tags=["conversation"], dependencies=protected)
    def delete_empty_conversations(project_id: str, db: Database = Depends(get_db)) -> dict:
        """Delete every conversation where the PO never wrote nor sent a meeting."""
        deleted = []
        for c in db.list_conversations():
            if c["mode"] not in CONVERSATION_MODES:
                continue
            kinds = {e["kind"] for e in db.list_events(c["id"], limit=100000)}
            if not kinds & {"po_request", "meeting"}:
                finish(f"{project_id}:{c['id']}")
                db.delete_conversation(c["id"])
                deleted.append(c["id"])
        return {"deleted": deleted}

    # ------------------------------------------------------------------ conversation history

    @app.get("/api/v1/projects/{project_id}/conversations", tags=["history"], dependencies=protected)
    def list_conversations(project_id: str, only_po: bool = Query(True, description="false also returns meetings processed in batch"),
                           db: Database = Depends(get_db)) -> list[dict]:
        conversations = db.list_conversations()
        if only_po:
            conversations = [c for c in conversations if c["mode"] in CONVERSATION_MODES]
        return [{**c, "active": c["status"] != "closed" and c["mode"] in CONVERSATION_MODES,
                 "in_memory": f"{project_id}:{c['id']}" in active} for c in conversations]

    @app.get("/api/v1/projects/{project_id}/conversations/{conversation_id}", tags=["history"], dependencies=protected)
    def get_conversation(project_id: str, conversation_id: str, db: Database = Depends(get_db)) -> dict:
        """The conversation as the PO sees it: their messages, the Twin's answers and the drafts."""
        conversation = db.get_conversation(conversation_id)
        if not conversation:
            raise HTTPException(404, "Conversation not found.")
        events = db.list_events(conversation_id, limit=100000)
        session = active.get(f"{project_id}:{conversation_id}")
        drafts = session.council.board() if session else _board_from_log(events)
        return {
            **conversation,
            "events": len(events),
            "active": conversation["status"] != "closed" and conversation["mode"] in CONVERSATION_MODES,
            "pending_proposal": any(d["status"] == "pending" for d in drafts),
            "drafts": drafts,
            "messages": [_conversation_message(e) for e in events if e["kind"] in CONVERSATION_KINDS],
            "saved_requirements": [e["content"]["requirement_id"] for e in events if e["kind"] == "requirement_saved"],
            "expert_consultations": _expert_consultations(events),
        }

    @app.get("/api/v1/projects/{project_id}/conversations/{conversation_id}/events", tags=["history"], dependencies=protected)
    def conversation_events(project_id: str, conversation_id: str, since: int = Query(0, description="only events with a larger id"),
                            full: bool = Query(False, description="true includes the full JSON of each message"),
                            db: Database = Depends(get_db)) -> list[dict]:
        """Behind the scenes: every JSON message between the agents, in order."""
        events = [e for e in db.list_events(conversation_id, limit=100000) if e["id"] > since]
        return [_behind(e) | ({"content": e["content"]} if full else {}) for e in events]

    # ------------------------------------------------------------------ requirements

    @app.get("/api/v1/projects/{project_id}/requirements", tags=["history"], dependencies=protected)
    def list_requirements(project_id: str, status: str | None = Query(None, description="active, under_review, superseded, split, merged"),
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

    @app.get("/api/v1/projects/{project_id}/requirements/{requirement_id}", tags=["history"], dependencies=protected)
    def get_requirement(project_id: str, requirement_id: str, db: Database = Depends(get_db)) -> dict:
        """Current content, all versions, links, sources, quality checks and the trace ("how did we get here")."""
        req = db.get_requirement(requirement_id)
        if not req:
            raise HTTPException(404, "Requirement not found.")
        return {**req, "quality": requirement_format.check(req["content"]), "validation": validation_of(db, requirement_id),
                "experts": _contributions(req.get("sources") or []),
                "versions": db.requirement_versions(requirement_id),
                "links": db.list_links(requirement_id), "trace": db.trace(requirement_id)}

    @app.get("/api/v1/projects/{project_id}/links", tags=["history"], dependencies=protected)
    def list_links(project_id: str, db: Database = Depends(get_db)) -> list[dict]:
        return db.list_links()

    @app.get("/api/v1/projects/{project_id}/quality", tags=["history"], dependencies=protected)
    def format_report(project_id: str, db: Database = Depends(get_db)) -> dict:
        """How the active requirements follow the structure: EARS statement, story (FR) or
        constraint card (NFR), Given/When/Then criteria and no vague terms."""
        return requirement_format.summarize(db.list_requirements(only_active=True))

    # ------------------------------------------------------------------ epics

    @app.get("/api/v1/projects/{project_id}/epics", tags=["history"], dependencies=protected)
    def list_epics(project_id: str, db: Database = Depends(get_db)) -> list[dict]:
        """The requirements grouped by theme: each epic with its user stories (FR) and constraint cards (NFR)."""
        return epics.view(db)

    @app.post("/api/v1/projects/{project_id}/epics", tags=["history"], dependencies=protected)
    def organize_epics(project_id: str, x_llm_key: str | None = Header(None)) -> list[dict]:
        """Ask the Twin to group the active requirements into epics (replaces the previous grouping)."""
        ctx = open_context(folder_of(project_id), provider_for(x_llm_key), validation=False)
        try:
            epics.organize(ctx)
            return epics.view(ctx.db)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(502, f"The language model failed: {exc}") from exc
        finally:
            ctx.db.close()

    # ------------------------------------------------------------------ experts

    @app.get("/api/v1/projects/{project_id}/experts", tags=["history"], dependencies=protected)
    def list_experts(project_id: str, db: Database = Depends(get_db)) -> list[dict]:
        """Every expert, with how many times it was consulted ("consultations")."""
        return db.list_experts()

    @app.get("/api/v1/projects/{project_id}/experts/{expert_id}", tags=["history"], dependencies=protected)
    def get_expert(project_id: str, expert_id: str, db: Database = Depends(get_db)) -> dict:
        """The current knowledge file and all its versions."""
        exp = db.get_expert(expert_id)
        if not exp:
            raise HTTPException(404, "Expert not found.")
        return {**exp, "knowledge": knowledge.read(exp["file"]), "versions": db.knowledge_versions(expert_id)}

    return app
