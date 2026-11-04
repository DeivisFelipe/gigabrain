"""Digital Twin: the Deliberative Council agent that talks to the PO.

In a conversation, for each PO message it chooses one of three actions:

- ask:      information is missing, so it asks the PO
- consult:  it needs to know what already exists (requirements, rules), so it sends a
            query to the Leader of the Consultative Council
- propose:  it has enough and proposes one or more requirements

In a meeting processed in batch (no PO), it drafts the requirements of each utterance
and gives the final vote after the experts' recommendation.

What it writes passes the Critic (agents/critic.py): a proposal before the PO sees it, a
draft before it goes to the experts. What does not pass comes back to be fixed.

The PO always has the final word: nothing is saved without explicit approval.
"""

from __future__ import annotations

from .. import requirement_format
from ..language import spoken_language, written_in
from ..messages import LEADER, PO, SYSTEM, TWIN
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

LANGUAGE: write "message_to_po", the "question" of the query and the requirements in the
language given in "language", even when the Consultative Council answers in another language.
When a message brings no "language", use the language the PO uses.

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

# The Critic returned a draft: fix only what it lists (see agents/critic.py).
REVISE_PROMPT = f"""\
You are the Digital Twin of GigaBrain. You drafted a requirement from one utterance of a meeting
and the Critic returned it: it did not pass validation. You receive your draft, the utterances it
cites and the points to fix.

Rules:
- Fix ONLY the listed points. Do not add requirements and do not change the scope.
- Keep the stakeholder's words in "statement" and keep the language given in "language".
- Do not invent what the utterances do not say (a number, a limit, a role). If a point cannot be
  fixed with what was said, leave that part as it is.

{REQUIREMENT_FORMAT}
Answer with a single JSON object:
{{"requirement": {{ ...requirement structure..., "topic": "..." }}}}
"""

# Added to DECIDE_PROMPT only when the Critic sends its report (the prompt above is left as it is).
GUIDANCE_NOTE = """
You also receive "validation": the Critic's report on this draft taken together with the
repository (one score per competency, the competencies that did not pass and the findings). Use it
to guide your vote: fix what it points out when the utterances allow it, and discard a draft
whose origin could not be confirmed.
"""

# What the PO reads when a model answer had nothing usable for the PO.
NOTHING_TO_SAY = "I could not work out an answer. Could you say that again?"

CONTEXT_UTTERANCES = 6
SAVED_IN_PROMPT = 30


class DigitalTwin(Agent):
    name = TWIN

    def __init__(self, ctx):
        super().__init__(ctx)
        self.history: list[dict] = []
        self.language: str | None = None  # the PO's, as far as the code can tell (language.py)

    # ------------------------------------------------------------------ conversation with the PO

    def _decide(self, data: dict) -> dict:
        decision = self._think(data)
        # A proposal passes the Critic before the PO sees it; what does not pass comes
        # back here, through _think, as a system notice (see agents/critic.py).
        if self.ctx.critic and decision["action"] == "propose":
            decision = self.ctx.critic.gate_proposal(self, decision, self._think)
        self._announce(decision)
        return decision

    def _think(self, data: dict, may_retry: bool = True) -> dict:
        """Take one message and decide the next action, without telling anyone yet."""
        if self.language:
            data = {**data, "language": self.language}
        self.history.append({"role": "user", "content": as_json(data)})
        decision = self.call_llm("twin", SYSTEM_PROMPT, self.history)
        decision["requirements"] = decision.get("requirements") or []
        if decision.get("action") not in ("ask", "consult", "propose"):
            decision["action"] = "ask"
        if decision["action"] == "propose" and not decision["requirements"]:
            decision["action"] = "ask"
        decision["message_to_po"] = decision.get("message_to_po") or NOTHING_TO_SAY
        decision["requirements"] = [
            {**requirement_format.normalize(r), **{k: r.get(k) for k in ("base", "links", "reason") if k in r},
             "topics": r.get("topics") or ["general"]}
            for r in decision["requirements"]
        ]
        self.history.append({"role": "assistant", "content": as_json(decision)})
        if self.language and not self._in_language(decision):
            if may_retry:  # here, before the query reaches the Leader and the other agents follow it
                self.send(SYSTEM, "language_retry", {"text": f"the answer was not in {self.language}; asked once more"})
                return self._think({"from": "system", "reason": "language", "notice":
                                    f"Your last answer was not in {self.language}. Give the same answer in {self.language}."}, False)
            self.send(SYSTEM, "language_off", {"text": f"the answer is still not in {self.language}; it goes on as it is"})
        return decision

    def _in_language(self, decision: dict) -> bool:
        texts = [decision["message_to_po"], (decision.get("query") or {}).get("question") or ""]
        for requirement in decision["requirements"]:
            criteria = requirement.get("acceptance_criteria") or []
            texts += [requirement["statement"], " ".join(f"{c.get('given')} {c.get('when')} {c.get('then')}" for c in criteria)]
        return all(written_in(text, self.language) for text in texts)

    def _announce(self, decision: dict) -> None:
        if decision["action"] == "consult":
            self.send(LEADER, "query", decision.get("query") or {})
        elif decision["action"] == "propose":
            self.send(PO, "proposal", {"text": decision.get("message_to_po", ""), "requirements": decision["requirements"]})
        else:
            self.send(PO, "question_to_po", {"text": decision.get("message_to_po", "")})

    def hear_po(self, text: str) -> dict:
        self.language = spoken_language(text) or self.language  # a short "yes" keeps the language of the conversation
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
        for raw in (answer.get("requirements") or []):
            req = requirement_format.normalize(raw)
            if not req["statement"]:
                continue
            drafts.append({**req, "index": next_index + len(drafts), "topic": raw.get("topic") or "general",
                           "turns": [utterance["id"]]})
        if drafts:
            self.send(TWIN, "drafts", {"turn": utterance["id"], "requirements": drafts})
        return drafts

    def revise(self, draft: dict, feedback: str, utterances: dict[str, str], language: str = "en") -> dict | None:
        """Fix a draft the Critic returned. Returns the new draft, or None if nothing usable came back."""
        answer = self.call_llm("twin_revise", REVISE_PROMPT, [{"role": "user", "content": as_json({
            "language": language,
            "draft": {k: draft.get(k) for k in (*requirement_format.EMPTY_REQUIREMENT, "topic")},
            "utterances": utterances,
            "validation_feedback": feedback,
        })}])
        raw = answer.get("requirement")
        if not isinstance(raw, dict) or not raw.get("statement"):
            return None
        revised = {**draft, **requirement_format.normalize(raw), "topic": raw.get("topic") or draft.get("topic") or "general"}
        self.send(TWIN, "drafts", {"turn": (draft.get("turns") or [None])[0], "requirements": [revised], "revision": True})
        return revised

    def extract_all_at_once(self, project: dict) -> list[dict]:
        """"Plain LLM" baseline: the whole meeting in a single request with the minimal prompt."""
        answer = self.call_llm("plain_llm", PLAIN_LLM_PROMPT, [{"role": "user", "content": "\n".join(
            f"[{t['id']}] {t['speaker']}: {t['text']}" for t in project["turns"])}])
        drafts = []
        for raw in (answer.get("requirements") or []):
            if not raw.get("text"):
                continue
            req = requirement_format.normalize({"statement": raw["text"].strip(), "class": raw.get("class"), "subtype": raw.get("subtype")})
            drafts.append({**req, "index": len(drafts), "topic": "general",
                           "turns": [t for t in (raw.get("turns") or []) if isinstance(t, str)]})
        self.send(TWIN, "drafts", {"requirements": drafts})
        return drafts

    def decide(self, draft: dict, recommendation: dict, utterances: dict[str, str], language: str = "en",
               guidance: dict | None = None) -> dict:
        """Final vote: save (as is or fixed) or discard the draft.

        `guidance` is the Critic's report on the draft taken together with the repository.
        When it says something did not pass, the Twin looks at it before voting.
        """
        guided = bool(guidance) and not guidance.get("passed", True)
        if recommendation["action"] == "keep" and not recommendation.get("links") and not guided:
            decision = {"save": True, "statement": draft["statement"], "class": draft["class"], "subtype": draft["subtype"],
                        "links": [], "motive": "the expert agreed with the draft"}
        else:
            payload = {
                "language": language,
                "draft": {k: draft[k] for k in ("statement", "class", "subtype", "turns")},
                "recommendation": {k: v for k, v in recommendation.items() if k != "validation"},
                "utterances": utterances,
            }
            if guidance:
                payload["validation"] = {k: guidance.get(k) for k in ("verdict", "failed_dimensions", "scores", "document", "findings")}
            answer = self.call_llm("twin_decide", DECIDE_PROMPT + (GUIDANCE_NOTE if guidance else ""),
                                   [{"role": "user", "content": as_json(payload)}])
            cls = "NFR" if str(answer.get("class", draft["class"])).upper() == "NFR" else "FR"
            decision = {
                "save": bool(answer.get("save", True)),
                "statement": (answer.get("statement") or draft["statement"]).strip(),
                "class": cls,
                "subtype": answer.get("subtype") if cls == "NFR" else None,
                "links": [l for l in (answer.get("links") or []) if isinstance(l, dict) and l.get("target")],
                "motive": answer.get("motive", ""),
            }
        self.send(TWIN, "decision", {
            "draft": draft["index"], "save": decision["save"], "recommendation": recommendation["action"],
            "text": ("save: " if decision["save"] else "discard: ") + decision["motive"],
        })
        return decision
