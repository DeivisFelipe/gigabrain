"""Domain model of the GigaBrain validation layer.

Everything validation handles is described here: what comes in (the artefacts
the agents produce, plus the sources that should support them), what goes out
(findings, one score per dimension and a verdict) and the labels that give those
data a meaning (level, dimension, severity, kind of evidence).

Design references:
- The five item dimensions and the five document dimensions are the ten
  parameters of the ReqInOne expert questionnaire (Zhu, Cordeiro and Sun),
  reused as the ruler.
- The requirement structure (EARS statement + user story + Gherkin criteria +
  FR/NFR class) is the one defined in gigabrain/requirement_format.py.
"""

from __future__ import annotations

import enum
import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any


# --------------------------------------------------------------------------
# Labels
# --------------------------------------------------------------------------


class Level(str, enum.Enum):
    """Level at which a finding or a score is produced.

    ITEM      -> intermediate specification: the artefact of a single agent.
    DOCUMENT  -> consolidated specification: the requirements taken together.
    """

    ITEM = "item"
    DOCUMENT = "document"


class Dimension(str, enum.Enum):
    """Quality dimensions (the "competencies" every artefact is scored on).

    The first five are item dimensions (Part 2 of the ReqInOne questionnaire,
    which rates single requirements) and the last five are document dimensions
    (Part 1, which rates the specification as a whole).
    """

    # item level
    CONFORMING = "conforming"
    UNAMBIGUOUS = "unambiguous"
    VERIFIABLE = "verifiable"
    CORRECTNESS = "correctness"
    UNDERSTANDABLE = "understandable"

    # document level
    INTERNAL_CONSISTENCY = "internal_consistency"
    NON_REDUNDANCY = "non_redundancy"
    COMPLETENESS = "completeness"
    CONCISENESS = "conciseness"
    TRACEABILITY = "traceability"

    @property
    def level(self) -> Level:
        return Level.ITEM if self in ITEM_DIMENSIONS else Level.DOCUMENT

    @property
    def label(self) -> str:
        return DIMENSION_LABELS[self]


ITEM_DIMENSIONS: tuple[Dimension, ...] = (
    Dimension.CONFORMING,
    Dimension.UNAMBIGUOUS,
    Dimension.VERIFIABLE,
    Dimension.CORRECTNESS,
    Dimension.UNDERSTANDABLE,
)

DOCUMENT_DIMENSIONS: tuple[Dimension, ...] = (
    Dimension.INTERNAL_CONSISTENCY,
    Dimension.NON_REDUNDANCY,
    Dimension.COMPLETENESS,
    Dimension.CONCISENESS,
    Dimension.TRACEABILITY,
)

DIMENSION_LABELS: dict[Dimension, str] = {
    Dimension.CONFORMING: "Conforming",
    Dimension.UNAMBIGUOUS: "Unambiguous",
    Dimension.VERIFIABLE: "Verifiable",
    Dimension.CORRECTNESS: "Correctness (grounded in the source)",
    Dimension.UNDERSTANDABLE: "Understandable",
    Dimension.INTERNAL_CONSISTENCY: "Internal consistency",
    Dimension.NON_REDUNDANCY: "Non-redundancy",
    Dimension.COMPLETENESS: "Completeness",
    Dimension.CONCISENESS: "Conciseness",
    Dimension.TRACEABILITY: "Traceability",
}


class Severity(str, enum.Enum):
    """How serious a finding is. Ordered: BLOCKER > MAJOR > MINOR > INFO."""

    BLOCKER = "blocker"
    MAJOR = "major"
    MINOR = "minor"
    INFO = "info"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]

    @property
    def label(self) -> str:
        return _SEVERITY_LABELS[self]


_SEVERITY_RANK: dict[Severity, int] = {
    Severity.BLOCKER: 3,
    Severity.MAJOR: 2,
    Severity.MINOR: 1,
    Severity.INFO: 0,
}

_SEVERITY_LABELS: dict[Severity, str] = {
    Severity.BLOCKER: "blocking",
    Severity.MAJOR: "major",
    Severity.MINOR: "minor",
    Severity.INFO: "info",
}


class EvidenceKind(str, enum.Enum):
    """How a finding was produced.

    Telling these apart is a design decision: deterministic findings
    are reproducible and auditable; the findings of an LLM judge are not, so
    they stay separate in the report and can be switched off for ablation.
    """

    DETERMINISTIC = "deterministic"
    LLM_JUDGMENT = "llm_judgment"
    HUMAN = "human"


class Verdict(str, enum.Enum):
    """Outcome of the quality gate."""

    APPROVED = "approved"
    APPROVED_WITH_REMARKS = "approved_with_remarks"
    REJECTED = "rejected"

    @property
    def label(self) -> str:
        return _VERDICT_LABELS[self]


_VERDICT_LABELS: dict[Verdict, str] = {
    Verdict.APPROVED: "approved",
    Verdict.APPROVED_WITH_REMARKS: "approved with remarks",
    Verdict.REJECTED: "rejected",
}


class ArtifactKind(str, enum.Enum):
    """Kind of intermediate artefact an agent produces.

    REQUIREMENT is the structure every GigaBrain agent emits today: an EARS
    "shall" statement, a user story and Gherkin acceptance criteria in the same
    object. TECHNICAL_ENABLER is the same structure with "kind": "enabler".

    USER_STORY and SHALL_STATEMENT are the single-format artefacts of the first
    prototype; they are kept because the envelope still accepts them.
    """

    REQUIREMENT = "requirement"
    TECHNICAL_ENABLER = "technical_enabler"
    USER_STORY = "user_story"
    SHALL_STATEMENT = "shall_statement"
    QUESTION = "question"


class RequirementType(str, enum.Enum):
    FUNCTIONAL = "FR"
    NON_FUNCTIONAL = "NFR"
    UNKNOWN = "unknown"


# NFR subtypes. The first eleven are the PROMISE subtypes used by ReqInOne; "safety"
# and "other" are the two extra codes of the GigaBrain taxonomy (knowledge.NFR_TAXONOMY).
NFR_SUBTYPES: tuple[str, ...] = (
    "availability",
    "fault_tolerance",
    "legal",
    "look_and_feel",
    "maintainability",
    "operational",
    "performance",
    "portability",
    "scalability",
    "security",
    "usability",
    "safety",
    "other",
)

# Codes used by the agents (see REQUIREMENT_FORMAT in agents/twin.py) -> subtype name.
NFR_SUBTYPE_CODES: dict[str, str] = {
    "PE": "performance",
    "SE": "security",
    "US": "usability",
    "LF": "look_and_feel",
    "A": "availability",
    "SA": "safety",
    "PO": "portability",
    "MN": "maintainability",
    "SC": "scalability",
    "L": "legal",
    "FT": "fault_tolerance",
    "O": "operational",
    "OT": "other",
}


# --------------------------------------------------------------------------
# Input: sources, glossary and artefacts
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceDocument:
    """A piece of raw material that may support a requirement.

    Validation checks the origin an agent declares against these sources. The
    Critic reads them through its own path (the transcript, the PO messages in
    the log), never through the agent that wrote the requirement.

    kind: need | turn | conversation | expert | requirement | transcript | other
    """

    id: str
    text: str
    kind: str = "need"
    origin: str = ""


@dataclass(frozen=True)
class GlossaryEntry:
    term: str
    definition: str = ""


@dataclass(frozen=True)
class EvidenceRef:
    """Link an agent declares between the artefact and its source.

    `quote` is optional: the agents cite the turn (or the conversation) a
    requirement came from, not a literal passage. With a quote, the quote is
    looked for in the source; without one, the statement itself must be
    supported by the cited source (see checks/grounding.py).
    """

    source_id: str
    quote: str = ""
    reason: str = ""


@dataclass(frozen=True)
class Story:
    """User story in its three parts: As a <role>, I want <goal>, so that <benefit>."""

    role: str = ""
    goal: str = ""
    benefit: str = ""

    def complete(self) -> bool:
        return all(part.strip() for part in (self.role, self.goal, self.benefit))

    def text(self) -> str:
        return f"As a {self.role}, I want {self.goal}, so that {self.benefit}."


@dataclass(frozen=True)
class Criterion:
    """Acceptance criterion in Gherkin form (Given / When / Then)."""

    scenario: str = ""
    given: str = ""
    when: str = ""
    then: str = ""

    def complete(self) -> bool:
        return all(part.strip() for part in (self.given, self.when, self.then))

    def text(self) -> str:
        parts = []
        if self.given.strip():
            parts.append(f"Given {self.given.strip()}")
        if self.when.strip():
            parts.append(f"when {self.when.strip()}")
        if self.then.strip():
            parts.append(f"then {self.then.strip()}")
        return ", ".join(parts)


@dataclass(frozen=True)
class LinkRef:
    """Relation an agent declares between this artefact and another requirement.

    `kind` is one of the repository link kinds (refines, splits, merges,
    supersedes, depends_on, conflicts_with) or one of two claims that are not
    stored as links: "base" (this is a new version of the target) and
    "duplicates" (an expert says the artefact repeats the target).
    """

    kind: str
    target: str
    reason: str = ""


@dataclass(frozen=True)
class Provenance:
    """Who produced the artefact and under which conditions.

    Recording this is what allows a run to be repeated and compared.
    The fields are optional because the pipeline does not fill them all yet.
    """

    agent_id: str = "unknown"
    agent_role: str = "unknown"
    model: str = ""
    temperature: float | None = None
    seed: int | None = None
    prompt_version: str = ""
    timestamp: str = ""


@dataclass
class Artifact:
    """Intermediate specification: the output of an agent, still a candidate.

    Not every field applies to every kind. `story` and `acceptance_criteria`
    always hold plain text, so the lexical checks work on any artefact;
    `story_parts` and `criteria` hold the structured form when the agent gave
    one, and the structural checks look at those.
    """

    id: str
    kind: ArtifactKind = ArtifactKind.USER_STORY
    title: str = ""
    story: str = ""
    statement: str = ""
    rationale: str = ""
    acceptance_criteria: list[str] = field(default_factory=list)
    evidence: list[EvidenceRef] = field(default_factory=list)
    req_type: RequirementType = RequirementType.UNKNOWN
    nfr_subtype: str = ""
    version: str = "1.0"
    supersedes: str = ""
    provenance: Provenance = field(default_factory=Provenance)
    raw: str = ""
    # structured form (GigaBrain requirement format)
    story_parts: Story | None = None
    criteria: list[Criterion] = field(default_factory=list)
    ears_pattern: str = ""
    links: list[LinkRef] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)

    def body_text(self) -> str:
        """Free text of the artefact, used by the lexical checks."""
        parts = [self.title, self.statement, self.story, self.rationale]
        parts.extend(self.acceptance_criteria)
        return "\n".join(p for p in parts if p)

    def headline(self) -> str:
        """The sentence that carries the requirement itself, without criteria or title."""
        return self.statement or self.story or self.title

    def linked_ids(self) -> set[str]:
        """Requirements this artefact declares a relation with."""
        ids = {link.target for link in self.links if link.target}
        if self.supersedes:
            ids.add(self.supersedes)
        return ids


@dataclass
class DocumentSection:
    id: str
    title: str
    text: str = ""


@dataclass
class SpecificationDocument:
    """The consolidated requirements document, when one is assembled."""

    title: str = ""
    sections: list[DocumentSection] = field(default_factory=list)

    def section(self, section_id: str) -> DocumentSection | None:
        for s in self.sections:
            if s.id == section_id:
                return s
        return None


@dataclass
class Envelope:
    """Everything validation needs for one run.

    The name is deliberate: it is a closed, versioned package, so the same
    input can be validated again later and give the same result.
    """

    schema_version: str = "1.1"
    run_id: str = ""
    project: str = "gigabrain"
    sources: list[SourceDocument] = field(default_factory=list)
    glossary: list[GlossaryEntry] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    document: SpecificationDocument | None = None

    def source(self, source_id: str) -> SourceDocument | None:
        for s in self.sources:
            if s.id == source_id:
                return s
        return None

    def artifact(self, artifact_id: str) -> Artifact | None:
        for a in self.artifacts:
            if a.id == artifact_id:
                return a
        return None

    def content_hash(self) -> str:
        """Stable fingerprint of the input.

        It is the reproducibility key: two reports with the same input hash and
        the same rule-set version should be identical.
        """
        data = _to_jsonable(self)
        data.pop("run_id", None)  # the id of the run is not content
        payload = json.dumps(data, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------
# Output: findings, scores and the report
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """A problem (or a remark) found by a check.

    `message` and `suggestion` are text for a person or for the agent that wrote
    the artefact; the rest are identifiers, used for aggregation and for the
    empirical study.
    """

    check_id: str
    dimension: Dimension
    severity: Severity
    message: str
    level: Level = Level.ITEM
    target_id: str = ""
    locator: str = ""
    excerpt: str = ""
    suggestion: str = ""
    evidence_kind: EvidenceKind = EvidenceKind.DETERMINISTIC

    def short(self) -> str:
        where = f" [{self.target_id}{'/' + self.locator if self.locator else ''}]" if self.target_id else ""
        return f"{self.severity.value.upper()} {self.check_id}{where}: {self.message}"


@dataclass
class DimensionScore:
    """Normalised score of one dimension.

    `score` lives in [0, 1]. `likert` is the same score rescaled to 1..5, the
    scale the human raters of ReqInOne used, so that the automated score can
    later be compared with expert ratings. It is a declared proxy, not a
    demonstrated equivalence.

    `passed` is the gate: the dimension reached the minimum score of the policy
    and has no blocking finding. `checks` is how many checks actually ran for
    the dimension; with zero, the score says "nothing found because nothing was
    looked at", and the report shows it as not assessed.
    """

    dimension: Dimension
    score: float
    likert: float
    findings: int = 0
    penalty: float = 0.0
    passed: bool = True
    checks: int = 0

    @property
    def label(self) -> str:
        return self.dimension.label

    @property
    def assessed(self) -> bool:
        return self.checks > 0


@dataclass
class ItemResult:
    """Result for one artefact: the five item dimensions plus a local verdict."""

    artifact_id: str
    scores: dict[Dimension, DimensionScore] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    verdict: Verdict = Verdict.APPROVED

    @property
    def overall(self) -> float:
        if not self.scores:
            return 1.0
        return sum(s.score for s in self.scores.values()) / len(self.scores)

    @property
    def failed_dimensions(self) -> list[Dimension]:
        return [d for d, s in self.scores.items() if not s.passed]

    @property
    def passed(self) -> bool:
        return self.verdict is not Verdict.REJECTED


@dataclass
class ValidationReport:
    """Output artefact of validation: versioned and persistable.

    This is the object the Critic returns to the cycle and the log keeps next
    to the requirement version it refers to.

    `focus` lists the artefacts that were actually assessed when the envelope
    also carries context (e.g. one candidate against the saved repository);
    empty means every artefact of the envelope.
    """

    run_id: str
    envelope_hash: str
    ruleset_version: str
    policy_version: str
    created_at: str
    verdict: Verdict = Verdict.APPROVED
    item_results: list[ItemResult] = field(default_factory=list)
    document_scores: dict[Dimension, DimensionScore] = field(default_factory=dict)
    document_findings: list[Finding] = field(default_factory=list)
    checks_run: list[str] = field(default_factory=list)
    checks_skipped: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    focus: list[str] = field(default_factory=list)
    document_assessed: bool = True
    #: Who answered tier 2 (the model), or "" when the judge was off.
    judge: str = ""

    # -- convenience queries -------------------------------------------------

    def all_findings(self) -> list[Finding]:
        out: list[Finding] = []
        for item in self.item_results:
            out.extend(item.findings)
        out.extend(self.document_findings)
        return out

    def findings_by_severity(self, severity: Severity) -> list[Finding]:
        return [f for f in self.all_findings() if f.severity is severity]

    def item_result(self, artifact_id: str) -> ItemResult | None:
        for item in self.item_results:
            if item.artifact_id == artifact_id:
                return item
        return None

    def item_dimension_average(self, dimension: Dimension) -> float:
        """Mean of an item dimension over every assessed artefact."""
        vals = [
            item.scores[dimension].score
            for item in self.item_results
            if dimension in item.scores
        ]
        return sum(vals) / len(vals) if vals else 1.0

    def failed_dimensions(self) -> list[Dimension]:
        """Every dimension that did not pass, at either level, in the order of the questionnaire."""
        failed = {d for item in self.item_results for d in item.failed_dimensions}
        failed |= {d for d, s in self.document_scores.items() if not s.passed}
        return [d for d in Dimension if d in failed]

    @property
    def passed(self) -> bool:
        return self.verdict is not Verdict.REJECTED

    @property
    def document_overall(self) -> float:
        if not self.document_scores:
            return 1.0
        return sum(s.score for s in self.document_scores.values()) / len(self.document_scores)

    @property
    def item_overall(self) -> float:
        if not self.item_results:
            return 1.0
        return sum(i.overall for i in self.item_results) / len(self.item_results)

    def to_dict(self) -> dict[str, Any]:
        return _to_jsonable(self)


# --------------------------------------------------------------------------
# Serialisation
# --------------------------------------------------------------------------


def _to_jsonable(obj: Any) -> Any:
    """Turn dataclasses and enums into plain JSON structures.

    `asdict` alone is not enough because enums are used as dictionary keys.
    """
    if isinstance(obj, enum.Enum):
        return obj.value
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _to_jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {
            (k.value if isinstance(k, enum.Enum) else str(k)): _to_jsonable(v)
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple, set)):
        return [_to_jsonable(v) for v in obj]
    return obj
