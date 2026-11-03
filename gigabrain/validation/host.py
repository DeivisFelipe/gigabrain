"""Adapters between GigaBrain's own data and the validation model.

The validation core (model, engine, checks) knows nothing about agents, SQLite or
the event log. This module is where the two meet:

- a requirement as the agents emit it, or as the repository stores it -> Artifact
- the turns of a meeting, the PO messages in the log, an expert's knowledge
  file -> SourceDocument
- the Requirements Repository -> TraceabilityProvider
- the event log -> ReportStore

One property is kept on purpose: **the sources are never taken from the agent
that wrote the requirement**. The turns come from the transcript, the PO
messages come from the log. That independent path is what lets the Critic check
a declared origin instead of believing it.
"""

from __future__ import annotations

import json

from .. import knowledge
from ..database import EVOLUTION_LINKS, Database
from ..messages import CRITIC, REPOSITORY, Message
from .adapters.jsonl_store import report_from_dict
from .envelope import EnvelopeError, as_criteria, as_kind, as_links, as_req_type, as_story, as_subtype
from .model import (
    Artifact,
    ArtifactKind,
    Envelope,
    EvidenceRef,
    Provenance,
    RequirementType,
    SourceDocument,
    ValidationReport,
)
from .report import to_summary

#: Kinds of entries in the `sources` of a saved requirement that point to content.
CONTENT_SOURCE_KINDS = ("turn", "conversation", "expert", "requirement")


# --------------------------------------------------------------------------
# Requirements -> artefacts
# --------------------------------------------------------------------------


def _kind(value) -> ArtifactKind:
    """Kind of a requirement, tolerant: what an LLM wrote must never break the Critic."""
    try:
        return as_kind(value, has_statement=True)
    except (EnvelopeError, TypeError):
        return ArtifactKind.REQUIREMENT


def _req_type(value) -> RequirementType:
    try:
        return as_req_type(value)
    except (EnvelopeError, TypeError):
        return RequirementType.UNKNOWN


def artifact_from_requirement(
    content: dict,
    artifact_id: str,
    *,
    evidence: list[EvidenceRef] | None = None,
    links: list[dict] | None = None,
    base: str | None = None,
    rationale: str | None = None,
    provenance: Provenance | None = None,
    version: str = "1",
) -> Artifact:
    """Artefact from a requirement in the structure of requirement_format.py.

    `content` is what an agent proposes or drafts, or the content of a saved
    version. Links, the base requirement and the reason travel next to the
    content in a proposal, so they may come inside it or as arguments.
    """
    story_text, story_parts = as_story(content.get("story"))
    topics = content.get("topics") or content.get("topic") or []
    criteria = content.get("acceptance_criteria")
    criteria_text, criteria_parts = as_criteria(criteria if isinstance(criteria, list) else [])
    return Artifact(
        id=artifact_id,
        kind=_kind(content.get("kind")),
        title=str(content.get("title") or ""),
        statement=str(content.get("statement") or ""),
        ears_pattern=str(content.get("ears_pattern") or ""),
        story=story_text,
        story_parts=story_parts,
        rationale=str(rationale if rationale is not None else content.get("reason") or ""),
        acceptance_criteria=criteria_text,
        criteria=criteria_parts,
        evidence=list(evidence or []),
        req_type=_req_type(content.get("class")),
        nfr_subtype=as_subtype(content.get("subtype")),
        links=as_links(links if links is not None else content.get("links"), base if base is not None else content.get("base")),
        topics=[str(t) for t in (topics if isinstance(topics, list) else [topics])],
        provenance=provenance or Provenance(),
        version=version,
    )


def proposal_ids(db: Database, requirements: list[dict]) -> dict[str, str]:
    """The ids the proposed requirements get when approved, in order: {"R3": "P1", "R4": "P2"}.

    The Twin does not know P1, and writes R3 for the requirement it proposes just before: once
    saved, that link is right. A new version of an existing requirement takes no new id.
    """
    number = int(db._next_requirement_id()[1:])
    ids: dict[str, str] = {}
    for position, requirement in enumerate(requirements, start=1):
        if not (requirement.get("base") and db.get_requirement(requirement["base"])):
            ids[f"R{number}"] = f"P{position}"
            number += 1
    return ids


def with_run_links(requirement: dict, ids: dict[str, str]) -> dict:
    """The requirement with its links to the same proposal written as the Critic knows them."""
    links = requirement.get("links")
    if not links:
        return requirement
    return {**requirement, "links": [{**l, "target": ids.get(l.get("target"), l.get("target"))} if isinstance(l, dict) else l for l in links]}


# --------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------


def _events(db: Database, kinds: tuple[str, ...], conversation_id: str | None = None) -> list[dict]:
    """Contents of the messages of some kinds, oldest first, read straight from the log.

    The filter is in the query on purpose: a processed meeting has thousands of
    messages, and the Critic reads the log many times.
    """
    sql = f"SELECT content FROM event WHERE kind IN ({','.join('?' * len(kinds))})"
    params: tuple = tuple(kinds)
    if conversation_id is not None:
        sql += " AND conversation_id = ?"
        params += (conversation_id,)
    return [json.loads(row[0]) for row in db.con.execute(sql + " ORDER BY id", params)]


def sources_from_turns(turns: list[dict], origin: str) -> list[SourceDocument]:
    """The turns of a meeting ({id, speaker, text}) as sources."""
    return [
        SourceDocument(id=t["id"], text=t.get("text", ""), kind="turn",
                       origin=f"{origin}, {t['speaker']}" if t.get("speaker") else origin)
        for t in turns
    ]


def conversation_source(db: Database, conversation_id: str) -> SourceDocument | None:
    """Everything the PO said in a conversation, read from the log."""
    said = [content.get("text", "") for content in _events(db, ("po_request",), conversation_id)]
    if not said:
        return None
    return SourceDocument(id=conversation_id, text="\n".join(said), kind="conversation",
                          origin="conversation with the PO")


#: Who says each line of a dialogue, by kind of message.
_DIALOGUE_SPEAKERS = {"po_request": "PO", "question_to_po": "Twin (question)", "proposal": "Twin (proposal message)"}


def dialogue_id(conversation_id: str) -> str:
    return f"{conversation_id}#dialogue"


def dialogue_source(db: Database, conversation_id: str) -> SourceDocument | None:
    """The conversation as a dialogue: what the PO said, and what the Twin asked or proposed.

    For the LLM judge only. A PO who answers "1. Yes" means something only next
    to the question. It is never cited as evidence, so the deterministic checks keep
    comparing with the PO's words alone, and nothing can be grounded in the Twin's own text.
    """
    sql = (f"SELECT kind, content FROM event WHERE conversation_id = ? "
           f"AND kind IN ({','.join('?' * len(_DIALOGUE_SPEAKERS))}) ORDER BY id")
    lines = []
    for kind, content in db.con.execute(sql, (conversation_id, *_DIALOGUE_SPEAKERS)):
        text = (json.loads(content).get("text") or "").strip()
        if text:
            lines.append(f"{_DIALOGUE_SPEAKERS[kind]}: {text}")
    if not any(line.startswith("PO: ") for line in lines):
        return None
    return SourceDocument(id=dialogue_id(conversation_id), text="\n".join(lines), kind="dialogue",
                          origin="conversation with the PO, with the Twin's questions")


def utterance_sources(db: Database, conversation_id: str) -> list[SourceDocument]:
    """The stakeholder utterances of a processed meeting, read from the log."""
    return [
        SourceDocument(id=content["turn"], text=content.get("text", ""), kind="turn",
                       origin=f"meeting {conversation_id}, {content.get('speaker', '?')}")
        for content in _events(db, ("utterance",), conversation_id)
    ]


def expert_source(db: Database, expert_id: str) -> SourceDocument | None:
    """The knowledge file of an expert, as it is now."""
    expert = db.get_expert(expert_id)
    if not expert:
        return None
    return SourceDocument(id=f"expert:{expert_id}", text=knowledge.read(expert["file"]), kind="expert",
                          origin=f"knowledge of the {expert_id} expert, v{expert['knowledge_version']}")


def requirement_source(db: Database, requirement_id: str) -> SourceDocument | None:
    """A saved requirement used as the source of another (a related requirement)."""
    req = db.get_requirement(requirement_id)
    if not req:
        return None
    text = req["content"].get("statement") or req["title"]
    return SourceDocument(id=f"requirement:{requirement_id}", text=text, kind="requirement",
                          origin=f"{requirement_id} v{req['current_version']}")


def consulted_in(db: Database, conversation_id: str) -> tuple[list[str], list[str]]:
    """(experts consulted, related requirements) of a conversation, read from the log.

    It is what the Council will record as the sources of an approved requirement,
    rebuilt here from the events instead of asked from the Council.
    """
    experts: list[str] = []
    related: list[str] = []
    for content in _events(db, ("expert_answer", "consolidated_answer"), conversation_id):
        if content.get("expert_id") and content["expert_id"] not in experts:
            experts.append(content["expert_id"])
        related += [r for r in content.get("related_requirements") or [] if r not in related]
    return experts, related


def conversation_evidence(db: Database, conversation_id: str) -> tuple[list[EvidenceRef], list[SourceDocument]]:
    """Evidence and sources of a requirement proposed in a conversation with the PO."""
    sources: list[SourceDocument] = []
    spoken = conversation_source(db, conversation_id)
    if spoken:
        sources.append(spoken)
    experts, related = consulted_in(db, conversation_id)
    sources += [s for s in (expert_source(db, e) for e in experts) if s]
    sources += [s for s in (requirement_source(db, r) for r in related) if s]
    evidence = [EvidenceRef(source_id=s.id) for s in sources]
    dialogue = dialogue_source(db, conversation_id) if spoken else None
    return evidence, sources + ([dialogue] if dialogue else [])  # the dialogue is read, never cited


# --------------------------------------------------------------------------
# The repository -> artefacts and sources
# --------------------------------------------------------------------------


class RepositoryReader:
    """Reads saved requirements as artefacts, each with the sources it declares.

    The `sources` of a saved version say where it came from: the turns of a
    meeting, the conversation with the PO, the experts consulted, related
    requirements. Each one is read from where it lives (the log, the knowledge
    files, the repository) and cached, because many requirements share them.
    """

    def __init__(self, db: Database) -> None:
        self.db = db
        self._modes = {c["id"]: c["mode"] for c in db.list_conversations()}
        self._turns: dict[str, dict[str, SourceDocument]] = {}
        self._other: dict[tuple[str, str], SourceDocument | None] = {}
        self._ambiguous: set[str] | None = None

    def meetings(self) -> list[str]:
        return [cid for cid, mode in self._modes.items() if mode.startswith("meeting_")]

    def turns(self, conversation_id: str) -> dict[str, SourceDocument]:
        if conversation_id not in self._turns:
            self._turns[conversation_id] = {s.id: s for s in utterance_sources(self.db, conversation_id)}
        return self._turns[conversation_id]

    def _turn_id(self, conversation_id: str, turn: str) -> str:
        """The id of a turn as a source: the turn id, qualified only when two meetings reuse it."""
        if self._ambiguous is None:
            texts: dict[str, set[str]] = {}
            for meeting in self.meetings():
                for turn_id, source in self.turns(meeting).items():
                    texts.setdefault(turn_id, set()).add(source.text)
            self._ambiguous = {turn_id for turn_id, seen in texts.items() if len(seen) > 1}
        return f"{turn}@{conversation_id}" if turn in self._ambiguous else turn

    def turn_source(self, conversation_id: str, turn: str) -> SourceDocument | None:
        source = self.turns(conversation_id).get(turn)
        if source is None:
            return None
        return SourceDocument(id=self._turn_id(conversation_id, turn), text=source.text, kind="turn", origin=source.origin)

    def all_turns(self) -> list[SourceDocument]:
        return [self.turn_source(meeting, turn) for meeting in self.meetings() for turn in self.turns(meeting)]

    def _cached(self, kind: str, ref: str, build) -> SourceDocument | None:
        if (kind, ref) not in self._other:
            self._other[(kind, ref)] = build(self.db, ref)
        return self._other[(kind, ref)]

    def declared(self, req: dict, conversation_id: str | None) -> tuple[list[EvidenceRef], list[SourceDocument]]:
        """(what the requirement cites, the cited sources that could be read)."""
        evidence: list[EvidenceRef] = []
        sources: list[SourceDocument] = []
        for entry in req.get("sources") or []:
            kind, ref = entry.get("kind"), str(entry.get("ref") or "")
            if not ref or kind not in CONTENT_SOURCE_KINDS:
                continue
            if kind == "turn":
                source = self.turn_source(conversation_id, ref) if conversation_id else None
                # A cited turn the log does not have stays as evidence without a
                # source: validation will say the cited source does not exist.
                evidence.append(EvidenceRef(source_id=source.id if source else ref))
            elif kind == "conversation":
                if self._modes.get(ref, "").startswith("meeting_"):
                    continue  # the meeting itself; what grounds the requirement are its turns
                source = self._cached(kind, ref, conversation_source)
                evidence.append(EvidenceRef(source_id=ref))
                dialogue = self._cached("dialogue", ref, dialogue_source)
                if source is not None and dialogue is not None:
                    sources.append(dialogue)  # read by the judge, never cited
            elif kind == "expert":
                source = self._cached(kind, ref, expert_source)
                evidence.append(EvidenceRef(source_id=f"expert:{ref}"))
            else:
                source = self._cached(kind, ref, requirement_source)
                evidence.append(EvidenceRef(source_id=f"requirement:{ref}"))
            if source is not None:
                sources.append(source)
        return evidence, sources

    def artifact(self, req: dict, *, with_sources: bool = True) -> tuple[Artifact, list[SourceDocument]]:
        links = [{"kind": l["kind"], "target": l["target_id"], "reason": l.get("reason") or ""}
                 for l in self.db.list_links(req["id"]) if l["source_id"] == req["id"]]
        content = {**req["content"], "kind": req.get("kind") or req["content"].get("kind")}
        if not with_sources:
            return artifact_from_requirement(content, req["id"], links=links, version=str(req["current_version"])), []
        versions = self.db.requirement_versions(req["id"])
        current = next((v for v in versions if v["version"] == req["current_version"]), versions[-1] if versions else {})
        evidence, sources = self.declared(req, current.get("conversation_id"))
        artifact = artifact_from_requirement(content, req["id"], evidence=evidence, links=links,
                                             rationale=current.get("reason") or "", version=str(req["current_version"]))
        return artifact, sources


def artifact_from_saved(db: Database, req: dict, *, reader: RepositoryReader | None = None) -> tuple[Artifact, list[SourceDocument]]:
    """A requirement of the repository as an artefact, with the sources it declares."""
    return (reader or RepositoryReader(db)).artifact(req)


def repository_envelope(db: Database, *, only_active: bool = True, with_sources: bool = True,
                        exclude: set[str] | None = None, run_id: str = "") -> Envelope:
    """The Requirements Repository as an envelope.

    With `with_sources`, every requirement comes with the sources it declares,
    and every stakeholder utterance of the meetings in the log comes along too,
    so that coverage can be measured. Without, the artefacts come alone: enough
    to be the context a candidate is checked against.
    """
    reader = RepositoryReader(db)
    artifacts: list[Artifact] = []
    sources: dict[str, SourceDocument] = {}
    for req in db.list_requirements(only_active=only_active):
        if exclude and req["id"] in exclude:
            continue
        artifact, declared = reader.artifact(req, with_sources=with_sources)
        artifacts.append(artifact)
        for source in declared:
            sources.setdefault(source.id, source)
    if with_sources:
        for source in reader.all_turns():
            sources.setdefault(source.id, source)
    return Envelope(run_id=run_id, artifacts=artifacts, sources=list(sources.values()))


def validation_of(db: Database, requirement_id: str, *, engine=None) -> dict | None:
    """The Critic's scores of a saved requirement, computed now.

    Its five item competencies and, taken with the rest of the repository, the
    five document ones. It is deterministic, so it is computed on demand, the way
    the format check is, instead of stored. `saved_with` is the report the
    version was saved with, when the Critic was on at the time.
    """
    from .engine import ValidationEngine  # here to keep the import of this module light

    req = db.get_requirement(requirement_id)
    if not req:
        return None
    reader = RepositoryReader(db)
    artifact, sources = reader.artifact(req)
    context = [reader.artifact(other, with_sources=False)[0]
               for other in db.list_requirements(only_active=True) if other["id"] != requirement_id]
    engine = engine or ValidationEngine(trace=RepositoryTraceability(db))
    report = engine.validate(Envelope(artifacts=[artifact, *context], sources=sources), focus={requirement_id})
    summary = to_summary(report)
    item = summary["items"][requirement_id]
    saved_with = next((s for s in req.get("sources") or [] if s.get("kind") == "validation"), None)
    return {
        "verdict": summary["verdict"], "passed": summary["passed"], "failed_dimensions": summary["failed_dimensions"],
        "score": item["score"], "scores": {d: v["score"] for d, v in item["scores"].items()},
        "document": summary["document"],
        "findings": summary["findings"], "counts": summary["counts"],
        "ruleset_version": report.ruleset_version, "policy_version": report.policy_version,
        "saved_with": {"report_id": saved_with["ref"], "verdict": saved_with.get("verdict")} if saved_with else None,
    }


# --------------------------------------------------------------------------
# Ports
# --------------------------------------------------------------------------


class RepositoryTraceability:
    """TraceabilityProvider over the Requirements Repository (read only)."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def knows(self, requirement_id: str) -> bool:
        return self.db.get_requirement(requirement_id) is not None

    def predecessors(self, requirement_id: str) -> list[str]:
        return [l["target_id"] for l in self.db.list_links(requirement_id)
                if l["source_id"] == requirement_id and l["kind"] in EVOLUTION_LINKS]

    def impacted_by(self, requirement_id: str) -> list[str]:
        return [l["source_id"] for l in self.db.list_links(requirement_id)
                if l["target_id"] == requirement_id and l["kind"] == "depends_on"]


def report_content(report: ValidationReport, *, stage: str, summary: str, full: bool = False,
                   max_findings: int = 20, suggestions: bool = True, **extra) -> dict:
    """Content of a "validation_report" message.

    The compact form (verdict, one score per competency, the findings) plus what
    makes the run reproducible: the hash of the input, the versions of the rule
    set and of the policy, and the checks that did not run and why. Validation
    is deterministic, so that is enough to get the same report again; `full`
    also stores the whole report, at about five times the size.
    """
    skipped: dict[str, list[str]] = {}
    for check_id, reason in report.checks_skipped.items():
        skipped.setdefault(reason, []).append(check_id)
    content = {
        "stage": stage, "summary": summary,
        **to_summary(report, max_findings=max_findings, suggestions=suggestions), **extra,
        "envelope_hash": report.envelope_hash, "checks_run": len(report.checks_run),
        "checks_skipped": skipped,  # {why: [which checks]}
    }
    if full:
        content["report"] = report.to_dict()
    return content


class EventReportStore:
    """ReportStore over the event log: a report is a message from the Critic.

    Nothing new is added to the databases: the reports are where every other
    message between the agents already is (log.db and logs/<conversation>.jsonl).

    `find` and `runs_of` return the compact content of the message, which is
    always there. `load` and `history` return whole reports, and so only find the
    ones stored with ValidationSettings.full_reports on.
    """

    def __init__(self, db: Database, recorder=None, conversation_id: str | None = None) -> None:
        self.db = db
        self.recorder = recorder
        self.conversation_id = conversation_id

    def save(self, report: ValidationReport, *, recipient: str = REPOSITORY, stage: str = "stored",
             summary: str = "", **extra) -> str:
        if self.recorder is None:
            raise RuntimeError("EventReportStore needs a recorder to save a report")
        self.recorder.record(Message(sender=CRITIC, recipient=recipient, kind="validation_report",
                                     content=report_content(report, stage=stage, summary=summary, full=True, **extra),
                                     conversation_id=self.conversation_id))
        return report.run_id

    def _contents(self) -> list[dict]:
        return _events(self.db, ("validation_report",))

    def find(self, report_id: str) -> dict | None:
        """The message content of a report (always available)."""
        return next((c for c in self._contents() if c.get("report_id") == report_id), None)

    def runs_of(self, envelope_hash: str) -> list[dict]:
        """Every report of the same input, compact: the basis for comparing runs."""
        return [c for c in self._contents() if c.get("envelope_hash") == envelope_hash]

    def load(self, report_id: str) -> ValidationReport | None:
        content = self.find(report_id)
        return report_from_dict(content["report"]) if content and isinstance(content.get("report"), dict) else None

    def history(self, envelope_hash: str) -> list[ValidationReport]:
        return [report_from_dict(c["report"]) for c in self.runs_of(envelope_hash) if isinstance(c.get("report"), dict)]
