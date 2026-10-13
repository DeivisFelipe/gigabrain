"""Messages exchanged between the agents.

Every interaction in GigaBrain is a `Message`: a JSON envelope with the sender,
the recipient, the kind of message and a structured content. Every message is
logged, can be inspected in the dashboard and replayed later.

Example:

    {
      "id": "a1b2c3...",
      "conversation_id": "c-20261004-153000",
      "timestamp": "2026-10-04T15:30:02",
      "sender": "leader",
      "recipient": "expert:payments",
      "kind": "query",
      "content": {"question": "Is there a rule for declined payments?"}
    }
"""

from __future__ import annotations

import datetime
import json
import uuid
from dataclasses import asdict, dataclass, field

# Fixed participants. Experts use "expert:<id>".
PO = "po"
TWIN = "twin"              # Digital Twin (Deliberative Council)
LEADER = "leader"          # Leader (Consultative Council)
SYSTEM = "system"          # input (meeting transcript) and errors
REPOSITORY = "repository"  # Requirements Repository
REGISTRY = "registry"      # Expert Registry (+ knowledge files)


def expert(id_: str) -> str:
    return f"expert:{id_}"


# Message kinds and what each one carries in `content`.
KINDS = {
    "po_request": "PO -> Twin. {text}",
    "question_to_po": "Twin -> PO. {text}",
    "proposal": "Twin -> PO. {text, requirements: [...]}",
    "approval": "PO -> Twin. {text}",
    "confirmation": "Twin -> PO. {text, saved}: what was stored after the approval",
    "query": "Twin -> Leader or Leader -> Expert. {question, ...}",
    "decomposition": "Leader -> Leader. {subqueries: [...]}",
    "expert_answer": "Expert -> Leader. {answer | action, motive, links, ...}",
    "consolidated_answer": "Leader -> Twin. {summary, conflicts, questions_for_po, related_requirements}",
    "expert_created": "Leader -> Registry. {expert_id, topic, description}",
    "pending": "Leader -> Registry. {motive, topic}",
    "requirement_saved": "Twin -> Repository. {requirement_id, version, links, decided_by}",
    "requirement_under_review": "Repository -> Twin. {requirement_id, because_of}",
    "knowledge_updated": "Expert -> Registry. {expert_id, version, motive}",
    "decision": "Twin -> Twin. {draft, save, motive}: the final vote of the Deliberative Council",
    "transcript": "System -> Twin. {project, title, turns}",
    "utterance": "System -> Twin. {turn, speaker, text}: the next utterance of the meeting",
    "drafts": "Twin -> Twin. {turn, requirements: [drafts taken from that utterance]}",
    "llm_call": "Agent -> LLM. {role, duration_ms, tokens?}",
    "error": "Any -> System. {error}",
}


def now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


@dataclass
class Message:
    sender: str
    recipient: str
    kind: str
    content: dict
    conversation_id: str | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: str = field(default_factory=now)

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown message kind: {self.kind}")

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)
