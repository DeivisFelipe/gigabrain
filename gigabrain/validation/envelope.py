"""Reading and writing the validation envelope (the input contract).

The envelope is JSON, versioned by `schema_version`, and it is the only input
the engine knows. Every source format (a requirement as the agents emit it, a
requirement read from the repository, a file written by hand) becomes an
envelope through an adapter: validation/host.py for what lives in GigaBrain,
this module for files.

Design decision: the contract is deliberately poor. It describes what
the project produces today plus the minimum needed to check grounding and
links. A field nobody fills in is a field that lies in the report.

Format (1.1; 1.0 files are still read):

    {
      "schema_version": "1.1",
      "run_id": "optional",
      "project": "gigabrain",
      "sources":   [{"id", "text", "kind", "origin"}],
      "glossary":  [{"term", "definition"}],
      "artifacts": [{
          "id", "kind", "title", "statement", "ears_pattern", "rationale",
          "story": "As a ..., I want ..., so that ..."  |  {"role", "goal", "benefit"},
          "acceptance_criteria": ["text", ...]  |  [{"scenario", "given", "when", "then"}, ...],
          "evidence": [{"source_id", "quote", "reason"}],
          "class": "FR" | "NFR", "subtype": "performance" | "PE" | ...,
          "links": [{"kind", "target", "reason"}], "base": "R3", "topics": [...],
          "version", "supersedes",
          "provenance": {"agent_id", "agent_role", "model", "temperature",
                         "seed", "prompt_version", "timestamp"}
      }],
      "document": {"title", "sections": [{"id", "title", "text"}]}
    }

`kind` is requirement | technical_enabler | user_story | shall_statement |
question; the values the agents use ("business", "enabler") are accepted too.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .model import (
    NFR_SUBTYPE_CODES,
    Artifact,
    ArtifactKind,
    Criterion,
    DocumentSection,
    Envelope,
    EvidenceRef,
    GlossaryEntry,
    LinkRef,
    Provenance,
    RequirementType,
    SourceDocument,
    SpecificationDocument,
    Story,
)

SUPPORTED_SCHEMA_VERSIONS = ("1.0", "1.1")

#: Values of "kind" the agents use, mapped to the kinds of the model.
_HOST_KINDS = {"business": ArtifactKind.REQUIREMENT, "enabler": ArtifactKind.TECHNICAL_ENABLER}


class EnvelopeError(ValueError):
    """Malformed input. The message is meant for whoever wrote the file."""


def _require(data: dict, key: str, where: str) -> Any:
    if not isinstance(data, dict) or key not in data or data[key] in (None, ""):
        raise EnvelopeError(f"Required field missing in {where}: '{key}'.")
    return data[key]


def as_kind(value: str | None, *, has_statement: bool = False) -> ArtifactKind:
    if not value:
        return ArtifactKind.REQUIREMENT if has_statement else ArtifactKind.USER_STORY
    if value in _HOST_KINDS:
        return _HOST_KINDS[value]
    try:
        return ArtifactKind(value)
    except ValueError:
        valid = ", ".join(k.value for k in ArtifactKind)
        raise EnvelopeError(f"Unknown artefact kind: '{value}'. Use one of: {valid}.")


def as_req_type(value: str | None) -> RequirementType:
    if not value:
        return RequirementType.UNKNOWN
    normalized = str(value).strip().upper()
    if normalized in ("FR", "F", "FUNCTIONAL"):
        return RequirementType.FUNCTIONAL
    if normalized in ("NFR", "NF", "NON_FUNCTIONAL", "NONFUNCTIONAL"):
        return RequirementType.NON_FUNCTIONAL
    raise EnvelopeError(f"Unknown class: '{value}'. Use 'FR' or 'NFR'.")


def as_subtype(value: str | None) -> str:
    """Subtype name from a name or from one of the codes the agents use (PE, SE...)."""
    text = str(value or "").strip()
    if not text:
        return ""
    return NFR_SUBTYPE_CODES.get(text.upper(), text.lower().replace(" ", "_").replace("-", "_"))


def as_story(value: Any) -> tuple[str, Story | None]:
    """(story as text, story in parts or None)."""
    if isinstance(value, dict):
        parts = Story(*(str(value.get(k) or "").strip() for k in ("role", "goal", "benefit")))
        return (parts.text() if any((parts.role, parts.goal, parts.benefit)) else ""), parts
    return str(value or ""), None


def as_criteria(values: Any, where: str = "acceptance_criteria") -> tuple[list[str], list[Criterion]]:
    """(criteria as text, criteria in Gherkin form). The second list is empty for plain text."""
    if not isinstance(values, list):
        raise EnvelopeError(f"'acceptance_criteria' in {where} must be a list.")
    if values and all(isinstance(v, dict) for v in values):
        criteria = [Criterion(*(str(v.get(k) or "").strip() for k in ("scenario", "given", "when", "then"))) for v in values]
        return [c.text() or c.scenario for c in criteria], criteria
    return [str(v) for v in values], []


def as_links(values: Any, base: str | None = None) -> list[LinkRef]:
    links = [LinkRef(kind="base", target=str(base))] if base else []
    for value in values or []:
        if isinstance(value, dict):
            links.append(LinkRef(kind=str(value.get("kind") or ""), target=str(value.get("target") or ""),
                                 reason=str(value.get("reason") or "")))
    return links


def _parse_provenance(data: dict | None) -> Provenance:
    data = data or {}
    return Provenance(
        agent_id=str(data.get("agent_id", "unknown")),
        agent_role=str(data.get("agent_role", "unknown")),
        model=str(data.get("model", "")),
        temperature=data.get("temperature"),
        seed=data.get("seed"),
        prompt_version=str(data.get("prompt_version", "")),
        timestamp=str(data.get("timestamp", "")),
    )


def _parse_artifact(data: dict, index: int) -> Artifact:
    where = f"artifacts[{index}]"
    artifact_id = str(_require(data, "id", where))
    criteria_text, criteria = as_criteria(data.get("acceptance_criteria") or [], where)

    evidence_raw = data.get("evidence") or []
    if not isinstance(evidence_raw, list):
        raise EnvelopeError(f"'evidence' in {where} must be a list.")
    evidence = [
        EvidenceRef(
            source_id=str(_require(e, "source_id", f"{where}.evidence[{i}]")),
            quote=str(e.get("quote", "")),
            reason=str(e.get("reason", "")),
        )
        for i, e in enumerate(evidence_raw)
    ]
    story_text, story_parts = as_story(data.get("story"))
    statement = str(data.get("statement", ""))

    return Artifact(
        id=artifact_id,
        kind=as_kind(data.get("kind"), has_statement=bool(statement.strip())),
        title=str(data.get("title", "")),
        story=story_text,
        statement=statement,
        rationale=str(data.get("rationale", "")),
        acceptance_criteria=criteria_text,
        evidence=evidence,
        req_type=as_req_type(data.get("class") or data.get("type")),
        nfr_subtype=as_subtype(data.get("subtype") or data.get("nfr_subtype")),
        version=str(data.get("version", "1.0")),
        supersedes=str(data.get("supersedes") or ""),
        provenance=_parse_provenance(data.get("provenance")),
        raw=str(data.get("raw", "")),
        story_parts=story_parts,
        criteria=criteria,
        ears_pattern=str(data.get("ears_pattern") or ""),
        links=as_links(data.get("links"), data.get("base")),
        topics=[str(t) for t in data.get("topics") or []],
    )


def from_dict(data: dict) -> Envelope:
    """Build an Envelope from an already deserialised dictionary."""
    if not isinstance(data, dict):
        raise EnvelopeError("The envelope must be a JSON object.")

    version = str(data.get("schema_version", "1.0"))
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        known = ", ".join(SUPPORTED_SCHEMA_VERSIONS)
        raise EnvelopeError(f"schema_version '{version}' is not supported. Known versions: {known}.")

    sources = [
        SourceDocument(
            id=str(_require(s, "id", f"sources[{i}]")),
            text=str(s.get("text", "")),
            kind=str(s.get("kind", "need")),
            origin=str(s.get("origin", "")),
        )
        for i, s in enumerate(data.get("sources") or [])
    ]

    glossary = [
        GlossaryEntry(
            term=str(_require(g, "term", f"glossary[{i}]")),
            definition=str(g.get("definition", "")),
        )
        for i, g in enumerate(data.get("glossary") or [])
    ]

    artifacts = [_parse_artifact(a, i) for i, a in enumerate(data.get("artifacts") or [])]

    document = None
    document_raw = data.get("document")
    if document_raw:
        sections = [
            DocumentSection(
                id=str(_require(s, "id", f"document.sections[{i}]")),
                title=str(s.get("title", "")),
                text=str(s.get("text", "")),
            )
            for i, s in enumerate(document_raw.get("sections") or [])
        ]
        document = SpecificationDocument(title=str(document_raw.get("title", "")), sections=sections)

    return Envelope(
        schema_version=version,
        run_id=str(data.get("run_id", "")),
        project=str(data.get("project", "gigabrain")),
        sources=sources,
        glossary=glossary,
        artifacts=artifacts,
        document=document,
    )


def from_json(text: str) -> Envelope:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EnvelopeError(f"Invalid JSON: {exc}") from exc
    return from_dict(data)


def load(path: str | Path) -> Envelope:
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        # The outer layer only knows EnvelopeError; letting FileNotFoundError leak
        # would make every caller handle two families of errors.
        raise EnvelopeError(f"Could not read '{target}': {exc.strerror}.") from exc
    return from_json(text)


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------

_REQ_TYPE_OUT = {
    RequirementType.FUNCTIONAL: "FR",
    RequirementType.NON_FUNCTIONAL: "NFR",
}


def _artifact_to_dict(artifact: Artifact) -> dict:
    data: dict[str, Any] = {
        "id": artifact.id,
        "kind": artifact.kind.value,
        "title": artifact.title,
        "version": artifact.version,
    }
    if artifact.statement:
        data["statement"] = artifact.statement
    if artifact.ears_pattern:
        data["ears_pattern"] = artifact.ears_pattern
    if artifact.story_parts is not None:
        parts = artifact.story_parts
        data["story"] = {"role": parts.role, "goal": parts.goal, "benefit": parts.benefit}
    elif artifact.story:
        data["story"] = artifact.story
    if artifact.rationale:
        data["rationale"] = artifact.rationale
    if artifact.criteria:
        data["acceptance_criteria"] = [
            {"scenario": c.scenario, "given": c.given, "when": c.when, "then": c.then} for c in artifact.criteria
        ]
    elif artifact.acceptance_criteria:
        data["acceptance_criteria"] = list(artifact.acceptance_criteria)
    if artifact.evidence:
        data["evidence"] = [
            {"source_id": e.source_id, "quote": e.quote, "reason": e.reason} for e in artifact.evidence
        ]
    if artifact.req_type in _REQ_TYPE_OUT:
        data["class"] = _REQ_TYPE_OUT[artifact.req_type]
    if artifact.nfr_subtype:
        data["subtype"] = artifact.nfr_subtype
    base = [link.target for link in artifact.links if link.kind == "base"]
    if base:
        data["base"] = base[0]
    links = [link for link in artifact.links if link.kind != "base"]
    if links:
        data["links"] = [{"kind": l.kind, "target": l.target, "reason": l.reason} for l in links]
    if artifact.topics:
        data["topics"] = list(artifact.topics)
    if artifact.supersedes:
        data["supersedes"] = artifact.supersedes
    if artifact.raw:
        data["raw"] = artifact.raw
    data["provenance"] = {
        "agent_id": artifact.provenance.agent_id,
        "agent_role": artifact.provenance.agent_role,
        "model": artifact.provenance.model,
        "temperature": artifact.provenance.temperature,
        "seed": artifact.provenance.seed,
        "prompt_version": artifact.provenance.prompt_version,
        "timestamp": artifact.provenance.timestamp,
    }
    return data


def to_dict(envelope: Envelope) -> dict:
    """Serialise in the contract format: the exact inverse of `from_dict`.

    Writing this by hand, instead of dumping the dataclass fields, is what
    guarantees a round trip without loss: the contract says "class" where the
    model says `req_type`, and an automatic serialisation has already lost the
    classification once (caught by the round-trip test).
    """
    data: dict[str, Any] = {
        "schema_version": envelope.schema_version,
        "project": envelope.project,
    }
    if envelope.run_id:
        data["run_id"] = envelope.run_id
    if envelope.sources:
        data["sources"] = [
            {"id": s.id, "kind": s.kind, "origin": s.origin, "text": s.text} for s in envelope.sources
        ]
    if envelope.glossary:
        data["glossary"] = [{"term": g.term, "definition": g.definition} for g in envelope.glossary]
    data["artifacts"] = [_artifact_to_dict(a) for a in envelope.artifacts]
    if envelope.document is not None:
        data["document"] = {
            "title": envelope.document.title,
            "sections": [{"id": s.id, "title": s.title, "text": s.text} for s in envelope.document.sections],
        }
    return data


def dumps(envelope: Envelope, *, indent: int = 2) -> str:
    return json.dumps(to_dict(envelope), ensure_ascii=False, indent=indent)
