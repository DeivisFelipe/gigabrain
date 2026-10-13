"""Access to the LLM.

Every agent talks to the LLM the same way: a system prompt plus a list of messages
whose content is JSON, and a JSON object back.

    provider.complete_json(role="leader_decompose", system=..., messages=[...]) -> dict

Two providers:

- DeepSeek (or any OpenAI-compatible API): the real one.
- Simulated: calls no API. It answers each role with fixed rules, so the whole flow
  can run, be tested and be shown without spending tokens. It does not measure quality.
"""

from __future__ import annotations

import json
import os
import re
import time

from . import knowledge
from .database import slug


class ProviderError(RuntimeError):
    pass


class OpenAICompatibleProvider:
    def __init__(self, base_url: str, model: str, key_variable: str):
        try:
            import openai
        except ImportError as exc:
            raise ProviderError("Install the dependencies: pip install -r requirements.txt") from exc
        key = os.environ.get(key_variable)
        if not key:
            raise ProviderError(f"{key_variable} is not set. Set the environment variable or use --simulated.")
        self.client = openai.OpenAI(base_url=base_url, api_key=key)
        self.model = model
        self.name = model
        self.last_usage: dict = {}

    def complete_json(self, role: str, system: str, messages: list[dict]) -> dict:
        conversation = [{"role": "system", "content": system}, *messages]
        for attempt in range(2):
            response = self.client.chat.completions.create(
                model=self.model,
                messages=conversation,
                response_format={"type": "json_object"},
                max_tokens=8192,  # requirements with stories and criteria are long
            )
            usage = response.usage
            self.last_usage = {"tokens_in": getattr(usage, "prompt_tokens", None),
                               "tokens_out": getattr(usage, "completion_tokens", None)}
            text = response.choices[0].message.content or ""
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                if attempt == 1:
                    raise ProviderError(f"{role}: the model did not return valid JSON: {text[:200]}")
                conversation += [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": "Answer only with a valid JSON object, no text outside it."},
                ]
        raise AssertionError("unreachable")


def create_provider(name: str):
    if name == "simulated":
        return SimulatedProvider()
    if name == "deepseek":
        return OpenAICompatibleProvider("https://api.deepseek.com", "deepseek-chat", "DEEPSEEK_API_KEY")
    raise ProviderError(f"unknown provider: {name}")


# ---------------------------------------------------------------------------- simulated

TOPIC_WORDS = {
    "payment": "payments", "billing": "payments", "invoice": "payments",
    "notif": "notifications", "email": "notifications", "e-mail": "notifications", "push": "notifications",
    "report": "reports", "dashboard": "reports",
    "login": "accounts", "sign up": "accounts", "password": "accounts",
    "stock": "inventory", "inventory": "inventory", "order": "orders", "cart": "orders", "coupon": "orders",
}

LINK_WORDS = [
    ("supersed", "supersedes"), ("replace", "supersedes"), ("split", "splits"), ("merge", "merges"),
    ("depend", "depends_on"), ("conflict", "conflicts_with"), ("refine", "refines"), ("detail", "refines"),
]

HESITATIONS = r"\b(uh|uhm|um|erm|you know|i mean|sort of|basically)\b,?\s*|\[(crosstalk|laughter)\]\s*"

# Discourse markers at the start of an utterance ("as I said", "actually, we want to change").
MARKERS = r"^(as i (said|mentioned)[^,:]*[,:]|just to (repeat|reiterate)[^,:]*[,:]|like i mentioned[^,:]*[,:]|again, as we discussed[,:]|actually,[^:]*:|we talked to the team[^:]*:|change of plan[^:]*:)\s*"

NFR_WORDS = [
    ("PE", r"response time|seconds?\b|performance|fast|latency|throughput"),
    ("SE", r"secur|encrypt|authenticat|password|permission|access control|privacy"),
    ("US", r"easy|easily|intuitive|usab|user[- ]friendly|learn"),
    ("LF", r"look|colou?r|font|layout|appearance"),
    ("A", r"availab|24 ?/ ?7|uptime"),
    ("PO", r"portab|platform|browser|operating system|windows|linux"),
    ("MN", r"maintain|modular"),
    ("SC", r"scal|concurrent users"),
    ("L", r"licen[cs]e|law|legal|regulat|complian"),
    ("FT", r"backup|recover|fault|failure"),
]

SPEAKER_ROLE = {"Client": "client", "EndUser": "end user", "TechLead": "technical lead"}


def _guess_topics(text: str) -> list[str]:
    t = text.lower()
    topics = []
    for word, topic in TOPIC_WORDS.items():
        if word in t and topic not in topics:
            topics.append(topic)
    return topics or ["general"]


def _clean(text: str) -> str:
    text = re.sub(MARKERS, "", text.strip(), flags=re.I)
    text = re.sub(HESITATIONS, "", text, flags=re.I)
    text = re.sub(r"\b(\w+) \1\b", r"\1", text, flags=re.I)  # "the the" -> "the"
    return re.sub(r"\s+", " ", text).strip()


def _classify(sentence: str) -> tuple[str, str | None]:
    for subtype, pattern in NFR_WORDS:
        if re.search(pattern, sentence, re.I):
            return "NFR", subtype
    return "FR", None


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:]


def _statement(sentence: str) -> str:
    """A "shall" sentence: keep it if it already has a modal verb, otherwise wrap it."""
    sentence = sentence.strip().rstrip(".")
    if re.search(r"\b(shall|must)\b", sentence, re.I):
        return sentence + "."
    if re.search(r"\b(should|will)\b", sentence, re.I):  # weak modal verbs become "shall"
        return re.sub(r"\b(should|will)\b", "shall", sentence, count=1, flags=re.I) + "."
    return f"The system shall ensure that {_lower_first(sentence)}."


def _structured(sentence: str, role: str, topic: str) -> dict:
    statement = _statement(sentence)
    cls, subtype = _classify(sentence)
    return {
        "title": sentence[:70],
        "statement": statement,
        "ears_pattern": "event" if re.match(r"^(when|quando)\b", sentence, re.I) else "ubiquitous",
        "story": {"role": role, "goal": _lower_first(sentence.rstrip(".")), "benefit": "the need raised in the meeting is met"},
        "acceptance_criteria": [{"scenario": "Main flow", "given": "the system is in normal operation",
                                 "when": "the situation described by the requirement happens", "then": _lower_first(statement)}],
        "class": cls, "subtype": subtype, "topic": topic,
    }


def _last(messages: list[dict]) -> dict:
    return json.loads(messages[-1]["content"])


class SimulatedProvider:
    """Imitates the agents' answers with simple rules (no LLM)."""

    name = "simulated"

    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.last_usage: dict = {}

    def complete_json(self, role: str, system: str, messages: list[dict]) -> dict:
        if self.delay:
            time.sleep(self.delay)
        method = getattr(self, "_" + role, None)
        if not method:
            raise ProviderError(f"the simulator does not know the role {role}")
        return method(messages)

    # Digital Twin: conversation with the PO -------------------------------------------

    def _twin(self, messages: list[dict]) -> dict:
        received = [json.loads(m["content"]) for m in messages if m["role"] == "user"]
        po_texts = [m["text"] for m in received if m.get("from") == "po"]
        leader_answers = [m["consolidated_answer"] for m in received if m.get("from") == "leader"]
        last = received[-1]

        if last.get("from") == "po" and not leader_answers:
            return {"action": "consult", "message_to_po": "I will consult the experts before proposing anything.",
                    "query": {"question": last["text"], "topics": _guess_topics(" ".join(po_texts))}, "requirements": []}

        if last.get("from") == "leader" and last["consolidated_answer"].get("questions_for_po"):
            answer = last["consolidated_answer"]
            questions = "\n".join(f"- {q}" for q in answer["questions_for_po"])
            return {"action": "ask", "message_to_po": f"I consulted the experts: {answer.get('summary', '')}\n\nBefore proposing, I need to know:\n{questions}",
                    "query": None, "requirements": []}

        related = [r for a in leader_answers for r in a.get("related_requirements", [])]
        return self._proposal(po_texts, related)

    def _proposal(self, po_texts: list[str], related: list[str]) -> dict:
        everything = " ".join(po_texts)
        main = po_texts[0].strip().rstrip(".")
        main = re.sub(r"^(i want to|i would like to|we need to|i need to)\s+", "", main, flags=re.I)
        main = re.sub(r"^(update|replace|change)\s+R\d+\s+(to|with)\s+", "", main, flags=re.I)
        cited = re.findall(r"\bR\d+\b", everything.upper())
        base = cited[0] if cited and re.search(r"update|new version|change", everything.lower()) else None

        links = []
        for word, kind in LINK_WORDS:
            if word in everything.lower():
                for target in [c for c in cited if c != base] or related[:1]:
                    links.append({"kind": kind, "target": target, "reason": f"PO asked: {main[:80]}"})
                break

        criteria = [{"scenario": f"Scenario {i}", "given": "the system is in normal operation",
                     "when": _lower_first(t.strip().rstrip(".")), "then": "the system behaves as the PO described"}
                    for i, t in enumerate(po_texts[1:], start=1)]
        criteria = criteria or [{"scenario": "Main flow", "given": "the system is in normal operation",
                                 "when": "the user performs the action", "then": "the system does what was requested"}]
        requirement = {
            "title": main[:70][:1].upper() + main[1:70],
            "statement": f"The system shall {_lower_first(main)}.",
            "ears_pattern": "ubiquitous",
            "story": {"role": "user", "goal": _lower_first(main), "benefit": "my request is met"},
            "acceptance_criteria": criteria,
            "class": _classify(everything)[0], "subtype": _classify(everything)[1],
            "kind": "business",
            "topics": _guess_topics(everything),
            "base": base,
            "links": links,
            "reason": "update requested by the PO" if base else "new request from the PO",
        }
        return {"action": "propose", "query": None, "requirements": [requirement],
                "message_to_po": "Based on our conversation and the experts, this is my proposal. Reply 'approved' to save it or tell me what to change."}

    # Digital Twin: meeting, one utterance at a time ------------------------------------

    def _twin_draft(self, messages: list[dict]) -> dict:
        """Rule baseline: each sentence of a stakeholder that looks like a requirement becomes a draft."""
        data = _last(messages)
        topic = "general"
        for line in data.get("context", []):
            found = re.search(r"\] Analyst: .*?(?:next topic|talk about|move on to)[:\s]+([^.?]+)", line, re.I)
            if found:
                topic = found.group(1).strip().lower()
        m = re.match(r"\[(T\d+)\] (\w+): (.*)", data["utterance"])
        if not m:
            return {"requirements": []}
        _, speaker, text = m.groups()
        if speaker == "Analyst" or re.search(r"\b(sorry|on mute|hear me|connection)\b", text, re.I):
            return {"requirements": []}
        role = SPEAKER_ROLE.get(speaker, "user")
        return {"requirements": [_structured(s, role, topic) for s in re.split(r"(?<=[.!?])\s+", _clean(text)) if len(s.split()) >= 6]}

    def _plain_llm(self, messages: list[dict]) -> dict:
        """Rule baseline over the whole meeting (no topic context, no structure)."""
        requirements = []
        for line in messages[-1]["content"].splitlines():
            m = re.match(r"\[(T\d+)\] (\w+): (.*)", line)
            if not m or m.group(2) == "Analyst" or re.search(r"\b(sorry|on mute|hear me|connection)\b", m.group(3), re.I):
                continue
            for sentence in re.split(r"(?<=[.!?])\s+", _clean(m.group(3))):
                if len(sentence.split()) >= 6:
                    cls, subtype = _classify(sentence)
                    requirements.append({"text": sentence, "class": cls, "subtype": subtype, "turns": [m.group(1)]})
        return {"requirements": requirements}

    def _expert_review(self, messages: list[dict]) -> dict:
        """Recommend discarding a draft that repeats a saved requirement; link it when it changes one."""
        from .evaluation import similarity
        data = _last(messages)
        text = data["draft"]["statement"]
        saved = data.get("topic_requirements", []) + data.get("similar_from_other_topics", [])
        ref = lambda r: r.get("statement") or r.get("title") or ""
        best = max(saved, key=lambda r: similarity(text, ref(r)), default=None)
        s = similarity(text, ref(best)) if best else 0
        numbers = lambda t: set(re.findall(r"\d+", t or ""))
        if s >= 0.85 and numbers(text) != numbers(ref(best)):
            # Same subject with another value: a change, not a repetition.
            return {"action": "keep", "duplicate_of": None, "motive": f"changes a value of {best['id']}",
                    "links": [{"kind": "conflicts_with", "target": best["id"], "reason": "value differs from the saved requirement"}]}
        if s >= 0.85:
            return {"action": "discard", "duplicate_of": best["id"], "links": [], "motive": f"repeats {best['id']}"}
        if s >= 0.5:  # similar but with something new: link instead of discarding
            return {"action": "keep", "duplicate_of": None, "motive": f"similar to {best['id']}",
                    "links": [{"kind": "refines", "target": best["id"], "reason": "details or changes a saved requirement"}]}
        return {"action": "keep", "duplicate_of": None, "links": [], "motive": "no duplicates in the topic"}

    def _expert_quality(self, messages: list[dict]) -> dict:
        cls, subtype = _classify(_last(messages)["draft"]["statement"])
        return {"class": cls, "subtype": subtype, "motive": "taxonomy keywords"}

    def _twin_decide(self, messages: list[dict]) -> dict:
        """Follow the expert's recommendation."""
        data = _last(messages)
        rec, draft = data["recommendation"], data["draft"]
        return {
            "save": rec["action"] != "discard",
            "statement": rec.get("statement") or draft["statement"],
            "class": rec.get("class") or draft["class"],
            "subtype": rec.get("subtype") or draft["subtype"],
            "links": rec.get("links", []),
            "motive": f"followed the recommendation ({rec['action']}): {rec.get('motive', '')}",
        }

    # Leader ------------------------------------------------------------------------------

    def _leader_decompose(self, messages: list[dict]) -> dict:
        data = _last(messages)
        existing = {e["id"] for e in data.get("experts", [])}
        subqueries = []
        for topic in data.get("suggested_topics") or ["general"]:
            id_ = slug(topic)
            subqueries.append({"expert_id": id_ if id_ in existing else None, "topic": topic,
                               "description": f"Business rules and requirements about {topic}.",
                               "question": f"What is already known about {topic} that affects this request: {data['question']}"})
        return {"subqueries": subqueries}

    def _leader_consolidate(self, messages: list[dict]) -> dict:
        data = _last(messages)
        summary, conflicts, questions, related = [], [], [], []
        for a in data["answers"]:
            summary.append(f"[{a['expert_id']}] {a['answer']}")
            conflicts += [c for c in a.get("conflicts", []) if c not in conflicts]
            questions += [q for q in a.get("questions_for_po", []) if q not in questions]
            related += [r for r in a.get("related_requirements", []) if r not in related]
        return {"summary": " ".join(summary), "conflicts": conflicts, "questions_for_po": questions, "related_requirements": related}

    # Expert ------------------------------------------------------------------------------

    def _expert_create(self, messages: list[dict]) -> dict:
        data = _last(messages)
        md = data["template"]
        for req in data.get("topic_requirements", []):
            md = knowledge.add_item(md, "Approved requirements", f"{req['id']} v{req['version']} — {req['title']}")
        return {"knowledge_markdown": md}

    def _expert_answer(self, messages: list[dict]) -> dict:
        data = _last(messages)
        md = data["knowledge"]
        topic = md.splitlines()[0].replace("# Expert:", "").strip() if md else "this topic"
        reqs = data.get("topic_requirements", [])
        rules = knowledge.items(md, "Business rules")
        open_points = knowledge.items(md, "Open points")
        if reqs:
            answer = f"About {topic} there are already: " + "; ".join(f"{r['id']} ({r['title']}, {r['status']})" for r in reqs[:4]) + "."
        else:
            answer = f"There are no approved requirements about {topic} yet."
        if rules:
            answer += " Known rules: " + "; ".join(rules[:3]) + "."
        questions = list(open_points[:1])
        if not reqs:
            questions.append(f"For {topic}: who is the main user and what defines that it is done?")
        conflicts = [f"{r['id']} is under review and may conflict with the request." for r in reqs if r["status"] == "under_review"]
        return {"answer": answer, "related_requirements": [r["id"] for r in reqs[:3]], "conflicts": conflicts,
                "questions_for_po": questions, "confidence": "high" if reqs else "low"}

    def _expert_update(self, messages: list[dict]) -> dict:
        data = _last(messages)
        md = data["current_knowledge"]
        trigger = data["trigger"]
        changes = []
        if trigger["kind"] == "requirement_approved":
            req = trigger["requirement"]
            md = knowledge.add_item(md, "Approved requirements", f"{req['id']} v{req['version']} — {req['title']}")
            md = knowledge.add_item(md, "Decisions (and why)", f"{req['id']}: {req.get('reason') or 'approved by the PO'}")
            changes.append(f"recorded {req['id']} v{req['version']}")
            for link in trigger.get("links", []):
                md = knowledge.add_item(md, "Decisions (and why)", f"{link['source']} {link['kind'].replace('_', ' ')} {link['target']}")
                changes.append(f"link {link['source']} {link['kind']} {link['target']}")
            for rid in trigger.get("under_review", []):
                md = knowledge.add_item(md, "Open points", f"{rid} must be reviewed because {req['id']} changed")
                changes.append(f"{rid} under review")
        elif trigger["kind"] == "reorganize":
            changes.append("nothing to reorganize")
        elif trigger["kind"] == "document":
            for line in trigger["text"].splitlines():
                line = line.strip().lstrip("-*").strip()
                if len(line) > 15 and not line.startswith("#"):
                    md = knowledge.add_item(md, "Business rules", f"{line} (source: {trigger['name']})")
                    changes.append(f"rule: {line[:40]}")
        return {"knowledge_markdown": md, "changes": changes}
