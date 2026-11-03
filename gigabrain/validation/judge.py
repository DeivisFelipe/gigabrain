"""Second tier of validation: an LLM judge for what the deterministic checks cannot see.

The deterministic tier compares words. It sees that a statement shares no words
with the utterance it cites. It does not see that an acceptance criterion adds a
rule nobody stated ("only administrators may see the list"), or that the Twin
proposed a history table the PO never asked for. Those are questions of meaning,
and only questions of that kind are asked here: the judge never reviews a
finding of the deterministic tier.

A question says what it asks, which competency it feeds and how heavy its
findings are. A finding of the judge:

- is marked EvidenceKind.LLM_JUDGMENT, and its message starts with "LLM judge:";
- must quote the requirement. A claim that is not in the requirement's own text
  is dropped, because then it is the judge that invented something;
- comes only from a claim the judge classified as "invented". Claims it
  classified as "inferred" (a rewording, the negative case of a stated rule,
  data a stated rule needs) are kept for analysis and never reported;
- is major for a rule (behaviour, permission, limit, data to keep, capability)
  and minor for a detail (the wording of a message, an identifier, a confirmation);
- counts in the gate like any other finding.

In a conversation the judge reads the dialogue, the Twin's questions included,
because a PO who answers "1. Yes" means something only next to the question
(host.dialogue_source). The dialogue is never cited as evidence.

The judge is off by default. LLMJudge receives the function that calls the model
(the Critic's call_llm, so every call is in the log) and keeps the answers of the
run in memory: an artefact that comes back unchanged is not judged twice.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Callable

from .model import Artifact, Dimension, EvidenceKind, Finding, Level, Severity, SourceDocument
from .registry import CheckContext
from .text import containment

#: Turns before the first cited one that also go to the judge. The Twin drafts a
#: requirement with the previous utterances in view (agents/twin.py, CONTEXT_UTTERANCES).
CONTEXT_TURNS = 6
#: Longest piece of one source sent to the judge (expert knowledge can be long).
MAX_SOURCE_CHARS = 6000
#: A dialogue is the whole conversation and is kept longer; its start (the request) matters most.
MAX_DIALOGUE_CHARS = 16000
#: At most this many claims of one answer become findings.
MAX_CLAIMS = 5
#: How much of a claim must be in the requirement's text (the threshold of quotes, CheckConfig).
MIN_CLAIM_CONTAINMENT = 0.85
#: The judge answers at temperature 0. With the API default, two runs over the same proposal
#: gave 1 and 5 findings (20/10/2026). It lowers the variation; it does not remove it.
TEMPERATURE = 0.0


@dataclass(frozen=True)
class JudgeQuestion:
    id: str
    dimension: Dimension
    severity: Severity
    weight: float
    description: str
    instruction: str
    #: Severity of an invented detail (the wording of a message, an identifier), lighter than a rule.
    detail_severity: Severity = Severity.MINOR


SYSTEM_PROMPT = """\
You are the judge of the Critic of GigaBrain, a multi-agent system for requirements engineering.
You did not write the requirement below and you gain nothing if it passes. You receive one
requirement and the sources it may rely on: the conversation with the PO, utterances of a meeting
("cited" ones and the "context" before them), expert knowledge and related requirements.

A conversation comes as a dialogue. The PO often answers the Twin's numbered questions briefly
("1. Yes; 3. it shows the date"): read each answer together with the question it answers. What a
Twin question or proposal message suggests counts as said only when the PO's answer accepts it.
"""

UNSUPPORTED_CONTENT = JudgeQuestion(
    id="judge.unsupported_content",
    dimension=Dimension.CORRECTNESS,
    severity=Severity.MAJOR,
    weight=1.5,
    description=(
        "The requirement (title, statement, story, acceptance criteria) states nothing that the "
        "sources do not say and that does not follow directly from them."
    ),
    instruction="""\
Question: does the requirement state a specific fact that the sources do not say?

Go through the title, statement, story and each acceptance criterion. For every piece that is not
plainly in the sources, decide:

"invented": it adds information nobody gave, for example a number, limit or period; who may or
may not do something; a record, field, table or history to keep; a message or text shown to the
user; a behaviour, state or rule; a capability nobody asked for.

"inferred": it adds nothing, for example
- a rewording of what the sources say;
- the generic frame of a scenario (a user, the system, normal operation);
- the negative or boundary case of a stated rule: if only librarians may delete a book, "a reader
  who tries to delete is refused" is inferred (but the exact text of the refusal is invented);
- the data a stated rule needs in order to work, even when nobody listed it as fields: to count
  loans per reader in the last 7 days, each loan needs the reader and the date;
- the "so that" part of a story, which is the reason and not a fact, unless it brings in a behaviour
  or capability nobody asked for.

For each invented piece also say its kind:
"rule": a behaviour, rule, permission or role, number, limit or period, data to keep, capability;
"detail": the exact wording of a message or label, an identifier, a confirmation, a layout.

Quote each piece exactly as written in the requirement, as short as possible. When you are not sure
it is invented, call it inferred.

Answer with a single JSON object:
{"claims": [{"claim": "exact excerpt of the requirement", "where": "title" | "statement" | "story" | "criterion <n>", "sources_say": "what the sources say about it, in one sentence", "verdict": "invented" | "inferred", "kind": "rule" | "detail"}]}
An empty list means that everything the requirement states is plainly in the sources.
""",
)

QUESTIONS: tuple[JudgeQuestion, ...] = (UNSUPPORTED_CONTENT,)
BY_ID = {q.id: q for q in QUESTIONS}
#: Weight of each question in the score, as `Check.weight` is for the checks.
WEIGHTS = {q.id: q.weight for q in QUESTIONS}


# --------------------------------------------------------------------------
# What the judge reads
# --------------------------------------------------------------------------


def material(artifact: Artifact, ctx: CheckContext) -> dict | None:
    """The requirement and its sources, or None when there is nothing to compare with.

    None is "I do not know", never "unsupported": without sources the question is
    not asked at all.
    """
    sources = _sources_of(artifact, ctx)
    if not sources:
        return None
    return {"requirement": _requirement(artifact), "sources": sources}


def _requirement(artifact: Artifact) -> dict:
    story = artifact.story_parts.text() if artifact.story_parts else artifact.story
    return {
        "title": artifact.title,
        "statement": artifact.statement,
        "story": story,
        "acceptance_criteria": [f"{i}. {text}" for i, text in enumerate(artifact.acceptance_criteria, start=1)],
    }


def _sources_of(artifact: Artifact, ctx: CheckContext) -> list[dict]:
    cited: list[SourceDocument] = []
    for ref in artifact.evidence:
        source = ctx.envelope.source(ref.source_id)
        if source is not None and source.kind == "conversation":
            # The PO's words alone, in place of the dialogue they belong to.
            source = ctx.envelope.source(f"{source.id}#dialogue") or source
        if source is not None and source not in cited:
            cited.append(source)
    turns = [s for s in ctx.envelope.sources if s.kind == "turn"]
    positions = [i for i, s in enumerate(turns) if any(s is c for c in cited)]
    context = turns[max(0, min(positions) - CONTEXT_TURNS):min(positions)] if positions else []
    return ([_source(s, "context") for s in context]
            + [_source(_without_itself(s, artifact.id), "cited") for s in cited])


def _without_itself(source: SourceDocument, artifact_id: str) -> SourceDocument:
    """An expert's knowledge without the lines about the requirement being judged.

    Once a requirement is saved, the knowledge of the expert it cites records it
    ("R1 (NFR) — <its statement>"). Left in, the requirement would be its own source.
    """
    if source.kind != "expert":
        return source
    own = re.compile(rf"\b{re.escape(artifact_id)}\b")
    text = "\n".join(line for line in source.text.splitlines() if not own.search(line))
    return SourceDocument(id=source.id, text=text, kind=source.kind, origin=source.origin)


def _source(source: SourceDocument, role: str) -> dict:
    limit = MAX_DIALOGUE_CHARS if source.kind == "dialogue" else MAX_SOURCE_CHARS
    data = {"id": source.id, "kind": source.kind, "role": role, "text": source.text[:limit]}
    if source.origin:
        data["origin"] = source.origin
    if len(source.text) > limit:
        data["truncated"] = True
    return data


# --------------------------------------------------------------------------
# The judge that calls a language model
# --------------------------------------------------------------------------


class LLMJudge:
    """JudgeProvider over a function with the shape of `provider.complete_json`.

    `call(role, system, messages) -> dict`. Inside the system it is the Critic's
    `call_llm`, which records each call (duration, tokens) in the event log.
    """

    role = "critic_judge"

    def __init__(self, call: Callable[[str, str, list[dict]], dict], name: str = "llm") -> None:
        self.call = call
        self.name = name
        self._answers: dict[str, list[dict]] = {}
        #: Claims the judge made that are not in the requirement: dropped, kept here to be counted.
        self.discarded: list[dict] = []
        #: Pieces the judge looked at and classified as inferred: never reported, kept to be counted.
        self.inferred: list[dict] = []

    @property
    def available(self) -> bool:
        return True

    def judge(self, *, question: str, dimension: Dimension, target_id: str, payload: dict) -> list[Finding]:
        spec = BY_ID[question]
        key = hashlib.sha256((question + json.dumps(payload, sort_keys=True, ensure_ascii=False)).encode()).hexdigest()
        if key not in self._answers:
            answer = self.call(self.role, SYSTEM_PROMPT + "\n" + spec.instruction,
                               [{"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=1)}],
                               temperature=TEMPERATURE)
            self._answers[key] = _claims(answer)
        findings = []
        for claim in self._answers[key]:
            if not _in_requirement(claim["claim"], payload["requirement"]):
                self.discarded.append({"target": target_id, **claim})
            elif claim["verdict"] != "invented":
                self.inferred.append({"target": target_id, **claim})
            elif len(findings) < MAX_CLAIMS:
                findings.append(_finding(spec, dimension, target_id, claim))
        return findings


def _claims(answer: Any) -> list[dict]:
    """The claims of an answer. An answer that does not answer the question is an error, not "nothing"."""
    if not isinstance(answer, dict) or "claims" not in answer:
        raise ValueError("the judge did not answer with a 'claims' list")
    items = answer["claims"] or []
    if not isinstance(items, list):
        raise ValueError("'claims' is not a list")
    claims = []
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("claim"), str) and item["claim"].strip():
            claims.append({
                "claim": item["claim"].strip(),
                "where": str(item.get("where") or ""),
                "sources_say": str(item.get("sources_say") or "").strip(),
                # Anything other than an explicit "inferred" counts as invented: the judge was asked
                # to say inferred when unsure, so a missing verdict is not a doubt it declared.
                "verdict": "inferred" if str(item.get("verdict", "")).strip().lower() == "inferred" else "invented",
                "kind": "detail" if str(item.get("kind", "")).strip().lower() == "detail" else "rule",
            })
    return claims


def _in_requirement(claim: str, requirement: dict) -> bool:
    text = " ".join([requirement["title"], requirement["statement"], requirement["story"],
                     *requirement["acceptance_criteria"]])
    return containment(claim, text) >= MIN_CLAIM_CONTAINMENT


def _locator(where: str) -> str:
    where = where.strip().lower()
    if where.startswith("criterion"):
        number = where.removeprefix("criterion").strip()
        return f"acceptance_criteria[{number}]" if number.isdigit() else "acceptance_criteria"
    return where if where in ("title", "statement", "story") else ""


def _finding(spec: JudgeQuestion, dimension: Dimension, target_id: str, claim: dict) -> Finding:
    what = "detail" if claim["kind"] == "detail" else "rule"
    message = f"LLM judge: \"{claim['claim']}\" is not said in the sources ({what})."
    if claim["sources_say"]:
        message += f" Sources: {claim['sources_say']}"
    return Finding(
        check_id=spec.id,
        dimension=dimension,
        severity=spec.detail_severity if claim["kind"] == "detail" else spec.severity,
        level=Level.ITEM,
        target_id=target_id,
        locator=_locator(claim["where"]),
        excerpt=claim["claim"][:160],
        message=message,
        suggestion="Remove it, or ask the PO for it. Do not replace it with another assumption.",
        evidence_kind=EvidenceKind.LLM_JUDGMENT,
    )
