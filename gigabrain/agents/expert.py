"""Expert: one agent per business topic (payments, notifications...).

What it knows is in its knowledge file (Markdown), plus the approved requirements of its
topic, which come from the database.

Life cycle:
1. create    the Leader creates it when a new topic appears (not during a meeting)
2. answer    answers the Leader's queries with what it knows
3. review    recommends what to do with a draft (keep, fix, discard, links); never decides
4. learn     updates the file when something changes. Triggers:
             - a requirement of its topic was approved (or got a new version, was superseded...)
             - someone gave it a project document to read
             - someone edited the file by hand (detected by the hash)
"""

from __future__ import annotations

from .. import knowledge
from ..database import slug
from ..messages import LEADER, REGISTRY, expert
from .base import Agent, as_json, requirement_summary

ANSWER_PROMPT = """\
You are a GigaBrain expert on the topic described in your knowledge file. You have no access
to code: only to your knowledge file and the requirements of your topic. Answer the Leader's
question using only that material. If you do not know, say so and turn the gap into a question
for the PO.

Answer with a single JSON object:
{
  "answer": "what you know that matters for the question",
  "related_requirements": ["R1", ...],
  "conflicts": ["description of each conflict with an existing requirement"],
  "questions_for_po": ["questions only the PO can answer"],
  "confidence": "high" | "medium" | "low"
}
"""

CREATE_PROMPT = """\
You are being born as a GigaBrain expert on a business topic. Build your initial knowledge file
in Markdown, following exactly the sections of the template you receive. Use only the
requirements you receive as source; do not invent rules. Whatever is uncertain goes to
"Open points".

Answer with a single JSON object: {"knowledge_markdown": "..."}
"""

UPDATE_PROMPT = """\
You are a GigaBrain expert and must update your knowledge file (Markdown) because of a trigger
(approved requirement, link between requirements, project document, or periodic reorganization).
Keep the same sections. Record decisions with the reason and cite requirement ids. If a
requirement was superseded or split, make it clear. If the file is longer than max_chars,
summarize and reorganize it without losing decisions.

Answer with a single JSON object:
{"knowledge_markdown": "...", "changes": ["what changed, in short sentences"]}
"""

QUALITY_PROMPT = """\
You are the quality expert of GigaBrain. Your only job is to check the classification of a draft
requirement: functional (FR) or non-functional (NFR) and, if NFR, the subtype. Use the rules and
the glossary of your knowledge file. You only recommend.

Answer with a single JSON object:
{"class": "FR" | "NFR", "subtype": "PE" | "SE" | "US" | "LF" | "A" | "SA" | "PO" | "MN" | "SC" | "L" | "FT" | "OT" | null,
 "motive": "short"}
"""

REVIEW_PROMPT = """\
You are a GigaBrain expert and review ONE draft requirement of your topic, drafted by the Digital
Twin from a meeting transcript. You receive the draft, the utterances it cites, your knowledge
file, the saved requirements of your topic and similar requirements of other topics. You only
RECOMMEND; the Digital Twin decides.

Recommend an action:
- "keep":    correct and supported by the utterances
- "fix":     supported, but the statement, the class (FR/NFR) or the subtype need a fix
- "discard": in two cases:
    1. DUPLICATE: says the same as a saved requirement, even in other words ("must" x "needs
       to", "the user shall" x "the system shall allow the user to") or with "as I said before"
       in front (say which one in "duplicate_of"). Compare the CONTENT: no new information
       means duplicate.
    2. UNSUPPORTED: the cited utterances do not say it.
Use "refines" (recommend "keep" with the link) only when the draft brings NEW information about
a saved requirement: one more field, condition, limit or case.
A change of a saved requirement (another value, another rule) is "conflicts_with" or, if the
stakeholder clearly replaced the rule, "supersedes".
Do not invent requirements and keep the original words.
LANGUAGE: "statement" stays in the language given in "language". Never translate; translating
is not a fix.

Answer with a single JSON object:
{"action": "keep" | "fix" | "discard", "statement": "only if fix", "class": "only if fix",
 "subtype": "only if fix", "duplicate_of": "R3" or null,
 "links": [{"kind": "depends_on", "target": "R2", "reason": "..."}], "motive": "short"}
"""


class Expert(Agent):
    def __init__(self, ctx, data: dict):
        super().__init__(ctx)
        self.id = data["id"]
        self.topic = data["topic"]
        self.description = data["description"]
        self.file = data["file"]
        self.name = expert(self.id)

    # ------------------------------------------------------------------ creation

    @classmethod
    def create(cls, ctx, topic: str, description: str, created_by: str = LEADER) -> Expert:
        id_ = slug(topic)
        data = ctx.db.create_expert(id_, topic, description, knowledge.path(ctx.knowledge_folder, id_))
        new = cls(ctx, data)
        answer = new.call_llm("expert_create", CREATE_PROMPT, [{"role": "user", "content": as_json({
            "topic": topic,
            "description": description,
            "template": knowledge.initial_template(topic, description),
            "topic_requirements": new.topic_requirements(),
        })}])
        md = answer.get("knowledge_markdown") or knowledge.initial_template(topic, description)
        new._store(md, "creation", {"created_by": created_by})
        return new

    @classmethod
    def create_fixed(cls, ctx, id_: str, topic: str, description: str, md: str) -> Expert:
        """Expert with ready-made knowledge (e.g. the quality taxonomy), no LLM."""
        data = ctx.db.create_expert(id_, topic, description, knowledge.path(ctx.knowledge_folder, id_))
        new = cls(ctx, data)
        new._store(md, "creation", {"created_by": "system", "knowledge": "fixed"})
        return new

    @classmethod
    def load(cls, ctx, id_: str) -> Expert | None:
        data = ctx.db.get_expert(id_)
        return cls(ctx, data) if data else None

    # ------------------------------------------------------------------ knowledge

    def topic_requirements(self) -> list[dict]:
        return [requirement_summary(r) for r in self.ctx.db.list_requirements() if self.id in r["topics"]]

    def read_knowledge(self) -> str:
        return knowledge.read(self.file)

    def _store(self, md: str, reason: str, origin: dict) -> int | None:
        knowledge.write(self.file, md)
        version = self.ctx.db.record_knowledge(self.id, knowledge.read(self.file), reason, origin)
        if version:
            self.send(REGISTRY, "knowledge_updated", {"expert_id": self.id, "version": version, "motive": reason, **origin})
        return version

    def sync(self) -> int | None:
        """If someone edited the file by hand, keep that as a new version."""
        md = self.read_knowledge()
        if not md:
            return None
        return self._store(md, "manual edit", {"file": self.file})

    def update(self, trigger: dict, reason: str) -> int | None:
        self.sync()
        answer = self.call_llm("expert_update", UPDATE_PROMPT, [{"role": "user", "content": as_json({
            "current_knowledge": self.read_knowledge(),
            "trigger": trigger,
            "topic_requirements": self.topic_requirements(),
            "max_chars": knowledge.MAX_CHARS,
        })}])
        md = answer.get("knowledge_markdown")
        if not md:
            return None
        return self._store(md, reason, {"trigger": trigger["kind"], "changes": answer.get("changes", [])})

    def note_requirement(self, requirement: dict, links: list[dict], under_review: list[str]) -> int | None:
        """Write a saved requirement into the file without the LLM; reorganize with the LLM once in a while."""
        self.sync()
        md = self.read_knowledge()
        md = knowledge.add_item(md, "Approved requirements", f"{requirement['id']} ({requirement['class']}) — {requirement['statement']}")
        if requirement.get("reason"):
            md = knowledge.add_item(md, "Decisions (and why)", f"{requirement['id']}: {requirement['reason']}")
        for link in links:
            md = knowledge.add_item(md, "Decisions (and why)", f"{link['source']} {link['kind'].replace('_', ' ')} {link['target']}")
        for rid in under_review:
            md = knowledge.add_item(md, "Open points", f"{rid} must be reviewed because {requirement['id']} changed")
        version = self._store(md, f"{requirement['id']} saved", {"trigger": "requirement_saved", "changes": [f"noted {requirement['id']}"]})
        if version and (version % knowledge.REORGANIZE_EVERY == 0 or len(md) > knowledge.MAX_CHARS):
            version = self.update({"kind": "reorganize"}, "periodic reorganization") or version
        return version

    def similar_from_other_topics(self, text: str, limit: int = 5) -> list[dict]:
        """Requirements of other topics with words in common: duplicates may cross topics."""
        from ..evaluation import similarity
        candidates = []
        for req in self.ctx.db.list_requirements(only_active=True):
            if self.id in req["topics"]:
                continue
            s = similarity(text, req["content"].get("statement") or req["title"])
            if s >= 0.3:
                candidates.append((s, requirement_summary(req)))
        candidates.sort(key=lambda c: -c[0])
        return [r for _, r in candidates[:limit]]

    # ------------------------------------------------------------------ recommendations

    def review(self, draft: dict, utterances: dict[str, str], language: str = "en") -> dict:
        """Recommend what to do with a draft requirement (does not decide)."""
        self.sync()
        answer = self.call_llm("expert_review", REVIEW_PROMPT, [{"role": "user", "content": as_json({
            "topic": self.topic,
            "language": language,
            "knowledge": self.read_knowledge(),
            "topic_requirements": self.topic_requirements(),
            "similar_from_other_topics": self.similar_from_other_topics(draft["statement"]),
            "draft": {k: draft[k] for k in ("statement", "class", "subtype", "turns")},
            "utterances": utterances,
        })}])
        action = answer.get("action") if answer.get("action") in ("keep", "fix", "discard") else "keep"
        recommendation = {
            "expert_id": self.id,
            "knowledge_version": self.ctx.db.get_expert(self.id)["knowledge_version"],
            "action": action,
            "statement": answer.get("statement") if action == "fix" else None,
            "class": answer.get("class") if action == "fix" else None,
            "subtype": answer.get("subtype") if action == "fix" else None,
            "duplicate_of": answer.get("duplicate_of"),
            "links": [l for l in answer.get("links", []) if isinstance(l, dict) and l.get("target")],
            "motive": answer.get("motive", ""),
        }
        self.send(LEADER, "expert_answer", {**recommendation, "answer": f"{action}: {recommendation['motive']}"})
        return recommendation

    def review_quality(self, draft: dict) -> dict:
        """Recommend the class (FR/NFR) and the subtype of the draft."""
        answer = self.call_llm("expert_quality", QUALITY_PROMPT, [{"role": "user", "content": as_json({
            "knowledge": self.read_knowledge(),
            "draft": {k: draft[k] for k in ("statement", "class", "subtype")},
        })}])
        cls = "NFR" if str(answer.get("class", draft["class"])).upper() == "NFR" else "FR"
        result = {"class": cls, "subtype": answer.get("subtype") if cls == "NFR" else None, "motive": answer.get("motive", "")}
        self.send(LEADER, "expert_answer", {
            "expert_id": self.id, "action": "classify", **result,
            "answer": f"class {cls}{' / ' + result['subtype'] if result['subtype'] else ''}: {result['motive']}",
        })
        return result

    # ------------------------------------------------------------------ queries in a conversation

    def answer(self, question: str) -> dict:
        self.sync()
        self.ctx.db.count_consultation(self.id)
        answer = self.call_llm("expert_answer", ANSWER_PROMPT, [{"role": "user", "content": as_json({
            "question": question,
            "knowledge": self.read_knowledge(),
            "topic_requirements": self.topic_requirements(),
        })}])
        result = {
            "expert_id": self.id,
            "knowledge_version": self.ctx.db.get_expert(self.id)["knowledge_version"],
            "answer": answer.get("answer", ""),
            "related_requirements": answer.get("related_requirements", []),
            "conflicts": answer.get("conflicts", []),
            "questions_for_po": answer.get("questions_for_po", []),
            "confidence": answer.get("confidence", "medium"),
        }
        self.send(LEADER, "expert_answer", result)
        return result
