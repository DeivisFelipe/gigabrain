"""Leader: the only fixed agent of the Consultative Council.

When the Twin sends a query, the Leader:

1. splits the question by business topic, looking at the Expert Registry
2. for each topic, uses the existing expert or creates a new one
   (in "in_meeting" mode it does not create: it records a pending item)
3. sends a subquery to each expert
4. consolidates the answers: merges them, points out conflicts and returns to the
   Twin the questions only the PO can answer

In a meeting processed in batch, it takes each draft to the expert of its topic and to
the quality expert, and returns their recommendation to the Twin.
"""

from __future__ import annotations

from .. import knowledge
from ..database import slug
from ..messages import LEADER, REGISTRY, TWIN, expert
from .base import Agent, as_json
from .expert import Expert

DECOMPOSE_PROMPT = """\
You are the Leader of the Consultative Council of GigaBrain. You receive a query from the
Digital Twin and decide which experts (one per business topic) must answer. A request may
involve several topics: "notify the customer when the payment is confirmed" involves payments
and notifications. Reuse existing experts; suggest a new topic only when no existing expert
covers the subject. Topics are about the business, never about code.

Answer with a single JSON object:
{
  "subqueries": [
    {"expert_id": "existing id or null", "topic": "topic name",
     "description": "what this expert covers", "question": "specific question for it"}
  ]
}
"""

CONSOLIDATE_PROMPT = """\
You are the Leader of the Consultative Council of GigaBrain. Merge the experts' answers into a
single answer for the Digital Twin. Point out conflicts between experts or with existing
requirements, and keep the questions only the PO can answer.

Answer with a single JSON object:
{"summary": "...", "conflicts": ["..."], "questions_for_po": ["..."], "related_requirements": ["R1", ...]}
"""

QUALITY_ID = "quality"
QUALITY_DESCRIPTION = "FR/NFR class and subtype (ISO/IEC 25010)"


class Leader(Agent):
    name = LEADER

    # ------------------------------------------------------------------ conversation with the PO

    def consult(self, question: str, topics: list[str]) -> dict:
        experts = self.ctx.db.list_experts()
        decomposition = self.call_llm("leader_decompose", DECOMPOSE_PROMPT, [{"role": "user", "content": as_json({
            "question": question,
            "suggested_topics": topics,
            "experts": [{"id": e["id"], "topic": e["topic"], "description": e["description"]} for e in experts],
            "mode": self.ctx.mode,
        })}])
        subqueries = decomposition.get("subqueries") or [
            {"expert_id": None, "topic": t, "description": f"Requirements about {t}", "question": question} for t in topics
        ]
        self.send(LEADER, "decomposition", {"question": question, "subqueries": subqueries})

        answers, without_expert = [], []
        for sub in subqueries:
            exp = self._get_or_create(sub)
            if not exp:
                without_expert.append(sub["topic"])
                continue
            self.send(expert(exp.id), "query", {"question": sub["question"], "topic": exp.topic})
            answers.append(exp.answer(sub["question"]))

        consolidated = self._consolidate(question, answers)
        if without_expert:
            consolidated["questions_for_po"].append(
                "There is no expert yet for: " + ", ".join(without_expert)
                + ". It will be created after the meeting; what do you already know about it?"
            )
        consolidated["sources"] = [{"kind": "expert", "ref": a["expert_id"], "version": a["knowledge_version"]} for a in answers]
        self.send(TWIN, "consolidated_answer", consolidated)
        return consolidated

    def _consolidate(self, question: str, answers: list[dict]) -> dict:
        empty = {"summary": "", "conflicts": [], "questions_for_po": [], "related_requirements": []}
        if not answers:
            return {**empty, "summary": "No expert available to answer."}
        if len(answers) == 1:
            a = answers[0]
            return {"summary": a["answer"], "conflicts": list(a["conflicts"]), "questions_for_po": list(a["questions_for_po"]),
                    "related_requirements": list(a["related_requirements"])}
        result = self.call_llm("leader_consolidate", CONSOLIDATE_PROMPT, [{"role": "user", "content": as_json({
            "question": question, "answers": answers,
        })}])
        return {key: result.get(key, default) for key, default in empty.items()}

    # ------------------------------------------------------------------ meeting in batch

    def consult_draft(self, draft: dict, utterances: dict[str, str], language: str = "en") -> dict:
        """Take a draft to the expert of its topic and to the quality expert; return the recommendation to the Twin."""
        exp = self._get_or_create({"topic": draft["topic"], "description": f"Requirements about {draft['topic']}."})
        if exp:
            self.send(expert(exp.id), "query", {"question": "review draft: " + draft["statement"][:100], "topic": exp.topic})
            recommendation = exp.review(draft, utterances, language)
        else:
            recommendation = {"expert_id": None, "action": "keep", "links": [], "duplicate_of": None,
                              "motive": "no expert for the topic; nothing to recommend"}

        # The quality expert checks class and subtype (only if the draft will not be discarded).
        if self.ctx.with_quality and recommendation["action"] != "discard":
            quality = self._quality_expert()
            self.send(expert(quality.id), "query", {"question": "classify: " + draft["statement"][:100], "topic": quality.topic})
            current = (recommendation.get("class") or draft["class"], recommendation.get("subtype") or draft["subtype"])
            q = quality.review_quality({**draft, "class": current[0], "subtype": current[1]})
            if (q["class"], q["subtype"]) != current:
                recommendation = {**recommendation, "action": "fix", "class": q["class"], "subtype": q["subtype"],
                                  "motive": (recommendation["motive"] + " | quality: " + q["motive"]).strip(" |")}
            recommendation["quality"] = q

        self.send(TWIN, "consolidated_answer", {"summary": f"{recommendation['action']}: {recommendation['motive']}", **recommendation})
        return recommendation

    def _quality_expert(self) -> Expert:
        existing = Expert.load(self.ctx, QUALITY_ID)
        if existing:
            return existing
        self.send(REGISTRY, "expert_created", {"expert_id": QUALITY_ID, "topic": QUALITY_ID, "description": QUALITY_DESCRIPTION})
        return Expert.create_fixed(self.ctx, QUALITY_ID, QUALITY_ID, QUALITY_DESCRIPTION, knowledge.NFR_TAXONOMY)

    def _get_or_create(self, sub: dict) -> Expert | None:
        id_ = sub.get("expert_id") or slug(sub["topic"])
        existing = Expert.load(self.ctx, id_)
        if existing:
            return existing
        if self.ctx.mode == "in_meeting":
            self.send(REGISTRY, "pending", {"motive": "topic without an expert during the meeting",
                                            "topic": sub["topic"], "description": sub.get("description")})
            return None
        # Announce before creating: creation already records the first knowledge version.
        description = sub.get("description") or f"Requirements about {sub['topic']}"
        self.send(REGISTRY, "expert_created", {"expert_id": slug(sub["topic"]), "topic": sub["topic"], "description": description})
        return Expert.create(self.ctx, sub["topic"], description)
