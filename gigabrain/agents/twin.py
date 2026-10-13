"""Digital Twin: the Deliberative Council agent that talks to the PO.

In a conversation, for each PO message it chooses one of three actions:

- ask:      information is missing, so it asks the PO
- consult:  it needs to know what already exists (requirements, rules), so it sends a
            query to the Leader of the Consultative Council
- propose:  it has enough and proposes one or more requirements

In a meeting processed in batch (no PO), it drafts the requirements of each utterance
and gives the final vote after the experts' recommendation.

The PO always has the final word: nothing is saved without explicit approval.
"""

from __future__ import annotations

from .. import requirement_format
from ..messages import LEADER, PO, TWIN
from .base import Agent, as_json

# The requirement structure every prompt asks for (see requirement_format.py).
REQUIREMENT_FORMAT = """\
Requirement structure:
{
  "title": "short title",
  "statement": "one 'shall' sentence following an EARS pattern",
  "ears_pattern": "ubiquitous" | "event" | "state" | "unwanted" | "optional" | "complex",
  "story": {"role": "who benefits", "goal": "what they want", "benefit": "why"},
  "acceptance_criteria": [
    {"scenario": "name", "given": "initial context", "when": "action or event", "then": "observable result"}
  ],
  "class": "FR" | "NFR",
  "subtype": null for FR; for NFR one of PE (performance), SE (security), US (usability),
             LF (look and feel), A (availability), SA (safety), PO (portability),
             MN (maintainability), SC (scalability), L (legal), FT (fault tolerance), OT (other)
}

EARS patterns for "statement":
- ubiquitous: "The <system> shall <response>."
- event:      "When <trigger>, the <system> shall <response>."
- state:      "While <state>, the <system> shall <response>."
- unwanted:   "If <problem>, then the <system> shall <response>."
- optional:   "Where <feature is present>, the <system> shall <response>."
- complex:    a combination, e.g. "While <state>, when <trigger>, the <system> shall <response>."
In Portuguese use "deve" for "shall" ("Quando ..., o sistema deve ...").

Acceptance criteria: at least the main scenario and one exception or limit scenario.
Every "then" must be verifiable: no "fast", "easy", "user-friendly" without a number.
"""

SYSTEM_PROMPT = f"""\
You are the Digital Twin of the Product Owner (PO) in the GigaBrain system, specialized in
eliciting and defining software requirements. You talk with the real PO to turn a request,
often vague, into well-defined requirements.

GigaBrain does NOT read code. All knowledge about the product comes from approved
requirements, project documents and what the PO says. That knowledge belongs to the
Consultative Council (the Leader and the experts per business topic). Consult the Leader when
you need to know what was already decided, whether the request looks like something that
exists, or whether it may conflict with another requirement. Do not invent product rules.

Good practices you apply:
- INVEST: Independent, Negotiable, Valuable, Estimable, Small and Testable.
- Point out to the PO: a technical solution disguised as a requirement, vague words without
  a criterion ("fast", "intuitive") and a scope too large for one request (suggest splitting).
- Obvious technical enablers become a separate requirement with "kind": "enabler".

{REQUIREMENT_FORMAT}
Traceability: when the request changes, details or contradicts an existing requirement, say it:
- "base": "R3" when the request is a new version of R3 (same requirement, new content)
- "links": relations of the new requirement with others, read as "new <kind> target":
  refines, splits, merges, supersedes, depends_on, conflicts_with

You receive JSON messages:
- {{"from": "po", "text": "..."}}                          a PO message
- {{"from": "leader", "consolidated_answer": {{...}}}}     answer of the Consultative Council
  (summary, conflicts, questions_for_po, related_requirements)
- {{"from": "system", "notice": "..."}}                    a system notice

If the Leader returns questions_for_po, take them to the PO before proposing.
If there are conflicts, show them to the PO and let the PO decide.

LANGUAGE: write "message_to_po" and the requirements in the language the PO uses.

Always answer with a single JSON object:
{{
  "action": "ask" | "consult" | "propose",
  "message_to_po": "short and direct text for the PO",
  "query": {{"question": "...", "topics": ["payments", ...]}} or null,
  "requirements": [
    {{ ...requirement structure..., "kind": "business" | "enabler", "topics": ["payments"],
      "base": null or "R<n>", "links": [{{"kind": "refines", "target": "R<n>", "reason": "..."}}],
      "reason": "why this requirement (or this new version) exists" }}
  ]
}}
"query" is filled only with "action": "consult"; "requirements" only with "action": "propose".
Be direct and concise. Do not repeat what the PO already said.
"""

DRAFT_PROMPT = f"""\
You are the Digital Twin of GigaBrain and you follow a requirements elicitation meeting
(Analyst, Client, TechLead, EndUser) ONE UTTERANCE AT A TIME. You receive the current
utterance of a stakeholder, the previous utterances as context and the requirements already
saved. Draft the software requirements stated IN THIS utterance: none, one or more.

Rules:
- Granularity: one requirement per function or rule stated in the utterance, as the
  stakeholder phrased it. Do NOT split a sentence into several requirements: a list of fields,
  options or examples of the same function is ONE requirement. Different sentences with
  different obligations are different requirements.
- Utterance without a requirement -> empty list: general presentation of the product (what it
  is, its purpose, license), greetings, connection problems, confirmations.
- Do not draft again something already in "already_saved". A new detail about the same subject
  is a different requirement.
- LANGUAGE: write every text field in the language given in "language" (en = English). Never
  translate. Keep the stakeholder's words in "statement", removing only hesitations (uh, erm,
  you know, i mean), repetitions ("the the") and the colloquial opening ("I'd like", "we'd need
  to be able to"). Example: "Uh, I'd like the the system to export reports to PDF" ->
  "The system shall export reports to PDF."
- "topic": short business topic (e.g. "database", "search", "security"). Use the topic the
  Analyst announced in the context.

{REQUIREMENT_FORMAT}
Answer with a single JSON object:
{{"requirements": [{{ ...requirement structure..., "topic": "..." }}]}}
"""

# "Plain LLM" baseline: a minimal request, without the Twin's rules and without the council.
PLAIN_LLM_PROMPT = """\
Extract the software requirements from the meeting transcript below. Write each requirement
in the language of the transcript. For each one give its class (FR or NFR), the NFR subtype
code if NFR (PE, SE, US, LF, A, SA, PO, MN, SC, L, FT, OT; null for FR) and the ids of the
turns where it was said.

Answer with a single JSON object:
{"requirements": [{"text": "...", "class": "FR", "subtype": null, "turns": ["T8"]}]}
"""

DECIDE_PROMPT = """\
You are the Digital Twin, the PO's representative in the Deliberative Council of GigaBrain, and
you have the final vote on what goes into the Requirements Repository. The expert of the topic
reviewed your draft and made a recommendation. Decide:
- save the requirement (applying or not the suggested fixes and links), or
- discard it (e.g. it repeats a saved requirement or the utterances do not support it).
Follow the recommendation when the utterances support it; reject it when it contradicts what
was said. Discard as duplicate when the draft says the same as a saved requirement, even in
other words; save it and link it with "refines" only when it brings new information.
LANGUAGE: keep "statement" in the language given in "language". Never translate.

Answer with a single JSON object:
{"save": true, "statement": "...", "class": "FR" | "NFR", "subtype": null,
 "links": [{"kind": "depends_on", "target": "R2", "reason": "..."}], "motive": "why you decided this"}
"""

CONTEXT_UTTERANCES = 6
SAVED_IN_PROMPT = 30


class DigitalTwin(Agent):
    name = TWIN

    def __init__(self, ctx):
        super().__init__(ctx)
        self.history: list[dict] = []

    # ------------------------------------------------------------------ conversation with the PO

    def _decide(self, data: dict) -> dict:
        self.history.append({"role": "user", "content": as_json(data)})
        decision = self.call_llm("twin", SYSTEM_PROMPT, self.history)
        decision.setdefault("requirements", [])
        if decision.get("action") not in ("ask", "consult", "propose"):
            decision["action"] = "ask"
        if decision["action"] == "propose" and not decision["requirements"]:
            decision["action"] = "ask"
        decision["requirements"] = [
            {**requirement_format.normalize(r), **{k: r.get(k) for k in ("base", "links", "reason") if k in r},
             "topics": r.get("topics") or ["general"]}
            for r in decision["requirements"]
        ]
        self.history.append({"role": "assistant", "content": as_json(decision)})
        self._announce(decision)
        return decision

    def _announce(self, decision: dict) -> None:
        if decision["action"] == "consult":
            self.send(LEADER, "query", decision["query"] or {})
        elif decision["action"] == "propose":
            self.send(PO, "proposal", {"text": decision.get("message_to_po", ""), "requirements": decision["requirements"]})
        else:
            self.send(PO, "question_to_po", {"text": decision.get("message_to_po", "")})

    def hear_po(self, text: str) -> dict:
        return self._decide({"from": "po", "text": text})

    def hear_leader(self, consolidated_answer: dict) -> dict:
        return self._decide({"from": "leader", "consolidated_answer": consolidated_answer})

    def hear_system(self, notice: str) -> dict:
        return self._decide({"from": "system", "notice": notice})

    # ------------------------------------------------------------------ meeting in batch

    def draft(self, project: dict, utterance_index: int, already_saved: list[dict], next_index: int) -> list[dict]:
        """Read ONE utterance of the meeting (with the previous ones as context) and return its drafts."""
        turns = project["turns"]
        utterance = turns[utterance_index]
        line = lambda t: f"[{t['id']}] {t['speaker']}: {t['text']}"
        answer = self.call_llm("twin_draft", DRAFT_PROMPT, [{"role": "user", "content": as_json({
            "project": project["title"],
            "language": project.get("language", "en"),
            "context": [line(t) for t in turns[max(0, utterance_index - CONTEXT_UTTERANCES):utterance_index]],
            "utterance": line(utterance),
            "already_saved": already_saved[-SAVED_IN_PROMPT:],
        })}])
        drafts = []
        for raw in answer.get("requirements", []):
            req = requirement_format.normalize(raw)
            if not req["statement"]:
                continue
            drafts.append({**req, "index": next_index + len(drafts), "topic": raw.get("topic") or "general",
                           "turns": [utterance["id"]]})
        if drafts:
            self.send(TWIN, "drafts", {"turn": utterance["id"], "requirements": drafts})
        return drafts

    def extract_all_at_once(self, project: dict) -> list[dict]:
        """"Plain LLM" baseline: the whole meeting in a single request with the minimal prompt."""
        answer = self.call_llm("plain_llm", PLAIN_LLM_PROMPT, [{"role": "user", "content": "\n".join(
            f"[{t['id']}] {t['speaker']}: {t['text']}" for t in project["turns"])}])
        drafts = []
        for raw in answer.get("requirements", []):
            if not raw.get("text"):
                continue
            req = requirement_format.normalize({"statement": raw["text"].strip(), "class": raw.get("class"), "subtype": raw.get("subtype")})
            drafts.append({**req, "index": len(drafts), "topic": "general",
                           "turns": [t for t in raw.get("turns", []) if isinstance(t, str)]})
        self.send(TWIN, "drafts", {"requirements": drafts})
        return drafts

    def decide(self, draft: dict, recommendation: dict, utterances: dict[str, str], language: str = "en") -> dict:
        """Final vote: save (as is or fixed) or discard the draft."""
        if recommendation["action"] == "keep" and not recommendation.get("links"):
            decision = {"save": True, "statement": draft["statement"], "class": draft["class"], "subtype": draft["subtype"],
                        "links": [], "motive": "the expert agreed with the draft"}
        else:
            answer = self.call_llm("twin_decide", DECIDE_PROMPT, [{"role": "user", "content": as_json({
                "language": language,
                "draft": {k: draft[k] for k in ("statement", "class", "subtype", "turns")},
                "recommendation": recommendation,
                "utterances": utterances,
            })}])
            cls = "NFR" if str(answer.get("class", draft["class"])).upper() == "NFR" else "FR"
            decision = {
                "save": bool(answer.get("save", True)),
                "statement": (answer.get("statement") or draft["statement"]).strip(),
                "class": cls,
                "subtype": answer.get("subtype") if cls == "NFR" else None,
                "links": [l for l in answer.get("links", []) if isinstance(l, dict) and l.get("target")],
                "motive": answer.get("motive", ""),
            }
        self.send(TWIN, "decision", {
            "draft": draft["index"], "save": decision["save"], "recommendation": recommendation["action"],
            "text": ("save: " if decision["save"] else "discard: ") + decision["motive"],
        })
        return decision
