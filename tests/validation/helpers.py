"""Lean builders for the tests.

Writing the whole envelope by hand in each test would hide what is being tested.
These shortcuts leave visible only what matters in each case.

Two starting points, both passing every check:
- `artifact()`: a requirement in the structure the agents emit (EARS statement,
  story in three parts, Gherkin criteria), in English, citing a meeting turn;
- `user_story()`: a plain user story in Portuguese that quotes its source, the
  format of the first prototype, which the envelope still accepts.
"""

from __future__ import annotations

from gigabrain.validation.model import (
    Artifact,
    ArtifactKind,
    Criterion,
    DocumentSection,
    Envelope,
    EvidenceRef,
    GlossaryEntry,
    LinkRef,
    RequirementType,
    SourceDocument,
    SpecificationDocument,
    Story,
)

# ---------------------------------------------------------------- structured requirement

TURN_TEXT = (
    "We need that when a topic receives the third report from different users, it goes to the "
    "review queue as one item, and the moderator is told only once."
)
CLEAN_STATEMENT = "When a topic receives the third report, the system shall create one item in the review queue."
CLEAN_STORY = Story(role="forum moderator", goal="to see the reports of a topic as one item",
                    benefit="I decide once per topic")
CLEAN_CRITERIA = [
    Criterion(scenario="Third report", given="a topic with two reports from different users",
              when="a third user reports the topic",
              then="the system creates one item for the topic in the review queue"),
    Criterion(scenario="Later report", given="a topic that is already in the review queue",
              when="another user reports it",
              then="the system increments the counter of the existing item"),
]


def source(source_id: str = "T2", text: str = TURN_TEXT, kind: str = "turn",
           origin: str = "meeting of 2026-03-12") -> SourceDocument:
    return SourceDocument(id=source_id, text=text, kind=kind, origin=origin)


def evidence(source_id: str = "T2", quote: str = "", reason: str = "") -> EvidenceRef:
    return EvidenceRef(source_id=source_id, quote=quote, reason=reason)


def artifact(
    artifact_id: str = "R1",
    *,
    kind: ArtifactKind = ArtifactKind.REQUIREMENT,
    title: str = "Group the reports of a topic",
    statement: str = CLEAN_STATEMENT,
    ears_pattern: str = "event",
    story: Story | None = CLEAN_STORY,
    rationale: str = "",
    criteria: list[Criterion] | None = None,
    evidences: list[EvidenceRef] | None = None,
    req_type: RequirementType = RequirementType.FUNCTIONAL,
    nfr_subtype: str = "",
    links: list[LinkRef] | None = None,
    supersedes: str = "",
) -> Artifact:
    parts = list(CLEAN_CRITERIA if criteria is None else criteria)
    return Artifact(
        id=artifact_id,
        kind=kind,
        title=title,
        statement=statement,
        ears_pattern=ears_pattern,
        story=story.text() if story else "",
        story_parts=story,
        rationale=rationale,
        acceptance_criteria=[c.text() for c in parts],
        criteria=parts,
        evidence=list([evidence()] if evidences is None else evidences),
        req_type=req_type,
        nfr_subtype=nfr_subtype,
        links=list(links or []),
        supersedes=supersedes,
    )


# ---------------------------------------------------------------- plain user story (Portuguese)

NEED_TEXT = (
    "O cliente quer que, quando um tópico receber três denúncias de usuários diferentes, o tópico "
    "entre numa fila de revisão e o moderador seja avisado uma vez só."
)
PT_STORY = "Como moderador do fórum, quero agrupar denúncias do mesmo tópico, para que eu decida uma vez por tópico."
PT_CRITERIA = [
    "Quando um tópico recebe a terceira denúncia, o sistema cria um item na fila.",
    "Quando o item é criado, o sistema envia uma notificação ao moderador.",
]


def need(source_id: str = "NEED-001", text: str = NEED_TEXT, origin: str = "Reunião de 12/03/2026") -> SourceDocument:
    return SourceDocument(id=source_id, text=text, kind="need", origin=origin)


def quote(source_id: str = "NEED-001", text: str = "o tópico entre numa fila de revisão",
          reason: str = "Define o destino do agrupamento.") -> EvidenceRef:
    return EvidenceRef(source_id=source_id, quote=text, reason=reason)


def user_story(
    artifact_id: str = "REQ-001",
    *,
    kind: ArtifactKind = ArtifactKind.USER_STORY,
    title: str = "Agrupar denúncias do mesmo tópico",
    story: str | None = None,
    statement: str = "",
    rationale: str = "",
    criteria: list[str] | None = None,
    evidences: list[EvidenceRef] | None = None,
    req_type: RequirementType = RequirementType.FUNCTIONAL,
    nfr_subtype: str = "",
    supersedes: str = "",
) -> Artifact:
    return Artifact(
        id=artifact_id,
        kind=kind,
        title=title,
        story=PT_STORY if story is None else story,
        statement=statement,
        rationale=rationale,
        acceptance_criteria=list(PT_CRITERIA if criteria is None else criteria),
        evidence=list([quote()] if evidences is None else evidences),
        req_type=req_type,
        nfr_subtype=nfr_subtype,
        supersedes=supersedes,
    )


# ---------------------------------------------------------------- envelope


def full_document() -> SpecificationDocument:
    return SpecificationDocument(
        title="Test specification",
        sections=[
            DocumentSection(id="introduction", title="Introduction", text="Text of the introduction."),
            DocumentSection(id="stakeholders", title="Stakeholders", text="Forum moderator."),
            DocumentSection(id="use_cases", title="Use cases", text="UC-01 Review a reported topic."),
            DocumentSection(id="glossary", title="Glossary", text="Review queue: a list."),
        ],
    )


def envelope(
    *artifacts: Artifact,
    sources: list[SourceDocument] | None = None,
    glossary: list[GlossaryEntry] | None = None,
    document: SpecificationDocument | None = None,
    run_id: str = "test",
) -> Envelope:
    if sources is None:
        # Only the default sources the artefacts cite: an uncited need would be
        # (rightly) reported as an orphan and hide what the test is about.
        cited = {ref.source_id for a in artifacts for ref in a.evidence}
        sources = [s for s in (source(), need()) if s.id in cited] or [source()]
    return Envelope(
        run_id=run_id,
        sources=list(sources),
        glossary=list(glossary or []),
        artifacts=list(artifacts) or [artifact()],
        document=document,
    )


def check_ids(findings) -> set[str]:
    return {f.check_id for f in findings}
