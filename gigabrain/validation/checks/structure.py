"""Structural conformity checks (dimension CONFORMING).

What is checked here is the form, not the content: does the artefact have the
fields its kind requires, and does it follow the agreed template?

The templates are not ours:
- the requirement structure (an EARS "shall" statement, a user story in three
  parts, Gherkin acceptance criteria, FR/NFR class and subtype) is the one of
  gigabrain/requirement_format.py, and the EARS pattern is detected by the very
  function the project uses (`detect_ears`), so that this layer and the format
  check shown by the API never disagree about what an EARS sentence is;
- "As a <role>, I want <goal>, so that <benefit>" (and "Como ..., quero ...,
  para que ...") is the user story template of the Digital Twin;
- "<subject> shall <action>" is the INCOSE pattern ReqInOne uses, kept for
  artefacts declared as SHALL_STATEMENT.
"""

from __future__ import annotations

import re
from typing import Iterable

from ...requirement_format import EARS_PATTERNS, detect_ears
from ..model import (
    NFR_SUBTYPES,
    Artifact,
    ArtifactKind,
    Dimension,
    Finding,
    Level,
    RequirementType,
    Severity,
)
from ..registry import CheckContext, item_check
from ..text import strip_accents

# The text is compared without accents and in lower case, but with punctuation,
# because the comma separates the three parts of the story.
STORY_RE_EN = re.compile(
    r"^\s*as\s+an?\s+(?P<role>[^,]{2,}?)\s*,\s*"
    r"i\s+want\s+(?P<goal>.{3,}?)\s*,\s*"
    r"so\s+that\s+(?P<benefit>.{3,}?)\s*\.?\s*$",
    re.IGNORECASE | re.DOTALL,
)
STORY_RE_PT = re.compile(
    r"^\s*como\s+(?P<role>[^,]{2,}?)\s*,\s*"
    r"quero\s+(?P<goal>.{3,}?)\s*,\s*"
    r"para\s+que\s+(?P<benefit>.{3,}?)\s*\.?\s*$",
    re.IGNORECASE | re.DOTALL,
)

# INCOSE pattern, in both languages. A subject must come before the modal verb.
SHALL_RE = re.compile(
    r"^\s*(?:the\s+)?(?P<subject>.{2,}?)\s+(?:shall|must)\s+(?P<action>.{3,})$",
    re.IGNORECASE | re.DOTALL,
)
DEVE_RE = re.compile(
    r"^\s*(?:o|a|os|as)?\s*(?P<subject>.{2,}?)\s+dever?[aá]?\s+(?P<action>.{3,})$",
    re.IGNORECASE | re.DOTALL,
)

#: Kinds that carry the full structure (statement + story + Gherkin criteria).
STRUCTURED = (ArtifactKind.REQUIREMENT, ArtifactKind.TECHNICAL_ENABLER)


def _canon(text: str) -> str:
    return strip_accents(text or "").lower().strip()


@item_check(
    id="structure.required_fields",
    dimension=Dimension.CONFORMING,
    severity=Severity.BLOCKER,
    description="The artefact has a title and a non-empty statement (or story).",
    weight=1.5,
)
def required_fields(artifact: Artifact, ctx: CheckContext) -> Iterable[Finding]:
    if not artifact.title.strip():
        yield Finding(
            check_id="structure.required_fields",
            dimension=Dimension.CONFORMING,
            severity=Severity.BLOCKER,
            level=Level.ITEM,
            target_id=artifact.id,
            locator="title",
            message="The artefact has no title.",
            suggestion="Give it a short title that identifies the requirement in one line.",
        )
    if not (artifact.statement.strip() or artifact.story.strip()):
        yield Finding(
            check_id="structure.required_fields",
            dimension=Dimension.CONFORMING,
            severity=Severity.BLOCKER,
            level=Level.ITEM,
            target_id=artifact.id,
            locator="statement",
            message="The artefact has neither a statement nor a story.",
            suggestion="Write the requirement as one 'shall' sentence following an EARS pattern.",
        )
    elif artifact.kind in STRUCTURED and not artifact.statement.strip():
        yield Finding(
            check_id="structure.required_fields",
            dimension=Dimension.CONFORMING,
            severity=Severity.BLOCKER,
            level=Level.ITEM,
            target_id=artifact.id,
            locator="statement",
            message="The requirement has a story but no statement.",
            suggestion="The story says who wants it and why; the 'shall' statement says what the system must do.",
        )


@item_check(
    id="structure.ears_statement",
    dimension=Dimension.CONFORMING,
    severity=Severity.MAJOR,
    description=(
        "The statement is a 'shall' sentence that fits an EARS pattern (ubiquitous, event, "
        "state, unwanted, optional or complex), and the declared pattern is the one the sentence has."
    ),
    weight=1.2,
    requires="Applies to artefacts of kind requirement or technical_enabler.",
)
def ears_statement(artifact: Artifact, ctx: CheckContext) -> Iterable[Finding]:
    if artifact.kind not in STRUCTURED:
        return
    statement = artifact.statement.strip()
    if not statement:
        return  # already reported by structure.required_fields
    detected = detect_ears(statement)
    if detected is None:
        yield Finding(
            check_id="structure.ears_statement",
            dimension=Dimension.CONFORMING,
            severity=Severity.MAJOR,
            level=Level.ITEM,
            target_id=artifact.id,
            locator="statement",
            excerpt=statement[:160],
            message="The statement is not a 'shall' sentence (no shall / must / deve).",
            suggestion="Rewrite it in an EARS pattern, e.g. 'When <trigger>, the <system> shall <response>.'",
        )
        return
    declared = artifact.ears_pattern.strip().lower()
    if declared and declared in EARS_PATTERNS and declared != detected:
        yield Finding(
            check_id="structure.ears_statement",
            dimension=Dimension.CONFORMING,
            severity=Severity.MINOR,
            level=Level.ITEM,
            target_id=artifact.id,
            locator="ears_pattern",
            excerpt=statement[:160],
            message=f"The statement is declared as '{declared}' but reads as '{detected}'.",
            suggestion="Fix the pattern label or the opening of the sentence so that both agree.",
        )


@item_check(
    id="structure.story_format",
    dimension=Dimension.CONFORMING,
    severity=Severity.MAJOR,
    description=(
        "The user story has its three parts: 'As a <role>, I want <goal>, so that <benefit>' "
        "(or 'Como ..., quero ..., para que ...')."
    ),
    weight=1.2,
    requires="Applies to artefacts of kind requirement or user_story.",
)
def story_format(artifact: Artifact, ctx: CheckContext) -> Iterable[Finding]:
    if artifact.kind not in (ArtifactKind.REQUIREMENT, ArtifactKind.USER_STORY):
        return

    def missing(label: str, locator: str, excerpt: str) -> Finding:
        return Finding(
            check_id="structure.story_format",
            dimension=Dimension.CONFORMING,
            severity=Severity.MINOR,
            level=Level.ITEM,
            target_id=artifact.id,
            locator=locator,
            excerpt=excerpt,
            message=f"The '{label}' of the story is empty or too short.",
            suggestion=f"State the {label} explicitly instead of leaving it implicit.",
        )

    parts = artifact.story_parts
    if parts is not None:
        for label in ("role", "goal", "benefit"):
            if len(getattr(parts, label).strip()) < 3:
                yield missing(label, f"story.{label}", artifact.story[:160])
        return

    story = artifact.story.strip()
    if not story:
        if artifact.kind is ArtifactKind.REQUIREMENT:
            yield Finding(
                check_id="structure.story_format",
                dimension=Dimension.CONFORMING,
                severity=Severity.MAJOR,
                level=Level.ITEM,
                target_id=artifact.id,
                locator="story",
                message="The requirement has no user story.",
                suggestion="Say who benefits, what they want and why: without it the value of the requirement is implicit.",
            )
        return  # a user_story without a story is reported by structure.required_fields

    canon = _canon(story)
    match = STORY_RE_EN.match(canon) or STORY_RE_PT.match(canon)
    if not match:
        yield Finding(
            check_id="structure.story_format",
            dimension=Dimension.CONFORMING,
            severity=Severity.MAJOR,
            level=Level.ITEM,
            target_id=artifact.id,
            locator="story",
            excerpt=story[:160],
            message="The story does not follow 'As a ..., I want ..., so that ...'.",
            suggestion=(
                "Rewrite it in the agreed template. If one of the three parts cannot be "
                "filled in, the requirement is not mature yet."
            ),
        )
        return
    for label in ("role", "goal", "benefit"):
        if len((match.group(label) or "").strip()) < 3:
            yield missing(label, f"story.{label}", story[:160])


@item_check(
    id="structure.enabler_format",
    dimension=Dimension.CONFORMING,
    severity=Severity.MAJOR,
    description="A technical enabler states the reason that makes it a precondition.",
    requires="Applies to artefacts of kind technical_enabler.",
)
def enabler_format(artifact: Artifact, ctx: CheckContext) -> Iterable[Finding]:
    if artifact.kind is not ArtifactKind.TECHNICAL_ENABLER:
        return
    if not artifact.rationale.strip():
        yield Finding(
            check_id="structure.enabler_format",
            dimension=Dimension.CONFORMING,
            severity=Severity.MAJOR,
            level=Level.ITEM,
            target_id=artifact.id,
            locator="rationale",
            message="The enabler does not say why it is needed.",
            suggestion=(
                "Explain why this item is a real precondition of a business requirement; "
                "without that, an enabler is a technical solution disguised as a requirement."
            ),
        )


@item_check(
    id="structure.shall_pattern",
    dimension=Dimension.CONFORMING,
    severity=Severity.MAJOR,
    description="The sentence follows the INCOSE pattern '<subject> shall <action> <object> [, when <condition>]'.",
    requires="Applies to artefacts of kind shall_statement.",
)
def shall_pattern(artifact: Artifact, ctx: CheckContext) -> Iterable[Finding]:
    if artifact.kind is not ArtifactKind.SHALL_STATEMENT:
        return
    statement = artifact.statement.strip()
    if not statement:
        return
    canon = _canon(statement)
    if not (SHALL_RE.match(canon) or DEVE_RE.match(canon)):
        yield Finding(
            check_id="structure.shall_pattern",
            dimension=Dimension.CONFORMING,
            severity=Severity.MAJOR,
            level=Level.ITEM,
            target_id=artifact.id,
            locator="statement",
            excerpt=statement[:160],
            message="The sentence does not follow '<subject> shall <action> <object>'.",
            suggestion="Name the subject (the system, module X) before the modal verb and keep a single action verb.",
        )


@item_check(
    id="structure.acceptance_criteria",
    dimension=Dimension.CONFORMING,
    severity=Severity.MAJOR,
    description="The artefact has acceptance criteria, at least the minimum number.",
    weight=1.2,
    requires="Does not apply to artefacts of kind question.",
)
def acceptance_criteria(artifact: Artifact, ctx: CheckContext) -> Iterable[Finding]:
    if artifact.kind is ArtifactKind.QUESTION:
        return
    criteria = [c for c in artifact.acceptance_criteria if c.strip()]
    minimum = (
        ctx.config.min_acceptance_criteria_enabler
        if artifact.kind is ArtifactKind.TECHNICAL_ENABLER
        else ctx.config.min_acceptance_criteria
    )
    if not criteria:
        yield Finding(
            check_id="structure.acceptance_criteria",
            dimension=Dimension.CONFORMING,
            severity=Severity.BLOCKER,
            level=Level.ITEM,
            target_id=artifact.id,
            locator="acceptance_criteria",
            message="The artefact has no acceptance criteria.",
            suggestion="Without an acceptance criterion there is no way to say the requirement is done.",
        )
    elif len(criteria) < minimum:
        # Information, not a defect: this layer cannot tell whether the source
        # states an exception or limit case. Asking for one made the writer invent it.
        yield Finding(
            check_id="structure.acceptance_criteria",
            dimension=Dimension.CONFORMING,
            severity=Severity.INFO,
            level=Level.ITEM,
            target_id=artifact.id,
            locator="acceptance_criteria",
            message=f"Only {len(criteria)} acceptance criterion(s); the convention is {minimum}.",
            suggestion="Add an exception or limit scenario only if the source states one; otherwise ask the stakeholder.",
        )


@item_check(
    id="structure.gherkin_criteria",
    dimension=Dimension.CONFORMING,
    severity=Severity.MAJOR,
    description="Every acceptance criterion has its Given, its When and its Then.",
    requires="Applies to artefacts whose criteria come in Gherkin form.",
)
def gherkin_criteria(artifact: Artifact, ctx: CheckContext) -> Iterable[Finding]:
    for index, criterion in enumerate(artifact.criteria, start=1):
        if criterion.complete():
            continue
        absent = [part for part in ("given", "when", "then") if not getattr(criterion, part).strip()]
        yield Finding(
            check_id="structure.gherkin_criteria",
            dimension=Dimension.CONFORMING,
            severity=Severity.MAJOR,
            level=Level.ITEM,
            target_id=artifact.id,
            locator=f"acceptance_criteria[{index}]",
            excerpt=criterion.text()[:160] or criterion.scenario[:160],
            message=f"The criterion has no {' / '.join(absent)}.",
            suggestion="A criterion needs the initial context (given), the action or event (when) and the observable result (then).",
        )


@item_check(
    id="structure.classification",
    dimension=Dimension.CONFORMING,
    severity=Severity.MINOR,
    description="The artefact is classified as FR or NFR and, if NFR, has one of the known subtypes.",
    requires="Does not apply to enablers or questions.",
)
def classification(artifact: Artifact, ctx: CheckContext) -> Iterable[Finding]:
    if artifact.kind in (ArtifactKind.QUESTION, ArtifactKind.TECHNICAL_ENABLER):
        return
    if artifact.req_type is RequirementType.UNKNOWN:
        yield Finding(
            check_id="structure.classification",
            dimension=Dimension.CONFORMING,
            severity=Severity.MINOR,
            level=Level.ITEM,
            target_id=artifact.id,
            locator="class",
            message="The requirement is not classified as functional or non-functional.",
            suggestion="Classify it as FR or NFR.",
        )
        return
    if artifact.req_type is RequirementType.NON_FUNCTIONAL:
        subtype = (artifact.nfr_subtype or "").strip().lower()
        if not subtype:
            yield Finding(
                check_id="structure.classification",
                dimension=Dimension.CONFORMING,
                severity=Severity.MINOR,
                level=Level.ITEM,
                target_id=artifact.id,
                locator="subtype",
                message="The NFR has no subtype.",
                suggestion=f"Use one of the known subtypes: {', '.join(NFR_SUBTYPES)}.",
            )
        elif subtype not in NFR_SUBTYPES:
            yield Finding(
                check_id="structure.classification",
                dimension=Dimension.CONFORMING,
                severity=Severity.MINOR,
                level=Level.ITEM,
                target_id=artifact.id,
                locator="subtype",
                excerpt=subtype,
                message=f"Unknown NFR subtype: '{subtype}'.",
                suggestion=f"Known subtypes: {', '.join(NFR_SUBTYPES)}.",
            )
