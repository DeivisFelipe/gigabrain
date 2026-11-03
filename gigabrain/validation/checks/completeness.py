"""Completeness checks (dimension COMPLETENESS).

Completeness has two meanings here, and both are checked:

1. Completeness of form: the document has the sections its template requires.
   The default sections (introduction, stakeholders, use cases, glossary) are the
   summary sections ReqInOne fills in, derived from IEEE 830. They only apply
   when a document is assembled, which the project does not do yet.
2. Completeness of coverage: what was asked for became a requirement. This is
   the one that matters for elicitation: a set of requirements can be formally
   complete and still have left out half of what the client asked for.

Coverage is read in two ways. An explicit need (a source of kind "need") that no
requirement cites is a defect. A stakeholder turn of a meeting that no
requirement cites is only a remark: plenty of turns carry no requirement
(greetings, confirmations), and telling which ones do is the Twin's job, so the
check lists them for a person to look at and does not fail anything.
"""

from __future__ import annotations

from typing import Iterable

from ..model import Dimension, Finding, Level, RequirementType, Severity
from ..registry import CheckContext, document_check
from ..text import word_count
from ._requires import all_of, requires_document, requires_kind, requires_needs, requires_whole_set

#: Turns shorter than this are not worth listing ("Sorry, I was on mute.").
MIN_TURN_WORDS = 8


@document_check(
    id="completeness.required_sections",
    dimension=Dimension.COMPLETENESS,
    severity=Severity.MAJOR,
    description="The document has every section its template requires.",
    weight=1.2,
    requires="Skipped when the run has no assembled document.",
    applies=requires_document,
)
def required_sections(ctx: CheckContext) -> Iterable[Finding]:
    present = {s.id for s in ctx.envelope.document.sections}
    for section_id in ctx.config.required_sections:
        if section_id not in present:
            yield Finding(
                check_id="completeness.required_sections",
                dimension=Dimension.COMPLETENESS,
                severity=Severity.MAJOR,
                level=Level.DOCUMENT,
                target_id=section_id,
                message=f"Required section missing: '{section_id}'.",
                suggestion="Write the section or adjust the template in CheckConfig.required_sections.",
            )


@document_check(
    id="completeness.empty_sections",
    dimension=Dimension.COMPLETENESS,
    severity=Severity.MINOR,
    description="No declared section of the document is empty.",
    requires="Skipped when the run has no assembled document.",
    applies=requires_document,
)
def empty_sections(ctx: CheckContext) -> Iterable[Finding]:
    for section in ctx.envelope.document.sections:
        if not section.text.strip():
            yield Finding(
                check_id="completeness.empty_sections",
                dimension=Dimension.COMPLETENESS,
                severity=Severity.MINOR,
                level=Level.DOCUMENT,
                target_id=section.id,
                message=f"Section '{section.title or section.id}' is declared but empty.",
                suggestion="An empty section in the final document is worse than a missing one: it fakes completeness.",
            )


@document_check(
    id="completeness.orphan_needs",
    dimension=Dimension.COMPLETENESS,
    severity=Severity.MAJOR,
    description="Every need brought by the PO produced at least one requirement that cites it.",
    weight=1.5,
    requires="Looks at sources of kind 'need', over the whole specification; skipped when there are none.",
    applies=all_of(requires_whole_set, requires_needs),
)
def orphan_needs(ctx: CheckContext) -> Iterable[Finding]:
    referenced = {ref.source_id for artifact in ctx.envelope.artifacts for ref in artifact.evidence}
    for need in ctx.envelope.sources:
        if need.kind == "need" and need.id not in referenced:
            yield Finding(
                check_id="completeness.orphan_needs",
                dimension=Dimension.COMPLETENESS,
                severity=Severity.MAJOR,
                level=Level.DOCUMENT,
                target_id=need.id,
                excerpt=need.text[:160],
                message=f"The need '{need.id}' produced no requirement.",
                suggestion=(
                    "Either this point still has to be elicited, or the decision to drop it "
                    "has to be recorded: a silent drop cannot be traced."
                ),
            )


@document_check(
    id="completeness.uncited_turns",
    dimension=Dimension.COMPLETENESS,
    severity=Severity.INFO,
    description="Lists the stakeholder turns of the meeting that no requirement cites, for a person to look at.",
    weight=0.5,
    requires="Looks at sources of kind 'turn' over the whole specification; a remark, never a failure.",
    applies=all_of(requires_whole_set, requires_kind("turn")),
)
def uncited_turns(ctx: CheckContext) -> Iterable[Finding]:
    turns = [s for s in ctx.envelope.sources if s.kind == "turn" and word_count(s.text) >= MIN_TURN_WORDS]
    if not turns:
        return
    referenced = {ref.source_id for artifact in ctx.envelope.artifacts for ref in artifact.evidence}
    uncited = [t for t in turns if t.id not in referenced]
    if uncited:
        yield Finding(
            check_id="completeness.uncited_turns",
            dimension=Dimension.COMPLETENESS,
            severity=Severity.INFO,
            level=Level.DOCUMENT,
            target_id=",".join(t.id for t in uncited[:20]),
            excerpt=uncited[0].text[:160],
            message=(
                f"{len(uncited)} of {len(turns)} stakeholder turns produced no requirement "
                f"({1 - len(uncited) / len(turns):.0%} cited)."
            ),
            suggestion="Skim the listed turns: one of them may carry a requirement that was missed.",
        )


@document_check(
    id="completeness.nfr_presence",
    dimension=Dimension.COMPLETENESS,
    severity=Severity.INFO,
    description="The specification has non-functional requirements, not only functions.",
    weight=0.5,
    requires="Over the whole specification, when at least one requirement is classified.",
    applies=requires_whole_set,
)
def nfr_presence(ctx: CheckContext) -> Iterable[Finding]:
    classified = [a for a in ctx.envelope.artifacts if a.req_type is not RequirementType.UNKNOWN]
    if not classified:
        return
    if not any(a.req_type is RequirementType.NON_FUNCTIONAL for a in classified):
        yield Finding(
            check_id="completeness.nfr_presence",
            dimension=Dimension.COMPLETENESS,
            severity=Severity.INFO,
            level=Level.DOCUMENT,
            message="The specification has no non-functional requirement.",
            suggestion="Performance, security and usability are rarely irrelevant; they are usually just not elicited.",
        )
