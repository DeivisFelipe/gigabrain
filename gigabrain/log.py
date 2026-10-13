"""Record of everything the agents do.

Every `Message` goes to three places:

1. table `event` in log.db           -> queries and dashboard
2. data/logs/<conversation>.jsonl    -> one JSON line per message, easy to open
3. terminal (if show_agents=True)    -> follow the internal conversation live
"""

from __future__ import annotations

import json
import os

from .database import Database
from .messages import Message

COLORS = {"po": "\033[97m", "twin": "\033[96m", "leader": "\033[95m", "expert": "\033[93m", "system": "\033[90m"}
RESET = "\033[0m"


def _color(participant: str) -> str:
    return COLORS.get(participant.split(":")[0], "")


def summary(msg) -> str:
    """One readable line about what the message says. `msg` needs .kind and .content."""
    c = msg.content
    if msg.kind == "decomposition":
        return "topics: " + ", ".join(f"{s.get('topic')}" for s in c.get("subqueries", []))
    if msg.kind == "expert_created":
        return f"new expert '{c.get('expert_id')}'"
    if msg.kind == "pending":
        return f"{c.get('motive')} ({c.get('topic')})"
    if msg.kind == "knowledge_updated":
        return f"{c.get('expert_id')} v{c.get('version')}: {c.get('motive')}"
    if msg.kind == "llm_call":
        return f"{c.get('role')} in {c.get('duration_ms')} ms"
    if msg.kind == "requirement_saved":
        return f"{c.get('requirement_id')} v{c.get('version')}" + (f" · decided by {c['decided_by']}" if c.get("decided_by") else "")
    for key in ("text", "question", "summary", "answer", "motive", "error"):
        if c.get(key):
            text = str(c[key]).replace("\n", " ")
            return text[:110] + ("…" if len(text) > 110 else "")
    return json.dumps(c, ensure_ascii=False)[:110]


class Recorder:
    def __init__(self, db: Database, logs_folder: str, show_agents: bool = False):
        self.db = db
        self.logs_folder = logs_folder
        self.show_agents = show_agents
        os.makedirs(logs_folder, exist_ok=True)

    def record(self, msg: Message) -> Message:
        self.db.record_event(msg)
        name = msg.conversation_id or "no-conversation"
        with open(os.path.join(self.logs_folder, f"{name}.jsonl"), "a", encoding="utf-8") as f:
            f.write(msg.to_json() + "\n")
        if self.show_agents and msg.kind not in ("po_request", "approval"):
            print(f"  {_color(msg.sender)}[{msg.sender} → {msg.recipient}]{RESET} {msg.kind}: {summary(msg)}")
        return msg
