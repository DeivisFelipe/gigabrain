"""What every agent shares.

`Context` holds what every agent needs: the database, the log recorder, the LLM
provider, where the knowledge files live, the conversation mode and the id of the
current conversation.

`Agent` gives every agent two shortcuts:
- send(...)     creates a JSON Message and records it in the log
- call_llm(...) calls the LLM and records how long it took
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from ..database import Database
from ..log import Recorder
from ..messages import SYSTEM, Message

MODES = {
    "in_meeting": "during the meeting: fast, only uses experts that already exist",
    "after_meeting": "after the meeting: may create new experts",
}


@dataclass
class Context:
    db: Database
    recorder: Recorder
    provider: object
    knowledge_folder: str
    mode: str = "after_meeting"
    conversation_id: str | None = None
    with_quality: bool = True  # consult the quality expert (FR/NFR class and subtype)


def requirement_summary(req: dict) -> dict:
    """Compact form of a requirement to send to the agents."""
    c = req["content"]
    return {
        "id": req["id"],
        "version": req["current_version"],
        "status": req["status"],
        "title": c.get("title"),
        "statement": c.get("statement"),
        "story": c.get("story"),
        "topics": req["topics"],
    }


def as_json(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=1)


class Agent:
    name = "agent"

    def __init__(self, ctx: Context):
        self.ctx = ctx

    def send(self, recipient: str, kind: str, content: dict) -> Message:
        msg = Message(sender=self.name, recipient=recipient, kind=kind, content=content, conversation_id=self.ctx.conversation_id)
        return self.ctx.recorder.record(msg)

    def call_llm(self, role: str, system: str, messages: list[dict]) -> dict:
        start = time.perf_counter()
        try:
            answer = self.ctx.provider.complete_json(role, system, messages)
        except Exception as exc:
            self.send(SYSTEM, "error", {"role": role, "error": str(exc)})
            raise
        self.send("llm", "llm_call", {
            "role": role,
            "provider": getattr(self.ctx.provider, "name", "?"),
            "duration_ms": round((time.perf_counter() - start) * 1000),
            **getattr(self.ctx.provider, "last_usage", {}),
        })
        return answer
