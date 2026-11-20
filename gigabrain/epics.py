"""Epics: the requirements of the Repository grouped by the theme they address.

A list of requirements is flat. Once it grows, the Digital Twin organizes it one level up: the
requirements about the same theme go into one epic, and each epic shows its user stories (the
functional requirements) and its constraint cards (the non-functional ones, which limit those
stories and are never told as stories).

Every active requirement goes into exactly one epic. Organizing again replaces the epics: the
requirements are the source of truth, the epics are a view of them, so the previous grouping is
kept only in the log.
"""

from __future__ import annotations

from .agents.base import Agent, as_json
from .database import Database
from .messages import REPOSITORY, TWIN

ORGANIZE_PROMPT = """\
You are the Digital Twin of the Product Owner in GigaBrain. Organize the requirements of the
Repository into epics: an epic is a large business capability (e.g. "Checkout and payment",
"Account and access") made of the requirements about that same theme.

Rules:
- Every requirement goes into exactly one epic. Do not leave any out and do not repeat any.
- Group by business theme, not by class: an epic usually has functional requirements (user
  stories) and the non-functional requirements that constrain them (constraint cards).
- A non-functional requirement that applies to the whole system (e.g. availability) may form an
  epic of its own, like "Platform qualities".
- Prefer few, meaningful epics: between 2 and 9 for most projects; one requirement alone is an
  epic only when it fits no other.
- LANGUAGE: write "title" and "description" in the language of the requirements.

Answer with a single JSON object:
{"epics": [{"title": "short name", "description": "the capability in one sentence",
            "requirements": ["R1", "R4"]}]}
"""


class EpicOrganizer(Agent):
    name = TWIN

    def organize(self, requirements: list[dict]) -> list[dict]:
        """Ask the model for the epics, then make sure every requirement is in exactly one."""
        known = [r["id"] for r in requirements]
        answer = self.call_llm("twin_epics", ORGANIZE_PROMPT, [{"role": "user", "content": as_json({"requirements": [
            {"id": r["id"], "title": r["title"], "statement": r["content"].get("statement"), "class": r["content"].get("class"),
             "subtype": r["content"].get("subtype"), "topics": r["topics"]} for r in requirements]})}])
        epics, placed = [], set()
        for raw in answer.get("epics") or []:
            ids = [i for i in (raw.get("requirements") or []) if i in known and i not in placed]
            if not ids:
                continue
            placed.update(ids)
            epics.append({"title": raw.get("title") or ids[0], "description": raw.get("description") or "", "requirements": ids})
        left = [i for i in known if i not in placed]
        if left:  # what the model forgot goes together, so that nothing disappears from the view
            epics.append({"title": "Other requirements", "description": "Requirements not placed in another epic.",
                          "requirements": left})
        return epics


def organize(ctx) -> list[dict]:
    """Group the active requirements of the Repository into epics and store them."""
    requirements = ctx.db.list_requirements(only_active=True)
    epics = EpicOrganizer(ctx).organize(requirements) if requirements else []
    stored = ctx.db.replace_epics(epics)
    EpicOrganizer(ctx).send(REPOSITORY, "epics_organized", {
        "text": f"{len(stored)} epics for {len(requirements)} requirements",
        "epics": [{"id": e["id"], "title": e["title"], "requirements": e["requirements"]} for e in stored]})
    return stored


def view(db: Database) -> list[dict]:
    """The stored epics with their stories (FR) and constraint cards (NFR), as the PO reads them."""
    result = []
    for epic in db.list_epics():
        stories, constraints = [], []
        for rid in epic["requirements"]:
            req = db.get_requirement(rid)
            if not req:
                continue
            c = req["content"]
            item = {"id": req["id"], "title": req["title"], "status": req["status"], "statement": c.get("statement"),
                    "subtype": c.get("subtype"), "acceptance_criteria": c.get("acceptance_criteria") or []}
            if c.get("class") == "NFR":
                constraints.append({**item, "constraint": c.get("constraint")})
            else:
                stories.append({**item, "story": c.get("story")})
        result.append({**epic, "stories": stories, "constraints": constraints})
    return result
